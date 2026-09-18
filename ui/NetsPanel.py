# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Nets panel — every net, and how much of it is still to route.

The ratsnest shows what is missing in the 3-D view; this shows the same thing
as a list you can work down: a net per row, its pads, how many connections are
still open and how many are made. Clicking a row selects that net's pads and
its rubber lines, so it can be found in a crowded board.

Rebuild re-reads the document: pads move with their components and traces come
and go, so the rubber lines are derived, never stored.
"""

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore


class NetsPanel(QtWidgets.QDockWidget):
    """Dock widget listing the nets of the active document."""

    def __init__(self, parent=None):
        super().__init__("Nets", parent)
        self.setObjectName("NetsPanel")
        self.setMinimumWidth(420)
        self._rows = []
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
            ("Rebuild", self._rebuild,
             "Read the document again and redraw the connections still open"),
            ("Show lines", lambda: self._set_visible(True),
             "Show every rubber line"),
            ("Hide lines", lambda: self._set_visible(False),
             "Hide the rubber lines without deleting them"),
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

        self._table = QtWidgets.QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Net", "Pads", "Open", "Routed"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch)
        for column in (1, 2, 3):
            self._table.horizontalHeader().setSectionResizeMode(
                column, QtWidgets.QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet("font-size: 12px;")
        self._table.itemClicked.connect(self._on_row_clicked)
        layout.addWidget(self._table, 1)

        self.setWidget(central)

    # ── contents ───────────────────────────────────────────────────────

    def populate(self):
        from core import ratsnest

        doc = FreeCAD.activeDocument()
        self._rows = []
        self._table.setRowCount(0)
        if doc is None:
            self._summary.setText("No active document.")
            return
        try:
            self._rows = ratsnest.net_status(doc)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[NetsPanel] {exc}\n")
            self._summary.setText("Could not read the nets — see the Report view.")
            return

        self._table.setRowCount(len(self._rows))
        for index, row in enumerate(self._rows):
            cells = (row["net"], str(row["pads"]), str(row["open"]), str(row["done"]))
            for column, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, index)
                self._table.setItem(index, column, item)

        still_open = sum(row["open"] for row in self._rows)
        self._summary.setText(
            f"{len(self._rows)} net(s), {still_open} connection(s) to route"
            if self._rows else "No nets in this document.")

    # ── actions ────────────────────────────────────────────────────────

    def _rebuild(self):
        from core import ratsnest

        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        doc.openTransaction("Rebuild Ratsnest")
        try:
            ratsnest.rebuild(doc)
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            FreeCAD.Console.PrintError(f"[NetsPanel] {exc}\n")
        doc.recompute()
        self.populate()

    def _set_visible(self, visible):
        from core import ratsnest

        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        for obj in doc.Objects:
            if getattr(obj, ratsnest.IS_RATSNEST, False):
                try:
                    obj.ViewObject.Visibility = visible
                except Exception:
                    pass

    def _on_row_clicked(self, item):
        from core import ratsnest

        doc = FreeCAD.activeDocument()
        index = item.data(QtCore.Qt.UserRole)
        if doc is None or index is None or index >= len(self._rows):
            return
        net = self._rows[index]["net"]
        FreeCADGui.Selection.clearSelection()
        for obj in doc.Objects:
            if getattr(obj, "NetName", "") != net:
                continue
            if getattr(obj, "IsContactPoint", False) or getattr(obj, ratsnest.IS_RATSNEST, False):
                try:
                    FreeCADGui.Selection.addSelection(doc.Name, obj.Name)
                except Exception:
                    pass
