# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Pick the top contact areas out of a GDS by hand.

Automatic pad detection (core.chip_proxy.get_bond_pad_positions_mm) works by
convention: named bondpad_*/IOPad cells, then the Cadence/IHP datatype-2 PIN
marker, then the top drawing layers. Every one of those conventions is a
house style, and a layout that does not share it gets no pads at all — with
no error, because "this GDS has no pad cells" is indistinguishable from "this
GDS has no pads".

SKY130 is exactly that case. It draws on datatype 20, so the datatype-2 rule
finds nothing and the datatype-0 rule finds nothing either; its pad opening
(pad.drawing, 76/20) is typed LEFOBS rather than PIN, so the map-driven rule
skips it as well. The pads are plainly there in the file — no heuristic in
this workbench happens to describe them.

So this module lets the layout be asked instead of guessed at. It reads the
GDS structure — its cells, and the layers they draw on — cheaply enough to
show in a dialog, and turns whichever of them the user points at into pads:

    structure_tree(gds)           -> the cell hierarchy, with instance counts
    layer_rows(gds)               -> every layer, with how pad-like it looks
    pads_from_layers(gds, keys)   -> one pad per polygon (merged if touching)
    pads_from_cells(gds, names)   -> one pad per instance of those cells
    attach_pads(doc, chip, pads)  -> the markers, on top of the chip

The pads it produces carry the same keys as the automatic path
(name/x_mm/y_mm/width_mm/height_mm/label) and the markers carry the same
ContactPoint schema, so wire bonding, the netlist and the DRC treat a picked
pad exactly like a detected one.

