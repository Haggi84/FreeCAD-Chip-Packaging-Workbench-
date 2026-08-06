# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.body_routing — routing that crosses from one face of
a 3-D body onto the next.

Routing WITHIN a face is already covered by test_trace_walkaround and
test_routing_frame. What is new here, and what these tests pin down, is
everything about leaving a face: that the body's topology yields the right
neighbours, that the chosen face sequence is the sensible one rather than
merely a valid one, and that the resulting per-face paths really do meet at
the shared edge instead of just ending near it.
"""

import FreeCAD
import Part

from _harness import TestCase

import core.body_routing as br
import core.routing_frame as rf
import core.trace_obstacles as tob

V = FreeCAD.Vector


def _plain_field_factory(shape, clearance=0.35):
    """field_for_face with no copper at all — just each face's own boundary.
    Keeps these tests about crossing faces rather than about obstacles."""
    cache = {}

    def factory(face_idx):
        if face_idx not in cache:
            face = shape.Faces[face_idx]
            frame = rf.SurfaceFrame(face)
            field = tob.PolyField(
                [], clearance=clearance, cell_size=1.0,
                boundary=tob.face_outer_poly_on_frame(face, frame))
            cache[face_idx] = (frame, field)
        return cache[face_idx]

    return factory, cache


def _face_by_normal(shape, pick):
    for i, f in enumerate(shape.Faces):
        try:
            n = f.normalAt(0, 0)
        except Exception:
            continue
        if pick(n, f):
            return i, f
    return None, None


def run():
    tc = TestCase("body_routing")

    box = Part.makeBox(40, 20, 10, V(0, 0, 0))

    # ── adjacency from real topology ────────────────────────────────────────
    adj = br.face_adjacency(box)
    tc.check("face_adjacency: every face of a box has neighbours",
              len(adj) == 6, f"got {len(adj)} faces")
    tc.check("face_adjacency: each box face touches exactly four others",
              all(len(v) == 4 for v in adj.values()),
              f"got {sorted(len(v) for v in adj.values())}")
    tc.check("face_adjacency: a face is never its own neighbour",
              all(all(nb != f for nb, _e in v) for f, v in adj.items()))

    cyl = Part.makeCylinder(10, 20)
    cadj = br.face_adjacency(cyl)
    tc.check("face_adjacency: a cylinder's curved flank is adjacent to both "
              "caps (topology, not a planar-only rule)",
              any(len(v) == 2 for v in cadj.values()), f"got {cadj}")

    tc.check("face_adjacency: an empty shape yields nothing rather than raising",
              br.face_adjacency(None) == {})

    # ── face path search ────────────────────────────────────────────────────
    top_i, _top = _face_by_normal(box, lambda n, f: n.z > 0.5)
    bottom_i, _b = _face_by_normal(box, lambda n, f: n.z < -0.5)
    side_i, _s = _face_by_normal(box, lambda n, f: abs(n.y) > 0.5)

    same = br.find_face_path(box, top_i, top_i)
    tc.check("find_face_path: start == goal is a single-face path",
              same == [top_i], f"got {same}")

    to_side = br.find_face_path(box, top_i, side_i)
    tc.check("find_face_path: top to an adjacent side is a direct two-face hop",
              to_side is not None and len(to_side) == 2
              and to_side[0] == top_i and to_side[-1] == side_i,
              f"got {to_side}")

    to_bottom = br.find_face_path(box, top_i, bottom_i)
    tc.check("find_face_path: top to bottom goes via exactly one side face "
              "(they are opposite, so it cannot be direct)",
              to_bottom is not None and len(to_bottom) == 3
              and to_bottom[0] == top_i and to_bottom[-1] == bottom_i,
              f"got {to_bottom}")

    capped = br.find_face_path(box, top_i, bottom_i, max_faces=2)
    tc.check("find_face_path: max_faces refuses a route that would need more "
              "faces than allowed",
              capped is None, f"got {capped}")

    # Two disjoint bodies in one compound share no edge, so no path exists.
    apart = Part.makeCompound([Part.makeBox(1, 1, 1, V(0, 0, 0)),
                                Part.makeBox(1, 1, 1, V(50, 0, 0))])
    a_adj = br.face_adjacency(apart)
    far = br.find_face_path(apart, 0, len(apart.Faces) - 1, adjacency=a_adj)
    tc.check("find_face_path: no route between faces of two separate solids",
              far is None, f"got {far}")

    # ── crossing points ─────────────────────────────────────────────────────
    ei = br.shared_edge_index(adj, top_i, side_i)
    tc.check("shared_edge_index: finds the edge joining two adjacent faces",
              ei is not None)
    tc.check("shared_edge_index: reports None for faces that do not touch",
              br.shared_edge_index(adj, top_i, bottom_i) is None)

    if ei is not None:
        edge = box.Edges[ei]
        pts = br.crossing_points(edge, samples=5)
        tc.check("crossing_points: returns the requested number of candidates",
                  len(pts) == 5, f"got {len(pts)}")
        tc.check("crossing_points: every candidate lies on the edge",
                  all(edge.distToShape(Part.Vertex(p))[0] < 1e-6 for p in pts))
        tc.check("crossing_points: the corners themselves are skipped — a "
                  "crossing there belongs to a third face too",
                  all(min(p.distanceToPoint(v.Point) for v in edge.Vertexes) > 1e-6
                      for p in pts))
        inset_pts = br.crossing_points(edge, samples=5, inset=3.0)
        tc.check("crossing_points: inset keeps candidates further from the ends",
                  all(min(p.distanceToPoint(v.Point) for v in edge.Vertexes)
                      >= min(q.distanceToPoint(v.Point) for v in edge.Vertexes)
                      for p, q in [(inset_pts[0], pts[0])]),
                  f"inset {inset_pts[0]} vs plain {pts[0]}")

    # ── routing across faces, end to end ────────────────────────────────────
    factory, cache = _plain_field_factory(box)

    start = V(8.0, 6.0, 10.0)      # on the top face
    goal_side = V(30.0, 0.0, 4.0)  # on the y=0 side wall
    segs = br.route_across_faces(box, top_i, start, side_i, goal_side, factory)
    tc.check("route_across_faces: routes from the top face onto a side wall",
              segs is not None and len(segs) == 2, f"got {segs}")

    if segs:
        tc.check("route_across_faces: one path per face, in order",
                  [s[0] for s in segs] == [top_i, side_i],
                  f"got {[s[0] for s in segs]}")
        tc.check("route_across_faces: every segment has at least two points",
                  all(len(pts) >= 2 for _f, pts in segs))

        frames = {f: cache[f][0] for f, _p in segs}
        # The critical property: consecutive segments must MEET. If the hand-off
        # at the shared edge were even slightly off, the baked trace would come
        # out as two disconnected pieces that merely look joined.
        a_frame = frames[segs[0][0]]
        b_frame = frames[segs[1][0]]
        end_a = a_frame.to_3d(*segs[0][1][-1])
        start_b = b_frame.to_3d(*segs[1][1][0])
        tc.check("route_across_faces: the two segments meet exactly at the "
                  "shared edge (a gap here would bake as a broken trace)",
                  end_a.distanceToPoint(start_b) < 1e-6,
                  f"gap of {end_a.distanceToPoint(start_b):.6f} mm")

        first_pt = a_frame.to_3d(*segs[0][1][0])
        last_pt = b_frame.to_3d(*segs[-1][1][-1])
        tc.check("route_across_faces: the route starts at the requested point",
                  first_pt.distanceToPoint(start) < 1e-3,
                  f"got {first_pt} for {start}")
        tc.check("route_across_faces: ...and ends at the requested point",
                  last_pt.distanceToPoint(goal_side) < 1e-3,
                  f"got {last_pt} for {goal_side}")

        length = br.segments_length(segs, frames)
        tc.check("segments_length: reports a plausible routed length "
                  "(at least the straight-line distance)",
                  length >= start.distanceToPoint(goal_side) - 1e-6,
                  f"routed {length:.3f} vs direct "
                  f"{start.distanceToPoint(goal_side):.3f}")

    # Top to bottom must cross three faces and still join up throughout.
    goal_bottom = V(30.0, 12.0, 0.0)
    segs3 = br.route_across_faces(box, top_i, start, bottom_i, goal_bottom, factory)
    tc.check("route_across_faces: reaches the opposite face by crossing a "
              "third one on the way",
              segs3 is not None and len(segs3) == 3, f"got {segs3}")
    if segs3:
        frames3 = {f: cache[f][0] for f, _p in segs3}
        gaps = []
        for (fa, pa), (fb, pb) in zip(segs3, segs3[1:]):
            end = frames3[fa].to_3d(*pa[-1])
            nxt = frames3[fb].to_3d(*pb[0])
            gaps.append(end.distanceToPoint(nxt))
        tc.check("route_across_faces: every hand-off across three faces is exact",
                  all(g < 1e-6 for g in gaps), f"gaps {gaps}")

    # A goal the planner cannot reach is reported, not faked.
    lonely = Part.makeBox(1, 1, 1, V(0, 0, 0))
    lonely_factory, _c = _plain_field_factory(lonely)
    tc.check("route_across_faces: an unknown face index reports None",
              br.route_across_faces(lonely, 0, V(0, 0, 0), 99, V(1, 1, 1),
                                     lonely_factory) is None)

    # ── crossings are squared up to the shared edge ─────────────────────────
    # Reported from real use as "gaps or kinks at the edges". Two segments on
    # adjacent faces meet at a POINT on the shared edge, each ending in a
    # rectangular cross-section lying in its OWN face's tangent plane. Only
    # when both arrive square to the edge do those rectangles coincide along
    # it; crossing at a shallow angle leaves a wedge-shaped notch on the
    # outside of the bend.
    def _joint_contact(shape, f_start, p_start, f_goal, p_goal, approach):
        fac, cch = _plain_field_factory(shape)
        segs = br.route_across_faces(shape, f_start, p_start, f_goal, p_goal,
                                      fac, approach_mm=approach)
        if not segs or len(segs) < 2:
            return None
        sols = [rf.build_surface_trace_solid(cch[fi][0], pts, 0.3, 0.035)
                for fi, pts in segs]
        return sum(f.Area for f in sols[0].common(sols[1]).Faces)

    # A deliberately shallow crossing: start far across the top, aim low down
    # the far end of a side wall. Measured on the planar/planar junction of a
    # plain box, where a boolean common() is a reliable contact metric.
    full = 0.3 * 0.035 * 2      # the cross-section, counted from both sides
    shallow_off = _joint_contact(box, top_i, V(4, 17, 10), side_i,
                                  V(36, 0, 2), 0.0)
    shallow_on = _joint_contact(box, top_i, V(4, 17, 10), side_i,
                                 V(36, 0, 2), 0.3)
    tc.check("crossings: a shallow crossing WITHOUT a squared-up approach "
              "leaves part of the joint open (the reported gap)",
              shallow_off is not None and shallow_off < full * 0.95,
              f"contact {shallow_off} of {full}")
    tc.check("crossings: squaring up to the edge closes the joint",
              shallow_on is not None and shallow_on >= full * 0.98,
              f"contact {shallow_on} of {full}")
    if shallow_off and shallow_on:
        tc.check("crossings: ...which is a real improvement, not noise",
                  shallow_on > shallow_off,
                  f"{shallow_on:.6f} vs {shallow_off:.6f}")

    # Squaring up must not break the easy case it was not needed for.
    perp = _joint_contact(box, top_i, V(20, 15, 10), side_i, V(20, 0, 4), 0.3)
    tc.check("crossings: an already-perpendicular crossing stays sound",
              perp is not None and perp >= full * 0.98, f"contact {perp}")

    # On a filleted body the segments still hand over exactly. (common() is
    # NOT a usable contact metric across a curved/planar junction — it comes
    # back empty even when the two solids meet perfectly — so this checks the
    # endpoints instead.)
    _fillet_base = Part.makeBox(40, 30, 12, V(0, 0, 0))
    filleted = _fillet_base.makeFillet(3.0, _fillet_base.Edges)
    f_fac, f_cache = _plain_field_factory(filleted)
    f_top = max(range(len(filleted.Faces)),
                 key=lambda i: (isinstance(filleted.Faces[i].Surface, Part.Plane),
                                 filleted.Faces[i].CenterOfMass.z,
                                 filleted.Faces[i].Area))
    f_side = next(i for i, f in enumerate(filleted.Faces)
                  if isinstance(f.Surface, Part.Plane) and f.normalAt(0, 0).y < -0.5)
    f_segs = br.route_across_faces(
        filleted, f_top, filleted.Faces[f_top].CenterOfMass,
        f_side, filleted.Faces[f_side].CenterOfMass, f_fac, approach_mm=0.3)
    tc.check("crossings: a filleted body routes over its narrow curved faces",
              f_segs is not None and len(f_segs) >= 2, f"got {f_segs}")
    if f_segs:
        gaps = []
        for (fa, pa), (fb, pb) in zip(f_segs, f_segs[1:]):
            end = f_cache[fa][0].path_to_3d(pa)[-1]
            nxt = f_cache[fb][0].path_to_3d(pb)[0]
            gaps.append(end.distanceToPoint(nxt))
        tc.check("crossings: every hand-off over a fillet is still exact",
                  all(g < 1e-6 for g in gaps), f"gaps {gaps}")

    # ── through the real document path, including the bake ──────────────────
    from _harness import new_document
    import routing.BodyRouteCommand as brc
    from wirebond.SetContactPointsOnFaceCommand import (
        _add_to_contact_points_group, _create_housing_marker,
        _next_housing_index)

    doc = new_document("TestBodyRouting")
    try:
        pkg = doc.addObject("Part::Feature", "Package")
        pkg.Shape = Part.makeBox(40, 20, 10, V(0, 0, 0))
        doc.recompute()
        FreeCAD.setActiveDocument(doc.Name)

        pad_top = _create_housing_marker(doc, pkg.Name, V(8, 6, 10),
                                          _next_housing_index(doc))
        pad_side = _create_housing_marker(doc, pkg.Name, V(30, 0, 4),
                                           _next_housing_index(doc))
        for m in (pad_top, pad_side):
            _add_to_contact_points_group(doc, m)
        doc.recompute()

        params = {"width_mm": 0.3, "thickness_mm": 0.035,
                   "clearance_mm": 0.2, "max_faces": 8}
        trace, message = brc.route_between(doc, pad_top, pad_side, params)
        tc.check("BodyRoute: connects a pad on the top face to one on a flank",
                  trace is not None, message)

        if trace is not None:
            tc.check("BodyRoute: the baked trace records every face it crosses",
                      len(list(trace.SourceFaceIndices)) == 2,
                      f"got {list(trace.SourceFaceIndices)}")
            tc.check("BodyRoute: the trace is a single valid solid, not a pile "
                      "of disconnected per-face fragments",
                      trace.Shape.isValid() and len(trace.Shape.Solids) == 1,
                      f"valid={trace.Shape.isValid()} "
                      f"solids={len(trace.Shape.Solids)}")
            tc.check("BodyRoute: the trace has volume",
                      trace.Shape.Volume > 0.0, f"got {trace.Shape.Volume}")

            wp = list(trace.Waypoints)
            tc.check("BodyRoute: the trace starts on the first pad",
                      (wp[0] - V(8, 6, 10)).Length < 0.01, f"got {wp[0]}")
            tc.check("BodyRoute: ...and ends on the second",
                      (wp[-1] - V(30, 0, 4)).Length < 0.01, f"got {wp[-1]}")
            off = [p for p in wp
                   if pkg.Shape.distToShape(Part.Vertex(p))[0] > 0.05]
            tc.check("BodyRoute: every waypoint lies on the body's surface — "
                      "the trace wraps around the edge rather than cutting "
                      "through the air",
                      not off, f"{len(off)} waypoint(s) off the surface")

        # A pair on ONE face still works through the same command, so this
        # tool is not only usable for the cross-face case.
        pad_top2 = _create_housing_marker(doc, pkg.Name, V(12, 14, 10),
                                           _next_housing_index(doc))
        _add_to_contact_points_group(doc, pad_top2)
        doc.recompute()
        t2, m2 = brc.route_between(doc, pad_top, pad_top2, params)
        tc.check("BodyRoute: a pair on the same face routes too (one segment)",
                  t2 is not None and len(list(t2.SourceFaceIndices)) == 1, m2)

        # Two pads on different BODIES is refused with an explanation rather
        # than silently routing over whichever body happens to be found.
        other = doc.addObject("Part::Feature", "Elsewhere")
        other.Shape = Part.makeBox(5, 5, 5, V(200, 200, 0))
        doc.recompute()
        far_pad = _create_housing_marker(doc, other.Name, V(202, 200, 2),
                                          _next_housing_index(doc))
        _add_to_contact_points_group(doc, far_pad)
        doc.recompute()
        t3, m3 = brc.route_between(doc, pad_top, far_pad, params)
        tc.check("BodyRoute: pads on two different bodies are refused with a "
                  "reason, not routed through thin air",
                  t3 is None and "body" in m3.lower(), f"got {m3!r}")
    finally:
        FreeCAD.closeDocument(doc.Name)

    return tc.results
