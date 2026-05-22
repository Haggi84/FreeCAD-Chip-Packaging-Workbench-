"""
DetailLayerPanel
================
Dock panel that lets the user promote individual GDS layers from
Performance (Wireframe) to Detail (full shading) while all others stay
in fast Wireframe mode.

Layout
------
  ┌──────────────────────────────────────────────────────────────┐
  │  [Refresh]  [All Wireframe]  [All Detail]                    │
  ├──────────────────────────────────────────────────────────────┤
  │  Layer name          Z-bottom  Z-top   [D] (detail toggle)   │
  │  ─ sorted top-down ─                                         │
  │  ActiveMetal6        2.340     2.400   ◉                     │
  │  Via6                2.200     2.340   ○                     │
  │  ActiveMetal5        1.980     2.200   ○                     │
  │  …                                                           │
  ├──────────────────────────────────────────────────────────────┤
  │  Cursor at Z = 2.350 mm  │  3 / 24 layers in Detail mode    │
  └──────────────────────────────────────────────────────────────┘

The vertical Z-bar on the right (repurposed _SliderTrack) acts as a
height cursor: moving it highlights which Z-range is intersected and
auto-promotes that layer to Detail while demoting the previous one
(Single-layer cursor mode).  The user can also directly click the ◉/○
toggle column for free multi-layer selection.

Modes
-----
  Cursor mode  — slider controls which single layer is in Detail.
  Free mode    — every row's toggle is independent; slider is hidden.
"""

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore, QtGui


# ── re-use helpers from LayerSliderPanel ──────────────────────────────────────

def _gds_group(doc):
    if doc is None:
        return None
    for obj in doc.Objects:
        if obj.Name == "GDS_Die" or obj.Label == "GDS_Die":
            return obj
    return None


def _z_min(obj) -> float:
    for attr in ("Shape", "Mesh"):
        try:
            return float(getattr(obj, attr).BoundBox.ZMin)
        except Exception:
            pass
    return 0.0


def _z_max(obj) -> float:
    for attr in ("Shape", "Mesh"):
        try:
            return float(getattr(obj, attr).BoundBox.ZMax)
        except Exception:
            pass
    return _z_min(obj)


def _has_geometry(obj) -> bool:
    return (hasattr(obj, "Shape") and obj.Shape is not None) or \
           (hasattr(obj, "Mesh")  and obj.Mesh  is not None)


# ── Z-bar (vertical slider repurposed as height cursor) ──────────────────────

