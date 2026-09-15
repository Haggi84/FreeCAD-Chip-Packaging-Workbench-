# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Export for Thermal Simulation — write the assembly as per-material STEP
files, a Gmsh script with named volumes and boundary surfaces, a material
table and a manifest (see core.thermal_export).

If no part has a material yet, Assign Materials runs first. Parts that still
have none are listed before anything is written, because the export leaves
them out.
"""

import os
import traceback

import FreeCAD
import FreeCADGui

from Get_Path import get_icon

_PREVIEW_LIMIT = 12


def _preview(names):
    shown = "\n".join(f"  {n}" for n in names[:_PREVIEW_LIMIT])
    if len(names) > _PREVIEW_LIMIT:
        shown += f"\n  … and {len(names) - _PREVIEW_LIMIT} more"
    return shown


def _ask_boundary(parent, defaults):
    """The boundary conditions to write, or None when cancelled."""
    from compat import QtWidgets

    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Thermal boundary conditions")
    form = QtWidgets.QFormLayout(dialog)
    spins = {}
    for key, label, suffix, low, high, decimals in (
        ("die_power_W", "Power dissipated in the die", " W", 0.0, 1.0e4, 3),
        ("heat_sink_temperature_C", "Heat-sink temperature (underside)", " °C", -273.0, 1000.0, 1),
        ("convection_W_per_m2K", "Convection coefficient (other faces)", " W/m²K", 0.0, 1.0e6, 1),
        ("ambient_temperature_C", "Ambient temperature", " °C", -273.0, 1000.0, 1),
    ):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        spin.setValue(defaults[key])
        form.addRow(f"{label}:", spin)
        spins[key] = spin
    note = QtWidgets.QLabel(
        "Named as surfaces and a volume in the Gmsh script, with these values "
        "in the manifest. The solver applies them.")
    note.setWordWrap(True)
    form.addRow(note)
    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    if not dialog.exec_():
        return None
    return {key: spin.value() for key, spin in spins.items()}


class ThermalExportCommand:
    """Export the active document as a thermal simulation model."""

    def GetResources(self):
        return {
            "MenuText": "Export for Thermal Simulation",
            "ToolTip":  "Write every part with a material as per-material STEP "
                        "files, plus a Gmsh script with boundary surfaces, a "
                        "material table and a manifest",
            "Pixmap":   get_icon("Thermal_Export.svg"),
        }

    def Activated(self):
        from compat import QtWidgets, QtCore
        from core import materials, thermal_export
        from thermal.MaterialsCommand import active_stackup

        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        parent = FreeCADGui.getMainWindow()
        title = "Export for Thermal Simulation"

        parts = materials.physical_parts(doc)
        if not parts:
            QtWidgets.QMessageBox.information(
                parent, title, "The document contains no solid parts to export.")
            return

        if not any(hasattr(o, materials.PROPERTY) for o in parts):
            doc.openTransaction("Assign Materials")
            materials.assign_materials(doc, stackup_data=active_stackup())
            doc.commitTransaction()

        missing = [o.Name for o in parts if materials.material_of(o) is None]
        if len(missing) == len(parts):
            QtWidgets.QMessageBox.warning(
                parent, title,
                "No part has a material, so there is nothing to export.\n\n"
                "Set one under Material ▸ PackageMaterial in the property editor.")
            return
        if missing:
            answer = QtWidgets.QMessageBox.question(
                parent, title,
                f"{len(missing)} of {len(parts)} part(s) have no material and "
                f"will be left out:\n\n{_preview(missing)}\n\n"
                "Set them under Material ▸ PackageMaterial in the property "
                "editor to include them.\n\nExport the rest now?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return

        boundary = _ask_boundary(parent, thermal_export.DEFAULT_BOUNDARY)
        if boundary is None:
            return

        start_dir = (os.path.dirname(doc.FileName) if doc.FileName
                     else os.path.expanduser("~"))
        out_dir = QtWidgets.QFileDialog.getExistingDirectory(
            parent, "Export thermal model to folder", start_dir)
        if not out_dir:
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            summary = thermal_export.export_thermal_model(
                doc, out_dir, basename=doc.Label, boundary=boundary)
        except ValueError as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(parent, title, str(exc))
            return
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            FreeCAD.Console.PrintError(
                f"[ThermalExport] {exc}\n{traceback.format_exc()}")
            QtWidgets.QMessageBox.critical(parent, title, f"Export failed: {exc}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()

        msg = (f"{summary['parts']} part(s) in {len(summary['materials'])} "
               f"material(s) written to\n{summary['directory']}\n\n"
               + "\n".join(summary["files"]))
        source = summary["boundary_conditions"]["HeatSource"]
        if source.get("physical_volume") is None:
            msg += "\n\nThere is no silicon in the model — apply the die power by hand."
        if summary["overlaps_resolved"]:
            msg += (f"\n\n{len(summary['overlaps_resolved'])} filler part(s) "
                    "were cut where they overlapped other parts.")
        if summary["overlaps_remaining"]:
            msg += (f"\n\n{len(summary['overlaps_remaining'])} overlap(s) between "
                    "parts of different materials remain — see the manifest. "
                    "Fix the geometry before meshing.")
        if summary["skipped"]:
            msg += f"\n\n{len(summary['skipped'])} part(s) left out — see the manifest."
        box = (QtWidgets.QMessageBox.warning if summary["overlaps_remaining"]
               else QtWidgets.QMessageBox.information)
        box(parent, title, msg)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ThermalExportCommand", ThermalExportCommand())
