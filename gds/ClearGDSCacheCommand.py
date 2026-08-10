# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Clear the GDSII import cache.

Importing a full chip is expensive — measured on a real 15 MB, 70-layer
layout: about 70 s to build 92,000 solids and 630,000 faces. The result is
therefore serialised to disk and reused, which brings a re-import of the
same file down to roughly 10 s.

The cache key already folds in the GDS file's modification time and every
import option, so a stale entry can never be served for a file that has
changed. This command exists for the two cases the key cannot cover:
reclaiming disk space, and discarding entries after a workbench update
changes how geometry is built.
"""

import os
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from core.Core_Functionality import (clear_gds_cache, gds_cache_dir,
                                      gds_cache_size)
from Get_Path import get_icon


def _human(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes:.0f} {unit}" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024.0
    return f"{nbytes:.1f} GB"


class ClearGDSCacheCommand:

    def GetResources(self):
        return {
            "MenuText": "Clear GDSII Import Cache",
            "ToolTip": (
                "Delete cached GDSII import results.\n\n"
                "Imports are cached so re-opening the same layout is about\n"
                "ten times faster. The cache is keyed on the file's\n"
                "modification time and the import options, so it never\n"
                "serves stale geometry — clear it only to reclaim disk space."
            ),
            "Pixmap": get_icon("Clear_Cache.svg"),
        }

    def Activated(self):
        count, size = gds_cache_size()
        parent = FreeCADGui.getMainWindow()
        if count == 0:
            QtWidgets.QMessageBox.information(
                parent, "GDSII import cache",
                f"The cache is already empty.\n\n{gds_cache_dir()}")
            return
        answer = QtWidgets.QMessageBox.question(
            parent, "Clear GDSII import cache",
            f"Delete {count} cached import(s), freeing {_human(size)}?\n\n"
            f"{gds_cache_dir()}\n\n"
            "Nothing in your documents is affected — the next import of each "
            "layout simply takes full time again.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        removed, freed = clear_gds_cache()
        FreeCAD.Console.PrintMessage(
            f"[GDSCache] Removed {removed} cached import(s), "
            f"freed {_human(freed)}.\n")
        QtWidgets.QMessageBox.information(
            parent, "GDSII import cache",
            f"Removed {removed} cached import(s), freeing {_human(freed)}.")

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ClearGDSCacheCommand", ClearGDSCacheCommand())
