from compat import QtWidgets
import FreeCAD, FreeCADGui, os, sys

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from housing.HousingConfigurator import TransparentHousingConfigurator
from core.housing import build_housing
from Get_Path import get_icon
from session.SessionManager import session_manager

def create_housing(config):
    return build_housing(config)


class HousingCommand:
    def GetResources(self):
        return {
            "MenuText": "Housing Configurator",
            "ToolTip": "Configure and generate a transparent housing for a leadframe",
            "Pixmap": get_icon("Housing_Configurator.png")
        }

    def Activated(self):
        dialog = TransparentHousingConfigurator()
        if dialog.exec_():
            config = dialog.get_config()
            create_housing(config)
            session_manager.record_action("housing_config", config)
            QtWidgets.QMessageBox.information(None, "Success", f"Housing created:\n{config}")
        else:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Housing configuration cancelled.")

    def IsActive(self):
        return True

FreeCADGui.addCommand('HousingCommand', HousingCommand())