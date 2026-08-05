# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Toggle command for the Design Rule Check panel — singleton-panel pattern
copied from wirebond.WirebondCommand.ShowContactPointPanelCommand.
"""

import FreeCAD
import FreeCADGui
from compat import QtCore

from Get_Path import get_icon

# Singleton panel instance — persisted across command activations
_drc_panel = None


class ShowDRCPanelCommand:
    """Toggle the Design Rule Check dock panel."""

    def GetResources(self):
        return {
            "MenuText": "Design Rule Check",
            "ToolTip":  "Check routed traces and bond wires for clearance and "
                        "trace-width violations",
            "Pixmap":   get_icon("DRC_Check.svg"),
        }

    def Activated(self):
        global _drc_panel
        main_win = FreeCADGui.getMainWindow()

        if _drc_panel is None:
            from drc.DRCPanel import DRCPanel
            _drc_panel = DRCPanel(main_win)
            main_win.addDockWidget(QtCore.Qt.RightDockWidgetArea, _drc_panel)

        if _drc_panel.isVisible():
            _drc_panel.hide()
        else:
            _drc_panel.show()
            _drc_panel.raise_()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ShowDRCPanelCommand", ShowDRCPanelCommand())
