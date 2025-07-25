import FreeCADGui
import FreeCAD
import os

# Versuche alle Befehle zu registrieren
try:
    import GDSCommand
    import LeadframeCommand
    FreeCAD.Console.PrintMessage("✔ Commands loaded successfully\n")
except Exception as e:
    FreeCAD.Console.PrintError(f"❌ Failed to load commands: {str(e)}\n")

class MyWorkbench(FreeCADGui.Workbench):
    MenuText = "GDSII Workbench"
    ToolTip = "FreeCAD GDSII Workbench"
   #Icon = "Std_New"  # Use a default FreeCAD icon
    Icon = "C:/Program Files/FreeCAD 1.0/Mod/DI-PASSIONATE-FreeCAD/resources/icons/my_icon.svg"  # Optional: Add path to your icon

    def Initialize(self):
        try:
            self.appendToolbar("GDSII Tools", ["GDSCommand", "MyCommand", "LeadframeCommand"])
            FreeCAD.Console.PrintMessage("✔ Toolbar initialized\n")
        except Exception as e:
            FreeCAD.Console.PrintError(f"❌ Toolbar initialization failed: {str(e)}\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"

FreeCADGui.addWorkbench(MyWorkbench())
FreeCAD.Console.PrintMessage("🔧 GDSII Workbench registration attempted\n")
