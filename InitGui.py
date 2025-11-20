from PySide2 import QtWidgets, QtCore
<<<<<<< HEAD
import FreeCAD, FreeCADGui, os
=======
import FreeCAD, FreeCADGui

>>>>>>> Refactoring_Layout

# Try to register commands (some are optional)
try:
<<<<<<< HEAD
    import GDSCommand
    import LeadframeCommand
    import HousingCommand
    import LayeronLeadframe
    # WirebondCommand is optional; ignore if missing
    try:
        import WirebondCommand  # noqa: F401
    except Exception:
        pass
=======
    from gds import GDSCommand
    from leadframe import LeadframeCommand
    from housing import HousingCommand
    from leadframe import LayeronLeadframe
    from wirebond import WirebondCommand
    from help import HelpGuideCommand

>>>>>>> Refactoring_Layout
    FreeCAD.Console.PrintMessage("✔ Commands loaded successfully\n")
except Exception as e:
    FreeCAD.Console.PrintError(f"❌ Failed to load commands: {e}\n")

class MyWorkbench(FreeCADGui.Workbench):
<<<<<<< HEAD
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
=======

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
            self.appendToolbar("GDSII Tools", ["GDSCommand", "LeadframeCommand", "HousingCommand", "LayeronLeadframe", "WirebondCommand", "HelpGuideCommand"])
>>>>>>> Refactoring_Layout
            FreeCAD.Console.PrintMessage("✔ Toolbar initialized\n")
        except Exception as e:
            FreeCAD.Console.PrintError(f"❌ Toolbar initialization failed: {e}\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"

FreeCADGui.addWorkbench(MyWorkbench())
<<<<<<< HEAD
FreeCAD.Console.PrintMessage("🔧 GDSII Workbench registration attempted\n")
=======
FreeCAD.Console.PrintMessage("✔ GDSII Workbench registration attempted\n")
>>>>>>> Refactoring_Layout
