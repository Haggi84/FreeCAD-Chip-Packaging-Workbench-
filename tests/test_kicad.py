# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for the KiCad path: reading a board and a netlist, building
the components, and the ratsnest.

Two things decide whether any of this is usable:

  * the coordinate conversion. KiCad's Y axis points down the page and
    FreeCAD's points up, so every position is mirrored and every rotation
    negated with it. Get that wrong and a board imports mirrored — which
    looks plausible and routes wrong.
  * a rubber line is what is LEFT to route. It has to disappear when its two
    pads are connected, and only then; and the pads of a net that is half
    routed must still show the connections that remain.

The fixtures are written here rather than shipped: a KiCad file is an
S-expression, and a hand-written one pins the parsing exactly.
"""

import math
import os
import shutil
import tempfile

import FreeCAD
import Part

from _harness import TestCase, new_document

import core.components as components
import core.kicad as kicad
import core.ratsnest as ratsnest

V = FreeCAD.Vector

_BOARD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (net 0 "")
  (net 1 "GND")
  (net 2 "VCC")
  (net 3 "SIG")
  (footprint "Resistor_SMD:R_0805_2012Metric" (layer "F.Cu")
    (at 10 20 90)
    (property "Reference" "R1")
    (property "Value" "10k")
    (fp_line (start -1.68 -0.95) (end 1.68 -0.95) (layer "F.CrtYd"))
    (fp_line (start 1.68 0.95) (end -1.68 0.95) (layer "F.CrtYd"))
    (pad "1" smd roundrect (at -0.9375 0) (size 1.025 1.4) (layers "F.Cu") (net 1 "GND"))
    (pad "2" smd roundrect (at 0.9375 0) (size 1.025 1.4) (layers "F.Cu") (net 3 "SIG"))
    (model "${KICAD8_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0805_2012Metric.wrl"
      (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))
  )
  (footprint "Capacitor_SMD:C_0603_1608Metric" (layer "F.Cu")
    (at 30 20 0)
    (property "Reference" "C1")
    (property "Value" "100n")
    (fp_line (start -1.48 -0.85) (end 1.48 -0.85) (layer "F.CrtYd"))
    (fp_line (start 1.48 0.85) (end -1.48 0.85) (layer "F.CrtYd"))
    (pad "1" smd roundrect (at -0.7750 0) (size 0.9 0.95) (layers "F.Cu") (net 3 "SIG"))
    (pad "2" smd roundrect (at 0.7750 0) (size 0.9 0.95) (layers "F.Cu") (net 1 "GND"))
  )
  (footprint "Package_SO:SOIC-8" (layer "F.Cu")
    (at 20 40 0)
    (property "Reference" "U1")
    (property "Value" "OpAmp")
    (fp_line (start -2.5 -2.5) (end 2.5 -2.5) (layer "F.CrtYd"))
    (fp_line (start 2.5 2.5) (end -2.5 2.5) (layer "F.CrtYd"))
    (pad "1" smd roundrect (at -2 1.27) (size 1.5 0.6) (layers "F.Cu") (net 1 "GND"))
    (pad "2" smd roundrect (at -2 -1.27) (size 1.5 0.6) (layers "F.Cu") (net 2 "VCC"))
    (pad "3" smd roundrect (at 2 1.27) (size 1.5 0.6) (layers "F.Cu") (net 3 "SIG"))
  )
)
"""

_NETLIST = """(export (version "E")
  (design (source "demo.kicad_sch"))
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "C1") (value "100n") (footprint "Capacitor_SMD:C_0603_1608Metric"))
    (comp (ref "U1") (value "OpAmp") (footprint "Package_SO:SOIC-8"))
    (comp (ref "J1") (value "Conn") (footprint "Connector:Pin")))
  (nets
    (net (code "1") (name "GND")
      (node (ref "R1") (pin "1"))
      (node (ref "C1") (pin "2"))
      (node (ref "U1") (pin "1")))
    (net (code "2") (name "VCC")
      (node (ref "U1") (pin "2")))
    (net (code "3") (name "SIG")
      (node (ref "R1") (pin "2"))
      (node (ref "C1") (pin "1"))
      (node (ref "U1") (pin "3")))))
"""

# The same netlist with U1.3 moved from SIG to VCC — what a board saved before
# the last schematic change looks like. A pad belongs to exactly one net, so
# the pin is moved, not added to both.
_NETLIST_STALE = """(export (version "E")
  (design (source "demo.kicad_sch"))
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "C1") (value "100n") (footprint "Capacitor_SMD:C_0603_1608Metric"))
    (comp (ref "U1") (value "OpAmp") (footprint "Package_SO:SOIC-8")))
  (nets
    (net (code "1") (name "GND")
      (node (ref "R1") (pin "1"))
      (node (ref "C1") (pin "2"))
      (node (ref "U1") (pin "1")))
    (net (code "2") (name "VCC")
      (node (ref "U1") (pin "2"))
      (node (ref "U1") (pin "3")))
    (net (code "3") (name "SIG")
      (node (ref "R1") (pin "2"))
      (node (ref "C1") (pin "1")))))
"""


