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

# ── Module-name clash check ────────────────────────────────────────────────────
# Runs before any workbench module is imported, so a clash is reported even
# when it is the thing that breaks the imports below (see dip_package_guard.py).
try:
    import sys as _sys
    import dip_package_guard as _guard
    _guard_root = _guard.workbench_root()
    _guard_msg = _guard.describe(_guard.find_conflicts(_guard_root, _sys.path), _guard_root)
    if _guard_msg:
        FreeCAD.Console.PrintError(_guard_msg)
except Exception as e:
    FreeCAD.Console.PrintWarning(f"Module-name clash check failed: {e}\n")

# ── Command imports ────────────────────────────────────────────────────────────
try:
    from gds import GDSCommand
    from gds import ImportChipProxyCommand
    from gds import ChipTransformCommand
    from gds import ShowLayerSliderCommand
    from gds import LayerDisplay   # noqa: F401  (per-layer display quality)
    from gds import ToggleViaDetailCommand
    from gds import ClearGDSCacheCommand   # noqa: F401
    from gds import ChipTextureCommand   # noqa: F401
    from gds import ViewInGDS3DCommand   # noqa: F401
    from ui import LODManager as _LODManager  # noqa: F401  (side-effect: registers WorkbenchState provider)
    from gds import ShowDetailLayerPanelCommand
    from leadframe import LeadframeCommand
    from leadframe import LeadframeLibraryCommand
    from leadframe import PinNumberingCommand
    from housing import HousingCommand
    from housing import AddLidCommand
    from leadframe import LayeronLeadframe
    from wirebond import WirebondCommand
    from wirebond import SetContactPointsOnFaceCommand
    from wirebond import InteractiveContactPointCommand
    from wirebond import ContactPointSymmetryCommand   # noqa: F401
    from wirebond import ContactPointPatternCommand   # noqa: F401
    from help import HelpGuideCommand
    from help import AboutCommand
    from session import SaveSessionCommand
    from session import LoadSessionCommand
    from session import SessionMenuCommand
    from ui import TechConfigDialog  # noqa: F401  (side-effect: registers TechConfigCommand)
    from ui import ChipTheme   # noqa: F401
    from ui import DesktopShortcutCommand   # noqa: F401
    from pcb import PCBImportCommand      # noqa: F401
    from pcb import PCBPlacementCommand   # noqa: F401
    from routing import TraceRoutingCommand   # noqa: F401
    from routing import InteractiveRouterCommand   # noqa: F401
    from routing import BatchRouteCommand   # noqa: F401
    from routing import TraceDragCommand   # noqa: F401
    from routing import BodyRouteCommand   # noqa: F401
    from drc import DRCCommand   # noqa: F401
    from thermal import MaterialsCommand   # noqa: F401
    from thermal import ThermalExportCommand   # noqa: F401
    from wirebond import NetlistCommands   # noqa: F401
    from wirebond import PackagePadsCommand   # noqa: F401
    from gds import StackDieCommand   # noqa: F401
    from gds import DiesPanelCommand   # noqa: F401
    from kicad import KicadImportCommand   # noqa: F401
    from kicad import NetlistCommands   # noqa: F401

    FreeCAD.Console.PrintMessage("Commands loaded successfully\n")
except Exception as e:
    FreeCAD.Console.PrintError(f"Failed to load commands: {e}\n")


# ── Workbench-state save/restore observers ────────────────────────────────────
# Registered at module scope (not inside MyWorkbench.Initialize(), which only
# runs the first time this specific workbench is activated — a document may
# be opened before that ever happens). This makes native FreeCAD save/open
# automatically capture and restore the handful of workbench state values
# that live in Python module-level globals (VIA detail mode, LOD manager)
# rather than on a DocumentObject — see session/WorkbenchState.py.
try:
    from session import WorkbenchState
    FreeCAD.addDocumentObserver(WorkbenchState.SaveObserver())
    if FreeCAD.GuiUp:
        FreeCADGui.addDocumentObserver(WorkbenchState.RestoreObserver())
    FreeCAD.Console.PrintMessage("WorkbenchState observers registered\n")
