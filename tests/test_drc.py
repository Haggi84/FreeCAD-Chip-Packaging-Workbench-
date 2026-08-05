# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.drc — the post-hoc design rule check.

The property that matters most here is the SAME-NET ENDPOINT EXEMPTION: a
trace/wire legitimately touches the pad it starts and ends on, and without
excluding that specific pad from the clearance check, every routed object in
a document would flag itself against its own landing pad on the very first
run. That is tested explicitly, not just assumed from the implementation.
"""

import FreeCAD
import Part

from _harness import TestCase, new_document, load_module_from_file

# Loaded by file path, not `import core.drc`: this Mod install shares
# FreeCAD's sys.path with another Mod folder that also defines a top-level
# `core` package (a separate git clone of this same project). Whichever one
# FreeCAD's own Mod-folder scan puts first on sys.path "wins" the `core`
# name for the whole process, so `import core.drc` can silently resolve
# against the OTHER install's core/ package instead of this repo's — and
# fail with "No module named 'core.drc'" whenever this repo has a core/
# module the other clone hasn't been git-pulled to yet, exactly as happened
# here. Loading by explicit file path sidesteps the package-name collision
# entirely, matching load_module_from_file's documented purpose.
drc = load_module_from_file("drc_test_target", "core/drc.py")

V = FreeCAD.Vector


def _add_trace(doc, name, shape, width_mm=0.3, waypoints=None):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    obj.addProperty("App::PropertyBool", "IsRoutingTrace", "Routing", "")
    obj.IsRoutingTrace = True
    obj.addProperty("App::PropertyLength", "TraceWidth", "Routing", "")
    obj.TraceWidth = width_mm
    if waypoints:
        obj.addProperty("App::PropertyVectorList", "Waypoints", "Routing", "")
        obj.Waypoints = waypoints
    return obj


def run():
    tc = TestCase("drc")

    # ── clearance: violation vs. no false positive ───────────────────────────
    doc = new_document("TestDRCClearance")
    try:
        trace = _add_trace(doc, "Trace_001", Part.makeBox(5.0, 0.3, 0.05, V(0, 0, 0)))
        near = doc.addObject("Part::Feature", "CopperNear")
        near.Shape = Part.makeBox(1.0, 1.0, 0.1, V(5.1, -0.5, 0))   # 0.1 mm from the trace
        far = doc.addObject("Part::Feature", "CopperFar")
        far.Shape = Part.makeBox(1.0, 1.0, 0.1, V(20.0, -0.5, 0))   # far away
        doc.recompute()

        tight = drc.run_drc(doc, min_clearance_mm=0.5, min_trace_width_mm=0.0)
        tc.check("run_drc: flags a real clearance violation",
                  any(f.rule == "clearance" and "CopperNear" in f.object_names for f in tight),
                  f"got {tight}")
        tc.check("run_drc: does not flag the far-away copper",
                  not any("CopperFar" in f.object_names for f in tight), f"got {tight}")

        loose = drc.run_drc(doc, min_clearance_mm=0.05, min_trace_width_mm=0.0)
        tc.check("run_drc: no false positive once the required clearance is small enough",
                  not any(f.rule == "clearance" for f in loose), f"got {loose}")
    finally:
        FreeCAD.closeDocument(doc.Name)

    # ── same-net endpoint exemption ──────────────────────────────────────────
    doc2 = new_document("TestDRCExemption")
    try:
        pad = doc2.addObject("Part::Feature", "Pad1")
        pad.Shape = Part.makeBox(2.0, 2.0, 0.1, V(-1.0, -1.0, 0))   # spans x[-1,1], y[-1,1]

        # A trace whose FIRST waypoint sits inside Pad1 — simulates "this
        # trace starts on this pad." The trace body itself touches the pad
        # (distance 0), which must NOT be reported.
        trace = _add_trace(
            doc2, "Trace_001", Part.makeBox(5.0, 0.3, 0.05, V(0, -0.15, 0)),
            waypoints=[V(0.0, 0.0, 0.0), V(5.0, 0.0, 0.0)],
        )
        doc2.recompute()

        findings = drc.run_drc(doc2, min_clearance_mm=0.5, min_trace_width_mm=0.0)
        tc.check("run_drc: the pad a trace legitimately starts on is NOT flagged "
                  "(same-net endpoint exemption — without this every trace would "
                  "flag itself against its own landing pad)",
                  not any("Pad1" in f.object_names for f in findings), f"got {findings}")
    finally:
        FreeCAD.closeDocument(doc2.Name)

    # ── two traces sharing a landing pad don't cross-flag each other ────────
    doc3 = new_document("TestDRCSharedPad")
    try:
        pad = doc3.addObject("Part::Feature", "Pad2")
        pad.Shape = Part.makeBox(3.0, 3.0, 0.1, V(-1.5, -1.5, 0))   # spans x/y [-1.5, 1.5]

        t1 = _add_trace(
            doc3, "Trace_001", Part.makeBox(5.0, 0.3, 0.05, V(-6.0, -0.15, 0)),
            waypoints=[V(-6.0, 0.0, 0.0), V(-1.0, 0.0, 0.0)],   # ends inside Pad2
        )
        t2 = _add_trace(
            doc3, "Trace_002", Part.makeBox(0.3, 5.0, 0.05, V(-0.15, 1.0, 0)),
            waypoints=[V(0.0, 6.0, 0.0), V(0.0, 1.0, 0.0)],     # starts inside Pad2
        )
        doc3.recompute()

        findings = drc.run_drc(doc3, min_clearance_mm=0.5, min_trace_width_mm=0.0)
        tc.check("run_drc: two traces converging on the SAME pad do not flag "
                  "each other (expected same-net convergence, not a violation)",
                  not any(set(f.object_names) == {"Trace_001", "Trace_002"} for f in findings),
                  f"got {findings}")
        tc.check("run_drc: ...and neither flags the shared pad itself",
                  not any("Pad2" in f.object_names for f in findings), f"got {findings}")
    finally:
        FreeCAD.closeDocument(doc3.Name)

    # ── substrate exemption: a trace resting on a much larger board ─────────
    # (a real PCB/leadframe/package body a trace is routed ON TOP OF touches
    # it at 0mm along the trace's whole length — that is expected, not a
    # violation, and is a DIFFERENT case from the same-net pad exemption
    # above since the trace never "ends" on the board.)
    doc6 = new_document("TestDRCSubstrate")
    try:
        board = doc6.addObject("Part::Feature", "Board")
        board.Shape = Part.makeBox(150.0, 100.0, 1.6, V(-10, -10, 0))  # a PCB-scale board

        trace = _add_trace(
            doc6, "Trace_001", Part.makeBox(5.0, 0.3, 0.035, V(0, 0, 1.6)),
            waypoints=[V(0.0, 0.15, 1.6), V(5.0, 0.15, 1.6)],
        )
        doc6.recompute()

        findings = drc.run_drc(doc6, min_clearance_mm=0.2, min_trace_width_mm=0.0)
        tc.check("run_drc: a trace resting on a much larger board is not "
                  "flagged against the board itself (substrate heuristic)",
                  not any("Board" in f.object_names for f in findings), f"got {findings}")
    finally:
        FreeCAD.closeDocument(doc6.Name)

    # ── min trace width — property-driven, no geometry involved ─────────────
    doc4 = new_document("TestDRCWidth")
    try:
        # Positioned far from everything else so ONLY the width rule can fire.
        narrow = _add_trace(doc4, "Trace_001",
                             Part.makeBox(5.0, 0.05, 0.02, V(100, 100, 0)),
                             width_mm=0.05)
        wide = _add_trace(doc4, "Trace_002",
                           Part.makeBox(5.0, 0.3, 0.02, V(200, 200, 0)),
                           width_mm=0.3)
        doc4.recompute()

        findings = drc.run_drc(doc4, min_clearance_mm=0.2, min_trace_width_mm=0.1)
        tc.check("run_drc: a trace narrower than the minimum is flagged",
                  any(f.rule == "min-width" and f.object_names == ["Trace_001"]
                      for f in findings), f"got {findings}")
        tc.check("run_drc: a trace at/above the minimum width is not flagged for width",
                  not any(f.rule == "min-width" and "Trace_002" in f.object_names
                          for f in findings), f"got {findings}")
        tc.check("run_drc: the two far-apart traces are not flagged for clearance",
                  not any(f.rule == "clearance" for f in findings), f"got {findings}")
    finally:
        FreeCAD.closeDocument(doc4.Name)

    # ── empty / no routed objects ────────────────────────────────────────────
    doc5 = new_document("TestDRCEmpty")
    try:
        plain = doc5.addObject("Part::Feature", "JustABox")
        plain.Shape = Part.makeBox(1, 1, 1)
        doc5.recompute()
        tc.check("run_drc: a document with no Trace_NNN/BondWire_NNN objects "
                  "reports no findings (nothing to check, not an error)",
                  drc.run_drc(doc5) == [])
        tc.check("run_drc: None document returns an empty list rather than raising",
                  drc.run_drc(None) == [])
    finally:
        FreeCAD.closeDocument(doc5.Name)

    return tc.results
