# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Chip Proxy — fast, tessellation-free extraction of just what's needed to
place a chip as a lightweight 3-D stand-in during layout: its footprint,
its real stack thickness, and its bond-pad positions.

Why this exists
----------------
Core_Functionality.load_gds() is slow on a real full-chip GDS because it
tessellates every polygon on every loaded layer into OCCT shapes — even
with LOD/contacts-only-3D, the layers that DO load immediately still pay
that cost. For laying out where a chip sits and how it's wired to its
neighbours, none of that per-layer routing geometry is actually needed —
only three things are:

  1. Footprint (XY outline) — via gdstk's own Cell.bounding_box(), a
     native, non-tessellating query (~0.03 s even on a 4000+ cell /
     1M+ polygon GDS, vs. minutes for a full load_gds() pass).
  2. Real stack thickness — from the stackup XML (parse_stackup_xml),
     summed, rather than used per-layer.
  3. Bond-pad positions — PREFERS finding actual bond-pad library-cell
     instances by name (e.g. IHP's "bondpad_70x70"/"bondpad_CDNS_*") via
     get_bond_pad_positions_mm(), falling back to the DT=2/DT=0 PIN-pad
     polygon heuristic (get_pad_positions_mm(), the same one
     Core_Functionality.import_pin_pads_as_contacts() uses) only when no
     such named pad cells exist anywhere in the GDS. The named-cell
     approach exists because on a real, synthesized/padframe-based
     full-chip GDS, DT=2 "PIN" markers are often placed at EVERY internal
     net endpoint (for LVS/verification), not just the physical bond pads
     meant for wire bonding — on one such file this workbench was tested
     against, the DT=2 heuristic alone produced 1109 "pads" scattered
     across the whole die, when the real pad ring — found correctly via
     named bondpad_* cell instances — has exactly 20.

extract_chip_proxy() ties these three together into one plain dict (no
FreeCAD document touched). build_chip_proxy_object() turns that dict into
an actual lightweight block + pad markers in a document. Each pad marker
is a small flat box sized to that pad's REAL footprint (width_mm/height_mm,
captured alongside its position) rather than an abstract point with no
visual relation to the real pad — so a glance at the proxy actually shows
where and how big the real bond pads are. The marker still carries the
EXACT SAME ContactPoint/SourceObject/IsContactPoint property schema as
ContactPointTool.py and SetContactPointsOnFaceCommand.py (wire-bond snap
resolution reads that property directly, never the Shape), so the existing
wire-bonding tool works against these pads completely unchanged regardless
of the marker's shape.

