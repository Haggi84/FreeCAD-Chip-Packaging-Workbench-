# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Layer Slider Panel — interactive layer-by-layer GDS viewer.

Mimics the Prusa-Slicer layer slider: a vertical slider on the right side
reveals layers from bottom (Z=0) upward.  Layers ABOVE the handle are hidden;
layers AT or BELOW are shown.  The model-tree eye icons update in sync.
"""

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore, QtGui


# ── helpers ───────────────────────────────────────────────────────────────────

def _gds_group(doc):
    if doc is None:
        return None
    for obj in doc.Objects:
        if obj.Name == "GDS_Die" or obj.Label == "GDS_Die":
            return obj
    return None


def _z_min(obj) -> float:
    try:
        return float(obj.Shape.BoundBox.ZMin)
    except Exception:
        pass
    try:
        return float(obj.Mesh.BoundBox.ZMin)
    except Exception:
        pass
    return 0.0


def _z_max(obj) -> float:
    try:
        return float(obj.Shape.BoundBox.ZMax)
    except Exception:
        pass
    try:
        return float(obj.Mesh.BoundBox.ZMax)
    except Exception:
        pass
    return _z_min(obj)


def _has_geometry(obj) -> bool:
    return (hasattr(obj, "Shape") and obj.Shape is not None) or \
           (hasattr(obj, "Mesh")  and obj.Mesh  is not None)


# ── slider track widget (custom-drawn, always visible) ────────────────────────

class _SliderTrack(QtWidgets.QWidget):
    """
    Custom-drawn vertical track showing the active (blue) and inactive (grey)
    portions, with a draggable white handle.  Uses no QSlider CSS quirks.
    """

    valueChanged = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min     = 0
        self._max     = 0
        self._value   = 0
        self._dragging = False
        self.setMinimumWidth(36)
        self.setMinimumHeight(120)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed,
            QtWidgets.QSizePolicy.Expanding,
        )
        self.setCursor(QtCore.Qt.PointingHandCursor)

    # ── public ────────────────────────────────────────────────────────────────

    def setRange(self, mn: int, mx: int):
        self._min = mn
        self._max = max(mn, mx)
        self._value = self._max
        self.update()

    def setValue(self, v: int):
        v = max(self._min, min(self._max, v))
        if v != self._value:
            self._value = v
            self.update()
            self.valueChanged.emit(v)

    def value(self) -> int:
        return self._value

    # ── geometry helpers ──────────────────────────────────────────────────────

    def _track_rect(self) -> QtCore.QRect:
        w = self.width()
        return QtCore.QRect(w // 2 - 5, 12, 10, self.height() - 24)

    def _handle_y(self) -> int:
        tr  = self._track_rect()
        rng = self._max - self._min
        if rng == 0:
            return tr.top()
        frac = (self._max - self._value) / rng   # 0 = top (max), 1 = bottom (min)
        return tr.top() + int(frac * tr.height())

    def _value_for_y(self, y: int) -> int:
        tr  = self._track_rect()
        rng = self._max - self._min
        if rng == 0:
            return self._min
        frac = (y - tr.top()) / max(tr.height(), 1)
        frac = max(0.0, min(1.0, frac))
        return self._max - int(round(frac * rng))

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p   = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        tr   = self._track_rect()
        hy   = self._handle_y()
        blue = QtGui.QColor("#1A99E6")
        grey = QtGui.QColor("#cccccc")
        dark = QtGui.QColor("#999999")

        # inactive section (above handle = layers that are hidden)
        inactive = QtCore.QRect(tr.x(), tr.top(), tr.width(), hy - tr.top())
        if inactive.height() > 0:
            p.setBrush(grey)
            p.setPen(QtCore.Qt.NoPen)
            p.drawRoundedRect(inactive, 5, 5)

        # active section (below handle = layers that are visible)
        active = QtCore.QRect(tr.x(), hy, tr.width(), tr.bottom() - hy + 1)
        if active.height() > 0:
            p.setBrush(blue)
            p.setPen(QtCore.Qt.NoPen)
            p.drawRoundedRect(active, 5, 5)

        # tick marks
        p.setPen(QtGui.QPen(dark, 1))
        n = self._max - self._min
        if n > 0 and n <= 60:
            for i in range(n + 1):
                frac = i / n
                ty   = tr.top() + int(frac * tr.height())
                p.drawLine(tr.x() - 3, ty, tr.x(), ty)

        # handle
        hr = QtCore.QRect(tr.x() + tr.width() // 2 - 10, hy - 10, 20, 20)
        p.setBrush(QtGui.QColor("white"))
        p.setPen(QtGui.QPen(blue, 3))
        p.drawEllipse(hr)

        p.end()

    # ── mouse events ─────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self.setValue(self._value_for_y(event.pos().y()))

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.setValue(self._value_for_y(event.pos().y()))

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._dragging = False

    def wheelEvent(self, event):
        delta = 1 if event.angleDelta().y() > 0 else -1
        self.setValue(self._value + delta)


# ── panel ─────────────────────────────────────────────────────────────────────

class LayerSliderPanel(QtWidgets.QDockWidget):

    _MODE_NORMAL      = 0
    _MODE_TRANSPARENT = 1
    _MODE_HIDDEN      = 2

    def __init__(self, parent=None):
        super().__init__("Layer Slider", parent)
        self.setObjectName("LayerSliderPanel")
        self.setMinimumWidth(300)
        self.setMinimumHeight(420)

        self._layers       = []
        self._saved_states = {}
        self._active       = False
        self._other_mode   = self._MODE_NORMAL

        self._build_ui()
        self.populate()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QtWidgets.QWidget()
        root    = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # toolbar
        btn_row = QtWidgets.QHBoxLayout()
        btn_refresh = QtWidgets.QPushButton("Refresh")
        btn_refresh.setToolTip("Re-scan document for GDS layers")
        btn_refresh.clicked.connect(self.populate)
        self._btn_reset = QtWidgets.QPushButton("Show All")
        self._btn_reset.setToolTip("Restore all layers and exit slider mode")
        self._btn_reset.clicked.connect(self.reset_all)
        self._btn_reset.setEnabled(False)
        btn_row.addWidget(btn_refresh)
        btn_row.addWidget(self._btn_reset)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # other-objects control
        grp_other = QtWidgets.QGroupBox("Non-GDS objects")
        grp_other.setStyleSheet("QGroupBox { font-size: 10px; }")
        other_row = QtWidgets.QHBoxLayout(grp_other)
        other_row.setContentsMargins(4, 2, 4, 2)
        self._rb_normal      = QtWidgets.QRadioButton("Normal")
        self._rb_transparent = QtWidgets.QRadioButton("Transparent")
        self._rb_hidden      = QtWidgets.QRadioButton("Hide")
        self._rb_normal.setChecked(True)
        for rb in (self._rb_normal, self._rb_transparent, self._rb_hidden):
            other_row.addWidget(rb)
            rb.toggled.connect(self._on_other_mode_changed)
        root.addWidget(grp_other)

        # info label
        self._lbl_info = QtWidgets.QLabel("No layers loaded.")
        self._lbl_info.setStyleSheet(
            "background:#2b2b2b; color:#e0e0e0; padding:4px 6px;"
            "border-radius:3px; font-family:monospace; font-size:10px;"
        )
        self._lbl_info.setWordWrap(True)
        self._lbl_info.setMinimumHeight(52)
        root.addWidget(self._lbl_info)

        # ── splitter: tree (left) + custom slider track (right) ──────────────
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setHandleWidth(4)

        self._tree = QtWidgets.QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Layer", "Z (mm)"])
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self._tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self._tree.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self._tree.setAlternatingRowColors(True)
        self._tree.setStyleSheet("font-size: 10px;")
        splitter.addWidget(self._tree)

        # Custom slider track in a wrapper
        track_wrapper = QtWidgets.QWidget()
        track_wrapper.setMinimumWidth(44)
        track_wrapper.setMaximumWidth(56)
        tw_layout = QtWidgets.QVBoxLayout(track_wrapper)
        tw_layout.setContentsMargins(4, 4, 4, 4)
        tw_layout.setSpacing(2)

        lbl_top = QtWidgets.QLabel("▲")
        lbl_top.setAlignment(QtCore.Qt.AlignHCenter)
        lbl_top.setStyleSheet("color:#1A99E6; font-size:10px; font-weight:bold;")

        self._track = _SliderTrack()
        self._track.valueChanged.connect(self._on_slider_changed)

        lbl_bot = QtWidgets.QLabel("▼")
        lbl_bot.setAlignment(QtCore.Qt.AlignHCenter)
        lbl_bot.setStyleSheet("color:#999; font-size:10px;")

        tw_layout.addWidget(lbl_top, 0, QtCore.Qt.AlignHCenter)
        tw_layout.addWidget(self._track, 1)
        tw_layout.addWidget(lbl_bot, 0, QtCore.Qt.AlignHCenter)
        splitter.addWidget(track_wrapper)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        root.addWidget(splitter, 1)

        # status
        self._lbl_status = QtWidgets.QLabel("")
        self._lbl_status.setStyleSheet("color:#888; font-size:9px; padding:0 2px;")
        root.addWidget(self._lbl_status)

        self.setWidget(central)

    # ── public API ─────────────────────────────────────────────────────────────

    def populate(self):
        self.reset_all()
        self._layers = []
        self._tree.clear()

        doc = FreeCAD.activeDocument()
        if not doc:
            self._lbl_info.setText("No active document.")
            self._lbl_status.setText("")
            return

        grp = _gds_group(doc)
        if grp is None:
            self._lbl_info.setText("No GDS_Die group found.\nImport a GDS file first.")
            self._lbl_status.setText("")
            return

        raw = []
        for obj in grp.Group:
            if not _has_geometry(obj):
                continue
            raw.append((_z_min(obj), _z_max(obj), obj, obj.Label or obj.Name))

        if not raw:
            self._lbl_info.setText("GDS_Die group is empty.")
            return

        raw.sort(key=lambda t: (t[0], t[1]))
        self._layers = raw
        n = len(raw)

        self._track.setRange(0, n - 1)   # emits valueChanged → _on_slider_changed
        # We want to start fully open (all visible) without triggering visibility changes:
        # The setRange call sets value to n-1 internally but _active is still False,
        # so no visibility changes are applied yet.

        for idx, (z0, z1, obj, label) in enumerate(raw):
            item = QtWidgets.QTreeWidgetItem([label, f"{z0:.4f}"])
            item.setData(0, QtCore.Qt.UserRole, idx)
            item.setForeground(1, QtGui.QBrush(QtGui.QColor("#888")))
            self._tree.addTopLevelItem(item)

        self._tree.resizeColumnToContents(1)
        self._lbl_status.setText(f"{n} layer(s) found in GDS_Die group.")
        self._update_info_label(n - 1)
        self._update_tree_marks(-1)   # all visible initially

        FreeCAD.Console.PrintMessage(
            f"LayerSlider: loaded {n} layers from GDS_Die group.\n"
        )

    def reset_all(self):
        """Restore all objects to their saved state."""
        self._restore_states()
        self._active = False
        self._btn_reset.setEnabled(False)
        self._update_tree_marks(-1)
        FreeCADGui.updateGui()

    # ── slots ──────────────────────────────────────────────────────────────────

    def _on_slider_changed(self, value: int):
        if not self._layers:
            return
        if not self._active:
            self._save_states()
            self._active = True
            self._btn_reset.setEnabled(True)
        self._apply_visibility(value)
        self._update_info_label(value)
        self._update_tree_marks(value)

    def _on_other_mode_changed(self, _checked):
        if self._rb_hidden.isChecked():
            self._other_mode = self._MODE_HIDDEN
        elif self._rb_transparent.isChecked():
            self._other_mode = self._MODE_TRANSPARENT
        else:
            self._other_mode = self._MODE_NORMAL
        if self._active:
            self._apply_visibility(self._track.value())

    # ── visibility logic ───────────────────────────────────────────────────────

    def _save_states(self):
        doc = FreeCAD.activeDocument()
        if not doc:
            return
        self._saved_states = {}
        for obj in doc.Objects:
            try:
                vis  = obj.ViewObject.Visibility
                tran = getattr(obj.ViewObject, "Transparency", 0)
                self._saved_states[obj.Name] = (vis, tran)
            except Exception:
                pass

    def _restore_states(self):
        doc = FreeCAD.activeDocument()
        if not doc or not self._saved_states:
            return
        for obj in doc.Objects:
            state = self._saved_states.get(obj.Name)
            if state is None:
                continue
            vis, tran = state
            try:
                obj.ViewObject.Visibility   = vis
                obj.ViewObject.Transparency = tran
            except Exception:
                pass
        self._saved_states = {}

    def _apply_visibility(self, slider_value: int):
        doc = FreeCAD.activeDocument()
        if not doc or not self._layers:
            FreeCAD.Console.PrintWarning("LayerSlider: cannot apply — no doc or layers\n")
            return

        gds_names = {t[2].Name for t in self._layers}

        # GDS layers: show up to slider_value (inclusive), hide the rest
        for idx, (z0, z1, obj, label) in enumerate(self._layers):
            visible = (idx <= slider_value)
            try:
                obj.ViewObject.Visibility = visible
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"LayerSlider: visibility error on '{label}': {exc}\n"
                )

        # Non-GDS objects
        for obj in doc.Objects:
            if obj.Name in gds_names:
                continue
            try:
                if self._other_mode == self._MODE_HIDDEN:
                    obj.ViewObject.Visibility = False
                elif self._other_mode == self._MODE_TRANSPARENT:
                    obj.ViewObject.Visibility   = True
                    obj.ViewObject.Transparency = 80
                else:
                    state = self._saved_states.get(obj.Name)
                    if state:
                        obj.ViewObject.Visibility   = state[0]
                        obj.ViewObject.Transparency = state[1]
            except Exception:
                pass

        # Flush all pending Qt events so the 3D view and model tree repaint
        FreeCADGui.updateGui()

    # ── display helpers ────────────────────────────────────────────────────────

    def _update_info_label(self, slider_value: int):
        n = len(self._layers)
        if n == 0:
            self._lbl_info.setText("No layers.")
            return
        idx = max(0, min(slider_value, n - 1))
        z0, z1, obj, label = self._layers[idx]
        self._lbl_info.setText(
            f"Top visible layer ({idx + 1}/{n}):\n"
            f"{label}\n"
            f"Z: {z0:.4f} … {z1:.4f} mm"
        )
        self._lbl_status.setText(f"Showing {idx + 1} of {n} layer(s)")

    def _update_tree_marks(self, slider_value: int):
        """Dim hidden layers in the slider panel's own tree (not the model tree)."""
        n = self._tree.topLevelItemCount()
        for i in range(n):
            item  = self._tree.topLevelItem(i)
            idx   = item.data(0, QtCore.Qt.UserRole)
            vis   = (slider_value < 0) or (idx <= slider_value)
            color = QtGui.QColor("#e0e0e0") if vis else QtGui.QColor("#555555")
            for col in range(2):
                item.setForeground(col, QtGui.QBrush(color))
            font = item.font(0)
            font.setStrikeOut(not vis)
            item.setFont(0, font)

    # ── Qt overrides ───────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self.reset_all()
        super().closeEvent(event)