No Qt here: ui.PadPickerDialog is the dialog, this is what it and the tests
call.
"""

from statistics import median

import gdstk
import Part
import FreeCAD
from FreeCAD import Base

from core.chip_proxy import (
    die_cells,
    _DEFAULT_PAD_SIZE_MM,
)
from core.Core_Functionality import _apply_gds_ref_transform

# A bond pad is not a via and not a die-sized field. These bounds only decide
# what is offered and pre-selected; the dialog exposes both, because a
# micro-bump array is legitimately below the lower one.
DEFAULT_MIN_PAD_MM = 0.010    # 10 µm — above routing wires and vias
DEFAULT_MAX_PAD_MM = 0.500    # 500 µm — below fill fields and the die itself

# Merging touching polygons is O(n log n) in gdstk but still real work, and a
# routing layer can carry millions. Above this count the layer is used as
# drawn — it is not a pad layer anyway, and the user will see that.
_MERGE_LIMIT = 20_000

# Where a picked pad records what it came from, so it can be found again.
PAD_SOURCE = "PadSource"

# How many of a layer's shapes are kept to report a typical pad size.
# Every shape is still counted and size-tested; this only bounds the
# list the median is taken over.
_SIZE_SAMPLE = 5_000

_PAD_MARKER_MIN_THICKNESS_MM = 1e-4


# ── reading the file ───────────────────────────────────────────────────────

def _scale_of(lib):
    return (lib.unit * 1000.0) if getattr(lib, "unit", None) else 0.001


def _layer_name(layer, datatype, selected_layers=None, ihp_map=None):
    """
    The name this PDK gives (layer, datatype) — from the .lyp, else the .map.

    Falls back to "L/D" so a layer the PDK files do not mention is still
    identifiable; on an unconfigured import that is every layer, and the
    numbers alone are enough to recognise a pad layer you know.
    """
    for entry in selected_layers or []:
        if (entry.get("layer_id") == layer and entry.get("datatype") == datatype):
            name = entry.get("name")
            if name:
                return str(name)
    mapped = (ihp_map or {}).get((layer, datatype))
    if mapped and mapped.get("edi_name"):
        return str(mapped["edi_name"])
    return f"{layer}/{datatype}"


def _placements(ref):
    """
    How many copies one reference places.

    A reference carrying a repetition (an AREF — a bump array, a pad row, a
    memory tile) stands for many placements, and counting it as one
    understates the layer it draws on by orders of magnitude. gdstk exposes
    the count as Repetition.size; its `offsets` attribute is only filled in
    for the explicit kind and is None for a regular grid, so reading that
    alone silently counts every array in the file as a single instance.
    """
    repetition = getattr(ref, "repetition", None)
    if repetition is None:
        return 1
    for read in (lambda: repetition.size,
                 lambda: len(repetition.get_offsets()),
                 lambda: len(repetition.offsets)):
        try:
            count = read()
        except Exception:
            continue
        if count and count > 0:
            return int(count)
    return 1


def _reference_counts(cell):
    """{child cell name: how many times *cell* places it}."""
    counts = {}
    for ref in getattr(cell, "references", []) or []:
        child = getattr(ref, "cell", None)
        if child is None:
            continue
        counts[child.name] = counts.get(child.name, 0) + _placements(ref)
    return counts


def instance_counts(lib):
    """
    How many times each cell ends up on the die, counting every path to it.

    This is what makes the layer table affordable. Asking gdstk to flatten
    the hierarchy — even one layer at a time — walks the whole reference tree
    per call: on a real SKY130 SoC that is three seconds per layer and over
    three minutes for the table, and flattening everything at once instead
    costs gigabytes. The counts come from the reference graph alone, which is
    small, and each cell's own polygons are then weighted by them. One pass,
    no geometry built, same numbers.

    The graph is a DAG; anything left over after the topological pass is part
    of a cycle (an invalid file) and is simply left at zero rather than hung
    on.
    """
    by_name = {cell.name: cell for cell in lib.cells}
    edges = {name: _reference_counts(cell) for name, cell in by_name.items()}

    parents = {name: 0 for name in by_name}
    for children in edges.values():
        for child in children:
            if child in parents:
                parents[child] += 1

    counts = {name: 0 for name in by_name}
    for top in die_cells(lib):
        counts[top.name] = counts.get(top.name, 0) + 1

    pending = [name for name, n in parents.items() if n == 0]
    while pending:
        name = pending.pop()
        for child, placements in edges.get(name, {}).items():
            if child not in counts:
                continue
            counts[child] += counts[name] * placements
            parents[child] -= 1
            if parents[child] == 0:
                pending.append(child)
    return counts


def layer_rows(gds_path, selected_layers=None, ihp_map=None,
               min_mm=DEFAULT_MIN_PAD_MM, max_mm=DEFAULT_MAX_PAD_MM):
    """
    Every layer in *gds_path*, described well enough to choose from.

    Each row is {"layer", "datatype", "name", "polygons", "pad_like",
    "median_w_mm", "median_h_mm"}. "pad_like" counts the polygons whose
    extent falls inside [min_mm, max_mm] in both directions — the number
    that actually tells a pad layer from a routing layer at a glance, and
    what the dialog sorts and pre-selects on. Both counts are of placed
    polygons: a pad cell drawn once and placed twenty times counts twenty.

    Shapes are measured as drawn, in their own cell. A reference that rotates
    one by 90 degrees swaps its width and height, which cannot change whether
    it is pad-sized, since the same bounds apply to both. A magnified
    reference would be measured at its drawn size; magnification is vanishly
    rare in a layout that has pads in it, and paying for the flattened
    geometry of a whole chip to cover it is not worth it — see
    instance_counts().

    Rows are ordered by layer then datatype: a stack reads bottom to top by
    layer number in every PDK this has met, so the pad layers are the last
    rows, where they are expected to be.
    """
    lib = gdstk.read_gds(str(gds_path))
    scale = _scale_of(lib)
    counts = instance_counts(lib)

    stats = {}
    for cell in lib.cells:
        placed = counts.get(cell.name, 0)
        if placed <= 0:
            continue
        for poly in getattr(cell, "polygons", []) or []:
            key = (poly.layer, poly.datatype)
            entry = stats.get(key)
            if entry is None:
                entry = stats[key] = {"polygons": 0, "pad_like": 0,
                                      "widths": [], "heights": []}
            entry["polygons"] += placed
            (x0, y0), (x1, y1) = poly.bounding_box()
            w = (x1 - x0) * scale
            h = (y1 - y0) * scale
            if len(entry["widths"]) < _SIZE_SAMPLE:
                entry["widths"].append(w)
                entry["heights"].append(h)
            if min_mm <= w <= max_mm and min_mm <= h <= max_mm:
                entry["pad_like"] += placed

        # A path becomes polygons only once it is built, so it is counted as
        # one shape on each of its layers and not measured. Pads are not
        # drawn as paths; this is here so a path-only layer still appears.
        for path in getattr(cell, "paths", []) or []:
            layers = getattr(path, "layers", None) or []
            datatypes = getattr(path, "datatypes", None) or []
            for i, layer in enumerate(layers):
                key = (layer, datatypes[i] if i < len(datatypes) else 0)
                entry = stats.setdefault(key, {"polygons": 0, "pad_like": 0,
                                               "widths": [], "heights": []})
                entry["polygons"] += placed

    rows = []
    for (layer, datatype), entry in sorted(stats.items()):
        rows.append({
            "layer": layer,
            "datatype": datatype,
            "name": _layer_name(layer, datatype, selected_layers, ihp_map),
            "polygons": entry["polygons"],
            "pad_like": entry["pad_like"],
            "median_w_mm": median(entry["widths"]) if entry["widths"] else 0.0,
            "median_h_mm": median(entry["heights"]) if entry["heights"] else 0.0,
        })
    return rows


def structure_tree(gds_path, max_depth=6):
    """
    The GDS structure tree: top cells, and what each of them instantiates.

    Each node is {"name", "instances", "width_mm", "height_mm", "layers",
    "children"}. "instances" is how many times that cell is placed by its
    parent, "layers" the (layer, datatype) pairs it draws on itself, and the
    size is the cell's own bounding box — which is what identifies a pad
    cell: a padframe's pad cell is the one placed twenty times at 70 µm.

    A cell is expanded once; where the same cell appears again its children
    are not repeated (a real layout instantiates one standard cell tens of
    thousands of times, and a tree that repeats it is neither useful nor
    finite in practice). *max_depth* bounds it further.
    """
    lib = gdstk.read_gds(str(gds_path))
    scale = _scale_of(lib)
    expanded = set()

    def node(cell, instances, depth):
        bb = cell.bounding_box()
        width = (bb[1][0] - bb[0][0]) * scale if bb else 0.0
        height = (bb[1][1] - bb[0][1]) * scale if bb else 0.0
        entry = {
            "name": cell.name,
            "instances": instances,
            "width_mm": width,
            "height_mm": height,
            "layers": sorted({(p.layer, p.datatype)
                              for p in getattr(cell, "polygons", []) or []}),
            "children": [],
        }
        if depth >= max_depth or cell.name in expanded:
            return entry
        expanded.add(cell.name)

        counts = _reference_counts(cell)
        cells_by_name = {ref.cell.name: ref.cell
                         for ref in getattr(cell, "references", []) or []
                         if getattr(ref, "cell", None) is not None}

        for name in sorted(counts):
            entry["children"].append(
                node(cells_by_name[name], counts[name], depth + 1))
        return entry

    return [node(cell, 1, 0) for cell in die_cells(lib)]


# ── turning a selection into pads ──────────────────────────────────────────

def _merged(polygons):
    """
    Touching polygons unioned into one shape each, when that is affordable.

    A pad is frequently drawn as several overlapping rectangles, and one pad
    per polygon would then put three contact points on one pad. Union first
    and each physical pad is one polygon again.
    """
    if not polygons or len(polygons) > _MERGE_LIMIT:
        return polygons
    try:
        return gdstk.boolean(polygons, [], "or") or polygons
    except Exception:
        return polygons


def pads_from_layers(gds_path, keys, min_mm=DEFAULT_MIN_PAD_MM,
                     max_mm=DEFAULT_MAX_PAD_MM, selected_layers=None,
                     ihp_map=None, merge=True):
    """
    One pad per polygon on each (layer, datatype) in *keys*, size-filtered.

    This is the answer to "the pads are on this layer" — the case every
    convention-based rule misses. Polygons outside [min_mm, max_mm] in either
    direction are dropped, which is what keeps a shared layer (pad openings
    and a seal ring on the same number) from contributing the seal ring.
    """
    wanted = {(int(layer), int(datatype)) for layer, datatype in keys}
    if not wanted:
        return []

    lib = gdstk.read_gds(str(gds_path))
    scale = _scale_of(lib)
    cells = die_cells(lib)

    pads = []
    for layer, datatype in sorted(wanted):
        name = _layer_name(layer, datatype, selected_layers, ihp_map)
        polygons = []
        for cell in cells:
            polygons.extend(cell.get_polygons(depth=None, layer=layer,
                                              datatype=datatype))
        if merge:
            polygons = _merged(polygons)
        for poly in polygons:
            (x0, y0), (x1, y1) = poly.bounding_box()
            w = (x1 - x0) * scale
            h = (y1 - y0) * scale
            if not (min_mm <= w <= max_mm and min_mm <= h <= max_mm):
                continue
            pads.append({
                "name": name,
                "x_mm": (x0 + x1) / 2.0 * scale,
                "y_mm": (y0 + y1) / 2.0 * scale,
                "width_mm": w,
                "height_mm": h,
                "source": f"layer {layer}/{datatype}",
            })
    return pads


def _offsets(ref):
    """
    Every placement of one reference, as (dx, dy) in its parent's frame.

    A plain reference is placed once, at (0, 0) relative to its own origin.
    An AREF places the same cell across a lattice, and gdstk reports those
    offsets including the first — so this returns one entry per copy and the
    caller simply translates the reference's own box by each.
    """
    repetition = getattr(ref, "repetition", None)
    if repetition is None:
        return [(0.0, 0.0)]
    try:
        offsets = repetition.get_offsets()
    except Exception:
        offsets = getattr(repetition, "offsets", None)
    if offsets is None or len(offsets) == 0:
        return [(0.0, 0.0)]
    return [(float(x), float(y)) for x, y in offsets]


def _placed_boxes(cell, matches, cache, depth=0, max_depth=15):
    """
    Bounding boxes of every placed instance of a matching cell, in *cell*'s
    own coordinates.

    This is core.chip_proxy._find_named_cell_refs with array references
    expanded: that one reads each reference's origin, which is a single
    point even when the reference stands for a whole lattice of pads. A
    padframe built as an AREF would otherwise yield one pad instead of
    twenty — and reporting one pad where there are twenty is worse than
    reporting none, because it looks like it worked.

    A match is a leaf: a pad cell that contains another pad cell is one pad,
    not two.
    """
    if depth > max_depth:
        return []
    cached = cache.get(cell.name)
    if cached is not None:
        return cached

    found = []
    for ref in getattr(cell, "references", []) or []:
        child = getattr(ref, "cell", None)
        if child is None:
            continue

        if matches(child.name.upper()):
            box = child.bounding_box()
            boxes = [(child.name, box[0][0], box[0][1], box[1][0], box[1][1])] \
                if box is not None else [(child.name, 0.0, 0.0, 0.0, 0.0)]
        else:
            boxes = _placed_boxes(child, matches, cache, depth + 1, max_depth)
            if not boxes:
                continue

        origin = getattr(ref, "origin", (0.0, 0.0)) or (0.0, 0.0)
        rotation = getattr(ref, "rotation", 0.0) or 0.0
        magnification = getattr(ref, "magnification", 1.0) or 1.0
        x_reflection = bool(getattr(ref, "x_reflection", False))
        placements = _offsets(ref)

        for (name, x0, y0, x1, y1) in boxes:
            corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            moved = _apply_gds_ref_transform(corners, origin, rotation,
                                             magnification, x_reflection)
            xs = [p[0] for p in moved]
            ys = [p[1] for p in moved]
            bx0, by0, bx1, by1 = min(xs), min(ys), max(xs), max(ys)
            for dx, dy in placements:
                found.append((name, bx0 + dx, by0 + dy, bx1 + dx, by1 + dy))

    cache[cell.name] = found
    return found


def pads_from_cells(gds_path, names):
    """
    One pad per placed instance of each named cell — the padframe case.

    Names are matched exactly (case-insensitively), not as prefixes: the
    picker shows the real cell names, so there is nothing to guess, and a
    prefix match would silently also take the sub-cells whose names begin
    the same way.
    """
    wanted = {str(name).upper() for name in names or []}
    if not wanted:
        return []

    lib = gdstk.read_gds(str(gds_path))
    scale = _scale_of(lib)
    cache = {}
    pads = []
    for top in die_cells(lib):
        for (name, x0, y0, x1, y1) in _placed_boxes(
                top, lambda upper: upper in wanted, cache):
            w = (x1 - x0) * scale
            h = (y1 - y0) * scale
            pads.append({
                "name": name,
                "x_mm": (x0 + x1) / 2.0 * scale,
                "y_mm": (y0 + y1) / 2.0 * scale,
                "width_mm": w if w > 1e-6 else _DEFAULT_PAD_SIZE_MM,
                "height_mm": h if h > 1e-6 else _DEFAULT_PAD_SIZE_MM,
                "source": f"cell {name}",
            })
    return pads


def dedupe(pads, tol_mm=0.001):
    """
    Drop pads that land on top of one another.

    Picking both a pad cell and the layer its opening is drawn on is a
    natural thing to do — they describe the same twenty pads, and without
    this every pad would get two contact points a micron apart, which wire
    bonding would then offer as two separate targets.
    """
    kept = []
    for pad in pads:
        if any(abs(pad["x_mm"] - other["x_mm"]) <= tol_mm
               and abs(pad["y_mm"] - other["y_mm"]) <= tol_mm
               for other in kept):
            continue
        kept.append(pad)
    return kept


def label_pads(gds_path, pads):
    """Give each pad the layout's own text label, where one sits on it."""
    try:
        from core.pad_names import read_labels_from_file, label_for_pad
        labels = read_labels_from_file(gds_path)
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[Pads] could not read labels: {exc}\n")
        return pads
    for pad in pads:
        pad["label"] = label_for_pad(labels, pad)
    return pads


