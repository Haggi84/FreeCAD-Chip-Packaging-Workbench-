import FreeCADGui
import FreeCAD
import os

# Versuche alle Befehle zu registrieren
try:
    import GDSCommand
    import LeadframeCommand
    import HousingCommand
    import LayeronLeadframe
    FreeCAD.Console.PrintMessage("✔ Commands loaded successfully\n")
except Exception as e:
    FreeCAD.Console.PrintError(f"❌ Failed to load commands: {str(e)}\n")

class MyWorkbench(FreeCADGui.Workbench):
    MenuText = "GDSII Workbench"
    ToolTip = "FreeCAD GDSII Workbench"
    Icon = "Std_New"  # Optional: Add path to your icon

    def Initialize(self):
        try:
            self.appendToolbar("GDSII Tools", ["GDSCommand", "LeadframeCommand", "HousingCommand", "LayeronLeadframe"])
            FreeCAD.Console.PrintMessage("✔ Toolbar initialized\n")
        except Exception as e:
            FreeCAD.Console.PrintError(f"❌ Toolbar initialization failed: {str(e)}\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"

FreeCADGui.addWorkbench(MyWorkbench())
FreeCAD.Console.PrintMessage("🔧 GDSII Workbench registration attempted\n")