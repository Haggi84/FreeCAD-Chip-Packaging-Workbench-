# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.ports — the simulation ports drawn on a layout.

A port is a surface: give it volume and it stops being a port, so that is
checked rather than assumed. It is also parametric, because a port drawn by
hand is meant to be corrected by typing the number afterwards — so the face
has to follow its properties, not be baked once.

The rest is about it belonging to the chip: in the imported layout's group,
moving when the chip moves, and not being mistaken for copper by the design
rule check.
"""

import FreeCAD
import Part

from _harness import TestCase, new_document, load_module_from_file

import core.materials as materials
import core.ports as ports

V = FreeCAD.Vector

drc = load_module_from_file("drc_ports_test", "core/drc.py")


def run():
    tc = TestCase("ports")
    _check_face(tc)
    _check_snapping(tc)
    _check_object(tc)
    _check_parametric(tc)
    _check_belongs_to_the_chip(tc)
    return tc.results


def _check_face(tc):
    face = ports.port_face(V(0, 0, 0), V(2, 0, 0), 0.5)
    tc.check("a port is a surface, with no volume at all",
              face.Volume == 0.0 and len(face.Solids) == 0, str(face.Volume))
    tc.check("its area is its length times its height",
              abs(face.Area - 1.0) < 1e-9, str(face.Area))
    tc.check("+Z reaches up from the edge",
              abs(face.BoundBox.ZMin) < 1e-9 and abs(face.BoundBox.ZMax - 0.5) < 1e-9,
              f"z {face.BoundBox.ZMin} .. {face.BoundBox.ZMax}")

    down = ports.port_face(V(0, 0, 1.0), V(2, 0, 1.0), 0.5, "-Z")
    tc.check("-Z reaches down from it",
              abs(down.BoundBox.ZMin - 0.5) < 1e-9
              and abs(down.BoundBox.ZMax - 1.0) < 1e-9,
              f"z {down.BoundBox.ZMin} .. {down.BoundBox.ZMax}")
    tc.check("the height is a magnitude: the direction says which way",
              abs(ports.signed_height(0.5, "-Z") + 0.5) < 1e-12
              and abs(ports.signed_height(-0.5, "+Z") - 0.5) < 1e-12)

    # An edge that climbs in Z still gives a flat port: the segment and Z
    # span a plane between them.
    slanted = ports.port_face(V(0, 0, 0), V(1, 1, 1), 0.4)
    surface = slanted.Surface
    tc.check("a port on an edge that climbs in Z is still planar",
              hasattr(surface, "Axis") or "Plane" in str(type(surface)),
              str(type(surface)))
    tc.check("...and spans from the edge by its height",
              abs(slanted.BoundBox.ZLength - (1.0 + 0.4)) < 1e-9,
              str(slanted.BoundBox.ZLength))

    for start, end, height, why in (
        (V(0, 0, 0), V(0, 0, 0), 0.5, "no length"),
        (V(0, 0, 0), V(1, 0, 0), 0.0, "no height"),
    ):
        try:
            ports.port_face(start, end, height)
            refused = False
        except ValueError:
            refused = True
        tc.check(f"a port with {why} is refused, not built as a line", refused)


def _check_snapping(tc):
    edge = Part.makeLine(V(0, 0, 0), V(10, 0, 0)).Edges[0]
    tc.check("a click beside the edge lands on it",
              (ports.snap_to_edge(edge, V(3, 2, 1)) - V(3, 0, 0)).Length < 1e-9,
              str(ports.snap_to_edge(edge, V(3, 2, 1))))
    tc.check("a click past the end stays on the edge",
              (ports.snap_to_edge(edge, V(50, 0, 0)) - V(10, 0, 0)).Length < 1e-9,
              str(ports.snap_to_edge(edge, V(50, 0, 0))))


def _check_object(tc):
    doc = new_document("PortsObject")
    try:
        gds = doc.addObject("App::DocumentObjectGroup", "GDS_Die")
        gds.Label = "GDS_Die"
        port = ports.make_port(doc, V(0, 0, 0), V(2, 0, 0), 0.5,
                               source=("Layer_TopMetal2_134", "Edge7"),
                               impedance=50.0)
        doc.recompute()

        tc.check("the port is built and marked as one",
                  getattr(port, ports.IS_PORT, False) and port.PortNumber == 1)
        tc.check("it is a surface in the document too",
                  port.Shape.Volume == 0.0 and abs(port.Shape.Area - 1.0) < 1e-9)
        tc.check("it records the edge it was drawn on, for when someone asks "
                  "later what it belongs to",
                  (port.SourceObject, port.SourceSubElement)
                  == ("Layer_TopMetal2_134", "Edge7"))
        tc.check("its length follows from the two points",
                  abs(port.Width.Value - 2.0) < 1e-9, str(port.Width))
        tc.check("the reference impedance is carried for the solver",
                  abs(port.Impedance - 50.0) < 1e-9)

        group = doc.getObject(ports.GROUP_NAME)
        tc.check("ports live in their own group inside the imported chip's, so "
                  "they are part of it rather than loose in the document",
                  group is not None and port in group.Group
                  and group in gds.Group, str(group))

        second = ports.make_port(doc, V(0, 1, 0), V(1, 1, 0), 0.3, "-Z")
        tc.check("the next port is numbered on", second.PortNumber == 2)
        tc.check("ports_of finds them both", len(ports.ports_of(doc)) == 2)
        tc.check("next_number continues after the highest",
                  ports.next_number(doc) == 3)

        try:
            ports.make_port(doc, V(0, 0, 0), V(0, 0, 0), 0.5)
            refused = False
        except ValueError:
            refused = True
        tc.check("a degenerate port is refused before anything is added, so no "
                  "half-made object is left behind",
                  refused and len(ports.ports_of(doc)) == 2)
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_parametric(tc):
    doc = new_document("PortsParametric")
    try:
        port = ports.make_port(doc, V(0, 0, 0), V(2, 0, 0), 0.5)
        doc.recompute()

        port.Height = 1.5
        doc.recompute()
        tc.check("changing the height rebuilds the face — a port drawn roughly "
                  "is corrected by typing the number",
                  abs(port.Shape.BoundBox.ZMax - 1.5) < 1e-9
                  and abs(port.Shape.Area - 3.0) < 1e-9,
                  f"{port.Shape.BoundBox.ZMax} / {port.Shape.Area}")

        port.Direction = "-Z"
        doc.recompute()
        tc.check("flipping the direction turns it over",
                  abs(port.Shape.BoundBox.ZMin + 1.5) < 1e-9
                  and abs(port.Shape.BoundBox.ZMax) < 1e-9,
                  f"z {port.Shape.BoundBox.ZMin} .. {port.Shape.BoundBox.ZMax}")

        port.EndPoint = V(4, 0, 0)
        doc.recompute()
        tc.check("moving an end point changes the length, and the recorded "
                  "width follows it",
                  abs(port.Width.Value - 4.0) < 1e-9
                  and abs(port.Shape.Area - 6.0) < 1e-9,
                  f"{port.Width} / {port.Shape.Area}")

        port.Height = 0.0
        doc.recompute()
        tc.check("a height of zero leaves the last good face rather than "
                  "replacing it with nothing",
                  port.Shape.Area > 0.0, str(port.Shape.Area))
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_belongs_to_the_chip(tc):
    doc = new_document("PortsChip")
    try:
        layer = doc.addObject("Part::Feature", "Layer_TopMetal2_134")
        layer.Shape = Part.makeBox(2.0, 1.0, 0.003, V(0, 0, 0))
        port = ports.make_port(doc, V(0, 0, 0.003), V(2, 0, 0.003), 0.5)

        trace = doc.addObject("Part::Feature", "Trace_001")
        trace.Shape = Part.makeBox(2.0, 0.2, 0.035, V(0, 0.4, 0))
        trace.addProperty("App::PropertyBool", "IsRoutingTrace", "Routing", "")
        trace.addProperty("App::PropertyLength", "TraceWidth", "Routing", "")
        trace.IsRoutingTrace = True
        trace.TraceWidth = 0.2
        doc.recompute()

        findings = drc.run_drc(doc, min_clearance_mm=1.0, min_trace_width_mm=0.0)
        tc.check("the design rule check does not treat a port as copper — it is "
                  "a surface for a solver, and would otherwise be flagged "
                  "against every trace near it",
                  not any(port.Name in f.object_names for f in findings),
                  f"got {findings}")

        tc.check("a port is not a physical part either, so it stays out of the "
                  "thermal export",
                  port not in materials.physical_parts(doc))

        transform = load_module_from_file("chip_transform_ports_test",
                                          "gds/ChipTransformCommand.py")
        moving = {o.Name for o in transform._gds_objects(doc)}
        tc.check("a port moves with the chip it was drawn on",
                  port.Name in moving, str(sorted(moving)))

        transform._translate_objects([port], 1.0, 0.0, 0.0)
        doc.recompute()
        tc.check("...and after the move it is still where the geometry is",
                  abs(port.Shape.BoundBox.XMin - 1.0) < 1e-9,
                  str(port.Shape.BoundBox.XMin))
    finally:
        FreeCAD.closeDocument(doc.Name)