# ── putting them on the chip ───────────────────────────────────────────────

def _local_shape(obj):
    """*obj*'s shape in its own coordinates, with its placement removed."""
    shape = obj.Shape.copy()
    shape.Placement = FreeCAD.Placement()
    return shape


def _has_own_shape(obj):
    """
    Whether *obj* is a solid in its own right, rather than a group of them.

    A group reports a Shape — the compound of what is in it — but has no
    Placement, so "does it have a Shape" is not the question to ask.
    """
    if obj.isDerivedFrom("App::DocumentObjectGroup"):
        return False
    shape = getattr(obj, "Shape", None)
    return (shape is not None and not shape.isNull()
            and hasattr(obj, "Placement"))


def _same_placement(a, b, tol=1e-9):
    return (a.Base.distanceToPoint(b.Base) <= tol
            and max(abs(x - y) for x, y in zip(a.Rotation.Q, b.Rotation.Q)) <= tol)


def _shared_placement(objects):
    """
    The placement every object in *objects* has, or the identity if they
    differ.

    A GDS import starts with every layer at the identity, and Move Chip
    moves them all by the same amount, so they agree — which is what lets
    new pads be built in the layout's own coordinates and then placed like
    everything else. If they have been moved apart by hand there is no one
    frame to use, and the layout's raw coordinates are the honest answer.
    """
    placements = [obj.Placement for obj in objects]
    first = placements[0]
    if all(_same_placement(first, other) for other in placements[1:]):
        return first
    return FreeCAD.Placement()


