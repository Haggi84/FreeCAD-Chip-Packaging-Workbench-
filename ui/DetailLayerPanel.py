"""
DetailLayerPanel
================
Dock panel for the LOD-controlled GDS workflow.

Each routing layer has three states:

  SOLID   ▣  Not yet loaded — IC_Body_Solid bridges the stack.
              Click "Load" to start the background import.
  LOADING ⟳  Tessellation running in the background. Row is greyed out.
  DETAIL  ◉  Full geometry visible.

PIN/bonding layers and COMP always arrive from the import as DETAIL.
Fill layers always remain SOLID (BBox — never fully loaded).

Frame extrusion (leadframe/substrate) is independent of this and
works as before.

Layout
------
  ┌─────────────────────────────────────────────────────────────────┐
  │  [Refresh]  [Load all]  [Reset all]                             │
  │  [Set Frame]  <template>  [Frames On]  [Frames Off]             │
  ├────────────┬──────────┬──────┬──────┬──────┬───────────────────┤
  │  Layer     │ Z-Range  │ State│ BBox │ Det. │ Frame             │
  ├────────────┼──────────┼──────┼──────┼──────┼───────────────────┤
  │ TopMetal2  │ 3.0–3.3  │  ◉   │  □   │  ◉   │  □                │
  │ Metal5     │ 1.8–2.0  │  ▣   │  □   │  ○   │  □  [Load]        │
  │ Metal4     │ 1.2–1.8  │  ⟳   │  □   │  ○   │  □                │
  └────────────┴──────────┴──────┴──────┴──────┴───────────────────┘

Modes
-----
  Cursor mode  — Z-slider controls which layer shows the detail view.
  Free mode    — each row is independently toggleable.
"""

import os
import FreeCAD
import FreeCADGui
import Part
from FreeCAD import Base
from compat import QtWidgets, QtCore, QtGui

try:
    from ui.LODManager import LODState
except ImportError:
    class LODState:  # type: ignore
        class SOLID:   value = "solid"
        class LOADING: value = "loading"
        class DETAIL:  value = "detail"

_LOD_BTN_WIDTH = 0   # no separate Load button anymore — LOD dot is clickable


# ── parallel frame worker (module-level so ThreadPoolExecutor can call it) ────

