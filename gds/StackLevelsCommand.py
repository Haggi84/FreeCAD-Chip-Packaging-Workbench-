# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Complete Stack from PDK — show the process levels this layout leaves empty.

The import builds geometry for what the GDS contains. A design that routes
on the top metals only therefore arrives as metal floating six microns above
the silicon, with Activ, the contacts, Metal1-5, the vias, MIM and Vmim —
all real levels of the process — missing entirely, because this particular
chip draws nothing on them.

The same thing the import offers as an option, available afterwards: a full
GDS import is expensive enough that finding the box unticked should not mean
doing it again. Running it a second time rebuilds, so it also serves to
correct a stack completed against the wrong PDK, and it can take them away
again.

The derivation is in core.stack_levels.
"""

import FreeCAD
import FreeCADGui
from compat import QtWidgets

import core.stack_levels as stack_levels
import core.gds_tech as gds_tech
from Get_Path import get_icon


def _imports_in(doc):
    """The imported GDS layouts in *doc* — the groups a stack belongs to."""
    return [obj for obj in doc.Objects
            if obj.isDerivedFrom("App::DocumentObjectGroup")
            and (obj.Name.startswith("GDS_Die") or obj.Label.startswith("GDS_Die"))]


def _choose(doc, parent):
    imports = _imports_in(doc)
    if not imports:
        QtWidgets.QMessageBox.information(
            parent, "Complete Stack",
            "There is no imported GDS layout in this document.\n\n"
            "This completes the stack of a chip imported with \"Load GDSII\": "
            "it needs the layers themselves to see which levels of the "
            "process the layout leaves empty.")
        return None

    for selected in FreeCADGui.Selection.getSelection():
        if selected in imports:
            return selected
        for parent_obj in getattr(selected, "InList", None) or []:
            if parent_obj in imports:
                return parent_obj

    if len(imports) == 1:
        return imports[0]
    labels = [obj.Label for obj in imports]
    label, ok = QtWidgets.QInputDialog.getItem(
        parent, "Complete Stack", "Which layout?", labels, 0, False)
    return imports[labels.index(label)] if ok else None


def _stackup_for(group, parent):
    """
    The stackup that describes this chip: its own technology, else the
    session's. Without one there are no heights to build from.
    """
    data = gds_tech.stackup_of(group)
    if data:
        return data
    try:
        from core.TechConfig import tech_config
        from core.Core_Functionality import parse_stackup_xml
        if tech_config.has_xml():
            return parse_stackup_xml(tech_config.get_xml()) or None
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[Stack] no stackup: {exc}\n")
    QtWidgets.QMessageBox.warning(
        parent, "Complete Stack",
        "This chip records no technology, and no stackup XML is configured.\n\n"
        "The levels are built at the heights the stackup states, so there is "
        "nothing to build from. Set one in Technology Configuration, or "
        "record the chip's own under ChipProxy ▸ SourceXML.")
    return None


def _footprint(group):
    """
    (xmin, ymin, xmax, ymax) of the chip in its own coordinates.

    Taken from the die body when there is one — it spans the die outline,
    where the loaded layers span only what this design happens to draw.
    """
    members = [obj for obj in group.Group or []
               if getattr(obj, "Shape", None) is not None
               and not obj.Shape.isNull()
               and not getattr(obj, stack_levels.IS_UNUSED, False)]
    body = [obj for obj in members if getattr(obj, "IsDieBody", False)]
    for source in (body, members):
        if not source:
            continue
        boxes = [obj.Shape.BoundBox for obj in source]
        return (min(b.XMin for b in boxes), min(b.YMin for b in boxes),
                max(b.XMax for b in boxes), max(b.YMax for b in boxes))
    return None


def complete_stack(doc, group, parent=None):
    """Build the empty levels of *group*. Returns the objects created."""
    parent = parent or FreeCADGui.getMainWindow()
    stackup = _stackup_for(group, parent)
    if not stackup:
        return []

    members = list(group.Group or [])
    keys = stack_levels.keys_in_document(doc, members)
    if not keys:
        QtWidgets.QMessageBox.warning(
            parent, "Complete Stack",
            f"Nothing in '{group.Label}' says which GDS layer it is, so which "
            "levels are empty cannot be worked out.")
        return []

    empty = stack_levels.unused(stackup, keys)
    already = [obj for obj in members
               if getattr(obj, stack_levels.IS_UNUSED, False)]
    if already:
        answer = QtWidgets.QMessageBox.question(
            parent, "Complete Stack",
            f"'{group.Label}' already shows {len(already)} empty level(s).\n\n"
            "Build them again, or remove them?",
            QtWidgets.QMessageBox.Retry | QtWidgets.QMessageBox.Discard
            | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Retry)
        if answer == QtWidgets.QMessageBox.Cancel:
            return []
        stack_levels.remove(doc)
        if answer == QtWidgets.QMessageBox.Discard:
            FreeCAD.Console.PrintMessage(
                f"[Stack] {len(already)} empty level(s) removed.\n")
            return []
        members = list(group.Group or [])

    if not empty:
        QtWidgets.QMessageBox.information(
            parent, "Complete Stack",
            f"This layout draws on every level the stackup defines — there is "
            f"nothing to add.\n\n{stack_levels.describe(stackup, keys)}")
        return []

    footprint = _footprint(group)
    if footprint is None:
        QtWidgets.QMessageBox.warning(
            parent, "Complete Stack",
            f"'{group.Label}' has no geometry to measure the die outline from.")
        return []

    import core.gds_pads as gds_pads
    placement = gds_pads.chip_top(group)[1]
    shift = stack_levels.shift_from_objects(members, stackup, placement)

    try:
        made = stack_levels.build(doc, footprint, empty, stackup, group=group,
                                  shift_mm=shift, placement=placement)
    except Exception as exc:
        import traceback
        FreeCAD.Console.PrintError(
            f"[Stack] could not build the levels: {exc}\n{traceback.format_exc()}\n")
        QtWidgets.QMessageBox.critical(parent, "Complete Stack", str(exc))
        return []

    QtWidgets.QMessageBox.information(
        parent, "Complete Stack",
        f"{len(made)} level(s) added at the heights the stackup states.\n\n"
        f"{stack_levels.describe(stackup, keys)}\n\n"
        "They are ghosted and labelled “[not in the layout]”: nothing "
        "is drawn there in this design, so they are left out of the material "
        "assignment, the thermal export and the design rule check.")
    return made


class CompleteStackCommand:
    """Build the PDK levels this layout does not draw on."""

    def GetResources(self):
        return {
            "MenuText": "Complete Stack from PDK",
            "ToolTip": (
                "Show the process levels this layout leaves empty. Each one "
                "gets a die-sized slab at the height and thickness the\n"
                "stackup states, so the model reads like the PDK's own "
                "stackup drawing instead of metal floating above the silicon. "
                "Run it again to rebuild or remove them."
            ),
            "Pixmap": get_icon("Complete_Stack.svg"),
        }

    def Activated(self):
        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        parent = FreeCADGui.getMainWindow()
        group = _choose(doc, parent)
        if group is None:
            return
        complete_stack(doc, group, parent)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("CompleteStackCommand", CompleteStackCommand())
