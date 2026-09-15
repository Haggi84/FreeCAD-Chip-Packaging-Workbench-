# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Shared harness for headless FreeCAD smoke tests.

Run all tests with:
    "<FreeCAD install>\\bin\\freecadcmd.exe" tests\\run_all.py

No pytest dependency — these tests run inside FreeCAD's own bundled Python
via freecadcmd (no GUI), which may not have pytest installed, and pytest's
own collection/runner model doesn't fit a single long-lived FreeCAD process
well.  Each test_*.py module exposes a single ``run() -> list[TestResult]``.

Headless caveat
----------------
freecadcmd has no GUI: ``FreeCAD.GuiUp`` is False, ``obj.ViewObject`` is
always None, and ``FreeCADGui.addCommand`` does not exist at all.  Every
command file in this plugin calls ``FreeCADGui.addCommand(...)`` at import
time, and several packages' ``__init__.py`` transitively import command
files — so plain ``import gds.ToggleViaDetailCommand`` (etc.) fails
before reaching the target code.  ``load_module_from_file()`` below loads a
single .py file directly, bypassing its package's ``__init__.py`` and any
sibling command-registration side effects.
"""

import os
import sys
import importlib.util
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class TestResult:
    def __init__(self, name: str, passed: bool, message: str = ""):
        self.name = name
        self.passed = passed
        self.message = message


class TestCase:
    """Collects named pass/fail checks under a common prefix; never raises."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.results: list[TestResult] = []

    def check(self, name: str, condition: bool, message: str = "") -> bool:
        ok = bool(condition)
        self.results.append(TestResult(f"{self.prefix}: {name}", ok, message))
        return ok

    def check_raises_nothing(self, name: str, fn) -> bool:
        try:
            fn()
            self.results.append(TestResult(f"{self.prefix}: {name}", True))
            return True
        except Exception as exc:
            tb = traceback.format_exc()
            self.results.append(
                TestResult(f"{self.prefix}: {name}", False, f"{exc}\n{tb}")
            )
            return False


def load_module_from_file(mod_name: str, rel_path: str):
    """Load REPO_ROOT/rel_path as a standalone module named mod_name."""
    path = os.path.join(REPO_ROOT, *rel_path.split("/"))
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def new_document(name: str):
    """Fresh document, closing any leftover one of the same name first
    (safe to re-run within the same freecadcmd process)."""
    import FreeCAD
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)
    return FreeCAD.newDocument(name)
