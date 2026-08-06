# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Exact copper outlines as routing obstacles.

core.trace_routing.collect_obstacles approximates every copper solid by its
axis-aligned bounding box. That is fine for compact pads and vias, but it is
badly wrong for the shape a PCB is mostly made of — a *diagonal* trace. On a
real board tested against this workbench, one ordinary diagonal copper run
produced a 7.19 x 10.34 mm keep-out box that swallowed two pads 2.5 mm apart,
making it impossible to route between them at all: not merely conservative,
fatal.

This module instead takes each copper solid's real outline in the routing
plane — the boundary of its horizontal face, discretised to a polygon — and
does segment-to-polygon distance tests against it. Clearance is applied as a
distance threshold rather than by offsetting the polygon, which avoids the
fragility (and cost) of a real 2D offset on hundreds of shapes.

Qt-free and document-free apart from collect_obstacle_polys(), which reads a
document once per routing session.
"""

import math
from collections import namedtuple, defaultdict

import FreeCAD

from core.trace_routing import _partdesign_body_of, expand_surface_exclusions

# pts: list of (x, y) in world XY, implicitly closed.
# bbox: (xmin, ymin, xmax, ymax) for cheap prefiltering.
Poly = namedtuple("Poly", ["pts", "bbox"])

# Faces flatter than this (|normal.z|) are treated as the horizontal face
# whose outline describes the copper footprint.
_HORIZONTAL_NZ = 0.7

# Same guard as core.trace_routing: anything this large is FreeCAD Origin
# construction geometry, never real copper.
_MAX_EXTENT_MM = 1.0e6


# ── polygon geometry ───────────────────────────────────────────────────────────

def point_in_poly(poly: Poly, x: float, y: float) -> bool:
    """Even-odd ray cast against the polygon's own points."""
    xmin, ymin, xmax, ymax = poly.bbox
    if x < xmin or x > xmax or y < ymin or y > ymax:
        return False
    pts = poly.pts
    n = len(pts)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > y) != (yj > y)) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _clip_into_bounds(x: float, y: float, bbox, pad: float):
    """
    Pull (x, y) back inside *bbox* = (xmin, ymin, xmax, ymax), inset by
    *pad* on every side — an axis-aligned approximation of "stay inside the
    boundary," used to keep a walk-around detour corner on the routable
    surface. Exact for the common case (a rectangular board/package face);
    for a non-rectangular boundary this can still return a point outside
    the true outline, which is why every corner is re-validated by
    path_blockers() before being accepted — this only widens what gets
    TRIED, it is never the final word on what is actually clear.
    """
    xmin, ymin, xmax, ymax = bbox
    lo_x, hi_x = xmin + pad, xmax - pad
    lo_y, hi_y = ymin + pad, ymax - pad
    if lo_x > hi_x:
        lo_x = hi_x = (xmin + xmax) / 2.0
    if lo_y > hi_y:
        lo_y = hi_y = (ymin + ymax) / 2.0
    return (min(max(x, lo_x), hi_x), min(max(y, lo_y), hi_y))


def _seg_seg_distance(ax, ay, bx, by, cx, cy, dx_, dy_) -> float:
    """Shortest distance between segments AB and CD (2D)."""
    def _pt_seg(px, py, x1, y1, x2, y2):
        vx, vy = x2 - x1, y2 - y1
        L2 = vx * vx + vy * vy
        if L2 < 1e-24:
            return math.hypot(px - x1, py - y1)
        t = ((px - x1) * vx + (py - y1) * vy) / L2
        t = max(0.0, min(1.0, t))
        return math.hypot(px - (x1 + t * vx), py - (y1 + t * vy))

    r_x, r_y = bx - ax, by - ay
    s_x, s_y = dx_ - cx, dy_ - cy
    denom = r_x * s_y - r_y * s_x
    if abs(denom) > 1e-18:
        t = ((cx - ax) * s_y - (cy - ay) * s_x) / denom
        u = ((cx - ax) * r_y - (cy - ay) * r_x) / denom
        if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
            return 0.0            # they cross
    return min(
        _pt_seg(ax, ay, cx, cy, dx_, dy_),
        _pt_seg(bx, by, cx, cy, dx_, dy_),
        _pt_seg(cx, cy, ax, ay, bx, by),
        _pt_seg(dx_, dy_, ax, ay, bx, by),
    )


