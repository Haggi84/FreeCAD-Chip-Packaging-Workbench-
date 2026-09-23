# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.stack_levels — the PDK levels a layout leaves empty.

The numbers are checked against the bundled SG13G2 stackup rather than a
fixture, because the point of the feature is that the heights are the PDK's
own: a slab at the wrong Z is worse than no slab, since it looks like
geometry that was imported.

Three things have to hold and are easy to get wrong:

* which levels count — SUBGND and BACKSIDEGND sit at negative Z and are
  simulation reference planes, not process levels, and the volume they
  occupy is already modelled as epi and silicon;
* what "used" means — SG13G2 draws Metal1 as 8/0, its pin as 8/2 and its
  label as 8/25, and any of them means the level is in use, while SKY130
  needs the datatype to tell met1 (68/20) from the via above it (68/44);
* that the slabs stay out of everything that treats geometry as material —
  an empty level is not metal in the real die.
"""

import os

import FreeCAD
import Part

from _harness import TestCase, new_document, REPO_ROOT, load_module_from_file

import core.materials as materials
import core.stack_levels as stack_levels
from core.Core_Functionality import parse_stackup_xml

V = FreeCAD.Vector

_IHP_XML = os.path.join(REPO_ROOT, "resources", "stack_info",
                        "IHP-PDK_SG13G2", "SG13G2_200um.xml")
_SKY_XML = os.path.join(REPO_ROOT, "resources", "stack_info",
                        "SkyWater-PDK_SKY130", "SKY130A_300um.xml")

# SG13G2, from the stackup: name -> (layer, z0 µm, thickness µm)
_SG13G2 = {
    "Activ": (1, 0.0, 0.4),
    "Cont": (6, 0.4, 0.64),
    "Metal1": (8, 1.04, 0.42),
    "Via1": (19, 1.46, 0.54),
    "Metal5": (67, 5.09, 0.49),
    "MIM": (36, 5.6043, 0.1497),
    "TopMetal1": (126, 6.4303, 2.0),
    "TopMetal2": (134, 11.2303, 3.0),
}


def run():
    tc = TestCase("stack_levels")
    if not os.path.isfile(_IHP_XML):
        tc.skip("stack_levels: the PDK's own heights are used",
                f"{_IHP_XML} is not present")
        return tc.results

    stackup = parse_stackup_xml(_IHP_XML)
    _check_levels(tc, stackup)
    _check_used_and_unused(tc, stackup)
    _check_sky130_datatypes(tc)
    _check_shift(tc, stackup)
    _check_build(tc, stackup)
    _check_not_a_part(tc, stackup)
    return tc.results


def _check_levels(tc, stackup):
    found = {level["name"]: level for level in stack_levels.levels(stackup)}

    tc.check("every interconnect level of the PDK is offered",
             set(found) == {"Activ", "Cont", "Metal1", "Metal2", "Metal3",
                            "Metal4", "Metal5", "Via1", "Via2", "Via3", "Via4",
                            "MIM", "Vmim", "TopVia1", "TopVia2", "TopMetal1",
                            "TopMetal2"},
             str(sorted(found)))

    tc.check("the simulation reference planes below the die surface are not "
             "levels — the substrate already models that volume",
             "SUBGND" not in found and "BACKSIDEGND" not in found,
             str(sorted(found)))
    tc.check("neither is a dielectric — the oxide between the levels is the "
             "substrate's business",
             "LBE" not in found)

    for name, (layer, z0_um, t_um) in _SG13G2.items():
        level = found.get(name)
        if not tc.check(f"{name} is there", level is not None):
            continue
        tc.check(f"{name} keeps the PDK's own height and thickness",
                 abs(level["z0_mm"] - z0_um / 1000.0) < 1e-9
                 and abs(level["t_mm"] - t_um / 1000.0) < 1e-9,
                 f"{level['z0_mm']} / {level['t_mm']}")
        tc.check(f"{name} knows its GDS layer", level["layer"] == layer,
                 str(level["layer"]))

    tc.check("the levels come back bottom-first, as a stack is read",
             [level["name"] for level in stack_levels.levels(stackup)][:3]
             == ["Activ", "Cont", "Metal1"],
             str([level["name"] for level in stack_levels.levels(stackup)][:3]))
    tc.check("the name is the stackup's own spelling, not the upper-cased key",
             found["TopMetal2"]["name"] == "TopMetal2")


def _check_used_and_unused(tc, stackup):
    # A layout that routes on the top metals only — the case in the report.
    keys = {(134, 0), (133, 0), (126, 0), (39, 4)}
    empty = {level["name"] for level in stack_levels.unused(stackup, keys)}
    drawn = {level["name"] for level in stack_levels.used(stackup, keys)}

    tc.check("the levels the layout draws on are recognised as used",
             drawn == {"TopMetal2", "TopVia2", "TopMetal1"}, str(sorted(drawn)))
    tc.check("...and everything under them is reported as empty",
             empty == {"Activ", "Cont", "Metal1", "Metal2", "Metal3", "Metal4",
                       "Metal5", "Via1", "Via2", "Via3", "Via4", "MIM", "Vmim",
                       "TopVia1"},
             str(sorted(empty)))
    tc.check("a layer the stackup does not know about changes nothing",
             stack_levels.unused(stackup, keys | {(999, 0)}) ==
             stack_levels.unused(stackup, keys))

    # SG13G2 states no datatypes, so any datatype on the layer counts.
    pin_only = {(8, 2)}
    tc.check("a level drawn only as a pin marker still counts as used — the "
             "stackup states no datatype, so the layer number is what says it",
             "Metal1" not in {e["name"] for e in stack_levels.unused(stackup, pin_only)})

    tc.check("with nothing drawn at all, every level is empty",
             len(stack_levels.unused(stackup, set()))
             == len(stack_levels.levels(stackup)))
    tc.check("the summary says how many of the PDK's levels are drawn",
             "3 of 17" in stack_levels.describe(stackup, keys),
             stack_levels.describe(stackup, keys).splitlines()[0])


def _check_sky130_datatypes(tc):
    if not os.path.isfile(_SKY_XML):
        tc.skip("stack_levels: SKY130's shared layer numbers are told apart",
                "the bundled SKY130 stackup is not present")
        return
    stackup = parse_stackup_xml(_SKY_XML)
    found = {level["name"]: level for level in stack_levels.levels(stackup)}
    if not tc.check("SKY130's met1 and the via above it are separate levels",
                    "met1" in found and "via" in {n.lower() for n in found},
                    str(sorted(found))):
        return

    # met1 is 68/20, via (met1->met2) is 68/44: drawing met1 must not mark
    # the via as used, or an empty via level would go unmodelled.
    keys = {(68, 20)}
    empty = {level["name"].lower() for level in stack_levels.unused(stackup, keys)}
    tc.check("drawing met1 does not mark the via that shares its layer number "
             "as drawn too",
             "via" in empty, str(sorted(empty)))
    tc.check("...while met1 itself is used",
             "met1" not in empty, str(sorted(empty)))


def _check_shift(tc, stackup):
    tc.check("with no imported stack there is nothing to align to",
             stack_levels.stack_shift_mm({}, stackup) == 0.0)

    # As build_stack_mm_from_xml produces it: TopMetal1 at its stated height.
    stack = {(126, 0): {"t_mm": 0.002, "z0_mm": 0.0064303, "from_xml": True}}
    tc.check("a stack at the PDK's own heights needs no shift",
             abs(stack_levels.stack_shift_mm(stack, stackup)) < 1e-12,
             str(stack_levels.stack_shift_mm(stack, stackup)))

    # As drop_to_die_surface leaves it: the same layer slid down to z=0.
    dropped = {(126, 0): {"t_mm": 0.002, "z0_mm": 0.0, "from_xml": True}}
    tc.check("a stack dropped onto the die surface reports how far it moved, "
             "so the empty levels move with it instead of floating above",
             abs(stack_levels.stack_shift_mm(dropped, stackup) - 0.0064303) < 1e-9,
             str(stack_levels.stack_shift_mm(dropped, stackup)))

    tc.check("a stack built from rank heuristics rather than the XML is not "
             "measured against the PDK at all",
             stack_levels.stack_shift_mm(
                 {(126, 0): {"t_mm": 0.002, "z0_mm": 0.5}}, stackup) == 0.0)


def _check_build(tc, stackup):
    doc = new_document("StackLevels")
    try:
        keys = {(134, 0), (126, 0)}
        empty = stack_levels.unused(stackup, keys)
        made = stack_levels.build(doc, (0.0, 0.0, 2.0, 3.0), empty, stackup)

        tc.check("one slab per empty level", len(made) == len(empty),
                 f"{len(made)} vs {len(empty)}")
        tc.check("each spans the die outline",
                 all(abs(o.Shape.BoundBox.XLength - 2.0) < 1e-9
                     and abs(o.Shape.BoundBox.YLength - 3.0) < 1e-9 for o in made))

        by_name = {o.StackLevelName: o for o in made}
        metal3 = by_name["Metal3"]
        tc.check("Metal3's slab stands exactly where the PDK puts Metal3",
                 abs(metal3.Shape.BoundBox.ZMin - 3.03 / 1000.0) < 1e-9
                 and abs(metal3.Shape.BoundBox.ZLength - 0.49 / 1000.0) < 1e-9,
                 f"{metal3.Shape.BoundBox.ZMin} / {metal3.Shape.BoundBox.ZLength}")
        tc.check("...and says in its label that nothing is drawn there, so it "
                 "is not mistaken for an imported layer",
                 "[not in the layout]" in metal3.Label, metal3.Label)
        tc.check("...and carries the GDS layer it stands for",
                 metal3.GDSLayerID == 30, str(metal3.GDSLayerID))

        tc.check("they are all found again as a set",
                 len(stack_levels.existing(doc)) == len(made))
        shifted = stack_levels.build(doc, (0.0, 0.0, 2.0, 3.0),
                                     [e for e in empty if e["name"] == "Metal3"],
                                     stackup, shift_mm=0.001)
        tc.check("...by exactly the amount the stack moved",
                 abs(shifted[0].Shape.BoundBox.ZMin - (3.03 - 1.0) / 1000.0) < 1e-9,
                 str(shifted[0].Shape.BoundBox.ZMin))

        gone = stack_levels.remove(doc)
        tc.check("and they can all be taken away again",
                 gone == len(made) + 1 and not stack_levels.existing(doc),
                 str(gone))

        try:
            stack_levels.build(doc, (0.0, 0.0, 0.0, 3.0), empty, stackup)
            raised = False
        except ValueError:
            raised = True
        tc.check("a degenerate footprint is refused rather than built flat",
                 raised)
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_not_a_part(tc, stackup):
    doc = new_document("StackLevelsParts")
    try:
        real = doc.addObject("Part::Feature", "Layer_TopMetal2_134")
        real.Shape = Part.makeBox(2.0, 3.0, 0.003, V(0, 0, 0.0112303))
        real.addProperty("App::PropertyInteger", "GDSLayerID", "LOD", "")
        real.addProperty("App::PropertyInteger", "GDSDatatype", "LOD", "")
        real.GDSLayerID = 134
        real.GDSDatatype = 0

        made = stack_levels.build(doc, (0.0, 0.0, 2.0, 3.0),
                                  stack_levels.unused(stackup, {(134, 0)}),
                                  stackup)
        doc.recompute()

        parts = {obj.Name for obj in materials.physical_parts(doc)}
        tc.check("the imported layer is still a physical part",
                 real.Name in parts, str(sorted(parts)))
        tc.check("an empty level is not — it would otherwise put metal into "
                 "the thermal model where the die has none",
                 not any(o.Name in parts for o in made), str(sorted(parts)))

        drc = load_module_from_file("drc_levels_test", "core/drc.py")
        copper = {name for name, _solid in drc._copper_candidates(doc, set())}
        tc.check("the imported layer is copper the design rule check knows about",
                 real.Name in copper, str(sorted(copper)))
        tc.check("...but an empty level is not, so nothing is flagged for "
                 "being too close to a layer that is not there",
                 not any(o.Name in copper for o in made), str(sorted(copper)))

        transform = load_module_from_file("chip_transform_levels_test",
                                          "gds/ChipTransformCommand.py")
        moving = {o.Name for o in transform._gds_objects(doc)}
        tc.check("but it does belong to the die, and moves when the chip does",
                 all(o.Name in moving for o in made), str(sorted(moving)))

        keys = stack_levels.keys_in_document(doc)
        tc.check("reading the used layers back out of the document ignores "
                 "the empty levels, so completing the stack twice is a no-op",
                 keys == {(134, 0)}, str(sorted(keys)))
        tc.check("...and nothing is left to build the second time",
                 not stack_levels.unused(stackup, keys)
                 or {e["name"] for e in stack_levels.unused(stackup, keys)}
                 == {o.StackLevelName for o in made},
                 str(sorted(e["name"] for e in stack_levels.unused(stackup, keys))))
    finally:
        FreeCAD.closeDocument(doc.Name)
