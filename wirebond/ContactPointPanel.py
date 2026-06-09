"""
Contact Point Browser panel.

A dockable panel that lists all ContactPoint markers in the active document,
grouped by origin (Leadframe vs Die/GDS).  Hovering over an entry temporarily
highlights the corresponding marker in the 3D view; clicking it selects it.

Connected contact points (those referenced by at least one BondWire) are shown
greyed out in the tree.  A netlist table below the tree summarises all bonds.
"""

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore, QtGui


# Highlight colour applied while the mouse hovers over a tree row
_COLOR_HOVER = (0.10, 0.90, 0.10)   # bright green

# Foreground colours for connected vs. free contact points
_FG_CONNECTED = QtGui.QColor("#2FE60B")   # greyed out
_FG_FREE      = QtGui.QColor("#0f0101")   # normal


# ── helpers ────────────────────────────────────────────────────────────────────

def _is_leadframe_source(src: str) -> bool:
    """True when the SourceObject name belongs to leadframe geometry."""
    return src.startswith(("Lead_", "BGA_Ball_", "DiePaddle"))


def _is_housing_point(obj) -> bool:
    """True when the object is a housing contact point (contact_point_housing_*)."""
    return obj.Name.startswith("contact_point_housing_")


def _is_pcb_point(obj) -> bool:
    """True when the object is a PCB pad ContactPoint."""
    return (
        obj.Name.startswith("PCB_Pad_")
        or getattr(obj, "PadType", "") == "PCB_Pad"
        or getattr(obj, "SourceObject", "").startswith("PCB_")
    )


def _color_swatch(r_f, g_f, b_f, size: int = 12) -> QtGui.QIcon:
    """Return a solid-colour icon from 0–1 float RGB components."""
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtGui.QColor(int(r_f * 255), int(g_f * 255), int(b_f * 255)))
    return QtGui.QIcon(pix)


def _collect_bonds(doc) -> list[dict]:
    """
    Return a list of bond dicts from all BondWire_* objects in *doc*.

    Each dict has keys: wire_name, net_name, start_cp, end_cp, length_mm.
    """
    bonds = []
    if doc is None:
        return bonds
    for obj in doc.Objects:
        if not obj.Name.startswith("BondWire_"):
            continue
        bonds.append({
            "wire_name": obj.Name,
            "net_name":  getattr(obj, "NetName",    obj.Name),
            "start_cp":  getattr(obj, "StartCP",    ""),
            "end_cp":    getattr(obj, "EndCP",      ""),
            "length_mm": getattr(obj, "WireLength", 0.0),
        })
    bonds.sort(key=lambda b: b["wire_name"])
    return bonds


def _connected_cp_names(bonds: list[dict]) -> set[str]:
    """Return the set of ContactPoint object names referenced by any bond."""
    names: set[str] = set()
    for b in bonds:
        if b["start_cp"]:
            names.add(b["start_cp"])
        if b["end_cp"]:
            names.add(b["end_cp"])
    return names


# ── panel ─────────────────────────────────────────────────────────────────────

