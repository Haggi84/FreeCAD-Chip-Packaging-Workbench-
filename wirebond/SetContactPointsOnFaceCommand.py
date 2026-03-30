"""
Interactive contact-point placement on the top face of an imported package.

Workflow
--------
1. Select the imported package object (e.g. a library STEP body) in the 3D view.
2. Activate "Set Contact Points on Face".
3. The top surface is highlighted in yellow.
4. Click anywhere on that surface; a contact point marker appears at the exact
   clicked position.  Keep clicking to add more, then close the panel.
"""

import os
import sys

import FreeCAD
import FreeCADGui
import Part
from PySide2 import QtWidgets, QtCore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from Get_Path import get_icon
from wirebond.ContactPointTool import _create_contact_marker, _next_marker_index

# ── constants ──────────────────────────────────────────────────────────────────

_UP_THRESH = 0.9          # minimum face-normal Z component to be "upward"
_TOP_Z_TOL = 1.0          # mm tolerance: clicks further from top_z are rejected
_COLOR_HIGHLIGHT = (1.0, 1.0, 0.0, 1.0)   # yellow — top-face highlight
_COLOR_CONTACT   = (0.10, 0.80, 0.30)     # green — package contact marker


# ── geometry helpers ───────────────────────────────────────────────────────────

def _face_normal(face) -> float:
    """Return the Z component of the face's outward normal, or 0 on failure."""
    for uv in ((0.0, 0.0), (0.5, 0.5)):
        try:
            return face.normalAt(*uv).z
        except Exception:
            continue
    return 0.0


def _find_top_z(shape: Part.Shape):
    """
    Return the Z height of the highest upward-facing horizontal face, or None
    if no such face exists.
    """
    best = None
    for face in shape.Faces:
        if _face_normal(face) >= _UP_THRESH:
            z = face.CenterOfMass.z
            if best is None or z > best:
                best = z
    return best


def _top_face_indices(shape: Part.Shape, top_z: float, tol: float = 0.5):
    """Return face indices of every upward-facing face near *top_z*."""
    result = []
    for i, face in enumerate(shape.Faces):
        if _face_normal(face) >= _UP_THRESH and abs(face.CenterOfMass.z - top_z) <= tol:
            result.append(i)
    return result


# ── task panel ─────────────────────────────────────────────────────────────────

