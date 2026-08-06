# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Routing ACROSS the faces of a 3-D body — a trace that starts on one face,
crosses a shared edge, and carries on over the next one.

Routing on a single face already works anywhere in this workbench, on flat,
slanted and curved faces alike, because core.routing_frame.SurfaceFrame maps
any face to a metric 2-D space. What it cannot do is leave that face: the
routable-surface boundary stops a trace at the edge, by design. This module
adds the missing piece — connecting pads that sit on DIFFERENT faces of the
same body, for example a pad on top of a package and one down its flank.

The approach, deliberately built out of parts that already exist and are
tested rather than a new 3-D search:

  1. Work out which faces touch which (face_adjacency), from the body's own
     topology — two faces are neighbours when they share an edge.
  2. Find a sequence of faces from the start face to the goal face
     (find_face_path), weighted by real distance so the route does not take
     a silly way round.
  3. For each hop, pick a point on the shared edge to cross at
     (crossing_points), then route WITHIN each face from where the trace
     entered to where it leaves, using exactly the same walk-around the
     single-face routers use.
  4. Hand back one 2-D path per face, which the caller bakes into a single
     trace solid.

Crossing points sit exactly ON a face's boundary, which the boundary check
would normally reject as "leaves the surface". It does not, because
core.trace_obstacles already exempts a path's own endpoints when they hug
the boundary — the rule added so that a pad near the edge of its board could
still be routed from. That exemption is what makes edge-to-edge routing
possible at all.

