"""
Contact Point Browser panel.

A dockable panel that lists all ContactPoint markers in the active document,
grouped by origin (Leadframe vs Die/GDS).  Hovering over an entry temporarily
highlights the corresponding marker in the 3D view; clicking it selects it.
"""

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore, QtGui


# Highlight colour applied while the mouse hovers over a tree row
_COLOR_HOVER = (0.10, 0.90, 0.10)   # bright green


# ── helpers ────────────────────────────────────────────────────────────────────

def _is_leadframe_source(src: str) -> bool:
    """True when the SourceObject name belongs to leadframe geometry."""
    return src.startswith(("Lead_", "BGA_Ball_", "DiePaddle"))


def _is_housing_point(obj) -> bool:
    """True when the object is a housing contact point (contact_point_housing_*)."""
    return obj.Name.startswith("contact_point_housing_")


def _color_swatch(r_f, g_f, b_f) -> QtGui.QIcon:
    """Return a 12×12 solid-colour icon from 0–1 float RGB components."""
    pix = QtGui.QPixmap(12, 12)
    pix.fill(QtGui.QColor(int(r_f * 255), int(g_f * 255), int(b_f * 255)))
    return QtGui.QIcon(pix)


# ── panel ─────────────────────────────────────────────────────────────────────

class ContactPointPanel(QtWidgets.QDockWidget):
    """
    Dock widget listing ContactPoint markers with hover-highlight.

    Usage
    -----
    panel = ContactPointPanel(FreeCADGui.getMainWindow())
    main_win.addDockWidget(Qt.RightDockWidgetArea, panel)
    """

    def __init__(self, parent=None):
        super().__init__("Contact Points", parent)
        self.setObjectName("ContactPointPanel")
        self.setMinimumWidth(340)

        self._highlighted      = None
        self._orig_point_color = None
        self._orig_point_size  = None

        # ── layout ────────────────────────────────────────────────────
        central = QtWidgets.QWidget()
        layout  = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Toolbar row
        btn_row     = QtWidgets.QHBoxLayout()
        btn_refresh = QtWidgets.QPushButton("Refresh")
        btn_refresh.setToolTip("Reload contact points from the active document")
        btn_refresh.clicked.connect(self.populate)
        lbl_hint = QtWidgets.QLabel("Hover to highlight  |  Click to select")
        lbl_hint.setStyleSheet("color: #888; font-size: 10px;")
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch()
        btn_row.addWidget(lbl_hint)
        layout.addLayout(btn_row)

        # Summary label
        self._lbl_count = QtWidgets.QLabel("")
        self._lbl_count.setStyleSheet("color: #aaa; font-size: 10px; padding: 0 2px;")
        layout.addWidget(self._lbl_count)

        # Tree
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Name", "Source", "Position (mm)"])
        self.tree.header().setStretchLastSection(True)
        self.tree.setMouseTracking(True)
        self.tree.viewport().setMouseTracking(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.setAlternatingRowColors(True)

        # Hover: itemEntered fires on row entry; viewport Leave clears highlight
        self.tree.itemEntered.connect(self._on_item_entered)
        self.tree.viewport().installEventFilter(self)

        # Click → select in FreeCAD 3D view
        self.tree.itemClicked.connect(self._on_item_clicked)

        layout.addWidget(self.tree)
        self.setWidget(central)

        self.populate()

    # ── public API ────────────────────────────────────────────────────

    def populate(self):
        """Rebuild the tree from the active document."""
        self._clear_highlight()
        self.tree.clear()

        doc = FreeCAD.activeDocument()
        if not doc:
            self._lbl_count.setText("No active document.")
            return

        cps = [o for o in doc.Objects if getattr(o, "IsContactPoint", False)]

        if not cps:
            self._lbl_count.setText("No contact points found.")
            item = QtWidgets.QTreeWidgetItem(["(none)"])
            item.setFlags(QtCore.Qt.NoItemFlags)
            self.tree.addTopLevelItem(item)
            return

        self._lbl_count.setText(f"{len(cps)} contact point(s)")

        # Build group rows
        lf_group      = self._make_group("Leadframe",  "#1A99E6")
        die_group     = self._make_group("Die / GDS",   "#E64D1A")
        housing_group = self._make_group("Housing",     "#FFFF00")

        for obj in cps:
            src = getattr(obj, "SourceObject", "")
            cp  = getattr(obj, "ContactPoint", None)
            pos = (f"({cp.x:.3f}, {cp.y:.3f}, {cp.z:.3f})" if cp else "—")

            item = QtWidgets.QTreeWidgetItem([obj.Label, src, pos])
            item.setData(0, QtCore.Qt.UserRole, obj.Name)
            item.setToolTip(0, f"Object name: {obj.Name}")
            item.setToolTip(1, f"Source: {src}")
            item.setToolTip(2, pos)

            # Small colour swatch matching the marker dot
            vc = getattr(obj.ViewObject, "PointColor", None)
            if vc:
                item.setIcon(0, _color_swatch(*vc[:3]))

            if _is_housing_point(obj):
                housing_group.addChild(item)
            elif _is_leadframe_source(src):
                lf_group.addChild(item)
            else:
                die_group.addChild(item)

        for grp in (lf_group, die_group, housing_group):
            if grp.childCount():
                self.tree.addTopLevelItem(grp)
                grp.setExpanded(True)

        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)

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

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _make_group(label: str, dot_color_hex: str) -> QtWidgets.QTreeWidgetItem:
        grp = QtWidgets.QTreeWidgetItem([label, "", ""])
        grp.setFlags(QtCore.Qt.ItemIsEnabled)   # not selectable / not hoverable

        font = grp.font(0)
        font.setBold(True)
        grp.setFont(0, font)

        # Colour indicator square for the group header
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
