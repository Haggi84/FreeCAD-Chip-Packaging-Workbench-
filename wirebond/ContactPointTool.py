"""Utilities for defining contact points on imported leadframe STEP models."""

from typing import List, Optional

import FreeCAD
import FreeCADGui
import Part
from FreeCAD import Base
from PySide2 import QtWidgets


def _point_from_subobject(sub_obj) -> Optional[Base.Vector]:
    """Return a representative point for the given sub-object."""
    try:
        if hasattr(sub_obj, "Point"):
            return Base.Vector(sub_obj.Point)
        if hasattr(sub_obj, "CenterOfMass"):
            return Base.Vector(sub_obj.CenterOfMass)
        if hasattr(sub_obj, "Curve") and hasattr(sub_obj.Curve, "value"):
            return Base.Vector(sub_obj.Curve.value(0.5))
    except Exception:
        return None
    return None


def _create_contact_marker(doc, source_name: str, point: Base.Vector, index: int):
    marker = doc.addObject("Part::Feature", f"ContactPoint_{index:03d}")
    marker.Shape = Part.Point(point)
    marker.addProperty("App::PropertyVector", "ContactPoint", "Wirebond", "Defined contact point on the imported STEP model")
    marker.addProperty("App::PropertyString", "SourceObject", "Wirebond", "Name of the source object the point belongs to")
    marker.addProperty("App::PropertyBool", "IsContactPoint", "Wirebond", "Marks this object as a wirebond contact point")

    marker.ContactPoint = point
    marker.SourceObject = source_name
    marker.IsContactPoint = True

    if hasattr(marker, "ViewObject"):
        marker.ViewObject.PointSize = 6
        marker.ViewObject.PointColor = (0.90, 0.30, 0.10)
        marker.ViewObject.DisplayMode = "Point"

    return marker


def define_contact_points() -> List[str]:
    """Create explicit contact point markers from the current selection.

    The user should pre-select vertices, edges, or faces on the imported
    STEP/leadframe model. Each selected sub-element becomes a marker with
    a stored position for later wirebonding.
    """

    doc = FreeCAD.activeDocument()
    if not doc:
        QtWidgets.QMessageBox.warning(
            None,
            "No document",
            "Open a document and select geometry on the imported STEP model before defining contact points.",
        )
        return []

    selection = FreeCADGui.Selection.getSelectionEx()
    if not selection:
        QtWidgets.QMessageBox.information(
            None,
            "Select geometry",
            "Select vertices, edges, or faces on the imported STEP model, then run the command again.",
        )
        return []

    created_markers: List[str] = []

    for sel in selection:
        obj = sel.Object
        if not hasattr(obj, "Shape"):
            continue

        sub_objects = sel.SubObjects or []
        sub_names = sel.SubElementNames or []

        if not sub_objects:
            # Fallback to the object's bounding box center if no subelement was provided.
            point = obj.Shape.BoundBox.Center
            marker = _create_contact_marker(doc, obj.Name, point, len(created_markers) + 1)
            created_markers.append(marker.Name)
            continue

        for idx, sub_obj in enumerate(sub_objects):
            point = _point_from_subobject(sub_obj)
            if point is None:
                point = obj.Shape.BoundBox.Center
            marker = _create_contact_marker(doc, obj.Name, point, len(created_markers) + 1)
            if idx < len(sub_names):
                marker.Label = f"{obj.Label}:{sub_names[idx]}"
            created_markers.append(marker.Name)

    if created_markers:
        doc.recompute()
        QtWidgets.QMessageBox.information(
            None,
            "Contact points created",
            f"Created {len(created_markers)} contact point marker(s). They can be used directly in manual wire bonding.",
        )

    return created_markers


class DefineContactPointsCommand:
    def GetResources(self):
        return {
            "MenuText": "Define Contact Points",
            "ToolTip": "Create wirebond contact point markers from selected STEP geometry",
        }

    def Activated(self):
        define_contact_points()

    def IsActive(self):
        doc = FreeCAD.activeDocument()
        return bool(doc and FreeCADGui.Selection.getSelection())
