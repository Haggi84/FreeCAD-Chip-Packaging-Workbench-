import FreeCADGui
import GDSCommand  # stellt sicher, dass der Command registriert ist

class MyWorkbench(FreeCADGui.Workbench):
    MenuText = "GDSII Workbench"
    ToolTip = "FreeCAD GDSII Workbench"
    Icon = ""  # Optional: Hier kann ein Icon-Pfad rein

    def Initialize(self):
        self.appendToolbar("GDSII Tools", ["GDSCommand"])

    def GetClassName(self):
        return "Gui::PythonWorkbench"

FreeCADGui.addWorkbench(MyWorkbench())
