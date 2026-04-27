from PySide2 import QtWidgets, QtCore, QtGui
import FreeCAD, FreeCADGui

import os
if os.environ.get("FREECAD_DEBUGPY") == "1":
    import debugpy
    debugpy.listen(("localhost", 5678))
    FreeCAD.Console.PrintMessage("debugpy: waiting for VS Code attach on port 5678...\n")
    debugpy.wait_for_client()
    FreeCAD.Console.PrintMessage("debugpy: attached.\n")

# ── Command imports ────────────────────────────────────────────────────────────
try:
    from gds import GDSCommand
    from gds import ChipTransformCommand
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
    from session import SessionMenuCommand

    FreeCAD.Console.PrintMessage("Commands loaded successfully\n")
except Exception as e:
    FreeCAD.Console.PrintError(f"Failed to load commands: {e}\n")


# ── Advanced tools dropdown command ───────────────────────────────────────────

class AdvancedMenuCommand:
    """
    Pops up a menu with all advanced tools when clicked —
    same pattern as the Session save/load dropdown.
    """

    # (display label, FreeCAD command name, icon filename)
    _ITEMS = [
        ("Leadframe Configurator",   "LeadframeCommand",           "Leadframe_Configurator.png"),
        ("Center Leadframe",         "CenterLeadframeCommand",     "Center_Leadframe.svg"),
        ("Housing Configurator",     "HousingCommand",             "Housing_Configurator.png"),
        ("Layer on Leadframe",       "LayeronLeadframe",           "Layer on Leadframe.png"),
        ("Define Contact Points",    "DefineContactPointsCommand", "Define_Contact_Points.svg"),
    ]

    def GetResources(self):
        from Get_Path import get_icon
        return {
            "MenuText": "Advanced Tools",
            "ToolTip":  "Leadframe, Housing, Layer-on-Leadframe, Define Contact Points",
            "Pixmap":   get_icon("Toggle_Advanced.svg"),
        }

    def Activated(self):
        from PySide2 import QtWidgets, QtGui
        import FreeCADGui
        from Get_Path import get_icon

        menu = QtWidgets.QMenu()
        actions = []
        for label, cmd_name, icon_file in self._ITEMS:
            icon_path = get_icon(icon_file)
            if icon_path:
                act = menu.addAction(QtGui.QIcon(icon_path), label)
            else:
                act = menu.addAction(label)
            actions.append((act, cmd_name))

        chosen = menu.exec_(QtGui.QCursor.pos())
        for act, cmd_name in actions:
            if chosen == act:
                FreeCADGui.runCommand(cmd_name)
                break

    def IsActive(self):
        return True


FreeCADGui.addCommand("AdvancedMenuCommand", AdvancedMenuCommand())


# ── Workbench definition ───────────────────────────────────────────────────────

class MyWorkbench(FreeCADGui.Workbench):

    from Get_Path import get_icon
    MenuText = "Chip-Packaging Workbench"
    ToolTip  = "FreeCAD Chip-Packaging Workbench"
    Icon     = get_icon("my_icon.svg")

    def Initialize(self):
        try:
            self.appendToolbar(
                "GDSII Tools",
                [
                    "GDSCommand",
                    "LeadframeLibraryCommand",
                    "ChipTransformCommand",
                    "SetContactPointsOnFaceCommand",
                    "ShowContactPointPanelCommand",
                    "WirebondCommand",
                    "WireBumpConfiguratorCommand",
                    "CancelWireBondingCommand",
                    "SessionMenuCommand",
                    "AdvancedMenuCommand",
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
