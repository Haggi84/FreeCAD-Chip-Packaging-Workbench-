# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for the batch-route pipeline: SurfaceFrame -> PolyField ->
route_head -> bake_trace, looped over several queued pad pairs — exactly
what routing/BatchRouteSession.route_all() does. No FreeCADGui.Selection
needed: this calls the same functions the session calls directly, matching
the existing headless/GUI split (core.trace_walkaround / core.routing_frame
have headless tests here; the GUI session/panel files, like
TraceRoutingSession.py before them, are not covered by freecadcmd since they
need FreeCADGui.Selection).

The property that actually needs pinning down is obstacle GROWTH: after
baking pair 1's trace, does folding its own footprint into the live
PolyField (via core.trace_obstacles.outline_polys_of_object_on_frame +
PolyField.add) actually make pair 2 route around it? Without that step,
every pair after the first would happily overlap the ones baked before it.
"""

import FreeCAD
import Part

from _harness import TestCase, new_document

import core.trace_walkaround as wa
import core.trace_obstacles as tob
import core.routing_frame as rf
from routing.InteractiveRouterCommand import (bake_trace, rebuild_trace,
                                               resolve_trace_frame)
from routing.BatchRouteSession import BatchRouteSession

V = FreeCAD.Vector


def _make_session(doc, board, face_index, top, frame, params):
    """A BatchRouteSession wired up WITHOUT start_session — that method
    touches FreeCADGui.Selection, which freecadcmd doesn't have. Everything
    else (field bookkeeping, route_all, rip-up) is GUI-free and is exactly
    what this wires."""
    s = BatchRouteSession()
    s.doc = doc
    s.obj_name = board.Name
    s.face_index = face_index
    s.frame = frame
    s.params = dict(params)
    s._boundary = tob.face_outer_poly_on_frame(top, frame)
    band = params["thickness_mm"] + params["clearance_mm"]
    trace_names = {o.Name for o in doc.Objects if getattr(o, "IsRoutingTrace", False)}
    s._base_polys = tob.collect_obstacle_polys_on_frame(
        doc, {board.Name} | trace_names, frame, band)
    s._base_polys += tob.face_hole_polys_on_frame(top, frame)
    s._trace_polys = {}
    for o in doc.Objects:
        if getattr(o, "IsRoutingTrace", False):
            polys = s._trace_obstacle_polys(o)
            if polys:
                s._trace_polys[o.Name] = polys
    s._rebuild_field()
    s.is_active = True
    return s


def _xy(path):
    return [(round(p.x, 3), round(p.y, 3)) for p in path] if path else path


def _add_pad(doc, name, pos):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = Part.makeBox(0.6, 0.6, 0.05, pos + V(-0.3, -0.3, 0))
    obj.addProperty("App::PropertyBool", "IsContactPoint", "Routing", "")
    obj.IsContactPoint = True
    obj.addProperty("App::PropertyVector", "ContactPoint", "Routing", "")
    obj.ContactPoint = pos
    return obj


def run():
    tc = TestCase("batch_route")

    doc = new_document("TestBatchRoute")
    try:
        board = doc.addObject("Part::Feature", "Board")
        board.Shape = Part.makeBox(40, 40, 1, V(0, 0, 0))
        face_index = max(range(len(board.Shape.Faces)),
                          key=lambda i: board.Shape.Faces[i].Area)
        top = board.Shape.Faces[face_index]

        pads = {
            "A": _add_pad(doc, "PadA", V(10, 10, 1)),
            "B": _add_pad(doc, "PadB", V(20, 10, 1)),
            "C": _add_pad(doc, "PadC", V(15, 5, 1)),
            "D": _add_pad(doc, "PadD", V(15, 20, 1)),
            "E": _add_pad(doc, "PadE", V(35, 10, 1)),
            "F": _add_pad(doc, "PadF", V(35, 35, 1)),
        }
        FreeCAD.setActiveDocument(doc.Name)

        frame = rf.SurfaceFrame(top)
        w, th, cl = 0.3, 0.035, 0.2
        field = tob.PolyField([], clearance=cl + w / 2, cell_size=1.0,
                               boundary=tob.face_outer_poly_on_frame(top, frame))

        # Wall PadF in on all four sides so nothing can reach it — the same
        # "hollow walled in by copper" shape core.trace_walkaround already
        # covers directly, reused here to prove route_head reports None
        # through the actual batch-route call sequence, not just in
        # isolation.
        def _bar(x0, y0, x1, y1):
            return tob.Poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], (x0, y0, x1, y1))
        fx, fy = 35.0, 35.0
        for wall in (_bar(fx - 3, fy - 3, fx - 2, fy + 3),
                     _bar(fx + 2, fy - 3, fx + 3, fy + 3),
                     _bar(fx - 3, fy - 3, fx + 3, fy - 2),
                     _bar(fx - 3, fy + 2, fx + 3, fy + 3)):
            field.add(wall)

        def _route_and_bake(pad_a, pad_b):
            a2 = frame.to_2d(pad_a.ContactPoint)
            c2 = frame.to_2d(pad_b.ContactPoint)
            path, _flip = wa.route_head(V(*a2, 0), V(*c2, 0), field, step_deg=wa.STEP_45)
            if path is None:
                return None, None, None
            # Check collision-freeness against the field as route_head itself
            # saw it — BEFORE folding this trace's own outline in, or the
            # path would trivially "collide" with its own just-added footprint.
            clear = wa.path_blockers(path, field) == []
            pts2d = [(p.x, p.y) for p in path]
            obj = bake_trace(board.Name, face_index, pts2d, w, th, cl)
            if obj is not None:
                for poly in tob.outline_polys_of_object_on_frame(obj, frame):
                    field.add(poly)
            return path, obj, clear

        # Pair 1: a straight, unobstructed run along y=10 from x=10 to x=20.
        path1, trace1, clear1 = _route_and_bake(pads["A"], pads["B"])
        tc.check("batch route: pair 1 (unobstructed) routes and bakes",
                  path1 is not None and trace1 is not None, f"path={_xy(path1)}")

        # Pair 2: a straight line from C(15,5) to D(15,20) would cross right
        # through the middle of pair 1's trace (x=15 lies inside pair 1's
        # x=[10,20] run at y=10). This is the actual thing worth testing: does
        # obstacle growth make pair 2 detour around pair 1's baked trace?
        path2, trace2, clear2 = _route_and_bake(pads["C"], pads["D"])
        tc.check("batch route: pair 2 routes and bakes despite crossing pair 1's path",
                  path2 is not None and trace2 is not None, f"path={_xy(path2)}")
        if path2 is not None:
            tc.check("batch route: pair 2's route is collision-free against the "
                      "live field (which now includes pair 1's own baked trace)",
                      clear2, f"got {_xy(path2)}")
            tc.check("batch route: pair 2 actually detoured (more than the direct "
                      "2-point path) rather than cutting straight through pair 1's "
                      "trace — proof obstacle growth is doing real work",
                      len(path2) > 2, f"got {_xy(path2)}")

        # Pair 3: PadF is fully walled in -> reported blocked, not raised,
        # and nothing gets baked for it.
        path3, trace3, clear3 = _route_and_bake(pads["E"], pads["F"])
        tc.check("batch route: a deliberately unreachable pair reports blocked "
                  "(None) rather than raising or silently baking a bad trace",
                  path3 is None and trace3 is None)

        tc.check("batch route: exactly the two reachable pairs were baked, "
                  "not the blocked one",
                  len([o for o in doc.Objects if o.Name.startswith("Trace_")]) == 2,
                  f"got {[o.Name for o in doc.Objects if o.Name.startswith('Trace_')]}")
    finally:
        FreeCAD.closeDocument(doc.Name)

    # ── regression: two crossing pairs must never physically overlap ────────
    # Reported from real use: routing two pairs whose straight-line
    # connections cross (the classic "X" of two diagonals on a rectangular
    # board) baked two traces that visibly crossed each other in the 3-D
    # view. Root cause: the first (BENT) trace's obstacle footprint only
    # covered its longest leg — outline_polys_of_object_on_frame picked the
    # single largest near-horizontal face instead of one per leg — so the
    # second pair's route never saw the missing leg and cut straight
    # through it. This checks actual baked SOLID geometry (Shape.distToShape),
    # not just the 2-D field the router itself consulted, so it would catch
    # this even if some other part of the pipeline made the same mistake.
    doc2 = new_document("TestBatchRouteNoCross")
    try:
        board = doc2.addObject("Part::Feature", "Board")
        board.Shape = Part.makeBox(80, 50, 10, V(-40, -25, 0))
        face_index = max(range(len(board.Shape.Faces)),
                          key=lambda i: board.Shape.Faces[i].Area)
        top = board.Shape.Faces[face_index]
        FreeCAD.setActiveDocument(doc2.Name)

        frame = rf.SurfaceFrame(top)
        w, th, cl = 0.3, 0.035, 0.2
        field = tob.PolyField([], clearance=cl + w / 2, cell_size=1.0,
                               boundary=tob.face_outer_poly_on_frame(top, frame))

        # Two diagonals of the board that cross near its centre.
        p1 = V(-36.273, -22.182, 10.0)
        p2 = V(34.443, 19.609, 10.0)
        p3 = V(-34.862, 17.162, 10.0)
        p4 = V(30.966, -22.854, 10.0)

        def _route_and_bake2(a, c):
            a2, c2 = frame.to_2d(a), frame.to_2d(c)
            path, _flip = wa.route_head(V(*a2, 0), V(*c2, 0), field, step_deg=wa.STEP_45)
            if path is None:
                return None
            pts2d = [(p.x, p.y) for p in path]
            obj = bake_trace(board.Name, face_index, pts2d, w, th, cl)
            for poly in tob.outline_polys_of_object_on_frame(obj, frame):
                field.add(poly)
            return obj

        trace_a = _route_and_bake2(p1, p2)
        trace_b = _route_and_bake2(p4, p3)
        tc.check("batch route: both legs of a crossing pair of diagonals route "
                  "and bake", trace_a is not None and trace_b is not None)
        if trace_a is not None and trace_b is not None:
            dist = trace_a.Shape.distToShape(trace_b.Shape)[0]
            tc.check("batch route: the two baked traces never touch or overlap "
                      "(regression guard for the missing-leg obstacle bug)",
                      dist >= cl - 1e-6, f"distance={dist:.4f} mm, required >= {cl} mm")

        # Regression: the SAME missing-leg crossing, but across SESSIONS —
        # a NEW session's obstacle collection scans the document afresh via
        # collect_obstacle_polys_on_frame, which used its own single-face
        # outline rule and dropped the earlier bent traces' short legs all
        # over again (reported from real use as a crossing on the 4th
        # connection, routed in a separate batch session).
        if trace_a is not None and trace_b is not None:
            field2 = tob.PolyField(
                tob.collect_obstacle_polys_on_frame(doc2, {board.Name}, frame, th + cl),
                clearance=cl + w / 2, cell_size=1.0,
                boundary=tob.face_outer_poly_on_frame(top, frame))
            p5, p6 = V(-30, -10, 10.0), V(25, 8, 10.0)
            a2, c2 = frame.to_2d(p5), frame.to_2d(p6)
            path3, _flip = wa.route_head(V(*a2, 0), V(*c2, 0), field2, step_deg=wa.STEP_45)
            if path3 is not None:
                trace_c = bake_trace(board.Name, face_index,
                                      [(p.x, p.y) for p in path3], w, th, cl)
                d_a = trace_c.Shape.distToShape(trace_a.Shape)[0]
                d_b = trace_c.Shape.distToShape(trace_b.Shape)[0]
                tc.check("batch route: a trace routed in a FRESH session keeps "
                          "clearance from BOTH legs of earlier sessions' bent "
                          "traces (cross-session missing-leg regression guard)",
                          min(d_a, d_b) >= cl - 1e-6,
                          f"distances {d_a:.4f} / {d_b:.4f} mm, required >= {cl} mm")

        # Regression: the walk-around's posture math can emit consecutive
        # path points separated only by floating-point noise. OCCT's solid
        # builders reject such segments ("length of box too small"), which
        # used to crash bake_trace — and with it the whole Route All batch —
        # instead of just baking the real geometry (found by a randomized
        # stress run, 4 crashes out of 25 crossing trials).
        degenerate_path = [(-10.0, -10.0), (-10.0 + 1e-9, -10.0 + 1e-9),
                            (5.0, 5.0), (5.0, 5.0 + 1e-10), (5.0, 12.0)]
        try:
            deg_obj = bake_trace(board.Name, face_index, degenerate_path, w, th, cl)
        except Exception as exc:
            deg_obj = None
            tc.check("bake_trace: a path containing numerically-degenerate "
                      "points bakes instead of raising",
                      False, f"raised {exc}")
        else:
            tc.check("bake_trace: a path containing numerically-degenerate "
                      "points bakes instead of raising",
                      deg_obj is not None and not deg_obj.Shape.isNull())
        if deg_obj is not None:
            tc.check("bake_trace: the degenerate points were dropped, the real "
                      "waypoints kept",
                      len(deg_obj.Waypoints) == 3,
                      f"got {len(deg_obj.Waypoints)} waypoint(s)")
    finally:
        FreeCAD.closeDocument(doc2.Name)

    # ── trace spacing: minimum edge-to-edge space between traces ────────────
    doc3 = new_document("TestBatchRouteSpacing")
    try:
        board = doc3.addObject("Part::Feature", "Board")
        board.Shape = Part.makeBox(60, 30, 5, V(0, 0, 0))
        face_index = max(range(len(board.Shape.Faces)),
                          key=lambda i: (board.Shape.Faces[i].Area,
                                          board.Shape.Faces[i].BoundBox.ZMax))
        top = board.Shape.Faces[face_index]
        FreeCAD.setActiveDocument(doc3.Name)
        frame = rf.SurfaceFrame(top)
        w, th, cl = 0.3, 0.035, 0.2

        pad_a = _add_pad(doc3, "SpA", V(10, 10, 5))
        pad_b = _add_pad(doc3, "SpB", V(50, 10, 5))
        pad_c = _add_pad(doc3, "SpC", V(30, 3, 5))
        pad_d = _add_pad(doc3, "SpD", V(30, 25, 5))

        params = {"width_mm": w, "thickness_mm": th, "clearance_mm": cl,
                   "trace_spacing_mm": 1.0, "allow_reroute": False}
        session = _make_session(doc3, board, face_index, top, frame, params)
        session.queue = [("SpA", "SpB"), ("SpC", "SpD")]
        results = session.route_all()

        tc.check("trace spacing: both pairs still route with a 1.0 mm spacing",
                  all(r[2] == "baked" for r in results), f"got {results}")
        traces = [o for o in doc3.Objects if o.Name.startswith("Trace_")]
        if len(traces) == 2:
            d = traces[0].Shape.distToShape(traces[1].Shape)[0]
            tc.check("trace spacing: the second trace keeps the REQUESTED "
                      "spacing from the first, not just the clearance "
                      "(walks around its end at >= ~1.0 mm, not 0.2 mm)",
                      d >= 0.9, f"distance={d:.4f} mm, requested spacing 1.0 mm")
    finally:
        FreeCAD.closeDocument(doc3.Name)

    # ── rip-up & reroute: an existing trace moves to make room ──────────────
    # Wall W splits the board left/right with two gaps: A (y 10-11, fits ONE
    # trace) and B (y 15-16). A splitter wall P divides the LEFT side so the
    # lower-left region reaches ONLY gap A, while the upper-left region
    # reaches ONLY gap B. Trace 1 is baked occupying gap A, but its own
    # endpoints (upper-left -> right) could equally go via gap B. Pair 2
    # (lower-left -> right) can ONLY use gap A — so it is blocked unless the
    # session reroutes trace 1 through gap B first.
    doc4 = new_document("TestBatchRouteRipup")
    try:
        board = doc4.addObject("Part::Feature", "Board")
        board.Shape = Part.makeBox(70, 25, 5, V(0, 0, 0))
        face_index = max(range(len(board.Shape.Faces)),
                          key=lambda i: (board.Shape.Faces[i].Area,
                                          board.Shape.Faces[i].BoundBox.ZMax))
        top = board.Shape.Faces[face_index]
        FreeCAD.setActiveDocument(doc4.Name)
        frame = rf.SurfaceFrame(top)
        w, th, cl = 0.3, 0.035, 0.2

        def _wall(name, x0, y0, x1, y1):
            o = doc4.addObject("Part::Feature", name)
            o.Shape = Part.makeBox(x1 - x0, y1 - y0, 0.2, V(x0, y0, 5))
            return o

        _wall("W1", 28, 0, 30, 10)      # wall below gap A
        _wall("W2", 28, 11, 30, 15)     # wall between gaps A and B
        _wall("W3", 28, 16, 30, 25)     # wall above gap B
        _wall("P",  0, 12.5, 28, 13)    # left-side splitter: LL vs UL region

        # Trace 1 baked deliberately through gap A (its left end sits in the
        # upper-left region, so its REROUTE alternative via gap B exists).
        t1_wp = [frame.to_2d(V(x, y, 5)) for x, y in
                 [(5, 20), (20, 20), (20, 10.5), (60, 10.5)]]
        t1 = bake_trace(board.Name, face_index, t1_wp, w, th, cl)
        wp_before = list(t1.Waypoints)

        pad_2a = _add_pad(doc4, "R2a", V(5, 8.0, 5))
        pad_2b = _add_pad(doc4, "R2b", V(60, 8.0, 5))

        params = {"width_mm": w, "thickness_mm": th, "clearance_mm": cl,
                   "trace_spacing_mm": 0.0, "allow_reroute": False}
        session = _make_session(doc4, board, face_index, top, frame, params)

        # Pair 2 needs gap A; without rip-up it is simply blocked.
        session.queue = [("R2a", "R2b")]
        r2_blocked = session.route_all()
        tc.check("rip-up: with rerouting disabled, the conflicting pair is "
                  "reported blocked (fixture sanity)",
                  r2_blocked and r2_blocked[0][2] == "blocked", f"got {r2_blocked}")

        # Same pair with rerouting allowed: trace 1 must move to gap B.
        session.queue = [("R2a", "R2b")]
        session.params["allow_reroute"] = True
        r2 = session.route_all()
        tc.check("rip-up: with rerouting enabled, the pair routes by moving "
                  "the existing trace out of the way",
                  r2 and r2[0][2] == "baked", f"got {r2}")
        if r2 and r2[0][2] == "baked":
            wp_after = list(t1.Waypoints)
            tc.check("rip-up: the existing trace really was rerouted "
                      "(waypoints changed)",
                      len(wp_after) != len(wp_before)
                      or any((a - b).Length > 1e-6 for a, b in zip(wp_after, wp_before)),
                      f"before {len(wp_before)} wp, after {len(wp_after)} wp")
            t2 = doc4.getObject(r2[0][3])
            d = t1.Shape.distToShape(t2.Shape)[0]
            tc.check("rip-up: the rerouted trace and the new trace keep "
                      "clearance from each other",
                      d >= cl - 1e-6, f"distance={d:.4f} mm")
            tc.check("rip-up: the rerouted trace still connects its ORIGINAL "
                      "endpoints (the connection is moved, never lost)",
                      (wp_after[0] - wp_before[0]).Length < 1e-6
                      and (wp_after[-1] - wp_before[-1]).Length < 1e-6,
                      f"ends {wp_after[0]} / {wp_after[-1]}")
    finally:
        FreeCAD.closeDocument(doc4.Name)

    # ── trace rebuild machinery (what drag-editing commits through) ─────────
    doc5 = new_document("TestTraceRebuild")
    try:
        board = doc5.addObject("Part::Feature", "Board")
        board.Shape = Part.makeBox(40, 30, 5, V(0, 0, 0))
        face_index = max(range(len(board.Shape.Faces)),
                          key=lambda i: (board.Shape.Faces[i].Area,
                                          board.Shape.Faces[i].BoundBox.ZMax))
        top = board.Shape.Faces[face_index]
        FreeCAD.setActiveDocument(doc5.Name)
        frame = rf.SurfaceFrame(top)
        w, th, cl = 0.3, 0.035, 0.2

        pts = [frame.to_2d(V(x, y, 5)) for x, y in [(5, 15), (35, 15)]]
        t = bake_trace(board.Name, face_index, pts, w, th, cl)
        tc.check("rebuild: bake_trace persists the routing surface "
                  "(SourceObject / SourceFaceIndex)",
                  getattr(t, "SourceObject", "") == board.Name
                  and getattr(t, "SourceFaceIndex", -1) == face_index,
                  f"got {getattr(t, 'SourceObject', None)!r} / "
                  f"{getattr(t, 'SourceFaceIndex', None)!r}")

        rframe, rsrc, ridx = resolve_trace_frame(doc5, t)
        tc.check("rebuild: resolve_trace_frame finds the trace's own surface",
                  rframe is not None and rsrc == board.Name and ridx == face_index,
                  f"got {rsrc!r} / {ridx!r}")

        old_name, old_net = t.Name, t.NetName
        new_pts = [frame.to_2d(V(x, y, 5)) for x, y in
                   [(5, 15), (15, 15), (20, 22), (30, 22), (35, 15)]]
        r = rebuild_trace(t.Name, new_pts)
        tc.check("rebuild: rebuild_trace re-shapes the trace in place",
                  r is not None and not r.Shape.isNull(), f"got {r}")
        if r is not None:
            tc.check("rebuild: identity preserved (same object, name, net)",
                      r.Name == old_name and r.NetName == old_net,
                      f"got {r.Name} / {r.NetName}")
            tc.check("rebuild: waypoints follow the new path",
                      len(r.Waypoints) == 5, f"got {len(r.Waypoints)}")
            tc.check("rebuild: endpoints unchanged (drag re-shapes, never "
                      "re-connects)",
                      (r.Waypoints[0] - V(5, 15, 5)).Length < 1e-6
                      and (r.Waypoints[-1] - V(35, 15, 5)).Length < 1e-6,
                      f"got {r.Waypoints[0]} / {r.Waypoints[-1]}")

        # A trace WITHOUT the source properties (baked before they existed)
        # must still resolve via the geometric fallback scan.
        t.removeProperty("SourceObject")
        t.removeProperty("SourceFaceIndex")
        f2, s2, i2 = resolve_trace_frame(doc5, t)
        tc.check("rebuild: a legacy trace without SourceObject still resolves "
                  "its surface geometrically",
                  f2 is not None and s2 == board.Name, f"got {s2!r} / {i2!r}")
    finally:
        FreeCAD.closeDocument(doc5.Name)

    return tc.results
