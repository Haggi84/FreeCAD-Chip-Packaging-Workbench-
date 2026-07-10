# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Smoke test for core.housing.build_housing() — previously completely
untested.  Builds a transparent QFN housing (with and without a lid) and
checks the expected parametric objects (outer shell, cavity cut, alignment
posts, fused final housing, optional lid) come out valid.

Note: unlike core.leadframe.build_leadframe(doc, config), build_housing()
takes no explicit document — it always operates on FreeCAD.activeDocument().
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
    "include_lid": True,
    "lid_thickness": 0.3,
    "material": "Polycarbonate",
    "transparency": 0.5,
    "qfn_pad_thickness": 0.05,
}


def _check_valid_shape(tc: TestCase, doc, obj_name: str, min_volume: float = 1e-9):
    obj = doc.getObject(obj_name)
    if not tc.check(f"{obj_name}: object created", obj is not None):
        return
    shape = getattr(obj, "Shape", None)
    tc.check(f"{obj_name}: has shape", shape is not None)
    if shape is None:
        return
    tc.check(f"{obj_name}: shape valid", shape.isValid())
    tc.check(f"{obj_name}: shape not null", not shape.isNull())
    tc.check(f"{obj_name}: positive volume", shape.Volume > min_volume,
             f"volume={shape.Volume}")


def run():
    tc = TestCase("housing_build")

    # ── with lid ──────────────────────────────────────────────────────────
    doc = new_document("TestHousingWithLid")
    FreeCAD.setActiveDocument(doc.Name)

    def _build():
        housing.build_housing(_QFN_HOUSING_CONFIG)

    if not tc.check_raises_nothing("builds without exception (with lid)", _build):
        FreeCAD.closeDocument(doc.Name)
        return tc.results

    _check_valid_shape(tc, doc, "HousingBody")
    _check_valid_shape(tc, doc, "AlignmentPosts")
    _check_valid_shape(tc, doc, "FinalHousing")
    _check_valid_shape(tc, doc, "Lid")

    # Sanity-check outer dimensions: outer_length = frame_length +
    # 2*(wall_thickness + clearance + qfn_pad_thickness)
    final = doc.getObject("FinalHousing")
    if final is not None and final.Shape is not None and not final.Shape.isNull():
        expected_len = (_QFN_HOUSING_CONFIG["frame_length"]
                        + 2 * (_QFN_HOUSING_CONFIG["wall_thickness"]
                               + _QFN_HOUSING_CONFIG["clearance"]
                               + _QFN_HOUSING_CONFIG["qfn_pad_thickness"]))
        bb = final.Shape.BoundBox
        actual_len = bb.XLength
        tc.check(
            "outer housing length matches config",
            abs(actual_len - expected_len) < 0.05,
            f"got {actual_len:.4f}, expected ~{expected_len:.4f}",
        )

    FreeCAD.closeDocument(doc.Name)

    # ── without lid ───────────────────────────────────────────────────────
    doc2 = new_document("TestHousingNoLid")
    FreeCAD.setActiveDocument(doc2.Name)
    config_no_lid = dict(_QFN_HOUSING_CONFIG, include_lid=False)

    def _build_no_lid():
        housing.build_housing(config_no_lid)

    if tc.check_raises_nothing("builds without exception (no lid)", _build_no_lid):
        _check_valid_shape(tc, doc2, "FinalHousing")
        tc.check("no Lid object created when include_lid=False",
                 doc2.getObject("Lid") is None)

    FreeCAD.closeDocument(doc2.Name)
    return tc.results
