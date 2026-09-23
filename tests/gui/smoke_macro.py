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


def _sky_like_gds():
    """
    A small SKY130-flavoured GDS: pad openings on 76/20, nothing on the
    datatypes automatic detection looks at. Written to a temporary file so
    the pad picker can be opened on something real.
    """
    import tempfile
    import gdstk

    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    pad = lib.new_cell("pad_70")
    pad.add(gdstk.rectangle((-35.0, -35.0), (35.0, 35.0), layer=76, datatype=20))
    top = lib.new_cell("chip_top")
    top.add(gdstk.rectangle((0.0, 0.0), (2000.0, 2000.0), layer=235, datatype=4))
    for x, y in ((200.0, 200.0), (1800.0, 200.0),
                 (200.0, 1800.0), (1800.0, 1800.0)):
        top.add(gdstk.Reference(pad, (x, y)))
        top.add(gdstk.Label("PAD", (x, y), layer=76, texttype=5))

    path = os.path.join(tempfile.mkdtemp(prefix="dip_smoke_gds_"), "sky.gds")
    lib.write_gds(path)
    return path


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

        # Define Port is a click-by-click session, so the command only
        # shows what it is worth in a running GUI: it opens a panel and
        # listens to the 3-D view.
        FreeCADGui.runCommand("DefinePortCommand")
        QtWidgets.QApplication.processEvents()
        from ports import PortCommand as _port_command
        _check("Define Port starts a drawing session",
               _port_command.session.active,
               _port_command.session.state)
        _check("...and it opens its panel",
               _port_command.session.panel is not None)
        _port_command.session.finish()
        QtWidgets.QApplication.processEvents()
        _check("...and Finish ends it",
               not _port_command.session.active)

        # ── the technology chooser and the pad picker ────────────
        # Both are Qt, so headless tests cannot reach them at all; what is
        # checked here is that they build, read a real file, and that the
        # pads they produce end up on the chip.
        from ui.TechnologyDialog import TechnologyDialog
        from ui.PadPickerDialog import PadPickerDialog
        import core.chip_proxy as chip_proxy
        import core.gds_pads as gds_pads
        import core.gds_tech as gds_tech

        tech_dialog = TechnologyDialog(mw, "some_chip.gds")
        QtWidgets.QApplication.processEvents()
        chosen = tech_dialog.technology()
        _check("the technology chooser starts on a configured PDK",
               bool(chosen["name"]), str(chosen))
        _check("...and offers the bundled ones to choose between",
               tech_dialog._combo.count() >= 3, tech_dialog._combo.count())
        tech_dialog.close()

        gds = _sky_like_gds()
        proxy_data = chip_proxy.extract_chip_proxy(gds)
        _check("a SKY130-style layout still imports with no pads detected",
               not proxy_data["pads"], str(len(proxy_data["pads"])))
        block = chip_proxy.build_chip_proxy_object(doc, proxy_data, name="SmokeChip")
        gds_tech.tag(block, chosen)

        picker = PadPickerDialog(gds, block.Label, mw)
        QtWidgets.QApplication.processEvents()
        _check("the pad picker suggests the layer the pad openings are on",
               picker.layer_keys() == [(76, 20)], str(picker.layer_keys()))
        min_mm, max_mm = picker.size_bounds_mm()
        pads = gds_pads.pick_pads(gds, picker.layer_keys(), picker.cell_names(),
                                  min_mm, max_mm)
        picker.close()
        _check("...and picking it finds the four pads", len(pads) == 4,
               str(len(pads)))

        markers = gds_pads.attach_pads(doc, block, pads)
        _check("the picked pads become contact points on the chip",
               len(markers) == 4 and all(m.IsContactPoint for m in markers))
        _check("...which know the technology of the die they are on",
               (gds_tech.technology_of(markers[0]) or {}).get("name") == chosen["name"])

        # ── the empty PDK levels ─────────────────────────────
        # The colours and ghosting are ViewObject work, which does not exist
        # headlessly — so building them is checked here, on a stand-in
        # import that draws on one level of the PDK.
        import core.stack_levels as stack_levels
        from gds import StackLevelsCommand
        import Part as _Part

        levels_group = doc.addObject("App::DocumentObjectGroup", "GDS_Die")
        top_metal = doc.addObject("Part::Feature", "Layer_TopMetal2_134")
        top_metal.Shape = _Part.makeBox(0.2, 0.2, 0.003,
                                        FreeCAD.Vector(0, 0, 0.0112303))
        for prop, value in (("GDSLayerID", 134), ("GDSDatatype", 0)):
            top_metal.addProperty("App::PropertyInteger", prop, "LOD", prop)
            setattr(top_metal, prop, value)
        levels_group.addObject(top_metal)
        doc.recompute()

        stackup = StackLevelsCommand._stackup_for(levels_group, mw)
        _check("the bundled PDK's stackup is available to build levels from",
               bool(stackup))
        if stackup:
            footprint = StackLevelsCommand._footprint(levels_group)
            keys = stack_levels.keys_in_document(doc, list(levels_group.Group))
            empty = stack_levels.unused(stackup, keys)
            _check("the levels this layout does not draw on are found",
                   len(empty) >= 10, str(len(empty)))
            built = stack_levels.build(doc, footprint, empty, stackup,
                                       group=levels_group)
            QtWidgets.QApplication.processEvents()
            _check("...and are built as ghosted slabs, not solid geometry",
                   all(o.ViewObject.Transparency == 80 for o in built),
                   str([o.ViewObject.Transparency for o in built][:3]))
            _check("...in the colour their material has in the stackup",
                   built[0].ViewObject.ShapeColor is not None)
            _check("...and they say in the tree that nothing is drawn there",
                   all("[not in the layout]" in o.Label for o in built))
            _check("removing them again leaves the imported layer alone",
                   stack_levels.remove(doc) == len(built)
                   and doc.getObject(top_metal.Name) is not None)

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