def chip_top(target):
    """
    (top_z_in_local_coords, placement) for whatever the pads go on.

    A chip proxy is one block, and its own coordinates are the GDS's — which
    is the frame the pad positions are measured in, so the marker geometry is
    built there and the block's placement is copied onto it. That is the same
    arrangement the import produces, and it is what lets Move Chip keep pads
    and die together by moving placements alone.

    A full GDS import is a group, which has no placement of its own: its
    members carry one each, and they share it. That shared placement plays
    the same role as the block's, so pads picked for a chip that has already
    been placed on a carrier land on the chip and not back at the origin.

    The local top is taken by subtracting the placement's own Z rather than
    by copying the geometry to strip it — a full import's layers are far too
    heavy to copy for one number. That is exact for any rotation about Z,
    which is what a chip placement is.
    """
    if _has_own_shape(target):
        return _local_shape(target).BoundBox.ZMax, target.Placement

    members = [member for member in getattr(target, "Group", None) or []
               if getattr(member, "Shape", None) is not None
               and not member.Shape.isNull()]
    if not members:
        raise ValueError(f"'{target.Label}' has no geometry to put pads on top of.")
    placement = _shared_placement(members)
    top = max(member.Shape.BoundBox.ZMax for member in members)
    return top - placement.Base.z, placement


