from PySide2 import QtWidgets, QtCore
import FreeCAD, Part, Sketcher, FreeCADGui, os
from FreeCAD import Base

# Versuche alle Befehle zu registrieren
try:
    import GDSCommand
    import LeadframeCommand
    import HousingCommand
    import LayeronLeadframe
    import WirebondCommand
    FreeCAD.Console.PrintMessage("✔ Commands loaded successfully\n")
except Exception as e:
    FreeCAD.Console.PrintError(f"❌ Failed to load commands: {str(e)}\n")

class MyWorkbench(FreeCADGui.Workbench):
    # def GetResources(self):
    #     icon_path = os.path.join(os.path.dirname(__file__),"resources", "icons", "Workbench_logo.png")
    #     return {
    #         "MenuText": "GDSII Workbench",
    #         "ToolTip": "FreeCAD GDSII Workbench",
    #         "Pixmap": icon_path
    #     }
    #icon_path = os.path.join(os.path.dirname(__file__),"resources", "icons", "my_icon.svg")
    MODULE_PATH = os.path.join(FreeCAD.getHomePath(), "Mod", "DI-PASSIONATE-FreeCAD")
    ICON_PATH = os.path.join(MODULE_PATH, "resources", "icons", "my_icon.svg")
    #ICON_PATH = os.path.join(os.path.dirname(__path__[0]), "resources", "icons", "my_icon.svg")
    MenuText = "GDSII Workbench"
    ToolTip = "FreeCAD GDSII Workbench"
    #Icon = icon_path
    Icon = ICON_PATH  #"C:/Program Files/FreeCAD 1.0/Mod/DI-PASSIONATE-FreeCAD/resources/icons/my_icon.svg" #"icon_path  # Optional: Add path to your icon

    def Initialize(self):
        try:
            self.appendToolbar("GDSII Tools", ["GDSCommand", "LeadframeCommand", "HousingCommand", "LayeronLeadframe", "WirebondCommand"])
            FreeCAD.Console.PrintMessage("✔ Toolbar initialized\n")
        except Exception as e:
            FreeCAD.Console.PrintError(f"❌ Toolbar initialization failed: {str(e)}\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"

FreeCADGui.addWorkbench(MyWorkbench())
FreeCAD.Console.PrintMessage("🔧 GDSII Workbench registration attempted\n")