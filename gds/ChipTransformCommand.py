"""
Chip Transform Tool

Opens a modeless dialog to translate and rotate all GDS chip objects
(Layer_*, IC_Body_Solid, GDS_Pin_*, GDS_PINs_*, ContactPoint_*, contact_point_*,
BondWire_*, WireBump_*, GridPt*) as a group,
or to operate on the current FreeCAD selection.

Translation and rotation are applied incrementally via buttons or keyboard
shortcuts when the dialog has keyboard focus.

Keyboard shortcuts (dialog must be focused):
  ←/→         ±X translation
  ↑/↓         ±Y translation
  PgUp/PgDn   ±Z translation
  Shift+←/→   ±Rz rotation (around Z)
  Shift+↑/↓   ±Rx rotation (around X)
  Shift+PgUp/Dn ±Ry rotation (around Y)
"""

import os
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore, QtGui

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)
from Get_Path import get_icon


# ── GDS object detection ───────────────────────────────────────────────────────

# All name prefixes that belong to the GDS chip (case-sensitive match against
# the actual names produced by GDSCommand, Core_Functionality, SetContactPoints,
# ManualWireBonding, and WireBumpConfigurator).
_GDS_PREFIXES = (
    "Layer_",            # GDSCommand / LayeronLeadframe: Layer_{name}_{id}[_{dt}]
    "IC_Body_Solid",     # GDSCommand contacts_only_3d body
    "GDS_Pin_",          # GDSCommand: GDS_Pin_Instances
    "GDS_PINs_",         # Core_Functionality auto-PIN: GDS_PINs_{edi_name}  (uppercase!)
    "ContactPoint_",     # ContactPointTool / Core_Functionality: ContactPoint_{n}
    "contact_point_",    # SetContactPointsOnFaceCommand: contact_point_housing_{n}
    "BondWire_",         # ManualWireBonding: BondWire_{n}
    "WireBump_",         # WireBumpConfigurator: WireBump_{shape}_{n}
    "GridPt",            # SetContactPointsOnFaceCommand grid markers (if any remain)
)


def _all_objects(doc):
    """Every object in the document that has a Shape."""
    return [o for o in (doc.Objects if doc else []) if hasattr(o, "Shape")]


def _gds_objects(doc):
    """GDS chip objects only — excludes leadframe, housing, sketches, etc."""
    return [
        o for o in _all_objects(doc)
        if any(o.Name.startswith(p) for p in _GDS_PREFIXES)
    ]


def _selected_objects():
    return [s.Object for s in FreeCADGui.Selection.getSelectionEx()
            if hasattr(s.Object, "Shape")]


