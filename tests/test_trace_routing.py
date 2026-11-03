# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless smoke tests for core.trace_routing — the algorithm layer behind
the semi-automated point-to-point trace routing tool (grid sampling,
spatial hashing, obstacle collection, angle-constrained visibility-graph
A*, path simplification, and solid trace construction).

The angle-constrained pathfinder is genuinely new capability for this repo
(no prior pathfinding code anywhere) so it gets the most scrutiny here,
in particular a test that pins down the bend-angle CONVENTION (deviation
from straight, not interior corner angle) — see
"find_path: fails when max_bend_deg is tighter...".
"""

import math
import random
import time

import FreeCAD
import Part

from _harness import TestCase, new_document

import core.trace_routing as trace_routing

V = FreeCAD.Vector


def run():
    tc = TestCase("trace_routing")

    # ── sample_face_grid ─────────────────────────────────────────────────────
    box = Part.makeBox(10.0, 10.0, 1.0)
    top_face = max(box.Faces, key=lambda f: f.BoundBox.ZMin)
    tc.check("sample_face_grid fixture: box top face is planar (dispatches to fast path)",
              isinstance(top_face.Surface, Part.Plane))
    grid_pts = trace_routing.sample_face_grid(top_face, 2.0)
    tc.check("sample_face_grid: produced multiple points", len(grid_pts) > 4,
              f"got {len(grid_pts)}")
    tc.check("sample_face_grid: all points within the face's XY bounds",
              all(-1e-6 <= p.x <= 10.0 + 1e-6 and -1e-6 <= p.y <= 10.0 + 1e-6 for p in grid_pts),
              f"bounds violated among {[(p.x, p.y) for p in grid_pts]}")
    tc.check("sample_face_grid: all points lie on the top face (z=1)",
              all(abs(p.z - 1.0) < 1e-6 for p in grid_pts))

    # ── sample_face_grid: curved-surface fallback path ───────────────────────
    cyl = Part.makeCylinder(5.0, 10.0)
    curved_face = next((f for f in cyl.Faces if not isinstance(f.Surface, Part.Plane)), None)
    tc.check("sample_face_grid fixture: found a genuinely curved cylinder face",
              curved_face is not None)
    if curved_face is not None:
        curved_pts = trace_routing.sample_face_grid(curved_face, 2.0)
        tc.check("sample_face_grid: curved-surface fallback produces points",
                  len(curved_pts) > 4, f"got {len(curved_pts)}")

    # ── sample_face_grid: performance regression guard ───────────────────────
    # Reproduces the exact failure reported against a real PCB: OCCT's
    # per-point domain classifier (the ORIGINAL implementation's
    # isPartOfDomain/isInside check) rebuilds its trim classifier from
    # scratch on every call, so cost scales with trim complexity PER SAMPLE
    # POINT. On a face with ~150 trim holes (a stand-in for a real copper
    # pour's via-clearance holes), that made grid generation take minutes —
    # confirmed via direct profiling during development (3600 isPartOfDomain
    # calls alone did not finish in 2 minutes). The fix (planar fast path:
    # tessellate the boundary once, sample directly in the plane's own local
    # 2D basis, no OCCT surface evaluation per point) should complete this
    # same scenario in well under a second; this test fails loudly if that
    # ever regresses.
    outer = Part.makePolygon([V(0, 0, 0), V(20, 0, 0), V(20, 20, 0), V(0, 20, 0), V(0, 0, 0)])
    outer_face = Part.Face(outer)
    random.seed(1)
    holes = [Part.Face(Part.Wire(Part.makeCircle(
                0.3, V(random.uniform(1, 19), random.uniform(1, 19), 0))))
             for _ in range(150)]
    perforated = outer_face.cut(Part.Compound(holes))
    tc.check("perf fixture: perforated face has exactly one face",
              len(perforated.Faces) == 1, f"got {len(perforated.Faces)}")
    if perforated.Faces:
        perf_face = perforated.Faces[0]
        tc.check("perf fixture: perforated face is still planar",
                  isinstance(perf_face.Surface, Part.Plane))

        t0 = time.time()
        perf_pts = trace_routing.sample_face_grid(perf_face, 0.5)
        elapsed = time.time() - t0

        tc.check("sample_face_grid: perforated-face grid completes in well under a second "
                  "(regression guard — this took MINUTES before the planar fast path)",
                  elapsed < 2.0, f"took {elapsed:.3f}s")
        tc.check("sample_face_grid: perforated-face grid produced points",
                  len(perf_pts) > 0, f"got {len(perf_pts)}")
        # A generous upper bound (50x50 cap) proves holes actually excluded
        # some candidates rather than the containment check silently always
        # returning True.
        tc.check("sample_face_grid: perforated-face grid excludes at least some "
                  "hole-covered candidates (containment check is doing real work)",
                  len(perf_pts) < 50 * 50, f"got {len(perf_pts)}")

    # ── point-in-loops-with-holes correctness (isolated from any Face) ──────
    outer_loop = ([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)], (0.0, 0.0, 10.0, 10.0))
    hole_loop  = ([(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)], (4.0, 4.0, 6.0, 6.0))
    pil_loops = [outer_loop, hole_loop]
    pil_index = trace_routing._build_loop_index(pil_loops, 1.0)
    pil_cases = [
        ("inside outer, outside hole", 2.0, 2.0, True),
        ("inside the hole -> excluded", 5.0, 5.0, False),
        ("outside the outer boundary entirely", 15.0, 15.0, False),
    ]
    for name, x, y, expected in pil_cases:
        got = trace_routing._point_in_loops(x, y, pil_loops, pil_index, 1.0)
        tc.check(f"_point_in_loops: {name}", got == expected, f"got {got}")

    # ── spatial hash: query_neighbors matches brute force ───────────────────
    random.seed(42)
    cloud = [V(random.uniform(0, 20), random.uniform(0, 20), 0.0) for _ in range(60)]
    index = trace_routing.build_point_index(cloud, 2.0)
    center, radius = 10, 5.0
    brute = sorted(j for j in range(len(cloud))
                    if j != center and (cloud[j] - cloud[center]).Length <= radius)
    fast = sorted(trace_routing.query_neighbors(cloud, index, 2.0, center, radius))
    tc.check("query_neighbors matches brute-force neighbor search",
              brute == fast, f"brute={brute} fast={fast}")

    # ── segment_intersects_rect ──────────────────────────────────────────────
    rect = trace_routing.Rect(0.0, 0.0, 10.0, 10.0)
    seg_cases = [
        ("fully outside",       V(-5, -5, 0), V(-5, 5, 0), False),
        ("fully inside",        V(2, 2, 0),   V(8, 8, 0),  True),
        ("crossing through",    V(-5, 5, 0),  V(15, 5, 0), True),
        ("touching an edge",    V(-5, 0, 0),  V(5, 0, 0),  True),
        ("corner-to-corner diagonal", V(-1, 11, 0), V(11, -1, 0), True),
        ("zero-length inside",  V(5, 5, 0),   V(5, 5, 0),  True),
        ("zero-length outside", V(-5, -5, 0), V(-5, -5, 0), False),
        ("parallel outside",    V(-5, -1, 0), V(15, -1, 0), False),
    ]
    for name, p0, p1, expected in seg_cases:
        got = trace_routing.segment_intersects_rect(p0, p1, rect)
        tc.check(f"segment_intersects_rect: {name}", got == expected, f"got {got}")

    # ── collect_obstacles ─────────────────────────────────────────────────────
    doc = new_document("TestTraceRoutingObstacles")
    try:
        board = doc.addObject("Part::Feature", "Board")
        board.Shape = Part.makeBox(20.0, 20.0, 1.0)   # z in [0,1] — the routing surface

        near_part = doc.addObject("Part::Feature", "SomePart")
        near_part.Shape = Part.makeBox(2.0, 2.0, 0.5, V(5, 5, 1))   # z in [1,1.5]

        cp = doc.addObject("Part::Feature", "cp_marker")
        cp.Shape = Part.makeBox(0.1, 0.1, 0.1, V(1, 1, 1))
        cp.addProperty("App::PropertyBool", "IsContactPoint", "Wirebond", "")
        cp.IsContactPoint = True

        grp = doc.addObject("App::DocumentObjectGroup", "SomeGroup")
        grp_child = doc.addObject("Part::Feature", "GroupChild")
        grp_child.Shape = Part.makeBox(1.0, 1.0, 1.0, V(15, 15, 1))   # z in [1,2]
        grp.addObject(grp_child)

        far_below = doc.addObject("Part::Feature", "FarBelow")
        far_below.Shape = Part.makeBox(1.0, 1.0, 1.0, V(0, 0, -50))

        rects = trace_routing.collect_obstacles(
            doc, exclude_names={"Board"}, z_min=0.9, z_max=1.6, expand_mm=0.0
        )
        tc.check("collect_obstacles: excludes routing surface, ContactPoint marker, "
                  "the group object itself, and out-of-Z-band geometry — keeps only "
                  "SomePart and GroupChild",
                  len(rects) == 2, f"got {len(rects)} rects: {rects}")

        rects_expanded = trace_routing.collect_obstacles(
            doc, exclude_names={"Board"}, z_min=0.9, z_max=1.6, expand_mm=1.0
        )
        match = next((r for r in rects_expanded
                      if abs(r.xmin - 4.0) < 1e-6 and abs(r.xmax - 8.0) < 1e-6), None)
        tc.check("collect_obstacles: expand_mm grows the keep-out rect",
                  match is not None, f"got {rects_expanded}")
    finally:
        FreeCAD.closeDocument(doc.Name)

    # ── collect_obstacles: per-solid decomposition (board-spanning layer) ────
    # Regression guard for "No path found for that leg": a real PCB copper /
    # pad / via layer imports as ONE object holding many disjoint solids. Its
    # OVERALL bounding box spans the whole board, so as a single obstacle it
    # blocks the entire routing surface. It must be decomposed into one
    # keep-out per solid, leaving the gaps between traces open.
    doc_ps = new_document("TestTraceRoutingPerSolid")
    try:
        # Two separate copper "traces" at the same Z, far apart in X, fused
        # into ONE object (compound of 2 solids) — exactly the layer shape.
        t_a = Part.makeBox(1.0, 1.0, 0.1, V(0, 0, 0))
        t_b = Part.makeBox(1.0, 1.0, 0.1, V(30, 0, 0))
        copper = doc_ps.addObject("Part::Feature", "CopperLayer")
        copper.Shape = Part.makeCompound([t_a, t_b])

        ps_rects = trace_routing.collect_obstacles(
            doc_ps, exclude_names=set(), z_min=-1.0, z_max=1.0, expand_mm=0.0
        )
        tc.check("collect_obstacles: a 2-solid layer becomes TWO small rects, "
                  "not one board-spanning rect",
                  len(ps_rects) == 2, f"got {len(ps_rects)} rects: {ps_rects}")
        tc.check("collect_obstacles: neither per-solid rect spans the whole "
                  "layer extent (the gap between traces stays open)",
                  all((r.xmax - r.xmin) < 5.0 for r in ps_rects),
                  f"got {ps_rects}")
    finally:
        FreeCAD.closeDocument(doc_ps.Name)

    # ── collect_obstacles: construction geometry / oversized bbox exclusion ──
    # Regression guard for the reported "gridding a STEP file hangs forever"
    # bug: FreeCAD's Origin datum planes/axes (App::Plane / App::Line, added
    # by many STEP imports) carry ~1e100 mm bounding boxes. If treated as
    # obstacles, bucketing one into mm-scale cells is an effectively infinite
    # loop (see the VisibilityGraph no-hang test below). They must never
    # reach that path.
    doc2 = new_document("TestTraceRoutingConstructionGeom")
    try:
        real_body = doc2.addObject("Part::Feature", "RealBody")
        real_body.Shape = Part.makeBox(3.0, 3.0, 0.5, V(0, 0, 0))   # z in [0, 0.5]

        # App::Part / App::DocumentObjectGroup containers — not physical bodies
        container = doc2.addObject("App::Part", "AnAppPart")
        grp2 = doc2.addObject("App::DocumentObjectGroup", "AGroup")

        # A genuine Part::Feature but with an implausibly large bbox (stands in
        # for a datum plane's shape / a unit-scale mistake) — must be dropped
        # by the _MAX_OBSTACLE_EXTENT_MM sanity clamp.
        giant = doc2.addObject("Part::Feature", "GiantThing")
        giant.Shape = Part.makeBox(5.0e6, 5.0e6, 1.0, V(-2.5e6, -2.5e6, 0.0))

        rects2 = trace_routing.collect_obstacles(
            doc2, exclude_names=set(), z_min=-1.0, z_max=1.0, expand_mm=0.0
        )
        tc.check("collect_obstacles: keeps only the real physical body, dropping "
                  "App::Part/group containers and the implausibly-large-bbox object",
                  len(rects2) == 1, f"got {len(rects2)} rects: {rects2}")
        if rects2:
            tc.check("collect_obstacles: the kept rect is the real body (small bbox)",
                      (rects2[0].xmax - rects2[0].xmin) < 100.0,
                      f"got width {rects2[0].xmax - rects2[0].xmin}")
    finally:
        FreeCAD.closeDocument(doc2.Name)

    # ── VisibilityGraph: an oversized obstacle must NOT hang (THE bug) ───────
    # Before the fix, add_obstacle() bucketed a 1e100-wide rect cell by cell
    # via range(cx0, cx1+1) — ~1e100 iterations, an effective infinite loop
    # that froze FreeCAD ("stuck in calculation state"). The clamp to the
    # grid's own cell region must make this complete instantly. A hang here
    # would hang the whole suite, so this test both proves the fix and
    # fails-by-timeout-visibility if it ever regresses.
    hang_pts = [V(x * 0.5, y * 0.5, 0.0) for x in range(12) for y in range(12)]
    huge_rect = trace_routing.Rect(-1e100, -0.35, 1e100, 0.35)
    t_hang = time.time()
    hang_graph = trace_routing.build_visibility_graph(hang_pts, [huge_rect],
                                                        neighbor_radius=2.0, cell_size=0.5)
    hang_elapsed = time.time() - t_hang
    tc.check("VisibilityGraph: an astronomically large obstacle is bucketed in "
              "bounded time (regression guard for the STEP-file gridding hang)",
              hang_elapsed < 2.0, f"took {hang_elapsed:.3f}s")
    # And it still behaves as an obstacle where it actually overlaps the grid:
    # the band at y in [-0.35, 0.35] blocks a segment crossing it.
    tc.check("VisibilityGraph: the clamped huge obstacle still blocks a crossing "
              "segment within the grid region (correctness preserved)",
              not hang_graph.line_of_sight(V(0.0, -1.0, 0.0), V(0.0, 1.0, 0.0)))
    tc.check("VisibilityGraph: a segment clear of the huge obstacle's band is "
              "still reported clear",
              hang_graph.line_of_sight(V(0.0, 1.0, 0.0), V(2.0, 1.0, 0.0)))

    # ── sample_face_grid: refuses an implausibly large (datum-plane-like) face ─
    huge_face = Part.makePlane(5.0e6, 5.0e6)
    huge_face_pts = trace_routing.sample_face_grid(huge_face, 0.5)
    tc.check("sample_face_grid: returns no points for an implausibly large face "
              "(datum-plane guard, avoids a discretize() blow-up)",
              huge_face_pts == [], f"got {len(huge_face_pts)} points")

    # ── find_path: unobstructed direct case ──────────────────────────────────
    graph_direct = trace_routing.VisibilityGraph([V(0, 0, 0), V(10, 0, 0)], [],
                                                    neighbor_radius=20.0, cell_size=5.0)
    direct_path = trace_routing.find_path(graph_direct, 0, 1, max_bend_deg=45.0)
    tc.check("find_path: unobstructed case returns the direct 2-point path",
              direct_path is not None and len(direct_path) == 2, f"got {direct_path}")

    # ── find_path: degenerate inputs ─────────────────────────────────────────
    def _same_point():
        trace_routing.find_path(graph_direct, 0, 0, 45.0)
    tc.check("find_path: start == end raises ValueError",
              _raises(ValueError, _same_point))

    def _bad_angle():
        trace_routing.find_path(graph_direct, 0, 1, 200.0)
    tc.check("find_path: out-of-range max_bend_deg raises ValueError",
              _raises(ValueError, _bad_angle))

    # ── find_path: angle-convention pin-down ─────────────────────────────────
    # A single forced detour requiring an exact ~30 degree bend (deviation
    # from straight) at the midpoint — a direct start->end line is blocked by
    # an obstacle placed at ITS midpoint, while both legs via `mid` stay clear.
    theta = math.radians(30.0)
    start = V(-10.0, 0.0, 0.0)
    mid   = V(0.0, 0.0, 0.0)
    end   = V(mid.x + 10.0 * math.cos(theta), mid.y + 10.0 * math.sin(theta), 0.0)

    mp = V((start.x + end.x) / 2.0, (start.y + end.y) / 2.0, 0.0)
    detour_obstacle = trace_routing.Rect(mp.x - 0.5, mp.y - 0.5, mp.x + 0.5, mp.y + 0.5)

    tc.check("find_path fixture: obstacle blocks the direct start-end segment",
              not trace_routing.segment_clear(start, end, [detour_obstacle]))
    tc.check("find_path fixture: obstacle does not block the start-mid leg",
              trace_routing.segment_clear(start, mid, [detour_obstacle]))
    tc.check("find_path fixture: obstacle does not block the mid-end leg",
              trace_routing.segment_clear(mid, end, [detour_obstacle]))

    graph_loose = trace_routing.VisibilityGraph([start, mid, end], [detour_obstacle],
                                                   neighbor_radius=50.0, cell_size=5.0)
    path_loose = trace_routing.find_path(graph_loose, 0, 2, max_bend_deg=35.0)
    tc.check("find_path: succeeds when max_bend_deg (35) exceeds the required ~30 degree bend",
              path_loose is not None and len(path_loose) == 3, f"got {path_loose}")

    graph_tight = trace_routing.VisibilityGraph([start, mid, end], [detour_obstacle],
                                                   neighbor_radius=50.0, cell_size=5.0)
    path_tight = trace_routing.find_path(graph_tight, 0, 2, max_bend_deg=25.0)
    tc.check("find_path: fails when max_bend_deg (25) is tighter than the required ~30 degree "
              "bend — pins down the convention as deviation-from-straight, not interior angle",
              path_tight is None, f"got {path_tight}")

    # ── simplify_path ─────────────────────────────────────────────────────────
    near_collinear = [V(0, 0, 0), V(5, 0.01, 0), V(10, 0, 0)]
    simplified = trace_routing.simplify_path(near_collinear, [], max_bend_deg=45.0)
    tc.check("simplify_path: collapses a near-collinear midpoint when unobstructed",
              len(simplified) == 2, f"got {simplified}")

    blocking_rect = trace_routing.Rect(4.0, -1.0, 6.0, 1.0)
    simplified_blocked = trace_routing.simplify_path(near_collinear, [blocking_rect], max_bend_deg=45.0)
    tc.check("simplify_path: keeps the midpoint when an obstacle blocks the shortcut",
              len(simplified_blocked) == 3, f"got {simplified_blocked}")

    # ── route_leg (full orchestration) ───────────────────────────────────────
    graph_route = trace_routing.VisibilityGraph([start, mid, end], [detour_obstacle],
                                                   neighbor_radius=50.0, cell_size=5.0)
    routed = trace_routing.route_leg(graph_route, start, end, max_bend_deg=35.0)
    tc.check("route_leg: finds a detour when the direct route is blocked",
              routed is not None and len(routed) >= 2, f"got {routed}")

    routed_fail = trace_routing.route_leg(graph_route, start, end, max_bend_deg=5.0)
    tc.check("route_leg: returns None (not an exception) when constraints can't be satisfied",
              routed_fail is None)

    routed_same = trace_routing.route_leg(graph_route, start, start, max_bend_deg=45.0)
    tc.check("route_leg: start == end returns None rather than raising",
              routed_same is None)

    # ── endpoint exemption: a leg may start/end inside an obstacle ───────────
    # Regression guard for "No path found" between two nearby points: on a
    # real board, grid points are sampled across existing copper, so a
    # clicked start/end frequently sits INSIDE an obstacle keep-out. That
    # obstacle (the pad/copper being connected) must not block the leg.
    ex_a = V(0.0, 0.0, 0.0)
    ex_b = V(5.0, 0.0, 0.0)
    # An obstacle that CONTAINS the start point ex_a (and part of the path).
    covering = trace_routing.Rect(-1.0, -1.0, 1.0, 1.0)
    tc.check("exemption fixture: start point really is inside the obstacle",
              trace_routing.point_in_rect(ex_a, covering))
    tc.check("exemption fixture: without exemption the start-covering obstacle "
              "blocks the direct segment",
              not trace_routing.segment_clear(ex_a, ex_b, [covering]))

    graph_ex = trace_routing.VisibilityGraph([ex_a, ex_b], [covering],
                                               neighbor_radius=20.0, cell_size=5.0)
    routed_ex = trace_routing.route_leg(graph_ex, ex_a, ex_b, max_bend_deg=45.0)
    tc.check("route_leg: routes out of an obstacle that contains the start point "
              "(endpoint-exemption fix for the reported 'No path found')",
              routed_ex is not None and len(routed_ex) >= 2, f"got {routed_ex}")

    # But an obstacle that contains NEITHER endpoint still blocks, forcing a
    # detour / failure as before (exemption is strictly endpoint-scoped).
    blocker_mid = trace_routing.Rect(2.0, -1.0, 3.0, 1.0)
    graph_ex2 = trace_routing.VisibilityGraph([ex_a, ex_b], [blocker_mid],
                                                neighbor_radius=20.0, cell_size=5.0)
    routed_ex2 = trace_routing.route_leg(graph_ex2, ex_a, ex_b, max_bend_deg=45.0)
    tc.check("route_leg: a mid-path obstacle containing neither endpoint is NOT "
              "exempted (no free straight shot, only 2 collinear nodes -> None)",
              routed_ex2 is None, f"got {routed_ex2}")

    # ── build_trace_solid ────────────────────────────────────────────────────
    two_pt = [V(0, 0, 0), V(10, 0, 0)]
    shape_2pt = trace_routing.build_trace_solid(two_pt, width_mm=0.3, thickness_mm=0.05)
    tc.check("build_trace_solid: 2-point trace is a valid, non-null shape",
              shape_2pt is not None and shape_2pt.isValid() and not shape_2pt.isNull())
    tc.check("build_trace_solid: 2-point trace has positive volume",
              shape_2pt.Volume > 0, f"got {shape_2pt.Volume}")
    tc.check("build_trace_solid: bounding-box length roughly matches the waypoint span",
              abs(shape_2pt.BoundBox.XLength - 10.0) < 0.5, f"got {shape_2pt.BoundBox.XLength}")

    multi_leg = [V(0, 0, 0), V(10, 0, 0), V(10, 10, 0), V(20, 10, 0)]
    shape_multi = trace_routing.build_trace_solid(multi_leg, width_mm=0.3, thickness_mm=0.05)
    tc.check("build_trace_solid: multi-leg trace is a valid shape with positive volume",
              shape_multi is not None and shape_multi.isValid() and not shape_multi.isNull()
              and shape_multi.Volume > 0, f"volume={getattr(shape_multi, 'Volume', None)}")

    fallback_shape = trace_routing._fuse_leg_boxes(multi_leg, 0.3, 0.05)
    tc.check("build_trace_solid: box-fuse fallback path (exercised directly) is valid with volume",
              fallback_shape is not None and fallback_shape.isValid() and not fallback_shape.isNull()
              and fallback_shape.Volume > 0)

    def _too_few_points():
        trace_routing.build_trace_solid([V(0, 0, 0)], 0.3, 0.05)
    tc.check("build_trace_solid: fewer than 2 distinct waypoints raises ValueError",
              _raises(ValueError, _too_few_points))

    def _bad_width():
        trace_routing.build_trace_solid(two_pt, -0.1, 0.05)
    tc.check("build_trace_solid: non-positive width raises ValueError",
              _raises(ValueError, _bad_width))

    return tc.results


def _raises(exc_type, fn) -> bool:
    try:
        fn()
    except exc_type:
        return True
    except Exception:
        return False
    return False
