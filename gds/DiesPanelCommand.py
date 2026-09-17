# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Toggle command for the Dies panel — same singleton-panel pattern as the DRC
and Contact Point panels.
"""

import FreeCAD
import FreeCADGui
from compat import QtCore

from Get_Path import get_icon

_dies_panel = None


class ShowDiesPanelCommand:
    """Toggle the Dies dock panel."""

    def GetResources(self):
        return {
            "MenuText": "Dies",
            "ToolTip":  "Show or hide the Dies panel: every die with its tier, "
                        "size, pads and how many of them are bonded",
            "Pixmap":   get_icon("Dies_Panel.svg"),
        }

    def Activated(self):
        global _dies_panel
        main_win = FreeCADGui.getMainWindow()

        if _dies_panel is None:
            from ui.DiesPanel import DiesPanel
            _dies_panel = DiesPanel(main_win)
            main_win.addDockWidget(QtCore.Qt.RightDockWidgetArea, _dies_panel)

        if _dies_panel.isVisible():
            _dies_panel.hide()
        else:
            _dies_panel.populate()
            _dies_panel.show()
            _dies_panel.raise_()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ShowDiesPanelCommand", ShowDiesPanelCommand())