def _bounding_center(objects):
    """World-space bounding box centre of a list of shaped objects."""
    xmin = ymin = zmin = float("inf")
    xmax = ymax = zmax = float("-inf")
    for obj in objects:
        try:
            b = obj.Shape.BoundBox
            xmin = min(xmin, b.XMin); xmax = max(xmax, b.XMax)
            ymin = min(ymin, b.YMin); ymax = max(ymax, b.YMax)
            zmin = min(zmin, b.ZMin); zmax = max(zmax, b.ZMax)
        except Exception:
            pass
    if xmin == float("inf"):
        return FreeCAD.Vector(0, 0, 0)
    return FreeCAD.Vector((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)


# ── Low-level transform helpers ────────────────────────────────────────────────

def _translate_objects(objects, dx, dy, dz):
    v = FreeCAD.Vector(dx, dy, dz)
    for obj in objects:
        obj.Placement = FreeCAD.Placement(
            obj.Placement.Base + v,
            obj.Placement.Rotation,
        )


def _rotate_objects(objects, axis_vec, angle_deg, center):
    """Rotate objects around *center* by *angle_deg* about *axis_vec* (world frame)."""
    rot = FreeCAD.Rotation(axis_vec, angle_deg)
    for obj in objects:
        old_pos = obj.Placement.Base
        old_rot = obj.Placement.Rotation
        rel     = old_pos - center
        new_pos = rot.multVec(rel) + center
        new_rot = rot * old_rot          # * is the correct compose operator in FreeCAD
        obj.Placement = FreeCAD.Placement(new_pos, new_rot)


def _restore_placements(objects, saved):
    """Restore placements from a {name: Placement} snapshot."""
    for obj in objects:
        if obj.Name in saved:
            obj.Placement = saved[obj.Name].copy()


# ── Dialog ─────────────────────────────────────────────────────────────────────

class ChipTransformDialog(QtWidgets.QDialog):
    """
    Modeless dialog for incremental chip translation / rotation.
    Stays open so the user can make many adjustments, then close it.
    """

    _AX = FreeCAD.Vector(1, 0, 0)
    _AY = FreeCAD.Vector(0, 1, 0)
    _AZ = FreeCAD.Vector(0, 0, 1)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chip Transform")
        self.setWindowFlags(
            QtCore.Qt.Tool |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.WindowCloseButtonHint,
        )
        self.setMinimumWidth(400)

        self._dx = self._dy = self._dz = 0.0    # cumulative translation (mm)
        self._rx = self._ry = self._rz = 0.0    # cumulative rotation (deg, approx)
        self._initial_placements = {}            # {name: Placement} snapshot at open

        self._build_ui()
        self._snapshot_placements()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)

        # Object scope
        scope_grp = QtWidgets.QGroupBox("Objects to Move")
        scope_lay = QtWidgets.QHBoxLayout(scope_grp)
        self._rb_gds = QtWidgets.QRadioButton("GDS Chip Objects")
        self._rb_all = QtWidgets.QRadioButton("All Document Objects")
        self._rb_sel = QtWidgets.QRadioButton("Current Selection")
        self._rb_gds.setChecked(True)
        for rb in (self._rb_gds, self._rb_all, self._rb_sel):
            scope_lay.addWidget(rb)
        root.addWidget(scope_grp)

        # Translation
        t_grp = QtWidgets.QGroupBox("Translate")
        t_lay = QtWidgets.QGridLayout(t_grp)
        t_lay.setHorizontalSpacing(4)
        t_lay.setVerticalSpacing(4)

        self._t_step = QtWidgets.QDoubleSpinBox()
        self._t_step.setRange(0.001, 1000.0)
        self._t_step.setValue(0.1)
        self._t_step.setDecimals(3)
        self._t_step.setSuffix(" mm")
        t_lay.addWidget(QtWidgets.QLabel("Step:"), 0, 0)
        t_lay.addWidget(self._t_step, 0, 1, 1, 4)

        # XY arrow cross (row/col arrangement)
        t_lay.addWidget(self._nav_btn("↑  +Y",  lambda: self._do_translate( 0, +1,  0)), 1, 2)
        t_lay.addWidget(self._nav_btn("← -X",   lambda: self._do_translate(-1,  0,  0)), 2, 1)
        center_lbl = QtWidgets.QLabel("XY")
        center_lbl.setAlignment(QtCore.Qt.AlignCenter)
        t_lay.addWidget(center_lbl, 2, 2)
        t_lay.addWidget(self._nav_btn("+X →",   lambda: self._do_translate(+1,  0,  0)), 2, 3)
        t_lay.addWidget(self._nav_btn("↓  -Y",  lambda: self._do_translate( 0, -1,  0)), 3, 2)

        # Z column
        t_lay.addWidget(QtWidgets.QLabel("Z:"), 1, 5, alignment=QtCore.Qt.AlignRight)
        t_lay.addWidget(self._nav_btn("▲ +Z",  lambda: self._do_translate(0, 0, +1)), 2, 5)
        t_lay.addWidget(self._nav_btn("▼ -Z",  lambda: self._do_translate(0, 0, -1)), 3, 5)

        reset_pos = QtWidgets.QPushButton("Reset Position to Origin")
        reset_pos.clicked.connect(self._reset_position)
        t_lay.addWidget(reset_pos, 4, 0, 1, 6)

        root.addWidget(t_grp)

        # Rotation
        r_grp = QtWidgets.QGroupBox("Rotate (around bounding-box center)")
        r_lay = QtWidgets.QGridLayout(r_grp)
        r_lay.setHorizontalSpacing(4)
        r_lay.setVerticalSpacing(4)

        self._r_step = QtWidgets.QDoubleSpinBox()
        self._r_step.setRange(0.1, 180.0)
        self._r_step.setValue(15.0)
        self._r_step.setDecimals(1)
        self._r_step.setSuffix(" °")
        r_lay.addWidget(QtWidgets.QLabel("Step:"), 0, 0)
        r_lay.addWidget(self._r_step, 0, 1, 1, 4)

        axes = [
            ("X", self._AX, "_rx"),
            ("Y", self._AY, "_ry"),
            ("Z", self._AZ, "_rz"),
        ]
        for row, (label, axis, _) in enumerate(axes, start=1):
            r_lay.addWidget(QtWidgets.QLabel(f"Around {label}:"), row, 0)
            ax = axis
            r_lay.addWidget(self._nav_btn(f"−{label}", lambda a=ax: self._do_rotate(a, -1)), row, 1)
            r_lay.addWidget(self._nav_btn(f"+{label}", lambda a=ax: self._do_rotate(a, +1)), row, 2)

        root.addWidget(r_grp)

        # Status
        status_grp = QtWidgets.QGroupBox("Cumulative Offset (since dialog opened)")
        status_lay = QtWidgets.QVBoxLayout(status_grp)
        mono = QtGui.QFont("Courier")
        mono.setPointSize(9)
        self._lbl_pos = QtWidgets.QLabel()
        self._lbl_rot = QtWidgets.QLabel()
        self._lbl_pos.setFont(mono)
        self._lbl_rot.setFont(mono)
        status_lay.addWidget(self._lbl_pos)
        status_lay.addWidget(self._lbl_rot)
        root.addWidget(status_grp)
        self._update_status()

        # Keyboard hint
        hint = QtWidgets.QLabel(
            "<small><b>Keyboard shortcuts</b> (click this dialog first to focus it):<br>"
            "← → = ±X &nbsp;&nbsp; ↑ ↓ = ±Y &nbsp;&nbsp; PgUp/PgDn = ±Z<br>"
            "Shift + ← → = ±Rz &nbsp;&nbsp; Shift + ↑ ↓ = ±Rx &nbsp;&nbsp; "
            "Shift + PgUp/PgDn = ±Ry</small>"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        # Bottom buttons
        btn_row = QtWidgets.QHBoxLayout()
        restore_btn = QtWidgets.QPushButton("Restore Original")
        restore_btn.setToolTip("Undo all changes made in this dialog session")
        restore_btn.clicked.connect(self._restore_original)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(restore_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _nav_btn(self, label, callback):
        b = QtWidgets.QPushButton(label)
        b.setFixedWidth(62)
        b.setFixedHeight(32)
        b.clicked.connect(callback)
        return b

    # ── Snapshot & restore ─────────────────────────────────────────────────────

    def _snapshot_placements(self):
        """Capture current placements so 'Restore Original' can undo everything."""
        doc = FreeCAD.activeDocument()
        all_objs = _all_objects(doc) if doc else []
        self._initial_placements = {o.Name: o.Placement.copy() for o in all_objs}

    def _restore_original(self):
        doc = FreeCAD.activeDocument()
        if not doc:
            return
        objs = _all_objects(doc)
        doc.openTransaction("Chip Transform: Restore Original")
        _restore_placements(objs, self._initial_placements)
        doc.commitTransaction()
        doc.recompute()
        self._dx = self._dy = self._dz = 0.0
        self._rx = self._ry = self._rz = 0.0
        self._update_status()

    # ── Object collection ──────────────────────────────────────────────────────

    def _objects(self):
        doc = FreeCAD.activeDocument()
        if self._rb_sel.isChecked():
            objs = _selected_objects()
        elif self._rb_all.isChecked():
            objs = _all_objects(doc)
        else:
            objs = _gds_objects(doc)
        if not objs:
            QtWidgets.QMessageBox.warning(
                self, "Nothing to move",
                "No objects found for the selected scope.\n\n"
                "• 'GDS Chip Objects' requires a GDS import (Layer_*, ContactPoint_*, …)\n"
                "• 'Current Selection' requires objects selected in the 3D view\n"
                "• 'All Document Objects' requires an open document",
            )
        return objs

    # ── Transform actions ──────────────────────────────────────────────────────

    def _do_translate(self, sx, sy, sz):
        objs = self._objects()
        if not objs:
            return
        step = self._t_step.value()
        dx, dy, dz = sx * step, sy * step, sz * step
        doc = FreeCAD.activeDocument()
        doc.openTransaction("Chip Translate")
        _translate_objects(objs, dx, dy, dz)
        doc.commitTransaction()
        doc.recompute()
        self._dx += dx
        self._dy += dy
        self._dz += dz
        self._update_status()

    def _do_rotate(self, axis, sign):
        objs = self._objects()
        if not objs:
            return
        angle = sign * self._r_step.value()
        center = _bounding_center(objs)
        doc = FreeCAD.activeDocument()
        doc.openTransaction("Chip Rotate")
        _rotate_objects(objs, axis, angle, center)
        doc.commitTransaction()
        doc.recompute()
        if axis.x:   self._rx += angle
        elif axis.y: self._ry += angle
        elif axis.z: self._rz += angle
        self._update_status()

    def _reset_position(self):
        objs = self._objects()
        if not objs:
            return
        if self._dx == 0.0 and self._dy == 0.0 and self._dz == 0.0:
            return
        doc = FreeCAD.activeDocument()
        doc.openTransaction("Chip Reset Position")
        _translate_objects(objs, -self._dx, -self._dy, -self._dz)
        doc.commitTransaction()
        doc.recompute()
        self._dx = self._dy = self._dz = 0.0
        self._update_status()

    # ── Status display ─────────────────────────────────────────────────────────

    def _update_status(self):
        self._lbl_pos.setText(
            f"Position:  X={self._dx:+8.3f}  Y={self._dy:+8.3f}  Z={self._dz:+8.3f}  mm"
        )
        self._lbl_rot.setText(
            f"Rotation:  Rx={self._rx:+7.1f}°  Ry={self._ry:+7.1f}°  Rz={self._rz:+7.1f}°"
        )

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key   = event.key()
        shift = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
        handled = True

        if shift:
            if   key == QtCore.Qt.Key_Right:    self._do_rotate(self._AZ, +1)
            elif key == QtCore.Qt.Key_Left:     self._do_rotate(self._AZ, -1)
            elif key == QtCore.Qt.Key_Up:       self._do_rotate(self._AX, +1)
            elif key == QtCore.Qt.Key_Down:     self._do_rotate(self._AX, -1)
            elif key == QtCore.Qt.Key_PageUp:   self._do_rotate(self._AY, +1)
            elif key == QtCore.Qt.Key_PageDown: self._do_rotate(self._AY, -1)
            else: handled = False
        else:
            if   key == QtCore.Qt.Key_Right:    self._do_translate(+1,  0,  0)
            elif key == QtCore.Qt.Key_Left:     self._do_translate(-1,  0,  0)
            elif key == QtCore.Qt.Key_Up:       self._do_translate( 0, +1,  0)
            elif key == QtCore.Qt.Key_Down:     self._do_translate( 0, -1,  0)
            elif key == QtCore.Qt.Key_PageUp:   self._do_translate( 0,  0, +1)
            elif key == QtCore.Qt.Key_PageDown: self._do_translate( 0,  0, -1)
            else: handled = False

        if handled:
            event.accept()
        else:
            super().keyPressEvent(event)


# ── Command ────────────────────────────────────────────────────────────────────

_dialog_instance = None


class ChipTransformCommand:
    def GetResources(self):
        return {
            "MenuText": "Move / Rotate Chip",
            "ToolTip": (
                "Open the Chip Transform dialog to translate and rotate\n"
                "GDS chip objects using buttons or keyboard arrow keys."
            ),
            "Pixmap": get_icon("Chip_Transform.svg"),
        }

    def Activated(self):
        global _dialog_instance
        main_win = FreeCADGui.getMainWindow()
        if _dialog_instance is None or not _dialog_instance.isVisible():
            _dialog_instance = ChipTransformDialog(main_win)
            _dialog_instance.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        _dialog_instance.show()
        _dialog_instance.raise_()
        _dialog_instance.activateWindow()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand("ChipTransformCommand", ChipTransformCommand())
