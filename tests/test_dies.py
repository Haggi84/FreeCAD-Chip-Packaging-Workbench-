# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.dies — die identity, tiers, pads, stacking and the
die attach.

Two properties matter most, and both are about a stack being more than a
picture:

  * a pad belongs to ONE die. The pads of an upper die are inside the outline
    of the die below it, simply higher up, so anything that tests only "inside
    the outline" assigns them to both.
  * moving a die moves what its pads SAY, not only where their markers are
    drawn. A contact point stores its position as a property, and a wire is
    bonded to that stored position — leave it behind and the wire lands where
    the pad used to be.
"""

import FreeCAD
import Part

from _harness import TestCase, new_document, load_module_from_file

import core.dies as dies
import core.materials as materials

V = FreeCAD.Vector


def _proxy(doc, name, size, at):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = Part.makeBox(*size, at)
    obj.addProperty("App::PropertyBool", "IsChipProxy", "ChipProxy", "")
    obj.IsChipProxy = True
    return obj


def _pad(doc, name, point, source="", pad_name=""):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = Part.Vertex(point)
    for prop, ptype in (("ContactPoint", "App::PropertyVector"),
                        ("IsContactPoint", "App::PropertyBool"),
                        ("SourceObject", "App::PropertyString"),
                        ("PadName", "App::PropertyString")):
        obj.addProperty(ptype, prop, "Wirebond", "")
    obj.ContactPoint, obj.IsContactPoint = point, True
    obj.SourceObject, obj.PadName = source, pad_name
    return obj


def _two_dies(doc):
    """A 4 x 4 base die on the carrier and a 2 x 2 die standing beside it."""
    base = _proxy(doc, "Base_Block", (4.0, 4.0, 0.2), V(0, 0, 0))
    upper = _proxy(doc, "Upper_Block", (2.0, 2.0, 0.15), V(10.0, 0.0, 0.0))
    # Four pads at the corners, plus one in the middle — the one a die stacked
    # centrally will cover.
    for i, (x, y) in enumerate(((0.3, 0.3), (3.7, 0.3), (0.3, 3.7), (3.7, 3.7),
                                (2.0, 2.0)), start=1):
        _pad(doc, f"BasePad_{i:03d}", V(x, y, 0.2), "Base_Block", f"B{i}")
    for i, (x, y) in enumerate(((10.2, 0.2), (11.8, 1.8)), start=1):
        _pad(doc, f"UpperPad_{i:03d}", V(x, y, 0.15), "Upper_Block", f"U{i}")
    doc.recompute()
    return base, upper


def run():
    tc = TestCase("dies")
    _check_identity(tc)
    _check_pads(tc)
    _check_stacking(tc)
    _check_die_attach(tc)
    _check_chip_transform(tc)
    return tc.results


def _check_chip_transform(tc):
    """
    Move / Rotate Chip must move what a pad SAYS, not only its marker.

    It used to set placements alone, so after moving a chip every pad still
    reported the position it had before — and bonding, netlist matching and
    every design rule read that reported position, not the marker.
    """
    transform = load_module_from_file("chip_transform_dies_test",
                                      "gds/ChipTransformCommand.py")
    doc = new_document("DieTransform")
    try:
        _proxy(doc, "Base_Block", (2.0, 2.0, 0.2), V(0, 0, 0))
        pad = _pad(doc, "BasePad_001", V(0.5, 0.5, 0.2), "Base_Block", "B1")
        doc.recompute()
        objects = [doc.getObject("Base_Block"), pad]

        snapshot = {o.Name: (o.Placement.copy(),
                             FreeCAD.Vector(o.ContactPoint)
                             if getattr(o, "ContactPoint", None) is not None else None)
                    for o in objects}

        transform._translate_objects(objects, 1.0, 2.0, 0.5)
        doc.recompute()
        tc.check("after a move, the pad's stored position moved with its marker",
                  (FreeCAD.Vector(pad.ContactPoint) - V(1.5, 2.5, 0.7)).Length < 1e-9,
                  str(pad.ContactPoint))
        tc.check("...and it still agrees with where the marker is drawn",
                  (FreeCAD.Vector(pad.ContactPoint)
                   - pad.Shape.BoundBox.Center).Length < 1e-9,
                  f"{pad.ContactPoint} vs {pad.Shape.BoundBox.Center}")

        transform._rotate_objects(objects, V(0, 0, 1), 90.0, V(0, 0, 0))
        doc.recompute()
        tc.check("after a rotation, the stored position turned with the chip",
                  (FreeCAD.Vector(pad.ContactPoint) - V(-2.5, 1.5, 0.7)).Length < 1e-6,
                  str(pad.ContactPoint))

        transform._restore_placements(objects, snapshot)
        doc.recompute()
        tc.check("Restore Original puts the stored position back too",
                  (FreeCAD.Vector(pad.ContactPoint) - V(0.5, 0.5, 0.2)).Length < 1e-9,
                  str(pad.ContactPoint))
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_identity(tc):
    doc = new_document("DieIdentity")
    try:
        _two_dies(doc)
        found = dies.collect(doc)
        tc.check("both dies are found", len(found) == 2, str(found))
        tc.check("neither is stacked yet, so both are tier 0",
                  all(die.tier == 0 and die.below == "" for die in found), str(found))

        named = dies.identify(doc)
        tc.check("every die gets a name", [die.name for die in named] == ["U1", "U2"],
                  str([die.name for die in named]))
        tc.check("the name is recorded on the die itself",
                  doc.getObject("Base_Block").DieName == "U1")
        tc.check("pads are told which die they are on",
                  doc.getObject("BasePad_001").DieName == "U1"
                  and doc.getObject("UpperPad_001").DieName == "U2")

        doc.getObject("Base_Block").DieName = "MEM"
        again = dies.identify(doc)
        tc.check("a name changed by hand survives the next run",
                  [die.name for die in again] == ["MEM", "U2"],
                  str([die.name for die in again]))
        tc.check("...and its pads follow the new name",
                  doc.getObject("BasePad_001").DieName == "MEM")
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_pads(tc):
    doc = new_document("DiePads")
    try:
        base, _upper = _two_dies(doc)
        # A die sitting on the base, its pads 0.25 mm above the base's top.
        stacked = _proxy(doc, "Stacked_Block", (2.0, 2.0, 0.05), V(1.0, 1.0, 0.2))
        _pad(doc, "StackedPad_001", V(1.2, 1.2, 0.25), "Stacked_Block", "S1")
        doc.recompute()

        found = {die.block.Name: die for die in dies.collect(doc)}
        base_die = found["Base_Block"]
        stacked_die = found["Stacked_Block"]

        base_pads = {p.Name for p in dies.pads_of(doc, base_die)}
        stacked_pads = {p.Name for p in dies.pads_of(doc, stacked_die)}
        tc.check("a pad of the die above does not belong to the die below — its "
                  "own top is nearer, even though both are within tolerance",
                  "StackedPad_001" not in base_pads
                  and stacked_pads == {"StackedPad_001"},
                  f"base {sorted(base_pads)}, stacked {sorted(stacked_pads)}")
        tc.check("the base keeps its own five pads", len(base_pads) == 5,
                  str(sorted(base_pads)))
        tc.check("die_of finds the die a point sits on",
                  dies.die_of(list(found.values()), V(1.2, 1.2, 0.25)) is stacked_die)
        tc.check("a point in mid-air is on no die",
                  dies.die_of(list(found.values()), V(1.2, 1.2, 2.0)) is None)

        tiers = {die.block.Name: die.tier for die in dies.collect(doc)}
        tc.check("the stacked die is tier 1, on the base",
                  tiers["Stacked_Block"] == 1 and tiers["Base_Block"] == 0, str(tiers))

        covered = dies.covered_pads(doc, dies.collect(doc))
        tc.check("the base pad under the stacked die is reported as covered, "
                  "and the ones in the open are not",
                  any(pad.Name == "BasePad_005" for pad, _h, _c in covered)
                  and all(pad.Name != "BasePad_001" for pad, _h, _c in covered),
                  str([(p.Name, c.name) for p, _h, c in covered]))
        tc.check("a pad of the covering die is not itself covered",
                  all(pad.Name != "StackedPad_001" for pad, _h, _c in covered))
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_stacking(tc):
    doc = new_document("DieStacking")
    try:
        _two_dies(doc)
        found = {die.block.Name: die for die in dies.identify(doc)}
        base, upper = found["Base_Block"], found["Upper_Block"]
        pad = doc.getObject("UpperPad_001")
        before = FreeCAD.Vector(pad.ContactPoint)

        report = dies.stack_die(doc, upper, base, attach_mm=0.025)
        doc.recompute()

        tc.check("the die lands on the base with the attach between them",
                  abs(upper.z_bottom - (base.z_top + 0.025)) < 1e-9,
                  f"{upper.z_bottom} vs {base.z_top + 0.025}")
        tc.check("it is centred on the die below",
                  abs((upper.outline[0] + upper.outline[2]) / 2.0 - 2.0) < 1e-9
                  and abs((upper.outline[1] + upper.outline[3]) / 2.0 - 2.0) < 1e-9,
                  str(upper.outline))
        tc.check("it is recorded as tier 1, on the base die",
                  report["tier"] == 1 and upper.block.DieTier == 1
                  and upper.block.DieBelow == base.name)

        moved = FreeCAD.Vector(pad.ContactPoint)
        tc.check("the pad's marker moved with the die",
                  abs(pad.Shape.BoundBox.Center.z - upper.z_top) < 0.01,
                  f"marker at {pad.Shape.BoundBox.Center.z}, die top {upper.z_top}")
        tc.check("...and so did the position it stores, which is where a wire "
                  "would be bonded",
                  abs((moved - before).Length - report["delta"].Length) < 1e-9
                  and abs(moved.z - upper.z_top) < 1e-9,
                  f"{before} -> {moved}, die top {upper.z_top}")
        tc.check("the pad still belongs to its own die after the move",
                  {p.Name for p in dies.pads_of(doc, upper)}
                  == {"UpperPad_001", "UpperPad_002"},
                  str([p.Name for p in dies.pads_of(doc, upper)]))

        tc.check("the base pad it now covers is reported — it cannot be bonded "
                  "any more, and nothing shows that from above",
                  [pad for pad, _h, _c in report["covered_pads"]] == ["BasePad_005"],
                  str(report["covered_pads"]))
        tc.check("a die sitting wholly on its base overhangs nothing",
                  report["overhang_mm2"] < 1e-9, str(report["overhang_mm2"]))

        attach = report["attach"]
        tc.check("the die attach is built where the two dies meet",
                  attach is not None
                  and abs(attach.Shape.BoundBox.ZMin - base.z_top) < 1e-9
                  and abs(attach.Shape.BoundBox.ZLength - 0.025) < 1e-9,
                  str(attach.Shape.BoundBox if attach else None))
        tc.check("it spans the supported part of the die, 2 x 2 mm",
                  abs(attach.Shape.BoundBox.XLength - 2.0) < 1e-9
                  and abs(attach.Shape.BoundBox.YLength - 2.0) < 1e-9)
        tc.check("it is made of die attach epoxy, so it carries heat in an export",
                  materials.material_of(attach) is not None
                  and materials.material_of(attach).name == "Die attach epoxy")
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_die_attach(tc):
    doc = new_document("DieAttachEdges")
    try:
        _two_dies(doc)
        found = {die.block.Name: die for die in dies.identify(doc)}
        base, upper = found["Base_Block"], found["Upper_Block"]

        report = dies.stack_die(doc, upper, base, attach_mm=0.02, centre=False)
        tc.check("without centring the die keeps its X and Y",
                  abs(report["delta"].x) < 1e-12 and abs(report["delta"].y) < 1e-12,
                  str(report["delta"]))
        tc.check("a die hanging entirely off its base is reported as overhang",
                  abs(report["overhang_mm2"] - 4.0) < 1e-9, str(report["overhang_mm2"]))
        tc.check("...and gets no die attach, since nothing supports it",
                  report["attach"] is None or report["attach"].Shape.Volume == 0.0,
                  str(report["attach"]))
    except ValueError as exc:
        tc.check("a die hanging entirely off its base is refused with a reason",
                  "does not sit over" in str(exc), str(exc))
    finally:
        FreeCAD.closeDocument(doc.Name)

    doc = new_document("DieAttachNone")
    try:
        base, upper = _two_dies(doc)
        found = {die.block.Name: die for die in dies.collect(doc)}
        tc.check("a zero-thickness attach builds nothing rather than a zero solid",
                  dies.build_die_attach(doc, found["Upper_Block"],
                                        found["Base_Block"], 0.0) is None)
    finally:
        FreeCAD.closeDocument(doc.Name)
