# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Smoke test for core.via_clustering.cluster_boxes() — the fix for a real bug
seen earlier in this project: simplifying a via layer to "one box per
layer" bridged the gap between separate via arrays/pads, visually (and, for
frame cutouts, geometrically) fusing unrelated features together.
cluster_boxes() groups solids by proximity instead, so a tightly packed via
array collapses into one block while physically separate arrays stay
separate.  There was no regression protection for this fix until now.
"""

import Part
from FreeCAD import Base

from _harness import TestCase

from core.via_clustering import cluster_boxes


def _make_box_at(x, y, size=0.05, z=0.0, height=0.1):
    return Part.makeBox(size, size, height, Base.Vector(x, y, z))


def run():
    tc = TestCase("via_clustering")

    # ── Case 1: two tightly packed via arrays, far apart from each other ──
    # Mimics the real bug scenario: a 3x3 grid of tiny vias (0.05mm boxes,
    # 0.055mm pitch -> 0.005mm gaps, i.e. below the 0.01mm clustering
    # threshold) at two locations 2mm apart.  Vias within each array should
    # merge into one block per array, and the two arrays must stay separate.
    solids = []
    for cx, cy in ((0.0, 0.0), (2.0, 2.0)):
        for i in range(3):
            for j in range(3):
                solids.append(_make_box_at(cx + i * 0.055, cy + j * 0.055))
    compound = Part.makeCompound(solids)

    result = cluster_boxes(compound, gap=0.01)
    n_result_solids = len(result.Solids) if result.Solids else 1
    tc.check(
        "two well-separated 3x3 via arrays collapse to exactly 2 blocks",
        n_result_solids == 2,
        f"got {n_result_solids} block(s)",
    )
    tc.check("clustered result is valid", result.isValid())
    tc.check("clustered result is not null", not result.isNull())

    if n_result_solids == 2:
        boxes = sorted(result.Solids, key=lambda s: s.BoundBox.XMin)
        # Each combined block should roughly span its own 3x3 array
        # (0 to ~0.25mm per array footprint), not bridge to the other one.
        b0, b1 = boxes
        tc.check(
            "block 0 does not bridge into the second array's region",
            b0.BoundBox.XMax < 1.0, f"XMax={b0.BoundBox.XMax:.4f}",
        )
        tc.check(
            "block 1 does not bridge into the first array's region",
            b1.BoundBox.XMin > 1.0, f"XMin={b1.BoundBox.XMin:.4f}",
        )

    # ── Case 2: gap larger than the array separation -> everything merges ──
    # Sanity check that the gap parameter actually controls the behaviour:
    # with a huge gap, even the two 2mm-apart arrays should merge into one
    # block, confirming the earlier "one giant box" failure mode is what
    # you get with a too-large gap, not a hardcoded always-separate result.
    result_big_gap = cluster_boxes(compound, gap=5.0)
    n_big = len(result_big_gap.Solids) if result_big_gap.Solids else 1
    tc.check(
        "a large gap merges both arrays into one block",
        n_big == 1, f"got {n_big} block(s)",
    )

    # ── Case 3: single solid -> single box, no clustering machinery needed ──
    single = _make_box_at(0.0, 0.0)
    result_single = cluster_boxes(single, gap=0.01)
    tc.check("single solid input returns a valid single box",
             result_single.isValid() and not result_single.isNull())
    tc.check("single solid input volume is positive",
             result_single.Volume > 1e-9)

    return tc.results
