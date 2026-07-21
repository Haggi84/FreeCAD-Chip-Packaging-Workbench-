# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Smoke test for core.housing.add_lid_to_housing() / find_housing_body() —
the standalone "close up an already-open package" action added alongside
build_housing()'s own include_lid flag.

Covers the exact scenario this function exists for: a housing built WITHOUT
a lid (open for wire bonding), lidded afterward as a separate step, with the
lid's footprint/height derived from the housing's own geometry rather than
any saved config.
"""

import FreeCAD

from _harness import TestCase, new_document

import core.housing as housing

_QFN_HOUSING_CONFIG = {
    "frame_type": "QFN (Quad Flat No-lead)",
    "frame_length": 4.0,
    "frame_width": 4.0,
    "frame_thickness": 0.9,
    "wall_thickness": 0.5,
    "clearance": 0.2,
    "housing_height": 1.2,
    "include_lid": False,
    "lid_thickness": 0.3,
    "material": "Polycarbonate",
    "transparency": 0.5,
    "qfn_pad_thickness": 0.05,
}


def run():
    tc = TestCase("add_lid")

    doc = new_document("TestAddLid")
    FreeCAD.setActiveDocument(doc.Name)

    # ── find_housing_body: nothing built yet ────────────────────────────────
    tc.check("find_housing_body: None before any housing exists",
             housing.find_housing_body(doc) is None)

    # ── build an OPEN housing (the scenario this feature targets) ──────────
    def _build():
        housing.build_housing(dict(_QFN_HOUSING_CONFIG))

    if not tc.check_raises_nothing("build_housing (no lid): no exception", _build):
        FreeCAD.closeDocument(doc.Name)
        return tc.results

    tc.check("no Lid before add_lid_to_housing is called",
             doc.getObject("Lid") is None)

    found = housing.find_housing_body(doc)
    tc.check("find_housing_body: finds FinalHousing", found is not None
              and found.Name == "FinalHousing")

    final = doc.getObject("FinalHousing")
    housing_bb = final.Shape.BoundBox

    # ── add_lid_to_housing: auto-detected housing ───────────────────────────
    box = {}

    def _add_lid():
        box["lid"] = housing.add_lid_to_housing(doc, 0.4)

    if tc.check_raises_nothing("add_lid_to_housing: no exception", _add_lid):
        lid = box.get("lid")
        tc.check("add_lid_to_housing returns the Lid object", lid is not None)
        if lid is not None:
            tc.check("Lid object created in document", doc.getObject("Lid") is lid)
            shp = lid.Shape
            tc.check("Lid shape valid", shp is not None and shp.isValid())
            tc.check("Lid shape not null", shp is not None and not shp.isNull())
            tc.check("Lid has positive volume", shp is not None and shp.Volume > 1e-9)

            lid_bb = shp.BoundBox
            tc.check(
                "Lid footprint matches housing outer footprint (X)",
                abs(lid_bb.XLength - housing_bb.XLength) < 1e-6,
                f"lid={lid_bb.XLength:.4f} housing={housing_bb.XLength:.4f}",
            )
            tc.check(
                "Lid footprint matches housing outer footprint (Y)",
                abs(lid_bb.YLength - housing_bb.YLength) < 1e-6,
                f"lid={lid_bb.YLength:.4f} housing={housing_bb.YLength:.4f}",
            )
            tc.check(
                "Lid sits on top of the housing (ZMin == housing ZMax)",
                abs(lid_bb.ZMin - housing_bb.ZMax) < 1e-6,
                f"lid.ZMin={lid_bb.ZMin:.4f} housing.ZMax={housing_bb.ZMax:.4f}",
            )
            tc.check(
                "Lid thickness matches requested value",
                abs(lid_bb.ZLength - 0.4) < 1e-6,
                f"got {lid_bb.ZLength:.4f}",
            )

    # ── re-running replaces the lid rather than stacking a duplicate ───────
    def _add_lid_again():
        box["lid2"] = housing.add_lid_to_housing(doc, 0.8)

    if tc.check_raises_nothing("add_lid_to_housing (re-run): no exception", _add_lid_again):
        lid2 = box.get("lid2")
        tc.check("re-run returns a Lid object", lid2 is not None)
        if lid2 is not None:
            shp2 = lid2.Shape
            tc.check(
                "re-run updates thickness, not stacks a duplicate",
                shp2 is not None and abs(shp2.BoundBox.ZLength - 0.8) < 1e-6,
                f"got {shp2.BoundBox.ZLength if shp2 else None}",
            )
        # Exactly one "Lid"-named object should exist, not two.
        others = [o for o in doc.Objects if o.Name == "Lid"]
        tc.check("still only one Lid object after re-run", len(others) == 1,
                 f"found {len(others)}")

    # ── no housing in the document at all ───────────────────────────────────
    doc2 = new_document("TestAddLidNoHousing")
    tc.check("find_housing_body: None in an unrelated empty document",
             housing.find_housing_body(doc2) is None)
    tc.check("add_lid_to_housing: returns None with no housing present",
             housing.add_lid_to_housing(doc2, 0.5) is None)
    FreeCAD.closeDocument(doc2.Name)

    FreeCAD.closeDocument(doc.Name)
    return tc.results
