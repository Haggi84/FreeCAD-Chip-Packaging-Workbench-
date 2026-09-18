# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The Nets panel, and rebuilding the ratsnest on demand.

The rubber lines are derived from the document, so they are rebuilt rather
than kept up to date: after routing a connection, after moving a component,
after importing more of a board.
"""

import FreeCAD
import FreeCADGui
from compat import QtCore

from Get_Path import get_icon

_nets_panel = None


class ShowNetsPanelCommand:
    """Toggle the Nets dock panel."""

    def GetResources(self):
        return {
            "MenuText": "Nets",
            "ToolTip":  "Show or hide the Nets panel: every net with its pads "
                        "and how many connections are still to route",
            "Pixmap":   get_icon("Nets_Panel.svg"),
        }

    def Activated(self):
        global _nets_panel
        main_win = FreeCADGui.getMainWindow()

        if _nets_panel is None:
            from ui.NetsPanel import NetsPanel
            _nets_panel = NetsPanel(main_win)
            main_win.addDockWidget(QtCore.Qt.RightDockWidgetArea, _nets_panel)

        if _nets_panel.isVisible():
            _nets_panel.hide()
        else:
            _nets_panel.populate()
            _nets_panel.show()
            _nets_panel.raise_()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


class UpdateRatsnestCommand:
    """Redraw the connections still open."""

    def GetResources(self):
        return {
            "MenuText": "Update Ratsnest",
            "ToolTip":  "Redraw the rubber lines: a line disappears once its "
                        "two pads are connected, and follows a component when "
                        "it is moved",
            "Pixmap":   get_icon("Ratsnest.svg"),
        }

    def Activated(self):
        from compat import QtWidgets
        from core import ratsnest

        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        parent = FreeCADGui.getMainWindow()

        doc.openTransaction("Update Ratsnest")
        try:
            report = ratsnest.rebuild(doc)
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            FreeCAD.Console.PrintError(f"[Ratsnest] {exc}\n")
            QtWidgets.QMessageBox.critical(parent, "Update Ratsnest", str(exc))
            return
        doc.recompute()

        panel = parent.findChild(QtWidgets.QDockWidget, "NetsPanel")
        if panel is not None:
            panel.populate()

        if report["nets"] == 0:
            QtWidgets.QMessageBox.information(
                parent, "Update Ratsnest",
                "No pad in this document carries a net. Import a KiCad board, "
                "or set NetName on the pads you want connected.")
            return
        QtWidgets.QMessageBox.information(
            parent, "Update Ratsnest",
            f"{report['links']} connection(s) still to route across "
            f"{report['nets']} net(s).\n"
            f"{report['connected_nets']} net(s) are fully routed.")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ShowNetsPanelCommand", ShowNetsPanelCommand())
    FreeCADGui.addCommand("UpdateRatsnestCommand", UpdateRatsnestCommand())
