# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.materials — what each solid is made of.

The property that matters most is that assignment is DERIVED, never guessed:
a material comes from something the document already records (a stackup
material, a configurator choice, the kind of object), and anything else is
reported as unassigned with a reason. A plausible wrong conductivity is worse
than a visible gap, because nothing downstream would notice it.

The second is that a choice made by hand survives re-running the command.
"""

import os

import FreeCAD
import Part

from _harness import TestCase, REPO_ROOT, new_document

import core.materials as materials
import core.leadframe as leadframe
import core.housing as housing
import core.substrate as substrate
from core.Core_Functionality import parse_stackup_xml

V = FreeCAD.Vector

_IHP_XML = os.path.join(REPO_ROOT, "resources", "stack_info",
                        "IHP-PDK_SG13G2", "SG13G2_200um.xml")

_QFN = {
    "frame_type": "QFN (Quad Flat No-lead)",
    "frame_length": 5.0, "frame_width": 5.0, "frame_thickness": 0.2,
    "material": "Copper",
    "left_lead_count": 4, "right_lead_count": 4,
    "top_lead_count": 4, "bottom_lead_count": 4,
    "lead_width": 0.25, "lead_pitch": 0.5, "inner_lead_length": 0.6,
    "has_die_paddle": True, "die_paddle_length": 2.5, "die_paddle_width": 2.5,
}

_HOUSING = {
    "frame_type": "QFN (Quad Flat No-lead)",
    "frame_length": 5.0, "frame_width": 5.0, "frame_thickness": 0.2,
    "wall_thickness": 0.5, "clearance": 0.2, "housing_height": 1.2,
    "include_lid": True, "lid_thickness": 0.3,
    "material": "Transparent ABS", "transparency": 0.5,
    "qfn_pad_thickness": 0.05,
}


def run():
    tc = TestCase("materials")
    _check_library(tc)
    _check_assignment(tc)
    _check_lid_inherits(tc)
    return tc.results


def _check_library(tc):
    tc.check("names(): Unassigned first, then every library material",
              materials.names()[0] == materials.UNASSIGNED
              and set(materials.names()[1:]) == set(materials.LIBRARY))
    tc.check("every library material has positive thermal properties",
              all(m.thermal_conductivity > 0 and m.density > 0 and m.specific_heat > 0
                  for m in materials.LIBRARY.values()))
    tc.check("every library material has a known kind",
              all(m.kind in {"semiconductor", "dielectric", "metal", "solder",
                             "encapsulant", "polymer", "laminate"}
                  for m in materials.LIBRARY.values()))

    for name, expected in (("Substrate", "Silicon"), ("EPI", "Silicon"),
                           ("SiO2", "Silicon dioxide"), ("copper", "Copper"),
                           ("Transparent ABS", "ABS"), ("Acrylic", "Acrylic (PMMA)"),
                           ("Alloy 42", "Alloy 42")):
        got = materials.resolve(name)
        tc.check(f"resolve({name!r}) -> {expected}",
                  got is not None and got.name == expected, str(got))
    tc.check("resolve: an unknown name is None, not a guess",
              materials.resolve("LOWLOSS") is None)
    tc.check("resolve: empty and None are None",
              materials.resolve("") is None and materials.resolve(None) is None)
    tc.check("resolve: Unassigned is not a material",
              materials.resolve(materials.UNASSIGNED) is None)


def _box(doc, name, size, at):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = Part.makeBox(*size, at)
    return obj


def _flag(obj, prop, value, ptype="App::PropertyBool"):
    obj.addProperty(ptype, prop, "Test", "")
    setattr(obj, prop, value)


def _check_assignment(tc):
    doc = new_document("TestMaterials")
    FreeCAD.setActiveDocument(doc.Name)
    try:
        leadframe.build_leadframe(dict(_QFN))
        housing.build_housing(dict(_HOUSING))

        if os.path.isfile(_IHP_XML):
            substrate.build_substrate_objects(
                doc, (10.0, 10.0, 11.0, 11.0), parse_stackup_xml(_IHP_XML))

        proxy = _box(doc, "Chip_Block", (1, 1, 0.2), V(-0.5, -0.5, 0.2))
        _flag(proxy, "IsChipProxy", True)
        trace = _box(doc, "Trace_001", (2, 0.2, 0.035), V(20, 0, 0))
        _flag(trace, "IsRoutingTrace", True)
        _box(doc, "BondWire_001", (1, 0.025, 0.025), V(30, 0, 0))
        bump = _box(doc, "WireBump_Bal_001", (0.05, 0.05, 0.05), V(31, 0, 0))
        _flag(bump, "IsWireBump", True)
        ball = _box(doc, "BGA_Ball_00_00", (0.3, 0.3, 0.3), V(40, 0, 0))
        _flag(ball, "IsLeadFinger", True)
        _flag(ball, "LeadSide", "BGA", "App::PropertyString")
        _box(doc, "Layer_Metal1_8", (1, 1, 0.001), V(50, 0, 0))
        _box(doc, "Mystery", (1, 1, 1), V(60, 0, 0))
        marker = _box(doc, "ContactPoint_999", (0.1, 0.1, 0.01), V(70, 0, 0))
        _flag(marker, "IsContactPoint", True)
        doc.recompute()

        physical = {o.Name for o in materials.physical_parts(doc)}
        tc.check("physical_parts: a contact-point marker is not a part",
                  "ContactPoint_999" not in physical)
        tc.check("physical_parts: sketches and the housing's intermediate "
                  "extrusions and cut are not parts — only the finished body is",
                  "FinalHousing" in physical
                  and not ({"HousingOuterBody", "HousingInnerCut", "HousingBody",
                            "AlignmentPosts", "LidSketch"} & physical),
                  str(sorted(physical)))

        report = materials.assign_materials(doc)
        assigned = {name: mat for name, mat, _src in report["assigned"]}
        unassigned = dict(report["unassigned"])

        expectations = [
            ("DiePaddle", "Copper", "the paddle takes the leadframe configurator's metal"),
            ("Lead_L01", "Copper", "a lead takes the leadframe configurator's metal"),
            ("LeadframeBody", "Epoxy mould compound", "the leadframe body is mould compound"),
            ("FinalHousing", "ABS", "the housing takes the housing configurator's material"),
            ("Lid", "ABS", "a lid built with the housing takes its material"),
            ("Chip_Block", "Silicon", "a chip proxy is bulk silicon"),
            ("Trace_001", "Copper", "a routed trace is copper"),
            ("BondWire_001", "Gold", "a bond wire is gold"),
            ("WireBump_Bal_001", "Gold", "a wire bump is gold"),
            ("BGA_Ball_00_00", "SAC305 solder", "a BGA ball is solder, not the leadframe metal"),
        ]
        if os.path.isfile(_IHP_XML):
            expectations += [
                ("GDS_Substrate", "Silicon", "the die substrate comes from the stackup"),
                ("GDS_EPI", "Silicon", "the epi comes from the stackup"),
            ]
        for obj_name, expected, why in expectations:
            tc.check(f"assign: {why}", assigned.get(obj_name) == expected,
                      f"{obj_name}: got {assigned.get(obj_name)!r}")

        tc.check("assign: a GDS interconnect layer is NOT guessed — the stackup "
                  "does not say which metal",
                  "Layer_Metal1_8" in unassigned
                  and "which metal" in unassigned["Layer_Metal1_8"],
                  str(unassigned.get("Layer_Metal1_8")))
        tc.check("assign: an unrecognised solid is reported, with a reason",
                  "Mystery" in unassigned and unassigned["Mystery"])
        mystery = doc.getObject("Mystery")
        tc.check("assign: an unassigned part still gets the property, so the "
                  "property editor offers the list",
                  getattr(mystery, materials.PROPERTY, None) == materials.UNASSIGNED
                  and materials.LIBRARY.keys() <= set(
                      mystery.getEnumerationsOfProperty(materials.PROPERTY)))
        tc.check("assign: the source of each choice is recorded",
                  doc.getObject("DiePaddle").PackageMaterialSource == "leadframe: Copper",
                  doc.getObject("DiePaddle").PackageMaterialSource)

        # A choice made by hand must survive running the command again.
        materials.set_material(mystery, "FR-4")
        materials.set_material(doc.getObject("Trace_001"), "Silver")
        again = materials.assign_materials(doc)
        kept = {name: mat for name, mat, _src in again["kept"]}
        tc.check("re-run: a hand-set material on an unrecognised part is kept",
                  kept.get("Mystery") == "FR-4" and mystery.PackageMaterial == "FR-4",
                  str(kept.get("Mystery")))
        tc.check("re-run: a hand-set material overriding a default is kept",
                  doc.getObject("Trace_001").PackageMaterial == "Silver")
        tc.check("re-run: a hand-set material is reported as the user's",
                  dict((n, s) for n, _m, s in again["kept"]).get("Mystery")
                  == materials.SOURCE_USER)

        materials.assign_materials(doc, overwrite=True)
        tc.check("overwrite: re-derives what can be derived",
                  doc.getObject("Trace_001").PackageMaterial == "Copper")

        try:
            materials.set_material(mystery, "Unobtainium")
            refused = False
        except ValueError:
            refused = True
        tc.check("set_material refuses a name outside the library", refused)
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_lid_inherits(tc):
    doc = new_document("TestMaterialsLid")
    FreeCAD.setActiveDocument(doc.Name)
    try:
        cfg = dict(_HOUSING, include_lid=False, material="Polycarbonate")
        housing.build_housing(cfg)
        lid = housing.add_lid_to_housing(doc, 0.3)
        tc.check("add_lid_to_housing: a lid added later records the housing's material",
                  lid is not None and getattr(lid, "HousingMaterial", "") == "Polycarbonate",
                  str(getattr(lid, "HousingMaterial", None)))
        name, _src = materials.classify(lid)
        tc.check("...so it is assigned like the housing", name == "Polycarbonate", str(name))
    finally:
        FreeCAD.closeDocument(doc.Name)
