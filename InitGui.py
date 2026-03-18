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
    from help import HelpGuideCommand

    FreeCAD.Console.PrintMessage("Commands loaded successfully\n")
except Exception as e:
    FreeCAD.Console.PrintError(f"Failed to load commands: {e}\n")

class MyWorkbench(FreeCADGui.Workbench):

    from Get_Path import get_icon
    MenuText = "GDSII Workbench"
    ToolTip = "FreeCAD GDSII Workbench"
    Icon = get_icon("my_icon.svg")


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
            FreeCAD.Console.PrintMessage("Toolbar initialized\n")
        except Exception as e:
            FreeCAD.Console.PrintError(f"Toolbar initialization failed: {e}\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"

FreeCADGui.addWorkbench(MyWorkbench())
FreeCAD.Console.PrintMessage("GDSII Workbench registration attempted\n")