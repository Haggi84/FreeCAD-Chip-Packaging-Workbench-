# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for the proposed pinout — core.netlist.propose_connections —
and for exporting a document's wires back out as a netlist.

The proposal exists for the case where nobody has decided yet which pad goes
to which pin, so what matters is that it gives something a person can start
from: pads paired with the pins facing them, no crossings, and everything it
could not pair named rather than dropped.

Pairing by angle is what avoids the crossings. Nearest-neighbour pairing gives
a shorter total on paper and a diagram full of crossed wires.
"""

import math
import os
import shutil
import tempfile

import FreeCAD
import Part

from _harness import TestCase, new_document

import core.netlist as netlist

V = FreeCAD.Vector


def _contact_point(doc, name, point, pad_name="", source=""):
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


def _ring(doc, prefix, count, radius, z, phase=0.0, pad_names=()):
    """A ring of contact points about the origin, created out of order — the
    proposal must not depend on the order they happen to exist in."""
    made = []
    order = list(range(count))
    order = order[::2] + order[1::2]
    for slot, i in enumerate(order):
        angle = phase + 2.0 * math.pi * i / count
        point = V(radius * math.cos(angle), radius * math.sin(angle), z)
        pad_name = pad_names[i] if i < len(pad_names) else ""
        made.append(_contact_point(doc, f"{prefix}{slot + 1:03d}", point, pad_name))
    return made


def _die(doc, half=1.0):
    die = doc.addObject("Part::Feature", "Chip_Block")
    die.Shape = Part.makeBox(2 * half, 2 * half, 0.2, V(-half, -half, 0.0))
    die.addProperty("App::PropertyBool", "IsChipProxy", "ChipProxy", "")
    die.IsChipProxy = True
    return die


def run():
    tc = TestCase("netlist_propose")
    _check_proposal(tc)
    _check_unequal_counts(tc)
    _check_refusals(tc)
    _check_round_trip(tc)
    _check_export(tc)
    _check_multi_die(tc)
    _check_stacked_dies(tc)
    return tc.results


def _die_at(doc, name, at, size):
    die = doc.addObject("Part::Feature", name)
    die.Shape = Part.makeBox(*size, at)
    die.addProperty("App::PropertyBool", "IsChipProxy", "ChipProxy", "")
    die.IsChipProxy = True
    return die


def _pads_on(doc, prefix, die_at, size, z, count=4):
    """Pads spread around the top face of a die."""
    x0, y0 = die_at
    w, h = size
    spots = ((x0 + 0.2 * w, y0 + 0.2 * h), (x0 + 0.8 * w, y0 + 0.2 * h),
             (x0 + 0.8 * w, y0 + 0.8 * h), (x0 + 0.2 * w, y0 + 0.8 * h))
    return [_contact_point(doc, f"{prefix}{i + 1:03d}", V(x, y, z))
            for i, (x, y) in enumerate(spots[:count])]


def _check_multi_die(tc):
    """Two dies side by side: each pairs with the pins along its own side."""
    doc = new_document("ProposeTwoDies")
    try:
        _die_at(doc, "Left_Block", V(-4.0, -1.0, 0.0), (2.0, 2.0, 0.2))
        _die_at(doc, "Right_Block", V(2.0, -1.0, 0.0), (2.0, 2.0, 0.2))
        _pads_on(doc, "LeftPad_", (-4.0, -1.0), (2.0, 2.0), 0.2)
        _pads_on(doc, "RightPad_", (2.0, -1.0), (2.0, 2.0), 0.2)
        # Four bond fingers round each die, as a two-die module would have.
        for index, (cx, prefix) in enumerate(((-3.0, "left"), (3.0, "right"))):
            for slot in range(4):
                angle = math.pi / 4 + slot * math.pi / 2
                point = V(cx + 3.0 * math.cos(angle), 3.0 * math.sin(angle), 0.2)
                _contact_point(doc, f"contact_point_housing_{index * 4 + slot + 1:03d}",
                               point)
        doc.recompute()

        rows, report = netlist.propose_connections(doc)
        tc.check("every pad of both dies is bonded", len(rows) == 8, str(report))
        tc.check("the report is per die, naming each and its pad count",
                  sorted((d["die"], d["pads"]) for d in report["per_die"])
                  == [("Left_Block", 4), ("Right_Block", 4)],
                  str(report["per_die"]))
        tc.check("every connection records which die it belongs to",
                  all(row["die"] in ("Left_Block", "Right_Block") for row in rows),
                  str(rows))

        # Each die's pins must be the ones on its own side, which is what
        # pairing per die about its own centre is for.
        for row in rows:
            pad = doc.getObject(row["from"])
            pin = doc.getObject(row["to"])
            own_side = (pad.ContactPoint.x < 0) == (pin.ContactPoint.x < 0)
            if not own_side:
                tc.check("each die bonds to the pins on its own side", False,
                          f"{row['from']} -> {row['to']}")
                break
        else:
            tc.check("each die bonds to the pins on its own side", True)
        tc.check("no wires cross", report["crossings"] == 0, str(report))
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_stacked_dies(tc):
    """Two tiers: pads of the upper die must not be counted on the lower one."""
    doc = new_document("ProposeStack")
    try:
        _die_at(doc, "Base_Block", V(-1.5, -1.5, 0.0), (3.0, 3.0, 0.2))
        _die_at(doc, "Upper_Block", V(-0.5, -0.5, 0.225), (1.0, 1.0, 0.15))
        _pads_on(doc, "BasePad_", (-1.5, -1.5), (3.0, 3.0), 0.2)
        _pads_on(doc, "UpperPad_", (-0.5, -0.5), (1.0, 1.0), 0.375)
        _ring(doc, "contact_point_housing_", 8, 6.0, 0.2)
        doc.recompute()

        rows, report = netlist.propose_connections(doc)
        by_die = {entry["die"]: entry for entry in report["per_die"]}
        tc.check("each tier is reported separately, with its own pads",
                  by_die.get("Base_Block", {}).get("pads") == 4
                  and by_die.get("Upper_Block", {}).get("pads") == 4,
                  str(report["per_die"]))
        tc.check("the tiers are numbered",
                  by_die["Base_Block"]["tier"] == 0
                  and by_die["Upper_Block"]["tier"] == 1, str(report["per_die"]))
        tc.check("all eight pads are bonded, four from each tier",
                  len(rows) == 8 and len([r for r in rows if r["die"] == "Upper_Block"]) == 4,
                  str(rows))

        tmp = tempfile.mkdtemp(prefix="propose_stack_")
        try:
            path = netlist.write_netlist_csv(rows, os.path.join(tmp, "stack.csv"))
            with open(path, encoding="utf-8") as fh:
                header = fh.readline().strip()
            tc.check("the netlist carries a die column when the dies differ",
                      header == "net,from,to,die", header)
            read_back = netlist.read_netlist_csv(path)
            tc.check("...and reading it back still gives the same connections",
                      [(r["from"], r["to"]) for r in read_back]
                      == [(r["from"], r["to"]) for r in rows], str(read_back))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        FreeCAD.closeDocument(doc.Name)


def _angle_of(cp):
    return math.atan2(cp.ContactPoint.y, cp.ContactPoint.x)


def _check_proposal(tc):
    doc = new_document("ProposeRing")
    try:
        _die(doc)
        die_pads = _ring(doc, "ContactPoint_", 8, 0.6, 0.2,
                         pad_names=("VDD", "", "", "", "", "", "", ""))
        _ring(doc, "contact_point_housing_", 8, 4.0, 0.2, phase=math.pi / 8)
        doc.recompute()

        die, package = netlist.classify_contact_points(doc)
        tc.check("contact points over the die are die-side, the rest are the package",
                  len(die) == 8 and len(package) == 8,
                  f"{len(die)} die, {len(package)} package")

        rows, report = netlist.propose_connections(doc)
        tc.check("every pad gets a pin", len(rows) == 8, str(report))
        tc.check("nothing is left unpaired",
                  not report["unpaired_die"] and not report["unpaired_package"],
                  str(report))
        tc.check("no two wires cross — the point of pairing by angle",
                  report["crossings"] == 0, str(report))

        pairs = {row["from"]: row["to"] for row in rows}
        tc.check("each pad and each pin is used exactly once",
                  len(pairs) == 8 and len(set(pairs.values())) == 8, str(rows))

        by_name = {cp.Name: cp for cp in die + package}
        by_pad_name = {cp.PadName: cp for cp in die_pads if cp.PadName}
        worst = 0.0
        for row in rows:
            a = by_name.get(row["from"]) or by_pad_name[row["from"]]
            b = by_name[row["to"]]
            gap = abs((_angle_of(a) - _angle_of(b) + math.pi) % (2 * math.pi) - math.pi)
            worst = max(worst, gap)
        tc.check("each pad is bonded to the pin facing it, within half a pitch",
                  worst < math.pi / 8 + 1e-9, f"worst {math.degrees(worst):.1f} deg")

        tc.check("a labelled pad names its net and is referred to by its label",
                  any(row["net"] == "VDD" and row["from"] == "VDD" for row in rows),
                  str(rows))
        tc.check("unlabelled pads get a placeholder net named after their die, "
                  "and are referred to by object name",
                  all(row["net"].startswith("Chip_Block_")
                      and row["from"].startswith("ContactPoint_")
                      for row in rows if row["net"] != "VDD"), str(rows))
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_unequal_counts(tc):
    doc = new_document("ProposeFewerPads")
    try:
        _die(doc)
        _ring(doc, "ContactPoint_", 6, 0.6, 0.2)
        _ring(doc, "contact_point_housing_", 8, 4.0, 0.2, phase=math.pi / 8)
        doc.recompute()
        rows, report = netlist.propose_connections(doc)
        tc.check("fewer pads than pins: every pad is bonded and the spare pins "
                  "are named, not dropped",
                  len(rows) == 6 and len(report["unpaired_package"]) == 2
                  and not report["unpaired_die"], str(report))
        tc.check("...and the wires still do not cross",
                  report["crossings"] == 0, str(report))
    finally:
        FreeCAD.closeDocument(doc.Name)

    doc = new_document("ProposeFewerPins")
    try:
        _die(doc)
        _ring(doc, "ContactPoint_", 10, 0.6, 0.2)
        _ring(doc, "contact_point_housing_", 4, 4.0, 0.2)
        doc.recompute()
        rows, report = netlist.propose_connections(doc)
        tc.check("more pads than pins: the pins are used up and the spare pads "
                  "are named",
                  len(rows) == 4 and len(report["unpaired_die"]) == 6, str(report))
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_refusals(tc):
    doc = new_document("ProposeNoDie")
    try:
        _ring(doc, "contact_point_housing_", 4, 4.0, 0.2)
        doc.recompute()
        rows, report = netlist.propose_connections(doc)
        tc.check("without a die there is nothing to propose, and the reason says so",
                  rows == [] and "die" in (report["problem"] or ""), str(report))
    finally:
        FreeCAD.closeDocument(doc.Name)

    doc = new_document("ProposeNoPackage")
    try:
        _die(doc)
        _ring(doc, "ContactPoint_", 4, 0.6, 0.2)
        doc.recompute()
        rows, report = netlist.propose_connections(doc)
        tc.check("a die with no package pins is refused too",
                  rows == [] and bool(report["problem"]), str(report))
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_round_trip(tc):
    tmp = tempfile.mkdtemp(prefix="propose_test_")
    doc = new_document("ProposeRoundTrip")
    try:
        _die(doc)
        _ring(doc, "ContactPoint_", 4, 0.6, 0.2)
        _ring(doc, "contact_point_housing_", 4, 4.0, 0.2, phase=math.pi / 4)
        doc.recompute()
        rows, _report = netlist.propose_connections(doc)
        path = netlist.write_netlist_csv(
            rows, os.path.join(tmp, "proposal.csv"),
            comments=["proposed, edit before use"])
        read_back = netlist.read_netlist_csv(path)
        tc.check("a written proposal reads back as the same connections",
                  [(r["net"], r["from"], r["to"]) for r in read_back]
                  == [(r["net"], r["from"], r["to"]) for r in rows], str(read_back))
        with open(path, encoding="utf-8") as fh:
            first_line = fh.readline()
        tc.check("the note survives as a comment line the reader skips",
                  first_line.startswith("# proposed"), first_line)

        unmatched = [(row["from"], netlist.find_contact_point(doc, row["from"])[1])
                     for row in read_back
                     if netlist.find_contact_point(doc, row["from"])[0] is None]
        tc.check("every name the proposal writes matches a contact point again",
                  not unmatched, str(unmatched))
    finally:
        FreeCAD.closeDocument(doc.Name)
        shutil.rmtree(tmp, ignore_errors=True)


def _check_export(tc):
    tmp = tempfile.mkdtemp(prefix="export_netlist_test_")
    doc = new_document("ExportNetlist")
    try:
        _die(doc)
        pads = _ring(doc, "ContactPoint_", 2, 0.6, 0.2, pad_names=("VDD", "GND"))
        pins = _ring(doc, "contact_point_housing_", 2, 4.0, 0.2)
        for index, (pad, pin) in enumerate(zip(pads, pins), start=1):
            wire = doc.addObject("Part::Feature", f"BondWire_{index:03d}")
            wire.Shape = Part.makeBox(0.1, 0.1, 0.1)
            for prop in ("StartCP", "EndCP", "NetName"):
                wire.addProperty("App::PropertyString", prop, "Wirebond", "")
            wire.StartCP, wire.EndCP = pad.Name, pin.Name
            wire.NetName = f"N{index}"
        doc.recompute()

        path = os.path.join(tmp, "bonded.csv")
        count = netlist.export_netlist_csv(doc, path)
        rows = netlist.read_netlist_csv(path)
        tc.check("the document's wires export as a netlist",
                  count == 2 and len(rows) == 2,
                  f"{count} written, {len(rows)} read")
        tc.check("pads are exported by their label where it is unambiguous",
                  {r["from"] for r in rows} == {"VDD", "GND"}, str(rows))
        tc.check("the exported netlist matches the same contact points again",
                  all(netlist.find_contact_point(doc, r["to"])[0] is not None
                      for r in rows), str(rows))
    finally:
        FreeCAD.closeDocument(doc.Name)
        shutil.rmtree(tmp, ignore_errors=True)