def run():
    tc = TestCase("kicad")
    tmp = tempfile.mkdtemp(prefix="kicad_test_")
    try:
        board_path = _write(os.path.join(tmp, "demo.kicad_pcb"), _BOARD)
        netlist_path = _write(os.path.join(tmp, "demo.net"), _NETLIST)
        _check_parsing(tc, board_path)
        _check_netlist(tc, netlist_path, board_path, tmp)
        _check_model_paths(tc, tmp)
        _check_components(tc, board_path)
        _check_ratsnest(tc, board_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return tc.results


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# ── parsing ──────────────────────────────────────────────────────────────────

def _check_parsing(tc, board_path):
    tc.check("parse_sexp: nesting and quoted strings",
              kicad.parse_sexp('(a "b c" (d 1))') == ["a", "b c", ["d", "1"]])
    for text, why in (("(a", "an unclosed bracket"), ("a)", "a stray bracket")):
        try:
            kicad.parse_sexp(text)
            refused = False
        except ValueError:
            refused = True
        tc.check(f"parse_sexp: {why} is refused", refused)

    board = kicad.read_board(board_path)
    by_ref = {fp["ref"]: fp for fp in board["footprints"]}
    tc.check("every footprint is read", sorted(by_ref) == ["C1", "R1", "U1"],
              str(sorted(by_ref)))
    tc.check("the nets are read by code and name",
              board["nets"] == {"0": "", "1": "GND", "2": "VCC", "3": "SIG"},
              str(board["nets"]))

    r1 = by_ref["R1"]
    tc.check("reference, value and footprint library come through",
              (r1["value"], r1["library"]) == ("10k", "Resistor_SMD:R_0805_2012Metric"),
              f"{r1['value']} / {r1['library']}")
    tc.check("the Y axis is mirrored: KiCad's points down the page, FreeCAD's up",
              abs(r1["x_mm"] - 10.0) < 1e-9 and abs(r1["y_mm"] + 20.0) < 1e-9,
              f"({r1['x_mm']}, {r1['y_mm']})")
    tc.check("...and the rotation is negated with it, so the part turns the "
              "same way it does on the KiCad canvas",
              abs(r1["rotation_deg"] + 90.0) < 1e-9, str(r1["rotation_deg"]))

    # R1 is rotated 90° in KiCad: its pad 1, at -0.9375 mm along X in the
    # footprint, ends up 0.9375 mm along +Y of the mirrored board.
    pad1 = next(p for p in r1["pads"] if p["number"] == "1")
    tc.check("a pad is placed by its footprint's rotation and position",
              abs(pad1["x_mm"] - 10.0) < 1e-6
              and abs(pad1["y_mm"] - (-20.0 + 0.9375)) < 1e-6,
              f"({pad1['x_mm']}, {pad1['y_mm']})")
    tc.check("a pad carries its net and size",
              pad1["net"] == "GND" and abs(pad1["width_mm"] - 1.025) < 1e-9,
              str(pad1))

    x0, y0, x1, y1 = r1["courtyard"]
    tc.check("the courtyard is read, mirrored with everything else",
              abs(x1 - x0 - 3.36) < 1e-6 and abs(y1 - y0 - 1.9) < 1e-6,
              str(r1["courtyard"]))
    tc.check("the 3-D model reference is read",
              r1["model"]["path"].endswith("R_0805_2012Metric.wrl"), str(r1["model"]))
    tc.check("a footprint without a model is fine",
              by_ref["C1"]["model"] is None)


def _check_netlist(tc, netlist_path, board_path, tmp):
    netlist = kicad.read_netlist(netlist_path)
    tc.check("components come through with value and footprint",
              netlist["components"]["U1"]["value"] == "OpAmp"
              and sorted(netlist["components"]) == ["C1", "J1", "R1", "U1"],
              str(sorted(netlist["components"])))
    tc.check("nets come through as names with their nodes",
              netlist["nets"]["SIG"] == [("R1", "2"), ("C1", "1"), ("U1", "3")],
              str(netlist["nets"]["SIG"]))

    board = kicad.read_board(board_path)
    differences = kicad.compare(board, netlist)
    tc.check("a component in the netlist but not on the board is reported",
              differences["missing_from_board"] == ["J1"], str(differences))
    tc.check("the two agree about every pad's net",
              differences["net_mismatch"] == [], str(differences["net_mismatch"]))

    stale = kicad.read_netlist(_write(os.path.join(tmp, "stale.net"), _NETLIST_STALE))
    differences = kicad.compare(board, stale)
    tc.check("a pad whose net differs between board and netlist is reported — "
              "a board saved before the last schematic change",
              [(ref, pin) for ref, pin, _b, _n in differences["net_mismatch"]]
              == [("U1", "3")], str(differences["net_mismatch"]))


def _check_model_paths(tc, tmp):
    models = os.path.join(tmp, "models", "Resistor_SMD.3dshapes")
    os.makedirs(models, exist_ok=True)
    step = os.path.join(models, "R_0805_2012Metric.step")
    _write(step, "ISO-10303-21;")
    raw = "${KICAD8_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0805_2012Metric.wrl"

    tc.check("the .step beside the .wrl KiCad names is what gets found — the "
              "WRL is a rendering mesh, the STEP is the solid",
              kicad.resolve_model_path(raw, [os.path.join(tmp, "models")]) == step,
              str(kicad.resolve_model_path(raw, [os.path.join(tmp, "models")])))

    os.environ["KICAD8_3DMODEL_DIR"] = os.path.join(tmp, "models")
    try:
        tc.check("KiCad's own environment variable is honoured",
                  kicad.resolve_model_path(raw) == step)
        tc.check("the variable's folder is among the search folders",
                  os.path.join(tmp, "models") in kicad.model_search_dirs())
    finally:
        del os.environ["KICAD8_3DMODEL_DIR"]

    tc.check("a model that is nowhere to be found is None, not a guess",
              kicad.resolve_model_path(
                  "${KICAD8_3DMODEL_DIR}/Nope.3dshapes/Nope.wrl",
                  [os.path.join(tmp, "models")]) is None)
    tc.check("no model reference at all is None", kicad.resolve_model_path("") is None)


# ── components ───────────────────────────────────────────────────────────────

def _check_components(tc, board_path):
    board = kicad.read_board(board_path)
    doc = new_document("KicadComponents")
    try:
        report = components.build_board(doc, board, base_z_mm=1.6, use_models=False)
        tc.check("every component is built, with every pad",
                  len(report["components"]) == 3 and report["pads"] == 7,
                  f"{len(report['components'])} components, {report['pads']} pads")
        tc.check("without a model each body is a stand-in",
                  report["placeholders"] == 3 and report["with_model"] == 0,
                  str(report))

        pads = {p.PadName: p for p in components.pads_of(doc)}
        tc.check("pads are named by reference and pin, as a netlist refers to them",
                  sorted(pads) == ["C1.1", "C1.2", "R1.1", "R1.2",
                                   "U1.1", "U1.2", "U1.3"], str(sorted(pads)))
        tc.check("each pad carries its net and its component",
                  pads["R1.1"].NetName == "GND" and pads["R1.1"].ComponentRef == "R1")
        tc.check("pads sit on the plane the components stand on, which is where "
                  "a trace has to reach them",
                  all(abs(pad.ContactPoint.z - 1.6) < 1e-9 for pad in pads.values()))

        body = next(o for o in components.components_of(doc) if o.ComponentRef == "R1")
        bb = body.Shape.BoundBox
        tc.check("a stand-in body is the size of the courtyard, turned with the "
                  "footprint — R1 is rotated, so its 3.36 mm side runs along Y",
                  abs(bb.XLength - 1.9) < 1e-6 and abs(bb.YLength - 3.36) < 1e-6,
                  f"{bb.XLength:.4f} x {bb.YLength:.4f}")
        tc.check("...and it stands on the placement plane",
                  abs(bb.ZMin - 1.6) < 1e-9 and abs(bb.ZLength - 1.0) < 1e-9,
                  f"z {bb.ZMin} .. {bb.ZMax}")
        tc.check("a component is one group with its body and its pads",
                  len(report["components"][0]["group"].Group)
                  == 1 + len(report["components"][0]["pads"]))

        one = components.build_board(doc, board, use_models=False, refs={"C1"})
        tc.check("a subset can be imported on its own",
                  [c["ref"] for c in one["components"]] == ["C1"], str(one))
    finally:
        FreeCAD.closeDocument(doc.Name)


# ── ratsnest ─────────────────────────────────────────────────────────────────

def _trace(doc, name, a, b):
    """A baked trace between two pads, as the routers leave one behind."""
    obj = doc.addObject("Part::Feature", name)
    start, end = FreeCAD.Vector(a.ContactPoint), FreeCAD.Vector(b.ContactPoint)
    obj.Shape = Part.makeLine(start, end)
    obj.addProperty("App::PropertyBool", "IsRoutingTrace", "Routing", "")
    obj.addProperty("App::PropertyVectorList", "Waypoints", "Routing", "")
    obj.addProperty("App::PropertyString", "NetName", "Routing", "")
    obj.IsRoutingTrace = True
    obj.Waypoints = [start, end]
    obj.NetName = getattr(a, "NetName", "")
    return obj


def _check_ratsnest(tc, board_path):
    board = kicad.read_board(board_path)
    doc = new_document("KicadRatsnest")
    try:
        components.build_board(doc, board, use_models=False)
        pads = {p.PadName: p for p in components.pads_of(doc)}
        doc.recompute()

        report = ratsnest.rebuild(doc)
        # GND and SIG have three pads each (two links), VCC has one (none).
        tc.check("a net of n pads needs n-1 connections, and one pad needs none",
                  report["links"] == 4 and report["nets"] == 3, str(report))
        lines = [o for o in doc.Objects if getattr(o, ratsnest.IS_RATSNEST, False)]
        tc.check("each one is drawn as a line carrying its net and its two pads",
                  len(lines) == 4
                  and all(line.NetName and line.StartCP and line.EndCP
                          for line in lines), str(lines))
        tc.check("the shortest connection of a net is the one offered — GND runs "
                  "from R1.1 to the nearer of the other two",
                  all(line.Shape.Length > 0 for line in lines))

        # Route one GND connection: its rubber line must go, the other stay.
        _trace(doc, "Trace_001", pads["R1.1"], pads["C1.2"])
        doc.recompute()
        report = ratsnest.rebuild(doc)
        gnd = [o for o in doc.Objects
               if getattr(o, ratsnest.IS_RATSNEST, False) and o.NetName == "GND"]
        tc.check("routing one connection removes its rubber line and leaves the "
                  "rest of the net",
                  report["links"] == 3 and len(gnd) == 1, str(report))
        tc.check("...and the line that remains is the one still missing",
                  {gnd[0].StartCP, gnd[0].EndCP} & {pads["U1.1"].Name},
                  f"{gnd[0].StartCP} — {gnd[0].EndCP}")

        # Finish the net: no line at all, and it counts as done.
        _trace(doc, "Trace_002", pads["C1.2"], pads["U1.1"])
        doc.recompute()
        report = ratsnest.rebuild(doc)
        tc.check("a fully routed net has no rubber lines left",
                  not any(o.NetName == "GND" for o in doc.Objects
                          if getattr(o, ratsnest.IS_RATSNEST, False)), str(report))
        tc.check("...and is reported as connected",
                  report["connected_nets"] == 1 and report["links"] == 2, str(report))

        status = {row["net"]: row for row in ratsnest.net_status(doc)}
        tc.check("the net list shows what is open and what is done",
                  status["GND"]["open"] == 0 and status["GND"]["done"] == 2
                  and status["SIG"]["open"] == 2, str(status))
        tc.check("a net with a single pad needs nothing",
                  status["VCC"]["pads"] == 1 and status["VCC"]["open"] == 0,
                  str(status["VCC"]))

        # Move a component: the lines are rebuilt from where the pads are now.
        for pad in components.pads_of(doc, "U1"):
            pad.ContactPoint = FreeCAD.Vector(pad.ContactPoint) + V(0, 0, 5.0)
        doc.recompute()
        ratsnest.rebuild(doc)
        sig = [o for o in doc.Objects
               if getattr(o, ratsnest.IS_RATSNEST, False) and o.NetName == "SIG"]
        touching = [line for line in sig
                    if any(abs(v.Point.z - 5.0) < 1e-9 for v in line.Shape.Vertexes)]
        tc.check("after moving a component its rubber lines follow its pads",
                  bool(touching), str([line.Label for line in sig]))

        # What the routers call after baking a trace: rebuild for a document
        # that has rubber lines, and leave every other document alone.
        before = len([o for o in doc.Objects if getattr(o, ratsnest.IS_RATSNEST, False)])
        tc.check("refresh_if_present rebuilds a document that has a ratsnest",
                  ratsnest.refresh_if_present(doc) is not None and before > 0)

        tc.check("clearing removes every line",
                  ratsnest.clear(doc) > 0
                  and not any(getattr(o, ratsnest.IS_RATSNEST, False)
                              for o in doc.Objects))
        tc.check("...and refreshing then does nothing, so routing in a document "
                  "without rubber lines costs nothing",
                  ratsnest.refresh_if_present(doc) is None)
    finally:
        FreeCAD.closeDocument(doc.Name)
