# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Import Netlist and Export Bonding Diagram commands.

Import Netlist reads a CSV of connections (see core.netlist), asks for the
wire parameters with the same dialog as manual bonding, and bonds every
connection it can match. Export Bonding Diagram writes the plan-view SVG and
the wire table (see core.bonding_diagram).
"""

import os
import traceback

import FreeCAD
import FreeCADGui

from Get_Path import get_icon

_LIST_LIMIT = 15


def _refresh_contact_point_panel():
    try:
        from compat import QtWidgets
        panel = FreeCADGui.getMainWindow().findChild(QtWidgets.QDockWidget, "ContactPointPanel")
        if panel is not None:
            panel.populate()
    except Exception:
        pass


class ImportNetlistCommand:
    """Bond every connection listed in a netlist CSV."""

    def GetResources(self):
        return {
            "MenuText": "Import Netlist",
            "ToolTip":  "Bond die pads to package pins from a CSV netlist "
                        "(columns: net, from, to). Pads are matched by name, "
                        "layout label, pin number or lead.",
            "Pixmap":   get_icon("Import_Netlist.svg"),
        }

    def Activated(self):
        from compat import QtWidgets
        from core import netlist
        from wirebond.WirebondConfigurator import WirebondConfigurator
        from wirebond.ManualWireBonding import place_bond_wire

        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        parent = FreeCADGui.getMainWindow()
        title = "Import Netlist"

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent, "Select netlist", os.path.dirname(doc.FileName or ""),
            "Netlist (*.csv);;All files (*)")
        if not path:
            return
        try:
            rows = netlist.read_netlist_csv(path)
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(parent, title, str(exc))
            return

        dialog = WirebondConfigurator(parent)
        if not dialog.exec_():
            return
        config = dialog.get_config()

        doc.openTransaction("Import Netlist")
        try:
            report = netlist.apply_netlist(
                doc, rows, lambda a, b: place_bond_wire(doc, a, b, config))
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            FreeCAD.Console.PrintError(f"[Netlist] {exc}\n{traceback.format_exc()}")
            QtWidgets.QMessageBox.critical(parent, title, f"Import failed: {exc}")
            return
        doc.recompute()
        _refresh_contact_point_panel()

        msg = (f"{len(report['placed'])} wire(s) placed, "
               f"{len(report['updated'])} existing wire(s) renamed, "
               f"{len(report['failed'])} connection(s) not bonded.")
        if report["failed"]:
            msg += "\n\n" + "\n".join(
                f"  {row['net'] or '(no net)'} {row['from']} → {row['to']}: {reason}"
                for row, reason in report["failed"][:_LIST_LIMIT])
            if len(report["failed"]) > _LIST_LIMIT:
                msg += f"\n  … and {len(report['failed']) - _LIST_LIMIT} more"
        box = (QtWidgets.QMessageBox.warning if report["failed"]
               else QtWidgets.QMessageBox.information)
        box(parent, title, msg)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


class ExportBondingDiagramCommand:
    """Write the bonding diagram SVG and the wire table CSV."""

    def GetResources(self):
        return {
            "MenuText": "Export Bonding Diagram",
            "ToolTip":  "Write a plan-view bonding diagram (SVG) and a wire "
                        "table (CSV) with net, pads, span, length and loop height",
            "Pixmap":   get_icon("Bonding_Diagram.svg"),
        }

    def Activated(self):
        from compat import QtWidgets
        from core import bonding_diagram

        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        parent = FreeCADGui.getMainWindow()
        title = "Export Bonding Diagram"

        start_dir = (os.path.dirname(doc.FileName) if doc.FileName
                     else os.path.expanduser("~"))
        out_dir = QtWidgets.QFileDialog.getExistingDirectory(
            parent, "Export bonding diagram to folder", start_dir)
        if not out_dir:
            return
        try:
            svg_path, csv_path = bonding_diagram.write_bonding_diagram(doc, out_dir)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(parent, title, str(exc))
            return
        except Exception as exc:
            FreeCAD.Console.PrintError(f"[BondingDiagram] {exc}\n{traceback.format_exc()}")
            QtWidgets.QMessageBox.critical(parent, title, f"Export failed: {exc}")
            return
        QtWidgets.QMessageBox.information(
            parent, title, f"Written:\n{svg_path}\n{csv_path}")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ImportNetlistCommand", ImportNetlistCommand())
    FreeCADGui.addCommand("ExportBondingDiagramCommand", ExportBondingDiagramCommand())
