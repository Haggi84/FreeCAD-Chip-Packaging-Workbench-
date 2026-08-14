# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
"Create Desktop Shortcut" — the GUI half.

core/desktop_shortcut.py works out every path and generates both the
launcher macro and the PowerShell that makes the .lnk; this module writes
the files, renders the icon, and runs it.

The icon is rendered here rather than in core because the good version needs
Qt: the workbench's own .svg rasterised at each size Windows asks for. When
Qt cannot do that — no SVG image plugin in the build — core.ico draws a chip
motif from rectangles instead. Either way the .ico is assembled by
core.ico.build_ico, which is the part with the format rules in it and the
part that is tested.
"""

import os
import subprocess
import tempfile

import FreeCAD
import FreeCADGui
from compat import QtWidgets

import core.desktop_shortcut as ds
import core.ico as ico
import core.theme as theme
from Get_Path import get_icon


def _png_frames_from_svg(svg_path, sizes=ico.STANDARD_SIZES):
    """
    [(size, png_bytes), ...] rendered from an .svg, or [] if Qt cannot.

    Each size is rendered from the vector source rather than scaled from one
    bitmap — that is the whole reason for shipping several sizes, and a
    16 px icon downsampled from 256 is a grey smudge.
    """
    from compat import QtGui, QtCore

    if not svg_path or not os.path.isfile(svg_path):
        return []
    frames = []
    icon = QtGui.QIcon(svg_path)
    if icon.isNull():
        return []
    for size in sizes:
        pm = icon.pixmap(QtCore.QSize(size, size))
        if pm.isNull() or pm.width() != size:
            return []          # partial success is worse than the fallback
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.WriteOnly)
        if not pm.save(buf, "PNG"):
            return []
        frames.append((size, bytes(buf.data())))
        buf.close()
    return frames


def build_icon_file(path, flavour=None) -> str:
    """Write the .ico, preferring the workbench's own artwork."""
    flavour = flavour or theme.DEFAULT_FLAVOUR
    frames = []
    try:
        frames = _png_frames_from_svg(get_icon("my_icon.svg"))
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[Shortcut] SVG render skipped: {exc}\n")
    source = "workbench icon"
    if not frames:
        frames = [(n, ico.render_chip_icon_png(n, flavour))
                  for n in ico.STANDARD_SIZES]
        source = "generated chip motif"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ico.write_ico(path, ico.build_ico(frames))
    FreeCAD.Console.PrintMessage(
        f"[Shortcut] icon written ({source}, {len(frames)} sizes)\n")
    return path


def _run_powershell(script: str):
    """(ok, output). The script is passed as a file — a multi-line -Command
    argument is mangled by the shell's own quoting."""
    fd, ps1 = tempfile.mkstemp(suffix=".ps1")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(script)
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW    # no console flash
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", ps1],
            capture_output=True, text=True, timeout=60, creationflags=flags)
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, out.strip()
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            os.remove(ps1)
        except OSError:
            pass


def create_shortcut(desktop=None):
    """Do the whole job. Returns (ok, message)."""
    if not ds.is_windows():
        return False, ("Desktop shortcuts are created through the Windows "
                        "shell, so this command only works on Windows.")

    p = ds.plan(desktop)

    if not os.path.isfile(p["target"]):
        return False, (f"Could not find FreeCAD's executable at:\n{p['target']}\n\n"
                        "The shortcut would not start anything, so nothing was "
                        "created.")

    try:
        os.makedirs(os.path.dirname(p["macro"]), exist_ok=True)
        with open(p["macro"], "w", encoding="utf-8") as fh:
            fh.write(ds.launcher_macro_text())
    except OSError as exc:
        return False, f"Could not write the launcher macro:\n{exc}"

    try:
        build_icon_file(p["icon"])
    except Exception as exc:
        # An icon-less shortcut still works; say so rather than abandoning.
        FreeCAD.Console.PrintWarning(f"[Shortcut] icon failed: {exc}\n")
        p["icon"] = ""

    ok, out = _run_powershell(ds.shortcut_script(
        p["lnk"], p["target"], p["arguments"], p["icon"], p["workdir"],
        p["description"]))
    if not ok:
        return False, f"Windows refused to create the shortcut:\n{out}"
    if not os.path.isfile(p["lnk"]):
        return False, (f"PowerShell reported success but nothing appeared at:\n"
                        f"{p['lnk']}")
    return True, (f"Created:\n{p['lnk']}\n\n"
                   f"It starts:\n{p['target']}\n"
                   f"and opens straight into the Chip-Packaging workbench.")


class CreateDesktopShortcutCommand:
    """Put a chip-flavoured launcher on the Windows desktop."""

    def GetResources(self):
        return {
            "MenuText": "Create Desktop Shortcut",
            "ToolTip": (
                "Put an icon on the Windows desktop that starts FreeCAD and\n"
                "opens straight into the Chip-Packaging workbench.\n\n"
                "Creates a multi-size icon so it stays sharp in the taskbar\n"
                "as well as on the desktop."
            ),
            "Pixmap": get_icon("Desktop_Shortcut.svg"),
        }

    def Activated(self):
        parent = FreeCADGui.getMainWindow()
        if not ds.is_windows():
            QtWidgets.QMessageBox.information(
                parent, "Desktop shortcut",
                "This command creates a Windows .lnk and only works on "
                "Windows.")
            return

        p = ds.plan()
        exists = os.path.isfile(p["lnk"])
        question = (f"{'Replace' if exists else 'Create'} this shortcut?\n\n"
                     f"{p['lnk']}")
        answer = QtWidgets.QMessageBox.question(
            parent, "Desktop shortcut", question,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return

        ok, msg = create_shortcut()
        FreeCAD.Console.PrintMessage(f"[Shortcut] {msg.splitlines()[0]}\n")
        (QtWidgets.QMessageBox.information if ok
         else QtWidgets.QMessageBox.warning)(parent, "Desktop shortcut", msg)

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("CreateDesktopShortcutCommand",
                          CreateDesktopShortcutCommand())
