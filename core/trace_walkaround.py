# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
KiCad-style interactive routing geometry — the "head" and walk-around.

This is the geometry behind routing/InteractiveRouterCommand.py, and a
deliberately different model from core.trace_routing's sampled grid +
visibility graph + A* search:

  * core.trace_routing SEARCHES for a route between two fixed points. It
    needs a point grid, and its behaviour therefore depends on grid spacing,
    graph connectivity and search cost tuning.
  * this module SHAPES a route that follows the cursor. There is no grid and
    no search: the trace from the last fixed point to wherever the mouse is
    is two segments (a straight run plus a 45-degree diagonal — the "posture"),
    and when that collides with copper it is walked around the obstacle.

That is how KiCad's router behaves, and it removes several whole classes of
problem the search-based tool has: there are no grid points to click, no
spacing/connectivity coupling, and "no route" is a visible blocked head
rather than a silent failure.

Obstacles are the same axis-aligned keep-out rectangles
core.trace_routing.collect_obstacles already produces (one per copper solid,
expanded by clearance + half the trace width), and the same bounded
endpoint-escape rule applies so a trace may leave the pad it starts on
without being free to run along it.

Qt-free and document-free — see routing/ for the interactive layer.
"""

import math

import FreeCAD

from core.trace_routing import (
    Rect,
    segment_intersects_rect,
    segment_rect_overlap_length,
    point_in_rect,
    _cell_of,
    _candidate_obstacle_indices,
)

# Posture step conventions accepted by posture_points().
STEP_FREE = 0.0     # straight to the cursor, any angle
STEP_45   = 45.0    # KiCad default: diagonal + orthogonal run
STEP_90   = 90.0    # Manhattan: two orthogonal runs (an "L")

DEFAULT_MAX_DETOURS = 8


# ── the head ───────────────────────────────────────────────────────────────────

def posture_points(a, c, step_deg: float = STEP_45, flip: bool = False) -> list:
    """
    The two-segment "head" from the last fixed point *a* to the cursor *c*.

    step_deg = 45 (STEP_45, KiCad's default): one 45-degree diagonal covering
    min(|dx|, |dy|) on both axes, plus one axis-aligned run covering the
    remaining |dx| - |dy|. *flip* swaps which of the two comes first — the
    "posture" toggle, bound to `/` in KiCad.

    step_deg = 90 (STEP_90): the diagonal degenerates to nothing and this
    becomes the familiar L, with *flip* choosing horizontal-then-vertical or
    vertical-then-horizontal.

    step_deg = 0 (STEP_FREE): a single straight segment to the cursor.

    Returns [a, ...] ending at *c*; collinear/degenerate midpoints are
    dropped, so a purely horizontal move yields just [a, c] rather than a
    zero-length kink.
    """
    a = FreeCAD.Vector(a.x, a.y, a.z)
    c = FreeCAD.Vector(c.x, c.y, a.z)      # stay on the routing plane

    dx = c.x - a.x
    dy = c.y - a.y
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return [a]

    if step_deg <= 0.0:
        return [a, c]

    sx = 1.0 if dx >= 0 else -1.0
    sy = 1.0 if dy >= 0 else -1.0

    if step_deg >= 90.0:
        # Pure L. flip picks which axis is travelled first.
        mid = FreeCAD.Vector(a.x, c.y, a.z) if flip else FreeCAD.Vector(c.x, a.y, a.z)
    else:
        # 45-degree posture: the diagonal covers the shorter axis span.
        m = min(abs(dx), abs(dy))
        if flip:
            # straight run first, diagonal last
            mid = FreeCAD.Vector(c.x - sx * m, c.y - sy * m, a.z)
        else:
            # diagonal first, straight run last
            mid = FreeCAD.Vector(a.x + sx * m, a.y + sy * m, a.z)

    return _dedup_collinear([a, mid, c])


def _dedup_collinear(pts, tol: float = 1e-9) -> list:
    """Drop zero-length steps and midpoints that lie on a straight run."""
    out = []
    for p in pts:
        if not out or (p - out[-1]).Length > tol:
            out.append(p)
    if len(out) < 3:
        return out
    keep = [out[0]]
    for i in range(1, len(out) - 1):
        prev, cur, nxt = keep[-1], out[i], out[i + 1]
        v1 = cur - prev
        v2 = nxt - cur
        if v1.Length <= tol or v2.Length <= tol:
            continue
        cross = v1.x * v2.y - v1.y * v2.x
        if abs(cross) > tol * max(1.0, v1.Length * v2.Length):
            keep.append(cur)
    keep.append(out[-1])
    return keep


# ── collision ──────────────────────────────────────────────────────────────────

def path_blockers(path, obstacles, obstacle_index=None, cell_size=None,
                  exempt_start=None, exempt_end=None, escape_mm: float = 0.0):
    """
    Indices of every obstacle the polyline *path* actually violates.

    Obstacles containing the path's first/last point (*exempt_start* /
    *exempt_end*) are permitted up to *escape_mm* of travel inside them —
    the same bounded-escape rule core.trace_routing uses, so a trace can
    step off the pad it starts on without being allowed to run along a
    copper pour.
    """
    field = as_field(obstacles, obstacle_index, cell_size)
    if len(path) < 2:
        return []
    # Deliberately no "no obstacles -> nothing to check" shortcut: a field can
    # carry a routable-surface boundary with no copper on it at all, and
    # skipping the field then let a trace run straight off the edge of the
    # face it is supposed to stay on.
    ex_s = frozenset(exempt_start or ())
    ex_e = frozenset(exempt_end or ())
    hits = []
    seen = set()
    for si in range(len(path) - 1):
        p, q = path[si], path[si + 1]
        is_first = (si == 0)
        is_last = (si == len(path) - 2)

        # An endpoint's escape allowance is always measured FROM that
        # endpoint, so the goal end is tested on the reversed segment. A
        # segment that is both first and last (a direct two-point leg) gets
        # both tests, and an obstacle only really blocks when NEITHER
        # exemption excuses it — hence the intersection.
        from_start = field.blockers(p, q, ex_s if is_first else frozenset(), escape_mm)
        if is_last:
            from_end = field.blockers(q, p, ex_e, escape_mm)
            blocking = [i for i in from_start if i in set(from_end)]
        else:
            blocking = from_start

        for i in blocking:
            if i not in seen:
                seen.add(i)
                hits.append(i)
    return hits


def first_blocking_rect(path, obstacles, obstacle_index=None, cell_size=None,
                        exempt_start=None, exempt_end=None, escape_mm: float = 0.0):
    """Index of the first obstacle *path* violates, or None if it is clear."""
    hits = path_blockers(path, obstacles, obstacle_index, cell_size,
                         exempt_start, exempt_end, escape_mm)
    return hits[0] if hits else None


def obstacles_containing(pt, obstacles, obstacle_index=None, cell_size=None):
    """Indices of obstacles the point sits inside — for endpoint exemption.
    Accepts an obstacle field or a bare Rect list."""
    if hasattr(obstacles, "containing"):
        return obstacles.containing(pt)
    if not obstacles:
        return frozenset()
    if obstacle_index is not None and cell_size:
        cx, cy = _cell_of(pt.x, pt.y, cell_size)
        cands = obstacle_index.get((cx, cy), ())
    else:
        cands = range(len(obstacles))
    return frozenset(i for i in cands if point_in_rect(pt, obstacles[i]))


# ── walk-around ────────────────────────────────────────────────────────────────

def _corner_waypoints(rect: Rect, margin: float = 0.0, z: float = 0.0):
    """The four corners of *rect*, nudged outward so a route through one is
    just clear of the keep-out rather than grazing it (a segment touching an
    edge counts as a collision, by design)."""
    m = max(margin, 1e-6)
    return [
        FreeCAD.Vector(rect.xmin - m, rect.ymin - m, z),
        FreeCAD.Vector(rect.xmax + m, rect.ymin - m, z),
        FreeCAD.Vector(rect.xmax + m, rect.ymax + m, z),
        FreeCAD.Vector(rect.xmin - m, rect.ymax + m, z),
    ]


def _side_routes(start, goal, corners, max_per_side: int = 2):
    """
    The two ways around an obstacle: the walk points lying left of the
    start->goal line, and those lying right, each ordered by progress along
    that line.

    This is the hull walk KiCad's walk-around performs — you pass an
    obstacle on one side or the other — rather than treating every corner as
    an independent waypoint, which explodes combinatorially as soon as
    several obstacles are involved. Each side is trimmed to the *extreme*
    points (the ones furthest off the line), since the intermediate hull
    vertices lie inside the detour those already describe.
    """
    dx = goal.x - start.x
    dy = goal.y - start.y
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return []
    left, right = [], []
    for pt in corners:
        cross = dx * (pt.y - start.y) - dy * (pt.x - start.x)
        proj = dx * (pt.x - start.x) + dy * (pt.y - start.y)
        (left if cross > 0 else right).append((proj, abs(cross), pt))
    out = []
    for group in (left, right):
        if not group:
            continue
        group.sort(key=lambda t: t[0])           # order along the travel direction
        if len(group) > max_per_side:
            # keep the two that actually define the detour envelope
            extreme = sorted(group, key=lambda t: -t[1])[:max_per_side]
            group = sorted(extreme, key=lambda t: t[0])
        out.append([pt for _p, _c, pt in group])
    return out


class _RectField:
    """Adapter presenting a plain list of Rect keep-outs through the same
    protocol PolyField offers, so walk_around works with either."""

    def __init__(self, rects, index=None, cell_size=None):
        self.rects = list(rects)
        self.index = index
        self.cell_size = cell_size

    def __len__(self):
        return len(self.rects)

    def containing(self, pt):
        return obstacles_containing(pt, self.rects, self.index, self.cell_size)

    def blockers(self, p, q, exempt=frozenset(), escape_mm: float = 0.0):
        if self.index is not None and self.cell_size:
            cands = _candidate_obstacle_indices(p, q, self.index, self.cell_size)
        else:
            cands = range(len(self.rects))
        hits = []
        for i in cands:
            rect = self.rects[i]
            if not segment_intersects_rect(p, q, rect):
                continue
            if i in exempt and segment_rect_overlap_length(p, q, rect) <= escape_mm:
                continue
            hits.append(i)
        return hits

    def walk_points(self, i, margin, z):
        return _corner_waypoints(self.rects[i], margin, z)


def as_field(obstacles, obstacle_index=None, cell_size=None):
    """Accept either an obstacle field (PolyField) or a bare list of Rect."""
    if hasattr(obstacles, "blockers"):
        return obstacles
    return _RectField(obstacles or [], obstacle_index, cell_size)


def _path_length(pts) -> float:
    return sum((b - a).Length for a, b in zip(pts, pts[1:]))


def _join(head, tail):
    """Concatenate two posture paths sharing an endpoint."""
    if not head:
        return list(tail)
    if not tail:
        return list(head)
    return _dedup_collinear(list(head) + list(tail)[1:])


def walk_around(a, c, obstacles, obstacle_index=None, cell_size=None,
                step_deg: float = STEP_45, flip: bool = False,
                escape_mm: float = 0.0, max_detours: int = DEFAULT_MAX_DETOURS):
    """
    Route from *a* to the cursor *c* with a KiCad-style posture head, walking
    around any obstacle in the way.

    Returns a list of waypoints ending at *c*, or None when the target could
    not be reached within *max_detours* — the caller should draw that as a
    visibly blocked head rather than silently producing nothing.

    The walk is deliberately simple and bounded: take the first obstacle the
    head violates, try routing via each of its four (outward-nudged) corners
    using the same posture on both legs, keep the shortest candidate that is
    fully clear, and otherwise recurse from the best partial progress. With
    rectangular keep-outs this reproduces the behaviour that matters — the
    trace bends around copper instead of through it — without the grid,
    connectivity and tuning coupling of a graph search.
    """
    a = FreeCAD.Vector(a.x, a.y, a.z)
    c = FreeCAD.Vector(c.x, c.y, a.z)
    field = as_field(obstacles, obstacle_index, cell_size)

    ex_s = field.containing(a)
    ex_e = field.containing(c)

    def clear(path):
        return not path_blockers(path, field, None, None, ex_s, ex_e, escape_mm)

    direct = posture_points(a, c, step_deg, flip)
    if len(direct) < 2 or clear(direct):
        return direct

    margin = max((cell_size or 0.0) * 0.25, 1e-3)
    budget = [max_detours * 8]       # hard cap on hull expansions, so this
                                     # stays cheap enough to run per mouse-move

    def _solve(start, goal, depth, visited):
        if budget[0] <= 0:
            return None
        head = posture_points(start, goal, step_deg, flip)
        if len(head) < 2:
            return head
        ex_here = field.containing(start) | ex_s
        blockers = path_blockers(head, field, None, None, ex_here, ex_e, escape_mm)
        if not blockers:
            return head
        if depth <= 0:
            return None

        best = None
        best_len = float("inf")
        # Walk around the first couple of things in the way, not only the
        # very first: on a dense board the nearest blocker is often the one
        # with no room beside it while the next one over is routable.
        for bi in blockers[:2]:
            if bi in visited:
                continue
            if bi < 0:
                # The route left the routable face. That is not an obstacle
                # with a hull to walk around — the only remedy is a different
                # cursor position, so report blocked.
                continue
            for side in _side_routes(start, goal, field.walk_points(bi, margin, start.z)):
                budget[0] -= 1
                if budget[0] <= 0:
                    return best
                # Route through this side's waypoints, then on toward the goal.
                legs = [start] + side
                partial = []
                ok = True
                for u, v in zip(legs, legs[1:]):
                    seg = posture_points(u, v, step_deg, flip)
                    if path_blockers(seg, field, None, None,
                                     field.containing(u) | ex_s, None, escape_mm):
                        ok = False
                        break
                    partial = _join(partial, seg)
                if not ok or not partial:
                    continue
                rest = _solve(partial[-1], goal, depth - 1, visited | {bi})
                if rest is None:
                    continue
                cand = _join(partial, rest)
                if not clear(cand):
                    continue
                length = _path_length(cand)
                if length < best_len:
                    best_len = length
                    best = cand
        return best

    return _solve(a, c, max_detours, frozenset())


def route_head(a, c, obstacles, obstacle_index=None, cell_size=None,
               step_deg: float = STEP_45, flip: bool = False,
               escape_mm: float = 0.0, max_detours: int = DEFAULT_MAX_DETOURS):
    """
    The routed head for the interactive router: walk_around, but falling back
    to the opposite posture when the preferred one cannot get through.

    KiCad exposes the same two shapes via its posture toggle; trying both
    automatically means the trace usually still follows the cursor instead of
    going blocked just because the *other* L would have fitted. Returns
    (points, used_flip) or (None, flip) when neither posture is routable —
    the caller draws that as a visibly blocked head.
    """
    for use_flip in (flip, not flip):
        path = walk_around(a, c, obstacles, obstacle_index, cell_size,
                           step_deg, use_flip, escape_mm, max_detours)
        if path is not None and len(path) >= 2:
            return path, use_flip
    return None, flip
