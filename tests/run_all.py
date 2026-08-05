# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless smoke-test runner.

Usage (Windows):
    "C:\\Program Files\\FreeCAD 1.1\\bin\\freecadcmd.exe" tests\\run_all.py

Exits 0 if every check passed, 1 otherwise — suitable for CI.

Results are also written to tests/results.log.  FreeCAD's own native console
output (recompute progress bars, GDS/mesh diagnostics) is flushed through a
separate buffer than Python's stdout and can appear out of order — or be cut
off entirely — when this script's stdout is piped/redirected.  Writing the
final summary straight to a file sidesteps that ambiguity entirely.
"""

import os
import sys
import traceback

# freecadcmd does not add the running script's own directory to sys.path
# (unlike a normal CPython invocation), so sibling imports below would
# otherwise fail with ModuleNotFoundError.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from _harness import TestResult

import test_wirebond_geometry
import test_leadframe_build
import test_gds_import
import test_performance_mode
import test_pin_numbering
import test_via_clustering
import test_housing_build
import test_perf_mode_sync
import test_workbench_state
import test_lod_state
import test_add_lid
import test_chip_proxy
import test_chip_transform_proxy
import test_leadframe_library_chip_proxy
import test_trace_routing
import test_trace_walkaround
import test_trace_obstacles
import test_routing_frame
import test_drc
import test_batch_route
import test_face_symmetry

MODULES = [
    test_wirebond_geometry,
    test_leadframe_build,
    test_gds_import,
    test_performance_mode,
    test_pin_numbering,
    test_via_clustering,
    test_perf_mode_sync,
    test_housing_build,
    test_workbench_state,
    test_lod_state,
    test_add_lid,
    test_chip_proxy,
    test_chip_transform_proxy,
    test_leadframe_library_chip_proxy,
    test_trace_routing,
    test_trace_walkaround,
    test_trace_obstacles,
    test_routing_frame,
    test_drc,
    test_batch_route,
    test_face_symmetry,
]

RESULTS_LOG = os.path.join(_THIS_DIR, "results.log")


def main() -> int:
    all_results: list[TestResult] = []

    for mod in MODULES:
        try:
            all_results.extend(mod.run())
        except Exception as exc:
            all_results.append(TestResult(
                f"{mod.__name__}: MODULE CRASH", False,
                f"{exc}\n{traceback.format_exc()}"
            ))

    passed = [r for r in all_results if r.passed]
    failed = [r for r in all_results if not r.passed]

    lines = []
    lines.append("=" * 70)
    lines.append(f"RESULTS: {len(passed)} passed, {len(failed)} failed, {len(all_results)} total")
    lines.append("=" * 70)
    for r in all_results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"[{mark}] {r.name}")
        if not r.passed and r.message:
            for line in r.message.splitlines():
                lines.append(f"       {line}")
    lines.append("=" * 70)
    lines.append(f"{len(failed)} check(s) FAILED." if failed else "All checks passed.")

    report = "\n".join(lines)
    with open(RESULTS_LOG, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(report)

    return 1 if failed else 0


# NOTE: freecadcmd imports this script as a module named after its filename
# (__name__ == "run_all"), not "__main__" as a normal `python script.py`
# invocation would — the usual `if __name__ == "__main__":` guard never
# fires under freecadcmd, silently skipping main() entirely.  Call it
# unconditionally instead.
sys.exit(main())
