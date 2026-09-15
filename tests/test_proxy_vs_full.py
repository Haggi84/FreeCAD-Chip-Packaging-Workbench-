# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The chip proxy and the full GDS import must describe the same physical part.

Every tool is meant to work on a proxy in place of the full geometry. That is
only true if the two agree on what the proxy keeps: the outline, the die from
its underside to its top metal, and where the bond pads are. Here the same
layout and PDK files are imported both ways and compared number for number,
including that each contact point of the full import sits on the pad metal
that import actually built.
"""

import os

import FreeCAD
import gdstk

from _harness import TestCase, REPO_ROOT, new_document

import core.chip_proxy as chip_proxy
import core.substrate as substrate
from core import Core_Functionality as CF
from core.Core_Functionality import parse_lyp, parse_map, parse_stackup_xml
from core.lod_import import boundary_layer_keys

_IHP = os.path.join(REPO_ROOT, "resources", "stack_info", "IHP-PDK_SG13G2")
_LYP = os.path.join(_IHP, "sg13g2.lyp")
_MAP = os.path.join(_IHP, "sg13g2.map")
_XML = os.path.join(_IHP, "SG13G2_200um.xml")
_GDS = os.path.join(REPO_ROOT, "samples", "IC_Pad_EdgeSeal.boundary.gds")

_TOL = 1e-9


def run():
    tc = TestCase("proxy_vs_full")
    missing = [p for p in (_GDS, _LYP, _MAP, _XML) if not os.path.isfile(p)]
    if missing:
        tc.skip("chip proxy and full import agree", f"not present: {missing}")
        return tc.results

    proxy = chip_proxy.extract_chip_proxy(_GDS, _LYP, _MAP, _XML)
    all_layers, _ = parse_lyp(_LYP)
    ihp = parse_map(_MAP)
    stack = parse_stackup_xml(_XML)

    lib = gdstk.read_gds(_GDS)
    drawn = {(p.layer, p.datatype) for cell in lib.top_level()
             for p in cell.get_polygons(depth=None)}
    present = [L for L in all_layers if (L["layer_id"], L["datatype"]) in drawn]
    stack_mm = CF.build_stack_mm_from_xml(present, ihp, stack)

    shapes = {}
    for entry in CF.load_gds(
            _GDS, present, transform=None, preview_2d=False,
            compound_per_layer=True, min_area_mm2=0.0, decimate_tol_mm=0.0,
            skip_fill_datatype=False, fill_as_bbox=True, fill_layer_keys=set(),
            flat_layer_keys=boundary_layer_keys(all_layers),
            force_bbox_keys=set(), exclude_auto_bbox_keys=set(),
            protect_via_keys=set(), ihp_map=ihp, stack_mm=stack_mm,
            contacts_only_3d=False, mesh_3d=False, use_cache=False,
            auto_bbox_threshold=0, exact_geometry=True):
        if entry.get("shape") is not None:
            shapes[(entry["layer_id"], entry["datatype"])] = entry["shape"]

    # ── outline ─────────────────────────────────────────────────────────────
    seal = shapes.get((39, 4))
    if tc.check("the full import builds the seal ring", seal is not None):
        bb = seal.BoundBox
        x0, y0, x1, y1 = proxy["footprint_mm"]
        tc.check("outline: the proxy's footprint is the seal ring the full import draws",
                  all(abs(a - b) < _TOL for a, b in
                      ((x0, bb.XMin), (y0, bb.YMin), (x1, bb.XMax), (y1, bb.YMax))),
                  f"proxy {proxy['footprint_mm']} vs seal "
                  f"{(bb.XMin, bb.YMin, bb.XMax, bb.YMax)}")

    doc = new_document("ProxyVsFull")
    try:
        # ── height ──────────────────────────────────────────────────────────
        outline = chip_proxy.describe_die_footprint(_GDS)
        body = substrate.build_substrate_objects(doc, outline["footprint_mm"], stack)
        bottom = min(o.Shape.BoundBox.ZMin for o in body)
        solid_tops = [s.BoundBox.ZMax for s in shapes.values() if s.Solids]
        top = max(solid_tops)
        proxy_top = proxy["z0_mm"] + proxy["thickness_mm"]
        tc.check("height: the proxy's underside is the full import's die body underside",
                  abs(proxy["z0_mm"] - bottom) < _TOL, f"{proxy['z0_mm']} vs {bottom}")
        tc.check("height: the proxy's top is the top of the highest layer the full "
                  "import builds",
                  abs(proxy_top - top) < _TOL,
                  f"proxy top {proxy_top * 1000:.4f} um vs full {top * 1000:.4f} um")

        # ── pads ────────────────────────────────────────────────────────────
        count = CF.import_pin_pads_as_contacts(_GDS, ihp, doc, selected_layers=present,
                                               stack_mm=stack_mm)
        cps = [o for o in doc.Objects if getattr(o, "IsContactPoint", False)]
        full_xy = sorted((round(c.ContactPoint.x, 6), round(c.ContactPoint.y, 6)) for c in cps)
        proxy_xy = sorted((round(p["x_mm"], 6), round(p["y_mm"], 6)) for p in proxy["pads"])
        tc.check("pads: both find pads on this layout", count > 0 and len(proxy_xy) > 0,
                  f"full {count}, proxy {len(proxy_xy)}")
        tc.check("pads: the proxy's pads are at exactly the full import's contact points",
                  full_xy == proxy_xy, f"full {full_xy}\nproxy {proxy_xy}")

        layer_tops = [s.BoundBox.ZMax for s in shapes.values() if s.Solids]
        floating = [round(c.ContactPoint.z * 1000.0, 4) for c in cps
                    if not any(abs(c.ContactPoint.z - t) < _TOL for t in layer_tops)]
        tc.check("pads: every contact point of the full import sits on the top of pad "
                  "metal that import built, at the stackup's height",
                  cps and not floating,
                  f"contact points at {floating} um match no built layer top; layer tops "
                  f"{sorted(round(t * 1000.0, 4) for t in layer_tops)} um")
        if cps:
            highest = max(c.ContactPoint.z for c in cps)
            tc.check("pads: the highest contact points are on the proxy's top face",
                      abs(highest - proxy_top) < _TOL,
                      f"{highest * 1000:.4f} um vs {proxy_top * 1000:.4f} um")
    finally:
        FreeCAD.closeDocument(doc.Name)
    return tc.results
