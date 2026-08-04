# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.trace_walkaround — the KiCad-style routing geometry
(two-segment posture "head" plus walk-around) behind the interactive router.

This is a different model from core.trace_routing's grid + visibility graph
+ A* search: there is no grid and no search, so the properties worth pinning
down are geometric — the head has the right shape for each posture, and a
route that walks around an obstacle really is collision-free afterwards.
"""

import FreeCAD

from _harness import TestCase

import core.trace_walkaround as wa

V = FreeCAD.Vector


def _xy(path):
    return [(round(p.x, 6), round(p.y, 6)) for p in path]


def run():
    tc = TestCase("trace_walkaround")

    A = V(0, 0, 0)
    C = V(10, 4, 0)

    # ── posture: the two-segment head ────────────────────────────────────────
    p45 = wa.posture_points(A, C, wa.STEP_45, flip=False)
    tc.check("posture 45: three points (diagonal then straight)",
              len(p45) == 3, f"got {_xy(p45)}")
    tc.check("posture 45: diagonal comes first and covers the shorter axis span",
              _xy(p45)[1] == (4.0, 4.0), f"got {_xy(p45)}")
    tc.check("posture 45: ends exactly at the cursor", _xy(p45)[-1] == (10.0, 4.0))

    p45f = wa.posture_points(A, C, wa.STEP_45, flip=True)
    tc.check("posture 45 flipped: straight run comes first",
              _xy(p45f)[1] == (6.0, 0.0), f"got {_xy(p45f)}")
    tc.check("posture flip changes the route but not the endpoints",
              _xy(p45f)[0] == _xy(p45)[0] and _xy(p45f)[-1] == _xy(p45)[-1]
              and _xy(p45f)[1] != _xy(p45)[1])

    # Every 45-posture segment must be axis-aligned or exactly diagonal.
    for a, b in zip(p45, p45[1:]):
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        on_grid = dx < 1e-9 or dy < 1e-9 or abs(dx - dy) < 1e-9
        tc.check("posture 45: each segment is axis-aligned or exactly 45 degrees",
                  on_grid, f"segment {(a.x, a.y)}->{(b.x, b.y)}")

    p90 = wa.posture_points(A, C, wa.STEP_90, flip=False)
    p90f = wa.posture_points(A, C, wa.STEP_90, flip=True)
    tc.check("posture 90: an L via the horizontal leg first",
              _xy(p90) == [(0.0, 0.0), (10.0, 0.0), (10.0, 4.0)], f"got {_xy(p90)}")
    tc.check("posture 90 flipped: the other L (vertical leg first)",
              _xy(p90f) == [(0.0, 0.0), (0.0, 4.0), (10.0, 4.0)], f"got {_xy(p90f)}")

    tc.check("posture free: a single direct segment",
              _xy(wa.posture_points(A, C, wa.STEP_FREE)) == [(0.0, 0.0), (10.0, 4.0)])

    # ── posture degenerate cases ─────────────────────────────────────────────
    tc.check("posture: a purely horizontal move needs no kink",
              _xy(wa.posture_points(V(0, 0, 0), V(10, 0, 0), wa.STEP_45)) ==
              [(0.0, 0.0), (10.0, 0.0)])
    tc.check("posture: an exact 45-degree move needs no kink",
              _xy(wa.posture_points(V(0, 0, 0), V(5, 5, 0), wa.STEP_45)) ==
              [(0.0, 0.0), (5.0, 5.0)])
    tc.check("posture: zero-length move collapses to a single point",
              len(wa.posture_points(V(1, 1, 0), V(1, 1, 0), wa.STEP_45)) == 1)
    tc.check("posture: the head stays on the routing plane (start's Z)",
              all(abs(p.z - 3.0) < 1e-9
                  for p in wa.posture_points(V(0, 0, 3), V(5, 2, 99), wa.STEP_45)))

    # ── collision detection ──────────────────────────────────────────────────
    blocker = wa.Rect(3.0, -2.0, 6.0, 2.0)
    straight = [V(0, 0, 0), V(10, 0, 0)]
    tc.check("path_blockers: reports the obstacle a segment runs through",
              wa.path_blockers(straight, [blocker]) == [0])
    tc.check("path_blockers: a clear path reports nothing",
              wa.path_blockers([V(0, 5, 0), V(10, 5, 0)], [blocker]) == [])
    tc.check("first_blocking_rect: returns the index, or None when clear",
              wa.first_blocking_rect(straight, [blocker]) == 0
              and wa.first_blocking_rect([V(0, 5, 0), V(10, 5, 0)], [blocker]) is None)

    # Endpoint escape: a trace may leave the pad it starts on, but only for a
    # bounded distance inside it (same rule as core.trace_routing).
    pad = wa.Rect(-1.0, -1.0, 1.0, 1.0)
    esc_path = [V(0, 0, 0), V(6, 0, 0)]
    tc.check("escape: the pad containing the start point does not block a short exit",
              wa.path_blockers(esc_path, [pad],
                               exempt_start=wa.obstacles_containing(V(0, 0, 0), [pad]),
                               escape_mm=2.0) == [])
    long_pour = wa.Rect(-1.0, -1.0, 20.0, 1.0)
    tc.check("escape: a long pour containing the start still blocks a run down it",
              wa.path_blockers([V(0, 0, 0), V(30, 0, 0)], [long_pour],
                               exempt_start=wa.obstacles_containing(V(0, 0, 0), [long_pour]),
                               escape_mm=2.0) == [0])

    tc.check("obstacles_containing: finds the rect a point is inside",
              wa.obstacles_containing(V(0, 0, 0), [pad, blocker]) == frozenset({0}))

    # ── walk-around ──────────────────────────────────────────────────────────
    obs = [blocker]
    routed = wa.walk_around(V(0, 0, 0), V(10, 0, 0), obs, step_deg=wa.STEP_45)
    tc.check("walk_around: finds a route past a blocking obstacle",
              routed is not None and len(routed) > 2, f"got {routed and _xy(routed)}")
    if routed:
        tc.check("walk_around: the returned route is genuinely collision-free",
                  wa.path_blockers(routed, obs) == [], f"got {_xy(routed)}")
        tc.check("walk_around: the route still starts and ends where asked",
                  _xy(routed)[0] == (0.0, 0.0) and _xy(routed)[-1] == (10.0, 0.0),
                  f"got {_xy(routed)}")
        for a, b in zip(routed, routed[1:]):
            dx, dy = abs(b.x - a.x), abs(b.y - a.y)
            tc.check("walk_around: detour segments keep the 45-degree posture",
                      dx < 1e-9 or dy < 1e-9 or abs(dx - dy) < 1e-9,
                      f"segment {(a.x, a.y)}->{(b.x, b.y)}")

    tc.check("walk_around: an unobstructed route is just the plain head",
              _xy(wa.walk_around(A, C, [], step_deg=wa.STEP_45)) == _xy(p45))

    # Fully enclosed target -> no route, reported as None rather than a
    # silently-wrong path that runs through the keep-out.
    cage = [wa.Rect(-5.0, -5.0, 5.0, 5.0)]
    tc.check("walk_around: returns None when the target is sealed inside a keep-out",
              wa.walk_around(V(-20, 0, 0), V(0, 0, 0), cage,
                             step_deg=wa.STEP_45, escape_mm=0.0) is None)

    # Two obstacles in series still get walked around.
    two = [wa.Rect(3.0, -2.0, 5.0, 2.0), wa.Rect(8.0, -2.0, 10.0, 2.0)]
    routed2 = wa.walk_around(V(0, 0, 0), V(14, 0, 0), two, step_deg=wa.STEP_45)
    tc.check("walk_around: handles two obstacles in series",
              routed2 is not None and wa.path_blockers(routed2, two) == [],
              f"got {routed2 and _xy(routed2)}")

    # ── walk-around against exact copper outlines (PolyField) ───────────────
    # The same tall-thin-obstacle case that exposed the offset bug: a 2 x 8 mm
    # copper run squarely between start and goal must be routed around, not
    # reported blocked.
    import core.trace_obstacles as tob
    tall = tob.Poly([(9, 6), (11, 6), (11, 14), (9, 14)], (9, 6, 11, 14))
    pf = tob.PolyField([tall], clearance=0.35, cell_size=1.0)
    ps, pe = V(2, 10, 1), V(18, 10, 1)

    tc.check("PolyField fixture: the direct line really is blocked",
              pf.blockers(ps, pe) == [0])
    around = wa.walk_around(ps, pe, pf, step_deg=wa.STEP_45)
    tc.check("walk_around: routes around a tall copper run using exact outlines "
              "(regression guard — a mis-offset hull made this report blocked)",
              around is not None and len(around) > 2,
              f"got {around and _xy(around)}")
    if around:
        tc.check("walk_around: that route is collision-free",
                  wa.path_blockers(around, pf) == [], f"got {_xy(around)}")
        tc.check("walk_around: it actually goes around, not through "
                  "(some waypoint clears the obstacle's span)",
                  any(p.y > 14.0 or p.y < 6.0 for p in around), f"got {_xy(around)}")

    head, used_flip = wa.route_head(ps, pe, pf, step_deg=wa.STEP_45)
    tc.check("route_head: returns a routed path and the posture it used",
              head is not None and isinstance(used_flip, bool), f"got {head}")

    # A goal that lies INSIDE copper is reachable on purpose: you are
    # connecting to that copper, so it is the same net (see
    # PolyField.blockers). Being unreachable requires the goal to sit in a
    # hollow fully enclosed by copper it is not part of.
    inside_goal = tob.PolyField(
        [tob.Poly([(-5, -5), (5, -5), (5, 5), (-5, 5)], (-5, -5, 5, 5))],
        clearance=0.35, cell_size=1.0)
    same_net, _ = wa.route_head(V(-20, 0, 0), V(0, 0, 0), inside_goal, step_deg=wa.STEP_45)
    tc.check("route_head: a goal inside copper IS reachable — landing on the pad "
              "you are connecting to is same-net, not a violation",
              same_net is not None, f"got {same_net}")

    def _bar(x0, y0, x1, y1):
        return tob.Poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], (x0, y0, x1, y1))

    ring = tob.PolyField([_bar(-6, -6, -4, 6), _bar(4, -6, 6, 6),
                          _bar(-6, -6, 6, -4), _bar(-6, 4, 6, 6)],
                         clearance=0.35, cell_size=1.0)
    walled, _ = wa.route_head(V(-20, 0, 0), V(0, 0, 0), ring, step_deg=wa.STEP_45)
    tc.check("route_head: reports None when the goal sits in a hollow walled in by "
              "copper, so the caller can draw a visibly blocked head",
              walled is None, f"got {walled and _xy(walled)}")

    return tc.results
