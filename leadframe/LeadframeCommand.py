from PySide2 import QtWidgets
import FreeCAD, FreeCADGui, os, sys

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)
from Get_Path import get_icon

from core.leadframe import build_leadframe

def create_leadframe(config, doc=None, gds_objects=None):
    return build_leadframe(config, doc=doc, gds_objects=gds_objects)

def configure_leadframe():
    from leadframe.LeadframeConfigurator import LeadframeConfigurator
    
    """
    Open the leadframe configuration dialog and create a leadframe based on user input.

    Returns: configuration
    """
    dialog = LeadframeConfigurator()
    if dialog.exec_():
        return dialog.get_config()
    return None


class LeadframeCommand:
    def GetResources(self):
        return {
            "MenuText": "Leadframe Configurator",
            "ToolTip": "Configure and generate a leadframe geometry",
            "Pixmap": get_icon("Leadframe_Configurator.png")
        }

    def Activated(self):
        config = configure_leadframe()
        if config:
            create_leadframe(config)
            QtWidgets.QMessageBox.information(None, "Success", f"Leadframe created:\n{config}")
        else:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Leadframe configuration cancelled.")

    def IsActive(self):
        return True

FreeCADGui.addCommand('LeadframeCommand', LeadframeCommand())