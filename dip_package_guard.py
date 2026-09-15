# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Detect another folder on sys.path that provides one of this workbench's
top-level package names.

FreeCAD puts every Mod folder on sys.path, and Python imports a top-level
name from whichever folder comes first. This workbench uses generic names —
core, ui, gds, session — so a second copy of the project, or any addon that
happens to use one of those names, earlier on the path silently replaces
this workbench's code with its own: stale behaviour, or an ImportError for a
module the other copy does not have. It has happened here before, which is
why core/drc.py is still loaded by file path.

Nothing here fixes the clash — only removing or renaming the other folder
does — but it turns a baffling failure into a message naming both folders.

Standard library only, and named so that nothing else is likely to shadow it.
"""

import os

PACKAGES = ("core", "gds", "drc", "routing", "wirebond", "session", "ui",
            "pcb", "housing", "leadframe", "help", "thermal")
MODULES = ("compat", "Get_Path", "version")


def _norm(path):
    return os.path.normcase(os.path.realpath(path))


def workbench_root():
    """This workbench's own folder — the one this file is in."""
    return os.path.dirname(os.path.abspath(__file__))


def _entries(folder, cache):
    if folder not in cache:
        try:
            cache[folder] = set(os.listdir(folder))
        except OSError:
            cache[folder] = set()
    return cache[folder]


def providers(name, search_path):
    """
    Folders on *search_path* that provide top-level *name*, in path order,
    each listed once.

    Names are compared with their exact case, from the folder listing. Python
    imports case-sensitively even on a case-insensitive file system, so
    FreeCAD's own Mod/Help folder, which holds Help.py, does not provide
    `help` — although os.path.isfile("Mod/Help/help.py") says it exists.
    """
    found, cache = [], {}
    for entry in search_path:
        if not isinstance(entry, str) or not entry:
            continue
        folder = _norm(entry)
        if folder in found:
            continue
        entries = _entries(folder, cache)
        is_package = (name in entries
                      and "__init__.py" in _entries(os.path.join(folder, name), cache))
        if is_package or name + ".py" in entries:
            found.append(folder)
    return found


def find_conflicts(root, search_path):
    """
    [(name, winner, others)] for every name this workbench provides that some
    other folder on *search_path* provides as well. *winner* is the folder
    Python will actually import it from.
    """
    root = _norm(root)
    conflicts = []
    for name in PACKAGES + MODULES:
        folders = providers(name, search_path)
        if root not in folders:
            continue
        others = [f for f in folders if f != root]
        if others:
            conflicts.append((name, folders[0], others))
    return conflicts


def describe(conflicts, root):
    """A message for the Report view, or "" when there is nothing to report."""
    if not conflicts:
        return ""
    root = _norm(root)
    lines = ["DI-PASSIONATE workbench: another folder on the Python path provides "
             "the same module names as this workbench:"]
    for name, winner, others in conflicts:
        state = ("this workbench's copy is used" if winner == root
                 else f"THE OTHER COPY IS USED: {winner}")
        lines.append(f"  {name}: also in {', '.join(others)} — {state}")
    lines.append("Remove or rename the other folder; while both exist, which "
                 "code runs depends on folder order.")
    return "\n".join(lines) + "\n"
