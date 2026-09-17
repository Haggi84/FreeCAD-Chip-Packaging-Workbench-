# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Detect Package Pads — contact points on the bond fingers of an imported
package model.

A package from the Leadframe Library has no contact points, and placing one
on every lead by hand is the slowest step in setting a bond up. This lists the
planes of near-horizontal faces in the selected model (see core.package_pads)
so the bond shelf can be picked out of them at a glance, and puts a contact
point on every face of the chosen planes.

It proposes rather than decides: which plane holds the bond fingers depends on
the model, and the list shows the evidence — how many faces, how big they are,
and how much of the plane runs around the outline.
"""

import FreeCAD
import FreeCADGui
import Part

from Get_Path import get_icon


def _world_shape(obj):
    shape = obj.Shape.copy()
    try:
        shape.Placement = obj.getGlobalPlacement()
    except Exception:
        pass
    return shape


def _selected_shape(objs):
    shapes = []
    for obj in objs:
        try:
            if obj.Shape.Faces:
                shapes.append(_world_shape(obj))
        except Exception:
            continue
    if not shapes:
        return None
    return shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)


def _existing_points(doc):
    points = []
    for obj in doc.Objects:
        if getattr(obj, "IsContactPoint", False):
            point = getattr(obj, "ContactPoint", None)
            if point is not None:
                points.append(FreeCAD.Vector(point))
    return points


def _choose_levels(QtWidgets, parent, levels, source_name):
    """The plane chooser. Returns the chosen row indices, or None."""
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Detect Package Pads")
    dialog.setMinimumWidth(560)
    layout = QtWidgets.QVBoxLayout(dialog)

    intro = QtWidgets.QLabel(
        f"Flat faces of <b>{source_name}</b>, grouped by the plane they lie in.<br>"
        "The bond shelf is usually the plane with many like-sized faces and a "
        "high share around the outline. Select one or more planes.")
    intro.setWordWrap(True)
    layout.addWidget(intro)

    table = QtWidgets.QTableWidget(len(levels), 5)
    table.setHorizontalHeaderLabels(
        ["Z (mm)", "Faces", "Smallest (mm2)", "Largest (mm2)", "Around the edge"])
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    for row, level in enumerate(levels):
        cells = (f"{level['z_mm']:.3f}", str(level["count"]),
                 f"{level['area_min_mm2']:.4f}", f"{level['area_max_mm2']:.4f}",
                 f"{level['ring_fraction'] * 100:.0f} %")
        for column, text in enumerate(cells):
            table.setItem(row, column, QtWidgets.QTableWidgetItem(text))
    table.resizeColumnsToContents()
    if levels:
        table.selectRow(0)
    layout.addWidget(table)

    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if not dialog.exec_():
        return None
    return sorted({index.row() for index in table.selectedIndexes()})


class DetectPackagePadsCommand:
    """Place contact points on the bond fingers of a selected package model."""

    def GetResources(self):
        return {
            "MenuText": "Detect Package Pads",
            "ToolTip":  "Find the bond-finger plane of a selected package model "
                        "and place a contact point on every finger",
            "Pixmap":   get_icon("Detect_Package_Pads.svg"),
        }

    def Activated(self):
        from compat import QtWidgets
        from core import package_pads
        from wirebond.SetContactPointsOnFaceCommand import (
            _create_housing_marker, _next_housing_index)

        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        parent = FreeCADGui.getMainWindow()
        title = "Detect Package Pads"

        selection = FreeCADGui.Selection.getSelection()
        shape = _selected_shape(selection)
        if shape is None:
            QtWidgets.QMessageBox.information(
                parent, title,
                "Select the package model first — the imported STEP body, or the "
                "leadframe you want bond fingers on.")
            return

        levels = package_pads.detect_levels(shape)
        if not levels:
            QtWidgets.QMessageBox.warning(
                parent, title,
                "No flat faces of a plausible pad size were found in the "
                "selection. The leads may be modelled as curved surfaces, or "
                "their faces may be larger than 150 mm2.")
            return

        source_name = (selection[0].Label if len(selection) == 1
                       else f"{len(selection)} objects")
        chosen = _choose_levels(QtWidgets, parent, levels, source_name)
        if not chosen:
            return

        points = package_pads.points_of(levels, chosen)
        fresh = package_pads.without_existing(points, _existing_points(doc))
        if not fresh:
            QtWidgets.QMessageBox.information(
                parent, title,
                f"All {len(points)} face(s) of the chosen plane(s) already have "
                "a contact point.")
            return

        doc.openTransaction("Detect Package Pads")
        try:
            index = _next_housing_index(doc)
            for offset, point in enumerate(fresh):
                _create_housing_marker(doc, selection[0].Name, point, index + offset)
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            FreeCAD.Console.PrintError(f"[PackagePads] {exc}\n")
            QtWidgets.QMessageBox.critical(parent, title, str(exc))
            return
        doc.recompute()

        try:
            panel = parent.findChild(QtWidgets.QDockWidget, "ContactPointPanel")
            if panel is not None:
                panel.populate()
        except Exception:
            pass

        skipped = len(points) - len(fresh)
        message = f"{len(fresh)} contact point(s) placed."
        if skipped:
            message += f"\n{skipped} face(s) already had one."
        QtWidgets.QMessageBox.information(parent, title, message)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("DetectPackagePadsCommand", DetectPackagePadsCommand())
