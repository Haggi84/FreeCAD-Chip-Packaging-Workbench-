"""
Wire Bump Configurator
======================
Dialog that lets the user:
  1. Choose a bump shape (Ball, Wedge, Stitch, Nail Head).
  2. Adjust the shape's geometric parameters.
  3. Preview the cross-section schematic with dimension annotations.
  4. Select connections from the netlist browser.
  5. Place bumps at both endpoints of every selected connection.

Bump objects are named  WireBump_<Shape>_NNN  and carry
  IsWireBump = True
  BumpShape  = <shape name>
so they can be identified later.
"""

import FreeCAD
import FreeCADGui
import Part
from PySide2 import QtWidgets, QtCore, QtGui

import os
import sys
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)
from Get_Path import get_icon


# ── colour ─────────────────────────────────────────────────────────────────────
_COLOR_BUMP = (0.90, 0.75, 0.20)   # gold — matches wire colour


# ── bump-shape catalogue ───────────────────────────────────────────────────────

BUMP_SHAPES = {
    "Ball Bond": {
        "description": "Spherical ball bond — typical first bond on die pad",
        "params": {
            "Diameter (mm)": dict(key="diameter", default=0.050, lo=0.005, hi=0.500, step=0.005),
            "Height (mm)":   dict(key="height",   default=0.035, lo=0.005, hi=0.300, step=0.005),
        },
    },
    "Wedge Bond": {
        "description": "Triangular-prism wedge — typical second bond on leadframe / housing pin",
        "params": {
            "Width (mm)":  dict(key="width",  default=0.060, lo=0.005, hi=0.500, step=0.005),
            "Length (mm)": dict(key="length", default=0.090, lo=0.005, hi=0.500, step=0.005),
            "Height (mm)": dict(key="height", default=0.020, lo=0.005, hi=0.200, step=0.005),
        },
    },
    "Stitch Bond": {
        "description": "Flat circular stitch — used in ball-wedge or stitch-stitch processes",
        "params": {
            "Diameter (mm)": dict(key="diameter", default=0.070, lo=0.005, hi=0.500, step=0.005),
            "Height (mm)":   dict(key="height",   default=0.010, lo=0.001, hi=0.100, step=0.001),
        },
    },
    "Nail Head": {
        "description": "Truncated-cone (frustum) nail-head bond",
        "params": {
            "Base diameter (mm)": dict(key="base_d", default=0.080, lo=0.010, hi=0.600, step=0.005),
            "Top diameter (mm)":  dict(key="top_d",  default=0.040, lo=0.005, hi=0.400, step=0.005),
            "Height (mm)":        dict(key="height", default=0.040, lo=0.005, hi=0.300, step=0.005),
        },
    },
}


# ── geometry builders ──────────────────────────────────────────────────────────

def _build_bump_shape(shape_name: str, params: dict) -> Part.Shape:
    """Return a Part.Shape for *shape_name* with the given *params*, centred at origin."""
    if shape_name == "Ball Bond":
        d = params.get("diameter", 0.05)
        h = params.get("height",   0.035)
        r = d / 2.0
        # Sphere scaled in Z so its actual height == h
        sphere = Part.makeSphere(r)
        mat = FreeCAD.Matrix()
        mat.scale(1.0, 1.0, h / max(d, 1e-9))
        shape = sphere.transformGeometry(mat)
        # Translate so the base (ZMin) sits at Z = 0
        shape.translate(FreeCAD.Vector(0, 0, -shape.BoundBox.ZMin))
        return shape

    if shape_name == "Wedge Bond":
        w = params.get("width",  0.06)
        l = params.get("length", 0.09)
        h = params.get("height", 0.02)
        # Triangular prism: triangle in XZ plane, extruded in Y
        v1 = FreeCAD.Vector(-w / 2, 0, 0)
        v2 = FreeCAD.Vector( w / 2, 0, 0)
        v3 = FreeCAD.Vector(0,       0, h)
        e1 = Part.Edge(Part.LineSegment(v1, v2).toShape())
        e2 = Part.Edge(Part.LineSegment(v2, v3).toShape())
        e3 = Part.Edge(Part.LineSegment(v3, v1).toShape())
        face = Part.Face(Part.Wire([e1, e2, e3]))
        prism = face.extrude(FreeCAD.Vector(0, l, 0))
        prism.translate(FreeCAD.Vector(0, -l / 2, 0))
        return prism

    if shape_name == "Stitch Bond":
        d = params.get("diameter", 0.07)
        h = params.get("height",   0.01)
        return Part.makeCylinder(d / 2, h)

    if shape_name == "Nail Head":
        r1 = params.get("base_d", 0.08) / 2.0
        r2 = params.get("top_d",  0.04) / 2.0
        h  = params.get("height", 0.04)
        return Part.makeCone(r1, r2, h)

    # Fallback: small sphere
    return Part.makeSphere(0.03)