def seg_poly_distance(p, q, poly: Poly) -> float:
    """
    Distance from segment [p, q] to *poly*; 0.0 when they touch, cross, or
    the segment lies inside the polygon.
    """
    if point_in_poly(poly, p.x, p.y) or point_in_poly(poly, q.x, q.y):
        return 0.0
    pts = poly.pts
    n = len(pts)
    best = float("inf")
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        d = _seg_seg_distance(p.x, p.y, q.x, q.y, x1, y1, x2, y2)
        if d < best:
            best = d
            if best <= 0.0:
                return 0.0
    return best


def seg_poly_edge_distance(p, q, poly: Poly) -> float:
    """
    Distance from segment [p, q] to the polygon's EDGES, ignoring whether the
    segment is inside or outside it.

    seg_poly_distance short-circuits to 0 when an endpoint is inside, which is
    what an obstacle wants but the opposite of what a containing BOUNDARY
    wants: there, being inside is normal and only nearness to the edge matters.
    """
    pts = poly.pts
    n = len(pts)
    best = float("inf")
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        d = _seg_seg_distance(p.x, p.y, q.x, q.y, x1, y1, x2, y2)
        if d < best:
            best = d
            if best <= 0.0:
                return 0.0
    return best


def poly_blocks(p, q, poly: Poly, clearance: float) -> bool:
    """True if [p, q] comes closer to *poly* than *clearance*."""
    xmin, ymin, xmax, ymax = poly.bbox
    sxmin, sxmax = (p.x, q.x) if p.x <= q.x else (q.x, p.x)
    symin, symax = (p.y, q.y) if p.y <= q.y else (q.y, p.y)
    if (sxmax < xmin - clearance or sxmin > xmax + clearance
            or symax < ymin - clearance or symin > ymax + clearance):
        return False
    return seg_poly_distance(p, q, poly) < clearance


# ── spatial index ──────────────────────────────────────────────────────────────

def _cell_of(x, y, cell):
    return (math.floor(x / cell), math.floor(y / cell))


def build_poly_index(polys, cell_size: float):
    """Bucket polygons by the cells their bbox spans — same technique as
    core.via_clustering, so a collision test only considers nearby copper."""
    cell = max(cell_size, 1e-6)
    index = defaultdict(list)
    for i, poly in enumerate(polys):
        xmin, ymin, xmax, ymax = poly.bbox
        cx0, cy0 = _cell_of(xmin, ymin, cell)
        cx1, cy1 = _cell_of(xmax, ymax, cell)
        # A pathologically large polygon would blow up this loop; such shapes
        # are filtered out at collection time (see _MAX_EXTENT_MM).
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                index[(cx, cy)].append(i)
    return index


def candidate_polys(p, q, index, cell_size: float, margin: float = 0.0):
    """Indices of polygons whose cells the segment passes through."""
    if index is None:
        return None
    cell = max(cell_size, 1e-6)
    xmin = min(p.x, q.x) - margin
    xmax = max(p.x, q.x) + margin
    ymin = min(p.y, q.y) - margin
    ymax = max(p.y, q.y) + margin
    cx0, cy0 = _cell_of(xmin, ymin, cell)
    cx1, cy1 = _cell_of(xmax, ymax, cell)
    out = set()
    for cx in range(cx0, cx1 + 1):
        for cy in range(cy0, cy1 + 1):
            out.update(index.get((cx, cy), ()))
    return out


def polys_containing(pt, polys, index=None, cell_size=None, clearance: float = 0.0):
    """Indices of polygons the point is inside — or within *clearance* of."""
    if index is not None and cell_size:
        cands = candidate_polys(pt, pt, index, cell_size, clearance)
    else:
        cands = range(len(polys))
    out = []
    for i in cands:
        poly = polys[i]
        if point_in_poly(poly, pt.x, pt.y):
            out.append(i)
        elif clearance > 0.0 and seg_poly_distance(pt, pt, poly) < clearance:
            out.append(i)
    return frozenset(out)