def _frame_worker(profile_brep: str, profile_z: float,
                  chip_union_brep: str | None, z0: float, z1: float):
    """
    Thread-safe computation of the encapsulant frame.

    Builds ONE solid from z0 (bottom of stack) to z1 (top of stack) by
    extruding the boundary profile, then subtracts the full chip union.
    This completely fills the void between chip edge and boundary —
    including gaps between layers and below the lowest layer.

    z0             — absolute Z of the bottom of the frame (e.g. 0.0 mm)
    z1             — absolute Z of the top  of the frame
    chip_union_brep — BREP of all chip shapes fused together, or None
    """
    try:
        pf = Part.Shape()
        pf.importBrepFromString(profile_brep)

        thickness = z1 - z0
        if thickness < 1e-9:
            return None

        # Move profile to z0 and extrude full height in one go
        moved = pf.copy()
        moved.translate(Base.Vector(0.0, 0.0, z0 - profile_z))
        frame = moved.extrude(Base.Vector(0.0, 0.0, thickness))

        # Subtract the entire chip geometry
        if chip_union_brep:
            chip = Part.Shape()
            chip.importBrepFromString(chip_union_brep)
            if not chip.isNull() and chip.Volume > 0:
                cut = frame.cut(chip)
                if not cut.isNull() and cut.isValid():
                    frame = cut

        return frame.exportBrepToString()
    except Exception:
        return None


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
    """
    One row: [●Color] [Name          ] [Z-Range  ] [●] [□BBox] [□Det] [□Frame]

    The colored LOD dot (●) is clickable:
      grey  ▸  not yet loaded → click starts loading
      amber ▸  being loaded (disabled)
      green ▸  loaded → click could e.g. re-render (future)

    No separate "Load" button anymore.
    """

    toggled           = QtCore.Signal(bool)
    simplify_toggled  = QtCore.Signal(bool)
    frame_toggled     = QtCore.Signal(bool)
    load_requested    = QtCore.Signal()          # Volume 3D (double-click)
    preview_requested = QtCore.Signal()          # Preview 2D (single-click when SOLID)

    COL_Z     = 110
    COL_LOD   = 0    # no dot anymore
    COL_BBOX  = 0   # column removed
    COL_DET   = 34
    COL_FRAME = 34



    def __init__(self, label: str, z0: float, z1: float,
                 color: tuple, is_simplified: bool = False,
                 lod_state: str = "detail",
                 is_body_solid: bool = False,
                 parent=None):
        super().__init__(parent)
        self._detail       = False
        self._simplified   = is_simplified
        self._lod_state    = lod_state
        self._is_body_solid = is_body_solid

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 1, 4, 1)
        lay.setSpacing(4)

        # ── Color swatch ──────────────────────────────────────────────────────
        swatch = QtWidgets.QLabel()
        swatch.setFixedSize(14, 14)
        r, g, b = (int(c * 255) for c in color)
        swatch.setStyleSheet(
            f"background: rgb({r},{g},{b}); border: 1px solid rgba(255,255,255,0.25);"
            f" border-radius: 2px;"
        )
        lay.addWidget(swatch)

        # ── Name ─────────────────────────────────────────────────────────────
        self._lbl_name = QtWidgets.QLabel(label)
        self._lbl_name.setStyleSheet("font-size: 10px; color: black;")
        self._lbl_name.setMinimumWidth(80)
        lay.addWidget(self._lbl_name, 1)

        # ── Z-Range ───────────────────────────────────────────────────────────
        self._lbl_z = QtWidgets.QLabel(f"{z0*1000:.1f} – {z1*1000:.1f} µm")
        self._lbl_z.setStyleSheet("font-size: 9px; color: black; font-family: monospace;")
        self._lbl_z.setFixedWidth(self.COL_Z)
        self._lbl_z.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        lay.addWidget(self._lbl_z)





        # ── Detail ────────────────────────────────────────────────────────────
        self._detail_cb = QtWidgets.QCheckBox()
        self._detail_cb.setChecked(False)
        self._detail_cb.setToolTip("Volume mode (full shading) / Wireframe")
        self._detail_cb.stateChanged.connect(self._on_detail_changed)
        _dc = QtWidgets.QWidget(); _dc.setFixedWidth(self.COL_DET)
        _dl = QtWidgets.QHBoxLayout(_dc); _dl.setContentsMargins(0,0,0,0)
        _dl.addWidget(self._detail_cb, 0, QtCore.Qt.AlignCenter)
        lay.addWidget(_dc)

        # ── Frame ─────────────────────────────────────────────────────────────
        self._frame_cb = QtWidgets.QCheckBox()
        self._frame_cb.setChecked(True)
        self._frame_cb.setEnabled(False)
        self._frame_cb.setToolTip(
            "Show/hide the substrate frame for this layer.\n"
            "Activated after selecting a frame template."
        )
        self._frame_cb.stateChanged.connect(self._on_frame_changed)
        _fc = QtWidgets.QWidget(); _fc.setFixedWidth(self.COL_FRAME)
        _fl = QtWidgets.QHBoxLayout(_fc); _fl.setContentsMargins(0,0,0,0)
        _fl.addWidget(self._frame_cb, 0, QtCore.Qt.AlignCenter)
        lay.addWidget(_fc)

        # Double-click on row: load when SOLID
        self.setToolTip("Double-click: load as Volume 3D\nSingle-click when in placeholder: load as Preview 2D")

        # Explicit white background — prevents FreeCAD theme from showing through
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QtGui.QColor("white"))
        self.setPalette(pal)

        # IC_Body_Solid gets special formatting
        if is_body_solid:
            self.setStyleSheet(
                "background: #0a2540; border-radius: 3px;"
            )
            self._lbl_name.setStyleSheet(
                "font-size: 10px; color: black; font-weight: bold;"
            )

    def set_lod_state(self, state: str):
        self._lod_state = state
        # Grey out name while loading
        if state == "loading":
            self._lbl_name.setStyleSheet("font-size: 10px; color: black;")
        elif not self._is_body_solid:
            col = "#e0f0ff" if self._detail else ""
            self._lbl_name.setStyleSheet(
                "font-size: 10px; color: black;"
                + ("font-weight: bold;" if self._detail else "")
            )

    def lod_state(self) -> str:
        return self._lod_state

    # ── Detail / BBox / Frame ─────────────────────────────────────────────────

    def set_detail(self, on: bool, silent: bool = False):
        self._detail = on
        self._detail_cb.blockSignals(True)
        self._detail_cb.setChecked(on)
        self._detail_cb.blockSignals(False)
        self._update_style(on)
        if not silent:
            self.toggled.emit(on)

    def is_detail(self) -> bool:   return self._detail
    def is_simplified(self) -> bool: return self._simplified

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
        self._simplified = (state == QtCore.Qt.Checked)
        self.simplify_toggled.emit(self._simplified)

    def _on_frame_changed(self, state: int):
        self.frame_toggled.emit(state == QtCore.Qt.Checked)

    def enable_frame(self, enabled: bool):
        self._frame_cb.setEnabled(enabled)

    def is_frame_visible(self) -> bool:
        return self._frame_cb.isChecked()

    def set_frame_visible(self, visible: bool, silent: bool = False):
        self._frame_cb.blockSignals(True)
        self._frame_cb.setChecked(visible)
        self._frame_cb.blockSignals(False)
        if not silent:
            self.frame_toggled.emit(visible)

    def mouseDoubleClickEvent(self, event):
        if self._lod_state in ("solid", "preview"):
            self.load_requested.emit()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        """Right-click menu: Preview / Volume / Reset."""
        menu = QtWidgets.QMenu(self)
        if self._lod_state == "solid":
            act_preview = menu.addAction("Load Preview  (2D, fast)")
            act_volume  = menu.addAction("Load Volume   (3D, slow)")
            chosen = menu.exec_(event.globalPos())
            if chosen == act_preview:
                self.preview_requested.emit()
            elif chosen == act_volume:
                self.load_requested.emit()
        elif self._lod_state == "preview":
            act_volume = menu.addAction("Load Volume  (upgrade to 3D)")
            chosen = menu.exec_(event.globalPos())
            if chosen == act_volume:
                self.load_requested.emit()
        elif self._lod_state == "detail":
            act_reset = menu.addAction("Reset to Placeholder")
            chosen = menu.exec_(event.globalPos())
            # reset is handled via simplify_toggled (BBox)
            if chosen == act_reset:
                self.simplify_toggled.emit(True)

    def _update_style(self, detail: bool):
        if self._is_body_solid:
            return
        color = "white" if detail else "black"
        self._lbl_name.setStyleSheet(
            f"font-size: 10px; color: {color};"
            + ("font-weight: bold;" if detail else "")
        )
        self._lbl_z.setStyleSheet(
            f"font-size: 9px; color: {color}; font-family: monospace;"
        )
        bg = "#0d2035" if detail else "transparent"
        self.setStyleSheet(f"background: {bg}; border-radius: 3px;")

    def highlight_cursor(self, on: bool):
        if self._is_body_solid:
            return
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
        super().__init__("Layer Control", parent)
        self.setObjectName("DetailLayerPanel")
        self.setMinimumWidth(400)
        self.setMinimumHeight(300)

        # internal state
        self._layers          = []   # list of (z0, z1, obj, label, color)
        self._rows            = []   # list of _LayerRow (top-down order)
        self._cursor_mode     = True
        self._cursor_idx      = -1
        self._frame_template  = None
        self._frame_objs: dict = {}
        self._lod_manager     = None   # set by _connect_lod_manager()

        self._build_ui()
        self.populate()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root_w  = QtWidgets.QWidget()
        root_l  = QtWidgets.QVBoxLayout(root_w)
        root_l.setContentsMargins(6, 6, 6, 6)
        root_l.setSpacing(4)

        # ── Toolbar ──────────────────────────────────────────────────────────
        tb = QtWidgets.QHBoxLayout()

        btn_refresh = QtWidgets.QPushButton("↺")
        btn_refresh.setFixedWidth(28)
        btn_refresh.setToolTip("Re-render: re-scan document and refresh layer list")
        btn_refresh.clicked.connect(self.populate)
        tb.addWidget(btn_refresh)

        _lbl_re = QtWidgets.QLabel("re-render")
        _lbl_re.setStyleSheet("font-size: 9px; color: #888;")
        tb.addWidget(_lbl_re)
        tb.addSpacing(8)

        btn_load_preview = QtWidgets.QPushButton("Load Preview")
        btn_load_preview.setToolTip(
            "Load all layers as fast 2D polygon preview.\n"
            "Shows real GDS geometry (pads, vias, traces) — no Z extrusion.\n"
            "Much faster than full Volume load."
        )
        btn_load_preview.clicked.connect(self._promote_all_preview)
        tb.addWidget(btn_load_preview)

        btn_load_all = QtWidgets.QPushButton("Load all")
        btn_load_all.setToolTip(
            "Load all routing layers as full 3D volumes.\n"
            "May take several minutes for complex chips."
        )
        btn_load_all.clicked.connect(self._promote_all)
        tb.addWidget(btn_load_all)

        btn_all_wire = QtWidgets.QPushButton("All Wireframe")
        btn_all_wire.setToolTip("Set all loaded layers to Wireframe")
        btn_all_wire.clicked.connect(self._set_all_wireframe)
        tb.addWidget(btn_all_wire)

        btn_all_det = QtWidgets.QPushButton("All Volume")
        btn_all_det.setToolTip("Set all loaded layers to Volume")
        btn_all_det.clicked.connect(self._set_all_detail)
        tb.addWidget(btn_all_det)

        tb.addStretch()
        root_l.addLayout(tb)

        # ── frame toolbar ─────────────────────────────────────────────────────
        ftb = QtWidgets.QHBoxLayout()

        btn_set_frame = QtWidgets.QPushButton("◎ Set Frame")
        btn_set_frame.setToolTip(
            "Select an object in the 3D view, then click here.\n"
            "Its XY face will be extruded per layer as a substrate frame."
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
        _hdr_sw.setFixedSize(14, 14)
        hdr_l.addWidget(_hdr_sw)
        # "Layer" (stretch)
        _lbl_layer = QtWidgets.QLabel("Layer")
        _lbl_layer.setStyleSheet("font-size: 9px; color: #aaa; font-weight: bold;")
        _lbl_layer.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        hdr_l.addWidget(_lbl_layer, 1)
        # Z-Range + LOD-Dot combined in a fixed width
        # (COL_Z + spacing + COL_LOD), so that the header aligns exactly above the row widgets
        _lbl_z = QtWidgets.QLabel("Z-Range (µm)")
        _lbl_z.setStyleSheet("font-size: 9px; color: #aaa; font-weight: bold;")
        _lbl_z.setFixedWidth(_LayerRow.COL_Z)
        _lbl_z.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        hdr_l.addWidget(_lbl_z)

        # "BBox" → "Simpli-fied" as shown in the screenshot

        # "Detail"
        _lbl_det = QtWidgets.QLabel("Volume")
        _lbl_det.setStyleSheet("font-size: 9px; color: #aaa; font-weight: bold;")
        _lbl_det.setFixedWidth(_LayerRow.COL_DET)
        _lbl_det.setAlignment(QtCore.Qt.AlignCenter)
        hdr_l.addWidget(_lbl_det)
        # "Substrate" (Frame)
        _lbl_frm = QtWidgets.QLabel("Substrate")
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

    def _restore_frame_state_from_doc(self, doc):
        """Re-populate _frame_objs and _frame_template from an already-open document.

        When the panel is closed and re-opened (or FreeCAD is restarted), the
        in-memory dicts are empty even though Encapsulant_Frame objects may
        already exist inside the Substrate_Frames group.  This method scans the
        document once and rebuilds the minimal state needed so that:
          • the frame checkboxes in each row are enabled, and
          • ChipTransformCommand._gds_objects() finds the frames via grp.Group.

        Frame objects are stored under a fixed sentinel key "_GLOBAL_FRAME_" so
        that the per-row lookup in _populate_from_* can find them without
        knowing the original template name.
        """
        frames_grp = next(
            (o for o in doc.Objects
             if o.Name == "Substrate_Frames" or o.Label == "Substrate Frames"),
            None,
        )
        if frames_grp is None:
            return

        for fo in getattr(frames_grp, "Group", []):
            if not (hasattr(fo, "Shape") and hasattr(fo, "Placement")):
                continue
            # Store under the sentinel key so _any_ per-row lookup that falls
            # back to this key will find the frame.
            self._frame_objs["_GLOBAL_FRAME_"] = fo
            FreeCAD.Console.PrintMessage(
                f"[DetailLayerPanel] Restored frame from document: '{fo.Name}'\\n"
            )

    def populate(self):
        """Scans the active document and rebuilds the rows.

        If a LODManager is available, rows are built from its complete layer
        list (all_layer_dicts) — this includes layers that have not yet been
        loaded and therefore have no FreeCAD object.  Without a LODManager
        the old scan path continues to run.
        """
        self._clear_rows()
        self._layers = []
        self._rows   = []

        doc = FreeCAD.activeDocument()
        if not doc:
            self._lbl_status.setText("No active document.")
            return

        # Recover frame objects that were created in a previous session so that
        # the frame checkboxes work and ChipTransform includes them immediately.
        self._restore_frame_state_from_doc(doc)

        self._connect_lod_manager(doc)

        if self._lod_manager is not None:
            self._populate_from_lod_manager(doc)
        else:
            self._populate_from_document(doc)

        zbar_layers = list(reversed(self._layers))
        self._zbar.set_stack([(z0, z1, o, l)
                               for z0, z1, o, l, _ in zbar_layers
                               if o is not None])

        if self._cursor_mode and self._rows:
            self._apply_cursor(0)

        self._update_status()
        FreeCAD.Console.PrintMessage(
            f"[DetailLayerPanel] {len(self._rows)} rows built.\n"
        )

    def _populate_from_lod_manager(self, doc):
        """
        Builds rows from the complete layer list of the LODManager.

        Order: IC_Body_Solid at the top (highlighted), then all layers
        from top to bottom (highest Z first).
        Layers without a FreeCAD object (not yet loaded) get obj=None.
        """
        ihp_map  = self._lod_manager._aux.get("ihp_map", {})
        stack_mm = self._lod_manager._aux.get("stack_mm") or {}

        # IC_Body_Solid no longer exists — placeholders take on this role

        for layer_dict in self._lod_manager.all_layer_dicts():
            key  = (layer_dict.get("layer_id", 0), layer_dict.get("datatype", 0))
            name = layer_dict.get("name", f"Layer_{key[0]}")

            z0, z1 = 0.0, 0.0
            if key in stack_mm:
                z0 = stack_mm[key].get("z0_mm", 0.0)
                z1 = z0 + stack_mm[key].get("t_mm", 0.0)

            obj = self._lod_manager._obj_map.get(key)
            if obj is None:
                obj_name = f"Layer_{name}_{key[0]}"
                obj = next((o for o in doc.Objects if o.Name == obj_name), None)

            # Color: from object or LYP fallback
            color = (0.4, 0.4, 0.7)
            if obj is not None:
                try:
                    c = obj.ViewObject.ShapeColor
                    color = (c[0], c[1], c[2])
                except Exception:
                    pass
            else:
                from core.Color import hex_to_rgb
                fc = layer_dict.get("fill-color", "#6666AA")
                try:
                    color = hex_to_rgb(fc)
                except Exception:
                    pass

            lod_state = self._lod_manager.state(key).value
            is_simp   = obj.Name in _simplified_shapes if obj else False
            label = f"{name}  ({key[0]}/{key[1]})"
            self._layers.append((z0, z1, obj, label, color))

            row = _LayerRow(label, z0, z1, color,
                            is_simplified=is_simp, lod_state=lod_state,
                            is_body_solid=False, parent=self._rows_widget)

            if obj is not None:
                row.toggled.connect(lambda on, o=obj: self._on_row_toggled(o, on))
                row.simplify_toggled.connect(
                    lambda on, o=obj: self._on_simplify_toggled(o, on))
                row.frame_toggled.connect(
                    lambda on, o=obj: self._on_frame_toggled(o, on))

            _key = key
            row.load_requested.connect(lambda k=_key: self._on_load_requested(k, preview_only=False))
            row.preview_requested.connect(lambda k=_key: self._on_load_requested(k, preview_only=True))

            frame_obj = (self._frame_objs.get(obj.Name if obj else "")
                         or self._frame_objs.get("_GLOBAL_FRAME_"))
            if frame_obj is not None:
                row.enable_frame(True)
                try:
                    row.set_frame_visible(frame_obj.ViewObject.Visibility, silent=True)
                except Exception:
                    pass

            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            self._rows.append(row)

    def _populate_from_document(self, doc):
        """Old scan path — used when no LODManager is available."""
        grp = _gds_group(doc)
        if grp is None:
            self._lbl_status.setText("No GDS_Die group — import a GDS file first.")
            return

        raw = []
        for obj in grp.Group:
            if not _has_geometry(obj):
                continue
            z0    = _z_min(obj)
            z1    = _z_max(obj)
            base_label = obj.Label or obj.Name
            # Append GDS layer ID and datatype if available
            lid = getattr(obj, "GDSLayerID",  None)
            dt  = getattr(obj, "GDSDatatype", None)
            label = f"{base_label}  ({lid}/{dt})" if lid is not None else base_label
            try:
                c     = obj.ViewObject.ShapeColor
                color = (c[0], c[1], c[2])
            except Exception:
                color = (0.4, 0.4, 0.7)
            raw.append((z0, z1, obj, label, color))

        if not raw:
            self._lbl_status.setText("GDS_Die group is empty.")
            return

        raw.sort(key=lambda t: -t[1])
        self._layers = raw
        doc_obj_names = {o.Name for o in doc.Objects}

        for z0, z1, obj, label, color in raw:
            is_simp = obj.Name in _simplified_shapes
            row = _LayerRow(label, z0, z1, color, is_simplified=is_simp,
                            lod_state="detail", parent=self._rows_widget)
            row.toggled.connect(lambda on, o=obj: self._on_row_toggled(o, on))
            row.simplify_toggled.connect(
                lambda on, o=obj: self._on_simplify_toggled(o, on))
            row.frame_toggled.connect(
                lambda on, o=obj: self._on_frame_toggled(o, on))

            frame_obj = (self._frame_objs.get(obj.Name)
                         or self._frame_objs.get("_GLOBAL_FRAME_"))
            if frame_obj is not None and frame_obj.Name in doc_obj_names:
                row.enable_frame(True)
                try:
                    row.set_frame_visible(frame_obj.ViewObject.Visibility, silent=True)
                except Exception:
                    pass

            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            self._rows.append(row)

    def _connect_lod_manager(self, doc):
        try:
            from ui.LODManager import get_lod_manager
            mgr = get_lod_manager(doc)
        except Exception:
            mgr = None
        if mgr is None or mgr is self._lod_manager:
            return
        self._lod_manager = mgr
        mgr.state_changed.connect(self._on_lod_state_changed)
        mgr.body_hidden.connect(self._update_status)
        FreeCAD.Console.PrintMessage("[DetailLayerPanel] LOD manager connected.\n")

    # ── Slots ──────────────────────────────────────────────────────────────────

    def _on_mode_changed(self, _):
        self._cursor_mode = self._rb_cursor.isChecked()
        self._zbar.setVisible(self._cursor_mode)
        if self._cursor_mode and self._cursor_idx >= 0:
            self._apply_cursor(self._cursor_idx)
        self._update_status()

    # ── LOD-Slots ─────────────────────────────────────────────────────────────

    def _on_load_requested(self, layer_key: tuple, preview_only: bool = False):
        """
        User triggers loading — delegates to LOD manager.

        preview_only=True  → fast 2D polygon preview
        preview_only=False → full 3D geometry (default on double-click)
        """
        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        self._connect_lod_manager(doc)
        if self._lod_manager is None:
            QtWidgets.QMessageBox.warning(
                self, "No LOD Manager",
                "No LOD manager for this document.\n"
                "Please re-import the GDS file."
            )
            return
        self._set_row_lod_state(layer_key, "loading")
        self._lod_manager.promote(layer_key, preview_only=preview_only)

    def _on_lod_state_changed(self, layer_key: tuple, state_value: str):
        """Callback from the LOD manager — updates the row display."""
        self._set_row_lod_state(layer_key, state_value)
        self._update_status()

    def _set_row_lod_state(self, layer_key: tuple, state: str):
        """Sets the LOD indicator of the matching row based on the layer key."""
        if self._lod_manager is None:
            return
        all_dicts = self._lod_manager.all_layer_dicts()
        for row_idx, layer_dict in enumerate(all_dicts):
            key = (layer_dict.get("layer_id", 0), layer_dict.get("datatype", 0))
            if key == layer_key and row_idx < len(self._rows):
                self._rows[row_idx].set_lod_state(state)
                return

    def _promote_all(self):
        """Loads all layers that have not yet been loaded (button 'Load all')."""
        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        self._connect_lod_manager(doc)
        if self._lod_manager is not None:
            self._lod_manager.promote_all()
        else:
            # Fallback: set all to detail (old mode without LOD manager)
            self._set_all_detail()

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
        action = "Volume" if detail else "Wireframe"
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

    def _promote_all_preview(self):
        """Fast 2D polygon preview for all layers not yet loaded."""
        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        self._connect_lod_manager(doc)
        if self._lod_manager is not None:
            self._lod_manager.promote_all(preview_only=True)
        else:
            self._set_all_detail()

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

        # ── Phase 1: serialise all inputs on the main thread ─────────────────
        # Shapes are exported to BREP strings so each worker thread owns an
        # independent OCCT object — no shared mutable state, no crashes.
        profile_brep = profile.exportBrepToString()

        # ── Chip union ────────────────────────────────────────────────────────
        # Fuse all loaded layer shapes into one solid to subtract from frame
        chip_shapes = []
        for _z0, _z1, _obj, _label, _color in self._layers:
            if _obj is None:
                continue
            _ls = getattr(_obj, "Shape", None)
            if _ls and not _ls.isNull() and _ls.Volume > 0:
                chip_shapes.append(_ls.copy())

        chip_union_brep = None
        if chip_shapes:
            try:
                _union = chip_shapes[0] if len(chip_shapes) == 1                          else chip_shapes[0].fuse(chip_shapes[1:])
                if not _union.isNull():
                    chip_union_brep = _union.exportBrepToString()
                    FreeCAD.Console.PrintMessage(
                        f"[FrameExtrusion] Chip union: {len(chip_shapes)} shapes.\n"
                    )
            except Exception as _fe:
                FreeCAD.Console.PrintWarning(
                    f"[FrameExtrusion] Chip union failed: {_fe}\n"
                )

        # ── Z extents: full boundary profile height ───────────────────────────
        # One single encapsulant solid from stack bottom to top.
        # profile_z is the Z of the profile face; we extrude downward to 0
        # and upward to the top of the highest layer.
        z_bottom = 0.0   # substrate floor
        z_top    = profile_z  # start from profile face Z as reference
        for _z0, _z1, _obj, _label, _color in self._layers:
            z_bottom = min(z_bottom, _z0)
            z_top    = max(z_top,    _z1)
        # Make sure z_top > z_bottom
        if z_top <= z_bottom:
            z_top = z_bottom + 0.001

        # ── Phase 2: single frame computation (one worker) ────────────────────
        progress = self._make_progress("Computing encapsulant frame…", 1)
        QtWidgets.QApplication.processEvents()

        from concurrent.futures import ThreadPoolExecutor, as_completed
        result_brep = None
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(
                _frame_worker,
                profile_brep, profile_z, chip_union_brep, z_bottom, z_top,
            )
            try:
                result_brep = fut.result()
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"[FrameExtrusion] frame computation failed: {exc}\n"
                )

        progress.setValue(1)
        progress.close()

        # ── Phase 3: apply single frame shape to document ─────────────────────
        try:
            doc.openTransaction("Build Encapsulant Frame")
        except Exception:
            pass

        if result_brep is None:
            FreeCAD.Console.PrintWarning("[FrameExtrusion] No frame shape produced.\n")
        else:
            frame_shape = Part.Shape()
            frame_shape.importBrepFromString(result_brep)

            # Single shared frame object — keyed by template name
            frame_key = f"__frame__{self._frame_template.Name}"
            doc_obj_names = {o.Name for o in doc.Objects}
            existing = self._frame_objs.get(frame_key)
            if existing is not None and existing.Name in doc_obj_names:
                existing.Shape = frame_shape
                frame_obj = existing
            else:
                frame_obj = doc.addObject("Part::Feature", "Encapsulant_Frame")
                frame_obj.Label = "Encapsulant Frame"
                try:
                    frame_obj.ViewObject.ShapeColor   = (0.55, 0.55, 0.62)
                    frame_obj.ViewObject.LineColor    = (0.25, 0.25, 0.30)
                    frame_obj.ViewObject.Transparency = 50
                except Exception:
                    pass
                if grp is not None:
                    try:
                        grp.addObject(frame_obj)
                    except Exception:
                        pass

            frame_obj.Shape = frame_shape
            self._frame_objs[frame_key] = frame_obj

            # Enable frame checkbox on all rows
            for row in self._rows:
                row.enable_frame(True)
                row.set_frame_visible(True, silent=True)

            FreeCAD.Console.PrintMessage(
                f"[FrameExtrusion] Encapsulant frame: "
                f"Z={z_bottom:.4f}–{z_top:.4f} mm  "
                f"chip cutout: {len(chip_shapes)} shape(s)\n"
            )

        try:
            doc.commitTransaction()
        except Exception:
            pass

        doc.recompute()
        FreeCADGui.updateGui()

    def _on_frame_toggled(self, obj, visible: bool):
        """Show / hide the encapsulant frame (single shared object)."""
        # Single frame object — toggle it regardless of which row triggered
        for frame_obj in self._frame_objs.values():
            try:
                frame_obj.ViewObject.Visibility = visible
            except Exception:
                pass
        FreeCADGui.updateGui()

    def _set_all_frames(self, visible: bool):
        """Show or hide the encapsulant frame."""
        for frame_obj in self._frame_objs.values():
            try:
                frame_obj.ViewObject.Visibility = visible
            except Exception:
                pass
        for i, row in enumerate(self._rows):
            row.set_frame_visible(visible, silent=True)
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

        # LOD statistics from the manager
        if self._lod_manager is not None:
            n_solid   = len(self._lod_manager.pending_keys())
            n_loading = len(self._lod_manager.loading_keys())
            lod_txt = ""
            if n_loading:
                lod_txt = f"  |  ⟳ {n_loading} loading"
            elif n_solid:
                lod_txt = f"  |  ▣ {n_solid} not loaded"
            else:
                lod_txt = "  |  ◉ all layers loaded"
            self._lbl_status.setText(
                f"Mode: {mode}  |  {n_det}/{n_tot} Volume{simp_txt}{lod_txt}"
            )
        else:
            self._lbl_status.setText(
                f"Mode: {mode}  |  {n_det}/{n_tot} Detail{simp_txt}"
            )

    def closeEvent(self, event):
        super().closeEvent(event)
