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
import Part
from FreeCAD import Base
from compat import QtWidgets, QtCore, QtGui


# ── per-session simplification store ─────────────────────────────────────────
# Maps obj.Name -> original Part.Shape so we can restore after bbox swap.
_simplified_shapes: dict = {}


def _simplify_layer(obj) -> bool:
    """Replace obj's shape with its axis-aligned bounding box.  Returns True on success."""
    if obj.Name in _simplified_shapes:
        return True  # already simplified
    try:
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            return False
        bb = shape.BoundBox
        box = Part.makeBox(
            max(bb.XLength, 1e-6),
            max(bb.YLength, 1e-6),
            max(bb.ZLength, 1e-6),
            Base.Vector(bb.XMin, bb.YMin, bb.ZMin),
        )
        _simplified_shapes[obj.Name] = shape
        obj.Shape = box
        FreeCAD.Console.PrintMessage(f"[Simplify] {obj.Name}: full geometry → bounding box\n")
        return True
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[Simplify] {obj.Name}: {exc}\n")
        return False


def _restore_layer(obj) -> bool:
    """Restore obj's shape from the saved original.  Returns True on success."""
    if obj.Name not in _simplified_shapes:
        return False
    try:
        obj.Shape = _simplified_shapes.pop(obj.Name)
        FreeCAD.Console.PrintMessage(f"[Simplify] {obj.Name}: bounding box → full geometry\n")
        return True
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[Simplify] {obj.Name}: restore failed: {exc}\n")
        return False


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
    """One row: colour swatch · layer name · Z range · BBox · Detail · Frame checkboxes."""

    toggled          = QtCore.Signal(bool)   # detail toggle changed
    simplify_toggled = QtCore.Signal(bool)   # bbox/simplify toggle changed
    frame_toggled    = QtCore.Signal(bool)   # frame visibility changed

    # Fixed widths shared with the header row for column alignment
    COL_Z     = 100
    COL_BBOX  = 36
    COL_DET   = 36
    COL_FRAME = 36

    def __init__(self, label: str, z0: float, z1: float,
                 color: tuple, is_simplified: bool = False, parent=None):
        super().__init__(parent)
        self._detail     = False
        self._simplified = is_simplified

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
        lbl_z = QtWidgets.QLabel(f"{z0:.3f} – {z1:.3f}")
        lbl_z.setStyleSheet("font-size: 9px; color: #888; font-family: monospace;")
        lbl_z.setFixedWidth(self.COL_Z)
        lbl_z.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        lay.addWidget(lbl_z)

        # BBox checkbox — centred in a fixed-width cell so column aligns with header
        self._bbox_cb = QtWidgets.QCheckBox()
        self._bbox_cb.setChecked(is_simplified)
        self._bbox_cb.setToolTip(
            "Replace this layer's geometry with a bounding-box solid.\n"
            "Uncheck to restore the full geometry."
        )
        self._bbox_cb.stateChanged.connect(self._on_bbox_changed)
        _bbox_cell = QtWidgets.QWidget()
        _bbox_cell.setFixedWidth(self.COL_BBOX)
        _bbox_l = QtWidgets.QHBoxLayout(_bbox_cell)
        _bbox_l.setContentsMargins(0, 0, 0, 0)
        _bbox_l.addWidget(self._bbox_cb, 0, QtCore.Qt.AlignCenter)
        lay.addWidget(_bbox_cell)

        # Detail checkbox — centred in a fixed-width cell
        self._detail_cb = QtWidgets.QCheckBox()
        self._detail_cb.setChecked(False)
        self._detail_cb.setToolTip("Detail mode (full shading); unchecked = Wireframe")
        self._detail_cb.stateChanged.connect(self._on_detail_changed)
        _det_cell = QtWidgets.QWidget()
        _det_cell.setFixedWidth(self.COL_DET)
        _det_l = QtWidgets.QHBoxLayout(_det_cell)
        _det_l.setContentsMargins(0, 0, 0, 0)
        _det_l.addWidget(self._detail_cb, 0, QtCore.Qt.AlignCenter)
        lay.addWidget(_det_cell)

        # Frame visibility checkbox — enabled only after a frame extrusion exists
        self._frame_cb = QtWidgets.QCheckBox()
        self._frame_cb.setChecked(True)
        self._frame_cb.setEnabled(False)          # grayed out until frame is built
        self._frame_cb.setToolTip(
            "Show / hide the substrate frame extrusion for this layer.\n"
            "Activate by selecting a template object and clicking 'Set Frame'."
        )
        self._frame_cb.stateChanged.connect(self._on_frame_changed)
        _frame_cell = QtWidgets.QWidget()
        _frame_cell.setFixedWidth(self.COL_FRAME)
        _frame_l = QtWidgets.QHBoxLayout(_frame_cell)
        _frame_l.setContentsMargins(0, 0, 0, 0)
        _frame_l.addWidget(self._frame_cb, 0, QtCore.Qt.AlignCenter)
        lay.addWidget(_frame_cell)

    def set_detail(self, on: bool, silent: bool = False):
        self._detail = on
        self._detail_cb.blockSignals(True)
        self._detail_cb.setChecked(on)
        self._detail_cb.blockSignals(False)
        self._update_style(on)
        if not silent:
            self.toggled.emit(on)

    def is_detail(self) -> bool:
        return self._detail

    def is_simplified(self) -> bool:
        return self._simplified

    def set_simplified(self, on: bool, silent: bool = False):
        self._simplified = on
        self._bbox_cb.blockSignals(True)
        self._bbox_cb.setChecked(on)
        self._bbox_cb.blockSignals(False)
        if not silent:
            self.simplify_toggled.emit(on)

    def _on_detail_changed(self, state: int):
        checked = (state == QtCore.Qt.Checked)
        self._detail = checked
        self._update_style(checked)
        self.toggled.emit(checked)

    def _on_bbox_changed(self, state: int):
        checked = (state == QtCore.Qt.Checked)
        self._simplified = checked
        self.simplify_toggled.emit(checked)

    def _on_frame_changed(self, state: int):
        self.frame_toggled.emit(state == QtCore.Qt.Checked)

    def enable_frame(self, enabled: bool):
        """Enable or disable the Frame checkbox (enabled only when a frame object exists)."""
        self._frame_cb.setEnabled(enabled)

    def is_frame_visible(self) -> bool:
        return self._frame_cb.isChecked()

    def set_frame_visible(self, visible: bool, silent: bool = False):
        self._frame_cb.blockSignals(True)
        self._frame_cb.setChecked(visible)
        self._frame_cb.blockSignals(False)
        if not silent:
            self.frame_toggled.emit(visible)

    def _update_style(self, detail: bool):
        col = "#e0f0ff" if detail else "#999999"
        self._lbl_name.setStyleSheet(
            f"font-size: 10px; color: {col}; "
            + ("font-weight: bold;" if detail else "")
        )
        bg = "#0d2035" if detail else "transparent"
        self.setStyleSheet(f"background: {bg}; border-radius: 3px;")

    def highlight_cursor(self, on: bool):
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
        self.setMinimumWidth(400)
        self.setMinimumHeight(300)

        # internal state
        self._layers          = []   # list of (z0, z1, obj, label, color)
        self._rows            = []   # list of _LayerRow (top-down order)
        self._cursor_mode     = True # True = slider drives single-layer detail
        self._cursor_idx      = -1   # row index currently at cursor
        self._frame_template  = None # FreeCAD object whose XY face is the frame profile
        self._frame_objs: dict = {}  # layer obj.Name → FreeCAD frame Part::Feature

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

        # ── frame toolbar ─────────────────────────────────────────────────────
        ftb = QtWidgets.QHBoxLayout()

        btn_set_frame = QtWidgets.QPushButton("◎ Set Frame")
        btn_set_frame.setToolTip(
            "Select an object in the 3D view, then click here.\n"
            "Its XY face will be extruded for every layer as a substrate frame."
        )
        btn_set_frame.clicked.connect(self._pick_frame_template)
        ftb.addWidget(btn_set_frame)

        self._lbl_frame = QtWidgets.QLabel("No template")
        self._lbl_frame.setStyleSheet("font-size: 9px; color: #888; font-style: italic;")
        ftb.addWidget(self._lbl_frame, 1)

        btn_frames_on  = QtWidgets.QPushButton("Frames On")
        btn_frames_off = QtWidgets.QPushButton("Frames Off")
        btn_frames_on.setToolTip("Show all substrate frame extrusions")
        btn_frames_off.setToolTip("Hide all substrate frame extrusions")
        btn_frames_on.clicked.connect(lambda: self._set_all_frames(True))
        btn_frames_off.clicked.connect(lambda: self._set_all_frames(False))
        ftb.addWidget(btn_frames_on)
        ftb.addWidget(btn_frames_off)

        root_l.addLayout(ftb)

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

        # ── column header + scroll area (rows) + Z-bar ───────────────────────
        body = QtWidgets.QHBoxLayout()

        # Left panel: header + scroll
        left_panel = QtWidgets.QWidget()
        left_l     = QtWidgets.QVBoxLayout(left_panel)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(0)

        # Header row — column labels aligned with _LayerRow cells
        hdr = QtWidgets.QWidget()
        hdr.setStyleSheet(
            "background: #1a1a2e; border-bottom: 1px solid #444;"
        )
        hdr_l = QtWidgets.QHBoxLayout(hdr)
        hdr_l.setContentsMargins(4, 3, 4, 3)
        hdr_l.setSpacing(4)
        # swatch placeholder
        _hdr_sw = QtWidgets.QLabel()
        _hdr_sw.setFixedSize(12, 12)
        hdr_l.addWidget(_hdr_sw)
        # "Layer" (stretch)
        _lbl_layer = QtWidgets.QLabel("Layer")
        _lbl_layer.setStyleSheet("font-size: 9px; color: #aaa; font-weight: bold;")
        _lbl_layer.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        hdr_l.addWidget(_lbl_layer, 1)
        # "Z-Range (mm)" (fixed)
        _lbl_z = QtWidgets.QLabel("Z-Range (mm)")
        _lbl_z.setStyleSheet("font-size: 9px; color: #aaa; font-weight: bold;")
        _lbl_z.setFixedWidth(_LayerRow.COL_Z)
        _lbl_z.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        hdr_l.addWidget(_lbl_z)
        # "BBox" (fixed)
        _lbl_bbox = QtWidgets.QLabel("BBox")
        _lbl_bbox.setStyleSheet("font-size: 9px; color: #aaa; font-weight: bold;")
        _lbl_bbox.setFixedWidth(_LayerRow.COL_BBOX)
        _lbl_bbox.setAlignment(QtCore.Qt.AlignCenter)
        hdr_l.addWidget(_lbl_bbox)
        # "Detail" (fixed)
        _lbl_det = QtWidgets.QLabel("Detail")
        _lbl_det.setStyleSheet("font-size: 9px; color: #aaa; font-weight: bold;")
        _lbl_det.setFixedWidth(_LayerRow.COL_DET)
        _lbl_det.setAlignment(QtCore.Qt.AlignCenter)
        hdr_l.addWidget(_lbl_det)
        # "Frame" (fixed)
        _lbl_frm = QtWidgets.QLabel("Frame")
        _lbl_frm.setStyleSheet("font-size: 9px; color: #aaa; font-weight: bold;")
        _lbl_frm.setFixedWidth(_LayerRow.COL_FRAME)
        _lbl_frm.setAlignment(QtCore.Qt.AlignCenter)
        hdr_l.addWidget(_lbl_frm)
        left_l.addWidget(hdr)

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
        left_l.addWidget(scroll, 1)

        body.addWidget(left_panel, 1)

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
        doc_obj_names = {o.Name for o in doc.Objects}
        for z0, z1, obj, label, color in raw:
            is_simp = obj.Name in _simplified_shapes
            row = _LayerRow(label, z0, z1, color, is_simplified=is_simp,
                            parent=self._rows_widget)
            row.toggled.connect(lambda on, o=obj: self._on_row_toggled(o, on))
            row.simplify_toggled.connect(lambda on, o=obj: self._on_simplify_toggled(o, on))
            row.frame_toggled.connect(lambda on, o=obj: self._on_frame_toggled(o, on))
            # Restore frame checkbox state if a frame object already exists
            frame_obj = self._frame_objs.get(obj.Name)
            if frame_obj is not None and frame_obj.Name in doc_obj_names:
                row.enable_frame(True)
                try:
                    row.set_frame_visible(
                        frame_obj.ViewObject.Visibility, silent=True)
                except Exception:
                    pass
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

    def _on_simplify_toggled(self, obj, simplify: bool):
        """User clicked the BBox toggle for a layer row."""
        label = obj.Label or obj.Name
        if simplify:
            dlg = self._make_progress(f"Simplifying '{label}' to bounding box…", 1)
            ok  = _simplify_layer(obj)
            dlg.setValue(1)
            dlg.close()
            if not ok:
                QtWidgets.QMessageBox.warning(
                    self, "Simplify",
                    f"Could not simplify '{label}'.\n"
                    "The layer may not have a valid shape (e.g. mesh or 2D wire)."
                )
                # revert button state
                self._sync_simplify_btn(obj, False)
        else:
            dlg = self._make_progress(f"Restoring full geometry for '{label}'…", 1)
            ok  = _restore_layer(obj)
            dlg.setValue(1)
            dlg.close()
            if not ok:
                QtWidgets.QMessageBox.warning(
                    self, "Restore",
                    f"No saved shape found for '{label}'.\n"
                    "The original geometry was not stored (layer may not have been simplified here)."
                )
                self._sync_simplify_btn(obj, True)
        FreeCADGui.updateGui()
        self._update_status()

    def _sync_simplify_btn(self, obj, state: bool):
        """Force a row's BBox button to the given state without firing the signal."""
        for i, (_, _, o, _, _) in enumerate(self._layers):
            if o.Name == obj.Name and i < len(self._rows):
                self._rows[i].set_simplified(state, silent=True)
                break

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

    # ── frame extrusions ─────────────────────────────────────────────────────

    def _pick_frame_template(self):
        """Read current FreeCAD selection and use that object as the frame profile."""
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            QtWidgets.QMessageBox.warning(
                self, "No selection",
                "Select an object in the 3D view first, then click 'Set Frame'.\n"
                "The object's largest XY-parallel face becomes the substrate profile."
            )
            return
        obj = sel[0]
        if not hasattr(obj, "Shape"):
            QtWidgets.QMessageBox.warning(
                self, "Invalid selection",
                f"'{obj.Label}' has no Shape — select a solid or face object."
            )
            return
        profile = self._get_profile_face(obj.Shape)
        if profile is None:
            QtWidgets.QMessageBox.warning(
                self, "No profile found",
                f"Could not find an XY-parallel face in '{obj.Label}'.\n"
                "Make sure the object has a flat horizontal face."
            )
            return

        self._frame_template = obj
        self._lbl_frame.setText(obj.Label)
        self._lbl_frame.setStyleSheet("font-size: 9px; color: #88cc88;")
        FreeCAD.Console.PrintMessage(
            f"[FrameExtrusion] Template set to '{obj.Label}' "
            f"(face area = {profile.Area:.4f} mm²)\n"
        )
        self._build_frames()

    def _build_frames(self):
        """(Re-)create frame extrusions for every loaded layer from the current template."""
        if self._frame_template is None or not self._layers:
            return

        doc = FreeCAD.activeDocument()
        if doc is None:
            return

        profile = self._get_profile_face(self._frame_template.Shape)
        if profile is None:
            return

        profile_z = profile.BoundBox.ZMin

        # Create / reuse the group
        grp = next(
            (o for o in doc.Objects
             if o.Name == "Substrate_Frames" or o.Label == "Substrate Frames"),
            None,
        )
        if grp is None:
            try:
                grp = doc.addObject("App::DocumentObjectGroup", "Substrate_Frames")
                grp.Label = "Substrate Frames"
            except Exception:
                grp = None

        try:
            doc.openTransaction("Build Frame Extrusions")
        except Exception:
            pass

        n = len(self._layers)
        progress = self._make_progress(f"Building {n} frame extrusion(s)…", n)
        doc_obj_names = {o.Name for o in doc.Objects}

        for idx, (z0, z1, obj, label, _color) in enumerate(self._layers):
            progress.setLabelText(f"Frame {idx + 1} / {n}\n{label}")
            progress.setValue(idx)
            QtWidgets.QApplication.processEvents()

            thickness = z1 - z0
            if thickness < 1e-9:
                continue

            # Translate profile face to z0, then extrude upward
            moved = profile.copy()
            moved.translate(Base.Vector(0.0, 0.0, z0 - profile_z))
            try:
                frame_shape = moved.extrude(Base.Vector(0.0, 0.0, thickness))
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"[FrameExtrusion] extrude failed for '{label}': {exc}\n"
                )
                continue

            # Boolean-cut the layer's own geometry out of the frame so the
            # layer footprint appears as a hole through the substrate slab.
            layer_shape = getattr(obj, "Shape", None)
            if layer_shape is not None and not layer_shape.isNull():
                try:
                    cut = frame_shape.cut(layer_shape)
                    if not cut.isNull() and cut.isValid():
                        frame_shape = cut
                    else:
                        FreeCAD.Console.PrintWarning(
                            f"[FrameExtrusion] boolean cut produced invalid shape "
                            f"for '{label}' — keeping full frame\n"
                        )
                except Exception as exc:
                    FreeCAD.Console.PrintWarning(
                        f"[FrameExtrusion] boolean cut failed for '{label}': {exc}\n"
                    )

            # Update existing frame object or create a new one
            existing = self._frame_objs.get(obj.Name)
            if existing is not None and existing.Name in doc_obj_names:
                existing.Shape = frame_shape
                frame_obj = existing
            else:
                safe = "SubFrame_" + "".join(
                    c if (c.isalnum() or c == "_") else "_" for c in label
                )
                frame_obj       = doc.addObject("Part::Feature", safe)
                frame_obj.Label = f"SubFrame: {label}"
                try:
                    frame_obj.ViewObject.ShapeColor   = (0.55, 0.55, 0.62)
                    frame_obj.ViewObject.LineColor    = (0.25, 0.25, 0.30)
                    frame_obj.ViewObject.Transparency = 70
                except Exception:
                    pass
                if grp is not None:
                    try:
                        grp.addObject(frame_obj)
                    except Exception:
                        pass

            frame_obj.Shape = frame_shape
            self._frame_objs[obj.Name] = frame_obj

            # Enable row checkbox and mark visible
            if idx < len(self._rows):
                self._rows[idx].enable_frame(True)
                self._rows[idx].set_frame_visible(True, silent=True)

            FreeCAD.Console.PrintMessage(
                f"[FrameExtrusion] '{frame_obj.Label}'  "
                f"thickness={thickness:.3f} mm  Z={z0:.3f}–{z1:.3f}\n"
            )

        progress.setValue(n)
        progress.close()

        try:
            doc.commitTransaction()
        except Exception:
            pass

        doc.recompute()
        FreeCADGui.updateGui()

    def _on_frame_toggled(self, obj, visible: bool):
        """Show / hide the frame extrusion for one layer."""
        frame_obj = self._frame_objs.get(obj.Name)
        if frame_obj is None:
            return
        try:
            frame_obj.ViewObject.Visibility = visible
        except Exception:
            pass
        FreeCADGui.updateGui()

    def _set_all_frames(self, visible: bool):
        """Show or hide all frame extrusions at once."""
        for i, (_, _, obj, _, _) in enumerate(self._layers):
            frame_obj = self._frame_objs.get(obj.Name)
            if frame_obj is not None:
                try:
                    frame_obj.ViewObject.Visibility = visible
                except Exception:
                    pass
            if i < len(self._rows):
                self._rows[i].set_frame_visible(visible, silent=True)
        FreeCADGui.updateGui()

    @staticmethod
    def _get_profile_face(shape):
        """Return the largest XY-parallel face of *shape* (Z-normal ≈ ±1)."""
        best, best_area = None, -1.0
        for face in getattr(shape, "Faces", []):
            try:
                n = face.normalAt(0, 0)
                if abs(abs(n.z) - 1.0) < 0.02:
                    if face.Area > best_area:
                        best, best_area = face, face.Area
            except Exception:
                pass
        return best

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
        n_simp = sum(1 for r in self._rows if r.is_simplified())
        n_tot  = len(self._rows)
        mode   = "Z-Cursor" if self._cursor_mode else "Free"
        simp_txt = f"  |  {n_simp} BBox" if n_simp else ""
        self._lbl_status.setText(
            f"Mode: {mode}  |  {n_det} / {n_tot} in Detail{simp_txt}"
        )

    def closeEvent(self, event):
        super().closeEvent(event)
