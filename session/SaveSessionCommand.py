# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Save Design Session command.

Thin branded wrapper around FreeCAD's native document save. The actual
extensible state capture (fast-mesh mode, VIA detail mode, LOD manager
state, …) happens automatically via session.WorkbenchState's
SaveObserver.slotStartSaveDocument — registered once at workbench init in
InitGui.py — so it fires identically whether the user clicks this button
or uses native Ctrl+S. This command exists only to keep the familiar
branded menu entry and to prompt for a filename on first save.
"""

import FreeCAD
import FreeCADGui
from compat import QtWidgets

from Get_Path import get_icon


class SaveSessionCommand:
    def GetResources(self):
        return {
            "MenuText": "Save Design Session",
            "ToolTip":  "Save the current design (native FreeCAD document).",
            "Pixmap": get_icon("Save_Session.svg"),
        }

    def Activated(self):
        doc = FreeCAD.activeDocument()
        if doc is None:
            QtWidgets.QMessageBox.information(
                None, "No Active Document",
                "Open or create a document before saving.",
            )
            return

        try:
            if doc.FileName:
                doc.save()
                saved_path = doc.FileName
            else:
                filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
                    None, "Save Design Session", "",
                    "FreeCAD Document (*.FCStd);;All Files (*)",
                )
                if not filepath:
                    return
                if not filepath.lower().endswith(".fcstd"):
                    filepath += ".FCStd"
                doc.saveAs(filepath)
                saved_path = filepath

            QtWidgets.QMessageBox.information(
                None, "Session Saved", f"Design saved to:\n{saved_path}",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                None, "Save Failed", f"Could not save document:\n{exc}",
            )

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("SaveSessionCommand", SaveSessionCommand())
