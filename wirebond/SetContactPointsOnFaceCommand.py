"""
Interactive contact-point placement on any face of any object.

Workflow
--------
1. Activate "Set Contact Points on Face" from the toolbar/menu.
2. Click any face on any solid object in the 3D view.
3. A contact point marker appears at the exact clicked position.
4. The Contact Point Browser is updated automatically after each placement.
5. Continue clicking to add more points; click "Finish" (OK) to close.
6. "Undo last point" removes the most recently placed marker.
"""

import os
import sys

import FreeCAD
import FreeCADGui
from PySide2 import QtWidgets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from Get_Path import get_icon
from wirebond.ContactPointTool import _create_contact_marker, _next_marker_index


# ── Contact Point Browser refresh ─────────────────────────────────────────────

def _refresh_contact_panel():
    """Find the Contact Point Browser dock and call populate() if visible."""
    try:
        mw = FreeCADGui.getMainWindow()
        for w in mw.findChildren(QtWidgets.QDockWidget):
            if w.objectName() == "ContactPointPanel":
                inner = w.widget()
                if inner is not None and hasattr(inner, "populate"):
                    inner.populate()
                    return
                # Some implementations attach populate() directly to the dock
                if hasattr(w, "populate"):
                    w.populate()
                    return
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"SetContactPoints: could not refresh browser: {exc}\n"
        )


# ── task panel ─────────────────────────────────────────────────────────────────

class _InteractivePlacePanel:
    """
    FreeCAD task-panel for interactive contact-point placement.
    No pre-selection required — just activate and click any face.
    """

    def __init__(self):
        self._placed = []          # names of markers placed this session
        self._view   = FreeCADGui.activeDocument().activeView()
        self._cb     = self._view.addEventCallback(
            "SoMouseButtonEvent", self._on_mouse
        )
        self._build_form()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_form(self):
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)

        hint = QtWidgets.QLabel(
            "Click <b>any face</b> on any object to place a contact point\n"
            "at the exact clicked position.\n\n"
            "Press <b>Finish</b> (OK) when done."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        layout.addWidget(sep)

        self._status = QtWidgets.QLabel("0 contact points placed.")
        layout.addWidget(self._status)

        undo_btn = QtWidgets.QPushButton("Undo last point")
        undo_btn.clicked.connect(self._undo_last)
        layout.addWidget(undo_btn)
        layout.addStretch()

    def getStandardButtons(self):
        # OK = "Finish"
        return int(QtWidgets.QDialogButtonBox.Ok)

    def accept(self):   # Finish / OK
        self._cleanup()

    def reject(self):   # safety (Esc)
        self._cleanup()

    # ── click handling ─────────────────────────────────────────────────────────

    def _on_mouse(self, info):
        if info.get("State") != "UP" or info.get("Button") != "BUTTON1":
            return

        x, y = info["Position"]
        hit  = self._view.getObjectInfo((x, y))
        if not hit:
            return

        obj_name = hit.get("Object")
        if not obj_name:
            return

        doc = FreeCAD.activeDocument()
        if not doc:
            return

        obj = doc.getObject(obj_name)
        if obj is None or not hasattr(obj, "Shape"):
            return

        pos = FreeCAD.Vector(float(hit["x"]), float(hit["y"]), float(hit["z"]))
        self._place_marker(obj, pos)

    def _place_marker(self, obj, pos: FreeCAD.Vector):
        doc = FreeCAD.activeDocument()
        if not doc:
            return

        idx    = _next_marker_index(doc)
        marker = _create_contact_marker(doc, obj.Name, pos, idx)
        doc.recompute()

        self._placed.append(marker.Name)
        self._status.setText(f"{len(self._placed)} contact point(s) placed.")
        _refresh_contact_panel()

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
        self._status.setText(f"{len(self._placed)} contact point(s) placed.")
        _refresh_contact_panel()

    # ── cleanup ───────────────────────────────────────────────────────────────

    def _cleanup(self):
        try:
            self._view.removeEventCallback("SoMouseButtonEvent", self._cb)
        except Exception:
            pass


# ── FreeCAD command ────────────────────────────────────────────────────────────

class SetContactPointsOnFaceCommand:
    def GetResources(self):
        return {
            "MenuText": "Set Contact Points on Face",
            "ToolTip": (
                "Activate, then click any face on any object to place contact "
                "points at the exact clicked positions. Press Finish when done."
            ),
            "Pixmap": get_icon("Set_Contact_Points.svg"),
        }

    def Activated(self):
        # Close any already-open task dialog first
        if FreeCADGui.Control.activeDialog():
            FreeCADGui.Control.closeDialog()
        FreeCADGui.Control.showDialog(_InteractivePlacePanel())

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand("SetContactPointsOnFaceCommand", SetContactPointsOnFaceCommand())
