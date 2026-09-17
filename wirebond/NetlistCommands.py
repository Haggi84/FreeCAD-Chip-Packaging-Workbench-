# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Netlist and bonding-diagram commands.

Propose Netlist writes a first pinout for a die and package that have none,
Import Netlist bonds the connections in a CSV, Export Bonding Diagram writes
the plan view and the wire table. The work itself is in core.netlist and
core.bonding_diagram; what is here is the dialogs.

Each step is also a plain function, so the Contact Point Browser's buttons and
the toolbar commands run exactly the same code.
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
        panel = FreeCADGui.getMainWindow().findChild(QtWidgets.QDockWidget,
                                                     "ContactPointPanel")
        if panel is not None:
            panel.populate()
    except Exception:
        pass


def _document_dir(doc):
    return os.path.dirname(doc.FileName) if doc.FileName else os.path.expanduser("~")


def _failed_lines(failed):
    lines = ["  {} {} -> {}: {}".format(row["net"] or "(no net)", row["from"],
                                        row["to"], reason)
             for row, reason in failed[:_LIST_LIMIT]]
    if len(failed) > _LIST_LIMIT:
        lines.append(f"  ... and {len(failed) - _LIST_LIMIT} more")
    return "\n".join(lines)


# ── the steps, each usable from a button or a command ────────────────────────

def import_netlist(parent, doc, path=None):
    """Bond the connections in a netlist CSV. True when wires were placed."""
    from compat import QtWidgets
    from core import netlist
    from wirebond.WirebondConfigurator import WirebondConfigurator
    from wirebond.ManualWireBonding import place_bond_wire

    title = "Import Netlist"
    if path is None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent, "Select netlist", _document_dir(doc),
            "Netlist (*.csv);;All files (*)")
        if not path:
            return False
    try:
        rows = netlist.read_netlist_csv(path)
    except (OSError, ValueError) as exc:
        QtWidgets.QMessageBox.warning(parent, title, str(exc))
        return False

    dialog = WirebondConfigurator(parent)
    if not dialog.exec_():
        return False
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
        return False
    doc.recompute()
    _refresh_contact_point_panel()

    message = (f"{len(report['placed'])} wire(s) placed, "
               f"{len(report['updated'])} existing wire(s) renamed, "
               f"{len(report['failed'])} connection(s) not bonded.")
    if report["failed"]:
        message += "\n\n" + _failed_lines(report["failed"])
    box = (QtWidgets.QMessageBox.warning if report["failed"]
           else QtWidgets.QMessageBox.information)
    box(parent, title, message)
    return bool(report["placed"])


def propose_netlist(parent, doc):
    """Write a proposed pinout, and offer to bond it straight away."""
    from compat import QtWidgets
    from core import netlist

    title = "Propose Netlist"
    rows, report = netlist.propose_connections(doc)
    if report["problem"]:
        QtWidgets.QMessageBox.warning(parent, title, report["problem"])
        return False

    default = os.path.join(_document_dir(doc), f"{doc.Label}_netlist.csv")
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        parent, "Save proposed netlist", default, "Netlist (*.csv)")
    if not path:
        return False

    comments = [
        f"Proposed pinout for {doc.Label} - EDIT BEFORE USE.",
        f"{len(rows)} connection(s) from {report['die']} die pad(s) and "
        f"{report['package']} package pin(s).",
        "Each die pad is bonded to the pin facing it, in ring order. The nets "
        "are placeholders unless the layout labels its pads.",
    ]
    if report["unpaired_die"]:
        comments.append("Unpaired die pads: " + ", ".join(report["unpaired_die"]))
    if report["unpaired_package"]:
        comments.append("Unpaired package pins: " + ", ".join(report["unpaired_package"]))
    try:
        netlist.write_netlist_csv(rows, path, comments)
    except OSError as exc:
        QtWidgets.QMessageBox.critical(parent, title, str(exc))
        return False

    summary = (f"{len(rows)} connection(s) written to\n{path}\n\n"
               f"Die pads: {report['die']}    Package pins: {report['package']}\n"
               f"Wires that would cross: {report['crossings']}")
    if report["unpaired_die"] or report["unpaired_package"]:
        summary += (f"\nUnpaired: {len(report['unpaired_die'])} die pad(s), "
                    f"{len(report['unpaired_package'])} package pin(s)")
    summary += ("\n\nThis is a starting point, not a pinout: edit the file for "
                "the connections your design needs.\n\nBond it as it stands now?")
    answer = QtWidgets.QMessageBox.question(
        parent, title, summary,
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No)
    if answer == QtWidgets.QMessageBox.Yes:
        return import_netlist(parent, doc, path)
    return False


def export_netlist(parent, doc):
    """Write the document's existing bond wires as a netlist CSV."""
    from compat import QtWidgets
    from core import netlist

    title = "Export Netlist"
    default = os.path.join(_document_dir(doc), f"{doc.Label}_netlist.csv")
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        parent, "Save netlist", default, "Netlist (*.csv)")
    if not path:
        return False
    try:
        count = netlist.export_netlist_csv(doc, path)
    except OSError as exc:
        QtWidgets.QMessageBox.critical(parent, title, str(exc))
        return False
    if not count:
        QtWidgets.QMessageBox.information(
            parent, title, "The document has no bond wires yet, so the netlist "
            "is empty apart from its header.")
        return False
    QtWidgets.QMessageBox.information(
        parent, title, f"{count} connection(s) written to\n{path}")
    return True


def export_bonding_diagram(parent, doc):
    """Write the plan-view diagram and the wire table."""
    from compat import QtWidgets
    from core import bonding_diagram

    title = "Export Bonding Diagram"
    out_dir = QtWidgets.QFileDialog.getExistingDirectory(
        parent, "Export bonding diagram to folder", _document_dir(doc))
    if not out_dir:
        return False
    try:
        svg_path, csv_path = bonding_diagram.write_bonding_diagram(doc, out_dir)
    except ValueError as exc:
        QtWidgets.QMessageBox.warning(parent, title, str(exc))
        return False
    except Exception as exc:
        FreeCAD.Console.PrintError(f"[BondingDiagram] {exc}\n{traceback.format_exc()}")
        QtWidgets.QMessageBox.critical(parent, title, f"Export failed: {exc}")
        return False
    QtWidgets.QMessageBox.information(
        parent, title, f"Written:\n{svg_path}\n{csv_path}")
    return True


# ── commands ─────────────────────────────────────────────────────────────────

class ProposeNetlistCommand:
    """Write a first pinout for a die and package that have none."""

    def GetResources(self):
        return {
            "MenuText": "Propose Netlist",
            "ToolTip":  "Pair every die pad with the package pin facing it, in "
                        "ring order, and write it as a netlist CSV to edit",
            "Pixmap":   get_icon("Propose_Netlist.svg"),
        }

    def Activated(self):
        doc = FreeCAD.activeDocument()
        if doc is not None:
            propose_netlist(FreeCADGui.getMainWindow(), doc)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


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
        doc = FreeCAD.activeDocument()
        if doc is not None:
            import_netlist(FreeCADGui.getMainWindow(), doc)

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
        doc = FreeCAD.activeDocument()
        if doc is not None:
            export_bonding_diagram(FreeCADGui.getMainWindow(), doc)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ProposeNetlistCommand", ProposeNetlistCommand())
    FreeCADGui.addCommand("ImportNetlistCommand", ImportNetlistCommand())
    FreeCADGui.addCommand("ExportBondingDiagramCommand", ExportBondingDiagramCommand())
