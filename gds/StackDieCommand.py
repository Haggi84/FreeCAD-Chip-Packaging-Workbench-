# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Stack Die — put one die on another, with the adhesive between them.

Stacking by hand means typing a Z offset that has to equal the base die's top
plus the die-attach thickness, and remembering that the pads move with the
die. Getting it wrong is invisible: the stack looks right and every loop
height above it is wrong.

This does the arithmetic from the geometry, builds the die attach as real
material (it carries the heat out and sets the height of everything above),
and reports the two things a stack hides: how much of the upper die hangs
over nothing, and which pads of the die below it now covers.
"""

import FreeCAD
import FreeCADGui

from Get_Path import get_icon


def _die_choices(dies):
    return [f"{die.name or die.block.Name} — tier {die.tier}, "
            f"{die.width_mm:.3f} x {die.length_mm:.3f} x {die.thickness_mm:.3f} mm"
            for die in dies]


def _ask(QtWidgets, parent, dies):
    """(die to move, base die, attach thickness, centre) or None."""
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Stack Die")
    dialog.setMinimumWidth(460)
    form = QtWidgets.QFormLayout(dialog)

    choices = _die_choices(dies)
    upper = QtWidgets.QComboBox()
    upper.addItems(choices)
    upper.setCurrentIndex(len(dies) - 1)
    base = QtWidgets.QComboBox()
    base.addItems(choices)
    form.addRow("Die to stack:", upper)
    form.addRow("On top of:", base)

    attach = QtWidgets.QDoubleSpinBox()
    attach.setRange(0.0, 5.0)
    attach.setDecimals(4)
    attach.setSingleStep(0.005)
    attach.setValue(0.025)
    attach.setSuffix(" mm")
    attach.setToolTip("Thickness of the die-attach adhesive. 0 places the die "
                      "directly on the one below, with no adhesive modelled.")
    form.addRow("Die attach:", attach)

    centre = QtWidgets.QCheckBox("Centre it on the die below")
    centre.setChecked(True)
    centre.setToolTip("Leave unticked to keep the die where it is in X and Y "
                      "and only set its height.")
    form.addRow(centre)

    note = QtWidgets.QLabel(
        "The die's pads move with it, and their stored positions move too, so "
        "bonds land where the pads now are.")
    note.setWordWrap(True)
    form.addRow(note)

    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)

    if not dialog.exec_():
        return None
    return (dies[upper.currentIndex()], dies[base.currentIndex()],
            attach.value(), centre.isChecked())


class StackDieCommand:
    """Place a die on another die, with its die attach."""

    def GetResources(self):
        return {
            "MenuText": "Stack Die",
            "ToolTip":  "Put one die on top of another with the die attach "
                        "between them, and report overhang and covered pads",
            "Pixmap":   get_icon("Stack_Die.svg"),
        }

    def Activated(self):
        from compat import QtWidgets
        from core import dies as die_model

        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        parent = FreeCADGui.getMainWindow()
        title = "Stack Die"

        dies = die_model.identify(doc)
        if len(dies) < 2:
            QtWidgets.QMessageBox.information(
                parent, title,
                f"Stacking needs two dies; this document has {len(dies)}. "
                "Import the second chip first.")
            return

        chosen = _ask(QtWidgets, parent, dies)
        if chosen is None:
            return
        die, base, attach_mm, centre = chosen
        if die is base:
            QtWidgets.QMessageBox.warning(
                parent, title, "A die cannot be stacked on itself.")
            return

        doc.openTransaction("Stack Die")
        try:
            report = die_model.stack_die(doc, die, base, attach_mm, centre)
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            FreeCAD.Console.PrintError(f"[Dies] {exc}\n")
            QtWidgets.QMessageBox.critical(parent, title, str(exc))
            return
        doc.recompute()

        for name in ("ContactPointPanel", "DiesPanel"):
            try:
                panel = parent.findChild(QtWidgets.QDockWidget, name)
                if panel is not None:
                    panel.populate()
            except Exception:
                pass

        message = (f"{die.name} is now tier {report['tier']}, on {base.name}.\n"
                   f"Moved by {report['delta'].z:.4f} mm in Z.")
        if report["attach"] is not None:
            message += f"\nDie attach: {attach_mm * 1000.0:.0f} µm."
        if report["overhang_mm2"] > 1e-6:
            message += (f"\n\n{report['overhang_mm2']:.4f} mm² of {die.name} "
                        "hangs over nothing — check it is supported.")
        if report["covered_pads"]:
            covered = ", ".join(pad for pad, _holder, _cover in report["covered_pads"][:10])
            message += (f"\n\n{len(report['covered_pads'])} pad(s) are now under "
                        f"a die and cannot be bonded:\n{covered}")
        box = (QtWidgets.QMessageBox.warning
               if report["covered_pads"] or report["overhang_mm2"] > 1e-6
               else QtWidgets.QMessageBox.information)
        box(parent, title, message)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("StackDieCommand", StackDieCommand())
