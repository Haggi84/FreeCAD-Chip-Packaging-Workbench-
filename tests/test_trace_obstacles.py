# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.trace_obstacles — exact copper outlines as routing
obstacles.

The reason this module exists is a real failure: approximating copper by its
axis-aligned bounding box turned one ordinary DIAGONAL trace into a
7.19 x 10.34 mm keep-out that swallowed two pads 2.5 mm apart, so no route
between them was possible. The regression guard below reproduces exactly
that geometry.
"""

import math

import FreeCAD
import Part

from _harness import TestCase, new_document

import core.trace_obstacles as tob

V = FreeCAD.Vector


def _rect_poly(x0, y0, x1, y1):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return tob.Poly(pts, (x0, y0, x1, y1))


def run():
    tc = TestCase("trace_obstacles")

    # ── point in polygon ─────────────────────────────────────────────────────
    sq = _rect_poly(0, 0, 10, 10)
    tc.check("point_in_poly: inside", tob.point_in_poly(sq, 5, 5))
    tc.check("point_in_poly: outside", not tob.point_in_poly(sq, 15, 5))
    tc.check("point_in_poly: bbox reject is consistent", not tob.point_in_poly(sq, -1, -1))

    # An L-shaped polygon: the notch must read as OUTSIDE, which a bounding
    # box could never express.
    ell = tob.Poly([(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)],
                   (0, 0, 10, 10))
    tc.check("point_in_poly: a concave notch is outside the shape",
              not tob.point_in_poly(ell, 8, 8))
    tc.check("point_in_poly: the solid arm of an L is inside",
              tob.point_in_poly(ell, 2, 8) and tob.point_in_poly(ell, 8, 2))

    # ── segment / polygon distance ───────────────────────────────────────────
    tc.check("seg_poly_distance: crossing the polygon gives 0",
              tob.seg_poly_distance(V(-5, 5, 0), V(15, 5, 0), sq) == 0.0)
    tc.check("seg_poly_distance: a segment inside gives 0",
              tob.seg_poly_distance(V(2, 2, 0), V(8, 8, 0), sq) == 0.0)
    d = tob.seg_poly_distance(V(-5, 15, 0), V(15, 15, 0), sq)
    tc.check("seg_poly_distance: a parallel segment measures the gap",
              abs(d - 5.0) < 1e-9, f"got {d}")

    tc.check("poly_blocks: honours the clearance threshold",
              tob.poly_blocks(V(-5, 12, 0), V(15, 12, 0), sq, clearance=3.0)
              and not tob.poly_blocks(V(-5, 12, 0), V(15, 12, 0), sq, clearance=1.0))

    # ── the diagonal-trace regression ────────────────────────────────────────
    # A thin diagonal run: as a polygon it blocks only its own thin band, but
    # its bounding box is a huge square. Two points on either side of that
    # box, well away from the copper itself, must be routable.
    t = 0.15
    diag = tob.Poly(
        [(0.0, 0.0), (7.0, 10.0), (7.0 - t * 2, 10.0), (-t * 2, 0.0)],
        (-t * 2, 0.0, 7.0, 10.0),
    )
    a, b = V(5.0, 1.0, 0), V(6.5, 1.0, 0)     # both inside the BBOX, clear of the copper
    tc.check("regression fixture: both points lie inside the diagonal's bounding box",
              diag.bbox[0] <= a.x <= diag.bbox[2] and diag.bbox[1] <= a.y <= diag.bbox[3]
              and diag.bbox[0] <= b.x <= diag.bbox[2])
    tc.check("a diagonal trace does NOT block a segment that misses the actual copper "
              "(its bounding box would have)",
              not tob.poly_blocks(a, b, diag, clearance=0.35),
              f"distance was {tob.seg_poly_distance(a, b, diag)}")
    tc.check("the same diagonal still blocks a segment that really crosses it",
              tob.poly_blocks(V(0.0, 5.0, 0), V(7.0, 5.0, 0), diag, clearance=0.35))

    # ── convex hull ──────────────────────────────────────────────────────────
    hull = tob.convex_hull([(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)])
    tc.check("convex_hull: drops interior points, keeps the 4 corners",
              len(hull) == 4, f"got {hull}")

    # ── PolyField ────────────────────────────────────────────────────────────
    field = tob.PolyField([sq], clearance=0.5, cell_size=2.0)
    tc.check("PolyField: reports a blocking polygon",
              field.blockers(V(-5, 5, 0), V(15, 5, 0)) == [0])
    tc.check("PolyField: containing() finds the polygon a point is in",
              field.containing(V(5, 5, 0)) == frozenset({0}))
    tc.check("PolyField: an exempt polygon does not block at all — a trace is on "
              "the net of the pad it starts from (same-net allowance)",
              field.blockers(V(5, 5, 0), V(15, 5, 0), exempt=frozenset({0})) == [])
    tc.check("PolyField: walk_points returns hull waypoints pushed clear of the shape",
              len(field.walk_points(0, 0.1, 0.0)) >= 4)
    for wp in field.walk_points(0, 0.1, 0.0):
        tc.check("PolyField: each walk point lies outside the polygon it goes around",
                  not tob.point_in_poly(sq, wp.x, wp.y), f"{(wp.x, wp.y)}")

    # Regression: the offset must be a MITRE along the edge normals, not a
    # radial push from the centroid. On a tall thin shape a radial push puts
    # the corner nearer the long edge than the clearance requires, so every
    # detour through it fails its own collision check and the walk-around
    # reports "blocked" for an obstacle it could obviously have gone around.
    tall = _rect_poly(9.0, 6.0, 11.0, 14.0)          # 2 x 8 mm, like a trace
    clr = 0.35
    tall_field = tob.PolyField([tall], clearance=clr, cell_size=1.0)
    wps = tall_field.walk_points(0, 1e-3, 0.0)
    tc.check("walk_points on a tall thin shape: four corners", len(wps) == 4, f"got {wps}")
    for wp in wps:
        d = tob.seg_poly_distance(wp, wp, tall)
        tc.check("walk_points: every waypoint keeps at least the full clearance "
                  "from the obstacle (mitre offset, not a radial push)",
                  d >= clr - 1e-6, f"point {(round(wp.x, 3), round(wp.y, 3))} only {d:.4f} away")
    # ...and consecutive waypoints along one side must be routable between.
    ordered = sorted(wps, key=lambda p: (p.y, p.x))
    for u, v in zip(ordered, ordered[1:]):
        if abs(u.y - v.y) < 1e-6:                     # a side of the detour
            tc.check("walk_points: the leg between two waypoints on the same side "
                      "does not itself collide",
                      not tob.poly_blocks(u, v, tall, clr),
                      f"{(u.x, u.y)}->{(v.x, v.y)}")

    # ── routable-surface boundary ────────────────────────────────────────────
    # On a 3-D body the routing face is TRIMMED, so its parameter rectangle is
    # bigger than the face. Without a boundary the walk-around can route right
    # off the edge of the surface and still look valid in 2-D.
    bnd = _rect_poly(0, 0, 10, 10)
    bounded = tob.PolyField([], clearance=0.5, cell_size=2.0, boundary=bnd)
    tc.check("boundary: a segment well inside the face is fine",
              bounded.blockers(V(2, 2, 0), V(8, 8, 0)) == [])
    tc.check("boundary: a segment leaving the face is blocked",
              tob.PolyField.BOUNDARY in bounded.blockers(V(5, 5, 0), V(25, 5, 0)))
    tc.check("boundary: a segment entirely outside the face is blocked",
              tob.PolyField.BOUNDARY in bounded.blockers(V(20, 20, 0), V(25, 25, 0)))
    tc.check("boundary: hugging the edge closer than the clearance is blocked",
              tob.PolyField.BOUNDARY in bounded.blockers(V(0.2, 2, 0), V(0.2, 8, 0)))
    tc.check("boundary: no boundary set means no such restriction",
              tob.PolyField([], clearance=0.5, cell_size=2.0)
              .blockers(V(5, 5, 0), V(25, 5, 0)) == [])

    tc.check("seg_poly_edge_distance: measures to the edges even from inside "
              "(unlike seg_poly_distance, which treats inside as a collision)",
              abs(tob.seg_poly_edge_distance(V(5, 5, 0), V(5, 6, 0), bnd) - 4.0) < 1e-9,
              f"got {tob.seg_poly_edge_distance(V(5, 5, 0), V(5, 6, 0), bnd)}")

    # The walk-around must not try to 'detour around' the face edge.
    import core.trace_walkaround as _wa
    off_face = _wa.walk_around(V(5, 5, 0), V(25, 5, 0), bounded, step_deg=_wa.STEP_45)
    tc.check("walk_around: reports blocked rather than inventing a detour around "
              "the edge of the routable surface",
              off_face is None, f"got {off_face}")

    # ── collection from a document ───────────────────────────────────────────
    doc = new_document("TestTraceObstacles")
    try:
        board = doc.addObject("Part::Feature", "Board")
        board.Shape = Part.makeBox(20, 20, 1)

        # One object holding two separate copper solids.
        s1 = Part.makeBox(2, 2, 0.1, V(3, 3, 1))
        s2 = Part.makeBox(2, 2, 0.1, V(12, 12, 1))
        cu = doc.addObject("Part::Feature", "Copper")
        cu.Shape = Part.makeCompound([s1, s2])

        cp = doc.addObject("Part::Feature", "cp")
        cp.Shape = Part.makeBox(0.1, 0.1, 0.1, V(1, 1, 1))
        cp.addProperty("App::PropertyBool", "IsContactPoint", "Wirebond", "")
        cp.IsContactPoint = True

        polys = tob.collect_obstacle_polys(doc, {"Board"}, 0.9, 1.3)
        tc.check("collect_obstacle_polys: one polygon per copper SOLID, board and "
                  "contact-point marker excluded",
                  len(polys) == 2, f"got {len(polys)}")
        if len(polys) == 2:
            areas = sorted((p.bbox[2] - p.bbox[0]) for p in polys)
            tc.check("collect_obstacle_polys: each polygon is the size of its own "
                      "solid, not of the whole layer",
                      all(abs(w - 2.0) < 0.2 for w in areas), f"widths {areas}")

        # Holes in the routing surface become obstacles too.
        outer = Part.Face(Part.makePolygon(
            [V(0, 0, 0), V(20, 0, 0), V(20, 20, 0), V(0, 20, 0), V(0, 0, 0)]))
        holed = outer.cut(Part.Face(Part.Wire(Part.makeCircle(2.0, V(10, 10, 0)))))
        hp = tob.face_hole_polys(holed.Faces[0])
        tc.check("face_hole_polys: a cutout becomes one obstacle polygon",
                  len(hp) == 1, f"got {len(hp)}")
        tc.check("face_hole_polys: a face with no holes yields none",
                  tob.face_hole_polys(outer) == [])

        # ── outline_polys_of_object_on_frame: single-object footprint ───────
        import core.routing_frame as rf
        top = board.Shape.Faces[max(
            range(len(board.Shape.Faces)),
            key=lambda i: board.Shape.Faces[i].Area)]
        frame = rf.SurfaceFrame(top)
        trace_obj = doc.addObject("Part::Feature", "SomeTrace")
        trace_obj.Shape = Part.makeBox(5, 0.3, 0.1, V(0, 0, 1))

        trace_polys = tob.outline_polys_of_object_on_frame(trace_obj, frame)
        tc.check("outline_polys_of_object_on_frame: one polygon for a single-solid object",
                  len(trace_polys) == 1, f"got {len(trace_polys)}")
        if trace_polys:
            bbox = trace_polys[0].bbox
            tc.check("outline_polys_of_object_on_frame: the polygon matches the "
                      "object's own footprint, not some other object's",
                      abs((bbox[2] - bbox[0]) - 5.0) < 0.2 and abs((bbox[3] - bbox[1]) - 0.3) < 0.2,
                      f"bbox {bbox}")

        empty_obj = doc.addObject("Part::Feature", "EmptyShape")
        tc.check("outline_polys_of_object_on_frame: an object with no Shape yields no polygons",
                  tob.outline_polys_of_object_on_frame(empty_obj, frame) == [])
    finally:
        FreeCAD.closeDocument(doc.Name)

    return tc.results
