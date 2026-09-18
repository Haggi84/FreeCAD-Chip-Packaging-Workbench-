# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Import KiCad — components, pads and nets from a board, and the ratsnest of
what is still unconnected.

The board (.kicad_pcb) carries the geometry: where each component sits, its
pads, their nets, and the path of its 3-D model. The netlist export (.net) is
optional and is used to check the board against the schematic — a board saved
before the last schematic change has pads on the wrong net, and every rubber
line drawn from it would be wrong.
"""

import os
import traceback

import FreeCAD
import FreeCADGui

from Get_Path import get_icon

_LIST_LIMIT = 12


def _browse(QtWidgets, parent, caption, patterns, start):
    path, _ = QtWidgets.QFileDialog.getOpenFileName(parent, caption, start, patterns)
    return path


def _ask(QtWidgets, parent, start_dir):
    """The import settings, or None."""
    from core import kicad

    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Import KiCad")
    dialog.setMinimumWidth(620)
    form = QtWidgets.QFormLayout(dialog)
    fields = {}

    def file_row(label, key, caption, patterns):
        row = QtWidgets.QHBoxLayout()
        edit = QtWidgets.QLineEdit()
        button = QtWidgets.QPushButton("Browse…")
        button.clicked.connect(
            lambda: edit.setText(_browse(QtWidgets, parent, caption, patterns,
                                         edit.text() or start_dir) or edit.text()))
        row.addWidget(edit, 1)
        row.addWidget(button)
        form.addRow(label, row)
        fields[key] = edit

    file_row("Board (.kicad_pcb):", "board", "Select KiCad board",
             "KiCad board (*.kicad_pcb);;All files (*)")
    file_row("Netlist (.net, optional):", "netlist", "Select KiCad netlist",
             "KiCad netlist (*.net);;All files (*)")

    model_row = QtWidgets.QHBoxLayout()
    model_dir = QtWidgets.QLineEdit()
    found = kicad.model_search_dirs()
    model_dir.setPlaceholderText(
        found[0] if found else "3-D model folder (KiCad's own is used when set)")
    browse_models = QtWidgets.QPushButton("Browse…")
    browse_models.clicked.connect(lambda: model_dir.setText(
        QtWidgets.QFileDialog.getExistingDirectory(
            parent, "KiCad 3-D model folder", model_dir.text() or start_dir)
        or model_dir.text()))
    model_row.addWidget(model_dir, 1)
    model_row.addWidget(browse_models)
    form.addRow("3-D models:", model_row)

    use_models = QtWidgets.QCheckBox(
        "Import each component's STEP model where it can be found")
    use_models.setChecked(True)
    use_models.setToolTip("A component whose model is missing still gets a "
                          "stand-in body the size of its courtyard, so it can "
                          "be placed and routed.")
    form.addRow(use_models)

    height = QtWidgets.QDoubleSpinBox()
    height.setRange(0.05, 50.0)
    height.setDecimals(2)
    height.setValue(1.0)
    height.setSuffix(" mm")
    form.addRow("Stand-in body height:", height)

    base_z = QtWidgets.QDoubleSpinBox()
    base_z.setRange(-1000.0, 1000.0)
    base_z.setDecimals(3)
    base_z.setValue(0.0)
    base_z.setSuffix(" mm")
    base_z.setToolTip("The plane the components stand on — the top of the "
                      "substrate or package they are placed upon. Their pads "
                      "land on it, which is where a trace has to reach.")
    form.addRow("Place on Z:", base_z)

    ratsnest = QtWidgets.QCheckBox("Draw the ratsnest after importing")
    ratsnest.setChecked(True)
    form.addRow(ratsnest)

    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)

    if not dialog.exec_():
        return None
    return {
        "board": fields["board"].text().strip(),
        "netlist": fields["netlist"].text().strip(),
        "model_dir": model_dir.text().strip(),
        "use_models": use_models.isChecked(),
        "height": height.value(),
        "base_z": base_z.value(),
        "ratsnest": ratsnest.isChecked(),
    }


def _mismatch_text(differences):
    lines = []
    for key, label in (("missing_from_board", "In the netlist but not on the board"),
                       ("missing_from_netlist", "On the board but not in the netlist")):
        refs = differences[key]
        if refs:
            shown = ", ".join(refs[:_LIST_LIMIT])
            if len(refs) > _LIST_LIMIT:
                shown += f", … (+{len(refs) - _LIST_LIMIT})"
            lines.append(f"{label}: {shown}")
    mismatch = differences["net_mismatch"]
    if mismatch:
        lines.append(f"Pads whose net differs between the two: {len(mismatch)}")
        for ref, pin, board_net, netlist_net in mismatch[:_LIST_LIMIT]:
            lines.append(f"  {ref}.{pin}: board '{board_net}', netlist '{netlist_net}'")
    return "\n".join(lines)


class KicadImportCommand:
    """Import components, pads and nets from a KiCad board."""

    def GetResources(self):
        return {
            "MenuText": "Import KiCad",
            "ToolTip":  "Import components with their 3-D models, pads and nets "
                        "from a KiCad board, and draw the ratsnest of what is "
                        "still unconnected",
            "Pixmap":   get_icon("KiCad_Import.svg"),
        }

    def Activated(self):
        from compat import QtWidgets, QtCore
        from core import components, kicad, ratsnest

        parent = FreeCADGui.getMainWindow()
        title = "Import KiCad"
        doc = FreeCAD.activeDocument()
        start_dir = (os.path.dirname(doc.FileName) if doc and doc.FileName
                     else os.path.expanduser("~"))

        settings = _ask(QtWidgets, parent, start_dir)
        if settings is None:
            return
        if not settings["board"] or not os.path.isfile(settings["board"]):
            QtWidgets.QMessageBox.warning(
                parent, title, "Select a KiCad board file (.kicad_pcb) to import.")
            return

        try:
            board = kicad.read_board(settings["board"])
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.critical(parent, title, str(exc))
            return

        if settings["netlist"]:
            try:
                netlist = kicad.read_netlist(settings["netlist"])
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(parent, title, str(exc))
                return
            differences = kicad.compare(board, netlist)
            text = _mismatch_text(differences)
            if text:
                answer = QtWidgets.QMessageBox.question(
                    parent, title,
                    "The board and the netlist do not agree:\n\n" + text +
                    "\n\nThe board is what gets imported. Continue?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No)
                if answer != QtWidgets.QMessageBox.Yes:
                    return

        if doc is None:
            doc = FreeCAD.newDocument("Assembly")

        model_dirs = kicad.model_search_dirs(
            [settings["model_dir"]] if settings["model_dir"] else [])

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        doc.openTransaction("Import KiCad")
        try:
            report = components.build_board(
                doc, board, base_z_mm=settings["base_z"], model_dirs=model_dirs,
                placeholder_height_mm=settings["height"],
                use_models=settings["use_models"])
            rats = ratsnest.rebuild(doc) if settings["ratsnest"] else None
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            QtWidgets.QApplication.restoreOverrideCursor()
            FreeCAD.Console.PrintError(f"[KiCad] {exc}\n{traceback.format_exc()}")
            QtWidgets.QMessageBox.critical(parent, title, f"Import failed: {exc}")
            return
        doc.recompute()
        QtWidgets.QApplication.restoreOverrideCursor()

        if FreeCAD.GuiUp:
            try:
                FreeCADGui.activeDocument().activeView().viewIsometric()
                FreeCADGui.SendMsgToActiveView("ViewFit")
            except Exception:
                pass
        for name in ("NetsPanel", "ContactPointPanel"):
            panel = parent.findChild(QtWidgets.QDockWidget, name)
            if panel is not None:
                panel.populate()

        message = (f"{len(report['components'])} component(s) with "
                   f"{report['pads']} pad(s).\n"
                   f"{report['with_model']} with a 3-D model, "
                   f"{report['placeholders']} as stand-in bodies.")
        if rats is not None:
            message += (f"\n\n{rats['links']} connection(s) still to route "
                        f"across {rats['nets']} net(s).")
        if report["skipped"]:
            message += "\n\nSkipped: " + ", ".join(
                f"{ref} ({why})" for ref, why in report["skipped"][:_LIST_LIMIT])
        QtWidgets.QMessageBox.information(parent, title, message)

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("KicadImportCommand", KicadImportCommand())
