# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Interactive (hover + click) contact-point placement.

Complements the grid-based SetContactPointsOnFaceCommand: instead of
generating a regular grid and picking from it, hover the mouse over any
face — a live cyan preview marker follows the cursor, snapped exactly to
the face surface at the picked point — and click to place a permanent
contact point right there. Repeat for as many points as needed; press
Close (or Esc) when done.

Classification (die-side orange "ContactPoint_NNN" vs package-side yellow
"contact_point_housing_NNN") and marker creation reuse the exact same
helpers as SetContactPointsOnFaceCommand.py, so both tools produce
identical, interchangeable markers.
"""

import FreeCAD
import FreeCADGui
import Part
from compat import QtWidgets, QtCore, qenum_int

from Get_Path import get_icon
from wirebond.SetContactPointsOnFaceCommand import (
    _get_source_assembly,
    _create_gds_marker,
    _create_housing_marker,
    _add_to_contact_points_group,
    _next_gds_index,
    _next_housing_index,
    _refresh_contact_panel,
)

_PREVIEW_NAME  = "_ContactPointPreview"
_COLOR_PREVIEW = (0.20, 0.80, 0.90)   # cyan — not yet placed


class _FaceOnlyGate:
    """SelectionGate allowing only Face sub-elements. Both the hover preview
    and the actual click are restricted to faces — "hover over a face" is
    the whole point of this tool. A Vertex-only shape (the preview marker
    itself) has no Face sub-element, so it's naturally excluded without
    needing an explicit by-name check."""

    def allow(self, doc, obj, sub) -> bool:
        return bool(sub) and sub.startswith("Face")


class _InteractivePanel:
    """
    Task panel hosting the hover-to-place session. All placement state (the
    live preview marker, the selection gate/observer) lives here so cleanup
    on Close/Escape is a single, reliable code path.
    """

    def __init__(self):
        self._active  = True
        self._count   = 0
        self._doc     = FreeCAD.activeDocument()
        self._preview = None

        FreeCADGui.Selection.addObserver(self)
        FreeCADGui.Selection.addSelectionGate(_FaceOnlyGate())
        self._ensure_preview()

        self._build_form()

    # ── form ──────────────────────────────────────────────────────────────

    def _build_form(self):
        self.form = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(self.form)
        root.setSpacing(6)

        hint = QtWidgets.QLabel(
            "<b>Interactive Contact Point</b><br>"
            "Hover over any face — a cyan marker snaps to your cursor.<br>"
            "Click to place a contact point right there. Repeat as needed.<br>"
            "Press <b>Close</b> (or Esc) when done."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        root.addWidget(sep)

        self._status = QtWidgets.QLabel()
        root.addWidget(self._status)
        root.addStretch()
        self._update_status()

    def _update_status(self):
        self._status.setText(f"{self._count} contact point(s) placed this session.")

    # ── SelectionObserver callbacks ──────────────────────────────────────────

    def setPreselection(self, doc, obj_name, sub):
        if not self._active or not sub.startswith("Face"):
            return
        try:
            pre = FreeCADGui.Selection.getPreselection()
            pt  = FreeCAD.Vector(*pre.PickedPoint)
        except Exception:
            return
        self._show_preview(pt)

    def removePreselection(self, doc, obj_name, sub):
        if not self._active:
            return
        self._hide_preview()

    def addSelection(self, doc, obj_name, sub, pnt):
        if not self._active or not sub.startswith("Face"):
            return
        try:
            if pnt and len(pnt) == 3:
                point = FreeCAD.Vector(pnt[0], pnt[1], pnt[2])
            else:
                FreeCAD.Console.PrintWarning(
                    "[InteractiveCP] No 3-D pick point — click a face "
                    "directly in the 3D view.\n"
                )
                return

            d = self._doc or FreeCAD.activeDocument()
            if d is None:
                return

            assembly = _get_source_assembly(d, obj_name)
            if assembly == "gds":
                marker = _create_gds_marker(d, obj_name, point, _next_gds_index(d))
            else:
                marker = _create_housing_marker(d, obj_name, point, _next_housing_index(d))
            _add_to_contact_points_group(d, marker)
            d.recompute()

            self._count += 1
            self._update_status()
            _refresh_contact_panel()
            FreeCAD.Console.PrintMessage(
                f"[InteractiveCP] Placed {marker.Name} on '{obj_name}' "
                f"at ({point.x:.3f}, {point.y:.3f}, {point.z:.3f})\n"
            )
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[InteractiveCP] placement failed: {exc}\n")
        finally:
            # Defer clearing so FreeCAD's own picker/highlight finishes
            # first — matches the pattern used by the grid tool's
            # _PanelObserver.addSelection (SetContactPointsOnFaceCommand.py).
            QtCore.QTimer.singleShot(0, FreeCADGui.Selection.clearSelection)

    def setSelection(self, doc):
        pass

    def clearSelection(self, doc):
        pass

    # ── live preview marker ──────────────────────────────────────────────────
    #
    # One persistent Vertex object, reused for the whole session — only
    # repositioned/shown/hidden on hover, never recreated per mouse-move and
    # never recomputed (doc.recompute() walks the whole dependency graph and
    # would be a severe hot-path cost here; FreeCADGui.updateGui() is the
    # lightweight viewport refresh already used elsewhere in this codebase
    # for the same reason, e.g. ManualWireBonding's snap marker).

    def _ensure_preview(self):
        doc = self._doc
        if doc is None or self._preview is not None:
            return
        try:
            m = doc.addObject("Part::Feature", _PREVIEW_NAME)
            m.Shape = Part.Vertex(0, 0, 0)
            m.ViewObject.PointSize   = 12
            m.ViewObject.PointColor  = _COLOR_PREVIEW
            m.ViewObject.DisplayMode = "Points"
            m.ViewObject.Visibility  = False
            m.addProperty(
                "App::PropertyBool", "IsUiPreview", "Wirebond",
                "Transient placement-preview marker, not part of the design",
            )
            m.IsUiPreview = True
            self._preview = m
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[InteractiveCP] preview create: {exc}\n")

    def _show_preview(self, point):
        if self._preview is None:
            self._ensure_preview()
        if self._preview is None:
            return
        try:
            self._preview.Shape = Part.Vertex(point.x, point.y, point.z)
            self._preview.ViewObject.Visibility = True
            FreeCADGui.updateGui()
        except Exception:
            pass

    def _hide_preview(self):
        if self._preview is None:
            return
        try:
            self._preview.ViewObject.Visibility = False
            FreeCADGui.updateGui()
        except Exception:
            pass

    def _remove_preview(self):
        if self._preview is None:
            return
        try:
            doc = self._doc or FreeCAD.activeDocument()
            if doc is not None and doc.getObject(self._preview.Name) is not None:
                doc.removeObject(self._preview.Name)
        except Exception:
            pass
        self._preview = None

    # ── close (Close button + Escape both route here) ───────────────────────

    def getStandardButtons(self):
        return qenum_int(QtWidgets.QDialogButtonBox.Close)

    def accept(self):
        self._cleanup()
        QtCore.QTimer.singleShot(0, FreeCADGui.Control.closeDialog)

    def reject(self):
        self._cleanup()
        QtCore.QTimer.singleShot(0, FreeCADGui.Control.closeDialog)

    def _cleanup(self):
        if not self._active:
            return
        self._active = False
        self._remove_preview()
        try:
            FreeCADGui.Selection.removeSelectionGate()
        except Exception:
            pass
        try:
            FreeCADGui.Selection.removeObserver(self)
        except Exception:
            pass
        doc = self._doc or FreeCAD.activeDocument()
        if doc is not None:
            doc.recompute()
        FreeCAD.Console.PrintMessage(
            f"[InteractiveCP] Session ended — {self._count} contact point(s) placed.\n"
        )


# ── FreeCAD command ────────────────────────────────────────────────────────────

class InteractiveContactPointCommand:
    def GetResources(self):
        return {
            "MenuText": "Interactive Contact Point",
            "ToolTip": (
                "Hover over a face to preview a snap point, click to place a\n"
                "contact point right there. Repeat for as many points as needed."
            ),
            "Pixmap": get_icon("Interactive_Contact_Point.svg"),
        }

    def Activated(self):
        if FreeCADGui.Control.activeDialog():
            QtWidgets.QMessageBox.information(
                None,
                "Task panel already open",
                "Please close the current task panel before placing contact points.",
            )
            return
        FreeCADGui.Control.showDialog(_InteractivePanel())

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand(
        "InteractiveContactPointCommand", InteractiveContactPointCommand()
    )
