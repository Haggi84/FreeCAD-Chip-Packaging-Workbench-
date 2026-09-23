# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Dies panel — the assembly seen as dies rather than as objects.

In a stack the 3-D view shows mostly the top die: everything below is hidden
behind it, and the tree shows blocks and markers with no notion of which die
they belong to. This lists the dies with their tier, size, pad count and how
many of those pads are bonded, and lets one tier be isolated so the rest gets
out of the way.

Clicking a row selects the die; Isolate hides every other die and its pads.
"""

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore


class DiesPanel(QtWidgets.QDockWidget):
    """Dock widget listing the dies of the active document."""

    def __init__(self, parent=None):
        super().__init__("Dies", parent)
        self.setObjectName("DiesPanel")
        self.setMinimumWidth(460)
        self._dies = []
        self._build_ui()
        self.populate()

    # ── UI ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        row = QtWidgets.QHBoxLayout()
        for label, slot, tip in (
            ("Refresh", self.populate,
             "Find the dies again and give any new one a name and a tier"),
            ("Isolate", self._isolate,
             "Show only the selected die and its pads"),
            ("Show all", self._show_all, "Show every die again"),
        ):
            button = QtWidgets.QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch()
        self._summary = QtWidgets.QLabel("")
        self._summary.setStyleSheet("color: #aaa; font-size: 12px;")
        row.addWidget(self._summary)
        layout.addLayout(row)

        self._table = QtWidgets.QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(
            ["Die", "Tier", "On", "Technology", "Size (mm)", "Thickness (mm)",
             "Pads", "Bonded"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet("font-size: 12px;")
        self._table.itemClicked.connect(self._on_row_clicked)
        layout.addWidget(self._table, 1)

        self.setWidget(central)

    # ── contents ───────────────────────────────────────────────────────

    def populate(self):
        from core import dies as die_model
        # Which PDK each die is from: in a package holding dies from more
        # than one process, that is as much a property of the die as its
        # tier, and the panel is where you look at the dies.
        import core.gds_tech as gds_tech

        doc = FreeCAD.activeDocument()
        self._dies = []
        self._table.setRowCount(0)
        if doc is None:
            self._summary.setText("No active document.")
            return

        try:
            self._dies = die_model.identify(doc)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[DiesPanel] {exc}\n")
            self._summary.setText("Could not read the dies — see the Report view.")
            return

        bonded = set()
        for obj in doc.Objects:
            if obj.Name.startswith("BondWire_"):
                for prop in ("StartCP", "EndCP"):
                    name = getattr(obj, prop, "")
                    if name:
                        bonded.add(name)

        self._table.setRowCount(len(self._dies))
        for row, die in enumerate(self._dies):
            pads = die_model.pads_of(doc, die)
            technology = gds_tech.technology_of(die.block)
            cells = (
                die.name,
                str(die.tier),
                die.below or "carrier",
                (technology or {}).get("name") or "—",
                f"{die.width_mm:.3f} × {die.length_mm:.3f}",
                f"{die.thickness_mm:.3f}",
                str(len(pads)),
                str(sum(1 for pad in pads if pad.Name in bonded)),
            )
            for column, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, row)
                self._table.setItem(row, column, item)
        self._table.resizeColumnsToContents()

        tiers = len({die.tier for die in self._dies})
        self._summary.setText(
            f"{len(self._dies)} die(s) in {tiers} tier(s)" if self._dies
            else "No dies in this document.")

    # ── actions ────────────────────────────────────────────────────────

    def _selected_die(self):
        rows = {index.row() for index in self._table.selectedIndexes()}
        if len(rows) != 1:
            return None
        row = rows.pop()
        return self._dies[row] if 0 <= row < len(self._dies) else None

    def _on_row_clicked(self, _item):
        from core import dies as die_model

        doc = FreeCAD.activeDocument()
        die = self._selected_die()
        if doc is None or die is None:
            return
        FreeCADGui.Selection.clearSelection()
        for obj in die_model.objects_of(doc, die):
            try:
                FreeCADGui.Selection.addSelection(doc.Name, obj.Name)
            except Exception:
                pass

    def _set_visible(self, objects, visible):
        for obj in objects:
            try:
                obj.ViewObject.Visibility = visible
            except Exception:
                pass

    def _isolate(self):
        from core import dies as die_model

        doc = FreeCAD.activeDocument()
        die = self._selected_die()
        if doc is None or die is None:
            return
        for other in self._dies:
            self._set_visible(die_model.objects_of(doc, other), other is die)

    def _show_all(self):
        from core import dies as die_model

        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        for die in self._dies:
            self._set_visible(die_model.objects_of(doc, die), True)
