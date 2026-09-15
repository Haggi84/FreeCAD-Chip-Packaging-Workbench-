# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for the path from layout to bonding paperwork: pad names from
the layout's labels, a wire's real length, placing wires without a session,
netlist import, and the bonding diagram.

The netlist checks care most about refusing to guess. Two pads with the same
name must be reported, not bonded to whichever came first, and importing the
same netlist twice must not double every wire.
"""

import csv
import math
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET

import FreeCAD
import Part
import gdstk
from FreeCAD import Base

from _harness import TestCase, REPO_ROOT, new_document, load_module_from_file

import core.bonding_diagram as bonding_diagram
import core.chip_proxy as chip_proxy
import core.netlist as netlist
import core.pad_names as pad_names
from core import Core_Functionality as CF
from core.Core_Functionality import parse_lyp, parse_map

mwb = load_module_from_file("wirebond_mwb_bonding_test", "wirebond/ManualWireBonding.py")

V = FreeCAD.Vector
_IHP = os.path.join(REPO_ROOT, "resources", "stack_info", "IHP-PDK_SG13G2")
_LYP = os.path.join(_IHP, "sg13g2.lyp")
_MAP = os.path.join(_IHP, "sg13g2.map")
_XML = os.path.join(_IHP, "SG13G2_200um.xml")

_BALL = dict(bond_type="Ball-Wedge", wedge_style="cut", wire_profile="spline",
             loop_height=0.3, diameter=0.025)
_JEDEC = dict(bond_type="Wedge-Wedge", wedge_style="cut", wire_profile="jedec",
              loop_height=0.3, diameter=0.025)


def run():
    tc = TestCase("bonding")
    tmp = tempfile.mkdtemp(prefix="bonding_test_")
    try:
        _check_label_matching(tc)
        _check_pad_names_from_layout(tc, tmp)
        _check_wire_length(tc)
        _check_netlist_and_diagram(tc, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return tc.results


# ── pad names ────────────────────────────────────────────────────────────────

def _check_label_matching(tc):
    labels = [{"text": "A", "x_mm": 0.5, "y_mm": 0.5},
              {"text": "B", "x_mm": 0.9, "y_mm": 0.9},
              {"text": "FAR", "x_mm": 5.0, "y_mm": 5.0}]
    tc.check("label_in_box: with two labels inside, the one nearest the centre wins",
              pad_names.label_in_box(labels, 0.0, 0.0, 1.0, 1.0) == "A")
    tc.check("label_in_box: a box with no label inside stays unnamed",
              pad_names.label_in_box(labels, 2.0, 2.0, 3.0, 3.0) == "")
    tc.check("label_for_pad: uses the pad's centre and size",
              pad_names.label_for_pad(labels, {"x_mm": 5.0, "y_mm": 5.0,
                                               "width_mm": 0.1, "height_mm": 0.1}) == "FAR")


def _write_labelled_gds(path):
    """Four 70 µm pads on TopMetal2 inside a seal ring; three carry a label."""
    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    cell = lib.new_cell("PADRING")
    cell.add(gdstk.rectangle((0, 0), (600, 600), layer=39, datatype=4))
    pads = (("VDD", 100, 100), ("GND", 300, 100), ("OUT", 500, 100), (None, 100, 500))
    for name, x, y in pads:
        cell.add(gdstk.rectangle((x - 35, y - 35), (x + 35, y + 35), layer=134, datatype=0))
        cell.add(gdstk.rectangle((x - 2, y - 2), (x + 2, y + 2), layer=134, datatype=2))
        if name:
            cell.add(gdstk.Label(name, (x, y), layer=134, texttype=25))
    lib.write_gds(path)


def _check_pad_names_from_layout(tc, tmp):
    if not all(os.path.isfile(p) for p in (_LYP, _MAP, _XML)):
        tc.skip("pad names from layout labels", "IHP PDK files not present")
        return
    gds = os.path.join(tmp, "padring.gds")
    _write_labelled_gds(gds)

    proxy = chip_proxy.extract_chip_proxy(gds, _LYP, _MAP, _XML)
    tc.check("proxy: all three labels are read", proxy.get("labels_found") == 3,
              str(proxy.get("labels_found")))
    found = sorted(p.get("label", "") for p in proxy["pads"])
    tc.check("proxy: each pad takes the label inside it, and the unlabelled pad "
              "stays unnamed rather than borrowing a neighbour's",
              found == ["", "GND", "OUT", "VDD"], str(found))

    doc = new_document("BondingPadNames")
    try:
        chip_proxy.build_chip_proxy_object(doc, proxy, name="PadRing")
        names = sorted(o.PadName for o in doc.Objects if getattr(o, "IsContactPoint", False))
        tc.check("proxy: the contact points carry the names as PadName",
                  names == ["", "GND", "OUT", "VDD"], str(names))
    finally:
        FreeCAD.closeDocument(doc.Name)

    doc = new_document("BondingPadNamesFull")
    try:
        layers, _ = parse_lyp(_LYP)
        count = CF.import_pin_pads_as_contacts(gds, parse_map(_MAP), doc, selected_layers=layers)
        names = sorted(getattr(o, "PadName", None) or ""
                       for o in doc.Objects if getattr(o, "IsContactPoint", False))
        tc.check("full import: auto PIN detection names its contact points the same way",
                  count == 4 and names == ["", "GND", "OUT", "VDD"],
                  f"{count} contact points: {names}")
    finally:
        FreeCAD.closeDocument(doc.Name)


# ── wire length and placement ────────────────────────────────────────────────

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


def _check_wire_length(tc):
    A = Base.Vector(0.0, 0.0, 0.30)
    B = Base.Vector(2.0, 0.5, 0.29)
    span = math.hypot(2.0, 0.5)
    H = 0.3

    info = {}
    mwb.create_bond_wire_3d(A, B, _JEDEC, info)
    # JEDEC trapezoid: straight rise over 7/8 of the span to 0.3 mm above the
    # higher pad, flat for 1/8, then straight down onto the lower pad.
    expected = math.hypot(0.875 * span, H) + 0.125 * span + (0.30 + H - 0.29)
    tc.check("the JEDEC profile's length along the loop is exactly its three "
              "segments, not the straight distance",
              info.get("arc_length_exact") is True
              and abs(info["arc_length_mm"] - expected) < 1e-9,
              f"got {info.get('arc_length_mm')}, expected {expected}")
    tc.check("span and loop height are recorded alongside",
              abs(info["span_mm"] - span) < 1e-12 and abs(info["loop_height_mm"] - H) < 1e-12)

    spline = {}
    mwb.create_bond_wire_3d(A, B, _BALL, spline)
    # The curve passes through the top of the ball's neck, the apex (28 % of
    # the span, H above the higher pad) and the landing, in that order, so it
    # is at least as long as the straight segments joining them, plus the neck.
    neck = 0.8 * 0.75 * _BALL["diameter"]
    lower = (neck
             + math.hypot(0.28 * span, (max(A.z, B.z) + H) - (A.z + neck))
             + math.hypot(0.72 * span, (max(A.z, B.z) + H) - B.z))
    tc.check("a ball-wedge spline loop is at least as long as the straight "
              "segments through its neck, apex and landing",
              spline.get("arc_length_exact") is True
              and lower <= spline["arc_length_mm"] < lower * 1.5,
              f"{spline} vs lower bound {lower}")

    doc = new_document("BondingPlace")
    try:
        a = _contact_point(doc, "ContactPoint_001", A)
        b = _contact_point(doc, "ContactPoint_002", B)
        wire = mwb.place_bond_wire(doc, a, b, _JEDEC)
        tc.check("place_bond_wire works without a session or GUI",
                  wire.Name == "BondWire_001" and wire.Shape.Volume > 0)
        tc.check("place_bond_wire records the length along the loop as WireLength",
                  abs(wire.WireLength.Value - expected) < 1e-6,
                  f"{wire.WireLength.Value} vs {expected}")
        tc.check("place_bond_wire records span, loop height, diameter and bond type",
                  abs(wire.SpanLength.Value - span) < 1e-6
                  and abs(wire.LoopHeight.Value - H) < 1e-6
                  and abs(wire.WireDiameter.Value - 0.025) < 1e-9
                  and wire.BondType == "Wedge-Wedge")
        tc.check("place_bond_wire records both contact points and a default net",
                  (wire.StartCP, wire.EndCP, wire.NetName)
                  == ("ContactPoint_001", "ContactPoint_002", "Net_001"))

        doc.addObject("Part::Feature", "BondWire_007").Shape = Part.makeBox(1, 1, 1, V(50, 0, 0))
        second = mwb.place_bond_wire(doc, a, b, _JEDEC, net_name="VDD")
        tc.check("numbering continues after the highest existing wire, so nothing "
                  "is renamed or collides",
                  second.Name == "BondWire_008" and second.NetName == "VDD",
                  f"{second.Name} / {second.NetName}")
    finally:
        FreeCAD.closeDocument(doc.Name)


# ── netlist and diagram ──────────────────────────────────────────────────────

def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _check_netlist_and_diagram(tc, tmp):
    good = _write(os.path.join(tmp, "netlist.csv"),
                  "# pads to pins\n"
                  "net,die_pad,package_pin\n"
                  "\n"
                  "VDD,VDD,Lead_L01\n"
                  "GND,gnd,2\n"
                  "SIG,IO,Lead_L01\n"
                  "NC,MISSING,Lead_L02\n")
    rows = netlist.read_netlist_csv(good)
    tc.check("read: comments and blank lines are skipped, die_pad/package_pin accepted",
              [(r["net"], r["from"], r["to"]) for r in rows]
              == [("VDD", "VDD", "Lead_L01"), ("GND", "gnd", "2"),
                  ("SIG", "IO", "Lead_L01"), ("NC", "MISSING", "Lead_L02")],
              str(rows))

    for text, what in (("a,b\n1,2\n", "without from/to columns"),
                       ("", "empty"),
                       ("from,to\nVDD,\n", "with a connection missing an end")):
        try:
            netlist.read_netlist_csv(_write(os.path.join(tmp, "bad.csv"), text))
            refused = False
        except ValueError:
            refused = True
        tc.check(f"read: a netlist {what} is refused", refused)

    doc = new_document("BondingNetlist")
    try:
        die = doc.addObject("Part::Feature", "Chip_Block")
        die.Shape = Part.makeBox(1.0, 1.0, 0.2, V(0, 0, 0))
        _contact_point(doc, "ContactPoint_001", V(0.2, 0.8, 0.2), "VDD", "Chip_Block")
        _contact_point(doc, "ContactPoint_002", V(0.2, 0.2, 0.2), "GND", "Chip_Block")
        _contact_point(doc, "ContactPoint_003", V(0.5, 0.5, 0.2), "IO", "Chip_Block")
        _contact_point(doc, "ContactPoint_004", V(0.6, 0.5, 0.2), "IO", "Chip_Block")
        for name, y, number in (("Lead_L01", 0.7, 1), ("Lead_L02", 0.1, 2)):
            lead = doc.addObject("Part::Feature", name)
            lead.Shape = Part.makeBox(1.0, 0.2, 0.2, V(2.0, y, 0))
            lead.addProperty("App::PropertyInteger", "PinNumber", "Leadframe", "")
            lead.PinNumber = number
        _contact_point(doc, "ContactPoint_010", V(2.5, 0.8, 0.2), source="Lead_L01")
        _contact_point(doc, "ContactPoint_011", V(2.5, 0.2, 0.2), source="Lead_L02")
        doc.recompute()

        place = lambda a, b: mwb.place_bond_wire(doc, a, b, _BALL)
        report = netlist.apply_netlist(doc, rows, place)
        wires = {o.NetName: o for o in doc.Objects if o.Name.startswith("BondWire_")}
        tc.check("apply: two connections are bonded",
                  len(report["placed"]) == 2 and set(wires) == {"VDD", "GND"},
                  str(report))
        tc.check("apply: a pad is found by its layout label and a lead by its name",
                  (wires["VDD"].StartCP, wires["VDD"].EndCP)
                  == ("ContactPoint_001", "ContactPoint_010"))
        tc.check("apply: matching is case-insensitive, and a lead is found by pin number",
                  (wires["GND"].StartCP, wires["GND"].EndCP)
                  == ("ContactPoint_002", "ContactPoint_011"))
        failed = {row["net"]: reason for row, reason in report["failed"]}
        tc.check("apply: two pads with the same name are reported, not guessed",
                  "matches 2 contact points by pad name" in failed.get("SIG", ""), str(failed))
        tc.check("apply: an unknown pad is reported",
                  "no contact point matches 'MISSING'" in failed.get("NC", ""), str(failed))

        again = netlist.apply_netlist(doc, rows, place)
        count = sum(1 for o in doc.Objects if o.Name.startswith("BondWire_"))
        tc.check("apply twice: existing wires are renamed, not duplicated",
                  len(again["placed"]) == 0 and len(again["updated"]) == 2 and count == 2,
                  f"{again} / {count} wires")

        svg_path, csv_path = bonding_diagram.write_bonding_diagram(doc, tmp, "pkg")
        tc.check("diagram: writes the SVG and the wire table",
                  os.path.isfile(svg_path) and os.path.isfile(csv_path))
        root = ET.parse(svg_path).getroot()
        lines = [e for e in root.iter("{http://www.w3.org/2000/svg}line")
                 if e.get("class") == "wire"]
        tc.check("diagram: the SVG is well-formed and draws one line per wire",
                  len(lines) == 2, f"{len(lines)} wire lines")
        with open(csv_path, encoding="utf-8") as fh:
            table = {r["net"]: r for r in csv.DictReader(fh)}
        vdd = table.get("VDD", {})
        tc.check("diagram: the wire table names pads by their layout label",
                  vdd.get("from") == "VDD", str(vdd))
        tc.check("diagram: the wire table carries length along the loop and loop height",
                  vdd.get("length_mm") == f"{wires['VDD'].WireLength.Value:.4f}"
                  and vdd.get("loop_height_mm") == f"{wires['VDD'].LoopHeight.Value:.4f}",
                  str(vdd))
    finally:
        FreeCAD.closeDocument(doc.Name)

    empty = new_document("BondingNoWires")
    try:
        try:
            bonding_diagram.write_bonding_diagram(empty, tmp, "empty")
            refused = False
        except ValueError:
            refused = True
        tc.check("diagram: a document without wires is refused, and nothing is written",
                  refused and not os.path.exists(os.path.join(tmp, "empty_wire_table.csv")))
    finally:
        FreeCAD.closeDocument(empty.Name)
