from PySide2 import QtWidgets, QtCore
import FreeCAD, FreeCADGui

# Try to register commands (some are optional)
try:
    from gds import GDSCommand
    from leadframe import LeadframeCommand
    from leadframe import LeadframeLibraryCommand
    from housing import HousingCommand
    from leadframe import LayeronLeadframe
    from wirebond import WirebondCommand
    from help import HelpGuideCommand

    FreeCAD.Console.PrintMessage("✔ Commands loaded successfully\n")
except Exception as e:
    FreeCAD.Console.PrintError(f"❌ Failed to load commands: {e}\n")

class MyWorkbench(FreeCADGui.Workbench):

    from Get_Path import get_icon
    #base_path = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() \
                #else os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "DI-PASSIONATE-FreeCAD")

    #icon_path = os.path.join(base_path, "resources", "icons", "my_icon.svg")
    #MODULE_PATH = os.path.join(FreeCAD.getHomePath(), "Mod", "DI-PASSIONATE-FreeCAD")
    #ICON_PATH = os.path.join(MODULE_PATH, "resources", "icons", "my_icon.svg")
    #ICON_PATH = os.path.join(os.path.dirname(__path__[0]), "resources", "icons", "my_icon.svg")
    MenuText = "GDSII Workbench"
    ToolTip = "FreeCAD GDSII Workbench"
    Icon = get_icon("my_icon.svg")
    #Icon = ICON_PATH  #"C:/Program Files/FreeCAD 1.0/Mod/DI-PASSIONATE-FreeCAD/resources/icons/my_icon.svg" #"icon_path  # Optional: Add path to your icon
    

    def Initialize(self):
        try:
            self.appendToolbar(
                "GDSII Tools",
                [
                    "GDSCommand",
                    "LeadframeCommand",
                    "LeadframeLibraryCommand",
                    "HousingCommand",
                    "LayeronLeadframe",
                    "DefineContactPointsCommand",
                    "WirebondCommand",
                    "HelpGuideCommand",
                ],
            )
            FreeCAD.Console.PrintMessage("✔ Toolbar initialized\n")
        except Exception as e:
            FreeCAD.Console.PrintError(f"❌ Toolbar initialization failed: {e}\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"

FreeCADGui.addWorkbench(MyWorkbench())
FreeCAD.Console.PrintMessage("✔ GDSII Workbench registration attempted\n")