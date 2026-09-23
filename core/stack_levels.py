# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The levels of the PDK stack a layout does not draw on.

A PDK defines more levels than any one design uses. An SG13G2 layout that
routes on TopMetal1 and TopMetal2 still has Activ, Cont, Metal1–5, Via1–4,
MIM and Vmim underneath it in the process — they are simply empty in this
particular chip. The import builds geometry only for what the GDS contains,
so those levels are missing from the model entirely, and the 3-D view no
longer resembles the stackup the PDK describes: metal floating at 6.4 µm
with nothing between it and the silicon.

This module closes that gap. It compares the stackup's levels against what
the layout actually draws and builds a die-sized slab for each level that is
missing, at the Z position and thickness the PDK states — the same shape the
LOD manager already uses for a layer that is in the file but not yet loaded,
and marked just as plainly, so a level that is empty in the design is never
mistaken for one that was imported.

What is deliberately left out:

  * Levels below the die surface (SG13G2's SUBGND and BACKSIDEGND sit at
    negative Z) — those are simulation reference planes, and the volume they
    occupy is already modelled as epi and silicon by core.substrate.
  * Dielectrics — the oxide between the levels is core.substrate's business
    (see build_dielectric_fill), not a level of its own.

The slabs are representations of empty process levels, not parts: a level
the layout does not draw is not metal in the real die, so they stay out of
the material assignment, the thermal export and the design rule check, and
say so in their own label.