def _place_bump(doc, position: FreeCAD.Vector, shape_name: str,
                params: dict, index: int):
    """Create a bump solid in *doc* at *position* and return it."""
    try:
        geom = _build_bump_shape(shape_name, params)
        geom.translate(position)
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"WireBump: geometry failed: {exc}\n")
        return None

    safe_name = shape_name.replace(" ", "")[:3]
    obj = doc.addObject("Part::Feature", f"WireBump_{safe_name}_{index:03d}")
    obj.Shape = geom

    obj.ViewObject.ShapeColor = _COLOR_BUMP

    def _prop(ptype, name, grp):
        if not hasattr(obj, name):
            obj.addProperty(ptype, name, grp, "")

    _prop("App::PropertyBool",   "IsWireBump", "Wirebond")
    _prop("App::PropertyString", "BumpShape",  "Wirebond")
    obj.IsWireBump = True
    obj.BumpShape  = shape_name
    return obj


def _next_bump_index(doc) -> int:
    return sum(1 for o in doc.Objects if getattr(o, "IsWireBump", False)) + 1


# ── schematic preview widget ───────────────────────────────────────────────────

class _PreviewWidget(QtWidgets.QWidget):
    """
    Draws a 2-D cross-section schematic of the selected bump shape.
    Pad surface = horizontal line at ~75 % height; bump drawn above.
    Dimension annotations shown on the right.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(220, 160)
        self._shape  = "Ball Bond"
        self._params = {}

    def set_shape(self, shape: str, params: dict):
        self._shape  = shape
        self._params = dict(params)
        self.update()

    # -- paint -----------------------------------------------------------------

    def paintEvent(self, _event):
        p   = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        p.fillRect(0, 0, w, h, QtGui.QColor(30, 30, 30))

        pad_y   = int(h * 0.75)
        cx      = w // 2
        scale_u = min(w, h) * 0.55     # pixels per mm for bump unit

        gold  = QtGui.QColor(230, 190, 50)
        grey  = QtGui.QColor(160, 160, 160)
        white = QtGui.QColor(220, 220, 220)
        dim_c = QtGui.QColor(100, 180, 255)

        # Pad surface
        p.setPen(QtGui.QPen(grey, 2))
        p.drawLine(10, pad_y, w - 10, pad_y)
        p.setPen(QtGui.QPen(grey, 1))
        p.setFont(QtGui.QFont("monospace", 7))
        p.drawText(10, pad_y + 12, "pad surface")

        shape = self._shape
        pr    = self._params

        # ── Ball Bond ──────────────────────────────────────────────────────────
        if shape == "Ball Bond":
            d = pr.get("diameter", 0.05)
            h_b = pr.get("height",   0.035)
            rx  = max(4, int(d   * scale_u / 2))
            ry  = max(3, int(h_b * scale_u))
            p.setBrush(QtGui.QBrush(gold))
            p.setPen(QtGui.QPen(gold.lighter(140), 1))
            p.drawEllipse(cx - rx, pad_y - 2 * ry, 2 * rx, 2 * ry)
            # dimension d
            self._draw_dim_h(p, cx - rx, cx + rx, pad_y - 2 * ry - 8, dim_c,
                             f"d={d:.3f}")
            # dimension h
            self._draw_dim_v(p, cx + rx + 6, pad_y, pad_y - 2 * ry, dim_c,
                             f"h={h_b:.3f}")

        # ── Wedge Bond ─────────────────────────────────────────────────────────
        elif shape == "Wedge Bond":
            ww  = pr.get("width",  0.06)
            h_b = pr.get("height", 0.02)
            px  = max(5, int(ww  * scale_u / 2))
            py  = max(3, int(h_b * scale_u))
            pts = [QtCore.QPoint(cx - px, pad_y),
                   QtCore.QPoint(cx + px, pad_y),
                   QtCore.QPoint(cx,       pad_y - py)]
            p.setBrush(QtGui.QBrush(gold))
            p.setPen(QtGui.QPen(gold.lighter(140), 1))
            p.drawPolygon(QtGui.QPolygon(pts))
            self._draw_dim_h(p, cx - px, cx + px, pad_y - py - 8, dim_c,
                             f"w={ww:.3f}")
            self._draw_dim_v(p, cx + px + 6, pad_y, pad_y - py, dim_c,
                             f"h={h_b:.3f}")

        # ── Stitch Bond ────────────────────────────────────────────────────────
        elif shape == "Stitch Bond":
            d   = pr.get("diameter", 0.07)
            h_b = pr.get("height",   0.01)
            rx  = max(5, int(d   * scale_u / 2))
            ry  = max(2, int(h_b * scale_u))
            ry  = max(ry, 4)   # keep it visible
            p.setBrush(QtGui.QBrush(gold))
            p.setPen(QtGui.QPen(gold.lighter(140), 1))
            p.drawRect(cx - rx, pad_y - ry, 2 * rx, ry)
            self._draw_dim_h(p, cx - rx, cx + rx, pad_y - ry - 8, dim_c,
                             f"d={d:.3f}")
            self._draw_dim_v(p, cx + rx + 6, pad_y, pad_y - ry, dim_c,
                             f"h={h_b:.3f}")

        # ── Nail Head ──────────────────────────────────────────────────────────
        elif shape == "Nail Head":
            r1  = pr.get("base_d", 0.08) / 2
            r2  = pr.get("top_d",  0.04) / 2
            h_b = pr.get("height", 0.04)
            px1 = max(5, int(r1  * scale_u))
            px2 = max(3, int(r2  * scale_u))
            py  = max(4, int(h_b * scale_u))
            pts = [QtCore.QPoint(cx - px1, pad_y),
                   QtCore.QPoint(cx + px1, pad_y),
                   QtCore.QPoint(cx + px2, pad_y - py),
                   QtCore.QPoint(cx - px2, pad_y - py)]
            p.setBrush(QtGui.QBrush(gold))
            p.setPen(QtGui.QPen(gold.lighter(140), 1))
            p.drawPolygon(QtGui.QPolygon(pts))
            self._draw_dim_h(p, cx - px1, cx + px1, pad_y + 14, dim_c,
                             f"base={pr.get('base_d',0.08):.3f}")
            self._draw_dim_h(p, cx - px2, cx + px2, pad_y - py - 8, dim_c,
                             f"top={pr.get('top_d',0.04):.3f}")
            self._draw_dim_v(p, cx + px1 + 6, pad_y, pad_y - py, dim_c,
                             f"h={h_b:.3f}")

        p.end()

    # -- dimension helpers -----------------------------------------------------

    @staticmethod
    def _draw_dim_h(p, x1, x2, y, color, label):
        """Horizontal dimension arrow."""
        p.setPen(QtGui.QPen(color, 1))
        p.drawLine(x1, y, x2, y)
        p.drawLine(x1, y - 3, x1, y + 3)
        p.drawLine(x2, y - 3, x2, y + 3)
        p.setFont(QtGui.QFont("monospace", 7))
        p.drawText((x1 + x2) // 2 - 20, y - 2, label)

    @staticmethod
    def _draw_dim_v(p, x, y1, y2, color, label):
        """Vertical dimension arrow."""
        p.setPen(QtGui.QPen(color, 1))
        p.drawLine(x, y1, x, y2)
        p.drawLine(x - 3, y1, x + 3, y1)
        p.drawLine(x - 3, y2, x + 3, y2)
        p.setFont(QtGui.QFont("monospace", 7))
        p.drawText(x + 4, (y1 + y2) // 2 + 4, label)


# ── main dialog ────────────────────────────────────────────────────────────────

class WireBumpConfiguratorDialog(QtWidgets.QDialog):
    """
    Wire Bump Configurator dialog.

    Left  : shape selector + parameter spinboxes
    Right : schematic preview
    Bottom: netlist browser (all BondWire objects in the active document)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wire Bump Configurator")
        self.resize(700, 580)
        self._spinboxes = {}   # label → QDoubleSpinBox

        self._build_ui()
        self._select_shape(list(BUMP_SHAPES)[0])
        self.refresh_netlist()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(8)

        # ── top row: shape + params (left) / preview (right) ──────────────────
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(10)

        # Left: shape + params
        left = QtWidgets.QGroupBox("Shape && Parameters")
        left.setFixedWidth(260)
        left_lay = QtWidgets.QVBoxLayout(left)

        self._shape_group = QtWidgets.QButtonGroup(self)
        for name in BUMP_SHAPES:
            rb = QtWidgets.QRadioButton(name)
            rb.toggled.connect(lambda checked, n=name: checked and self._select_shape(n))
            self._shape_group.addButton(rb)
            left_lay.addWidget(rb)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        left_lay.addWidget(sep)

        self._desc_lbl = QtWidgets.QLabel()
        self._desc_lbl.setWordWrap(True)
        self._desc_lbl.setStyleSheet("color: #aaa; font-size: 10px;")
        left_lay.addWidget(self._desc_lbl)

        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.HLine)
        left_lay.addWidget(sep2)

        self._param_widget = QtWidgets.QWidget()
        self._param_lay    = QtWidgets.QFormLayout(self._param_widget)
        self._param_lay.setSpacing(4)
        left_lay.addWidget(self._param_widget)
        left_lay.addStretch()
        top.addWidget(left)

        # Right: preview
        right = QtWidgets.QGroupBox("Cross-section Preview")
        right_lay = QtWidgets.QVBoxLayout(right)
        self._preview = _PreviewWidget()
        self._preview.setMinimumSize(240, 180)
        right_lay.addWidget(self._preview)
        right_lay.addStretch()
        top.addWidget(right, 1)

        root.addLayout(top)

        # ── netlist browser ────────────────────────────────────────────────────
        net_grp = QtWidgets.QGroupBox("Netlist — Bond Wire Connections")
        net_lay = QtWidgets.QVBoxLayout(net_grp)

        btn_row = QtWidgets.QHBoxLayout()
        sel_all  = QtWidgets.QPushButton("Select All")
        desel    = QtWidgets.QPushButton("Deselect All")
        refresh  = QtWidgets.QPushButton("Refresh")
        sel_all.clicked.connect(self._select_all)
        desel.clicked.connect(self._deselect_all)
        refresh.clicked.connect(self.refresh_netlist)
        btn_row.addWidget(sel_all)
        btn_row.addWidget(desel)
        btn_row.addWidget(refresh)
        btn_row.addStretch()
        net_lay.addLayout(btn_row)

        self._net_table = QtWidgets.QTableWidget()
        self._net_table.setColumnCount(5)
        self._net_table.setHorizontalHeaderLabels(
            ["", "Net", "Start Contact Point", "End Contact Point", "Length (mm)"]
        )
        self._net_table.horizontalHeader().setStretchLastSection(False)
        self._net_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.Stretch
        )
        self._net_table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.Stretch
        )
        self._net_table.setColumnWidth(0, 28)
        self._net_table.setColumnWidth(1, 80)
        self._net_table.setColumnWidth(4, 90)
        self._net_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._net_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._net_table.setAlternatingRowColors(True)
        self._net_table.setFixedHeight(160)
        net_lay.addWidget(self._net_table)

        root.addWidget(net_grp)

        # ── bottom buttons ─────────────────────────────────────────────────────
        btn_lay = QtWidgets.QHBoxLayout()
        self._place_btn = QtWidgets.QPushButton("Place Bumps on Selected Connections")
        self._place_btn.setStyleSheet(
            "QPushButton { background: #2a6496; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 3px; }"
            "QPushButton:hover { background: #3a74a6; }"
        )
        self._place_btn.clicked.connect(self._place_bumps)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_lay.addWidget(self._place_btn)
        btn_lay.addStretch()
        btn_lay.addWidget(close_btn)
        root.addLayout(btn_lay)

    # ── shape selection ────────────────────────────────────────────────────────

    def _select_shape(self, name: str):
        self._current_shape = name
        info = BUMP_SHAPES[name]
        self._desc_lbl.setText(info["description"])

        # Set radio button
        for rb in self._shape_group.buttons():
            if rb.text() == name:
                rb.setChecked(True)

        # Rebuild parameter spinboxes
        while self._param_lay.count():
            item = self._param_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._spinboxes.clear()

        for label, spec in info["params"].items():
            sb = QtWidgets.QDoubleSpinBox()
            sb.setRange(spec["lo"], spec["hi"])
            sb.setValue(spec["default"])
            sb.setSingleStep(spec["step"])
            sb.setDecimals(3)
            sb.valueChanged.connect(self._on_param_changed)
            self._spinboxes[spec["key"]] = sb
            self._param_lay.addRow(label, sb)

        self._on_param_changed()

    def _on_param_changed(self, _=None):
        params = {k: sb.value() for k, sb in self._spinboxes.items()}
        self._preview.set_shape(self._current_shape, params)

    def _current_params(self) -> dict:
        return {k: sb.value() for k, sb in self._spinboxes.items()}

    # ── netlist browser ────────────────────────────────────────────────────────

    def refresh_netlist(self):
        self._net_table.setRowCount(0)
        doc = FreeCAD.activeDocument()
        if not doc:
            return

        wires = [o for o in doc.Objects if o.Name.startswith("BondWire_")]
        self._wire_objects = wires   # keep reference for placement

        for row, obj in enumerate(wires):
            self._net_table.insertRow(row)

            chk = QtWidgets.QTableWidgetItem()
            chk.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            chk.setCheckState(QtCore.Qt.Unchecked)
            self._net_table.setItem(row, 0, chk)

            net  = getattr(obj, "NetName",   obj.Name)
            s_cp = getattr(obj, "StartCP",   "?")
            e_cp = getattr(obj, "EndCP",     "?")
            wlen = getattr(obj, "WireLength", 0.0)

            self._net_table.setItem(row, 1, QtWidgets.QTableWidgetItem(net))
            self._net_table.setItem(row, 2, QtWidgets.QTableWidgetItem(s_cp))
            self._net_table.setItem(row, 3, QtWidgets.QTableWidgetItem(e_cp))
            self._net_table.setItem(row, 4, QtWidgets.QTableWidgetItem(
                f"{float(wlen):.3f}"
            ))

        self._net_table.resizeRowsToContents()

    def _select_all(self):
        for row in range(self._net_table.rowCount()):
            self._net_table.item(row, 0).setCheckState(QtCore.Qt.Checked)

    def _deselect_all(self):
        for row in range(self._net_table.rowCount()):
            self._net_table.item(row, 0).setCheckState(QtCore.Qt.Unchecked)

    # ── bump placement ─────────────────────────────────────────────────────────

    def _place_bumps(self):
        doc = FreeCAD.activeDocument()
        if not doc:
            QtWidgets.QMessageBox.warning(self, "No document", "No active document.")
            return

        selected_rows = [
            row for row in range(self._net_table.rowCount())
            if self._net_table.item(row, 0).checkState() == QtCore.Qt.Checked
        ]
        if not selected_rows:
            QtWidgets.QMessageBox.information(
                self, "Nothing selected",
                "Please tick at least one connection in the netlist."
            )
            return

        shape  = self._current_shape
        params = self._current_params()
        placed = 0

        doc.openTransaction("Place Wire Bumps")
        try:
            for row in selected_rows:
                if row >= len(self._wire_objects):
                    continue
                wire_obj = self._wire_objects[row]

                for pt_attr in ("StartPoint", "EndPoint"):
                    raw = getattr(wire_obj, pt_attr, None)
                    if raw is None:
                        continue
                    try:
                        pos = FreeCAD.Vector(raw)
                    except Exception:
                        continue

                    idx  = _next_bump_index(doc)
                    bump = _place_bump(doc, pos, shape, params, idx)
                    if bump is not None:
                        placed += 1

            doc.commitTransaction()
            doc.recompute()
        except Exception as exc:
            doc.abortTransaction()
            QtWidgets.QMessageBox.critical(
                self, "Placement failed", str(exc)
            )
            return

        FreeCAD.Console.PrintMessage(
            f"[WireBump] {placed} bump(s) placed "
            f"({len(selected_rows)} connection(s), shape: {shape}).\n"
        )
        QtWidgets.QMessageBox.information(
            self, "Done",
            f"{placed} wire bump(s) placed on "
            f"{len(selected_rows)} connection(s)."
        )


# ── FreeCAD command ────────────────────────────────────────────────────────────

class WireBumpConfiguratorCommand:
    def GetResources(self):
        return {
            "MenuText": "Wire Bump Configurator",
            "ToolTip":  (
                "Configure bump shapes and place them at wire bond endpoints.  "
                "Select connections from the netlist and press 'Place Bumps'."
            ),
            "Pixmap": get_icon("Wire_bonding.png"),
        }

    def Activated(self):
        dlg = WireBumpConfiguratorDialog(FreeCADGui.getMainWindow())
        dlg.exec_()

    def IsActive(self):
        doc = FreeCAD.activeDocument()
        if not doc:
            return False
        return any(o.Name.startswith("BondWire_") for o in doc.Objects)


FreeCADGui.addCommand("WireBumpConfiguratorCommand", WireBumpConfiguratorCommand())
