from PySide2 import QtWidgets, QtCore
import FreeCAD, FreeCADGui, os

# Try to register existing GDSII commands
try:
    import GDSCommand
    import LeadframeCommand
    import HousingCommand
    import LayeronLeadframe
    try:
        import WirebondCommand
    except Exception:
        pass
    FreeCAD.Console.PrintMessage("✔ GDSII commands loaded\n")
except Exception as e:
    FreeCAD.Console.PrintError(f"❌ Failed to load GDSII commands: {e}\n")

# ---------------------------
# NEW: Create Package Command
# ---------------------------

class CmdCreatePackage:
    def GetResources(self):
        module_path = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "DI-PASSIONATE-FreeCAD")
        icon_path = os.path.join(module_path, "resources", "icons", "package.svg")
        return {
            'Pixmap': icon_path,
            'MenuText': "Create Package",
            'ToolTip': "Generate a parametric QFN / QFP / BGA package"
        }

    def IsActive(self):
        return True

    def Activated(self):
        try:
            from ui_create_package import show_dialog
            show_dialog()
        except Exception as e:
            FreeCAD.Console.PrintError(f"❌ Failed to open Create Package dialog: {e}\n")

# Register the new command
FreeCADGui.addCommand("DI_CreatePackage", CmdCreatePackage())


# -------------------
# Workbench definition
# -------------------

class MyWorkbench(FreeCADGui.Workbench):
    MODULE_PATH = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "DI-PASSIONATE-FreeCAD")
    ICON_PATH = os.path.join(MODULE_PATH, "resources", "icons", "package.svg")  # <- Workbench icon

    MenuText = "DI-PASSIONATE Workbench"
    ToolTip = "Tools for GDSII and Package Generation"
    Icon = ICON_PATH

    def Initialize(self):
        try:
            # Existing GDSII toolbar
            self.appendToolbar(
                "GDSII Tools",
                ["GDSCommand", "LeadframeCommand", "HousingCommand", "LayeronLeadframe"]
            )

            # NEW: Component library toolbar
            self.appendToolbar(
                "Component Library",
                ["DI_CreatePackage"]
            )

            self.appendMenu(
                "Component Library",
                ["DI_CreatePackage"]
            )

            FreeCAD.Console.PrintMessage("✔ DI-PASSIONATE Workbench initialized\n")
        except Exception as e:
            FreeCAD.Console.PrintError(f"❌ Workbench initialization failed: {e}\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"

FreeCADGui.addWorkbench(MyWorkbench())
FreeCAD.Console.PrintMessage("🔧 DI-PASSIONATE Workbench registration attempted\n")