def convex_hull(pts):
    """Monotone-chain convex hull of 2D points, counter-clockwise."""
    pts = sorted(set((round(x, 9), round(y, 9)) for x, y in pts))
    if len(pts) <= 2:
        return list(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _offset_hull(hull, push: float, max_miter: float = 4.0):
    """
    Offset a convex hull outward by *push*, mitred at each vertex so the
    perpendicular distance to BOTH adjacent edges is at least *push*.

    The miter length grows without bound as a corner gets sharper, so it is
    capped at *max_miter* x push; a clipped corner just means a very slightly
    tighter detour around a spike, never a colliding one, because the
    resulting path is collision-checked anyway.
    """
    n = len(hull)
    if n < 3:
        xs = [p[0] for p in hull] or [0.0]
        ys = [p[1] for p in hull] or [0.0]
        x0, x1 = min(xs) - push, max(xs) + push
        y0, y1 = min(ys) - push, max(ys) + push
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    # Work counter-clockwise so the outward normal of edge (a -> b) is
    # (dy, -dx).
    area2 = sum(hull[i][0] * hull[(i + 1) % n][1] - hull[(i + 1) % n][0] * hull[i][1]
                for i in range(n))
    pts = list(hull) if area2 > 0 else list(reversed(hull))

    def _normal(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-12:
            return None
        return (dy / L, -dx / L)

    out = []
    for i in range(n):
        prev, cur, nxt = pts[i - 1], pts[i], pts[(i + 1) % n]
        n1 = _normal(prev, cur)
        n2 = _normal(cur, nxt)
        if n1 is None or n2 is None:
            continue
        bx, by = n1[0] + n2[0], n1[1] + n2[1]
        bl = math.hypot(bx, by)
        if bl < 1e-12:
            continue
        bx, by = bx / bl, by / bl
        cos_half = bx * n1[0] + by * n1[1]
        length = push / max(cos_half, 1e-6)
        length = min(length, push * max_miter)
        out.append((cur[0] + bx * length, cur[1] + by * length))
    return out


def inflate_poly(poly: Poly, delta: float) -> Poly:
    """
    *poly* grown outward by *delta* (its convex hull, mitre-offset — exact
    for the near-rectangular leg footprints this is used on).

    This is how a per-CLASS clearance is expressed in a PolyField that only
    knows one global clearance: to demand more space around TRACES than
    around other copper (the user-facing "minimum trace spacing" option),
    the trace polygons themselves are inflated by (spacing - clearance)
    before being added, so the single field clearance stays authoritative
    for everything else.
    """
    if delta <= 0:
        return poly
    hull = _offset_hull(convex_hull(poly.pts), delta)
    if len(hull) < 3:
        return poly
    xs = [p[0] for p in hull]
    ys = [p[1] for p in hull]
    return Poly(hull, (min(xs), min(ys), max(xs), max(ys)))


class PolyField:
    """
    The obstacle set a router walks around: exact copper outlines, with
    clearance applied as a distance threshold.

    Implements the small protocol core.trace_walkaround.walk_around needs —
    `blockers`, `containing`, `walk_points` — so the same walk-around logic
    runs against either these polygons or plain rectangles.
    """

    # Sentinel blocker index meaning "this leaves the routable surface".
    # Not a real obstacle, so it cannot be walked around — the face edge is
    # not something you can detour past.
    BOUNDARY = -1

    def __init__(self, polys, clearance: float, cell_size: float = 1.0,
                 boundary=None):
        self.polys = list(polys)
        self.clearance = clearance
        self.cell_size = max(cell_size, 1e-6)
        self.index = build_poly_index(self.polys, self.cell_size)
        self._hulls = {}
        # Optional outer boundary of the routable face. A trace must stay
        # inside it: on a 3-D body a face is trimmed, so without this a
        # detour could happily wander off the edge of the surface it is
        # supposed to be routed on.
        self.boundary = boundary

    def _on_surface(self, pt, tol: float = 0.0) -> bool:
        """
        True when *pt* counts as being on the routable face.

        Inside the outline, or — when *tol* is given — within that distance
        of it. The tolerant form exists because a point sitting EXACTLY on
        the boundary is genuinely ambiguous to an even-odd ray cast, and
        such points are not an edge case here: a trace crossing from one
        face of a body onto the next (see core.body_routing) hands over at a
        point lying precisely on the shared edge, and must be routable to
        and from it on both faces.
        """
        if point_in_poly(self.boundary, pt.x, pt.y):
            return True
        if tol <= 0.0:
            return False
        return seg_poly_edge_distance(pt, pt, self.boundary) <= tol

    def leaves_surface(self, p, q, edge_exempt: bool = False) -> bool:
        """
        True if [p, q] leaves the routable face, or hugs its edge closer
        than the clearance allows.

        *edge_exempt* mirrors blockers()' outright same-net exemption for
        copper, applied to the boundary instead: a pad placed near the edge
        of its own routable surface is an entirely ordinary situation — real
        boards do this constantly — and a trace must be allowed to leave
        such a pad. Without this, ANY leg starting or ending at a point
        already within clearance of the edge was rejected outright, with no
        obstacle involved at all: confirmed against a real board, where a
        contact point sitting close to its own routing surface's edge could
        never be routed from or to, at any clearance the boundary itself
        didn't also happen to tolerate.

        When exempt, an endpoint ON the outline counts as on the face (see
        _on_surface); a point genuinely away from the face is still refused,
        so this never becomes permission to route off the surface.
        """
        if self.boundary is None:
            return False
        tol = self.clearance if edge_exempt else 0.0
        if not (self._on_surface(p, tol) and self._on_surface(q, tol)):
            return True
        if edge_exempt:
            return False
        return seg_poly_edge_distance(p, q, self.boundary) < self.clearance

    def point_hugs_boundary(self, pt) -> bool:
        """True if *pt* lies within clearance of the face's outline — the
        boundary-edge analogue of containing(), used to decide whether a leg
        touching *pt* gets leaves_surface()'s edge_exempt. Deliberately a
        pure distance test, so a point exactly ON the outline qualifies."""
        if self.boundary is None:
            return False
        return seg_poly_edge_distance(pt, pt, self.boundary) < self.clearance

    def __len__(self):
        return len(self.polys)

    def add(self, poly: Poly) -> None:
        i = len(self.polys)
        self.polys.append(poly)
        xmin, ymin, xmax, ymax = poly.bbox
        cx0, cy0 = _cell_of(xmin, ymin, self.cell_size)
        cx1, cy1 = _cell_of(xmax, ymax, self.cell_size)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                self.index[(cx, cy)].append(i)

    def containing(self, pt) -> frozenset:
        """Polygons the point is inside of, or within clearance of — the set a
        leg is allowed a bounded escape from."""
        return polys_containing(pt, self.polys, self.index, self.cell_size,
                                self.clearance)

    def blockers(self, p, q, exempt=frozenset(), escape_mm: float = 0.0,
                 edge_exempt: bool = False) -> list:
        """
        Indices of polygons this segment violates.

        Copper containing an endpoint is exempt OUTRIGHT, not merely for a
        bounded escape distance: a trace starting on a pad is on that pad's
        net, and overlapping it is the whole point of connecting to it —
        exactly the same-net allowance KiCad's DRC makes. Charging that
        overlap as a violation is what made routing between two adjacent
        pads impossible, since each pad blocked its own trace.

        *edge_exempt* is the same idea applied to the routable-surface
        BOUNDARY instead of a copper polygon — see leaves_surface().

        *escape_mm* is accepted for interface compatibility with the
        rectangle field and is not needed here: with exact outlines the
        exempt shape is the real pad, not a bounding box that might swallow
        half the board.
        """
        hits = []
        if self.leaves_surface(p, q, edge_exempt):
            hits.append(self.BOUNDARY)
        cands = candidate_polys(p, q, self.index, self.cell_size, self.clearance)
        for i in cands:
            if i in exempt:
                continue
            if poly_blocks(p, q, self.polys[i], self.clearance):
                hits.append(i)
        return hits

    def walk_points(self, i: int, margin: float, z: float) -> list:
        """
        Waypoints to route via when walking around polygon *i*: its convex
        hull offset outward by clearance + margin. The hull (not the bounding
        box) is what keeps a detour around a long diagonal trace tight.

        The offset is a proper MITER along the two edge normals meeting at
        each vertex, not a radial push from the centroid. A radial push does
        not preserve the perpendicular distance to the edges — on a tall thin
        shape a corner ends up nearer the long side than the clearance
        demands, so every detour through it fails its own collision check and
        the walk-around reports "blocked" for an obstacle it could plainly
        have gone around.

        When a routable-surface boundary is set, each offset corner is also
        pulled back inside it (see _clip_into_bounds): copper sitting close
        to the edge of the surface offsets a corner PAST that edge, and
        without this the walk-around's own BOUNDARY sentinel (see blockers())
        rejects that corner outright with no fallback — "leaves the surface"
        is treated as unconditionally unroutable there, by design, since the
        interactive router's response is a human nudging the cursor
        elsewhere. Batch routing has no cursor to nudge, so a copper run
        merely running close to the board edge — an ordinary, common
        situation, not a contrived one — silently failed every pair whose
        detour needed that corner, confirmed against a real board. Pulling
        the corner back inside the boundary here does not weaken collision
        safety: every corner returned still goes through the same
        path_blockers() check as any other candidate before being accepted.
        """
        key = (i, round(margin, 6))
        hull = self._hulls.get(key)
        if hull is None:
            hull = _offset_hull(convex_hull(self.polys[i].pts),
                                self.clearance + max(margin, 1e-3))
            if self.boundary is not None:
                hull = [_clip_into_bounds(x, y, self.boundary.bbox, self.clearance)
                        for x, y in hull]
            self._hulls[key] = hull
        return [FreeCAD.Vector(x, y, z) for x, y in hull]


# ── collection from the document ───────────────────────────────────────────────

def _outline_of_solid(solid, deflection: float):
    """
    The copper footprint of one solid as an XY polygon: the outer boundary of
    its largest near-horizontal face, discretised. Falls back to the solid's
    bounding rectangle when it has no usable horizontal face (which is the
    old bounding-box behaviour, but only for shapes where nothing better
    exists).
    """
    best = None
    best_area = 0.0
    try:
        for f in solid.Faces:
            try:
                n = f.normalAt(0, 0)
            except Exception:
                continue
            if abs(n.z) < _HORIZONTAL_NZ:
                continue
            a = f.Area
            if a > best_area:
                best_area = a
                best = f
    except Exception:
        best = None

    if best is not None:
        try:
            wire = max(best.Wires, key=lambda w: w.BoundBox.XLength * w.BoundBox.YLength)
            pts = [(v.x, v.y) for v in wire.discretize(Deflection=deflection)]
            if len(pts) >= 3:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                return Poly(pts, (min(xs), min(ys), max(xs), max(ys)))
        except Exception:
            pass

    bb = solid.BoundBox
    pts = [(bb.XMin, bb.YMin), (bb.XMax, bb.YMin), (bb.XMax, bb.YMax), (bb.XMin, bb.YMax)]
    return Poly(pts, (bb.XMin, bb.YMin, bb.XMax, bb.YMax))


def collect_obstacle_polys(doc, exclude_names, z_min: float, z_max: float,
                           deflection: float = 0.05) -> list:
    """
    One outline polygon per copper solid whose Z range overlaps
    [z_min, z_max].

    Mirrors core.trace_routing.collect_obstacles' filtering — physical bodies
    only (Part::Feature / Mesh::Feature, so FreeCAD Origin datum planes with
    their ~1e100 mm boxes are excluded), skipping contact-point and grid
    marker helpers, and decomposing each object into its individual solids so
    a whole copper layer does not become one board-sized keep-out.
    """
    polys = []
    if doc is None:
        return polys
    exclude = set(exclude_names or ())
    for o in doc.Objects:
        if o.Name in exclude:
            continue
        try:
            is_body = o.isDerivedFrom("Part::Feature") or o.isDerivedFrom("Mesh::Feature")
        except Exception:
            is_body = False
        if not is_body:
            continue
        if getattr(o, "IsContactPoint", False):
            continue
        if getattr(o, "IsGridPoint", False) or getattr(o, "IsRoutingGridPoint", False):
            continue
        shp = getattr(o, "Shape", None)
        if shp is None or shp.isNull():
            continue
        try:
            obb = shp.BoundBox
        except Exception:
            continue
        if (not obb.isValid() or obb.ZMax < z_min or obb.ZMin > z_max
                or obb.XLength > _MAX_EXTENT_MM or obb.YLength > _MAX_EXTENT_MM):
            continue

        subs = list(shp.Solids) or list(shp.Faces) or [shp]
        for sub in subs:
            try:
                bb = sub.BoundBox
            except Exception:
                continue
            if not bb.isValid() or bb.ZMax < z_min or bb.ZMin > z_max:
                continue
            if bb.XLength > _MAX_EXTENT_MM or bb.YLength > _MAX_EXTENT_MM:
                continue
            polys.append(_outline_of_solid(sub, deflection))
    return polys


def _wire_to_frame_poly(wire, frame, deflection: float):
    """Discretise a wire and map it into the routing frame's 2-D space,
    chaining the periodic-seam reference from point to point so an outline
    that straddles a cylinder's seam stays in one piece."""
    try:
        verts = wire.discretize(Deflection=deflection)
    except Exception:
        return None
    pts = []
    near = None
    for v in verts:
        xy = frame.to_2d(v, near=near)
        if xy is None:
            continue
        pts.append(xy)
        near = xy
    if len(pts) < 3:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return Poly(pts, (min(xs), min(ys), max(xs), max(ys)))


def _outline_in_frame(sub, frame, deflection: float):
    """
    The footprint of one solid in the routing frame: the outline of whichever
    of its faces lies most nearly parallel to the routing surface.

    Generalises _outline_of_solid's "largest near-horizontal face" rule — the
    reference direction is the routing surface's own normal rather than world
    +Z, so this works on a side wall or a curved flank exactly as it does on
    a board's top face.
    """
    best = None
    best_area = 0.0
    try:
        for f in sub.Faces:
            try:
                c = f.CenterOfMass
                n = f.normalAt(0, 0)
            except Exception:
                continue
            xy = frame.to_2d(c)
            if xy is None:
                continue
            ref = frame.normal_at(*xy)
            if abs(n.dot(ref)) < _HORIZONTAL_NZ:
                continue
            if f.Area > best_area:
                best_area = f.Area
                best = f
    except Exception:
        best = None

    if best is not None:
        try:
            wire = max(best.Wires,
                       key=lambda w: w.BoundBox.XLength * w.BoundBox.YLength)
            poly = _wire_to_frame_poly(wire, frame, deflection)
            if poly is not None:
                return poly
        except Exception:
            pass

    # Fallback: the convex outline of the solid's vertices in frame space.
    # Still far tighter than a world-axis bounding box for a diagonal run.
    pts = []
    near = None
    try:
        for v in sub.Vertexes:
            xy = frame.to_2d(v.Point, near=near)
            if xy is None:
                continue
            pts.append(xy)
            near = xy
    except Exception:
        pass
    if len(pts) < 3:
        return None
    hull = convex_hull(pts)
    if len(hull) < 3:
        return None
    xs = [p[0] for p in hull]
    ys = [p[1] for p in hull]
    return Poly(hull, (min(xs), min(ys), max(xs), max(ys)))


def _all_horizontal_face_polys(sub, frame, deflection: float) -> list:
    """
    Every near-horizontal face of *sub* as its own Poly, instead of
    _outline_in_frame's single "keep only the largest" rule.

    A BENT trace solid (built by fusing straight per-leg boxes at the
    corner) commonly ends up with one near-horizontal face PER LEG rather
    than one continuous face for the whole path — the boolean fuse does not
    merge faces across the bend. _outline_in_frame's "biggest face wins"
    rule was written for ordinary copper (usually already a single face)
    and silently drops every leg but the largest for a bent trace. That is
    exactly wrong when the caller wants this trace's OWN footprint as a
    routing obstacle for later pairs in the same batch: confirmed against a
    real board, the dropped leg was invisible to the next pair's route,
    which then crossed straight through it.
    """
    polys = []
    try:
        faces = sub.Faces
    except Exception:
        return polys
    for f in faces:
        try:
            c = f.CenterOfMass
            n = f.normalAt(0, 0)
        except Exception:
            continue
        xy = frame.to_2d(c)
        if xy is None:
            continue
        ref = frame.normal_at(*xy)
        if abs(n.dot(ref)) < _HORIZONTAL_NZ:
            continue
        try:
            wire = max(f.Wires, key=lambda w: w.BoundBox.XLength * w.BoundBox.YLength)
            poly = _wire_to_frame_poly(wire, frame, deflection)
            if poly is not None:
                polys.append(poly)
        except Exception:
            continue
    return polys


def collect_obstacle_polys_on_frame(doc, exclude_names, frame, band_mm: float,
                                    deflection: float = 0.05) -> list:
    """
    Copper outlines in a routing frame's 2-D space (see core.routing_frame).

    Same filtering as collect_obstacle_polys — physical bodies only, no
    construction geometry, decomposed per solid — but "near the routing
    surface" is a distance measured off the SURFACE rather than a world-Z
    band, so it is meaningful on a side wall or a curved flank too.

    PartDesign handling: features INSIDE a Body are skipped (they are
    history states of the Body's one solid, not separate copper — an
    intermediate Pad state can even be a SUPERSET of the final shape after
    a Pocket); the Body itself represents them all. Exclusions are expanded
    the same way (see expand_surface_exclusions), so picking either the
    Body or one of its features as the routing surface excludes the whole
    family.
    """
    polys = []
    seen = set()
    if doc is None or frame is None:
        return polys
    exclude = expand_surface_exclusions(doc, exclude_names)
    for o in doc.Objects:
        if o.Name in exclude:
            continue
        if _partdesign_body_of(o) is not None:
            continue        # history state inside a Body — the Body stands for it
        try:
            is_body = o.isDerivedFrom("Part::Feature") or o.isDerivedFrom("Mesh::Feature")
        except Exception:
            is_body = False
        if not is_body:
            continue
        if getattr(o, "IsContactPoint", False):
            continue
        if getattr(o, "IsGridPoint", False) or getattr(o, "IsRoutingGridPoint", False):
            continue
        shp = getattr(o, "Shape", None)
        if shp is None or shp.isNull():
            continue
        try:
            obb = shp.BoundBox
        except Exception:
            continue
        if (not obb.isValid()
                or obb.XLength > _MAX_EXTENT_MM or obb.YLength > _MAX_EXTENT_MM):
            continue

        for sub in (list(shp.Solids) or list(shp.Faces) or [shp]):
            try:
                bb = sub.BoundBox
            except Exception:
                continue
            if not bb.isValid() or bb.XLength > _MAX_EXTENT_MM:
                continue
            # Near the routing surface? Cheapest sufficient test: the closest
            # vertex of the solid.
            try:
                dmin = min(frame.distance_to_surface(v.Point) for v in sub.Vertexes)
            except Exception:
                continue
            if dmin > band_mm:
                continue
            _solid_footprint_polys(sub, frame, deflection, seen, polys)
    return polys


def _solid_footprint_polys(sub, frame, deflection, seen_bboxes, out) -> None:
    """
    Append every near-horizontal-face footprint of one solid to *out*,
    deduped by rounded bbox via *seen_bboxes* (top and bottom faces of the
    same leg project identically — keep one).

    One poly per FACE, not per solid: a bent trace solid carries one
    near-horizontal face PER LEG (the boolean fuse never merges them), and
    the old "largest face wins" rule silently dropped every leg but the
    biggest. That exact omission produced real crossings twice — first
    within one batch session (fixed in outline_polys_of_object_on_frame),
    then AGAIN for traces from an EARLIER session picked up by
    collect_obstacle_polys_on_frame's document scan, which still used the
    single-face rule. This shared helper is both code paths now, so they
    cannot drift apart a third time.
    """
    faces = _all_horizontal_face_polys(sub, frame, deflection)
    if not faces:
        # No near-horizontal face at all (e.g. a purely vertical/curved
        # surface trace) — fall back to _outline_in_frame's own
        # convex-hull-of-vertices path.
        poly = _outline_in_frame(sub, frame, deflection)
        faces = [poly] if poly is not None else []
    for poly in faces:
        key = tuple(round(v, 4) for v in poly.bbox)
        if key in seen_bboxes:
            continue
        seen_bboxes.add(key)
        out.append(poly)


def outline_polys_of_object_on_frame(obj, frame, deflection: float = 0.05) -> list:
    """
    Public wrapper for a single already-known object's footprint: one Poly
    per near-horizontal face per solid (see _solid_footprint_polys — NOT
    one poly per solid, which would silently drop every leg but the
    largest for a bent multi-segment trace), with no "is this near the
    surface" or document-scan filtering — the caller already knows *obj*
    belongs on *frame* (e.g. a trace just baked onto it) and wants its
    footprint folded into a live PolyField via .add(), the same
    obstacle-growth step core.trace_routing.TraceRoutingSession.confirm_trace
    does for its own Rect-based graph.
    """
    polys = []
    seen_bboxes = set()
    shp = getattr(obj, "Shape", None)
    if shp is None or shp.isNull():
        return polys
    for sub in (list(shp.Solids) or list(shp.Faces) or [shp]):
        _solid_footprint_polys(sub, frame, deflection, seen_bboxes, polys)
    return polys


def face_outer_poly_on_frame(face, frame, deflection: float = 0.05):
    """
    The face's OUTER boundary in the routing frame — the region a trace must
    stay inside.

    A face on a 3-D body is trimmed, so its parameter rectangle is larger
    than the face itself; without this a walk-around detour could route off
    the edge of the surface entirely and still look valid in 2-D.
    """
    try:
        wires = list(face.Wires)
    except Exception:
        return None
    if not wires:
        return None
    outer = max(wires, key=lambda w: w.BoundBox.XLength * w.BoundBox.YLength)
    return _wire_to_frame_poly(outer, frame, deflection)


def face_hole_polys_on_frame(face, frame, deflection: float = 0.05) -> list:
    """Cutouts in the routing surface, in the frame's 2-D space."""
    polys = []
    try:
        wires = list(face.Wires)
    except Exception:
        return polys
    if len(wires) <= 1:
        return polys
    outer = max(range(len(wires)),
                key=lambda i: wires[i].BoundBox.XLength * wires[i].BoundBox.YLength)
    for i, w in enumerate(wires):
        if i == outer:
            continue
        poly = _wire_to_frame_poly(w, frame, deflection)
        if poly is not None:
            polys.append(poly)
    return polys


def face_hole_polys(face, deflection: float = 0.05) -> list:
    """
    Outline polygons for the HOLES of the routing surface — cutouts are an
    absence of routable copper, so a trace must not cross one even though no
    body sits there. The outer boundary is excluded: it delimits the routable
    region rather than blocking it.
    """
    polys = []
    try:
        wires = list(face.Wires)
    except Exception:
        return polys
    if len(wires) <= 1:
        return polys
    outer = max(range(len(wires)),
                key=lambda i: wires[i].BoundBox.XLength * wires[i].BoundBox.YLength)
    for i, w in enumerate(wires):
        if i == outer:
            continue
        try:
            pts = [(v.x, v.y) for v in w.discretize(Deflection=deflection)]
        except Exception:
            continue
        if len(pts) < 3:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        polys.append(Poly(pts, (min(xs), min(ys), max(xs), max(ys))))
    return polys