Deliberately NOT shared with import_pin_pads_as_contacts()'s pad-detection
loop: that function is an existing, in-use, untested code path, and this
module's pad-position logic is independent (if similar) rather than
refactored out of it, to avoid any risk of regressing Auto PIN contacts
while adding this. Worth unifying later if that function grows test
coverage.
"""

import gdstk
import Part
import FreeCAD
from FreeCAD import Base

from core.Core_Functionality import (
    parse_lyp,
    parse_map,
    parse_stackup_xml,
    _find_pin_layer_keys,
    _apply_gds_ref_transform,
)

DEFAULT_SUBSTRATE_THICKNESS_UM = 200.0   # typical ground-die thickness fallback
DEFAULT_DIE_THICKNESS_MM       = 0.3     # last-resort fallback with zero PDK data
_MIN_PAD_MM                    = 0.010   # 10 µm — keeps bond pads, drops routing wires

# Cell-name signals for a real, physical bond-pad instance, tried in order
# — "BONDPAD" is the literal pad metal shape (most reliable); "IOPAD" is
# the surrounding I/O cell (buffer + ESD + pad) some flows use directly
# with no separate bondpad sub-cell. Matched as a prefix/substring against
# the upper-cased cell name.
_BONDPAD_NAME_PREFIXES  = ("BONDPAD",)
_IOPAD_NAME_SUBSTRINGS  = ("IOPAD",)


# ── footprint ──────────────────────────────────────────────────────────────────

def get_die_footprint_mm(gds_path) -> tuple:
    """
    Return (xmin, ymin, xmax, ymax) in mm — the true top-cell bounding box,
    via gdstk's native Cell.bounding_box(). Fast: no OCCT, no tessellation,
    independent of how many polygons the GDS actually contains.

    Raises ValueError if the GDS has no top-level geometry to measure.
    """
    lib   = gdstk.read_gds(str(gds_path))
    scale = (lib.unit * 1000.0) if getattr(lib, "unit", None) else 0.001
    cells = lib.top_level() or lib.cells

    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    found = False
    for cell in cells:
        bb = cell.bounding_box()
        if bb is None:
            continue
        (x0, y0), (x1, y1) = bb
        xmin = min(xmin, x0); ymin = min(ymin, y0)
        xmax = max(xmax, x1); ymax = max(ymax, y1)
        found = True

    if not found:
        raise ValueError(f"No geometry found in '{gds_path}' to derive a footprint from.")

    return (xmin * scale, ymin * scale, xmax * scale, ymax * scale)


# ── thickness ──────────────────────────────────────────────────────────────────

def get_die_thickness_mm(stackup_data: dict, substrate_thickness_um: float = None) -> tuple:
    """
    Return (thickness_mm, z0_mm, source).

    thickness_mm is the TOTAL physical die thickness — substrate plus
    interconnect stack — not just the interconnect height that
    parse_stackup_xml()'s per-Layer entries alone would give. z0_mm is
    where the substrate underside sits (<= 0), so the interconnect stack's
    own z0_mm values (as already computed by build_stack_mm_from_xml
    elsewhere) land at/above 0, consistent with the rest of this codebase.

    source is one of:
      "xml_with_substrate"                       — stackup carried a
                                                     <Substrate Offset=.../>
                                                     value (see
                                                     parse_stackup_xml).
      "xml_interconnect_plus_default_substrate"   — stackup had layer
                                                     Zmin/Zmax but no
                                                     substrate offset; a
                                                     default was assumed.
      "no_stackup_default"                        — no usable stackup data
                                                     at all; a flat default
                                                     thickness was used.
    Callers should surface `source` to the user when it isn't
    "xml_with_substrate" — the number is a reasonable estimate, not a
    PDK-verified fact.
    """
    if not stackup_data:
        return (DEFAULT_DIE_THICKNESS_MM, 0.0, "no_stackup_default")

    seen = set()
    zmins, zmaxs = [], []
    for entry in stackup_data.values():
        if not isinstance(entry, dict) or id(entry) in seen:
            continue
        seen.add(id(entry))
        # Only fold in layers at/above the wafer surface (Zmin >= 0) into the
        # interconnect span. Some stackup XMLs also carry backside/substrate
        # -simulation layers (e.g. IHP's BACKSIDEGND/LBE/SUBGND) whose Zmin
        # reaches deep negative — down to the substrate underside itself.
        # Those already represent the same physical depth _substrate_offset_um
        # accounts for below; folding them into interconnect_um here would
        # double-count the substrate on top of adding sub_um.
        if "zmin_um" in entry and "zmax_um" in entry and entry["zmin_um"] >= 0:
            zmins.append(entry["zmin_um"])
            zmaxs.append(entry["zmax_um"])

    if not zmins:
        return (DEFAULT_DIE_THICKNESS_MM, 0.0, "no_stackup_default")

    interconnect_um = max(zmaxs) - min(zmins)

    has_xml_substrate = substrate_thickness_um is None and "_substrate_offset_um" in stackup_data
    sub_um = (
        substrate_thickness_um if substrate_thickness_um is not None
        else stackup_data.get("_substrate_offset_um", DEFAULT_SUBSTRATE_THICKNESS_UM)
    )

    total_um = interconnect_um + sub_um
    z0_mm    = -sub_um / 1000.0
    source   = "xml_with_substrate" if has_xml_substrate else "xml_interconnect_plus_default_substrate"
    return (total_um / 1000.0, z0_mm, source)


# ── bond pads — named pad-cell instances (preferred) ────────────────────────────

def _find_named_cell_refs(cell, matches_fn, _cache, _depth: int = 0, _max_depth: int = 15):
    """
    Recursively search *cell*'s reference tree for instances of cells whose
    name satisfies matches_fn(cell_name_upper) -> bool, returning
    [(ref_cell_name, xmin, ymin, xmax, ymax), ...] — the axis-aligned
    bounding box of each match, in *cell*'s OWN local coordinate frame —
    the caller composes each level's transform on the way back up (see
    get_named_pad_positions_mm). Returning the full bbox (not just a point)
    is what lets callers render an actually pad-sized rectangle instead of
    an abstract snap dot with no relation to the real pad footprint.

    A matching cell is treated as a LEAF: recursion stops there rather than
    also descending into it — a real pad cell may itself contain a nested
    sub-cell that also matches (e.g. IHP's "bondpad_70x70" wraps a
    "bondpad_CDNS_..." shape cell one level down), and reporting both would
    silently double-count the same physical pad.

    Memoized per cell NAME (gdstk requires unique names within a library)
    so a cell type referenced thousands of times (vias, fillers, decap —
    common in a synthesized full-chip GDS) is only ever walked once.
    """
    if _depth > _max_depth:
        return []
    cached = _cache.get(cell.name)
    if cached is not None:
        return cached

    found = []
    for ref in getattr(cell, "references", []) or []:
        ref_cell = getattr(ref, "cell", None)
        if ref_cell is None:
            continue
        origin        = getattr(ref, "origin", (0.0, 0.0)) or (0.0, 0.0)
        rotation      = getattr(ref, "rotation", 0.0) or 0.0
        magnification = getattr(ref, "magnification", 1.0) or 1.0
        x_reflection  = bool(getattr(ref, "x_reflection", False))

        if matches_fn(ref_cell.name.upper()):
            bb = ref_cell.bounding_box()
            # Corners of the matched cell's OWN bbox (its real footprint),
            # not just its placement origin — a pad cell is often not
            # symmetric around its own origin (e.g. an I/O cell whose pad
            # sits off-centre relative to its ESD/driver logic).
            corners = (
                [(bb[0][0], bb[0][1]), (bb[1][0], bb[0][1]),
                 (bb[1][0], bb[1][1]), (bb[0][0], bb[1][1])]
                if bb is not None else [(0.0, 0.0)]
            )
        else:
            nested = _find_named_cell_refs(ref_cell, matches_fn, _cache, _depth + 1, _max_depth)
            if not nested:
                continue
            for (nm, nxmin, nymin, nxmax, nymax) in nested:
                corners = [(nxmin, nymin), (nxmax, nymin), (nxmax, nymax), (nxmin, nymax)]
                transformed = _apply_gds_ref_transform(
                    corners, origin, rotation, magnification, x_reflection
                )
                xs = [p[0] for p in transformed]
                ys = [p[1] for p in transformed]
                found.append((nm, min(xs), min(ys), max(xs), max(ys)))
            continue

        transformed = _apply_gds_ref_transform(
            corners, origin, rotation, magnification, x_reflection
        )
        xs = [p[0] for p in transformed]
        ys = [p[1] for p in transformed]
        found.append((ref_cell.name, min(xs), min(ys), max(xs), max(ys)))

    _cache[cell.name] = found
    return found


# Fallback pad footprint when a matched cell has no polygons of its own to
# size a rectangle from (bb is None) — keeps the marker visibly non-zero
# rather than an invisible degenerate box.
_DEFAULT_PAD_SIZE_MM = 0.05   # 50 µm square


def get_named_pad_positions_mm(gds_path, name_prefixes=(), name_substrings=()) -> list:
    """
    Return [{"name", "x_mm", "y_mm", "width_mm", "height_mm"}, ...] for
    every reference anywhere in the GDS whose CELL NAME starts with one of
    *name_prefixes* or contains one of *name_substrings* (case-insensitive)
    — used to find real bond-pad library-cell instances directly, which is
    far more reliable than DT=2/DT=0 polygon heuristics on a real,
    synthesized full-chip GDS (see get_bond_pad_positions_mm). x_mm/y_mm
    are the pad's true bounding-box CENTER (not just its placement origin,
    which can be off-centre for asymmetric I/O cells); width_mm/height_mm
    are its real footprint size, letting callers draw an accurately-sized
    rectangle instead of an abstract point. Returns [] if nothing matches
    anywhere — not an error, just "this GDS doesn't tag pads by cell name,"
    so the caller can fall back to the polygon-based heuristic.
    """
    prefixes   = tuple(p.upper() for p in name_prefixes)
    substrings = tuple(s.upper() for s in name_substrings)

    def _matches(name_upper: str) -> bool:
        return (any(name_upper.startswith(p) for p in prefixes)
                or any(s in name_upper for s in substrings))

    lib   = gdstk.read_gds(str(gds_path))
    scale = (lib.unit * 1000.0) if getattr(lib, "unit", None) else 0.001
    top_cells = [c for c in (lib.top_level() or lib.cells) if not c.name.startswith("$$$")]
    if not top_cells:
        top_cells = lib.top_level() or lib.cells

    cache: dict = {}
    pads = []
    for tc in top_cells:
        for (name, xmin, ymin, xmax, ymax) in _find_named_cell_refs(tc, _matches, cache):
            w_mm = (xmax - xmin) * scale
            h_mm = (ymax - ymin) * scale
            pads.append({
                "name": name,
                "x_mm": (xmin + xmax) / 2.0 * scale,
                "y_mm": (ymin + ymax) / 2.0 * scale,
                "width_mm":  w_mm if w_mm > 1e-6 else _DEFAULT_PAD_SIZE_MM,
                "height_mm": h_mm if h_mm > 1e-6 else _DEFAULT_PAD_SIZE_MM,
            })
    return pads


def get_bond_pad_positions_mm(gds_path, ihp_map: dict, selected_layers=None, top_n: int = 3) -> list:
    """
    Best bond-pad detection available for *gds_path*, trying (in order):

      1. Named "bondpad_*"-style cell instances — the literal physical pad
         shape, most reliable when present.
      2. Named "*IOPad*"-style cell instances — the surrounding I/O cell
         (buffer + ESD + pad) some flows integrate the pad into directly.
      3. get_pad_positions_mm()'s DT=2/DT=0 polygon heuristic — for bare
         macros with no padframe/library pad cells at all (e.g. a small
         analog block GDS with no I/O ring).

    Falls through to the next strategy only when the current one finds
    nothing, so a real padframe chip never even reaches the polygon
    heuristic that historically produced 1000+ false "pads" on such files.
    """
    named = get_named_pad_positions_mm(gds_path, name_prefixes=_BONDPAD_NAME_PREFIXES)
    if named:
        FreeCAD.Console.PrintMessage(
            f"[ChipProxy] pad detection — strategy: named bondpad_* cell "
            f"instances — {len(named)} pad(s)\n"
        )
        return named

    named = get_named_pad_positions_mm(gds_path, name_substrings=_IOPAD_NAME_SUBSTRINGS)
    if named:
        FreeCAD.Console.PrintMessage(
            f"[ChipProxy] pad detection — strategy: named *IOPad* cell "
            f"instances — {len(named)} pad(s)\n"
        )
        return named

    return get_pad_positions_mm(gds_path, ihp_map, selected_layers, top_n)


def _contains(poly_pts, px, py) -> bool:
    """Ray-casting point-in-polygon (GDS units — avoids mm conversion)."""
    n      = len(poly_pts)
    inside = False
    j      = n - 1
    for i in range(n):
        xi, yi = float(poly_pts[i][0]), float(poly_pts[i][1])
        xj, yj = float(poly_pts[j][0]), float(poly_pts[j][1])
        if ((yi > py) != (yj > py)) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def get_pad_positions_mm(gds_path, ihp_map: dict, selected_layers=None, top_n: int = 3) -> list:
    """
    Return [{"name": edi_name, "x_mm": .., "y_mm": ..}, ...] — bond-pad
    CENTER positions only, using the same DT=2 (PIN marker) / DT=0
    (drawing, S3 fallback) cascading strategy as
    Core_Functionality.import_pin_pads_as_contacts(), via the same
    _find_pin_layer_keys() layer-candidate selection — but never extrudes
    any 3-D pad geometry or touches a FreeCAD document, so this is cheap
    regardless of chip size: just polygon-centroid arithmetic.
    """
    candidates, strategy = _find_pin_layer_keys(
        str(gds_path), ihp_map, selected_layers or [], top_n
    )
    if not candidates:
        FreeCAD.Console.PrintWarning(
            "[ChipProxy] pad detection: no candidate layers found in the GDS file.\n"
        )
        return []

    try:
        lib = gdstk.read_gds(str(gds_path))
    except Exception as exc:
        FreeCAD.Console.PrintError(f"[ChipProxy] pad detection: cannot read GDS: {exc}\n")
        return []

    scale     = (lib.unit * 1000.0) if getattr(lib, "unit", None) else 0.001
    top_cells = lib.top_level() or lib.cells

    pads = []
    for lid, dt, edi_name in candidates:
        for cell in top_cells:
            if dt == 2:
                # DT=2 PIN marker: contact location = centroid of the DT=0
                # pad polygon that contains it (matches
                # import_pin_pads_as_contacts()'s "middle of pad" choice).
                pin_polys  = cell.get_polygons(layer=lid, datatype=2)
                draw_polys = cell.get_polygons(layer=lid, datatype=0)

                pad_index = []
                for p0 in draw_polys:
                    pts = p0.points
                    xs  = [float(p[0]) for p in pts]
                    ys  = [float(p[1]) for p in pts]
                    w   = (max(xs) - min(xs)) * scale
                    h   = (max(ys) - min(ys)) * scale
                    if w >= _MIN_PAD_MM and h >= _MIN_PAD_MM:
                        pad_index.append((pts, (min(xs), min(ys), max(xs), max(ys))))

                used = set()
                for pp in pin_polys:
                    pts2 = pp.points
                    cx_g = sum(float(p[0]) for p in pts2) / len(pts2)
                    cy_g = sum(float(p[1]) for p in pts2) / len(pts2)

                    found = None
                    for idx, (pts_raw, (xlo, ylo, xhi, yhi)) in enumerate(pad_index):
                        if idx in used:
                            continue
                        if cx_g < xlo or cx_g > xhi or cy_g < ylo or cy_g > yhi:
                            continue
                        if _contains(pts_raw, cx_g, cy_g):
                            found = idx
                            break

                    if found is not None:
                        used.add(found)
                        _, (xlo, ylo, xhi, yhi) = pad_index[found]
                        pads.append({
                            "name": edi_name,
                            "x_mm": ((xlo + xhi) / 2.0) * scale,
                            "y_mm": ((ylo + yhi) / 2.0) * scale,
                            "width_mm":  (xhi - xlo) * scale,
                            "height_mm": (yhi - ylo) * scale,
                        })
                    else:
                        # No DT=0 pad polygon found — only the tiny DT=2
                        # marker itself is known, so size the rectangle
                        # from ITS extent rather than a default, when that
                        # extent is non-degenerate.
                        pxs = [float(p[0]) for p in pts2]
                        pys = [float(p[1]) for p in pts2]
                        pw  = (max(pxs) - min(pxs)) * scale
                        ph  = (max(pys) - min(pys)) * scale
                        pads.append({
                            "name": edi_name,
                            "x_mm": cx_g * scale,
                            "y_mm": cy_g * scale,
                            "width_mm":  pw if pw > 1e-6 else _DEFAULT_PAD_SIZE_MM,
                            "height_mm": ph if ph > 1e-6 else _DEFAULT_PAD_SIZE_MM,
                        })
            else:
                # DT=0 drawing layer (S3 fallback): pad-sized polygons only.
                for p0 in cell.get_polygons(layer=lid, datatype=0):
                    pts = p0.points
                    xs  = [float(p[0]) for p in pts]
                    ys  = [float(p[1]) for p in pts]
                    w   = (max(xs) - min(xs)) * scale
                    h   = (max(ys) - min(ys)) * scale
                    if w >= _MIN_PAD_MM and h >= _MIN_PAD_MM:
                        pads.append({
                            "name": edi_name,
                            "x_mm": ((min(xs) + max(xs)) / 2.0) * scale,
                            "y_mm": ((min(ys) + max(ys)) / 2.0) * scale,
                            "width_mm":  w,
                            "height_mm": h,
                        })

    FreeCAD.Console.PrintMessage(
        f"[ChipProxy] pad detection — strategy: {strategy} — {len(pads)} pad(s)\n"
    )
    return pads


# ── one-shot extraction ─────────────────────────────────────────────────────────

def extract_chip_proxy(gds_path, lyp_path=None, map_path=None, xml_path=None,
                        substrate_thickness_um=None, top_n_pad_layers: int = 3) -> dict:
    """
    One-shot, tessellation-free extraction of everything needed to place a
    chip as a lightweight layout proxy. Returns a plain dict — no FreeCAD
    document objects are created here; see build_chip_proxy_object().

    lyp_path/map_path/xml_path are all optional: without a map, pad names
    fall back to generic layer identifiers; without a stackup XML, die
    thickness falls back to a flat default (see get_die_thickness_mm) and
    "thickness_source" in the result reflects that so callers can warn
    the user the number is a rough guess.
    """
    ihp_map = parse_map(map_path) if map_path else {}
    selected_layers = []
    if lyp_path:
        layers, _ = parse_lyp(lyp_path)
        selected_layers = layers
    stackup_data = parse_stackup_xml(xml_path) if xml_path else {}

    xmin, ymin, xmax, ymax = get_die_footprint_mm(gds_path)
    thickness_mm, z0_mm, thickness_source = get_die_thickness_mm(
        stackup_data, substrate_thickness_um
    )
    pads = get_bond_pad_positions_mm(gds_path, ihp_map, selected_layers, top_n_pad_layers)

    return {
        "source_gds": str(gds_path),
        "source_lyp": str(lyp_path) if lyp_path else None,
        "source_map": str(map_path) if map_path else None,
        "source_xml": str(xml_path) if xml_path else None,
        "footprint_mm": (xmin, ymin, xmax, ymax),
        "thickness_mm": thickness_mm,
        "z0_mm": z0_mm,
        "thickness_source": thickness_source,
        "pads": pads,
    }


# ── FreeCAD object construction ─────────────────────────────────────────────────

def _unique_chip_name(doc, name: str) -> str:
    """
    Return a base name whose derived "{base}_Proxy"/"{base}_Block" object
    names won't collide with an existing chip proxy — handles importing
    the same chip GDS twice, or two different chips whose file names
    happen to match (a real scenario once multiple dice are placed in one
    package). Checked directly against the exact names build_chip_proxy_object
    is about to create, rather than doc.getUniqueObjectName(name) alone,
    which only guards the bare "name" string — never actually used as an
    object name here — and would silently let "{name}_Proxy" collide,
    leaving FreeCAD to auto-suffix it in a way this module's own code
    (and the labels shown to the user) wouldn't know about.
    """
    candidate = name
    n = 1
    while doc.getObject(f"{candidate}_Proxy") is not None or doc.getObject(f"{candidate}_Block") is not None:
        n += 1
        candidate = f"{name}_{n:02d}"
    return candidate


def build_chip_proxy_object(doc, proxy_data: dict, name: str = "Chip"):
    """
    Create a lightweight extruded-block proxy + ContactPoint markers in
    *doc* from data returned by extract_chip_proxy() — the fast-to-render
    stand-in for a chip during layout, before (optionally, later) promoting
    it to full imported GDS geometry.

    Returns the block object (the proxy's Part::Feature), with the pad
    markers and the block itself grouped under one "<name> (Proxy)" group.
    """
    xmin, ymin, xmax, ymax = proxy_data["footprint_mm"]
    w = xmax - xmin
    h = ymax - ymin
    if w <= 0 or h <= 0:
        raise ValueError(f"Degenerate footprint ({w:.4f} x {h:.4f} mm) — cannot build a proxy block.")
    t  = max(proxy_data["thickness_mm"], 1e-4)
    z0 = proxy_data["z0_mm"]

    clean_name = name.replace(" ", "_")
    base_name  = _unique_chip_name(doc, clean_name)
    # Reflect any de-duplication suffix in the user-facing label too, so two
    # chips sharing a source file name still read as distinct in the tree.
    display_name = name if base_name == clean_name else f"{name} ({base_name.rsplit('_', 1)[-1]})"

    grp = doc.addObject("App::DocumentObjectGroup", f"{base_name}_Proxy")
    grp.Label = f"{display_name} (Proxy)"

    block = doc.addObject("Part::Feature", f"{base_name}_Block")
    block.Shape = Part.makeBox(w, h, t, Base.Vector(xmin, ymin, z0))
    block.Label = f"{display_name} Die Block"
    if FreeCAD.GuiUp:
        block.ViewObject.ShapeColor   = (0.35, 0.35, 0.40)
        block.ViewObject.Transparency = 40

    block.addProperty(
        "App::PropertyBool", "IsChipProxy", "ChipProxy",
        "Lightweight layout-stage stand-in for a chip — footprint and "
        "stack height only, no per-layer routing geometry",
    )
    block.addProperty("App::PropertyString", "SourceGDS", "ChipProxy", "Source GDS file")
    block.addProperty("App::PropertyString", "SourceLYP", "ChipProxy", "Source LYP file")
    block.addProperty("App::PropertyString", "SourceMAP", "ChipProxy", "Source IHP map file")
    block.addProperty("App::PropertyString", "SourceXML", "ChipProxy", "Source stackup XML file")
    block.addProperty(
        "App::PropertyString", "ThicknessSource", "ChipProxy",
        "How the die thickness was derived — see core.chip_proxy.get_die_thickness_mm",
    )

    block.IsChipProxy     = True
    block.SourceGDS       = proxy_data.get("source_gds") or ""
    block.SourceLYP       = proxy_data.get("source_lyp") or ""
    block.SourceMAP       = proxy_data.get("source_map") or ""
    block.SourceXML       = proxy_data.get("source_xml") or ""
    block.ThicknessSource = proxy_data.get("thickness_source") or ""

    grp.addObject(block)

    # Pad markers are small flat boxes sized to each pad's REAL footprint
    # (width_mm/height_mm from get_bond_pad_positions_mm — falls back to
    # _DEFAULT_PAD_SIZE_MM for older/hand-built proxy_data lacking those
    # keys) sitting on top of the die block — a recognisable pad shape at
    # a glance, not an abstract point unrelated to the real pad size.
    # ContactPoint/SourceObject/IsContactPoint still use the EXACT SAME
    # schema as ContactPointTool.py / SetContactPointsOnFaceCommand.py, so
    # the existing wire-bonding tool snaps to these completely unchanged —
    # resolve_snap_point() reads the ContactPoint property directly, never
    # the marker's own Shape, so this is safe regardless of marker shape.
    pad_t = max(min(t * 0.05, 0.01), 1e-4)   # thin — a visual pad thickness, not structural
    for i, pad in enumerate(proxy_data.get("pads", []), start=1):
        pad_w = pad.get("width_mm", _DEFAULT_PAD_SIZE_MM) or _DEFAULT_PAD_SIZE_MM
        pad_h = pad.get("height_mm", _DEFAULT_PAD_SIZE_MM) or _DEFAULT_PAD_SIZE_MM

        marker = doc.addObject("Part::Feature", f"{base_name}_Pad_{i:03d}")
        marker.Shape = Part.makeBox(
            pad_w, pad_h, pad_t,
            Base.Vector(pad["x_mm"] - pad_w / 2.0, pad["y_mm"] - pad_h / 2.0, z0 + t),
        )
        marker.Label = f"{display_name} {pad.get('name', 'Pad')} {i}"

        # Wire-bond snap point: the top face centre of the pad marker —
        # same "top of the visible pad surface" convention ContactPointTool
        # uses for real pad geometry.
        pt = Base.Vector(pad["x_mm"], pad["y_mm"], z0 + t + pad_t)

        marker.addProperty("App::PropertyVector", "ContactPoint", "Wirebond",
                            "Snap point for wire bonding")
        marker.addProperty("App::PropertyString", "SourceObject", "Wirebond",
                            "Source object this point belongs to")
        marker.addProperty("App::PropertyBool", "IsContactPoint", "Wirebond",
                            "Wire-bond contact point marker")
        marker.ContactPoint   = pt
        marker.SourceObject   = block.Name
        marker.IsContactPoint = True

        if FreeCAD.GuiUp:
            marker.ViewObject.ShapeColor   = (0.90, 0.30, 0.10)   # orange — die-side
            marker.ViewObject.Transparency = 0
            marker.ViewObject.DisplayMode  = "Flat Lines"

        grp.addObject(marker)

    doc.recompute()
    FreeCAD.Console.PrintMessage(
        f"[ChipProxy] Built '{display_name}': {w:.3f} x {h:.3f} x {t:.3f} mm "
        f"({len(proxy_data.get('pads', []))} pad(s)), "
        f"thickness source: {proxy_data.get('thickness_source')}\n"
    )
    return block
