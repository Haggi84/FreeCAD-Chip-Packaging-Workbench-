# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
PinNumberingCommand
====================
Assign datasheet pin numbers to package leads and display them as small 3-D
labels — without any PDF parsing or AI.

Two modes, chosen automatically based on what the current document contains
--------------------------------------------------------------------------
Auto (perimeter walk) — available when the package was built with the
  Leadframe Configurator, which tags every lead/BGA-ball object with
  ``IsLeadFinger``.  Virtually every QFN/QFP datasheet numbers pins
  sequentially around the perimeter starting at a pin-1 marker, going either
  clockwise or counter-clockwise — a fixed walk, not an arbitrary layout.  So
  the user only supplies two things:
    1. Which lead is pin 1 — click it in the 3D view.
    2. Which direction the datasheet numbers in.
  Every other lead is numbered automatically by walking the perimeter
  (sorted by geometric angle around the package centre) in that direction.

Manual (click each lead in order) — used for STEP-imported packages (e.g.
  from the MirrorSemi Leadframe Library), whose leads are one fused metal
  solid with no per-lead object to auto-detect or walk geometrically.  The
  user clicks each lead's face in datasheet pin order; every click places
  the next sequential number.  "Undo last" / "Reset" correct mis-clicks.

Workflow
--------
1. Run "Pin Numbering" — a small panel opens in whichever mode applies.
2. Optionally click "Open Datasheet PDF…" to view it in your system's PDF
   viewer while you identify pins (the file is only opened, never parsed).
3. Auto mode: pick a direction, click the pin-1 lead.
   Manual mode: click each lead's face in pin order.
4. (Auto mode only) Use "Fine-tune numbers…" to hand-correct individual pins
   for packages that deviate from strict perimeter numbering.
