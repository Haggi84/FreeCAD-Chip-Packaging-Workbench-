# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.gds_tech — one technology per chip, not per session.

The session's active PDK is a moving target: it is whatever was imported
last. A die imported from SKY130 has to keep saying SKY130 after an SG13G2
die is imported next to it, or the materials, the stackup heights and the
layer names of the first die quietly become the second die's.

So what is checked here is mostly inheritance and isolation: a pad marker
answers for its die, a layer answers for its import group, an object that
carries its own technology keeps it, and two dies in one document do not
bleed into each other.

The Technology Configuration on disk is never touched — a stub stands in for
it, because a test that rewrote the user's real PDK paths would be a bug in
its own right.
"""

import os

import FreeCAD
import Part

from _harness import TestCase, new_document, REPO_ROOT

import core.gds_tech as gds_tech

V = FreeCAD.Vector

_SKY_XML = os.path.join(REPO_ROOT, "resources", "stack_info",
                        "SkyWater-PDK_SKY130", "SKY130A_300um.xml")

_SKY = gds_tech.profile("SkyWater SKY130", "sky130.lyp", "sky130.map", _SKY_XML)
_IHP = gds_tech.profile("IHP-PDK SG13G2", "sg13g2.lyp", "sg13g2.map", "sg13g2.xml")


class _StubConfig:
    """Just enough of TechConfigManager to read profiles from."""

    def __init__(self):
        self._profiles = {
            "IHP-PDK SG13G2": {"description": "IHP", "lyp_path": "a.lyp",
                               "map_path": "a.map", "xml_path": "a.xml"},
            "SkyWater SKY130": {"description": "Sky", "lyp_path": "b.lyp",
                                "map_path": "b.map", "xml_path": "b.xml"},
        }
        self._active = "SkyWater SKY130"

    def profile_names(self):
        return list(self._profiles)

    def get_profile(self, name):
        return dict(self._profiles.get(name, {}))

    def get_active_name(self):
        return self._active

    def get_local(self):
        return dict(self._profiles[self._active])


def run():
    tc = TestCase("gds_tech")
    _check_config_profiles(tc)
    _check_tagging(tc)
    _check_inheritance(tc)
    _check_two_dies(tc)
    _check_stackup(tc)
    _check_materials_per_chip(tc)
    return tc.results


def _check_config_profiles(tc):
    config = _StubConfig()
    names = [p["name"] for p in gds_tech.config_profiles(config)]
    tc.check("every configured technology can be offered for an import",
             names == ["IHP-PDK SG13G2", "SkyWater SKY130"], str(names))

    active = gds_tech.from_config(None, config)
    tc.check("with no name, the session's active profile is the starting "
             "point — importing one chip after another with the same PDK "
             "should not mean choosing it every time",
             active["name"] == "SkyWater SKY130" and active["xml_path"] == "b.xml",
             str(active))
    tc.check("a named profile comes back with its own paths",
             gds_tech.from_config("IHP-PDK SG13G2", config)["lyp_path"] == "a.lyp")


def _block(doc, name, z=0.0):
    block = doc.addObject("Part::Feature", name)
    block.Shape = Part.makeBox(1.0, 1.0, 0.3, V(0, 0, z))
    return block


def _check_tagging(tc):
    doc = new_document("TechTag")
    try:
        block = _block(doc, "Die")
        tc.check("an untagged object has no technology, rather than a wrong one",
                 gds_tech.read(block) is None and gds_tech.technology_of(block) is None)

        gds_tech.tag(block, _SKY)
        tc.check("tagging records the technology's name where the property "
                 "editor shows it",
                 block.TechProfile == "SkyWater SKY130", block.TechProfile)
        tc.check("...and the three PDK files with it, so the chip is still "
                 "readable on a machine that never configured this PDK",
                 (block.SourceLYP, block.SourceMAP, block.SourceXML)
                 == ("sky130.lyp", "sky130.map", _SKY_XML))
        tc.check("reading it back gives the profile that went in",
                 gds_tech.read(block)["name"] == _SKY["name"])

        gds_tech.tag(block, _IHP)
        tc.check("re-tagging replaces it — correcting a chip imported with the "
                 "wrong PDK must not leave both",
                 gds_tech.read(block)["name"] == "IHP-PDK SG13G2"
                 and block.SourceXML == "sg13g2.xml")
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_inheritance(tc):
    doc = new_document("TechInherit")
    try:
        block = _block(doc, "Die")
        gds_tech.tag(block, _SKY)

        pad = doc.addObject("Part::Feature", "Die_Pad_001")
        pad.Shape = Part.makeBox(0.07, 0.07, 0.001, V(0, 0, 0.3))
        pad.addProperty("App::PropertyString", "SourceObject", "Wirebond", "")
        pad.SourceObject = block.Name
        tc.check("a pad answers for the die it belongs to, without being "
                 "tagged itself",
                 (gds_tech.technology_of(pad) or {}).get("name") == "SkyWater SKY130")
        tc.check("...but it is the die that carries it, so a report lists dies "
                 "and not every pad",
                 gds_tech.read(pad) is None)

        group = doc.addObject("App::DocumentObjectGroup", "GDS_Die")
        gds_tech.tag(group, _IHP)
        layer = _block(doc, "Layer_TopMetal2_134")
        group.addObject(layer)
        tc.check("an imported GDS layer answers for the import it came in with",
                 (gds_tech.technology_of(layer) or {}).get("name") == "IHP-PDK SG13G2")

        gds_tech.tag(layer, _SKY)
        tc.check("an object with its own technology keeps it, whatever group "
                 "it is put in",
                 gds_tech.technology_of(layer)["name"] == "SkyWater SKY130")

        loose = _block(doc, "Lid")
        tc.check("a part that is not from a GDS has no technology at all, and "
                 "callers fall back to what they did before",
                 gds_tech.technology_of(loose) is None)
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_two_dies(tc):
    doc = new_document("TechTwoDies")
    try:
        sky = _block(doc, "SkyDie")
        ihp = _block(doc, "IhpDie", z=1.0)
        gds_tech.tag(sky, _SKY)
        gds_tech.tag(ihp, _IHP)

        entries = gds_tech.technologies(doc)
        tc.check("both technologies are reported, one entry each",
                 [e["name"] for e in entries] == ["SkyWater SKY130", "IHP-PDK SG13G2"],
                 str([e["name"] for e in entries]))
        tc.check("...each naming the die that uses it",
                 entries[0]["objects"] == ["SkyDie"]
                 and entries[1]["objects"] == ["IhpDie"], str(entries))
        tc.check("a document with two PDKs in it knows that it is mixed",
                 gds_tech.is_mixed(doc))
        tc.check("the summary names both",
                 "SkyWater SKY130" in gds_tech.describe(doc)
                 and "IHP-PDK SG13G2" in gds_tech.describe(doc),
                 gds_tech.describe(doc))

        gds_tech.tag(ihp, _SKY)
        tc.check("two dies from the same PDK are one entry, not two",
                 len(gds_tech.technologies(doc)) == 1
                 and not gds_tech.is_mixed(doc))
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_stackup(tc):
    gds_tech.clear_cache()
    if not os.path.isfile(_SKY_XML):
        tc.skip("gds_tech: the bundled SKY130 stackup is read per chip",
                f"{_SKY_XML} is not present")
        return

    doc = new_document("TechStackup")
    try:
        block = _block(doc, "Die")
        gds_tech.tag(block, _SKY)
        stack = gds_tech.stackup_of(block)
        tc.check("a die's own stackup is read from the PDK it was imported "
                 "with — which is what puts its layers at the right height",
                 isinstance(stack, dict) and bool(stack), str(type(stack)))
        tc.check("reading it again is the same parse, not a second one",
                 gds_tech.stackup_of(block) is stack)

        gds_tech.tag(block, _IHP)
        tc.check("a technology whose stackup file is missing gives None "
                 "rather than another chip's stackup",
                 gds_tech.stackup_of(block) is None)

        untagged = _block(doc, "Lid")
        tc.check("a part with no technology has no stackup",
                 gds_tech.stackup_of(untagged) is None)
    finally:
        FreeCAD.closeDocument(doc.Name)


def _gds_layer(doc, name, layer, datatype, z=0.0):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = Part.makeBox(0.5, 0.5, 0.002, V(0, 0, z))
    obj.addProperty("App::PropertyInteger", "GDSLayerID", "LOD", "")
    obj.addProperty("App::PropertyInteger", "GDSDatatype", "LOD", "")
    obj.GDSLayerID = layer
    obj.GDSDatatype = datatype
    return obj


def _check_materials_per_chip(tc):
    """
    The point of recording a technology per chip, shown end to end.

    Two dies, two PDKs, one document. Materials are assigned with the SG13G2
    stackup as the document-wide fallback — the state you are in after
    importing that die last. The SKY130 die's met5 is not in that stackup at
    all, so without its own technology it would come out with no material,
    and a thermal model built from it would be missing its top metal.
    """
    ihp_xml = os.path.join(REPO_ROOT, "resources", "stack_info",
                           "IHP-PDK_SG13G2", "SG13G2_200um.xml")
    if not (os.path.isfile(ihp_xml) and os.path.isfile(_SKY_XML)):
        tc.skip("gds_tech: each die is classified with its own PDK",
                "the bundled stackups are not present")
        return

    import core.materials as materials
    from core.Core_Functionality import parse_stackup_xml
    gds_tech.clear_cache()

    doc = new_document("TechMaterials")
    try:
        sky_group = doc.addObject("App::DocumentObjectGroup", "GDS_Die_Sky")
        gds_tech.tag(sky_group, gds_tech.profile("SkyWater SKY130", "", "", _SKY_XML))
        sky_layer = _gds_layer(doc, "Layer_met5_72", 72, 20)
        sky_group.addObject(sky_layer)

        ihp_group = doc.addObject("App::DocumentObjectGroup", "GDS_Die_Ihp")
        gds_tech.tag(ihp_group, gds_tech.profile("IHP-PDK SG13G2", "", "", ihp_xml))
        ihp_layer = _gds_layer(doc, "Layer_TopMetal2_134", 134, 0, z=1.0)
        ihp_group.addObject(ihp_layer)

        stray = _gds_layer(doc, "Layer_met5_72_loose", 72, 20, z=2.0)
        doc.recompute()

        report = materials.assign_materials(doc, stackup_data=parse_stackup_xml(ihp_xml))
        assigned = {name: material for name, material, _ in report["assigned"]}
        unassigned = {name for name, _ in report["unassigned"]}

        tc.check("the SG13G2 die's top metal is assigned, as it always was",
                 assigned.get(ihp_layer.Name) == "Aluminium", str(assigned))
        tc.check("...and so is the SKY130 die's, although the stackup passed "
                 "in does not contain its layer at all — it is classified "
                 "with its own die's PDK",
                 assigned.get(sky_layer.Name) == "Aluminium", str(assigned))
        tc.check("a layer belonging to no chip still falls back to the "
                 "stackup given, and stays unassigned when that does not "
                 "describe it — which is what the per-die lookup avoids",
                 stray.Name in unassigned, str(report["unassigned"]))
    finally:
        FreeCAD.closeDocument(doc.Name)
