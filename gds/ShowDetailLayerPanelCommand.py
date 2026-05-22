"""
ShowDetailLayerPanelCommand
===========================
Toolbar command that opens (or raises) the Detail Layer Panel dock widget.
"""

import FreeCAD
import FreeCADGui
from Get_Path import get_icon

_PANEL_OBJECT_NAME = "DetailLayerPanel"
_panel_ref = None


class ShowDetailLayerPanelCommand:

    def GetResources(self):
        return {
            "MenuText": "Detail Layer Control",
            "ToolTip": (
                "Open the Detail Layer Control panel.\n"
                "\n"
                "Select which GDS layers are shown in full Detail (shaded)\n"
                "and which stay in fast Wireframe mode.\n"
                "\n"
                "Z-Cursor mode: drag the cursor to promote the layer at that\n"
                "height automatically.\n"
                "Free mode: toggle each layer independently."
            ),
            "Pixmap": get_icon("Performance_Mode.svg"),
        }

    def IsActive(self):
        return True

    def Activated(self):
        global _panel_ref
        from compat import QtWidgets, QtCore
        from ui.DetailLayerPanel import DetailLayerPanel

        mw = FreeCADGui.getMainWindow()

        existing = mw.findChild(QtWidgets.QDockWidget, _PANEL_OBJECT_NAME)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.populate()
            return

        panel = DetailLayerPanel(mw)
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
        panel.show()
        _panel_ref = panel


FreeCADGui.addCommand(
    "ShowDetailLayerPanelCommand",
    ShowDetailLayerPanelCommand(),
)
