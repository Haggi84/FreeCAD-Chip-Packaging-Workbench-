# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Design Rule Check panel.

A dockable panel with the rule thresholds, a "Run Check" button, and a
results table. Clicking a finding selects the offending object(s) in the 3-D
view, the same click-to-select mechanism wirebond.ContactPointPanel already
uses. No hover-highlight in v1 (deferred — the property-swap mechanism
differs between point markers and solid geometry, and isn't needed for the
core ask).
"""

import os
import importlib.util

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore

from Get_Path import get_root


def _load_drc_module():
    """
    core.drc, loaded by explicit file path rather than `import core.drc`.

    FreeCAD adds every Mod/* folder to sys.path at startup. A second,
    independent clone of this project living in a sibling Mod folder also
    defines a top-level `core` package, and whichever one wins that scan
    resolves for the WHOLE process — `import core.drc` could then silently
    run the OTHER install's copy, or fail outright if it hasn't been synced
    up to include this module (exactly what happened to this project's own
    test suite). Loading by explicit path sidesteps the collision entirely;
    core/drc.py has no internal relative imports, so this is safe.
    """
    path = os.path.join(get_root(), "core", "drc.py")
    spec = importlib.util.spec_from_file_location("di_passionate_core_drc", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drc = _load_drc_module()


class DRCPanel(QtWidgets.QDockWidget):
    """Dock widget: run the design rule check and browse its findings."""

    def __init__(self, parent=None):
        super().__init__("Design Rule Check", parent)
        self.setObjectName("DRCPanel")
        self.setMinimumWidth(420)
        self.setMinimumHeight(360)

        self._findings = []
        self._build_ui()

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self):
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        settings_row = QtWidgets.QHBoxLayout()
        settings_row.addWidget(QtWidgets.QLabel("Min clearance (mm):"))
        self.spin_clearance = QtWidgets.QDoubleSpinBox()
        self.spin_clearance.setRange(0.0, 100.0)
        self.spin_clearance.setDecimals(3)
        self.spin_clearance.setSingleStep(0.05)
        self.spin_clearance.setValue(drc.DEFAULT_MIN_CLEARANCE_MM)
        settings_row.addWidget(self.spin_clearance)

        settings_row.addSpacing(12)
        settings_row.addWidget(QtWidgets.QLabel("Min trace width (mm):"))
        self.spin_width = QtWidgets.QDoubleSpinBox()
        self.spin_width.setRange(0.0, 100.0)
        self.spin_width.setDecimals(3)
        self.spin_width.setSingleStep(0.05)
        self.spin_width.setValue(drc.DEFAULT_MIN_TRACE_WIDTH_MM)
        settings_row.addWidget(self.spin_width)
        settings_row.addStretch()
        layout.addLayout(settings_row)

        wire_row = QtWidgets.QHBoxLayout()
        wire_row.addWidget(QtWidgets.QLabel("Min wire spacing (mm):"))
        self.spin_wire_spacing = QtWidgets.QDoubleSpinBox()
        self.spin_wire_spacing.setRange(0.0, 10.0)
        self.spin_wire_spacing.setDecimals(3)
        self.spin_wire_spacing.setSingleStep(0.005)
        self.spin_wire_spacing.setValue(drc.DEFAULT_MIN_WIRE_SPACING_MM)
        self.spin_wire_spacing.setToolTip(
            "Minimum gap between two bond wires that do not land on the same pad")
        wire_row.addWidget(self.spin_wire_spacing)

        wire_row.addSpacing(12)
        wire_row.addWidget(QtWidgets.QLabel("Min lid clearance (mm):"))
        self.spin_lid_clearance = QtWidgets.QDoubleSpinBox()
        self.spin_lid_clearance.setRange(0.0, 10.0)
        self.spin_lid_clearance.setDecimals(3)
        self.spin_lid_clearance.setSingleStep(0.05)
        self.spin_lid_clearance.setValue(drc.DEFAULT_MIN_LID_CLEARANCE_MM)
        self.spin_lid_clearance.setToolTip(
            "Minimum headroom between the top of a bond loop and the lid "
            "underside — or the top of the housing when it has no lid yet")
        wire_row.addWidget(self.spin_lid_clearance)
        wire_row.addStretch()
        layout.addLayout(wire_row)

        run_row = QtWidgets.QHBoxLayout()
        self.btn_run = QtWidgets.QPushButton("Run Check")
        self.btn_run.setToolTip("Check every routed trace and bond wire in the "
                                 "active document for clearance, width, wire "
                                 "spacing, wire crossings and lid clearance")
        self.btn_run.clicked.connect(self.run_check)
        run_row.addWidget(self.btn_run)
        self._lbl_summary = QtWidgets.QLabel("Not run yet.")
        self._lbl_summary.setStyleSheet("color: #aaa; font-size: 12px;")
        run_row.addWidget(self._lbl_summary)
        run_row.addStretch()
        layout.addLayout(run_row)

        self._table = QtWidgets.QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Severity", "Rule", "Objects", "Message"])
        for col in (0, 1, 2):
            self._table.horizontalHeader().setSectionResizeMode(
                col, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet("font-size: 12px;")
        self._table.itemClicked.connect(self._on_row_clicked)
        layout.addWidget(self._table, 1)

        self.setWidget(central)

    # ── public API ────────────────────────────────────────────────────

    def run_check(self):
        doc = FreeCAD.activeDocument()
        self._findings = []
        if doc is None:
            self._lbl_summary.setText("No active document.")
            self._table.setRowCount(0)
            return

        self._findings = drc.run_drc(
            doc,
            min_clearance_mm=self.spin_clearance.value(),
            min_trace_width_mm=self.spin_width.value(),
            min_wire_spacing_mm=self.spin_wire_spacing.value(),
            min_lid_clearance_mm=self.spin_lid_clearance.value(),
        )
        self._populate_table()

    # ── table ─────────────────────────────────────────────────────────

    def _populate_table(self):
        tbl = self._table
        tbl.setRowCount(len(self._findings))

        n_warnings = sum(1 for f in self._findings if f.severity == "warning")
        n_violations = len(self._findings) - n_warnings
        if not self._findings:
            self._lbl_summary.setText("No violations found.")
        else:
            self._lbl_summary.setText(
                f"{n_violations} violation(s), {n_warnings} warning(s) found.")

        for row, finding in enumerate(self._findings):
            cells = [finding.severity, finding.rule,
                     ", ".join(finding.object_names), finding.message]
            for col, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, row)
                tbl.setItem(row, col, item)
        tbl.resizeRowsToContents()

    # ── slots ─────────────────────────────────────────────────────────

    def _on_row_clicked(self, cell):
        row = cell.data(QtCore.Qt.UserRole)
        if row is None or row >= len(self._findings):
            return
        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        FreeCADGui.Selection.clearSelection()
        for name in self._findings[row].object_names:
            if doc.getObject(name) is not None:
                FreeCADGui.Selection.addSelection(doc.Name, name)
