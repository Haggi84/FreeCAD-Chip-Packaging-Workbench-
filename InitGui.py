from PySide2 import QtWidgets, QtCore
import FreeCAD, FreeCADGui

import os
if os.environ.get("FREECAD_DEBUGPY") == "1":
    import debugpy
    debugpy.listen(("localhost", 5678))
    FreeCAD.Console.PrintMessage("debugpy: waiting for VS Code attach on port 5678...\n")
    debugpy.wait_for_client()
    FreeCAD.Console.PrintMessage("debugpy: attached.\n")

# Try to register commands (some are optional)
try:
    from gds import GDSCommand
    from leadframe import LeadframeCommand
    from leadframe import LeadframeLibraryCommand
    from housing import HousingCommand
    from leadframe import LayeronLeadframe
    from wirebond import WirebondCommand
    from wirebond import SetContactPointsOnFaceCommand
    from help import HelpGuideCommand
    from help import AboutCommand
    from session import SaveSessionCommand
    from session import LoadSessionCommand

    FreeCAD.Console.PrintMessage("Commands loaded successfully\n")
except Exception as e:
    FreeCAD.Console.PrintError(f"Failed to load commands: {e}\n")

class MyWorkbench(FreeCADGui.Workbench):

    from Get_Path import get_icon
    MenuText = "Chip-Packaging Workbench"
    ToolTip = "FreeCAD Chip-Packaging Workbench"
    Icon = get_icon("my_icon.svg")


    def Initialize(self):
        try:
            self.appendToolbar(
                "GDSII Tools",
                [
                    "GDSCommand",
                    "LeadframeCommand",
                    "CenterLeadframeCommand",
                    "LeadframeLibraryCommand",
                    "HousingCommand",
                    "LayeronLeadframe",
                    "DefineContactPointsCommand",
                    "SetContactPointsOnFaceCommand",
                    "ShowContactPointPanelCommand",
                    "WirebondCommand",
                    "WireBumpConfiguratorCommand",
                    "CancelWireBondingCommand",
                    "SaveSessionCommand",
                    "LoadSessionCommand",
                    "HelpGuideCommand",
                    "AboutCommand",
                ],
            )
            FreeCAD.Console.PrintMessage("Toolbar initialized\n")
        except Exception as e:
            FreeCAD.Console.PrintError(f"Toolbar initialization failed: {e}\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"

FreeCADGui.addWorkbench(MyWorkbench())
FreeCAD.Console.PrintMessage("GDSII Workbench registration attempted\n")