# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Assign Materials — tag every physical part with what it is made of.

Runs core.materials.assign_materials() on the active document and reports
what it assigned, what it kept (anything already set, including choices made
by hand) and what it could not decide. Any part's material can be changed
afterwards in the property editor, under Material ▸ PackageMaterial.
"""

import FreeCAD
import FreeCADGui

from Get_Path import get_icon

_DETAIL_LIMIT = 200


def active_stackup():
    """
    The stackup of the active Technology Configuration profile, or None —
    it is what says which metal each GDS layer is made of.
    """
    try:
        from core.TechConfig import tech_config
        if not tech_config.has_xml():
            return None
        from core.Core_Functionality import parse_stackup_xml
        return parse_stackup_xml(tech_config.get_xml()) or None
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[Materials] could not read the active stackup: {exc}\n")
        return None


def _details(report):
    lines = []
    for title, rows in (("Assigned", report["assigned"]), ("Kept", report["kept"])):
        if rows:
            lines.append(f"{title}:")
            lines += [f"  {name}: {mat}  ({source})" for name, mat, source in rows[:_DETAIL_LIMIT]]
    if report["unassigned"]:
        lines.append("Without a material:")
        lines += [f"  {name}: {reason}" for name, reason in report["unassigned"][:_DETAIL_LIMIT]]
    return "\n".join(lines)


class AssignMaterialsCommand:
    """Assign a material to every physical part of the active document."""

    def GetResources(self):
        return {
            "MenuText": "Assign Materials",
            "ToolTip":  "Tag every solid with its material — from the stackup, "
                        "the leadframe and housing settings, or the kind of part. "
                        "Parts it cannot decide are listed for you to set.",
            "Pixmap":   get_icon("Assign_Materials.svg"),
        }

    def Activated(self):
        from compat import QtWidgets
        from core import materials

        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        parent = FreeCADGui.getMainWindow()

        doc.openTransaction("Assign Materials")
        try:
            report = materials.assign_materials(doc, stackup_data=active_stackup())
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            FreeCAD.Console.PrintError(f"[Materials] {exc}\n")
            QtWidgets.QMessageBox.critical(parent, "Assign Materials", str(exc))
            return

        n_assigned = len(report["assigned"])
        n_kept = len(report["kept"])
        n_missing = len(report["unassigned"])

        box = QtWidgets.QMessageBox(parent)
        box.setWindowTitle("Assign Materials")
        box.setIcon(QtWidgets.QMessageBox.Warning if n_missing
                    else QtWidgets.QMessageBox.Information)
        box.setText(f"{n_assigned} part(s) assigned, {n_kept} kept, "
                    f"{n_missing} without a material.")
        info = ("Change a part's material in the property editor under "
                "Material ▸ PackageMaterial. A choice made there is kept when "
                "this is run again.")
        if n_missing:
            info += ("\n\nParts without a material are left out of the "
                     "thermal export.")
        box.setInformativeText(info)
        box.setDetailedText(_details(report))
        box.exec_()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("AssignMaterialsCommand", AssignMaterialsCommand())