def _group_of(obj):
    """The group *obj* sits in, or None — where its new pads belong too."""
    for parent in getattr(obj, "InList", None) or []:
        if parent.isDerivedFrom("App::DocumentObjectGroup"):
            return parent
    return None


def _next_index(doc, base_name):
    index = 1
    while doc.getObject(f"{base_name}_Pad_{index:03d}") is not None:
        index += 1
    return index


def pad_marker(doc, name, pad, z_top, thickness_mm, placement=None,
               source_object="", label=""):
    """
    One pad marker: a thin box on the surface, carrying the contact point.

    Deliberately the same schema as ContactPointTool and the chip proxy
    import — IsContactPoint/ContactPoint/SourceObject — because wire bonding
    resolves its snap target from the ContactPoint property and never from
    the marker's shape. A pad picked here is therefore indistinguishable
    from a detected one everywhere downstream, which is the whole point.
    """
    width = pad.get("width_mm") or _DEFAULT_PAD_SIZE_MM
    height = pad.get("height_mm") or _DEFAULT_PAD_SIZE_MM
    thickness = max(thickness_mm, _PAD_MARKER_MIN_THICKNESS_MM)

    marker = doc.addObject("Part::Feature", name)
    marker.Shape = Part.makeBox(
        width, height, thickness,
        Base.Vector(pad["x_mm"] - width / 2.0, pad["y_mm"] - height / 2.0, z_top))
    if placement is not None:
        marker.Placement = placement

    local_point = Base.Vector(pad["x_mm"], pad["y_mm"], z_top + thickness)
    point = placement.multVec(local_point) if placement is not None else local_point

    marker.addProperty("App::PropertyVector", "ContactPoint", "Wirebond",
                       "Snap point for wire bonding")
    marker.addProperty("App::PropertyString", "SourceObject", "Wirebond",
                       "Source object this point belongs to")
    marker.addProperty("App::PropertyBool", "IsContactPoint", "Wirebond",
                       "Wire-bond contact point marker")
    marker.addProperty("App::PropertyString", "PadName", "Wirebond",
                       "Pad name from the layout's text label")
    marker.addProperty("App::PropertyString", PAD_SOURCE, "Wirebond",
                       "Where this pad came from — which layer or cell of the GDS")
    marker.ContactPoint = point
    marker.SourceObject = source_object
    marker.IsContactPoint = True
    marker.PadName = label or pad.get("label") or ""
    setattr(marker, PAD_SOURCE, pad.get("source", ""))

    if FreeCAD.GuiUp:
        marker.ViewObject.ShapeColor = (0.90, 0.30, 0.10)
        marker.ViewObject.Transparency = 0
        marker.ViewObject.DisplayMode = "Flat Lines"
    return marker