Qt-free and document-free: obstacles arrive through a *field_for_face*
callback supplied by the caller, so the whole planner is covered by headless
tests.
"""

import heapq
import math

import FreeCAD
import Part

import core.trace_walkaround as trace_walkaround

# How many points along a shared edge are considered as crossing candidates.
DEFAULT_EDGE_SAMPLES = 9
# Upper bound on how many faces a single trace may cross, so a pathological
# body cannot turn one route into an unbounded search.
DEFAULT_MAX_FACES = 8


# ── topology ─────────────────────────────────────────────────────────────────

def _face_index(shape, face):
    for i, f in enumerate(shape.Faces):
        if f.isSame(face):
            return i
    return None


def face_adjacency(shape):
    """
    {face_index: [(neighbour_index, edge_index), ...]} for *shape*.

    Two faces are neighbours when they share an edge, taken from the body's
    real topology (Shape.ancestorsOfType) rather than by comparing geometry,
    so it is exact on curved bodies too — a cylinder's flank correctly comes
    out adjacent to both its caps.
    """
    adj = {}
    if shape is None:
        return adj
    try:
        edges = shape.Edges
    except Exception:
        return adj
    for ei, edge in enumerate(edges):
        try:
            owners = shape.ancestorsOfType(edge, Part.Face)
        except Exception:
            continue
        if len(owners) != 2:
            continue        # a free or seam edge — not a crossing opportunity
        a = _face_index(shape, owners[0])
        b = _face_index(shape, owners[1])
        if a is None or b is None or a == b:
            continue
        adj.setdefault(a, []).append((b, ei))
        adj.setdefault(b, []).append((a, ei))
    return adj


def _face_center(shape, idx):
    try:
        return shape.Faces[idx].CenterOfMass
    except Exception:
        return FreeCAD.Vector(0, 0, 0)


def _edge_mid(shape, edge_index):
    try:
        e = shape.Edges[edge_index]
        return e.valueAt((e.FirstParameter + e.LastParameter) / 2.0)
    except Exception:
        return None


def find_face_path(shape, start_face: int, goal_face: int, adjacency=None,
                   max_faces: int = DEFAULT_MAX_FACES,
                   start_pt=None, goal_pt=None, exclude_faces=None):
    """
    A sequence of face indices from *start_face* to *goal_face*, or None.

    Dijkstra over the CROSSINGS rather than over the faces: a state is
    "arrived on this face over that edge", and a step costs the real
    distance from one crossing to the next. That is much closer to the
    length the trace will actually have than a hop count or a distance
    between face centres — a centre-to-centre cost cannot tell a long thin
    face entered and left at the same end from one crossed end to end, so
    it happily picks a face sequence that is far from the shortest.

    With *start_pt* / *goal_pt* the first and last legs are measured from
    the real pad positions too. Without them it falls back to face centres,
    which keeps the function usable (and its tests meaningful) for callers
    that only care about which faces are reachable.
    """
    if shape is None:
        return None
    if start_face == goal_face:
        return [start_face]
    adj = face_adjacency(shape) if adjacency is None else adjacency
    if start_face not in adj or goal_face not in adj:
        return None

    origin = (FreeCAD.Vector(start_pt) if start_pt is not None
              else _face_center(shape, start_face))
    target = (FreeCAD.Vector(goal_pt) if goal_pt is not None
              else _face_center(shape, goal_face))

    # Faces to route around — used to look for an ALTERNATIVE way when the
    # shortest one turns out to be blocked by copper (see route_across_faces).
    banned = set(exclude_faces or ())
    banned.discard(start_face)
    banned.discard(goal_face)

    # State: (face just arrived on, edge crossed to get there). Seeded with
    # every way of leaving the start face.
    heap = []
    best = {}
    prev = {}
    for nb, ei in adj.get(start_face, ()):
        if nb in banned:
            continue
        mid = _edge_mid(shape, ei)
        if mid is None:
            continue
        cost = origin.distanceToPoint(mid)
        state = (nb, ei)
        if cost < best.get(state, float("inf")):
            best[state] = cost
            prev[state] = None
            heapq.heappush(heap, (cost, nb, ei))

    goal_state = None
    seen = set()
    while heap:
        d, face, via = heapq.heappop(heap)
        state = (face, via)
        if state in seen:
            continue
        seen.add(state)

        if face == goal_face:
            mid = _edge_mid(shape, via)
            total = d + (target.distanceToPoint(mid) if mid else 0.0)
            if goal_state is None or total < goal_state[0]:
                goal_state = (total, state)
            # Keep going: another arrival on the goal face may be shorter.
            continue

        depth = 1
        walk = state
        while prev.get(walk) is not None:
            walk = prev[walk]
            depth += 1
        if depth + 1 >= max_faces:
            continue

        here = _edge_mid(shape, via)
        if here is None:
            continue
        for nb, ei in adj.get(face, ()):
            if ei == via or nb in banned:
                continue
            nxt = _edge_mid(shape, ei)
            if nxt is None:
                continue
            nd = d + here.distanceToPoint(nxt)
            nstate = (nb, ei)
            if nd < best.get(nstate, float("inf")):
                best[nstate] = nd
                prev[nstate] = state
                heapq.heappush(heap, (nd, nb, ei))

    if goal_state is None:
        return None

    faces = []
    state = goal_state[1]
    while state is not None:
        faces.append(state[0])
        state = prev.get(state)
    faces.append(start_face)
    faces.reverse()

    # Collapse any repeat of the same face in a row (possible when two edges
    # of one face are used back to back).
    collapsed = []
    for f in faces:
        if not collapsed or collapsed[-1] != f:
            collapsed.append(f)
    return collapsed if len(collapsed) <= max_faces else None


def shared_edge_index(adjacency, face_a: int, face_b: int):
    """The index of an edge joining the two faces, or None."""
    for nb, ei in adjacency.get(face_a, ()):
        if nb == face_b:
            return ei
    return None


def crossing_points(edge, samples: int = DEFAULT_EDGE_SAMPLES,
                    inset: float = 0.0):
    """
    Candidate points to cross at, spread along *edge*.

    The ends are skipped (a crossing exactly at a corner belongs to a third
    face as well and tends to fail both faces' boundary checks), and *inset*
    pulls the range further in from the corners when a trace needs room to
    turn after landing.
    """
    pts = []
    n = max(int(samples), 1)
    try:
        p0, p1 = edge.FirstParameter, edge.LastParameter
        length = edge.Length
    except Exception:
        return pts
    if p1 <= p0:
        return pts
    frac = 0.0
    if length > 0.0 and inset > 0.0:
        frac = min(0.45, inset / length)
    lo = p0 + (p1 - p0) * frac
    hi = p1 - (p1 - p0) * frac
    for i in range(n):
        t = (i + 1.0) / (n + 1.0)
        try:
            pts.append(edge.valueAt(lo + (hi - lo) * t))
        except Exception:
            continue
    return pts


# ── the planner ──────────────────────────────────────────────────────────────

def edge_tangent_at(edge, point):
    """Direction of *edge* at *point*, or None."""
    try:
        return edge.tangentAt(edge.Curve.parameter(point))
    except Exception:
        pass
    try:                       # fall back to a finite difference
        u = edge.Curve.parameter(point)
        du = (edge.LastParameter - edge.FirstParameter) * 1e-4 or 1e-6
        return edge.valueAt(u + du) - edge.valueAt(u - du)
    except Exception:
        return None


def _inside_boundary(field, pt2) -> bool:
    boundary = getattr(field, "boundary", None)
    if boundary is None:
        return True
    try:
        from core.trace_obstacles import point_in_poly
        return point_in_poly(boundary, pt2[0], pt2[1])
    except Exception:
        return True


def perpendicular_approach(frame, field, cross3, tangent3, dist: float):
    """
    (approach_point_2d, crossing_point_2d) — a point just inside the face,
    offset from the crossing at right angles to the shared edge.

    Routing the last stretch perpendicular to the edge is what makes the two
    faces' segments MEET properly. Each segment ends in a rectangular
    cross-section lying in its own face's tangent plane; only when both
    arrive square to the edge do those two rectangles coincide along it.
    Crossing at a shallow angle instead leaves a wedge-shaped notch on the
    outside of the bend — measured at roughly 20% of the cross-section
    missing on a plain box, and worse on a complex part, which is exactly
    the "gaps and kinks at the edges" this avoids.

    The offset is shortened, and its direction flipped, as needed to stay on
    the face, so a narrow face still gets the best approach that fits.
    """
    c2 = frame.to_2d(cross3)
    if c2 is None or tangent3 is None:
        return None, c2
    step = max(dist, 1e-4) * 0.5
    try:
        q3 = FreeCAD.Vector(cross3) + FreeCAD.Vector(tangent3).normalize().multiply(step)
    except Exception:
        return None, c2
    q2 = frame.to_2d(q3, near=c2)
    if q2 is None:
        return None, c2
    ex, ey = q2[0] - c2[0], q2[1] - c2[1]
    L = math.hypot(ex, ey)
    if L < 1e-12:
        return None, c2
    ex, ey = ex / L, ey / L
    for sign in (1.0, -1.0):
        for scale in (1.0, 0.6, 0.35, 0.2):
            d = dist * scale
            cand = (c2[0] - ey * sign * d, c2[1] + ex * sign * d)
            if _inside_boundary(field, cand):
                return cand, c2
    return None, c2


def _route_2d(field, p2, q2, step_deg):
    """The walk-around between two points already in one face's 2-D space."""
    if math.hypot(q2[0] - p2[0], q2[1] - p2[1]) < 1e-9:
        return [tuple(p2)]
    path, _flip = trace_walkaround.route_head(
        FreeCAD.Vector(p2[0], p2[1], 0.0),
        FreeCAD.Vector(q2[0], q2[1], 0.0),
        field, step_deg=step_deg)
    if path is None:
        return None
    return [(p.x, p.y) for p in path]


def _dedupe(pts, tol: float = 1e-9):
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append(tuple(p))
    return out


def _face_path(frame, field, entry3, entry_tan, exit3, exit_tan,
               approach: float, step_deg):
    """
    The 2-D path across ONE face, from where the trace enters to where it
    leaves, squaring up to the shared edge at either end that is a crossing.
    """
    a_app, a2 = (perpendicular_approach(frame, field, entry3, entry_tan, approach)
                 if entry_tan is not None else (None, frame.to_2d(entry3)))
    if a2 is None:
        return None
    b_app, b2 = (perpendicular_approach(frame, field, exit3, exit_tan, approach)
                 if exit_tan is not None else (None, frame.to_2d(exit3, near=a2)))
    if b2 is None:
        return None

    mid = _route_2d(field, a_app or a2, b_app or b2, step_deg)
    if mid is None:
        return None

    pts = []
    if a_app is not None:
        pts.append(a2)          # start exactly on the edge, then square away
    pts.extend(mid)
    if b_app is not None:
        pts.append(b2)          # ...and come back square onto the next edge
    pts = _dedupe(pts)
    return pts if len(pts) >= 2 else None


def route_across_faces(shape, start_face: int, start_pt, goal_face: int,
                       goal_pt, field_for_face, step_deg: float = 45.0,
                       samples: int = DEFAULT_EDGE_SAMPLES,
                       max_faces: int = DEFAULT_MAX_FACES,
                       candidates_per_hop: int = 4,
                       approach_mm: float = 0.3):
    """
    Route from *start_pt* on *start_face* to *goal_pt* on *goal_face*,
    crossing faces as needed.

    *field_for_face(face_index)* must return (frame, field) for that face —
    the caller owns obstacle collection, which keeps this function free of
    any document dependency.

    Returns [(face_index, [(u, v), ...]), ...] — one 2-D path per face, in
    order — or None when no route was found.

    Crossing points are tried best-first by how much progress they make
    toward the goal; several are tried per hop, because the geometrically
    obvious crossing is often the one blocked by copper.
    """
    if shape is None:
        return None
    adj = face_adjacency(shape)
    first = find_face_path(shape, start_face, goal_face, adjacency=adj,
                            max_faces=max_faces, start_pt=start_pt,
                            goal_pt=goal_pt)
    if not first:
        return None

    # The shortest way round may be blocked by copper that is already there —
    # on a board being wired up one connection at a time, that is the normal
    # case rather than the exception. When the shortest sequence fails, look
    # for another by routing AROUND each of its intermediate faces in turn,
    # shortest alternative first. Without this a pair is reported unroutable
    # while a perfectly good longer way exists.
    attempts = [first]
    for banned_face in first[1:-1]:
        alt = find_face_path(shape, start_face, goal_face, adjacency=adj,
                             max_faces=max_faces, start_pt=start_pt,
                             goal_pt=goal_pt, exclude_faces={banned_face})
        if alt and alt not in attempts:
            attempts.append(alt)

    for faces in attempts:
        segments = _walk_faces(shape, adj, faces, start_pt, goal_pt,
                                field_for_face, step_deg, samples, max_faces,
                                candidates_per_hop, approach_mm)
        if segments:
            return segments
    return None


def _walk_faces(shape, adj, faces, start_pt, goal_pt, field_for_face,
                step_deg, samples, max_faces, candidates_per_hop, approach_mm):
    """Route along one already-chosen sequence of faces, or None."""
    segments = []
    current_pt = FreeCAD.Vector(start_pt)
    # Tangent of the edge the trace ARRIVED over, so the next face can square
    # away from it just as the previous one squared up to it.
    current_tan = None

    for hop, face_idx in enumerate(faces):
        try:
            frame, field = field_for_face(face_idx)
        except Exception:
            return None
        if frame is None or field is None:
            return None

        last = (hop == len(faces) - 1)
        if last:
            path2d = _face_path(frame, field, current_pt, current_tan,
                                 FreeCAD.Vector(goal_pt), None,
                                 approach_mm, step_deg)
            if path2d is None:
                return None
            segments.append((face_idx, path2d))
            return segments

        nxt = faces[hop + 1]
        ei = shared_edge_index(adj, face_idx, nxt)
        if ei is None:
            return None
        try:
            edge = shape.Edges[ei]
        except Exception:
            return None

        goal_v = FreeCAD.Vector(goal_pt)
        cands = crossing_points(edge, samples)
        # Best first: the crossing that most shortens the remaining journey.
        cands.sort(key=lambda p: (current_pt.distanceToPoint(p)
                                   + p.distanceToPoint(goal_v)))

        chosen = None
        for cand in cands[:max(1, candidates_per_hop)]:
            tan = edge_tangent_at(edge, cand)
            path2d = _face_path(frame, field, current_pt, current_tan,
                                 cand, tan, approach_mm, step_deg)
            if path2d is not None:
                chosen = (cand, tan, path2d)
                break
        if chosen is None:
            return None
        cand, tan, path2d = chosen
        segments.append((face_idx, path2d))
        current_pt = cand
        current_tan = tan

    return segments


def segments_length(segments, frames) -> float:
    """Total routed length, for reporting. *frames* maps face index -> frame."""
    total = 0.0
    for face_idx, pts in segments:
        frame = frames.get(face_idx)
        if frame is None:
            continue
        prev = None
        for xy in pts:
            try:
                p = frame.to_3d(xy[0], xy[1])
            except Exception:
                continue
            if prev is not None:
                total += prev.distanceToPoint(p)
            prev = p
    return total
