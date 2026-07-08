# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Proximity clustering for via/pad simplification.

A via layer's shape is normally a compound of many disjoint solids (each
individual via cut).  Two extremes are both wrong for a "simplified" view:

  * One box for the WHOLE layer bridges every gap between separate via
    arrays/pads, visually (and, for frame cutouts, geometrically) fusing
    unrelated features together.
  * One box per INDIVIDUAL solid keeps every single via visible as its own
    tiny square — no simplification at all for a tightly packed array.

``cluster_boxes`` groups solids that are within *gap* of each other (single-
linkage clustering) and returns one combined bounding box per cluster — so a
tightly packed via array (spacing << gap) collapses into one filled block,
while physically separate arrays/pads (spacing >> gap) stay separate blocks.
"""

import math
from collections import defaultdict

import FreeCAD
import Part

# Vias within a single contact array are typically sub-micron apart; separate
# arrays/pads are typically tens of microns or more apart.  10 µm sits well
# between the two for common GDS process scales.
DEFAULT_CLUSTER_GAP_MM = 0.01


def _boxes_touch(a, b, gap: float) -> bool:
    return (a.XMin - gap <= b.XMax and a.XMax + gap >= b.XMin and
            a.YMin - gap <= b.YMax and a.YMax + gap >= b.YMin and
            a.ZMin - gap <= b.ZMax and a.ZMax + gap >= b.ZMin)


def _single_box(bb) -> Part.Shape:
    return Part.makeBox(
        max(bb.XLength, 1e-6), max(bb.YLength, 1e-6), max(bb.ZLength, 1e-6),
        FreeCAD.Vector(bb.XMin, bb.YMin, bb.ZMin),
    )


def cluster_boxes(shape: Part.Shape, gap: float = DEFAULT_CLUSTER_GAP_MM) -> Part.Shape:
    """
    Return one bounding box per proximity cluster of *shape*'s solids, as a
    compound.  Solids whose bounding boxes are within *gap* of each other
    (directly or transitively) are merged into a single combined box.

    Uses a spatial grid (cell size = gap) to bucket solids before the
    proximity test, so clustering stays fast even for via layers with
    thousands of individual cuts (near-linear instead of full O(n²)).
    """
    solids = list(shape.Solids or [])
    if not solids:
        return _single_box(shape.BoundBox)

    boxes = [s.BoundBox for s in solids]
    n = len(boxes)
    cell = max(gap, 1e-6)

    def cell_of(x, y):
        return (math.floor(x / cell), math.floor(y / cell))

    grid = defaultdict(list)
    for idx, b in enumerate(boxes):
        cx0, cy0 = cell_of(b.XMin - gap, b.YMin - gap)
        cx1, cy1 = cell_of(b.XMax + gap, b.YMax + gap)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                grid[(cx, cy)].append(idx)

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    seen_pairs = set()
    for idxs in grid.values():
        m = len(idxs)
        for a in range(m):
            for b_ in range(a + 1, m):
                i, j = idxs[a], idxs[b_]
                if i == j:
                    continue
                key = (i, j) if i < j else (j, i)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                if _boxes_touch(boxes[i], boxes[j], gap):
                    union(i, j)

    clusters = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    out_boxes = []
    for members in clusters.values():
        xmin = ymin = zmin = float("inf")
        xmax = ymax = zmax = float("-inf")
        for idx in members:
            b = boxes[idx]
            xmin = min(xmin, b.XMin); xmax = max(xmax, b.XMax)
            ymin = min(ymin, b.YMin); ymax = max(ymax, b.YMax)
            zmin = min(zmin, b.ZMin); zmax = max(zmax, b.ZMax)
        if (xmax - xmin) <= 1e-9 or (ymax - ymin) <= 1e-9 or (zmax - zmin) <= 1e-9:
            continue
        out_boxes.append(Part.makeBox(
            xmax - xmin, ymax - ymin, zmax - zmin,
            FreeCAD.Vector(xmin, ymin, zmin),
        ))

    if not out_boxes:
        return _single_box(shape.BoundBox)
    return out_boxes[0] if len(out_boxes) == 1 else Part.makeCompound(out_boxes)