class _ZBar(QtWidgets.QWidget):
    """
    Thin vertical bar showing the full Z-range of the chip.
    A draggable cursor line marks the current Z height.
    Emits zChanged(float) when the cursor moves.
    """

    zChanged = QtCore.Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._z_min   = 0.0
        self._z_max   = 1.0
        self._z_cur   = 1.0          # starts at top
        self._layers  = []           # list of (z0, z1, label)
        self._dragging = False
        self.setMinimumWidth(40)
        self.setMinimumHeight(120)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed,
                           QtWidgets.QSizePolicy.Expanding)
        self.setCursor(QtCore.Qt.SizeVerCursor)

    def set_stack(self, layers):
        """layers: list of (z0, z1, obj, label) sorted bottom-up."""
        self._layers = layers
        if layers:
            self._z_min = layers[0][0]
            self._z_max = layers[-1][1]
            self._z_cur = self._z_max
        self.update()

    def z_cursor(self) -> float:
        return self._z_cur

    def set_z_cursor(self, z: float):
        z = max(self._z_min, min(self._z_max, z))
        if abs(z - self._z_cur) > 1e-9:
            self._z_cur = z
            self.update()
            self.zChanged.emit(z)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _track_rect(self) -> QtCore.QRect:
        m = 14
        return QtCore.QRect(8, m, self.width() - 16, self.height() - 2 * m)

    def _y_for_z(self, z: float) -> int:
        tr  = self._track_rect()
        rng = self._z_max - self._z_min
        if rng < 1e-9:
            return tr.bottom()
        frac = (self._z_max - z) / rng      # 0 = top, 1 = bottom
        return tr.top() + int(frac * tr.height())

    def _z_for_y(self, y: int) -> float:
        tr  = self._track_rect()
        rng = self._z_max - self._z_min
        if rng < 1e-9 or tr.height() == 0:
            return self._z_min
        frac = (y - tr.top()) / tr.height()
        frac = max(0.0, min(1.0, frac))
        return self._z_max - frac * rng

    # ── painting ─────────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p   = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        tr  = self._track_rect()

        # Background track
        p.setBrush(QtGui.QColor("#2b2b2b"))
        p.setPen(QtCore.Qt.NoPen)
        p.drawRoundedRect(tr, 4, 4)

        # Layer bands
        for z0, z1, _obj, _lbl in self._layers:
            y0 = self._y_for_z(z1)   # top of band
            y1 = self._y_for_z(z0)   # bottom of band
            band = QtCore.QRect(tr.x() + 2, y0, tr.width() - 4, max(1, y1 - y0))
            intersects = z0 <= self._z_cur <= z1
            col = QtGui.QColor("#1A99E6" if intersects else "#4a4a5a")
            p.setBrush(col)
            p.setPen(QtGui.QPen(QtGui.QColor("#666"), 0.5))
            p.drawRect(band)

        # Cursor line
        cy = self._y_for_z(self._z_cur)
        p.setPen(QtGui.QPen(QtGui.QColor("#ff9800"), 2))
        p.drawLine(tr.x(), cy, tr.right(), cy)

        # Z label
        p.setPen(QtGui.QColor("#ff9800"))
        p.setFont(QtGui.QFont("monospace", 7))
        label = f"{self._z_cur:.3f}"
        p.drawText(0, cy - 12, self.width(), 11,
                   QtCore.Qt.AlignHCenter, label)

        p.end()

    # ── mouse ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self.set_z_cursor(self._z_for_y(e.pos().y()))

    def mouseMoveEvent(self, e):
        if self._dragging:
            self.set_z_cursor(self._z_for_y(e.pos().y()))

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._dragging = False

    def wheelEvent(self, e):
        if not self._layers:
            return
        # Step to adjacent layer
        rng = self._z_max - self._z_min
        if rng < 1e-9:
            return
        step = rng / max(len(self._layers), 1)
        delta = step if e.angleDelta().y() > 0 else -step
        self.set_z_cursor(self._z_cur + delta)


# ── row widget for each layer ─────────────────────────────────────────────────

class _LayerRow(QtWidgets.QWidget):
    """One row: colour swatch · layer name · Z range · Detail toggle."""

    toggled = QtCore.Signal(bool)   # emitted when the detail toggle changes

    def __init__(self, label: str, z0: float, z1: float,
                 color: tuple, parent=None):
        super().__init__(parent)
        self._detail = False

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 1, 4, 1)
        lay.setSpacing(4)

        # colour swatch
        swatch = QtWidgets.QLabel()
        swatch.setFixedSize(12, 12)
        r, g, b = (int(c * 255) for c in color)
        swatch.setStyleSheet(
            f"background: rgb({r},{g},{b}); border: 1px solid #555; border-radius: 2px;"
        )
        lay.addWidget(swatch)

        # layer name
        self._lbl_name = QtWidgets.QLabel(label)
        self._lbl_name.setStyleSheet("font-size: 10px;")
        self._lbl_name.setMinimumWidth(90)
        lay.addWidget(self._lbl_name, 1)

        # Z range
        z_text = f"{z0:.3f} – {z1:.3f}"
        lbl_z = QtWidgets.QLabel(z_text)
        lbl_z.setStyleSheet("font-size: 9px; color: #888; font-family: monospace;")
        lbl_z.setFixedWidth(100)
        lbl_z.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        lay.addWidget(lbl_z)

        # Detail toggle button
        self._btn = QtWidgets.QPushButton("◉")
        self._btn.setCheckable(True)
        self._btn.setFixedWidth(28)
        self._btn.setFixedHeight(22)
        self._btn.setToolTip("Toggle: Detail (◉) / Wireframe (○)")
        self._btn.setStyleSheet(
            "QPushButton { font-size: 12px; border: 1px solid #555; border-radius: 3px; "
            "background: #2a2a3a; color: #aaa; }"
            "QPushButton:checked { background: #1A4A7A; color: #1A99E6; border-color: #1A99E6; }"
        )
        self._btn.clicked.connect(self._on_toggle)
        lay.addWidget(self._btn)

    def set_detail(self, on: bool, silent: bool = False):
        """Set detail state. If silent=True, don't emit toggled signal."""
        self._detail = on
        self._btn.blockSignals(True)
        self._btn.setChecked(on)
        self._btn.setText("◉" if on else "○")
        self._btn.blockSignals(False)
        self._update_style(on)
        if not silent:
            self.toggled.emit(on)

    def is_detail(self) -> bool:
        return self._detail

    def _on_toggle(self, checked: bool):
        self._detail = checked
        self._btn.setText("◉" if checked else "○")
        self._update_style(checked)
        self.toggled.emit(checked)

    def _update_style(self, detail: bool):
        col = "#e0f0ff" if detail else "#999999"
        self._lbl_name.setStyleSheet(
            f"font-size: 10px; color: {col}; "
            + ("font-weight: bold;" if detail else "")
        )
        bg = "#0d2035" if detail else "transparent"
        self.setStyleSheet(f"background: {bg}; border-radius: 3px;")

    def highlight_cursor(self, on: bool):
        """Visual highlight when the Z-cursor is inside this layer."""
        border = "border: 1px solid #ff9800;" if on else ""
        bg = "#0d2035" if self._detail else ("rgba(255,152,0,0.08)" if on else "transparent")
        self.setStyleSheet(f"background: {bg}; border-radius: 3px; {border}")


