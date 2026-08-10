# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Open the selected chip in GDS3D — the fast external viewer.

The division of labour this implements:

  FreeCAD holds the PROXY plus the semantic layer every tool depends on —
  contact points, footprint, real stack thickness. Measured on a 46 MB,
  3.2-million-polygon layout, that proxy builds in 0.78 s, and routing,
  wire bonding and the DRC all work against it unchanged. Full geometry is
  never needed for any of them.

  GDS3D holds the LOOKING. It renders the layout as triangles with no CAD
  kernel involved, which is precisely why it copes with layouts FreeCAD
  cannot, and equally why it cannot route a trace or run a check. It also
  owns the Gmsh export (its F key writes .geo and .pro).

GDS3D is launched as a SEPARATE PROCESS and nothing here links against it.
That is not incidental: GDS3D is GPL-2 (forced by the Gmsh code it
contains), which is incompatible with this workbench's GPL-3-or-later for
combining the two into one work. Writing a file it reads and starting it as
its own program keeps them at arm's length.

NOTE: the launch path has not been exercised against a real GDS3D binary
here — the command line and process-file format follow its documented
interface. The file generation is covered by tests; the spawn is not.
"""

import os
import subprocess
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

import core.gds3d_process as gds3d_process
from Get_Path import get_icon

_PREF_GROUP = "User parameter:BaseApp/Preferences/Mod/DI-PASSIONATE"
_PREF_KEY = "GDS3DExecutable"


def _prefs():
    return FreeCAD.ParamGet(_PREF_GROUP)


def get_executable() -> str:
    try:
        return _prefs().GetString(_PREF_KEY, "")
    except Exception:
        return ""


def set_executable(path: str) -> None:
    try:
        _prefs().SetString(_PREF_KEY, path or "")
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[GDS3D] could not store the path: {exc}\n")


def _ask_for_executable(parent):
    """Locate GDS3D once; the choice is remembered."""
    filt = "GDS3D executable (GDS3D*.exe GDS3D*)" if os.name == "nt" else "GDS3D executable (GDS3D* *)"
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        parent, "Locate the GDS3D executable", "", filt)
    if path:
        set_executable(path)
    return path


def _selected_proxy():
    try:
        sel = FreeCADGui.Selection.getSelection()
    except Exception:
        sel = []
    for o in sel:
        if getattr(o, "IsChipProxy", False):
            return o
        for member in (getattr(o, "Group", None) or []):
            if getattr(member, "IsChipProxy", False):
                return member
    return None


def _process_file_for(block):
    """
    Generate the process file describing this chip's stack, or (None, why).

    Everything needed is already recorded on the proxy when it is imported:
    the GDS it came from and the PDK files that described it.
    """
    gds = getattr(block, "SourceGDS", "") or ""
    if not gds or not os.path.isfile(gds):
        return None, None, ("This proxy does not record the GDS file it came "
                             "from (SourceGDS), so the viewer cannot be told "
                             "what to open.")
    lyp = getattr(block, "SourceLYP", "") or ""
    xml = getattr(block, "SourceXML", "") or ""

    lyp_layers = []
    if lyp and os.path.isfile(lyp):
        try:
            from core.tech.parsers import parse_lyp
            lyp_layers, _colours = parse_lyp(lyp)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[GDS3D] could not read the .lyp ({exc}); using default "
                "colours.\n")

    # Layers to describe: everything the .lyp knows, else whatever is in the
    # GDS itself.
    if lyp_layers:
        selected = [{"layer_id": e["layer_id"], "datatype": e["datatype"]}
                    for e in lyp_layers]
    else:
        try:
            from core.Core_Functionality import get_gds_layer
            selected = [{"layer_id": int(l), "datatype": int(d)}
                        for l, d in sorted(get_gds_layer(gds))]
        except Exception as exc:
            return None, None, f"Could not read the layers from the GDS: {exc}"

    stack_mm = {}
    if xml and os.path.isfile(xml):
        try:
            from core.tech.parsers import parse_stackup_xml
            from core.tech.stackup import build_stack_mm_from_xml
            stack_mm = build_stack_mm_from_xml(
                selected, {}, parse_stackup_xml(xml)) or {}
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[GDS3D] could not read the stackup ({exc}); using nominal "
                "layer heights.\n")

    entries = gds3d_process.layer_entries(selected, lyp_layers, stack_mm)
    if not entries:
        return None, None, "No layers could be described for the viewer."

    from core.Core_Functionality import gds_cache_dir
    out_dir = gds_cache_dir().parent / "gds3d"
    path = os.path.join(str(out_dir), f"{block.Name}_process.txt")
    try:
        gds3d_process.write_process_file(
            path, entries, title=f"{block.Label or block.Name} — from the active PDK")
    except Exception as exc:
        return None, None, f"Could not write the process file: {exc}"
    return path, gds, None


class ViewInGDS3DCommand:
    """Open the selected chip proxy in the external GDS3D viewer."""

    def GetResources(self):
        return {
            "MenuText": "View in GDS3D",
            "ToolTip": (
                "Open the selected chip's full layout in GDS3D, the external\n"
                "OpenGL viewer, generating its process file from the active\n"
                "PDK (stack heights, thicknesses and layer colours).\n\n"
                "FreeCAD keeps the fast proxy — routing, bonding and the DRC\n"
                "all work on it — while the viewer handles looking at full\n"
                "detail and the Gmsh export (its F key).\n\n"
                "Requires GDS3D to be installed separately; you will be asked\n"
                "for it once."
            ),
            "Pixmap": get_icon("View_GDS3D.svg"),
        }

    def Activated(self):
        parent = FreeCADGui.getMainWindow()
        block = _selected_proxy()
        if block is None:
            QtWidgets.QMessageBox.warning(
                parent, "Select a chip proxy",
                "Select an imported chip proxy (its block or its group) "
                "first.")
            return

        exe = get_executable()
        if not exe or not os.path.isfile(exe):
            QtWidgets.QMessageBox.information(
                parent, "GDS3D not configured",
                "GDS3D is a separate program (GPL-2) and is not bundled with "
                "this workbench.\n\nBuild or install it from "
                "https://github.com/trilomix/GDS3D, then point this at the "
                "executable — the choice is remembered.")
            exe = _ask_for_executable(parent)
            if not exe:
                return

        process_file, gds, error = _process_file_for(block)
        if error:
            QtWidgets.QMessageBox.warning(parent, "Cannot open in GDS3D", error)
            return

        topcell = getattr(block, "SourceCell", "") or None
        argv = gds3d_process.build_command(exe, process_file, gds, topcell)
        FreeCAD.Console.PrintMessage(
            f"[GDS3D] process file: {process_file}\n"
            f"[GDS3D] launching: {' '.join(argv)}\n")
        try:
            # Detached: the viewer is its own program with its own window,
            # and FreeCAD must not block while it is open.
            subprocess.Popen(argv, close_fds=True)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                parent, "Could not start GDS3D",
                f"{exc}\n\nCommand:\n{' '.join(argv)}\n\n"
                "Check the executable path (it is remembered, so clear it by "
                "picking a different file).")
            return
        FreeCAD.Console.PrintMessage(
            "[GDS3D] Viewer started. Press F in it to export the geometry "
            "for Gmsh (.geo + .pro).\n")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ViewInGDS3DCommand", ViewInGDS3DCommand())