class _ContactPointsPanel:
    """
    FreeCAD task-panel that intercepts 3D-view clicks and places contact
    point markers on the top face of the target object.
    """

    def __init__(self, target_obj, top_z: float, face_indices: list):
        self._target   = target_obj
        self._top_z    = top_z
        self._indices  = set(face_indices)
        self._placed   = []          # names of markers placed this session
        self._orig_dc  = None        # saved DiffuseColor for restore

        self._view = FreeCADGui.activeDocument().activeView()
        self._cb   = self._view.addEventCallback(
            "SoMouseButtonEvent", self._on_mouse
        )

        self._highlight()
        self._build_form()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_form(self):
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)

        layout.addWidget(QtWidgets.QLabel(f"<b>Object:</b> {self._target.Label}"))
        layout.addWidget(QtWidgets.QLabel(
            f"<b>Top surface Z:</b> {self._top_z:.4f} mm"
        ))

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        layout.addWidget(sep)

        hint = QtWidgets.QLabel(
            "The <b>top surface</b> is highlighted in yellow.\n"
            "Click anywhere on it to place a contact point."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)
        self._status = QtWidgets.QLabel("0 contact points placed.")
        layout.addWidget(self._status)

        undo_btn = QtWidgets.QPushButton("Undo last point")
        undo_btn.clicked.connect(self._undo_last)
        layout.addWidget(undo_btn)
        layout.addStretch()

    def getStandardButtons(self):
        return int(QtWidgets.QDialogButtonBox.Close)

    def reject(self):   # Close button
        self._cleanup()

    def accept(self):   # safety
        self._cleanup()

    # ── face highlighting ──────────────────────────────────────────────────────

    def _highlight(self):
        try:
            vp = FreeCADGui.activeDocument().getObject(self._target.Name)
            n  = len(self._target.Shape.Faces)
            if n == 0:
                return

            dc = list(vp.DiffuseColor) if hasattr(vp, "DiffuseColor") else []
            self._orig_dc = list(dc)     # save for restore

            # Expand single-colour entry to per-face list
            if len(dc) != n:
                base = dc[0] if dc else (0.80, 0.80, 0.80, 1.0)
                dc = [base] * n

            for i in self._indices:
                if i < n:
                    dc[i] = _COLOR_HIGHLIGHT

            vp.DiffuseColor = dc
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"SetContactPoints: could not highlight faces: {exc}\n"
            )

    def _restore_highlight(self):
        try:
            vp = FreeCADGui.activeDocument().getObject(self._target.Name)
            if self._orig_dc is not None and hasattr(vp, "DiffuseColor"):
                vp.DiffuseColor = self._orig_dc
        except Exception:
            pass

    # ── click handling ─────────────────────────────────────────────────────────

    def _on_mouse(self, info):
        if info.get("State") != "UP" or info.get("Button") != "BUTTON1":
            return

        x, y = info["Position"]
        hit = self._view.getObjectInfo((x, y))
        if not hit:
            return

        # Only accept clicks on the target object
        if hit.get("Object") != self._target.Name:
            return

        # Reject clicks that are too far from the top surface in Z
        click_z = hit.get("z", 0.0)
        if abs(click_z - self._top_z) > _TOP_Z_TOL:
            return

        # Snap Z to the exact top-face height
        pos = FreeCAD.Vector(hit["x"], hit["y"], self._top_z)
        self._place_marker(pos)

    def _place_marker(self, pos: FreeCAD.Vector):
        doc = FreeCAD.activeDocument()
        if not doc:
            return

        idx    = _next_marker_index(doc)
        marker = _create_contact_marker(doc, self._target.Name, pos, idx)

        # Override with the package-contact colour (green)
        try:
            marker.ViewObject.PointColor = _COLOR_CONTACT
        except Exception:
            pass

        doc.recompute()
        self._placed.append(marker.Name)
        n = len(self._placed)
        self._status.setText(f"{n} contact point(s) placed.")

    def _undo_last(self):
        if not self._placed:
            return
        doc = FreeCAD.activeDocument()
        if not doc:
            return
        name = self._placed.pop()
        try:
            doc.removeObject(name)
            doc.recompute()
        except Exception:
            pass
        n = len(self._placed)
        self._status.setText(f"{n} contact point(s) placed.")

    # ── cleanup ───────────────────────────────────────────────────────────────

    def _cleanup(self):
        try:
            self._view.removeEventCallback("SoMouseButtonEvent", self._cb)
        except Exception:
            pass
        self._restore_highlight()


# ── FreeCAD command ────────────────────────────────────────────────────────────

class SetContactPointsOnFaceCommand:
    def GetResources(self):
        return {
            "MenuText": "Set Contact Points on Face",
            "ToolTip": (
                "Select an imported package body, then click its top surface "
                "to place contact points at the exact clicked positions."
            ),
            "Pixmap": get_icon("Set_Contact_Points.svg"),
        }

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            QtWidgets.QMessageBox.information(
                None,
                "No selection",
                "Select the imported package object in the 3D view first.",
            )
            return

        target = sel[0]
        if not hasattr(target, "Shape"):
            QtWidgets.QMessageBox.warning(
                None,
                "Invalid selection",
                "The selected object has no shape.\n"
                "Select a solid body (e.g. a library-imported STEP file).",
            )
            return

        top_z = _find_top_z(target.Shape)
        if top_z is None:
            QtWidgets.QMessageBox.warning(
                None,
                "No top face found",
                "Could not find an upward-facing horizontal face on the selected object.\n"
                "Make sure the package is oriented with its top surface facing up (+Z).",
            )
            return

        indices = _top_face_indices(target.Shape, top_z)
        panel   = _ContactPointsPanel(target, top_z, indices)
        FreeCADGui.Control.showDialog(panel)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand("SetContactPointsOnFaceCommand", SetContactPointsOnFaceCommand())
