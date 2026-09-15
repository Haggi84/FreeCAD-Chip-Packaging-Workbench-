# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.substrate — the die body below the lowest drawn layer.

A die is mostly the silicon under the interconnect. On IHP SG13G2 the drawn
stack is 14.23 um and the body beneath it is 183.75 um, so modelling only the
layers gives an object under 8% of the real part's thickness — wrong for
anything that treats the die as a physical thing: bond-wire clearance,
package cavity height, a thermal export.

The stackup declares the body as <Dielectric> entries whose thicknesses sum
exactly to the <Substrate Offset>. What is tested here is that derivation and
its refusals: inventing a body of the wrong depth would be worse than
modelling none, because it would look right.
"""

import os

from _harness import TestCase, REPO_ROOT, new_document

import FreeCAD
import core.substrate as substrate
import core.chip_proxy as chip_proxy
from core.Core_Functionality import parse_stackup_xml

_IHP_XML = os.path.join(REPO_ROOT, "resources", "stack_info",
                        "IHP-PDK_SG13G2", "SG13G2_200um.xml")
_SKY_XML = os.path.join(REPO_ROOT, "resources", "stack_info",
                        "SkyWater-PDK_SKY130", "SKY130A_300um.xml")


def run():
    tc = TestCase("substrate")
    _check_parsing(tc)
    _check_derivation(tc)
    _check_refusals(tc)
    _check_geometry(tc)
    _check_drop_to_surface(tc)
    _check_dielectric_fill(tc)
    return tc.results


def _check_drop_to_surface(tc):
    """
    The other half of placing the die vertically: closing the gap a partial
    import leaves under the loaded stack.

    Importing only the top of an SG13G2 stack puts Metal5 at 5.09 um with
    nothing beneath it, because Activ, the contacts and Metal1-4 were never
    built. The two rules that keep the correction safe are that it measures
    only layers whose height the stackup actually states, and that it is a
    no-op once the lowest layer already sits on the die surface.
    """
    from core.tech.stackup import drop_stack_to_die_surface

    # A partial stack: real layers starting at 5.09 um, plus a marker layer
    # sitting at a meaningless rank-based fallback height.
    partial = {
        (67, 0):  {"t_mm": 0.00049, "z0_mm": 0.00509, "from_xml": True},
        (125, 0): {"t_mm": 0.00085, "z0_mm": 0.00558, "from_xml": True},
        (134, 0): {"t_mm": 0.00300, "z0_mm": 0.01123, "from_xml": True},
        (39, 4):  {"t_mm": 0.00020, "z0_mm": 0.0},     # EdgeSeal, no from_xml
    }
    dropped, shift = drop_stack_to_die_surface(partial)

    tc.check("drop: the shift equals the lowest real layer's height",
              abs(shift - 0.00509) < 1e-12, f"got {shift}")
    tc.check("drop: the lowest real layer lands exactly on the die surface",
              abs(dropped[(67, 0)]["z0_mm"]) < 1e-12,
              str(dropped[(67, 0)]["z0_mm"]))
    tc.check("drop: relative spacing is preserved — the stack moves as one",
              abs((dropped[(134, 0)]["z0_mm"] - dropped[(67, 0)]["z0_mm"])
                  - (partial[(134, 0)]["z0_mm"] - partial[(67, 0)]["z0_mm"]))
              < 1e-12)
    tc.check("drop: thicknesses are untouched",
              all(abs(dropped[k]["t_mm"] - partial[k]["t_mm"]) < 1e-12
                  for k in partial))

    # The marker is the datum the gap is measured against; moving it would
    # take the die outline off the die surface with the stack.
    tc.check("drop: a marker layer with a fallback height is NOT moved — it "
              "is where the die outline is drawn",
              abs(dropped[(39, 4)]["z0_mm"]) < 1e-12,
              str(dropped[(39, 4)]["z0_mm"]))
    tc.check("drop: the marker and the lowest real layer end up on the same "
              "plane, which is the whole point",
              abs(dropped[(39, 4)]["z0_mm"] - dropped[(67, 0)]["z0_mm"]) < 1e-12)

    # A full import already reaches the surface, so nothing may move — the
    # true PDK heights have to survive.
    full = dict(partial)
    full[(1, 0)] = {"t_mm": 0.0004, "z0_mm": 0.0, "from_xml": True}
    unchanged, no_shift = drop_stack_to_die_surface(full)
    tc.check("drop: a full import is a no-op, so true PDK heights survive",
              no_shift == 0.0
              and abs(unchanged[(134, 0)]["z0_mm"] - 0.01123) < 1e-12,
              f"shift={no_shift}")

    tc.check("drop: an empty stack is handled",
              drop_stack_to_die_surface({}) == ({}, 0.0))
    markers_only = {(39, 4): {"t_mm": 0.0002, "z0_mm": 0.0}}
    tc.check("drop: a stack of markers only does not move — there is no real "
              "height to measure against",
              drop_stack_to_die_surface(markers_only)[1] == 0.0)

    # Real data end to end.
    if os.path.isfile(_IHP_XML):
        from core.Core_Functionality import (parse_lyp, parse_map,
                                              build_stack_mm_from_xml)
        lyp = os.path.join(REPO_ROOT, "resources", "stack_info",
                           "IHP-PDK_SG13G2", "sg13g2.lyp")
        if os.path.isfile(lyp):
            all_layers, _ = parse_lyp(lyp)
            mapping = parse_map(os.path.join(
                REPO_ROOT, "resources", "stack_info", "IHP-PDK_SG13G2",
                "sg13g2.map"))
            data = parse_stackup_xml(_IHP_XML)
            top_only = [L for L in all_layers
                        if (L["layer_id"], L["datatype"])
                        in {(67, 0), (125, 0), (126, 0), (133, 0), (134, 0),
                            (39, 4)}]
            built = build_stack_mm_from_xml(top_only, mapping, data)
            _shifted, real_shift = drop_stack_to_die_surface(built)
            tc.check("drop: on a real top-of-stack selection the gap measures "
                      "4.89 um above the seal ring, closed by a 5.09 um shift",
                      abs(real_shift - 0.00509) < 1e-9,
                      f"got {real_shift * 1000.0:.4f} um")


def _check_parsing(tc):
    if not os.path.isfile(_IHP_XML):
        return
    data = parse_stackup_xml(_IHP_XML)
    names = [d["name"] for d in data.get("_dielectrics", [])]
    tc.check("parse_stackup_xml: exposes <Dielectric> entries, which it used "
              "to discard entirely",
              names == ["AIR", "Passive", "SiO2", "EPI", "Substrate"],
              str(names))
    tc.check("parse_stackup_xml: exposes <Material> colours",
              "SUBSTRATE" in (data.get("_materials") or {}))

    # The dielectric/material keys must not disturb the layer lookups that
    # every other caller relies on.
    tc.check("adding dielectrics leaves layer lookup by number intact",
              isinstance(data.get(8), dict)
              and abs(data[8]["zmin_um"] - 1.04) < 1e-9)
    thick, z0, source = chip_proxy.get_die_thickness_mm(data)
    tc.check("adding dielectrics does not disturb the thickness calculation, "
              "which iterates the same dict",
              source == "xml_with_substrate"
              and abs(thick - 0.19798) < 1e-6 and abs(z0 + 0.18375) < 1e-9,
              f"{thick} / {z0} / {source}")


def _check_derivation(tc):
    if not os.path.isfile(_IHP_XML):
        return
    data = parse_stackup_xml(_IHP_XML)
    layers = substrate.substrate_layers_mm(data)

    tc.check("SG13G2: the body is substrate + epi, bottom first",
              [e["name"] for e in layers] == ["Substrate", "EPI"],
              str([e["name"] for e in layers]))
    tc.check("SG13G2: substrate is 180 um, epi is 3.75 um — matching the "
              "PDK's own stackup preview",
              abs(layers[0]["t_mm"] - 0.180) < 1e-9
              and abs(layers[1]["t_mm"] - 0.00375) < 1e-9,
              str([e["t_mm"] for e in layers]))

    # AIR, the passivation and the inter-metal oxide sit ABOVE the die and
    # must never be swept into the body; AIR alone is 200 um and would more
    # than double it.
    tc.check("SG13G2: AIR / Passive / SiO2 are not part of the die body",
              not ({"AIR", "Passive", "SiO2"} & {e["name"] for e in layers}))

    _thick, die_z0, _src = chip_proxy.get_die_thickness_mm(data)
    tc.check("the body's underside meets the die's own z0, so the proxy and "
              "the full import describe the same physical part",
              abs(layers[0]["z0_mm"] - die_z0) < 1e-9,
              f"{layers[0]['z0_mm']} vs {die_z0}")
    top = layers[-1]["z0_mm"] + layers[-1]["t_mm"]
    tc.check("the body's top face lands exactly on z=0, where the "
              "interconnect starts — no gap, no overlap",
              abs(top) < 1e-9, f"top at {top}")

    contiguous = all(
        abs((a["z0_mm"] + a["t_mm"]) - b["z0_mm"]) < 1e-9
        for a, b in zip(layers, layers[1:]))
    tc.check("the slabs are contiguous — no gap between substrate and epi",
              contiguous)
    tc.check("total body thickness equals the declared substrate offset",
              abs(substrate.total_thickness_mm(data) - 0.18375) < 1e-9,
              str(substrate.total_thickness_mm(data)))

    if os.path.isfile(_SKY_XML):
        sky = parse_stackup_xml(_SKY_XML)
        sky_layers = substrate.substrate_layers_mm(sky)
        tc.check("SKY130 also yields a body, so the feature is not "
                  "SG13G2-specific",
                  [e["name"] for e in sky_layers] == ["Substrate", "EPI"],
                  str([e["name"] for e in sky_layers]))
        tc.check("SKY130: the body sums to its declared 300 um offset",
                  abs(substrate.total_thickness_mm(sky) - 0.300) < 1e-9)


def _check_refusals(tc):
    tc.check("no stackup at all yields no body",
              substrate.substrate_layers_mm({}) == [])
    tc.check("dielectrics without a substrate offset yield no body — there "
              "is nothing to say how deep the body is",
              substrate.substrate_layers_mm({
                  "_dielectrics": [{"name": "S", "material": "S",
                                     "thickness_um": 10.0}]}) == [])

    # The important refusal: rather than build a body of the wrong depth,
    # which would look plausible and be silently wrong.
    mismatched = substrate.substrate_layers_mm({
        "_substrate_offset_um": 100.0,
        "_dielectrics": [{"name": "S", "material": "S", "thickness_um": 10.0}]})
    tc.check("thicknesses that do not account for the declared offset yield "
              "no body, rather than one of the wrong depth",
              mismatched == [], str(mismatched))

    tc.check("a zero offset yields no body",
              substrate.substrate_layers_mm({
                  "_substrate_offset_um": 0.0,
                  "_dielectrics": [{"name": "S", "material": "S",
                                     "thickness_um": 10.0}]}) == [])

    tc.check("material_colour falls back when the material is unknown",
              substrate.material_colour({}, "nope", (0.1, 0.2, 0.3))
              == (0.1, 0.2, 0.3))


def _check_geometry(tc):
    if not os.path.isfile(_IHP_XML):
        return
    data = parse_stackup_xml(_IHP_XML)
    doc = new_document("SubstrateBuild")
    try:
        objs = substrate.build_substrate_objects(
            doc, (0.0, 0.0, 1.05, 1.05), data)
        doc.recompute()
        tc.check("build_substrate_objects: one solid per slab",
                  len(objs) == 2, f"got {len(objs)}")

        bb0 = objs[0].Shape.BoundBox
        bb1 = objs[1].Shape.BoundBox
        tc.check("the slabs span the die footprint",
                  abs(bb0.XLength - 1.05) < 1e-6
                  and abs(bb0.YLength - 1.05) < 1e-6)
        tc.check("substrate sits from -183.75 um to -3.75 um",
                  abs(bb0.ZMin + 0.18375) < 1e-9
                  and abs(bb0.ZMax + 0.00375) < 1e-9,
                  f"{bb0.ZMin} .. {bb0.ZMax}")
        tc.check("epi sits from -3.75 um to 0",
                  abs(bb1.ZMin + 0.00375) < 1e-9 and abs(bb1.ZMax) < 1e-9,
                  f"{bb1.ZMin} .. {bb1.ZMax}")
        tc.check("the slabs are tagged so later tools can tell body from "
                  "routing layers",
                  all(getattr(o, "IsDieBody", False) for o in objs))
        tc.check("each slab records the material it came from",
                  [o.StackMaterial for o in objs] == ["Substrate", "EPI"],
                  str([o.StackMaterial for o in objs]))

        try:
            substrate.build_substrate_objects(doc, (0.0, 0.0, 0.0, 1.0), data)
            degenerate_raised = False
        except ValueError:
            degenerate_raised = True
        tc.check("a degenerate footprint is refused rather than producing a "
                  "zero-width slab", degenerate_raised)
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_dielectric_fill(tc):
    """
    The space between the die surface and the lowest layer a layout actually
    uses is oxide, not air.

    A PDK defines more levels than any one design draws on. With only the top
    of an SG13G2 stack present, nothing sits between the silicon and Metal5 at
    5.09 um — but in the real part that volume is the inter-metal dielectric
    the unused levels are embedded in. Filling it is the honest fix; the
    earlier approach of sliding the whole stack down closed the same gap by
    falsifying every Z height in the model.
    """
    if not os.path.isfile(_IHP_XML):
        return
    data = parse_stackup_xml(_IHP_XML)

    entry = substrate.interconnect_dielectric(data)
    tc.check("interconnect_dielectric: SG13G2's is SiO2 — derived from the "
              "stackup order, not looked up by name",
              entry is not None and entry.get("name") == "SiO2",
              str(entry))
    # It must not pick the passivation or the air above the top metal, nor
    # any part of the die body below.
    tc.check("interconnect_dielectric: not the passivation or air, which sit "
              "ABOVE the top metal",
              (entry or {}).get("name") not in ("AIR", "Passive"))
    tc.check("interconnect_dielectric: not part of the die body",
              (entry or {}).get("name")
              not in {e["name"] for e in substrate.substrate_layers_mm(data)})

    fill = substrate.dielectric_fill_mm(data, 0.00509)   # Metal5's own z0
    tc.check("dielectric_fill_mm: spans the die surface up to the lowest "
              "used layer",
              fill is not None
              and abs(fill["z0_mm"]) < 1e-12
              and abs(fill["t_mm"] - 0.00509) < 1e-12,
              str(fill))
    tc.check("dielectric_fill_mm: made of the stackup's own dielectric",
              (fill or {}).get("material") == "SiO2")

    # A full import already reaches the surface: filling then would put a
    # slab through the middle of Activ.
    for no_gap in (0.0, -0.001, None, "nonsense"):
        tc.check(f"dielectric_fill_mm: no fill for a lowest layer at "
                  f"{no_gap!r} — there is no gap",
                  substrate.dielectric_fill_mm(data, no_gap) is None)

    doc = new_document("DielectricFill")
    try:
        body = substrate.build_substrate_objects(
            doc, (0.0, 0.0, 1.05, 1.05), data)
        obj = substrate.build_dielectric_fill(
            doc, (0.0, 0.0, 1.05, 1.05), data, 0.00509)
        doc.recompute()
        if not tc.check("build_dielectric_fill: creates the slab",
                         obj is not None):
            return

        bb = obj.Shape.BoundBox
        tc.check("the fill starts exactly on the epi top (z=0)",
                  abs(bb.ZMin) < 1e-12, str(bb.ZMin))
        tc.check("the fill stops exactly at the lowest used layer",
                  abs(bb.ZMax - 0.00509) < 1e-12, str(bb.ZMax))
        tc.check("the fill spans the die footprint",
                  abs(bb.XLength - 1.05) < 1e-9
                  and abs(bb.YLength - 1.05) < 1e-9)
        tc.check("the fill is tagged as die body, so it moves with the chip",
                  getattr(obj, "IsDieBody", False) is True)
        tc.check("the fill records the material it came from",
                  obj.StackMaterial == "SiO2", obj.StackMaterial)

        # Substrate, epi and fill must form one solid column with no seams.
        boxes = sorted([o.Shape.BoundBox for o in body + [obj]],
                       key=lambda b: b.ZMin)
        gaps = [round(b.ZMin - a.ZMax, 12) for a, b in zip(boxes, boxes[1:])]
        tc.check("substrate, epi and fill stack contiguously — no seam and "
                  "no overlap", all(g == 0.0 for g in gaps), str(gaps))
        tc.check("the column runs from the die underside to the lowest used "
                  "layer",
                  abs(boxes[0].ZMin + 0.18375) < 1e-9
                  and abs(boxes[-1].ZMax - 0.00509) < 1e-12,
                  f"{boxes[0].ZMin} .. {boxes[-1].ZMax}")

        try:
            substrate.build_dielectric_fill(doc, (0.0, 0.0, 0.0, 1.0),
                                             data, 0.00509)
            degenerate_raised = False
        except ValueError:
            degenerate_raised = True
        tc.check("a degenerate footprint is refused", degenerate_raised)
    finally:
        FreeCAD.closeDocument(doc.Name)
