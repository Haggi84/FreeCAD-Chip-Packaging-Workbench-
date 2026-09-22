# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Ports on an imported layout: a rectangle with no volume, spanning from an
edge of the geometry.

A port is where a simulation feeds the structure. It is a surface, not a
body: a rectangle standing on a piece of an edge — two points on that edge
give its length — and reaching up or down in Z by its height. Give it
thickness and it stops being a port.

The rectangle is always flat, whatever the edge does: two points and the Z
direction span a plane, so even an edge that climbs in Z gives a planar port.
A curved edge is spanned by the straight chord between the two points, which
is what a port is meant to be.

Ports are parametric. The length, direction and height stay editable in the
property editor afterwards and the face is rebuilt from them — a port drawn
roughly by hand is meant to be corrected by typing the number, without being
deleted and drawn again.

They belong to the chip: they live in the imported layout's own group, they
are saved in the document with it, and they move with it.

Qt-free, so it is testable headlessly.
"""

import FreeCAD
import Part

IS_PORT = "IsPort"
GROUP_NAME = "GDS_Ports"
# The group the GDS import puts its layers in; ports hang underneath it so
# they are part of the imported chip rather than loose in the document.
_GDS_GROUP_NAMES = ("GDS_Die",)

# Below this a port would be a line or a sliver, not a surface.
MIN_EXTENT_MM = 1e-6

DIRECTIONS = ("+Z", "-Z")


def _vector(value):
    return FreeCAD.Vector(value) if value is not None else None


def signed_height(height_mm, direction):
    """The height as a signed Z offset: the property pair (height, direction)
    reads better than a signed number in the property editor."""
    magnitude = abs(float(height_mm))
    return -magnitude if str(direction) == "-Z" else magnitude


def port_face(start, end, height_mm, direction="+Z"):
    """
    The rectangle spanned by the segment *start*–*end* and *height_mm* in Z.

    Raises ValueError when it would be degenerate — a port of no length or no
    height is a line, and a line is not a port.
    """
    start, end = _vector(start), _vector(end)
    length = (end - start).Length
    offset = signed_height(height_mm, direction)
    if length < MIN_EXTENT_MM:
        raise ValueError("A port needs two different points on the edge: its "
                         f"length is {length:.6f} mm.")
    if abs(offset) < MIN_EXTENT_MM:
        raise ValueError("A port needs a height: it is a surface, not a line.")
    rise = FreeCAD.Vector(0.0, 0.0, offset)
    corners = [start, end, end + rise, start + rise, start]
    return Part.Face(Part.makePolygon(corners))


def snap_to_edge(edge, point):
    """The point on *edge* nearest *point* — what a click on an edge means."""
    point = _vector(point)
    try:
        parameter = edge.Curve.parameter(point)
        first, last = sorted((edge.FirstParameter, edge.LastParameter))
        parameter = min(max(parameter, first), last)
        return FreeCAD.Vector(edge.valueAt(parameter))
    except Exception:
        vertex = Part.Vertex(point)
        try:
            return FreeCAD.Vector(edge.distToShape(vertex)[1][0][0])
        except Exception:
            return point


# ── the parametric port ──────────────────────────────────────────────────────

class PortFeature:
    """
    The recipe a port rebuilds itself from.

    Kept as a proxy on a Part::FeaturePython so the port stays editable:
    changing its height or flipping its direction in the property editor
    rebuilds the face, and the document carries the numbers it was made from
    rather than only the resulting surface.
    """

    def __init__(self, obj=None):
        if obj is not None:
            self.attach_properties(obj)
            obj.Proxy = self

    @staticmethod
    def attach_properties(obj):
        definitions = (
            ("App::PropertyBool", IS_PORT, "Port",
             "A simulation port: a surface, never a solid"),
            ("App::PropertyInteger", "PortNumber", "Port", "Port number"),
            ("App::PropertyVector", "StartPoint", "Port",
             "First point on the edge the port stands on"),
            ("App::PropertyVector", "EndPoint", "Port",
             "Second point on the edge; the two give the port its length"),
            ("App::PropertyEnumeration", "Direction", "Port",
             "Whether the port reaches up or down from the edge"),
            ("App::PropertyLength", "Height", "Port", "How far it reaches"),
            ("App::PropertyLength", "Width", "Port",
             "Length along the edge, from the two points (read only)"),
            ("App::PropertyFloat", "Impedance", "Port",
             "Reference impedance in ohms — carried for the solver, not used "
             "by the geometry"),
            ("App::PropertyString", "SourceObject", "Port",
             "The object whose edge the port was drawn on"),
            ("App::PropertyString", "SourceSubElement", "Port",
             "That object's edge, as picked (for example Edge12)"),
        )
        for ptype, name, group, doc_string in definitions:
            if not hasattr(obj, name):
                obj.addProperty(ptype, name, group, doc_string)
        if not obj.Direction:
            obj.Direction = list(DIRECTIONS)
        obj.setEditorMode("Width", 1)          # read-only: it follows the points
        return obj

    def execute(self, obj):
        try:
            face = port_face(obj.StartPoint, obj.EndPoint, obj.Height.Value,
                             obj.Direction)
        except ValueError as exc:
            FreeCAD.Console.PrintWarning(f"[Port] {obj.Name}: {exc}\n")
            return
        obj.Shape = face
        obj.Width = (FreeCAD.Vector(obj.EndPoint) - FreeCAD.Vector(obj.StartPoint)).Length

    # FreeCAD stores the proxy with the document; it carries no state of its
    # own, but both protocols have to answer or the object fails to restore.
    def dumps(self):
        return None

    def loads(self, _state):
        return None

    def __getstate__(self):
        return None

    def __setstate__(self, _state):
        return None


def ports_group(doc):
    """The group ports live in, inside the imported chip's own group when
    there is one."""
    group = doc.getObject(GROUP_NAME)
    if group is None:
        group = doc.addObject("App::DocumentObjectGroup", GROUP_NAME)
        group.Label = "Ports"
        parent = next((o for o in doc.Objects
                       if o.Name in _GDS_GROUP_NAMES or o.Label in _GDS_GROUP_NAMES),
                      None)
        if parent is not None:
            parent.addObject(group)
    return group


def ports_of(doc):
    return [o for o in (doc.Objects if doc else []) if getattr(o, IS_PORT, False)]


def next_number(doc):
    used = [int(getattr(port, "PortNumber", 0) or 0) for port in ports_of(doc)]
    return max(used, default=0) + 1


def make_port(doc, start, end, height_mm, direction="+Z", source=("", ""),
              impedance=50.0, number=None):
    """
    Build a port and put it with the chip. Returns the new object.

    The face is validated before anything is added, so a degenerate drag
    leaves no half-made object behind.
    """
    port_face(start, end, height_mm, direction)      # raises before we build

    number = next_number(doc) if number is None else int(number)
    obj = doc.addObject("Part::FeaturePython", f"Port_{number:03d}")
    PortFeature(obj)
    obj.Label = f"Port {number}"
    obj.PortNumber = number
    obj.StartPoint = _vector(start)
    obj.EndPoint = _vector(end)
    obj.Direction = str(direction)
    obj.Height = abs(float(height_mm))
    obj.Impedance = float(impedance)
    obj.SourceObject, obj.SourceSubElement = (source or ("", ""))[:2]
    setattr(obj, IS_PORT, True)
    obj.Proxy.execute(obj)

    if FreeCAD.GuiUp and getattr(obj, "ViewObject", None) is not None:
        obj.ViewObject.Proxy = 0
        obj.ViewObject.ShapeColor = (1.0, 0.35, 0.35)
        obj.ViewObject.Transparency = 40
        obj.ViewObject.DisplayMode = "Flat Lines"

    ports_group(doc).addObject(obj)
    FreeCAD.Console.PrintMessage(
        f"[Port] {obj.Label}: {obj.Width.Value:.4f} mm along the edge, "
        f"{obj.Height.Value:.4f} mm {obj.Direction}\n")
    return obj