class ContactPointPanel(QtWidgets.QDockWidget):
    """
    Dock widget listing ContactPoint markers with hover-highlight.

    Connected contact points are greyed out.
    A netlist table at the bottom summarises all BondWire connections.
    """

    def __init__(self, parent=None):
        super().__init__("Contact Points", parent)
        self.setObjectName("ContactPointPanel")
        self.setMinimumWidth(380)
        self.setMinimumHeight(420)

        self._highlighted      = None
        self._orig_point_color = None
        self._orig_point_size  = None

        self._build_ui()
        self.populate()

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self):
        central = QtWidgets.QWidget()
        layout  = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Toolbar row
        btn_row     = QtWidgets.QHBoxLayout()
        btn_refresh = QtWidgets.QPushButton("Refresh")
        btn_refresh.setToolTip("Reload contact points and netlist from the active document")
        btn_refresh.clicked.connect(self.populate)
        lbl_hint = QtWidgets.QLabel("Hover to highlight  |  Click to select")
        lbl_hint.setStyleSheet("color: #888; font-size: 12px;")
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch()
        btn_row.addWidget(lbl_hint)
        layout.addLayout(btn_row)

        # Summary label
        self._lbl_count = QtWidgets.QLabel("")
        self._lbl_count.setStyleSheet("color: #aaa; font-size: 12px; padding: 0 2px;")
        layout.addWidget(self._lbl_count)

        # ── splitter: contact-point tree (top) + netlist table (bottom) ──
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.setHandleWidth(5)

        # Contact-point tree
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Name", "Source", "Position (mm)"])
        self.tree.header().setStretchLastSection(True)
        self.tree.setMouseTracking(True)
        self.tree.viewport().setMouseTracking(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setStyleSheet(
            "QTreeWidget { font-size: 12px; }"
            "QTreeWidget::item:disabled { color: #555; }"
        )
        self.tree.itemEntered.connect(self._on_item_entered)
        self.tree.viewport().installEventFilter(self)
        self.tree.itemClicked.connect(self._on_item_clicked)
        splitter.addWidget(self.tree)

        # Netlist section
        net_widget = QtWidgets.QWidget()
        net_layout = QtWidgets.QVBoxLayout(net_widget)
        net_layout.setContentsMargins(0, 2, 0, 0)
        net_layout.setSpacing(2)

        net_header = QtWidgets.QHBoxLayout()
        lbl_net = QtWidgets.QLabel("Netlist")
        lbl_net.setStyleSheet("font-weight: bold; font-size: 12px; color: #ccc;")
        self._lbl_net_count = QtWidgets.QLabel("")
        self._lbl_net_count.setStyleSheet("color: #888; font-size: 12px;")
        net_header.addWidget(lbl_net)
        net_header.addStretch()
        net_header.addWidget(self._lbl_net_count)
        net_layout.addLayout(net_header)

        self._net_table = QtWidgets.QTableWidget(0, 4)
        self._net_table.setHorizontalHeaderLabels(["Net", "From CP", "To CP", "Length (mm)"])
        self._net_table.horizontalHeader().setStretchLastSection(False)
        self._net_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeToContents)
        self._net_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch)
        self._net_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.Stretch)
        self._net_table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeToContents)
        self._net_table.verticalHeader().setVisible(False)
        self._net_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._net_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._net_table.setAlternatingRowColors(True)
        self._net_table.setStyleSheet("font-size: 12px;")
        self._net_table.itemClicked.connect(self._on_net_row_clicked)
        net_layout.addWidget(self._net_table)
        splitter.addWidget(net_widget)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self.setWidget(central)

    # ── public API ────────────────────────────────────────────────────

    def populate(self):
        """Rebuild the tree and netlist from the active document."""
        self._clear_highlight()
        self.tree.clear()

        doc   = FreeCAD.activeDocument()
        bonds = _collect_bonds(doc)
        connected = _connected_cp_names(bonds)

        if not doc:
            self._lbl_count.setText("No active document.")
            self._build_netlist([], {})
            return

        cps = [o for o in doc.Objects if getattr(o, "IsContactPoint", False)]

        if not cps:
            self._lbl_count.setText("No contact points found.")
            item = QtWidgets.QTreeWidgetItem(["(none)"])
            item.setFlags(QtCore.Qt.NoItemFlags)
            self.tree.addTopLevelItem(item)
            self._build_netlist(bonds, {})
            return

        n_connected = sum(1 for cp in cps if cp.Name in connected)
        n_free      = len(cps) - n_connected
        self._lbl_count.setText(
            f"{len(cps)} contact point(s)  —  "
            f"{n_connected} connected  |  {n_free} free"
        )

        # Build group rows — free CPs first, then connected (greyed out)
        lf_group      = self._make_group("Leadframe",  "#75BEEB")
        die_group     = self._make_group("Die / GDS",  "#E64D1A")
        housing_group = self._make_group("Housing",    "#2D50B1")
        pcb_group     = self._make_group("PCB Pads",   "#1DA85A")

        # Build label lookup for netlist display (obj.Name → obj.Label)
        cp_label: dict[str, str] = {}
        for obj in cps:
            cp_label[obj.Name] = obj.Label or obj.Name

        for obj in cps:
            src        = getattr(obj, "SourceObject", "")
            cp         = getattr(obj, "ContactPoint", None)
            pos        = (f"({cp.x:.3f}, {cp.y:.3f}, {cp.z:.3f})" if cp else "—")
            is_conn    = obj.Name in connected

            item = QtWidgets.QTreeWidgetItem([obj.Label, src, pos])
            item.setData(0, QtCore.Qt.UserRole, obj.Name)
            item.setToolTip(0, f"Object: {obj.Name}"
                               + ("  ✓ connected" if is_conn else "  ○ free"))
            item.setToolTip(1, f"Source: {src}")
            item.setToolTip(2, pos)

            # Colour swatch
            vc = getattr(obj.ViewObject, "PointColor", None)
            if vc:
                item.setIcon(0, _color_swatch(*vc[:3]))

            # Grey out connected points
            if is_conn:
                for col in range(3):
                    item.setForeground(col, QtGui.QBrush(_FG_CONNECTED))
                font = item.font(0)
                font.setItalic(True)
                item.setFont(0, font)
                item.setToolTip(0, item.toolTip(0) + "\n(already used in a bond)")
            else:
                for col in range(3):
                    item.setForeground(col, QtGui.QBrush(_FG_FREE))

            if _is_pcb_point(obj):
                pcb_group.addChild(item)
            elif _is_housing_point(obj):
                housing_group.addChild(item)
            elif _is_leadframe_source(src):
                lf_group.addChild(item)
            else:
                die_group.addChild(item)

        for grp in (lf_group, die_group, housing_group, pcb_group):
            if grp.childCount():
                self.tree.addTopLevelItem(grp)
                grp.setExpanded(True)

        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)

        self._build_netlist(bonds, cp_label)

    # ── netlist table ─────────────────────────────────────────────────

    def _build_netlist(self, bonds: list[dict], cp_label: dict[str, str]):
        """Populate the netlist table from bond data."""
        tbl = self._net_table
        tbl.setRowCount(0)

        if not bonds:
            self._lbl_net_count.setText("No bonds yet.")
            return

        self._lbl_net_count.setText(f"{len(bonds)} bond wire(s)")
        tbl.setRowCount(len(bonds))

        _grey  = QtGui.QColor("#000000")
        _white = QtGui.QColor("#0F7705")

        for row, b in enumerate(bonds):
            net_lbl   = b["net_name"]
            start_lbl = cp_label.get(b["start_cp"], b["start_cp"] or "—")
            end_lbl   = cp_label.get(b["end_cp"],   b["end_cp"]   or "—")
            try:
                length_str = f"{float(b['length_mm']):.3f}"
            except Exception:
                length_str = "—"

            cells = [net_lbl, start_lbl, end_lbl, length_str]
            for col, text in enumerate(cells):
                cell = QtWidgets.QTableWidgetItem(text)
                cell.setForeground(QtGui.QBrush(_white))
                cell.setData(QtCore.Qt.UserRole, b["wire_name"])
                tbl.setItem(row, col, cell)

        tbl.resizeRowsToContents()

    # ── Qt overrides ──────────────────────────────────────────────────

    def eventFilter(self, source, event):
        if source is self.tree.viewport() and event.type() == QtCore.QEvent.Leave:
            self._clear_highlight()
        return False

    def closeEvent(self, event):
        self._clear_highlight()
        super().closeEvent(event)

    # ── slots ─────────────────────────────────────────────────────────

    def _on_item_entered(self, item, _col):
        obj = self._resolve(item)
        if obj is None:
            self._clear_highlight()
            return
        if obj is self._highlighted:
            return
        self._clear_highlight()
        try:
            self._highlighted      = obj
            self._orig_point_color = obj.ViewObject.PointColor
            self._orig_point_size  = obj.ViewObject.PointSize
            obj.ViewObject.PointColor = _COLOR_HOVER
            obj.ViewObject.PointSize  = 16
        except Exception:
            self._highlighted = None

    def _on_item_clicked(self, item, _col):
        obj = self._resolve(item)
        if obj is None:
            return
        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(doc.Name, obj.Name)

    def _on_net_row_clicked(self, cell):
        """Clicking a netlist row selects the corresponding BondWire in the 3D view."""
        wire_name = cell.data(QtCore.Qt.UserRole)
        if not wire_name:
            return
        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        wire = doc.getObject(wire_name)
        if wire is None:
            return
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(doc.Name, wire_name)

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _make_group(label: str, dot_color_hex: str) -> QtWidgets.QTreeWidgetItem:
        grp = QtWidgets.QTreeWidgetItem([label, "", ""])
        grp.setFlags(QtCore.Qt.ItemIsEnabled)

        font = grp.font(0)
        font.setBold(True)
        grp.setFont(0, font)

        try:
            r = int(dot_color_hex[1:3], 16)
            g = int(dot_color_hex[3:5], 16)
            b = int(dot_color_hex[5:7], 16)
            pix = QtGui.QPixmap(12, 12)
            pix.fill(QtGui.QColor(r, g, b))
            grp.setIcon(0, QtGui.QIcon(pix))
        except Exception:
            pass

        return grp

    def _resolve(self, item) -> object:
        """Return the FreeCAD object for *item*, or None for group headers."""
        name = item.data(0, QtCore.Qt.UserRole)
        if not name:
            return None
        doc = FreeCAD.activeDocument()
        return doc.getObject(name) if doc else None

    def _clear_highlight(self):
        if self._highlighted is not None:
            try:
                self._highlighted.ViewObject.PointColor = self._orig_point_color
                self._highlighted.ViewObject.PointSize  = self._orig_point_size
            except Exception:
                pass
            self._highlighted      = None
            self._orig_point_color = None
            self._orig_point_size  = None
