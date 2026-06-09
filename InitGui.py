# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
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
    from gds import ShowLayerSliderCommand
    from gds import TogglePerformanceModeCommand
    from gds import ShowDetailLayerPanelCommand
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
    from ui import TechConfigDialog  # noqa: F401  (side-effect: registers TechConfigCommand)
    from pcb import PCBImportCommand      # noqa: F401
    from pcb import PCBPlacementCommand   # noqa: F401

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
        from compat import QtWidgets, QtGui
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
            from compat import QtCore as _QtCore
            import FreeCAD as _FreeCAD

            # ── Technology configuration bar (shown above main tools) ──────
            self.appendToolbar(
                "Technology Configuration",
                ["TechConfigCommand"],
            )

            # ── Main tools toolbar ─────────────────────────────────────────
            self.appendToolbar(
                "GDSII Tools",
                [
                    "PCBImportCommand",
                    "PCBPlacementCommand",
                    "GDSCommand",
                    "TogglePerformanceModeCommand",
                    "ShowDetailLayerPanelCommand",
                    "ShowLayerSliderCommand",
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

            # Inject the status label into the tech config toolbar after Qt
            # has finished building it (singleShot defers until the event loop).
            _QtCore.QTimer.singleShot(400, self._inject_tech_status_label)

            _FreeCAD.Console.PrintMessage("Toolbars initialized\n")
        except Exception as e:
            import FreeCAD as _FC
            _FC.Console.PrintError(f"Toolbar initialization failed: {e}\n")

    def _inject_tech_status_label(self):
        """Find the Technology Configuration toolbar and add a status QLabel."""
        try:
            from compat import QtWidgets as _QW, QtCore as _QC
            import FreeCAD as _FC
            import FreeCADGui as _FCGui
            from core import TechStatusBar

            mw = _FCGui.getMainWindow()
            target_tb = None
            for tb in mw.findChildren(_QW.QToolBar):
                if tb.windowTitle() == "Technology Configuration":
                    target_tb = tb
                    break

            if target_tb is None:
                _FC.Console.PrintWarning(
                    "TechConfig: toolbar not found — retrying in 1 s\n"
                )
                _QC.QTimer.singleShot(1000, self._inject_tech_status_label)
                return

            # Build the label widget
            lbl = _QW.QLabel()
            lbl.setTextFormat(_QC.Qt.RichText)
            lbl.setMinimumWidth(360)
            lbl.setSizePolicy(
                _QW.QSizePolicy.Expanding,
                _QW.QSizePolicy.Preferred,
            )
            lbl.setToolTip(
                "Current technology configuration.\n"
                "Click the gear button on the left to manage profiles."
            )

            # Style: subtle inset look
            lbl.setStyleSheet(
                "QLabel {"
                "  background: #F5F5F5;"
                "  border: 1px solid #BDBDBD;"
                "  border-radius: 3px;"
                "  padding: 2px 6px;"
                "  margin: 2px 4px;"
                "}"
            )

            target_tb.addSeparator()
            target_tb.addWidget(lbl)

            TechStatusBar.set_label(lbl)
            _FC.Console.PrintMessage("TechConfig: status label injected\n")

        except Exception as exc:
            import FreeCAD as _FC
            _FC.Console.PrintWarning(
                f"TechConfig: status label injection failed: {exc}\n"
            )

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(MyWorkbench())
FreeCAD.Console.PrintMessage("GDSII Workbench registration attempted\n")
