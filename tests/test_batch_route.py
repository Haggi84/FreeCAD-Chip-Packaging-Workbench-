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
from routing.InteractiveRouterCommand import bake_trace

V = FreeCAD.Vector


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

    return tc.results
