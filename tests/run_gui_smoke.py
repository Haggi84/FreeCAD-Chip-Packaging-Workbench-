# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Launch the GUI smoke test (tests/gui/smoke_macro.py) in the full FreeCAD.

Usage (any Python 3, including FreeCAD's own):
    "C:\\Program Files\\FreeCAD 1.1\\bin\\python.exe" tests\\run_gui_smoke.py

A FreeCAD window opens for a few seconds and closes by itself. It runs with
a throwaway user folder (FREECAD_USER_HOME), so your preferences, recent
files and theme are untouched. Set DIP_FREECAD_EXE to use a FreeCAD that is
not in the usual place. On a headless Linux machine, run it under xvfb-run.

Exits 0 when every check passed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_MACRO = os.path.join(_HERE, "gui", "smoke_macro.py")
_TIMEOUT_S = 300


def _freecad_executable():
    candidates = [os.environ.get("DIP_FREECAD_EXE", "")]
    exe_dir = os.path.dirname(sys.executable)
    candidates += [os.path.join(exe_dir, "FreeCAD.exe"), os.path.join(exe_dir, "freecad")]
    candidates += [r"C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe"]
    candidates += [shutil.which(n) or "" for n in ("FreeCAD", "freecad")]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def main():
    exe = _freecad_executable()
    if exe is None:
        print("FreeCAD executable not found — set DIP_FREECAD_EXE.")
        return 2

    home = tempfile.mkdtemp(prefix="dip_gui_smoke_")
    result = os.path.join(home, "result.json")
    env = dict(os.environ, FREECAD_USER_HOME=home, DIP_GUI_SMOKE_RESULT=result)
    try:
        try:
            subprocess.run([exe, _MACRO], env=env, timeout=_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            print(f"FreeCAD did not finish within {_TIMEOUT_S} s.")
            return 1
        if not os.path.isfile(result):
            print("No result was written — FreeCAD did not run the smoke macro.")
            return 1
        with open(result, encoding="utf-8") as fh:
            data = json.load(fh)
        for check in data["checks"]:
            mark = "PASS" if check["passed"] else "FAIL"
            print(f"[{mark}] {check['name']}")
            if not check["passed"] and check["detail"]:
                print(f"       {check['detail']}")
        failed = sum(not c["passed"] for c in data["checks"])
        print(f"{len(data['checks']) - failed} passed, {failed} failed")
        return 0 if data["passed"] else 1
    finally:
        shutil.rmtree(home, ignore_errors=True)


sys.exit(main())
