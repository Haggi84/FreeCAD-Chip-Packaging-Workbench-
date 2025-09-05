from PySide2 import QtWidgets, QtCore
import FreeCAD, FreeCADGui, os

# Try to register commands (some are optional)
try:
    import GDSCommand
    import LeadframeCommand
    import HousingCommand
    import LayeronLeadframe
    # WirebondCommand is optional; ignore if missing
    try:
        import WirebondCommand  # noqa: F401
    except Exception:
        pass
    FreeCAD.Console.PrintMessage("✔ Commands loaded successfully\n")
except Exception as e:
    FreeCAD.Console.PrintError(f"❌ Failed to load commands: {e}\n")

class MyWorkbench(FreeCADGui.Workbench):
    MODULE_PATH = os.path.join(FreeCAD.getHomePath(), "Mod", "DI-PASSIONATE-FreeCAD")
    ICON_PATH = os.path.join(MODULE_PATH, "resources", "icons", "my_icon.svg") 
    
    """GDSII Workbench."""
    MenuText = "GDSII Workbench"
    ToolTip = "FreeCAD GDSII Workbench"
    Icon = ICON_PATH

    def Initialize(self):
        try:
            self.appendToolbar(
                "GDSII Tools",
                ["GDSCommand", "LeadframeCommand", "HousingCommand", "LayeronLeadframe"]
            )
            FreeCAD.Console.PrintMessage("✔ Toolbar initialized\n")
        except Exception as e:
            FreeCAD.Console.PrintError(f"❌ Toolbar initialization failed: {e}\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"

FreeCADGui.addWorkbench(MyWorkbench())
FreeCAD.Console.PrintMessage("🔧 GDSII Workbench registration attempted\n")