except Exception as e:
    FreeCAD.Console.PrintError(f"Failed to register WorkbenchState observers: {e}\n")


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
        ("Add Lid",                  "AddLidCommand",              "Add_Lid.svg"),
        ("Layer on Leadframe",       "LayeronLeadframe",           "Layer on Leadframe.png"),
        ("Define Contact Points",    "DefineContactPointsCommand", "Define_Contact_Points.svg"),
        ("Pin Numbering",            "PinNumberingCommand",        "Pin_Numbering.svg"),
        ("Clear GDSII Import Cache", "ClearGDSCacheCommand",       "Clear_Cache.svg"),
        ("Create Desktop Shortcut",  "CreateDesktopShortcutCommand", "Desktop_Shortcut.svg"),
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


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("AdvancedMenuCommand", AdvancedMenuCommand())


# ── Workbench definition ───────────────────────────────────────────────────────

class MyWorkbench(FreeCADGui.Workbench):

    from Get_Path import get_icon
    MenuText = "Chip-Packaging Workbench"
    ToolTip  = "FreeCAD Chip-Packaging Workbench"
    Icon     = get_icon("my_icon.svg")

    # Short category label shown at the start of each toolbar row — a
    # QToolBar's windowTitle() (used for the "GDSII Tools" / etc. name in
    # View → Toolbars) is only ever shown inline when the toolbar is
    # floating/undocked, never when docked in the normal toolbar area, so
    # splitting one toolbar into several gave no visual cue for which
    # category you're looking at. See _inject_toolbar_labels().
    _TOOLBAR_LABELS = {
        "Technology Configuration": "Tech",
        "Import":                   "Import",
        "Rendering":                "Render",
        "Package Assembly":         "Package",
        "Wire Bonding":             "Bonding",
        "Wire Bonding Session":     "Confirm / Abort",
        "Trace Routing":            "Routing",
        "Trace Routing Session":    "Confirm / Abort / End",
        "Batch Route Session":      "Route All / Cancel",
        "Contact Point Pattern":    "Confirm / Undo / Abort",
        "Netlist":                  "Netlist",
        "Design Rule Check":        "DRC",
        "Thermal Simulation":       "Thermal",
        "Session and Help":         "Workbench",
    }

    def Initialize(self):
        try:
            from compat import QtCore as _QtCore
            import FreeCAD as _FreeCAD

            # ── Technology configuration bar (shown above main tools) ──────
            self.appendToolbar(
                "Technology Configuration",
                ["TechConfigCommand"],
            )

            # ── Import ───────────────────────────────────────────────────
            self.appendToolbar(
                "Import",
                [
                    "PCBImportCommand",
                    "PCBPlacementCommand",
                    "KicadImportCommand",
                    "GDSCommand",
                    "ImportChipProxyCommand",
                    "ChipTextureCommand",
                    "ViewInGDS3DCommand",
                ],
            )

            # ── Rendering (how already-imported geometry is displayed) ─────
            self.appendToolbar(
                "Rendering",
                [
                    "ToggleViaDetailCommand",
                    "ShowDetailLayerPanelCommand",
                    "ShowLayerSliderCommand",
                ],
            )

            # ── Package Assembly ─────────────────────────────────────────
            self.appendToolbar(
                "Package Assembly",
                [
                    "ChipTransformCommand",
                    "LeadframeLibraryCommand",
                    "SetContactPointsOnFaceCommand",
                    "InteractiveContactPointCommand",
                    "DetectPackagePadsCommand",
                    "StackDieCommand",
                    "ShowDiesPanelCommand",
                    "ContactPointSymmetryCommand",
                    "ContactPointPatternCommand",
                    "ShowContactPointPanelCommand",
                ],
            )

            # ── Wire Bonding ─────────────────────────────────────────────
            self.appendToolbar(
                "Wire Bonding",
                [
                    "WirebondCommand",
                    "WireBumpConfiguratorCommand",
                    "ProposeNetlistCommand",
                    "ImportNetlistCommand",
                    "ExportBondingDiagramCommand",
                ],
            )

            # ── Wire Bonding Session (contextual) ───────────────────────
            # Confirm / Abort only make sense while a bonding session is
            # actually active. Hidden at startup by _hide_wirebond_session_toolbar
            # below; shown/hidden dynamically by
            # wirebond.ManualWireBonding._set_session_toolbar_visible() as the
            # session starts/ends, so these two buttons are fast to reach
            # exactly when — and only when — they're relevant.
            self.appendToolbar(
                "Wire Bonding Session",
                [
                    "FinishWireBondingCommand",
                    "CancelWireBondingCommand",
                ],
            )

            # ── Trace Routing ─────────────────────────────────────────────
            self.appendToolbar(
                "Trace Routing",
                [
                    "InteractiveRouteCommand",
                    "TraceRoutingCommand",
                    "StartBatchRouteCommand",
                    "DragTraceCommand",
                    "BodyRouteCommand",
                ],
            )

            # ── Trace Routing Session (contextual) ──────────────────────
            # Confirm/Abort act on the single in-progress trace; End closes
            # the whole session. Hidden at startup by
            # _hide_trace_routing_session_toolbar below; shown/hidden
            # dynamically by
            # routing.TraceRoutingSession._set_session_toolbar_visible() as
            # the session starts/ends — same contextual-toolbar pattern as
            # "Wire Bonding Session" above.
            self.appendToolbar(
                "Trace Routing Session",
                [
                    "ConfirmTraceCommand",
                    "AbortTraceCommand",
                    "EndRoutingSessionCommand",
                ],
            )

            # ── Batch Route Session (contextual) ────────────────────────
            # Route All executes the whole queue and ends the session; Cancel
            # ends it without routing anything still queued. Hidden at
            # startup by _hide_batch_route_session_toolbar below; shown/
            # hidden dynamically by
            # routing.BatchRouteSession._set_session_toolbar_visible() —
            # same contextual-toolbar pattern as "Trace Routing Session".
            self.appendToolbar(
                "Batch Route Session",
                [
                    "RouteAllCommand",
                    "CancelBatchRouteCommand",
                ],
            )

            # ── Contact Point Pattern (contextual) ──────────────────────
            # Confirm keeps the placed points, Undo drops the last one,
            # Abort removes them all. Hidden at startup by
            # _hide_contact_point_pattern_toolbar below; shown/hidden
            # dynamically by
            # wirebond.ContactPointPatternCommand._set_session_toolbar_visible
            # — same contextual-toolbar pattern as the routing sessions.
            self.appendToolbar(
                "Contact Point Pattern",
                [
                    "ConfirmPatternCommand",
                    "UndoPatternCommand",
                    "AbortPatternCommand",
                ],
            )

            # ── Netlist (KiCad nets and the ratsnest) ────────────────────
            self.appendToolbar(
                "Netlist",
                [
                    "ShowNetsPanelCommand",
                    "UpdateRatsnestCommand",
                ],
            )

            # ── Design Rule Check ────────────────────────────────────────
            self.appendToolbar(
                "Design Rule Check",
                [
                    "ShowDRCPanelCommand",
                ],
            )

            # ── Thermal Simulation ───────────────────────────────────────
            self.appendToolbar(
                "Thermal Simulation",
                [
                    "AssignMaterialsCommand",
                    "ThermalExportCommand",
                ],
            )

            # ── Session and Help ─────────────────────────────────────────
            # NOTE: deliberately NOT named "Workbench" — FreeCAD's own
            # built-in workbench-selector widget already uses that exact
            # title, so a QToolBar of ours with the same windowTitle() was
            # never matched by findChildren() in _inject_toolbar_labels(),
            # sending it into an infinite once-a-second retry loop.
            self.appendToolbar(
                "Session and Help",
                [
                    "SessionMenuCommand",
                    "AdvancedMenuCommand",
                    "ChipThemeCommand",
                    "HelpGuideCommand",
                    "AboutCommand",
                ],
            )

            # Inject the status label into the tech config toolbar after Qt
            # has finished building it (singleShot defers until the event loop).
            # _inject_toolbar_labels MUST run before _inject_tech_status_label:
            # it rebuilds each toolbar's content (including "Technology
            # Configuration"), which would silently wipe out the status label
            # if it ran second — hence the shorter delay here.
            _QtCore.QTimer.singleShot(350, self._inject_toolbar_labels)
            _QtCore.QTimer.singleShot(400, self._inject_tech_status_label)
            _QtCore.QTimer.singleShot(400, self._hide_wirebond_session_toolbar)
            _QtCore.QTimer.singleShot(400, self._hide_trace_routing_session_toolbar)
            _QtCore.QTimer.singleShot(400, self._hide_batch_route_session_toolbar)
            _QtCore.QTimer.singleShot(400, self._hide_contact_point_pattern_toolbar)

            _FreeCAD.Console.PrintMessage("Toolbars initialized\n")
        except Exception as e:
            import FreeCAD as _FC
            _FC.Console.PrintError(f"Toolbar initialization failed: {e}\n")

    def _hide_wirebond_session_toolbar(self):
        """Hide the contextual Wire Bonding Session toolbar at startup — it
        only becomes visible while a bonding session is actually active
        (see wirebond.ManualWireBonding._set_session_toolbar_visible)."""
        try:
            from compat import QtWidgets as _QW
            import FreeCAD as _FC
            import FreeCADGui as _FCGui

            mw = _FCGui.getMainWindow()
            for tb in mw.findChildren(_QW.QToolBar):
                if tb.windowTitle() == "Wire Bonding Session":
                    tb.setVisible(False)
                    break
        except Exception as exc:
            import FreeCAD as _FC
            _FC.Console.PrintWarning(
                f"Wire Bonding Session toolbar hide failed: {exc}\n"
            )

    def _hide_trace_routing_session_toolbar(self):
        """Hide the contextual Trace Routing Session toolbar at startup — it
        only becomes visible while a routing session is actually active (see
        routing.TraceRoutingSession._set_session_toolbar_visible)."""
        try:
            from compat import QtWidgets as _QW
            import FreeCAD as _FC
            import FreeCADGui as _FCGui

            mw = _FCGui.getMainWindow()
            for tb in mw.findChildren(_QW.QToolBar):
                if tb.windowTitle() == "Trace Routing Session":
                    tb.setVisible(False)
                    break
        except Exception as exc:
            import FreeCAD as _FC
            _FC.Console.PrintWarning(
                f"Trace Routing Session toolbar hide failed: {exc}\n"
            )

    def _hide_batch_route_session_toolbar(self):
        """Hide the contextual Batch Route Session toolbar at startup — it
        only becomes visible while a batch route session is actually active
        (see routing.BatchRouteSession._set_session_toolbar_visible)."""
        try:
            from compat import QtWidgets as _QW
            import FreeCAD as _FC
            import FreeCADGui as _FCGui

            mw = _FCGui.getMainWindow()
            for tb in mw.findChildren(_QW.QToolBar):
                if tb.windowTitle() == "Batch Route Session":
                    tb.setVisible(False)
                    break
        except Exception as exc:
            import FreeCAD as _FC
            _FC.Console.PrintWarning(
                f"Batch Route Session toolbar hide failed: {exc}\n"
            )

    def _hide_contact_point_pattern_toolbar(self):
        """Hide the contextual Contact Point Pattern toolbar at startup — it
        only becomes visible while a pattern session is actually active (see
        wirebond.ContactPointPatternCommand._set_session_toolbar_visible)."""
        try:
            from compat import QtWidgets as _QW
            import FreeCADGui as _FCGui

            mw = _FCGui.getMainWindow()
            for tb in mw.findChildren(_QW.QToolBar):
                if tb.windowTitle() == "Contact Point Pattern":
                    tb.setVisible(False)
                    break
        except Exception as exc:
            import FreeCAD as _FC
            _FC.Console.PrintWarning(
                f"Contact Point Pattern toolbar hide failed: {exc}\n"
            )

    _TOOLBAR_LABEL_MAX_RETRIES = 15   # ~15 s — see _inject_toolbar_labels

    def _inject_toolbar_labels(self, _retry: int = 0):
        """
        Add a small category-name caption BELOW each toolbar's icons,
        compactly — a first attempt at this forced every category onto its
        own dedicated pair of full-width rows (via insertToolBarBreak),
        which turned 7 categories into 14 rows and ate most of the window
        (real user-reported regression). QToolBar can't stack its own
        content into 2 rows internally, so instead each toolbar's row of
        individual QAction buttons is replaced with ONE small composite
        widget — a horizontal button row on top, a caption directly under
        it — so the toolbar's overall FOOTPRINT shrinks to just that
        widget's size and several categories can still pack side by side
        on the same row exactly like before any of this was added, each
        just two text-lines tall instead of one.

        The QToolButtons are bound to the SAME QAction objects FreeCAD's
        command system already created (via setDefaultAction), so
        enable/disable state, tooltips, and click handling keep working
        unchanged — nothing about the underlying commands is touched, only
        how their existing buttons are visually arranged.

        Retries with a bounded count (not forever): a toolbar name that
        collides with something FreeCAD itself already uses (as
        "Workbench" did with FreeCAD's own workbench-selector widget)
        would otherwise never be found by findChildren(), sending this
        into a silent once-a-second retry loop for the rest of the
        session — cap it so a naming mistake like that fails loudly
        instead of running forever.
        """
        try:
            from compat import QtWidgets as _QW, QtCore as _QC
            import FreeCAD as _FC
            import FreeCADGui as _FCGui

            mw = _FCGui.getMainWindow()
            toolbars = {tb.windowTitle(): tb for tb in mw.findChildren(_QW.QToolBar)}

            names   = list(self._TOOLBAR_LABELS.keys())
            missing = [n for n in names if n not in toolbars]
            if missing:
                if _retry >= self._TOOLBAR_LABEL_MAX_RETRIES:
                    _FC.Console.PrintError(
                        f"Toolbar category labels: giving up after "
                        f"{self._TOOLBAR_LABEL_MAX_RETRIES} retries — "
                        f"toolbar(s) never found: {missing}. Likely a "
                        f"windowTitle() collision with an existing toolbar "
                        f"of the same name.\n"
                    )
                    return
                _FC.Console.PrintWarning(
                    f"Toolbar category labels: not found yet ({missing}) — "
                    f"retrying in 1 s ({_retry + 1}/{self._TOOLBAR_LABEL_MAX_RETRIES})\n"
                )
                _QC.QTimer.singleShot(
                    1000, lambda: self._inject_toolbar_labels(_retry + 1)
                )
                return

            for name in names:
                tb = toolbars[name]
                if getattr(tb, "_diCategoryLabelled", False):
                    continue   # already done (e.g. a retry pass)

                # Only real command buttons (icon present) — defensively
                # excludes any non-command widget action (e.g. the status
                # label _inject_tech_status_label adds to "Technology
                # Configuration") from being mis-bound to a QToolButton,
                # in case scheduling order ever changes again.
                actions = [
                    a for a in tb.actions()
                    if not a.isSeparator() and not a.icon().isNull()
                ]
                if not actions:
                    continue

                container = _QW.QWidget()
                outer = _QW.QVBoxLayout(container)
                outer.setContentsMargins(2, 0, 2, 1)
                outer.setSpacing(0)

                btn_row = _QW.QWidget()
                btn_lay = _QW.QHBoxLayout(btn_row)
                btn_lay.setContentsMargins(0, 0, 0, 0)
                btn_lay.setSpacing(0)
                for act in actions:
                    btn = _QW.QToolButton()
                    btn.setDefaultAction(act)
                    btn.setIconSize(tb.iconSize())
                    btn_lay.addWidget(btn)
                outer.addWidget(btn_row)

                lbl = _QW.QLabel(self._TOOLBAR_LABELS[name])
                lbl.setAlignment(_QC.Qt.AlignCenter)
                lbl.setStyleSheet(self._caption_style())
                outer.addWidget(lbl)

                tb.clear()   # detaches the actions from this toolbar's own
                             # layout without destroying them — they're
                             # owned by FreeCAD's command manager, and are
                             # now shown via the QToolButtons above instead.
                tb.addWidget(container)
                tb._diCategoryLabelled = True

            _FC.Console.PrintMessage("Toolbar category labels injected (compact)\n")
        except Exception as exc:
            import FreeCAD as _FC
            _FC.Console.PrintWarning(f"Toolbar category label injection failed: {exc}\n")

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
            lbl.setStyleSheet(self._status_label_style())

            target_tb.addSeparator()
            target_tb.addWidget(lbl)

            TechStatusBar.set_label(lbl)
            _FC.Console.PrintMessage("TechConfig: status label injected\n")

        except Exception as exc:
            import FreeCAD as _FC
            _FC.Console.PrintWarning(
                f"TechConfig: status label injection failed: {exc}\n"
            )

    # ── Chip skin ─────────────────────────────────────────────────────────
    # Both widgets below set an explicit per-widget stylesheet, which beats
    # anything inherited from the main window — so they have to ask for the
    # themed colours rather than receive them. Their light-theme originals
    # stay as the fallback for anyone who switches the skin off; a near-white
    # status label on a dark FreeCAD was exactly the problem.

    def _caption_style(self):
        try:
            from ui import ChipTheme
            flavour = ChipTheme.active_flavour()
            if flavour:
                import core.theme as _theme
                return _theme.caption_style(flavour)
        except Exception:
            pass
        return ("QLabel {"
                "  color: #546e7a;"
                "  font-size: 9px;"
                "  font-weight: bold;"
                "}")

    def _status_label_style(self):
        try:
            from ui import ChipTheme
            flavour = ChipTheme.active_flavour()
            if flavour:
                import core.theme as _theme
                return _theme.status_label_style(flavour)
        except Exception:
            pass
        return ("QLabel {"
                "  background: #F5F5F5;"
                "  border: 1px solid #BDBDBD;"
                "  border-radius: 3px;"
                "  padding: 2px 6px;"
                "  margin: 2px 4px;"
                "}")

    def Activated(self):
        """
        Put the chip skin on when this workbench comes to the front.

        Scoped to the workbench on purpose: leaving it restores whatever
        FreeCAD theme the user had, so installing this workbench never
        changes how the rest of FreeCAD looks.
        """
        try:
            from ui import ChipTheme
            # One-shot: the skin used to be on unless switched off. Anyone
            # carrying a flavour from that era is switched back to FreeCAD's
            # own colours here, once.
            ChipTheme.migrate_to_opt_in()
            if ChipTheme.is_enabled():
                ChipTheme.apply_theme()
            else:
                # Off, but a previous session may have left our viewport
                # colours behind — the stylesheet goes with the window, the
                # background does not.
                ChipTheme.ensure_native_background()
        except Exception as exc:
            import FreeCAD as _FC
            _FC.Console.PrintWarning(f"Chip theme not applied: {exc}\n")

    def Deactivated(self):
        try:
            from ui import ChipTheme
            ChipTheme.remove_theme()
        except Exception as exc:
            import FreeCAD as _FC
            _FC.Console.PrintWarning(f"Chip theme not removed: {exc}\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(MyWorkbench())
FreeCAD.Console.PrintMessage("GDSII Workbench registration attempted\n")