Qt-free: ui/ and gds/ do the dialogs, this does the derivation.
"""

import FreeCAD
import Part

IS_UNUSED = "IsUnusedStackLayer"
LEVEL_NAME = "StackLevelName"
GROUP = "StackLevel"        # the property group these live in, for the editor

# Thin enough to be invisible is still worth building — the level exists.
MIN_THICKNESS_MM = 1e-6

_LEVEL_TYPES = ("conductor", "via")

# Levels at or below the die surface are the substrate's business. A hair of
# tolerance so a level stated as exactly 0.0 counts as being on the surface.
_Z_TOL_UM = 1e-9


def levels(stackup_data):
    """
    Every interconnect level the stackup declares, bottom-first.

    [{"name", "layer", "datatype", "type", "material", "t_mm", "z0_mm"}, ...]

    The name is the stackup's own spelling — "TopMetal2", not the upper-cased
    form the lookup dict is keyed by.
    """
    data = stackup_data or {}
    found = {}
    for key, entry in data.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if not isinstance(entry, dict) or "zmin_um" not in entry:
            continue
        if str(entry.get("type", "")).lower() not in _LEVEL_TYPES:
            continue
        if float(entry["zmin_um"]) < -_Z_TOL_UM:
            continue                     # below the die surface
        layer = int(entry.get("gds_layer", -1))
        if layer < 0:
            continue                     # no GDS layer to compare against
        thickness = float(entry.get("thickness_um", 0.0)) / 1000.0
        if thickness < MIN_THICKNESS_MM:
            continue
        found[id(entry)] = {
            "name": entry.get("name") or key,
            "layer": layer,
            "datatype": entry.get("gds_datatype"),
            "type": str(entry.get("type", "")).lower(),
            "material": entry.get("material") or key,
            "t_mm": thickness,
            "z0_mm": float(entry["zmin_um"]) / 1000.0,
        }
    return sorted(found.values(), key=lambda e: (e["z0_mm"], e["name"]))


def keys_in_gds(gds_path):
    """Every (layer, datatype) the GDS draws something on."""
    from core.Core_Functionality import get_gds_layer
    return set(get_gds_layer(str(gds_path)) or ())


def keys_in_document(doc, objects=None):
    """
    Every (layer, datatype) this document already has an object for.

    Used when completing the stack of a chip that is already imported, where
    the GDS may not be at hand any more. It counts the LOD manager's
    not-yet-loaded placeholders too — a layer waiting to be loaded is one the
    layout draws on, and must not be filled in as empty.
    """
    source = objects if objects is not None else (getattr(doc, "Objects", None) or [])
    keys = set()
    for obj in source:
        layer = getattr(obj, "GDSLayerID", None)
        if layer is None or getattr(obj, IS_UNUSED, False):
            continue
        keys.add((int(layer), int(getattr(obj, "GDSDatatype", 0) or 0)))
    return keys


def is_used(level, keys):
    """
    Whether *level* is drawn, given the (layer, datatype) pairs that exist.

    A stackup that states a datatype is matched exactly — SKY130 needs that,
    where met1 is 68/20 and the via above it is 68/44. A stackup that states
    only a layer number matches any datatype on it: SG13G2 draws Metal1 as
    8/0, its pin as 8/2 and its label as 8/25, and all three mean the level
    is in use.
    """
    datatype = level.get("datatype")
    if datatype is not None:
        return (level["layer"], int(datatype)) in keys
    return any(layer == level["layer"] for layer, _ in keys)


def unused(stackup_data, keys):
    """The levels of the stackup that nothing in *keys* draws on."""
    return [level for level in levels(stackup_data) if not is_used(level, keys)]


def used(stackup_data, keys):
    """The levels of the stackup that are drawn — the other half of the pair."""
    return [level for level in levels(stackup_data) if is_used(level, keys)]


def stack_shift_mm(stack_mm, stackup_data):
    """
    How far the imported layers were slid from the heights the PDK states.

    "Drop to die surface" moves a partial import down so its lowest layer
    sits on the silicon. Levels built from the PDK's own numbers would then
    float above the layers they belong between, so the same shift is applied
    to them. Derived by comparing one imported layer against the stackup
    rather than plumbed through the import, so it is right whichever way the
    stack was built.
    """
    if not stack_mm or not stackup_data:
        return 0.0
    for key, entry in stack_mm.items():
        if not isinstance(entry, dict) or not entry.get("from_xml"):
            continue
        stated = stackup_data.get(key)
        if stated is None and isinstance(key, tuple):
            stated = stackup_data.get(key[0])
        if not isinstance(stated, dict) or "zmin_um" not in stated:
            continue
        return float(stated["zmin_um"]) / 1000.0 - float(entry["z0_mm"])
    return 0.0


# Two levels that share a height must not share the same ground, or their
# slabs interpenetrate. Side by side they need a little air between them to
# read as two things — a fraction of the lane, so it scales with the die.
_LANE_GAP_FRACTION = 0.04


def overlaps(a, b, tol_mm=1e-9):
    """
    Whether two levels occupy the same heights.

    Touching is not overlapping: MIM ends at 5.7540 um and Vmim begins there,
    which is a shared face, not a shared volume.
    """
    return (a["z0_mm"] < b["z0_mm"] + b["t_mm"] - tol_mm
            and b["z0_mm"] < a["z0_mm"] + a["t_mm"] - tol_mm)


def lanes(level_list, tol_mm=1e-9):
    """
    Where each level sits across the die: {name: (lane, lanes_in_its_group)}.

    A stackup is not a simple pile. On SG13G2 the MIM capacitor sits inside
    TopVia1's span — TopVia1 runs 5.5800 to 6.4303 um while MIM occupies
    5.6043 to 5.7540 and Vmim carries on from there to 6.4303 — so slabs
    built across the whole die for all three would be inside one another.
    The PDK's own stackup drawing answers this by giving levels that share a
    height their own column, and this is the same answer: levels that overlap
    are given neighbouring strips of the die instead of the whole of it.

    Levels that overlap nothing keep the die to themselves, so the ordinary
    case is unchanged. Within a group the lanes come from a greedy pass in
    height order, which for intervals uses no more lanes than the deepest
    overlap — two here, not three, because MIM and Vmim only touch.
    """
    ordered = sorted(level_list, key=lambda e: (e["z0_mm"], e["name"]))
    lane_of = {}
    active = []                      # (z_top, lane) still overlapping
    for level in ordered:
        top = level["z0_mm"] + level["t_mm"]
        active = [a for a in active if a[0] > level["z0_mm"] + tol_mm]
        taken = {lane for _, lane in active}
        lane = 0
        while lane in taken:
            lane += 1
        lane_of[level["name"]] = lane
        active.append((top, lane))

    # How many lanes each level has to share with: everything it is
    # transitively bound to by an overlap, so a level in a chain of three is
    # split three ways and one on its own is not split at all.
    groups = _overlap_groups(ordered, tol_mm)
    out = {}
    for group in groups:
        count = max(lane_of[level["name"]] for level in group) + 1
        for level in group:
            out[level["name"]] = (lane_of[level["name"]], count)
    return out


def _overlap_groups(ordered, tol_mm=1e-9):
    """The levels split into sets that are linked by overlapping."""
    parent = {level["name"]: level["name"] for level in ordered}

    def find(name):
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if b["z0_mm"] >= a["z0_mm"] + a["t_mm"] - tol_mm:
                break                # sorted by height: nothing later touches it
            if overlaps(a, b, tol_mm):
                parent[find(b["name"])] = find(a["name"])

    grouped = {}
    for level in ordered:
        grouped.setdefault(find(level["name"]), []).append(level)
    return list(grouped.values())


def shares_height_with(level, level_list):
    """The names of the levels *level* overlaps — for saying so on the part."""
    return [other["name"] for other in level_list
            if other["name"] != level["name"] and overlaps(level, other)]


def _lane_span(xmin, width, lane, count):
    """(x0, width) of one lane across the die."""
    if count <= 1:
        return xmin, width
    slice_width = width / float(count)
    gap = slice_width * _LANE_GAP_FRACTION
    return xmin + lane * slice_width + gap / 2.0, slice_width - gap


# ── geometry ───────────────────────────────────────────────────────────────

def _colour(stackup_data, material):
    from core.substrate import material_colour
    return material_colour(stackup_data, material, (0.55, 0.55, 0.60))


def shift_from_objects(objects, stackup_data, placement=None):
    """
    How far the layers in *objects* sit from the heights the PDK states.

    The same question stack_shift_mm() answers from the import's own stacking
    dictionary, asked of a document instead — which is what completing the
    stack of a chip imported earlier has to work from. Measured against every
    layer that the stackup knows, and the median taken: one layer collapsed
    to a block or padded by dummy fill should not move the whole stack.

    Heights are read in the chip's own frame, so a chip already placed on a
    carrier is measured by where its layers sit within it, not by where the
    carrier put it.
    """
    from statistics import median

    base_z = placement.Base.z if placement is not None else 0.0
    differences = []
    for obj in objects or []:
        layer = getattr(obj, "GDSLayerID", None)
        if layer is None or getattr(obj, IS_UNUSED, False):
            continue
        datatype = int(getattr(obj, "GDSDatatype", 0) or 0)
        stated = (stackup_data or {}).get((int(layer), datatype))
        if stated is None:
            stated = (stackup_data or {}).get(int(layer))
        if not isinstance(stated, dict) or "zmin_um" not in stated:
            continue
        try:
            z0 = obj.Shape.BoundBox.ZMin - base_z
        except Exception:
            continue
        differences.append(float(stated["zmin_um"]) / 1000.0 - z0)
    return median(differences) if differences else 0.0


def build(doc, footprint_mm, entries, stackup_data=None, group=None,
          shift_mm=0.0, name_prefix="GDS", placement=None, lane_levels=None):
    """
    Build one slab per level in *entries*. Returns the objects.

    Each slab covers the die outline in XY and the level's own Z range, so
    the stack reads at a glance the way the PDK's own stackup drawing does —
    except where levels share a height, which get neighbouring strips of the
    die rather than the whole of it, so that no two slabs are inside one
    another. See lanes().

    Lanes are worked out over *lane_levels*, the whole stackup by default and
    not merely the levels being built: a level the layout does draw on still
    has to be left the room its own geometry occupies. So on a layout that
    uses TopVia1 but not MIM, the MIM slab takes the strip beside TopVia1's
    rather than the strip through it.

    *footprint_mm* is in the chip's own coordinates and *placement* is where
    that chip sits, so a stack completed after the die has been placed on a
    carrier lands on the die rather than back at the origin.
    """
    if not entries:
        return []

    if lane_levels is None:
        lane_levels = levels(stackup_data) if stackup_data else list(entries)
    lane_of = lanes(lane_levels)

    xmin, ymin, xmax, ymax = (float(v) for v in footprint_mm)
    width, length = xmax - xmin, ymax - ymin
    if width <= 0.0 or length <= 0.0:
        raise ValueError(
            f"Degenerate die footprint ({width:.4f} x {length:.4f} mm) — "
            f"cannot build the unused stack levels on it.")

    created = []
    for entry in entries:
        z0 = entry["z0_mm"] - shift_mm
        lane, lane_count = lane_of.get(entry["name"], (0, 1))
        x0, lane_width = _lane_span(xmin, width, lane, lane_count)
        obj = doc.addObject("Part::Feature",
                            f"{name_prefix}_Level_{entry['name']}")
        obj.Shape = Part.makeBox(lane_width, length, entry["t_mm"],
                                 FreeCAD.Vector(x0, ymin, z0))
        if placement is not None:
            obj.Placement = placement
        # Say in the label that this level is empty in THIS design. A
        # die-sized box carrying a layer's name is otherwise
        # indistinguishable from that layer imported and simplified to its
        # bounding box, which is exactly the confusion the LOD manager's
        # "[not loaded]" suffix exists to prevent.
        sharing = shares_height_with(entry, lane_levels)
        obj.Label = (f"{entry['name']} ({entry['t_mm'] * 1000.0:.3f} µm)"
                     f"  [not in the layout]")

        for prop, value, doc_text in (
                (IS_UNUSED, True,
                 "A level the PDK defines that this layout does not draw on — "
                 "its position and thickness, not geometry of the design"),
                (LEVEL_NAME, entry["name"], "The stackup's name for this level"),
                ("SharesHeightWith", ", ".join(sharing),
                 "Levels that occupy the same heights as this one, and "
                 "therefore stand beside it rather than across the whole die"),
                ("StackMaterial", entry["material"],
                 "Material named for this level in the stackup XML")):
            kind = ("App::PropertyBool" if isinstance(value, bool)
                    else "App::PropertyString")
            if not hasattr(obj, prop):
                obj.addProperty(kind, prop, GROUP, doc_text)
            setattr(obj, prop, value)
        for prop, value in (("GDSLayerID", entry["layer"]),
                            ("GDSDatatype", int(entry.get("datatype") or 0))):
            if not hasattr(obj, prop):
                obj.addProperty("App::PropertyInteger", prop, "LOD", prop)
            setattr(obj, prop, value)

        if FreeCAD.GuiUp and getattr(obj, "ViewObject", None) is not None:
            obj.ViewObject.ShapeColor = _colour(stackup_data, entry["material"])
            obj.ViewObject.LineColor = (0.30, 0.30, 0.30)
            obj.ViewObject.Transparency = 80      # ghosted — nothing is drawn here
        if group is not None:
            group.addObject(obj)
        created.append(obj)

    doc.recompute()
    FreeCAD.Console.PrintMessage(
        f"[Stack] {len(created)} PDK level(s) the layout does not use, built "
        f"at their stated heights: "
        f"{', '.join(e['name'] for e in entries)}\n")
    return created


def existing(doc):
    """The unused-level slabs already in *doc*."""
    return [obj for obj in (getattr(doc, "Objects", None) or [])
            if getattr(obj, IS_UNUSED, False)]


def remove(doc):
    """Delete them again. Returns how many went."""
    gone = 0
    for obj in existing(doc):
        doc.removeObject(obj.Name)
        gone += 1
    if gone:
        doc.recompute()
    return gone


def describe(stackup_data, keys):
    """A short summary of which levels are drawn and which are not."""
    drawn = used(stackup_data, keys)
    empty = unused(stackup_data, keys)
    if not drawn and not empty:
        return "The stackup declares no interconnect levels."
    lines = [f"{len(drawn)} of {len(drawn) + len(empty)} PDK levels are drawn "
             f"in this layout."]
    if drawn:
        lines.append("Drawn: " + ", ".join(e["name"] for e in drawn))
    if empty:
        lines.append("Empty: " + ", ".join(e["name"] for e in empty))
    return "\n".join(lines)
