import FreeCADGui  # stellt sicher, dass der Command registriert ist

class MyWorkbench(FreeCADGui.Workbench):
    MenuText = "GDSII Workbench"
    ToolTip = "FreeCAD GDSII Workbench"
    Icon = "Std_New"  # Use a default FreeCAD icon

    def Initialize(self):
        import GDSCommand
        self.appendToolbar("GDSII Tools", ["GDSCommand"])

    def GetClassName(self):
        return "Gui::PythonWorkbench"

FreeCADGui.addWorkbench(MyWorkbench())