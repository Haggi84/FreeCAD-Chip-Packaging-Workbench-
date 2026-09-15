# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for exact-KLayout import — build the layout as drawn.

The workbench has six independent mechanisms that trade geometry for speed
(per-layer polygon threshold, total polygon budget, micro-area scan,
dummy-fill collapsing, area filtering, outline decimation) plus two that
defer or re-render it (level-of-detail loading, fast-mesh baking). Each is
worth having, and each on its own makes the document disagree with KLayout.
They can only be trusted as a SET, which is why exact mode is one flag rather
than eight, and why the check below is "solid count == polygon count" per
layer rather than a spot check on one of them.

Units are pinned against KLayout's own reported numbers rather than against a
re-derivation of the same conversion this code performs, so a scale error
cannot pass by being wrong in both places.
"""

import os

from _harness import TestCase, REPO_ROOT

import gdstk
from core import Core_Functionality as CF
from core.Core_Functionality import parse_lyp, parse_map, parse_stackup_xml

_IHP = os.path.join(REPO_ROOT, "resources", "stack_info", "IHP-PDK_SG13G2")
# Small (0.1 MB) but carries 1,056 polygons across six layers, two of which
# are dense sub-micron via arrays — the exact case every simplification
# mechanism collapses.
_GDS = os.path.join(REPO_ROOT, "samples", "IC_Pad_EdgeSeal.boundary.gds")


def run():
    tc = TestCase("klayout_exact")
    if not (os.path.isfile(_GDS) and os.path.isfile(os.path.join(_IHP, "sg13g2.lyp"))):
        return tc.results
    _check_geometry(tc)
    _check_units(tc)
    _check_boundary_layers(tc)
    _check_wiring(tc)
    return tc.results


def _check_boundary_layers(tc):
    """
    ".boundary" layers are annotation and must be drawn flat on the die
    surface, not extruded.

    They mark an extent rather than describing a film that exists at a
    height, and none of them appear in the stackup XML — so they were being
    given a rank-based fallback Z and extruded into a slab floating at an
    arbitrary height. How arbitrary: the same EdgeSeal.boundary came out at
    0.0-0.2 um with one set of layers loaded and 9.0-9.2 um with another,
    purely because the fallback ranks whatever it happens to be given.
    """
    from core.lod_import import boundary_layer_keys

    _lib, counts, present, ihp, stack_mm = _fixture()
    all_layers, _ = parse_lyp(os.path.join(_IHP, "sg13g2.lyp"))
    keys = boundary_layer_keys(all_layers)
    names = {(L["layer_id"], L["datatype"]): L["name"] for L in all_layers}

    tc.check("boundary_layer_keys: finds EdgeSeal.boundary (39/4)",
              (39, 4) in keys)
    tc.check("boundary_layer_keys: finds prBoundary.boundary (189/4)",
              (189, 4) in keys)
    tc.check("boundary_layer_keys: every hit is a '.boundary' purpose",
              all(str(names.get(k, "")).lower().endswith(".boundary")
                  for k in keys),
              str([names.get(k) for k in sorted(keys)][:5]))

    # The metals must not be swept in — they are the geometry, and drawing
    # them flat would delete the entire stack height.
    for key, label in (((134, 0), "TopMetal2"), ((126, 0), "TopMetal1"),
                       ((67, 0), "Metal5"), ((125, 0), "TopVia1")):
        tc.check(f"boundary_layer_keys: leaves {label} alone",
                  key not in keys)

    built = {}
    for entry in CF.load_gds(
            _GDS, present, transform=None, preview_2d=False,
            compound_per_layer=True, min_area_mm2=0.0, decimate_tol_mm=0.0,
            skip_fill_datatype=False, fill_as_bbox=True,
            fill_layer_keys=set(), flat_layer_keys=keys,
            force_bbox_keys=set(), exclude_auto_bbox_keys=set(),
            protect_via_keys=set(), ihp_map=ihp, stack_mm=stack_mm,
            contacts_only_3d=False, mesh_3d=False, use_cache=False,
            auto_bbox_threshold=0, exact_geometry=True):
        built[(entry.get("layer_id"), entry.get("datatype"))] = entry.get("shape")

    seal = built.get((39, 4))
    if tc.check("EdgeSeal.boundary is built", seal is not None):
        bb = seal.BoundBox
        tc.check("EdgeSeal.boundary has zero height",
                  abs(bb.ZLength) < 1e-12, f"height {bb.ZLength * 1000.0} um")
        tc.check("EdgeSeal.boundary sits at z=0, directly on top of the epi",
                  abs(bb.ZMin) < 1e-12 and abs(bb.ZMax) < 1e-12,
                  f"z {bb.ZMin} .. {bb.ZMax}")
        tc.check("EdgeSeal.boundary is a face, not a solid",
                  len(seal.Solids) == 0 and len(seal.Faces) >= 1,
                  f"{len(seal.Solids)} solids, {len(seal.Faces)} faces")
        tc.check("EdgeSeal.boundary keeps its full 120 x 300 um outline",
                  abs(bb.XLength * 1000.0 - 120.0) < 1e-6
                  and abs(bb.YLength * 1000.0 - 300.0) < 1e-6,
                  f"{bb.XLength * 1000.0} x {bb.YLength * 1000.0} um")

    # Flattening the annotation must not have flattened the stack with it.
    top2 = built.get((134, 0))
    if top2 is not None:
        bb = top2.BoundBox
        tc.check("TopMetal2 keeps its 3 um thickness and its height in the "
                  "stack — only the annotation went flat",
                  abs(bb.ZLength * 1000.0 - 3.0) < 1e-6
                  and abs(bb.ZMin * 1000.0 - 11.2303) < 1e-6,
                  f"z {bb.ZMin * 1000.0} .. {bb.ZMax * 1000.0} um")


def _fixture():
    all_layers, _ = parse_lyp(os.path.join(_IHP, "sg13g2.lyp"))
    ihp = parse_map(os.path.join(_IHP, "sg13g2.map"))
    stack = parse_stackup_xml(os.path.join(_IHP, "SG13G2_200um.xml"))
    stack_mm = CF.build_stack_mm_from_xml(all_layers, ihp, stack)

    lib = gdstk.read_gds(_GDS)
    counts = {}
    for cell in lib.top_level():
        for poly in cell.get_polygons(depth=None):
            key = (poly.layer, poly.datatype)
            counts[key] = counts.get(key, 0) + 1
    present = [L for L in all_layers
               if (L["layer_id"], L["datatype"]) in counts]
    return lib, counts, present, ihp, stack_mm


def _build(present, ihp, stack_mm, **over):
    kw = dict(transform=None, preview_2d=False, compound_per_layer=True,
              min_area_mm2=0.0, decimate_tol_mm=0.0, skip_fill_datatype=False,
              fill_as_bbox=True, fill_layer_keys=set(), flat_layer_keys=set(),
              force_bbox_keys=set(), exclude_auto_bbox_keys=set(),
              protect_via_keys=set(), ihp_map=ihp, stack_mm=stack_mm,
              contacts_only_3d=False, mesh_3d=False, use_cache=False,
              auto_bbox_threshold=5_000)
    kw.update(over)
    out = {}
    for entry in CF.load_gds(_GDS, present, **kw):
        shape = entry.get("shape")
        out[(entry.get("layer_id"), entry.get("datatype"))] = (
            len(shape.Solids) if shape is not None else 0)
    return out


def _check_geometry(tc):
    _lib, counts, present, ihp, stack_mm = _fixture()
    wanted = {(L["layer_id"], L["datatype"]) for L in present}
    names = {(L["layer_id"], L["datatype"]): L["name"] for L in present}

    exact = _build(present, ihp, stack_mm, exact_geometry=True)
    for key in sorted(wanted):
        tc.check(f"exact: {names[key]} ({key[0]}/{key[1]}) builds every "
                  f"polygon KLayout draws",
                  exact.get(key, 0) == counts[key],
                  f"built {exact.get(key)}, KLayout has {counts[key]}")

    total_built = sum(exact.get(k, 0) for k in wanted)
    total_klayout = sum(counts[k] for k in wanted)
    tc.check("exact: the whole layout matches polygon for polygon",
              total_built == total_klayout,
              f"{total_built} vs {total_klayout}")

    # Pins the difference the flag makes. Without it the two dense via
    # arrays collapse to one box each via the micro-area scan — they are
    # sub-micron, which is what that scan targets.
    default = _build(present, ihp, stack_mm)
    collapsed = [k for k in wanted if default.get(k, 0) < counts[k]]
    tc.check("without the flag, dense sub-micron layers ARE collapsed — "
              "otherwise this test proves nothing",
              len(collapsed) >= 2, f"collapsed: {sorted(collapsed)}")
    for key in collapsed:
        tc.check(f"default mode collapses {names[key]} to a single solid",
                  default.get(key) == 1, f"got {default.get(key)}")

    # An explicit per-layer bbox request must still win: exact mode switches
    # off the AUTOMATIC rules, not the user's own choices.
    one = next(iter(sorted(collapsed)))
    forced = _build(present, ihp, stack_mm, exact_geometry=True,
                    force_bbox_keys={one})
    tc.check("exact: an explicit force-bbox request is still honoured",
              forced.get(one) == 1, f"got {forced.get(one)}")


def _check_units(tc):
    """
    Pinned against KLayout's own status-bar reading for this file:
        box(0,-150000 120000,150000) on EdgeSeal.boundary in GSGPad_simple
    at 1 nm per database unit — 120 um wide, 300 um tall.
    """
    lib, counts, present, ihp, stack_mm = _fixture()
    tc.check("fixture: the file's database unit is 1 nm, as KLayout reports",
              abs(lib.precision - 1e-9) < 1e-15 and abs(lib.unit - 1e-6) < 1e-15,
              f"unit={lib.unit}, precision={lib.precision}")

    seal = [L for L in present
            if (L["layer_id"], L["datatype"]) == (39, 4)]
    if not seal:
        return
    shapes = CF.load_gds(
        _GDS, seal, transform=None, preview_2d=False, compound_per_layer=True,
        min_area_mm2=0.0, decimate_tol_mm=0.0, skip_fill_datatype=False,
        fill_as_bbox=True, fill_layer_keys=set(), flat_layer_keys=set(),
        force_bbox_keys=set(), exclude_auto_bbox_keys=set(),
        protect_via_keys=set(), ihp_map=ihp, stack_mm=stack_mm,
        contacts_only_3d=False, mesh_3d=False, use_cache=False,
        auto_bbox_threshold=0, exact_geometry=True)
    bb = shapes[0]["shape"].BoundBox

    # FreeCAD is millimetres; KLayout displayed micrometres. Same physical
    # size, different display unit — that is the whole of the difference.
    for label, got_mm, want_um in (
        ("X min", bb.XMin, 0.0),
        ("X max", bb.XMax, 120.0),
        ("Y min", bb.YMin, -150.0),
        ("Y max", bb.YMax, 150.0),
    ):
        tc.check(f"units: {label} is {want_um} um, exactly as KLayout reports",
                  abs(got_mm * 1000.0 - want_um) < 1e-6,
                  f"got {got_mm * 1000.0:.6f} um")

    tc.check("units: the seal ring measures 120 x 300 um",
              abs(bb.XLength * 1000.0 - 120.0) < 1e-6
              and abs(bb.YLength * 1000.0 - 300.0) < 1e-6,
              f"{bb.XLength * 1000.0} x {bb.YLength * 1000.0} um")


def _check_wiring(tc):
    import inspect

    sig = inspect.signature(CF.load_gds)
    tc.check("load_gds accepts exact_geometry",
              "exact_geometry" in sig.parameters)

    src = inspect.getsource(CF.load_gds)
    block = src[src.index("_cache_options = {"):src.index("_ck = _cache_key")]
    tc.check("exact_geometry is iwwn the disk-cache key — otherwise turning it "
              "on returns the simplified geometry cached before",
              "exact_geometry" in block)

    # All three bbox mechanisms plus the filler rule must consult it, or a
    # layer still comes out collapsed and the result still is not KLayout.
    tc.check("every automatic collapse mechanism consults _exact",
              src.count("_exact") >= 5,
              f"found {src.count('_exact')} references")

    lod = inspect.getsource(
        __import__("core.lod_import", fromlist=["x"]).build_lod_import_params)
    tc.check("exact mode turns level-of-detail loading off — a body box "
              "where a layer should be is not KLayout either",
              "lod_mode = False" in lod)
    tc.check("exact mode reaches the loader",
              "exact_geometry          = exact_geometry" in lod
              or "exact_geometry=exact_geometry" in lod)

    with open(os.path.join(REPO_ROOT, "gds", "GDSCommand.py"),
              encoding="utf-8") as fh:
        gdssrc = fh.read()
    tc.check("exact mode skips fast-mesh baking, which approximates outlines",
              "if exact_geometry:" in gdssrc)
    tc.check("exact mode takes colours straight from the .lyp, without the "
              "gold bondable repaint KLayout never applies",
              "exact_geometry: bool = False" in gdssrc
              or "exact_geometry: bool=False" in gdssrc)
