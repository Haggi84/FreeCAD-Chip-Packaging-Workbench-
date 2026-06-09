# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
ShowLayerSliderCommand — opens the interactive GDS Layer Slider dock panel.
"""

import FreeCAD
import FreeCADGui
from Get_Path import get_icon


_PANEL_OBJECT_NAME = "LayerSliderPanel"
_panel_ref = None   # module-level singleton reference to avoid GC


class ShowLayerSliderCommand:

    def GetResources(self):
        return {
            "MenuText": "Layer Slider",
            "ToolTip":  (
                "Open the interactive Layer Slider.\n"
                "Step through GDS layers from bottom to top using a vertical\n"
                "slider, similar to Prusa Slicer's layer preview."
            ),
            "Pixmap": get_icon("Layer_Slider.svg"),
        }

    def IsActive(self):
        return True

    def Activated(self):
        global _panel_ref
        from compat import QtWidgets, QtCore
        from ui.LayerSliderPanel import LayerSliderPanel

        mw = FreeCADGui.getMainWindow()

        # Re-use existing panel if it is still alive
        existing = mw.findChild(QtWidgets.QDockWidget, _PANEL_OBJECT_NAME)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.populate()
            return

        panel = LayerSliderPanel(mw)
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
        panel.show()
        _panel_ref = panel   # keep alive


FreeCADGui.addCommand("ShowLayerSliderCommand", ShowLayerSliderCommand())
