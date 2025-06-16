import FreeCADGui
import GDSCommand

class MyWorkbench(FreeCADGui.Workbench):
    MenuText = "GDSII 3D Import"
    ToolTip = "Importiert GDSII mit LYP Layernamen und 3D Körpern"
    Icon = ""  # Hier könnte später ein Icon hin

    def Initialize(self):
        self.appendToolbar("GDS Tools", ["GDSCommand"])

    def GetClassName(self):
        return "Gui::PythonWorkbench"

FreeCADGui.addWorkbench(MyWorkbench())