def existing_pads(doc, target):
    """The pad markers already belonging to *target*."""
    name = target.Name
    return [obj for obj in doc.Objects
            if getattr(obj, "IsContactPoint", False)
            and str(getattr(obj, "SourceObject", "")) == name]


def bonded_pads(doc):
    """The names of every pad a bond wire already lands on."""
    landed = set()
    for obj in getattr(doc, "Objects", None) or []:
        if not obj.Name.startswith("BondWire_"):
            continue
        for prop in ("StartCP", "EndCP"):
            name = str(getattr(obj, prop, "") or "")
            if name:
                landed.add(name)
    return landed


def remove_pads(doc, target, source=None, keep_bonded=True):
    """
    Delete *target*'s pad markers, optionally only those from one source.

    Picking pads is an iterative act — the first attempt takes a layer that
    turns out to be the via above the pad — so replacing a previous pick has
    to be one step, not twenty manual deletions.

    A pad a bond wire already lands on is kept, and said so. Re-picking is a
    correction to the pads, not a decision to throw away bonding work, and a
    wire whose pad has gone is far harder to notice than a duplicate pad.
    """
    landed = bonded_pads(doc) if keep_bonded else set()
    removed = kept = 0
    for marker in existing_pads(doc, target):
        if source is not None and str(getattr(marker, PAD_SOURCE, "")) != source:
            continue
        if marker.Name in landed:
            kept += 1
            continue
        doc.removeObject(marker.Name)
        removed += 1
    if kept:
        FreeCAD.Console.PrintWarning(
            f"[Pads] {kept} pad(s) kept on '{target.Label}': a bond wire "
            f"lands on them.\n")
    return removed


