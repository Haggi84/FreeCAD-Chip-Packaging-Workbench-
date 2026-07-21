# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Load Design Session command.

Thin branded wrapper around FreeCAD's native document open. Extensible
state restoration (fast-mesh mode, VIA detail mode, LOD manager state, …)
happens automatically via session.WorkbenchState's
RestoreObserver.slotFinishRestoreDocument — registered once at workbench
init in InitGui.py — so it fires identically whether the user uses this
button or native File > Open.
"""

import os

import FreeCAD
import FreeCADGui
from compat import QtWidgets

from Get_Path import get_icon


class LoadSessionCommand:
    def GetResources(self):
        return {
            "MenuText": "Load Design Session",
            "ToolTip":  "Open a previously saved design (native FreeCAD document).",
            "Pixmap": get_icon("Load_Session.svg"),
        }

    def Activated(self):
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Load Design Session", "",
            "FreeCAD Document (*.FCStd);;All Files (*)",
        )
        if not filepath or not os.path.exists(filepath):
            return
        try:
            FreeCAD.openDocument(filepath)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                None, "Load Failed", f"Could not open document:\n{exc}",
            )

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("LoadSessionCommand", LoadSessionCommand())
