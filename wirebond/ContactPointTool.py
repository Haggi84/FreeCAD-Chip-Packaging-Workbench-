"""Interactive definition of contact points on imported leadframe STEP models."""

from typing import List, Optional, Set, Tuple

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
    marker.addProperty(
        "App::PropertyVector",
        "ContactPoint",
        "Wirebond",
        "Defined contact point on the imported STEP model",
    )
    marker.addProperty(
        "App::PropertyString",
        "SourceObject",
        "Wirebond",
        "Name of the source object the point belongs to",
    )
    marker.addProperty(
        "App::PropertyBool",
        "IsContactPoint",
        "Wirebond",
        "Marks this object as a wirebond contact point",
    )

    marker.ContactPoint = point
    marker.SourceObject = source_name
    marker.IsContactPoint = True

    if hasattr(marker, "ViewObject"):
        marker.ViewObject.PointSize = 6
        marker.ViewObject.PointColor = (0.90, 0.30, 0.10)
        marker.ViewObject.DisplayMode = "Point"

    return marker


class ContactPointPlacementSession:
    """Two-stage selection to define contact points on chosen faces."""

    def __init__(self):
        self.doc = None
        self.mode: str = "idle"  # idle | select_surfaces | place_markers
        self.selected_faces: Set[Tuple[str, int]] = set()
        self.created_markers: List[str] = []
        self.surface_prompt = None
        self.placement_prompt = None

    def start(self):
        self.doc = FreeCAD.activeDocument()
        if not self.doc:
            QtWidgets.QMessageBox.warning(
                None,
                "No document",
                "Open a document before defining contact points.",
            )
            return

        self.mode = "select_surfaces"
        self.selected_faces.clear()
        self.created_markers = []

        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addObserver(self)
        FreeCAD.Console.PrintMessage(
            "Select one or more faces where contact points should appear, then click 'Ready to place markers'.\n"
        )
        self._show_surface_prompt()

    def _show_surface_prompt(self):
        box = QtWidgets.QMessageBox()
        box.setWindowTitle("Select faces for contact points")
        box.setText(
            "Step 1: Select the faces where contact points should appear.\n"
            "Use multi-selection if needed, then click 'Ready to place markers'."
        )
        box.setStandardButtons(QtWidgets.QMessageBox.Cancel)
        box.addButton("Ready to place markers", QtWidgets.QMessageBox.AcceptRole)
        box.setModal(False)
        box.finished.connect(self._handle_surface_prompt_finished)
        self.surface_prompt = box
        box.show()

    def _handle_surface_prompt_finished(self, result):
        if result == QtWidgets.QMessageBox.Cancel:
            self.stop()
            return

        if not self.selected_faces:
            QtWidgets.QMessageBox.warning(
                None,
                "No faces selected",
                "Select at least one face on the imported STEP model before placing contact points.",
            )
            return

        self._begin_marker_stage()

    def _begin_marker_stage(self):
        self.mode = "place_markers"
        FreeCADGui.Selection.clearSelection()

        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            view.viewTop()
            if hasattr(view, "setCameraType"):
                view.setCameraType("Orthographic")
        except Exception:
            FreeCAD.Console.PrintWarning("Could not switch to top view; continuing with current camera.\n")

        FreeCAD.Console.PrintMessage(
            "Top view enabled. Click on the selected faces to place contact point markers.\n"
        )
        FreeCAD.Console.PrintMessage("Close the placement panel to finish.\n")
        self._show_placement_prompt()

    def _show_placement_prompt(self):
        box = QtWidgets.QMessageBox()
        box.setWindowTitle("Place contact point markers")
        box.setText(
            "Step 2: Click on the selected faces to create markers.\n"
            "Each click will create a visible, selectable contact point."
        )
        box.setStandardButtons(QtWidgets.QMessageBox.Cancel)
        box.addButton("Finish placement", QtWidgets.QMessageBox.AcceptRole)
        box.setModal(False)
        box.finished.connect(self._handle_placement_prompt_finished)
        self.placement_prompt = box
        box.show()

    def _handle_placement_prompt_finished(self, result):
        if result == QtWidgets.QMessageBox.Cancel:
            self.stop(remove_observer=True)
            return

        self.stop()

    def stop(self, remove_observer: bool = True):
        if remove_observer:
            try:
                FreeCADGui.Selection.removeObserver(self)
            except Exception:
                pass

        self.mode = "idle"
        if self.surface_prompt:
            self.surface_prompt.close()
            self.surface_prompt = None
        if self.placement_prompt:
            self.placement_prompt.close()
            self.placement_prompt = None

        if self.created_markers:
            try:
                self.doc.recompute()
            except Exception:
                pass
            QtWidgets.QMessageBox.information(
                None,
                "Contact points created",
                f"Created {len(self.created_markers)} contact point marker(s).",
            )
        else:
            QtWidgets.QMessageBox.information(None, "Contact point placement", "No contact points were created.")

    # Selection observer callbacks -------------------------------------------------
    def addSelection(self, doc, obj, sub, pos):
        if self.mode == "select_surfaces":
            self._record_face_selection(obj, sub)
            return

        if self.mode == "place_markers":
            self._place_marker(obj, sub, pos)

    def _record_face_selection(self, obj, sub):
        obj = self._as_object(obj)
        if obj is None:
            return

        if not sub or not str(sub).startswith("Face"):
            FreeCAD.Console.PrintWarning("Select a face to designate it for contact points.\n")
            return

        try:
            face_index = int(str(sub).replace("Face", "")) - 1
        except Exception:
            FreeCAD.Console.PrintWarning("Could not identify selected face index.\n")
            return

        key = (obj.Name, face_index)
        if key in self.selected_faces:
            return

        self.selected_faces.add(key)
        FreeCAD.Console.PrintMessage(f"Face {sub} of {obj.Name} added for marker placement.\n")

    def _is_allowed_face(self, obj, sub) -> bool:
        obj = self._as_object(obj)
        if obj is None:
            return False

        if not sub or not str(sub).startswith("Face"):
            return False

        try:
            face_index = int(str(sub).replace("Face", "")) - 1
        except Exception:
            return False

        for stored_name, stored_index in self.selected_faces:
            if stored_name == obj.Name and stored_index == face_index:
                return True
        return False

    def _place_marker(self, obj, sub, pos):
        obj = self._as_object(obj)
        if obj is None:
            return

        if not self._is_allowed_face(obj, sub):
            FreeCAD.Console.PrintWarning("Click on one of the pre-selected faces to place a contact point.\n")
            return

        point = self._point_from_selection(obj, sub, pos)
        if point is None:
            FreeCAD.Console.PrintWarning("Could not derive a 3D point from the click.\n")
            return

        marker = _create_contact_marker(self.doc, obj.Name, point, len(self.created_markers) + 1)
        marker.Label = f"{obj.Label}:{sub}"
        self.created_markers.append(marker.Name)
        try:
            self.doc.recompute()
        except Exception:
            pass
        FreeCAD.Console.PrintMessage(
            f"Contact point {marker.Name} created at ({point.x:.3f}, {point.y:.3f}, {point.z:.3f}).\n"
        )

    def _point_from_selection(self, obj, sub, pos) -> Optional[Base.Vector]:
        if pos is not None:
            try:
                return Base.Vector(pos)
            except Exception:
                pass

        try:
            face_index = int(str(sub).replace("Face", "")) - 1
            if 0 <= face_index < len(obj.Shape.Faces):
                return Base.Vector(obj.Shape.Faces[face_index].CenterOfMass)
        except Exception:
            pass

        try:
            sub_obj = obj.Shape.getElement(sub)
            return _point_from_subobject(sub_obj)
        except Exception:
            return None

    def _as_object(self, obj):
        if obj is None:
            return None
        if isinstance(obj, str) and self.doc:
            return self.doc.getObject(obj)
        return obj


contact_point_session = ContactPointPlacementSession()


def start_contact_point_definition():
    contact_point_session.start()


class DefineContactPointsCommand:
    def GetResources(self):
        return {
            "MenuText": "Define Contact Points",
            "ToolTip": "Interactively create wirebond contact point markers on selected faces",
        }

    def Activated(self):
        start_contact_point_definition()

    def IsActive(self):
        doc = FreeCAD.activeDocument()
        return bool(doc)