def _marker_thickness(target):
    """
    How thick a pad marker on *target* should be: thin enough to read as a
    pad on the surface, thick enough to see. Five percent of the chip's own
    height, capped, which is what the proxy import already uses.
    """
    height = float(getattr(target, "DieThickness", 0.0) or 0.0)
    if not height:
        try:
            if _has_own_shape(target):
                height = _local_shape(target).BoundBox.ZLength
            else:
                boxes = [member.Shape.BoundBox
                         for member in getattr(target, "Group", None) or []
                         if getattr(member, "Shape", None) is not None
                         and not member.Shape.isNull()]
                if boxes:
                    height = (max(b.ZMax for b in boxes)
                              - min(b.ZMin for b in boxes))
        except Exception:
            height = 0.0
    return max(min(height * 0.05, 0.01), _PAD_MARKER_MIN_THICKNESS_MM)


def attach_pads(doc, target, pads, replace=False, thickness_mm=None):
    """
    Build pad markers for *pads* on top of *target*. Returns the markers.

    *target* is a chip proxy block or an imported GDS group; the pads are in
    the GDS's own millimetre coordinates, exactly as the pick produced them.
    With *replace*, the target's existing pads are removed first.
    """
    if not pads:
        return []
    z_top, placement = chip_top(target)
    if replace:
        remove_pads(doc, target)

    if thickness_mm is None:
        thickness_mm = _marker_thickness(target)

    base_name = target.Name[:-6] if target.Name.endswith("_Block") else target.Name
    index = _next_index(doc, base_name)
    display = target.Label.replace(" Die Block", "")

    markers = []
    # The pads belong in the chip's own branch of the tree: inside the group
    # when the target IS one (a full GDS import), beside the block in its
    # group when it is a proxy.
    group = (target if target.isDerivedFrom("App::DocumentObjectGroup")
             else _group_of(target))
    for pad in pads:
        marker = pad_marker(doc, f"{base_name}_Pad_{index:03d}", pad, z_top,
                            thickness_mm, placement, target.Name,
                            pad.get("label", ""))
        marker.Label = f"{display} {pad.get('label') or pad.get('name', 'Pad')} {index}"
        if group is not None:
            group.addObject(marker)
        markers.append(marker)
        index += 1

    doc.recompute()
    FreeCAD.Console.PrintMessage(
        f"[Pads] {len(markers)} pad(s) added on '{display}'.\n")
    return markers


def pick_pads(gds_path, layer_keys=(), cell_names=(), min_mm=DEFAULT_MIN_PAD_MM,
              max_mm=DEFAULT_MAX_PAD_MM, selected_layers=None, ihp_map=None):
    """
    Everything a pick produces: pads from the chosen layers and cells,
    overlapping ones dropped, each labelled from the layout's own text.
    """
    pads = pads_from_layers(gds_path, layer_keys, min_mm, max_mm,
                            selected_layers, ihp_map)
    pads += pads_from_cells(gds_path, cell_names)
    pads = dedupe(pads)
    return label_pads(gds_path, pads)


def source_gds(target):
    """The GDS an object was imported from, following its chip if needed."""
    path = str(getattr(target, "SourceGDS", "") or "")
    if path:
        return path
    for member in getattr(target, "Group", None) or []:
        path = str(getattr(member, "SourceGDS", "") or "")
        if path:
            return path
    return ""


def chips_in(doc):
    """
    Everything in *doc* that pads can be put on: chip proxies and imports.

    A document holds several dice once multi-die layout starts, so the
    command has to offer a choice rather than assume the only one.
    """
    targets = [obj for obj in getattr(doc, "Objects", None) or []
               if getattr(obj, "IsChipProxy", False)]
    for obj in getattr(doc, "Objects", None) or []:
        if obj.isDerivedFrom("App::DocumentObjectGroup") and \
                (obj.Name.startswith("GDS_Die") or obj.Label.startswith("GDS_Die")):
            targets.append(obj)
    return targets
