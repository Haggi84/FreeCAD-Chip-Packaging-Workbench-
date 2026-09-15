# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for dip_package_guard — another folder on sys.path providing
this workbench's top-level names.

Two halves: the detection logic on synthetic folders, and the real
environment the suite runs in. The second one is deliberately allowed to
fail on a machine that has a second copy of the project installed — that is
the situation it exists to surface.
"""

import importlib.util
import os
import shutil
import sys
import tempfile

from _harness import TestCase, REPO_ROOT

import dip_package_guard as guard


def run():
    tc = TestCase("package_guard")
    _check_detection(tc)
    _check_real_environment(tc)
    return tc.results


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


def _check_detection(tc):
    tmp = tempfile.mkdtemp(prefix="package_guard_test_")
    try:
        ours = os.path.join(tmp, "ours")
        clone = os.path.join(tmp, "clone")
        addon = os.path.join(tmp, "addon")
        unrelated = os.path.join(tmp, "unrelated")
        _touch(os.path.join(ours, "core", "__init__.py"))
        _touch(os.path.join(ours, "ui", "__init__.py"))
        _touch(os.path.join(ours, "compat.py"))
        _touch(os.path.join(clone, "core", "__init__.py"))
        _touch(os.path.join(addon, "compat.py"))
        _touch(os.path.join(unrelated, "something", "__init__.py"))

        tc.check("no conflict when nothing else provides our names",
                  guard.find_conflicts(ours, [unrelated, ours]) == [])

        found = guard.find_conflicts(ours, [clone, ours, addon, unrelated])
        by_name = {name: (winner, others) for name, winner, others in found}
        norm = guard._norm
        tc.check("a clone earlier on the path is reported as the copy Python uses",
                  by_name.get("core") == (norm(clone), [norm(clone)]), str(found))
        tc.check("an addon later on the path providing a plain module is reported "
                  "too, with our copy still winning",
                  by_name.get("compat") == (norm(ours), [norm(addon)]), str(found))
        tc.check("names only we provide are not reported", "ui" not in by_name)

        # FreeCAD's own Mod/Help holds Help.py. On a case-insensitive disk
        # a plain file-exists test finds "help.py" there, but Python would
        # never import it as `help`.
        builtin = os.path.join(tmp, "builtin_help")
        _touch(os.path.join(builtin, "Help.py"))
        _touch(os.path.join(ours, "help", "__init__.py"))
        tc.check("a module differing only in case is not a provider — Python's "
                  "import is case-sensitive even where the disk is not",
                  guard.providers("help", [builtin, ours]) == [norm(ours)],
                  str(guard.providers("help", [builtin, ours])))

        tc.check("the same folder spelled twice is one provider, not a conflict",
                  guard.find_conflicts(ours, [ours, os.path.join(ours, ".")]) == [])
        tc.check("a folder that is not on the path at all cannot be judged",
                  guard.find_conflicts(ours, [clone]) == [])

        message = guard.describe(found, ours)
        tc.check("describe: names the other folder and says which copy runs",
                  norm(clone) in message and "THE OTHER COPY IS USED" in message,
                  message)
        tc.check("describe: nothing to say without conflicts",
                  guard.describe([], ours) == "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _check_real_environment(tc):
    tc.check("the guard's own folder is this repository",
              guard._norm(guard.workbench_root()) == guard._norm(REPO_ROOT))
    conflicts = guard.find_conflicts(REPO_ROOT, sys.path)
    tc.check("nothing else on this machine's Python path provides this "
              "workbench's module names",
              conflicts == [], guard.describe(conflicts, REPO_ROOT))
    for name in guard.PACKAGES + guard.MODULES:
        spec = importlib.util.find_spec(name)
        origin = getattr(spec, "origin", None) or ""
        tc.check(f"'{name}' imports from this repository",
                  guard._norm(origin).startswith(guard._norm(REPO_ROOT)), origin)