"""

import math
import os

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore, QtGui

from Get_Path import get_icon

_LABEL_GROUP = "PinLabels"
_LABEL_SUFFIX = "_PinLabel"
_MANUAL_LABEL_PREFIX = "PinLabel_"


# ── lead collection (Auto mode — parametric leadframes) ─────────────────────

def _lead_objects(doc):
    """All lead/ball objects tagged IsLeadFinger."""
    return [o for o in doc.Objects if getattr(o, "IsLeadFinger", False)]


def _lead_snap_point(doc, lead_obj):
    """
    Return the Base.Vector snap point for *lead_obj* — the same point used
    for wire bonding (the ContactPoint marker whose SourceObject references
    this lead) — falling back to the lead shape's own top-face centre.
    """
    for obj in doc.Objects:
        if (getattr(obj, "IsContactPoint", False)
                and getattr(obj, "SourceObject", "") == lead_obj.Name):
            return FreeCAD.Vector(getattr(obj, "ContactPoint", FreeCAD.Vector()))
    try:
        shape = lead_obj.Shape
        top = max(shape.Faces, key=lambda f: f.CenterOfMass.z)
        return top.CenterOfMass
    except Exception:
        return FreeCAD.Vector(lead_obj.Placement.Base)


def _perimeter_order(doc, leads):
    """
    Return *leads* sorted into a counter-clockwise walk around the package
    centre, viewed from top (standard math angle convention: X right, Y up,
    increasing angle = CCW).

    Uses each lead's existing wire-bond snap point rather than assuming any
    particular build order, so it is correct regardless of how the leads
    were generated or named.
    """
    pts = {lead.Name: _lead_snap_point(doc, lead) for lead in leads}
    cx = sum(p.x for p in pts.values()) / len(pts)
    cy = sum(p.y for p in pts.values()) / len(pts)

    def _angle(lead):
        p = pts[lead.Name]
        return math.atan2(p.y - cy, p.x - cx)

    return sorted(leads, key=_angle)


def _auto_number_from_pin1(doc, leads, pin1_lead, clockwise: bool) -> dict:
    """
    Number every lead by walking the perimeter starting at *pin1_lead*.

    Returns ``{lead.Name: pin_number}``.
    """
    ordered = _perimeter_order(doc, leads)
    idx = next(i for i, l in enumerate(ordered) if l.Name == pin1_lead.Name)
    ordered = ordered[idx:] + ordered[:idx]
    if clockwise:
        # pin 1 stays fixed; walk the rest in decreasing-angle (CW) order.
        ordered = [ordered[0]] + list(reversed(ordered[1:]))
    return {lead.Name: i + 1 for i, lead in enumerate(ordered)}


# ── label (re)creation ──────────────────────────────────────────────────────

def _label_group(doc):
    for obj in doc.Objects:
        if obj.Name == _LABEL_GROUP:
            return obj
    grp = doc.addObject("App::DocumentObjectGroup", _LABEL_GROUP)
    grp.Label = "Pin Labels"
    return grp


def _style_label(label):
    # App::Annotation text is a screen-space billboard sized in points, not
    # document mm — 2 pt (an earlier value here) is imperceptible at normal
    # zoom.  18 pt is clearly readable regardless of package/zoom scale.
    # Bright red for contrast against any background or pad colour.
    try:
        label.ViewObject.FontSize = 18
    except Exception:
        pass
    try:
        label.ViewObject.TextColor = (1.0, 0.0, 0.0)
    except Exception:
        pass
    try:
        label.ViewObject.Visibility = True
    except Exception:
        pass


def apply_pin_labels(doc, lead_pin_numbers: dict):
    """
    Auto mode: write ``PinNumber`` onto each lead object and (re)build one
    text label per lead under the PinLabels group.  *lead_pin_numbers* maps
    ``lead_obj.Name -> int``.
    """
    grp = _label_group(doc)

    for lead_obj in _lead_objects(doc):
        pin_no = lead_pin_numbers.get(lead_obj.Name)
        if pin_no is None:
            continue

        if not hasattr(lead_obj, "PinNumber"):
            lead_obj.addProperty(
                "App::PropertyInteger", "PinNumber", "Leadframe",
                "Datasheet pin number"
            )
        lead_obj.PinNumber = int(pin_no)

        label_name = lead_obj.Name + _LABEL_SUFFIX
        label = doc.getObject(label_name)
        if label is None:
            label = doc.addObject("App::Annotation", label_name)
            grp.addObject(label)
        label.LabelText = [str(pin_no)]
        label.Position = _lead_snap_point(doc, lead_obj)
        _style_label(label)

    FreeCAD.Console.PrintMessage(
        f"[PinNumbering] Applied pin numbers to {len(lead_pin_numbers)} lead(s).\n"
    )


def apply_manual_pin_label(doc, pin_no: int, point):
    """
    Manual mode: create/update a floating pin-number label at *point*.
    There is no discrete lead object to tag (the leads are one fused
    imported solid), so the label stands on its own, keyed only by pin
    number, under the PinLabels group.
    """
    grp = _label_group(doc)
    label_name = f"{_MANUAL_LABEL_PREFIX}{pin_no:03d}"
    label = doc.getObject(label_name)
    if label is None:
        label = doc.addObject("App::Annotation", label_name)
        grp.addObject(label)
    label.LabelText = [str(pin_no)]
    label.Position = FreeCAD.Vector(point)
    _style_label(label)
    return label


def remove_manual_pin_label(doc, pin_no: int):
    label_name = f"{_MANUAL_LABEL_PREFIX}{pin_no:03d}"
    label = doc.getObject(label_name)
    if label is not None:
        doc.removeObject(label_name)


def refresh_all_label_styles(doc) -> int:
    """
    Re-apply the current label styling (font size / colour / visibility) to
    every existing pin label, without touching position or text.  Lets a
    styling fix take effect on labels placed by an earlier version of this
    tool without having to re-click every lead.
    """
    n = 0
    for obj in doc.Objects:
        if obj.Name.startswith(_MANUAL_LABEL_PREFIX) or obj.Name.endswith(_LABEL_SUFFIX):
            _style_label(obj)
            n += 1
    return n


# ── selection gates ──────────────────────────────────────────────────────────

class _LeadFingerGate:
    """Auto mode: allow only IsLeadFinger objects to be selected."""

    def allow(self, doc, obj, sub) -> bool:
        try:
            fc_obj = obj if not isinstance(obj, str) else (
                (FreeCAD.getDocument(doc) if isinstance(doc, str) else doc).getObject(obj)
            )
            return fc_obj is not None and getattr(fc_obj, "IsLeadFinger", False)
        except Exception:
            return False


class _AnyShapeGate:
    """Manual mode: allow any object that carries geometry (imported STEP bodies, etc.)."""

    def allow(self, doc, obj, sub) -> bool:
        try:
            fc_obj = obj if not isinstance(obj, str) else (
                (FreeCAD.getDocument(doc) if isinstance(doc, str) else doc).getObject(obj)
            )
            return fc_obj is not None and hasattr(fc_obj, "Shape")
        except Exception:
            return False


# ── fine-tune table dialog (Auto mode only) ─────────────────────────────────

class PinFineTuneDialog(QtWidgets.QDialog):
    """Modal table for hand-correcting individual pin numbers after auto-numbering."""

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fine-tune Pin Numbers")
        self.resize(420, 520)
        self.doc = doc
        self._leads = sorted(
            _lead_objects(doc),
            key=lambda o: getattr(o, "PinNumber", 0)
        )

        root = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel(
            "Correct any pins that deviate from strict perimeter numbering "
            "(e.g. renumbered no-connects) against the datasheet."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._table = QtWidgets.QTableWidget(len(self._leads), 3)
        self._table.setHorizontalHeaderLabels(["Lead", "Side", "Pin #"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
        )
        for row, lead in enumerate(self._leads):
            name_item = QtWidgets.QTableWidgetItem(lead.Label or lead.Name)
            name_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            side_item = QtWidgets.QTableWidgetItem(getattr(lead, "LeadSide", ""))
            side_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            pin_item = QtWidgets.QTableWidgetItem(str(getattr(lead, "PinNumber", row + 1)))
            pin_item.setFlags(
                QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                | QtCore.Qt.ItemIsEditable
            )
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, side_item)
            self._table.setItem(row, 2, pin_item)
        root.addWidget(self._table, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_apply = QtWidgets.QPushButton("Apply")
        btn_apply.setDefault(True)
        btn_apply.clicked.connect(self._apply)
        btn_cancel = QtWidgets.QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_apply)
        btn_row.addWidget(btn_cancel)
        root.addLayout(btn_row)

    def _apply(self):
        lead_pin_numbers = {}
        for row, lead in enumerate(self._leads):
            try:
                pin_no = int(self._table.item(row, 2).text().strip())
            except (ValueError, AttributeError):
                QtWidgets.QMessageBox.warning(
                    self, "Invalid pin number",
                    f"Row {row + 1} ('{lead.Label or lead.Name}') does not "
                    "have a valid integer pin number."
                )
                return
            lead_pin_numbers[lead.Name] = pin_no

        self.doc.openTransaction("Fine-tune Pin Numbering")
        try:
            apply_pin_labels(self.doc, lead_pin_numbers)
            self.doc.commitTransaction()
        except Exception as exc:
            self.doc.abortTransaction()
            QtWidgets.QMessageBox.critical(self, "Failed", str(exc))
            return

        self.doc.recompute()
        FreeCADGui.updateGui()
        self.accept()


# ── interactive panel ────────────────────────────────────────────────────────

class PinNumberingPanel(QtWidgets.QDialog):
    """
    Modeless panel — stays open so the click-to-select interaction can reach
    the 3D view underneath it, matching the ChipTransform / wire-bond session
    windowing pattern used elsewhere in this workbench.
    """

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pin Numbering")
        self.setWindowFlags(
            QtCore.Qt.Tool
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.WindowCloseButtonHint
        )
        self.setMinimumWidth(380)
        self.doc = doc
        self._active = False
        self._lead_objs = _lead_objects(doc)
        self._auto_mode = bool(self._lead_objs)   # True → parametric leadframe
        self._manual_next_pin = 1
        self._manual_history = []   # list of pin numbers placed, for Undo

        root = QtWidgets.QVBoxLayout(self)

        btn_open_pdf = QtWidgets.QPushButton("Open Datasheet PDF…")
        btn_open_pdf.setToolTip(
            "Opens a local PDF in your system's default viewer so you can "
            "read the pinout diagram. The file is only displayed — nothing "
            "is parsed or sent anywhere."
        )
        btn_open_pdf.clicked.connect(self._open_datasheet)
        root.addWidget(btn_open_pdf)

        if self._auto_mode:
            self._build_auto_ui(root)
        else:
            self._build_manual_ui(root)

        btn_refresh_style = QtWidgets.QPushButton("Refresh Label Style")
        btn_refresh_style.setToolTip(
            "Re-applies the current font size/colour to every pin label "
            "already placed — use this if labels placed by an earlier "
            "version of this tool are too small/invisible to see."
        )
        btn_refresh_style.clicked.connect(self._refresh_label_style)
        root.addWidget(btn_refresh_style)

        btn_close = QtWidgets.QPushButton("Close")
        btn_close.clicked.connect(self.close)
        root.addWidget(btn_close)

        self._start_session()

    # ── UI: auto mode (parametric leadframe) ────────────────────────────

    def _build_auto_ui(self, root):
        hint = QtWidgets.QLabel(
            "Most packages number pins sequentially around the perimeter "
            "starting at a pin-1 marker. Pick the direction below, then "
            "click the lead that is pin 1 in the 3D view."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        dir_box = QtWidgets.QGroupBox("Numbering direction (viewed from top)")
        dir_lay = QtWidgets.QHBoxLayout(dir_box)
        self._rb_ccw = QtWidgets.QRadioButton("Counter-clockwise")
        self._rb_cw  = QtWidgets.QRadioButton("Clockwise")
        self._rb_ccw.setChecked(True)
        dir_lay.addWidget(self._rb_ccw)
        dir_lay.addWidget(self._rb_cw)
        root.addWidget(dir_box)

        self._status = QtWidgets.QLabel("Click the lead that is Pin 1 in the 3D view.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #888; font-size: 10px;")
        root.addWidget(self._status)

        self._btn_finetune = QtWidgets.QPushButton("Fine-tune numbers…")
        self._btn_finetune.setEnabled(False)
        self._btn_finetune.clicked.connect(self._finetune)
        root.addWidget(self._btn_finetune)

    # ── UI: manual mode (imported STEP package) ─────────────────────────

    def _build_manual_ui(self, root):
        hint = QtWidgets.QLabel(
            "No individually tagged leads were found (this package looks "
            "like an imported STEP model, where all leads are one fused "
            "solid). Click each lead's face in the 3D view, in datasheet "
            "pin order — every click places the next number."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._status = QtWidgets.QLabel("Click the face of Pin 1 in the 3D view.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #888; font-size: 10px;")
        root.addWidget(self._status)

        btn_row = QtWidgets.QHBoxLayout()
        self._btn_undo = QtWidgets.QPushButton("Undo last")
        self._btn_undo.setEnabled(False)
        self._btn_undo.clicked.connect(self._undo_manual)
        btn_reset = QtWidgets.QPushButton("Reset")
        btn_reset.clicked.connect(self._reset_manual)
        btn_row.addWidget(self._btn_undo)
        btn_row.addWidget(btn_reset)
        root.addLayout(btn_row)

    # ── datasheet viewing (no parsing) ──────────────────────────────────

    def _open_datasheet(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Datasheet PDF", "", "PDF Files (*.pdf)"
        )
        if not path or not os.path.exists(path):
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    def _refresh_label_style(self):
        n = refresh_all_label_styles(self.doc)
        self.doc.recompute()
        FreeCADGui.updateGui()
        QtWidgets.QMessageBox.information(
            self, "Labels refreshed", f"Restyled {n} pin label(s)."
        )

    # ── selection session ────────────────────────────────────────────────

    def _start_session(self):
        self._active = True
        FreeCADGui.Selection.addObserver(self)
        gate = _LeadFingerGate() if self._auto_mode else _AnyShapeGate()
        FreeCADGui.Selection.addSelectionGate(gate)

    def _stop_session(self):
        if not self._active:
            return
        self._active = False
        try:
            FreeCADGui.Selection.removeSelectionGate()
        except Exception:
            pass
        try:
            FreeCADGui.Selection.removeObserver(self)
        except Exception:
            pass

    def addSelection(self, doc_name, obj_name, sub, pos):
        if not self._active:
            return
        if self._auto_mode:
            self._handle_auto_selection(doc_name, obj_name)
        else:
            self._handle_manual_selection(doc_name, obj_name, pos)

    # ── auto mode selection handling ─────────────────────────────────────

    def _handle_auto_selection(self, doc_name, obj_name):
        try:
            doc = FreeCAD.getDocument(doc_name)
            lead = doc.getObject(obj_name)
            if lead is None or not getattr(lead, "IsLeadFinger", False):
                return

            leads = _lead_objects(doc)
            clockwise = self._rb_cw.isChecked()
            lead_pin_numbers = _auto_number_from_pin1(doc, leads, lead, clockwise)

            doc.openTransaction("Auto Pin Numbering")
            try:
                apply_pin_labels(doc, lead_pin_numbers)
                doc.commitTransaction()
            except Exception as exc:
                doc.abortTransaction()
                FreeCAD.Console.PrintError(f"[PinNumbering] apply failed: {exc}\n")
                return
            doc.recompute()
            FreeCADGui.updateGui()

            direction = "clockwise" if clockwise else "counter-clockwise"
            self._status.setText(
                f"Pin 1 = {lead.Label or lead.Name}, numbered {direction}. "
                "Click a different lead to renumber from there, or fine-tune "
                "individual pins below."
            )
            self._btn_finetune.setEnabled(True)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"[PinNumbering] selection error: {exc}\n")

    def _finetune(self):
        dlg = PinFineTuneDialog(self.doc, self)
        dlg.exec_()

    # ── manual mode selection handling ───────────────────────────────────

    def _handle_manual_selection(self, doc_name, obj_name, pos):
        try:
            doc = FreeCAD.getDocument(doc_name)

            point = None
            if isinstance(pos, FreeCAD.Vector):
                point = pos
            elif pos and len(pos) == 3:
                point = FreeCAD.Vector(pos[0], pos[1], pos[2])
            else:
                # No 3-D pick point supplied (e.g. selection made via the
                # tree view) — fall back to the clicked object's bbox centre.
                obj = doc.getObject(obj_name)
                if obj is not None and hasattr(obj, "Shape"):
                    try:
                        point = obj.Shape.BoundBox.Center
                    except Exception:
                        pass
            if point is None:
                FreeCAD.Console.PrintWarning(
                    "[PinNumbering] Could not determine a click position — "
                    "click a face directly in the 3D view.\n"
                )
                return

            pin_no = self._manual_next_pin
            doc.openTransaction(f"Pin Numbering: pin {pin_no}")
            try:
                apply_manual_pin_label(doc, pin_no, point)
                doc.commitTransaction()
            except Exception as exc:
                doc.abortTransaction()
                FreeCAD.Console.PrintError(f"[PinNumbering] apply failed: {exc}\n")
                return
            doc.recompute()
            FreeCADGui.updateGui()

            self._manual_history.append(pin_no)
            self._manual_next_pin += 1
            self._btn_undo.setEnabled(True)
            self._status.setText(
                f"Placed pin {pin_no}. Click the face of pin "
                f"{self._manual_next_pin} next."
            )
        except Exception as exc:
            FreeCAD.Console.PrintError(f"[PinNumbering] selection error: {exc}\n")

    def _undo_manual(self):
        if not self._manual_history:
            return
        last_pin = self._manual_history.pop()
        self.doc.openTransaction("Pin Numbering: undo")
        try:
            remove_manual_pin_label(self.doc, last_pin)
            self.doc.commitTransaction()
        except Exception:
            self.doc.abortTransaction()
        self.doc.recompute()
        FreeCADGui.updateGui()
        self._manual_next_pin = last_pin
        self._btn_undo.setEnabled(bool(self._manual_history))
        self._status.setText(f"Undone. Click the face of pin {self._manual_next_pin} next.")

    def _reset_manual(self):
        self.doc.openTransaction("Pin Numbering: reset")
        try:
            for pin_no in self._manual_history:
                remove_manual_pin_label(self.doc, pin_no)
            self.doc.commitTransaction()
        except Exception:
            self.doc.abortTransaction()
        self.doc.recompute()
        FreeCADGui.updateGui()
        self._manual_history = []
        self._manual_next_pin = 1
        self._btn_undo.setEnabled(False)
        self._status.setText("Click the face of Pin 1 in the 3D view.")

    # ── selection observer stubs ─────────────────────────────────────────

    def setPreselection(self, *_):
        pass

    def removePreselection(self, *_):
        pass

    def clearSelection(self, *_):
        pass

    def setSelection(self, *_):
        pass

    def closeEvent(self, event):
        self._stop_session()
        super().closeEvent(event)


# ── FreeCAD command ────────────────────────────────────────────────────────

_panel_instance = None


class PinNumberingCommand:

    def GetResources(self):
        return {
            "MenuText": "Pin Numbering",
            "ToolTip": (
                "Assign datasheet pin numbers to package leads and show "
                "them as 3-D labels.\n"
                "Parametric leadframes: pick a direction, click pin 1 — "
                "every other lead is numbered automatically.\n"
                "Imported STEP packages: click each lead's face in pin "
                "order."
            ),
            "Pixmap": get_icon("Pin_Numbering.svg"),
        }

    def IsActive(self):
        doc = FreeCAD.activeDocument()
        if doc is None:
            return False
        return any(hasattr(o, "Shape") for o in doc.Objects)

    def Activated(self):
        global _panel_instance
        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        if _panel_instance is not None:
            _panel_instance.close()
        _panel_instance = PinNumberingPanel(doc, FreeCADGui.getMainWindow())
        _panel_instance.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        _panel_instance.show()
        _panel_instance.raise_()
        _panel_instance.activateWindow()


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("PinNumberingCommand", PinNumberingCommand())
