# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Define Pads from GDS — pick the top contact areas and put them on the chip.

Automatic detection covers the pad conventions this workbench knows, and a
layout that follows none of them (SKY130, most obviously — see core.gds_pads)
imports with no pads at all, which leaves nothing to bond to. This command is
the way out: it shows what the file actually contains and takes whatever is
pointed at.

It runs after an import, on an existing chip, as often as needed — a pad ring
picked first, a bump field added afterwards. define_pads() is also what the
chip proxy import offers when it detects nothing, so both routes end in the
same dialog.
"""

import os

import FreeCAD
import FreeCADGui
from compat import QtWidgets

import core.gds_pads as gds_pads
import core.gds_tech as gds_tech
from Get_Path import get_icon


def _target_of(obj, targets):
    """The chip *obj* belongs to, if it is one of *targets* or part of one."""
    if obj in targets:
        return obj
    source = str(getattr(obj, "SourceObject", "") or "")
    if source:
        found = obj.Document.getObject(source)
        if found in targets:
            return found
    for parent in getattr(obj, "InList", None) or []:
        if parent in targets:
            return parent
    return None


def _choose_target(doc, parent):
    """
    Which chip the pads go on: the selected one, the only one, or asked for.

    A document with several dies is the ordinary case this workbench is for,
    so guessing is not acceptable — but asking when there is nothing to ask
    about is just a click in the way.
    """
    targets = gds_pads.chips_in(doc)
    if not targets:
        QtWidgets.QMessageBox.information(
            parent, "Define Pads",
            "There is no imported chip in this document yet.\n\n"
            "Import one with \"Import Chip (Layout Proxy)\" or \"Load GDSII\" "
            "first — pads are placed on top of it.")
        return None

    for selected in FreeCADGui.Selection.getSelection():
        found = _target_of(selected, targets)
        if found is not None:
            return found

    if len(targets) == 1:
        return targets[0]

    labels = [f"{t.Label}" for t in targets]
    label, ok = QtWidgets.QInputDialog.getItem(
        parent, "Define Pads", "Put the pads on which chip?", labels, 0, False)
    if not ok:
        return None
    return targets[labels.index(label)]


def _gds_for(target, parent):
    """The GDS this chip came from, asking for it if it was not recorded."""
    path = gds_pads.source_gds(target)
    if path and os.path.isfile(path):
        return path

    if path:
        message = (f"'{target.Label}' records its source as\n{path}\n\n"
                   "which is not there any more. Where is it now?")
    else:
        message = (f"'{target.Label}' does not record which GDS it came from "
                   "— it was imported before that was kept.\n\n"
                   "Which file is it?")
    QtWidgets.QMessageBox.information(parent, "Define Pads", message)
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        parent, "Select the chip's GDS file", os.path.dirname(path or ""),
        "GDS Files (*.gds *.GDS)")
    return path or ""


def _pdk_files(target):
    """(selected_layers, ihp_map) of the chip's own technology, for naming."""
    technology = gds_tech.technology_of(target)
    if not technology:
        return None, None
    selected_layers, ihp_map = None, None
    try:
        from core.Core_Functionality import parse_lyp, parse_map
        if technology.get("lyp_path") and os.path.isfile(technology["lyp_path"]):
            selected_layers = parse_lyp(technology["lyp_path"])[0]
        if technology.get("map_path") and os.path.isfile(technology["map_path"]):
            ihp_map = parse_map(technology["map_path"])
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[Pads] could not read the chip's PDK files: {exc}\n")
    return selected_layers, ihp_map


def define_pads(doc, target, gds_path=None, parent=None):
    """
    Show the picker for *target* and build whatever comes back.

    Returns the markers created, which is empty when nothing was picked or
    the dialog was cancelled.
    """
    parent = parent or FreeCADGui.getMainWindow()
    gds_path = gds_path or _gds_for(target, parent)
    if not gds_path or not os.path.isfile(gds_path):
        return []

    selected_layers, ihp_map = _pdk_files(target)
    from ui.PadPickerDialog import PadPickerDialog
    dialog = PadPickerDialog(
        gds_path, target.Label, parent, selected_layers, ihp_map,
        has_pads=bool(gds_pads.existing_pads(doc, target)))
    if dialog.exec_() != QtWidgets.QDialog.Accepted:
        return []

    min_mm, max_mm = dialog.size_bounds_mm()
    try:
        pads = gds_pads.pick_pads(gds_path, dialog.layer_keys(),
                                  dialog.cell_names(), min_mm, max_mm,
                                  selected_layers, ihp_map)
    except Exception as exc:
        import traceback
        FreeCAD.Console.PrintError(
            f"[Pads] reading the pads failed: {exc}\n{traceback.format_exc()}\n")
        QtWidgets.QMessageBox.critical(parent, "Define Pads", str(exc))
        return []

    if not pads:
        QtWidgets.QMessageBox.warning(
            parent, "Define Pads",
            "Nothing in what you picked is within the size bounds, so no pad "
            "was created.\n\nWiden the bounds, or pick a different layer — "
            "the Pad-sized column shows how many shapes each one would give.")
        return []

    markers = gds_pads.attach_pads(doc, target, pads,
                                   replace=dialog.replace_existing())
    named = sum(1 for marker in markers if marker.PadName)
    QtWidgets.QMessageBox.information(
        parent, "Define Pads",
        f"{len(markers)} pad(s) created on '{target.Label}'.\n"
        f"{named} of them took a name from the layout's own labels.\n\n"
        "They are contact points like any other: wire bonding, the netlist "
        "and the design rule check treat them the same.")
    return markers


class DefineChipPadsCommand:
    """Pick the chip's contact areas out of its GDS."""

    def GetResources(self):
        return {
            "MenuText": "Define Pads from GDS",
            "ToolTip": (
                "Pick the top contact areas out of the chip's GDS — by the "
                "layer they are drawn on, or the cell the padframe places —\n"
                "and put them on top of the chip as bondable contact points. "
                "For layouts whose pads automatic detection does not find, "
                "SKY130 among them."
            ),
            "Pixmap": get_icon("Define_Chip_Pads.svg"),
        }

    def Activated(self):
        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        parent = FreeCADGui.getMainWindow()
        target = _choose_target(doc, parent)
        if target is None:
            return
        define_pads(doc, target, parent=parent)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("DefineChipPadsCommand", DefineChipPadsCommand())