# ── main panel ────────────────────────────────────────────────────────────────

class DetailLayerPanel(QtWidgets.QDockWidget):
    """
    Dock panel: per-layer Detail/Wireframe toggle + Z-cursor mode.
    Sorted from top layer (highest Z) to bottom.
    """

    def __init__(self, parent=None):
        super().__init__("Detail Layer Control", parent)
        self.setObjectName("DetailLayerPanel")
        self.setMinimumWidth(340)
        self.setMinimumHeight(300)

        # internal state
        self._layers      = []       # list of (z0, z1, obj, label, color)
        self._rows        = []       # list of _LayerRow (top-down order)
        self._cursor_mode = True     # True = slider drives single-layer detail
        self._cursor_idx  = -1       # row index currently at cursor

        self._build_ui()
        self.populate()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root_w  = QtWidgets.QWidget()
        root_l  = QtWidgets.QVBoxLayout(root_w)
        root_l.setContentsMargins(6, 6, 6, 6)
        root_l.setSpacing(4)

        # ── toolbar ──────────────────────────────────────────────────────────
        tb = QtWidgets.QHBoxLayout()
        btn_refresh = QtWidgets.QPushButton("↺")
        btn_refresh.setFixedWidth(28)
        btn_refresh.setToolTip("Re-scan document for GDS layers")
        btn_refresh.clicked.connect(self.populate)
        tb.addWidget(btn_refresh)

        btn_all_wire = QtWidgets.QPushButton("All Wireframe")
        btn_all_wire.setToolTip("Set ALL layers to Wireframe (Performance)")
        btn_all_wire.clicked.connect(self._set_all_wireframe)
        tb.addWidget(btn_all_wire)

        btn_all_det = QtWidgets.QPushButton("All Detail")
        btn_all_det.setToolTip("Set ALL layers to Detail (full quality)")
        btn_all_det.clicked.connect(self._set_all_detail)
        tb.addWidget(btn_all_det)

        tb.addStretch()
        root_l.addLayout(tb)

        # ── mode selector ─────────────────────────────────────────────────────
        mode_box = QtWidgets.QGroupBox("Mode")
        mode_box.setStyleSheet("QGroupBox { font-size: 10px; }")
        mode_row = QtWidgets.QHBoxLayout(mode_box)
        mode_row.setContentsMargins(4, 2, 4, 2)
        self._rb_cursor = QtWidgets.QRadioButton("Z-Cursor  (single layer)")
        self._rb_free   = QtWidgets.QRadioButton("Free  (multi-layer)")
        self._rb_cursor.setChecked(True)
        self._rb_cursor.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self._rb_cursor)
        mode_row.addWidget(self._rb_free)
        root_l.addWidget(mode_box)

        # ── scroll area (rows) + Z-bar ────────────────────────────────────────
        body = QtWidgets.QHBoxLayout()

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        self._rows_widget  = QtWidgets.QWidget()
        self._rows_layout  = QtWidgets.QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(1)
        self._rows_layout.addStretch()
        scroll.setWidget(self._rows_widget)
        body.addWidget(scroll, 1)

        # Z-bar
        self._zbar = _ZBar()
        self._zbar.zChanged.connect(self._on_z_changed)
        body.addWidget(self._zbar)
        root_l.addLayout(body, 1)

        # ── status bar ────────────────────────────────────────────────────────
        self._lbl_status = QtWidgets.QLabel("")
        self._lbl_status.setStyleSheet(
            "color: #888; font-size: 9px; padding: 1px 2px;"
        )
        root_l.addWidget(self._lbl_status)

        self.setWidget(root_w)

    # ── public API ─────────────────────────────────────────────────────────────

    def populate(self):
        """Scan the active document for GDS layers and rebuild the row list."""
        self._clear_rows()
        self._layers = []
        self._rows   = []

        doc = FreeCAD.activeDocument()
        if not doc:
            self._lbl_status.setText("No active document.")
            return

        grp = _gds_group(doc)
        if grp is None:
            self._lbl_status.setText("No GDS_Die group — import a GDS first.")
            return

        raw = []
        for obj in grp.Group:
            if not _has_geometry(obj):
                continue
            z0    = _z_min(obj)
            z1    = _z_max(obj)
            label = obj.Label or obj.Name
            # Try to read stored ShapeColor as swatch colour
            try:
                c = obj.ViewObject.ShapeColor   # (r,g,b,a) floats 0..1
                color = (c[0], c[1], c[2])
            except Exception:
                color = (0.4, 0.4, 0.7)
            raw.append((z0, z1, obj, label, color))

        if not raw:
            self._lbl_status.setText("GDS_Die group is empty.")
            return

        # Sort top-down (highest Z first)
        raw.sort(key=lambda t: -t[1])
        self._layers = raw

        # Build rows (top-down = index 0 is the topmost layer)
        for z0, z1, obj, label, color in raw:
            row = _LayerRow(label, z0, z1, color, self._rows_widget)
            # Connect toggle → viewport update
            row.toggled.connect(lambda on, o=obj: self._on_row_toggled(o, on))
            # Insert before the stretch at end
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            self._rows.append(row)

        # Feed Z-bar (it needs bottom-up order)
        zbar_layers = list(reversed(raw))   # (z0,z1,obj,label)
        self._zbar.set_stack([(z0, z1, o, l) for z0, z1, o, l, _ in zbar_layers])

        # In cursor mode: promote the topmost layer immediately
        if self._cursor_mode and self._rows:
            self._apply_cursor(0)

        n = len(raw)
        self._update_status()
        FreeCAD.Console.PrintMessage(
            f"[DetailLayerPanel] {n} layer(s) loaded (top-down).\n"
        )

    # ── slots ──────────────────────────────────────────────────────────────────

    def _on_mode_changed(self, _):
        self._cursor_mode = self._rb_cursor.isChecked()
        # Show / hide Z-bar
        self._zbar.setVisible(self._cursor_mode)
        if self._cursor_mode:
            # Re-apply cursor to current position
            if self._cursor_idx >= 0:
                self._apply_cursor(self._cursor_idx)
        self._update_status()

    def _on_z_changed(self, z: float):
        """Z-cursor moved — find which layer row is at that Z and promote it."""
        if not self._cursor_mode or not self._layers:
            return
        # self._layers is top-down; find first row where z0 <= z <= z1
        for idx, (z0, z1, _obj, _lbl, _col) in enumerate(self._layers):
            if z0 <= z <= z1:
                self._apply_cursor(idx)
                return
        # z below all layers — promote bottommost
        self._apply_cursor(len(self._layers) - 1)

    def _on_row_toggled(self, obj, detail: bool):
        """Free-mode: user clicked a row toggle directly."""
        from gds.TogglePerformanceModeCommand import set_layer_detail
        label  = obj.Label or obj.Name
        action = "Detail" if detail else "Wireframe"
        dlg    = self._make_progress(f"Switching '{label}' to {action}…", 1)
        set_layer_detail(obj, detail)
        dlg.setValue(1)
        dlg.close()
        FreeCADGui.updateGui()
        self._update_status()

    # ── cursor-mode logic ─────────────────────────────────────────────────────

    def _apply_cursor(self, new_idx: int):
        """
        Promote row at new_idx to Detail, demote all others to Wireframe.
        Shows a progress dialog because every set_layer_detail call triggers
        an OCCT re-tessellation on the main thread.
        """
        from gds.TogglePerformanceModeCommand import set_layer_detail
        from compat import QtWidgets as _QW

        n   = len(self._layers)
        lbl = self._layers[new_idx][3] if new_idx < n else "?"
        dlg = self._make_progress(f"Detail: {lbl}  (0 / {n})", n)

        for idx, (z0, z1, obj, layer_lbl, _col) in enumerate(self._layers):
            want_detail = (idx == new_idx)
            action      = "Detail" if want_detail else "Wireframe"
            dlg.setLabelText(
                f"{'▶' if want_detail else '○'} {layer_lbl}  →  {action}"
                f"\n{idx + 1} / {n}"
            )
            dlg.setValue(idx)
            _QW.QApplication.processEvents()

            if idx < len(self._rows):
                self._rows[idx].set_detail(want_detail, silent=True)
                self._rows[idx].highlight_cursor(want_detail)
            set_layer_detail(obj, want_detail)

        dlg.setValue(n)
        dlg.close()

        self._cursor_idx = new_idx

        # Sync Z-bar cursor to centre of promoted layer
        z0, z1 = self._layers[new_idx][0], self._layers[new_idx][1]
        self._zbar.blockSignals(True)
        self._zbar.set_z_cursor((z0 + z1) / 2.0)
        self._zbar.blockSignals(False)
        self._zbar.update()

        FreeCADGui.updateGui()
        self._update_status()

    # ── batch actions ─────────────────────────────────────────────────────────

    def _set_all_wireframe(self):
        from gds.TogglePerformanceModeCommand import set_layer_detail
        n = len(self._layers)
        dlg = self._make_progress("Switching to Wireframe…", n)
        for idx, (_, _, obj, lbl, _) in enumerate(self._layers):
            dlg.setLabelText(f"Wireframe — {idx + 1} / {n}\n{lbl}")
            dlg.setValue(idx)
            from compat import QtWidgets as _QW
            _QW.QApplication.processEvents()
            set_layer_detail(obj, False)
            if idx < len(self._rows):
                self._rows[idx].set_detail(False, silent=True)
                self._rows[idx].highlight_cursor(False)
        dlg.close()
        self._cursor_idx = -1
        FreeCADGui.updateGui()
        self._update_status()

    def _set_all_detail(self):
        from gds.TogglePerformanceModeCommand import set_layer_detail
        n = len(self._layers)
        dlg = self._make_progress("Switching to Detail…", n)
        for idx, (_, _, obj, lbl, _) in enumerate(self._layers):
            dlg.setLabelText(f"Detail — {idx + 1} / {n}\n{lbl}")
            dlg.setValue(idx)
            from compat import QtWidgets as _QW
            _QW.QApplication.processEvents()
            set_layer_detail(obj, True)
            if idx < len(self._rows):
                self._rows[idx].set_detail(True, silent=True)
                self._rows[idx].highlight_cursor(False)
        dlg.close()
        FreeCADGui.updateGui()
        self._update_status()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _clear_rows(self):
        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows = []

    def _make_progress(self, label: str, n: int):
        """Non-cancelable progress dialog, shown immediately."""
        from compat import QtWidgets as _QW, QtCore as _QC
        dlg = _QW.QProgressDialog(
            label, None, 0, max(n, 1),
            FreeCADGui.getMainWindow(),
        )
        dlg.setWindowTitle("Rendering")
        dlg.setWindowModality(_QC.Qt.ApplicationModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setMinimumWidth(340)
        dlg.show()
        _QW.QApplication.processEvents()
        return dlg

    def _update_status(self):
        n_det  = sum(1 for r in self._rows if r.is_detail())
        n_tot  = len(self._rows)
        mode   = "Z-Cursor" if self._cursor_mode else "Free"
        self._lbl_status.setText(
            f"Mode: {mode}  |  {n_det} / {n_tot} layer(s) in Detail"
        )

    def closeEvent(self, event):
        super().closeEvent(event)
