"""
Contact point definition tool.

Workflow
--------
1. Select one or more GDS layer objects (imported via "Load GDSII") in the
   3D view — whole-object selection is sufficient, no sub-element required.
2. Run "Define Contact Points".
3. A ContactPoint marker is placed at the centre of the top face (highest-Z
   face) of each selected object.  This is the bonding surface of the pad.

Sub-element selection (vertex / edge / face) is still honoured when present,
allowing finer placement on STEP models or custom geometry.
"""

import os
import sys
from typing import List, Optional

import FreeCAD
import FreeCADGui
import Part
from FreeCAD import Base
from compat import QtWidgets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from Get_Path import get_icon


# ── snap-point helpers ─────────────────────────────────────────────────────────

def _top_face_center(shape: Part.Shape) -> Base.Vector:
    """
    Return the centre of mass of the highest-Z face of *shape*.
    Falls back to the bounding-box centre when the shape has no faces
    (e.g. a wire or vertex).
    """
    if shape.Faces:
        top_face = max(shape.Faces, key=lambda f: f.CenterOfMass.z)
        return Base.Vector(top_face.CenterOfMass)
    return Base.Vector(shape.BoundBox.Center)


def _point_from_subobject(sub_obj) -> Optional[Base.Vector]:
    """Return a representative point for a selected sub-element."""
    try:
        if hasattr(sub_obj, "Point"):
            return Base.Vector(sub_obj.Point)
        if hasattr(sub_obj, "CenterOfMass"):
            return Base.Vector(sub_obj.CenterOfMass)
        if hasattr(sub_obj, "Curve") and hasattr(sub_obj.Curve, "value"):
            return Base.Vector(sub_obj.Curve.value(0.5))
    except Exception:
        pass
    return None


# ── marker creation ────────────────────────────────────────────────────────────

def _next_marker_index(doc) -> int:
    """Return the next free ContactPoint index in the document."""
    existing = [o for o in doc.Objects if o.Name.startswith("ContactPoint_")]
    return len(existing) + 1


def _create_contact_marker(doc, source_name: str, point: Base.Vector, index: int):
    """Create a ContactPoint marker at *point* and return it."""
    marker = doc.addObject("Part::Feature", f"ContactPoint_{index:03d}")
    marker.Shape = Part.Vertex(point.x, point.y, point.z)

    marker.addProperty("App::PropertyVector", "ContactPoint", "Wirebond",
                        "Snap point for wire bonding")
    marker.addProperty("App::PropertyString", "SourceObject", "Wirebond",
                        "Name of the source object this point belongs to")
    marker.addProperty("App::PropertyBool",   "IsContactPoint", "Wirebond",
                        "Marks this object as a wire-bond contact point")

    marker.ContactPoint   = point
    marker.SourceObject   = source_name
    marker.IsContactPoint = True

    marker.ViewObject.PointSize   = 8
    marker.ViewObject.PointColor  = (0.90, 0.30, 0.10)   # orange — die-side
    marker.ViewObject.DisplayMode = "Points"

    return marker


# ── main function ──────────────────────────────────────────────────────────────

def define_contact_points() -> List[str]:
    """
    Place ContactPoint markers on all currently selected GDS layer objects.

    For each selected object:
    - If a sub-element (vertex / edge / face) was selected, use its position.
    - Otherwise, snap to the centre of the top face (highest-Z face) of the
      object's solid shape — i.e. the pad bonding surface.

    Returns a list of created marker names.
    """
    doc = FreeCAD.activeDocument()
    if not doc:
        QtWidgets.QMessageBox.warning(
            None, "No document",
            "Open a document and select GDS layer objects before defining contact points.",
        )
        return []

    selection = FreeCADGui.Selection.getSelectionEx()
    if not selection:
        QtWidgets.QMessageBox.information(
            None, "Nothing selected",
            "Select one or more GDS layer objects in the 3D view,\n"
            "then run 'Define Contact Points' again.\n\n"
            "A contact point will be placed at the centre of each pad's top face.",
        )
        return []

    created: List[str] = []

    for sel in selection:
        obj = sel.Object
        if not hasattr(obj, "Shape"):
            FreeCAD.Console.PrintWarning(
                f"Skipping '{obj.Name}': no Shape property.\n"
            )
            continue

        sub_objects = sel.SubObjects or []

        if sub_objects:
            # Sub-element selected — honour the explicit pick
            for idx, sub_obj in enumerate(sub_objects):
                pt = _point_from_subobject(sub_obj)
                if pt is None:
                    pt = _top_face_center(obj.Shape)
                marker = _create_contact_marker(
                    doc, obj.Name, pt, _next_marker_index(doc)
                )
                sub_names = sel.SubElementNames or []
                if idx < len(sub_names):
                    marker.Label = f"{obj.Label}:{sub_names[idx]}"
                created.append(marker.Name)
        else:
            # Whole-object selection — snap to top face centre
            pt = _top_face_center(obj.Shape)
            marker = _create_contact_marker(
                doc, obj.Name, pt, _next_marker_index(doc)
            )
            marker.Label = f"CP_{obj.Label}"
            created.append(marker.Name)

    if created:
        doc.recompute()
        QtWidgets.QMessageBox.information(
            None, "Contact points created",
            f"Created {len(created)} contact point(s).\n"
            "They are ready for use in manual wire bonding.",
        )
    else:
        QtWidgets.QMessageBox.warning(
            None, "No contact points created",
            "None of the selected objects had a usable Shape.\n"
            "Select GDS layer objects (Layer_… objects) and try again.",
        )

    return created


# ── FreeCAD command ────────────────────────────────────────────────────────────

class DefineContactPointsCommand:
    def GetResources(self):
        return {
            "MenuText": "Define Contact Points",
            "ToolTip":  (
                "Select GDS layer objects in the 3D view and run this command to place "
                "a contact point at the centre of each pad's top face."
            ),
            "Pixmap": get_icon("Define_Contact_Points.svg"),
        }

    def Activated(self):
        define_contact_points()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None
