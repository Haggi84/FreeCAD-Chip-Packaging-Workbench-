# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
GUI smoke test — runs inside the full FreeCAD, not freecadcmd.

Start it with tests/run_gui_smoke.py, which launches FreeCAD on this macro
with a throwaway user folder (so nothing touches your own preferences) and
reads back the result.

The headless suite cannot see any of this: whether the workbench activates,
whether every toolbar button is backed by a registered command, whether the
dock panels build and work on a real document, and whether the start-up
reported an error. A failure in any of those leaves every headless test
green.
"""

import json
import os
import traceback

import FreeCAD
import FreeCADGui

_RESULT = os.environ.get("DIP_GUI_SMOKE_RESULT", "")
_checks = []


def _check(name, ok, detail=""):
    _checks.append({"name": name, "passed": bool(ok), "detail": str(detail)})


def _finish():
    ok = all(c["passed"] for c in _checks) and bool(_checks)
    if _RESULT:
        with open(_RESULT, "w", encoding="utf-8") as fh:
            json.dump({"passed": ok, "checks": _checks}, fh, indent=2)
    # Hard exit: no save prompts for the test document, and nothing written
    # back on the way out. The user folder is a throwaway one anyway.
    os._exit(0 if ok else 1)


def _report_view_text(QtWidgets):
    mw = FreeCADGui.getMainWindow()
    texts = []
    for dock in mw.findChildren(QtWidgets.QDockWidget):
        if "report" in (dock.objectName() + dock.windowTitle()).lower():
            for edit in dock.findChildren(QtWidgets.QTextEdit):
                texts.append(edit.toPlainText())
            for edit in dock.findChildren(QtWidgets.QPlainTextEdit):
                texts.append(edit.toPlainText())
    return "\n".join(texts)


def _run():
    try:
        from PySide import QtWidgets

        FreeCADGui.activateWorkbench("MyWorkbench")
        QtWidgets.QApplication.processEvents()
        _check("the workbench activates",
               FreeCADGui.activeWorkbench().__class__.__name__ == "MyWorkbench",
               FreeCADGui.activeWorkbench().__class__.__name__)

        wb = FreeCADGui.getWorkbench("MyWorkbench")
        toolbars = wb.getToolbarItems()
        registered = set(FreeCADGui.listCommands())
        for expected in ("Import", "Wire Bonding", "Design Rule Check",
                         "Thermal Simulation", "Session and Help"):
            _check(f"toolbar '{expected}' exists", expected in toolbars,
                   sorted(toolbars))
        missing = sorted({cmd for items in toolbars.values() for cmd in items
                          if cmd != "Separator" and cmd not in registered})
        _check("every toolbar button is backed by a registered command",
               not missing, missing)

        doc = FreeCAD.newDocument("GuiSmoke")
        import Part
        lid = doc.addObject("Part::Feature", "Lid")
        lid.Shape = Part.makeBox(10, 10, 0.3, FreeCAD.Vector(-5, -5, 1.0))
        wire = doc.addObject("Part::Feature", "BondWire_001")
        wire.Shape = Part.makeCylinder(0.0125, 2.0, FreeCAD.Vector(0, 0, 0.95),
                                       FreeCAD.Vector(1, 0, 0))
        doc.recompute()

        inactive_errors = []
        for items in toolbars.values():
            for cmd in items:
                if cmd == "Separator" or cmd not in registered:
                    continue
                try:
                    FreeCADGui.Command.get(cmd).isActive()
                except Exception as exc:
                    inactive_errors.append(f"{cmd}: {exc}")
        _check("every command's IsActive runs without raising",
               not inactive_errors, inactive_errors)

        mw = FreeCADGui.getMainWindow()
        FreeCADGui.runCommand("ShowDRCPanelCommand")
        QtWidgets.QApplication.processEvents()
        panel = mw.findChild(QtWidgets.QDockWidget, "DRCPanel")
        _check("the Design Rule Check panel opens", panel is not None)
        if panel is not None:
            panel.run_check()
            rules = {f.rule for f in panel._findings}
            _check("the DRC panel runs on a real document and finds the lid clash",
                   "lid-clearance" in rules, sorted(rules))

        FreeCADGui.runCommand("ShowContactPointPanelCommand")
        QtWidgets.QApplication.processEvents()
        _check("the Contact Point Browser opens",
               mw.findChild(QtWidgets.QDockWidget, "ContactPointPanel") is not None)

        labelled = [tb.windowTitle() for tb in mw.findChildren(QtWidgets.QToolBar)
                    if getattr(tb, "_diCategoryLabelled", False)]
        _check("toolbar category captions were injected",
               "Thermal Simulation" in labelled, labelled)

        report = _report_view_text(QtWidgets)
        bad = [line for line in report.splitlines()
               if any(marker in line for marker in (
                   "Failed to load commands", "Toolbar initialization failed",
                   "Failed to register WorkbenchState", "another folder on the Python path"))]
        _check("start-up reported no workbench error in the Report view",
               not bad, bad)
    except Exception as exc:
        _check("smoke test ran to completion", False,
               f"{exc}\n{traceback.format_exc()}")
    _finish()


def _start():
    from PySide import QtCore
    # Toolbar captions are injected on timers of up to ~1.4 s after
    # activation; give them time before looking.
    QtCore.QTimer.singleShot(4000, _run)


# Keep the callbacks referenced: a macro's globals can be released once it
# has finished executing, and a timer holding a dead function never fires.
FreeCADGui._dip_gui_smoke = (_run, _start, _finish)
from PySide import QtCore as _QtCore  # noqa: E402
_QtCore.QTimer.singleShot(1000, _start)
