# SPDX-License-Identifier: GPL-3.0-or-later
from compat import QtWidgets
import FreeCAD, FreeCADGui
from Get_Path import get_icon


class PackageConfiguratorCommand:
    def GetResources(self):
        return {
            "MenuText": "Select Package",
            "ToolTip": "Use detected GDS chip data and manufacturing constraints to select a package",
            "Pixmap": get_icon("Leadframe_Library.svg"),
        }

    def Activated(self):
        from ui.PackageConfiguratorDialog import PackageConfiguratorDialog
        from leadframe.LeadframeConfigurator import LeadframeConfigurator
        dialog = PackageConfiguratorDialog(document=FreeCAD.activeDocument())
        # QDialog.exec() returns 1 for Accepted in both Qt5 and Qt6.
        if dialog.exec_() != 1:
            return
        config = dialog.get_config()
        leadframe = LeadframeConfigurator()
        leadframe.apply_package(config["package"])
        if leadframe.exec_() == 1:
            from leadframe.LeadframeCommand import create_leadframe
            leadframe_config = leadframe.get_config()
            leadframe_config.update({
                "mounting": config["requirements"].mounting,
            })
            create_leadframe(leadframe_config,
                             doc=FreeCAD.activeDocument())


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("PackageConfiguratorCommand", PackageConfiguratorCommand())
