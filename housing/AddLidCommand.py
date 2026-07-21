# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Add Lid command.

Closes up an existing (typically open) housing with a transparent lid,
sized automatically to the housing's own footprint. Meant for the common
sequence: build the housing without a lid so the die and bond wires stay
accessible, do the wire bonding, then run this once bonding is finished.
"""

import os
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from core.housing import add_lid_to_housing, find_housing_body
from Get_Path import get_icon


class AddLidDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Lid")
        self.setMinimumWidth(320)

        layout = QtWidgets.QFormLayout(self)

        self.lid_thickness = QtWidgets.QDoubleSpinBox()
        self.lid_thickness.setRange(0.1, 5.0)
        self.lid_thickness.setSingleStep(0.1)
        self.lid_thickness.setValue(0.5)
        self.lid_thickness.setSuffix(" mm")
        layout.addRow("Lid Thickness:", self.lid_thickness)

        hint = QtWidgets.QLabel(
            "Sized automatically to match the existing housing's footprint.\n"
            "Running this again replaces the current lid."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 10px;")
        layout.addRow(hint)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_lid_thickness(self) -> float:
        return self.lid_thickness.value()


class AddLidCommand:
    def GetResources(self):
        return {
            "MenuText": "Add Lid",
            "ToolTip": (
                "Add (or replace) a transparent lid on an existing housing.\n"
                "\n"
                "Sized automatically to the housing's own footprint — build "
                "the\nhousing without a lid first (e.g. to leave it open for "
                "wire bonding),\nthen use this once bonding is done."
            ),
            "Pixmap": get_icon("Add_Lid.svg"),
        }

    def Activated(self):
        doc = FreeCAD.activeDocument()
        if doc is None:
            QtWidgets.QMessageBox.warning(
                None, "No document", "Open a document with a housing first."
            )
            return

        housing = find_housing_body(doc)
        if housing is None:
            QtWidgets.QMessageBox.warning(
                None, "No housing found",
                "No housing was found in this document.\n\n"
                "Build one first via Housing Configurator (Advanced Tools) — "
                "leave 'Include Transparent Lid' unchecked if you want to "
                "wire-bond before closing it up, then come back to this "
                "command.",
            )
            return

        dialog = AddLidDialog()
        if dialog.exec_():
            lid = add_lid_to_housing(doc, dialog.get_lid_thickness(), housing_obj=housing)
            if lid is not None:
                QtWidgets.QMessageBox.information(
                    None, "Lid Added", f"Lid added on '{housing.Name}': {lid.Name}"
                )
            else:
                QtWidgets.QMessageBox.critical(
                    None, "Failed", "Could not add a lid — check the Report View."
                )

    def IsActive(self):
        doc = FreeCAD.activeDocument()
        return doc is not None and find_housing_body(doc) is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("AddLidCommand", AddLidCommand())
