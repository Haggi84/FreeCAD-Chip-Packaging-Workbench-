# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Semi-automated point-to-point conductor-trace routing.

Given a sampled grid of candidate points on a routing surface (e.g. the top
face of an imported PCB — see sample_face_grid(), a fresh implementation of
the same UV-parametrized sampling proven in
wirebond.SetContactPointsOnFaceCommand._sample_grid, deliberately not
imported from that GUI file — see core.chip_proxy's module docstring for
why this codebase prefers a fresh, independently-tested implementation over
sharing code out of an already-tested path) plus a set of rectangular
keep-out obstacles, this module finds a waypoint path between two clicked
points that:

  * respects a user-supplied maximum bend angle at every corner (free-angle
    routing, not Manhattan/45-only),
  * avoids every obstacle rectangle (previously-routed traces, other
    document geometry near the routing plane), and
  * is a real, obstacle-checked polyline — not just a straight line clipped
    afterwards.

Because a strict grid lattice only offers a handful of fixed directions (a
poor fit for "free angle with a max bend limit"), routing is done over a
VISIBILITY GRAPH built lazily on top of the sampled grid points: each point
connects to every OTHER sampled point within a radius that has a clear,
obstacle-free line of sight, giving many more usable directions than the
grid's own 8 immediate neighbours would. Path search is angle-constrained
A* over that graph, followed by a constrained "string-pulling" simplification
pass that collapses grid-granularity zig-zag into a clean polyline while
still respecting both obstacles and the bend limit.

Obstacles are intentionally axis-aligned bounding boxes, not exact polygon
outlines — a known v1 simplification, consistent with how this codebase
already approximates obstacles elsewhere (core.via_clustering._boxes_touch,
wirebond.ManualWireBonding._subtract_obstacles).

This module is Qt-free and never touches FreeCADGui.Selection or a task
panel — see routing/ for the GUI layer that drives it, mirroring the
core/ vs command-file split used throughout this repo (core.chip_proxy vs
gds.ImportChipProxyCommand, core.leadframe vs leadframe.LeadframeCommand).
"""

import math
import heapq
from collections import namedtuple, defaultdict

import FreeCAD
import Part

# ── obstacle rectangles ──────────────────────────────────────────────────────

Rect = namedtuple("Rect", ["xmin", "ymin", "xmax", "ymax"])

DEFAULT_NEIGHBOR_RADIUS_MULTIPLIER = 4.0   # neighbor_radius = multiplier * grid spacing


# ── face grid sampling ─────────────────────────────────────────────────────────
#
# Two containment strategies were tried and rejected before landing on the
# tessellate-once-then-ray-cast approach below:
#
#   1. OCCT's per-point domain classifier (face.isPartOfDomain / isInside) —
#      the ORIGINAL approach, ported from
#      wirebond.SetContactPointsOnFaceCommand._sample_grid. That tool is
#      normally run on a single small bond pad, so it never exposed this:
#      OCCT rebuilds its trim classifier from scratch on EVERY call, so cost
#      scales with the face's trim complexity PER SAMPLE POINT, not once. On
#      a face with ~170 trim loops (a stand-in for a real PCB copper pour
#      with many via-clearance holes), 3600 isPartOfDomain calls alone did
#      not finish in 2 minutes.
#   2. Fixing #1 by tessellating the boundary once and ray-casting in pure
#      Python — a huge win (minutes -> ~5s for 2500 points on that same
#      face) — but face.valueAt(u, v) itself turned out to be the next
#      bottleneck on a complex trimmed surface (2500 calls alone: ~5s).
#
# The fix that actually matters is _sample_planar_face_grid: PCB/leadframe/
# package routing surfaces are virtually always flat, so for a Part.Plane
# face we skip OCCT surface evaluation ENTIRELY — project the boundary onto
# the plane's own local 2D basis once (plain vector math), sample a regular
# grid directly in that metric frame, and convert back to world 3D with
# vector arithmetic. Confirmed in testing: the same 2500-point sample that
# took several minutes via isPartOfDomain drops to well under a millisecond.
# Genuinely curved faces fall back to the ORIGINAL isPartOfDomain/valueAt
# path unchanged — see _sample_curved_face_grid's docstring for why that
# fallback deliberately does NOT get the same tessellate-and-ray-cast
# treatment (it broke silently on a cylinder during development: periodic/
# seamed UV domains aren't safe to hand-roll a boundary polygon for). This
# is an acceptable scope boundary since PCB/leadframe/package routing
# surfaces — the actual reported problem — are virtually always flat.

def sample_face_grid(face, spacing_mm: float) -> list:
    """Return FreeCAD.Vector points sampled on *face* at ~spacing_mm
    intervals — see the section comment above for why this dispatches on
    face planarity rather than using one algorithm for every face."""
    # Reject an implausibly large face (e.g. a FreeCAD Origin datum plane,
    # ~1e100 mm) up front: with no boundary wires it would sample nothing
    # anyway, but a huge TRIMMED boundary would blow up wire.discretize()
    # into an effectively infinite point count. Real routing surfaces
    # (PCB/leadframe/package faces) are at most a few hundred mm.
    try:
        bb = face.BoundBox
        if (bb.XLength > _MAX_OBSTACLE_EXTENT_MM or bb.YLength > _MAX_OBSTACLE_EXTENT_MM
                or bb.ZLength > _MAX_OBSTACLE_EXTENT_MM):
            FreeCAD.Console.PrintWarning(
                "[trace_routing] refusing to grid an implausibly large face "
                f"({bb.XLength:.3g} x {bb.YLength:.3g} mm) — is this a datum "
                "plane rather than a real surface?\n"
            )
            return []
    except Exception:
        pass
    try:
        is_planar = isinstance(face.Surface, Part.Plane)
    except Exception:
        is_planar = False
    if is_planar:
        return _sample_planar_face_grid(face, spacing_mm)
    return _sample_curved_face_grid(face, spacing_mm)


def _sample_planar_face_grid(face, spacing_mm: float) -> list:
    """Fast path for planar faces — see the section comment above."""
    pts = []
    try:
        plane  = face.Surface
        origin = plane.Position
        normal = plane.Axis
        ref = FreeCAD.Vector(1.0, 0.0, 0.0) if abs(normal.x) < 0.9 else FreeCAD.Vector(0.0, 1.0, 0.0)
        ax1 = normal.cross(ref)
        ax1.normalize()
        ax2 = normal.cross(ax1)
        ax2.normalize()

        def _project(p):
            d = p - origin
            return (d.dot(ax1), d.dot(ax2))

        deflection = max(spacing_mm * 0.25, 0.01)
        loops = _tessellate_face_boundary(face, deflection, projector=_project)
        if not loops:
            return pts

        all_s = [p[0] for xy, _ in loops for p in xy]
        all_t = [p[1] for xy, _ in loops for p in xy]
        smin, smax = min(all_s), max(all_s)
        tmin, tmax = min(all_t), max(all_t)
        if (smax - smin) < 1e-9 or (tmax - tmin) < 1e-9:
            return pts

        n_s = max(2, min(50, int((smax - smin) / spacing_mm) + 1))
        n_t = max(2, min(50, int((tmax - tmin) / spacing_mm) + 1))
        index = _build_loop_index(loops, spacing_mm)

        for i in range(n_s):
            s = smin + (smax - smin) * i / (n_s - 1)
            for j in range(n_t):
                t = tmin + (tmax - tmin) * j / (n_t - 1)
                if _point_in_loops(s, t, loops, index, spacing_mm):
                    world = origin + ax1 * s + ax2 * t
                    pts.append(FreeCAD.Vector(world.x, world.y, world.z))
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[trace_routing] planar grid sampling: {exc}\n")
    return pts


def _sample_curved_face_grid(face, spacing_mm: float) -> list:
    """
    Fallback for genuinely curved (non-planar) faces: the ORIGINAL UV-
    parametrized regular sampling and OCCT domain-containment check
    (isPartOfDomain / isInside), unchanged.

    Deliberately NOT given the same tessellate-and-ray-cast treatment as
    the planar fast path above: a first attempt at that broke silently on
    a cylinder (0 points returned) because containment was tested in world
    XY, and a wrapping surface can have many surface points share the same
    XY projection — fixing that properly means testing containment in the
    face's own (u, v) parameter space instead, but periodic/seamed UV
    domains (a cylinder's u-seam, a sphere's poles, ...) break a naive
    tessellate-once boundary polygon in ways a flat plane's UV space never
    does. Since PCB/leadframe/package routing surfaces are virtually always
    flat — the planar path is what actually matters, confirmed against the
    real reported slowdown — it isn't worth the correctness risk of
    hand-rolling that for an edge case this feature doesn't need. Curved
    routing surfaces stay on the slower-but-correct original path.
    """
    pts = []
    try:
        u_min, u_max, v_min, v_max = face.ParameterRange
        if (u_max - u_min) < 1e-9 or (v_max - v_min) < 1e-9:
            return pts

        u_mid = (u_min + u_max) / 2.0
        v_mid = (v_min + v_max) / 2.0
        p0    = face.valueAt(u_mid, v_mid)

        du_p = (u_max - u_min) * 0.01
        dv_p = (v_max - v_min) * 0.01
        u_sc = (p0.distanceToPoint(face.valueAt(u_mid + du_p, v_mid)) / du_p
                if du_p > 1e-12 else 1.0)
        v_sc = (p0.distanceToPoint(face.valueAt(u_mid, v_mid + dv_p)) / dv_p
                if dv_p > 1e-12 else 1.0)

        du = (spacing_mm / u_sc) if u_sc > 1e-9 else (u_max - u_min) / 5.0
        dv = (spacing_mm / v_sc) if v_sc > 1e-9 else (v_max - v_min) / 5.0

        n_u = max(2, min(50, int((u_max - u_min) / du) + 1))
        n_v = max(2, min(50, int((v_max - v_min) / dv) + 1))

        for i in range(n_u):
            u = u_min + (u_max - u_min) * i / (n_u - 1)
            for j in range(n_v):
                v = v_min + (v_max - v_min) * j / (n_v - 1)
                try:
                    pt = face.valueAt(u, v)
                except Exception:
                    continue
                inside = True
                try:
                    inside = face.isPartOfDomain(u, v)
                except AttributeError:
                    try:
                        inside = face.isInside(pt, 1e-3, True)
                    except Exception:
                        inside = True
                if inside:
                    pts.append(FreeCAD.Vector(pt.x, pt.y, pt.z))
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[trace_routing] curved grid sampling: {exc}\n")
    return pts


def _tessellate_face_boundary(face, deflection_mm: float, projector=None):
    """
    Tessellate every boundary wire of *face* ONCE into 2D polygon loops.
    Used only by the planar fast path (_sample_planar_face_grid), which
    passes a projector onto the plane's own local (s, t) basis — see that
    function's docstring, and _sample_curved_face_grid's docstring for why
    the curved-surface fallback does NOT use this. *projector* defaults to
    (p.x, p.y) purely so this stays independently testable/reusable; no
    current caller relies on that default.

    Returns [(points_2d, (xmin, ymin, xmax, ymax)), ...], one entry per
    wire loop (outer boundary and every hole, undistinguished — see
    _point_in_loops for why that's fine).
    """
    proj = projector or (lambda p: (p.x, p.y))
    loops = []
    for wire in face.Wires:
        try:
            pts = wire.discretize(Deflection=deflection_mm)
        except Exception:
            pts = [v.Point for v in wire.Vertexes]
        xy = [proj(p) for p in pts]
        if len(xy) < 3:
            continue
        xs = [p[0] for p in xy]
        ys = [p[1] for p in xy]
        loops.append((xy, (min(xs), min(ys), max(xs), max(ys))))
    return loops


def _build_loop_index(loops, cell_size: float):
    """Spatial-hash bucket of loops by the cells their bbox spans — same
    bucketing technique as core.via_clustering.cluster_boxes — so a
    containment query only tests loops actually near the query point
    instead of every loop on the face. Matters when trim holes cluster or
    merge into large irregular shapes, which would defeat a flat per-loop
    bbox scan alone."""
    cell = max(cell_size, 1e-6)
    index = defaultdict(list)
    for idx, (_xy, (xmin, ymin, xmax, ymax)) in enumerate(loops):
        cx0, cy0 = _cell_of(xmin, ymin, cell)
        cx1, cy1 = _cell_of(xmax, ymax, cell)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                index[(cx, cy)].append(idx)
    return index


def _point_in_loops(x: float, y: float, loops, index, cell_size: float) -> bool:
    """Even-odd ray-cast containment across every candidate loop bucketed
    into the query point's cell. Even-odd across (outer boundary + every
    hole) together naturally handles "inside the face, outside every hole"
    without treating outer/hole loops specially."""
    cell = max(cell_size, 1e-6)
    cx, cy = _cell_of(x, y, cell)
    inside = False
    for idx in index.get((cx, cy), ()):
        xy, (xmin, ymin, xmax, ymax) = loops[idx]
        if x < xmin or x > xmax or y < ymin or y > ymax:
            continue
        n = len(xy)
        j = n - 1
        for i in range(n):
            xi, yi = xy[i]
            xj, yj = xy[j]
            if ((yi > y) != (yj > y)) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


# ── spatial hashing (same bucket style as core.via_clustering) ────────────────

def _cell_of(x: float, y: float, cell: float):
    return (math.floor(x / cell), math.floor(y / cell))


def build_point_index(points, cell_size: float):
    """dict[(cx, cy)] -> list of indices into *points*."""
    cell = max(cell_size, 1e-6)
    index = defaultdict(list)
    for i, p in enumerate(points):
        index[_cell_of(p.x, p.y, cell)].append(i)
    return index


def query_neighbors(points, index, cell_size: float, center_idx: int, radius: float):
    """Indices of points within *radius* of points[center_idx] (excluding itself)."""
    cell = max(cell_size, 1e-6)
    p0 = points[center_idx]
    cx, cy = _cell_of(p0.x, p0.y, cell)
    span = max(1, math.ceil(radius / cell))
    r2 = radius * radius
    out = []
    for dx in range(-span, span + 1):
        for dy in range(-span, span + 1):
            for j in index.get((cx + dx, cy + dy), ()):
                if j == center_idx:
                    continue
                dxp = points[j].x - p0.x
                dyp = points[j].y - p0.y
                if dxp * dxp + dyp * dyp <= r2:
                    out.append(j)
    return out


# ── segment / rectangle intersection ───────────────────────────────────────────

def segment_intersects_rect(p0, p1, rect: Rect) -> bool:
    """
    True if the segment [p0, p1] (XY only) intersects — or touches — *rect*.

    Liang-Barsky parametric line clipping against an axis-aligned rectangle.
    Touching an edge counts as intersecting (conservative, appropriate for a
    keep-out test). Handles a zero-length segment as a point-in-rect test.
    """
    dx = p1.x - p0.x
    dy = p1.y - p0.y

    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return rect.xmin <= p0.x <= rect.xmax and rect.ymin <= p0.y <= rect.ymax

    t0, t1 = 0.0, 1.0
    for p, q in (
        (-dx, p0.x - rect.xmin),
        (dx, rect.xmax - p0.x),
        (-dy, p0.y - rect.ymin),
        (dy, rect.ymax - p0.y),
    ):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return False
            if t > t0:
                t0 = t
        else:
            if t < t0:
                return False
            if t < t1:
                t1 = t
    return t0 <= t1


def _candidate_obstacle_indices(p0, p1, obstacle_index, cell_size: float):
    if not obstacle_index:
        return ()
    cell = max(cell_size, 1e-6)
    xmin, xmax = (p0.x, p1.x) if p0.x <= p1.x else (p1.x, p0.x)
    ymin, ymax = (p0.y, p1.y) if p0.y <= p1.y else (p1.y, p0.y)
    cx0, cy0 = _cell_of(xmin, ymin, cell)
    cx1, cy1 = _cell_of(xmax, ymax, cell)
    seen = set()
    for cx in range(cx0, cx1 + 1):
        for cy in range(cy0, cy1 + 1):
            for idx in obstacle_index.get((cx, cy), ()):
                seen.add(idx)
    return seen


def point_in_rect(p, rect: Rect) -> bool:
    return rect.xmin <= p.x <= rect.xmax and rect.ymin <= p.y <= rect.ymax


def segment_clear(p0, p1, obstacles, obstacle_index=None, cell_size=None, exempt=None) -> bool:
    """
    True if [p0, p1] crosses none of *obstacles* (list of Rect).

    *exempt* is an optional set of obstacle INDICES to ignore — used for
    per-leg endpoint exemption (see find_path): an obstacle the leg's start
    or end point sits inside is something the user is deliberately landing
    on (a pad, the copper being connected), so it must not block that leg.
    """
    if not obstacles:
        return True
    if obstacle_index is not None and cell_size is not None:
        idxs = _candidate_obstacle_indices(p0, p1, obstacle_index, cell_size)
        if exempt:
            idxs = [i for i in idxs if i not in exempt]
        return not any(segment_intersects_rect(p0, p1, obstacles[i]) for i in idxs)
    if exempt:
        candidates = (r for k, r in enumerate(obstacles) if k not in exempt)
    else:
        candidates = obstacles
    return not any(segment_intersects_rect(p0, p1, r) for r in candidates)


# ── obstacle collection from the document ──────────────────────────────────────

def _has_geometry(o) -> bool:
    shp = getattr(o, "Shape", None)
    return shp is not None and not shp.isNull()


# A real physical body can't plausibly be this large in mm — anything above
# this is FreeCAD construction geometry (Origin datum planes/axes carry
# ~1e100 mm bounding boxes) or a broken shape, never a routing obstacle.
# Guards against those bboxes reaching the cell-bucketing loop in
# VisibilityGraph.add_obstacle, where a 1e100-wide rect at mm-scale cells is
# an effectively infinite range() (the real cause of "gridding hangs forever"
# reported on STEP files that import an App::Origin).
_MAX_OBSTACLE_EXTENT_MM = 1.0e6


def _obstacle_subshapes(shp):
    """
    The individual sub-shapes of *shp* to treat as SEPARATE keep-out
    obstacles — its Solids if it has any, else its Faces, else the whole
    shape.

    Crucial for real PCBs: a copper / pad / via layer imports as ONE
    Part::Feature holding hundreds of disjoint solids (one per trace
    segment, pad, or via — e.g. 291 copper solids, 1013 pad solids on a
    real board tested against). Using that object's OVERALL bounding box
    would make it a single keep-out spanning the whole board, so routing
    could never find a path anywhere — the actual "No path found for that
    leg" symptom reported. One rect per solid instead leaves the real gaps
    between traces open for routing.
    """
    try:
        solids = shp.Solids
        if solids:
            return solids
    except Exception:
        pass
    try:
        faces = shp.Faces
        if faces:
            return faces
    except Exception:
        pass
    return [shp]


def collect_obstacles(doc, exclude_names, z_min: float, z_max: float, expand_mm: float) -> list:
    """
    Keep-out Rects for every PHYSICAL body sub-solid whose world AABB
    Z-range overlaps [z_min, z_max], each expanded by *expand_mm*
    (clearance + half trace width — computed by the caller).

    Obstacle objects are decomposed into their individual solids (see
    _obstacle_subshapes) — one Rect per trace/pad/via, NOT one Rect per
    whole layer object, so a board-spanning copper layer doesn't block the
    entire routing surface.

    Only objects derived from Part::Feature or Mesh::Feature are considered
    — this deliberately excludes FreeCAD's Origin construction geometry
    (App::Plane datum planes, App::Line axes, App::Point), which a STEP
    import commonly adds and which carry astronomically large (~1e100 mm)
    bounding boxes; treating those as obstacles both makes no physical sense
    and, before this filter, hung routing entirely (see
    _MAX_OBSTACLE_EXTENT_MM and VisibilityGraph.add_obstacle). App::Part /
    App::DocumentObjectGroup containers are likewise excluded — their own
    synthetic Shape spans all their children, and those children are already
    present as separate top-level Part::Feature objects.

    Also excludes: names in *exclude_names* (the routing-surface source
    object(s), the current in-progress trace's own preview legs — supplied
    by the caller) and IsContactPoint / grid-marker helper objects. A
    _MAX_OBSTACLE_EXTENT_MM sanity clamp drops any absurdly-sized sub-shape
    bbox as defence in depth.
    """
    rects = []
    if doc is None:
        return rects
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
        if not _has_geometry(o):
            continue
        # Cheap whole-object gate first: skip the (potentially expensive)
        # per-solid decomposition entirely when the whole object is out of
        # the Z band or is construction-geometry-sized.
        try:
            obb = o.Shape.BoundBox
        except Exception:
            continue
        if not obb.isValid() or obb.ZMax < z_min or obb.ZMin > z_max:
            continue
        if (obb.XLength > _MAX_OBSTACLE_EXTENT_MM or obb.YLength > _MAX_OBSTACLE_EXTENT_MM
                or obb.ZLength > _MAX_OBSTACLE_EXTENT_MM):
            FreeCAD.Console.PrintWarning(
                f"[trace_routing] skipping '{o.Name}' as an obstacle — bounding "
                f"box ({obb.XLength:.3g} x {obb.YLength:.3g} x {obb.ZLength:.3g} mm) "
                "is implausibly large (construction geometry?).\n"
            )
            continue

        for sub in _obstacle_subshapes(o.Shape):
            try:
                bb = sub.BoundBox
            except Exception:
                continue
            if not bb.isValid():
                continue
            if bb.ZMax < z_min or bb.ZMin > z_max:
                continue
            if (bb.XLength > _MAX_OBSTACLE_EXTENT_MM or bb.YLength > _MAX_OBSTACLE_EXTENT_MM
                    or bb.ZLength > _MAX_OBSTACLE_EXTENT_MM):
                continue
            rects.append(Rect(
                bb.XMin - expand_mm, bb.YMin - expand_mm,
                bb.XMax + expand_mm, bb.YMax + expand_mm,
            ))
    return rects


# ── visibility graph ────────────────────────────────────────────────────────────

class VisibilityGraph:
    """
    Lazily-computed visibility graph over a set of routing points.

    A node's candidate edges (to other points within neighbor_radius with
    clear line-of-sight past every obstacle) are computed on first access
    and cached — a typical point-to-point search only expands a bounded
    local region even on a large grid (~2500 points), so this stays cheap
    without needing the full edge set up front.

    Mutable by design: add_point() injects a clicked start/end point (and
    clears the neighbor cache, since an earlier-cached node might legally
    connect to it); add_obstacle() appends a newly-confirmed trace's
    keep-out rectangle (and clears the cache, since a previously-clear edge
    may now cross it) — this lets one VisibilityGraph persist for an entire
    routing session (built once at session start) across multiple
    click-to-click legs and multiple confirmed traces, rather than being
    rebuilt from scratch every time.
    """

    def __init__(self, points, obstacles=None, neighbor_radius: float = 1.0,
                 cell_size: float = 1.0):
        self.points = list(points)
        self.radius = neighbor_radius
        self.cell_size = max(cell_size, 1e-6)
        self.obstacles = []
        self._obstacle_index = defaultdict(list)
        self._point_index = build_point_index(self.points, self.cell_size)
        self._cell_bounds = self._compute_cell_bounds()
        self._cache = {}
        for r in (obstacles or ()):
            self.add_obstacle(r)

    def _compute_cell_bounds(self):
        """(min_cx, min_cy, max_cx, max_cy) of the grid points' own cells,
        with a 1-cell margin. add_obstacle() clamps every obstacle's cell
        bucketing to this window so an obstacle far larger than the routing
        area can never expand the bucketing loop into an effectively
        infinite range. Correct because a straight segment between two grid
        points stays within the points' bounding box, so an obstacle
        entirely outside that box cannot intersect any edge and never needs
        bucketing. Returns None when there are no points yet (bucketing then
        does nothing)."""
        if not self.points:
            return None
        cells = [_cell_of(p.x, p.y, self.cell_size) for p in self.points]
        cxs = [c[0] for c in cells]
        cys = [c[1] for c in cells]
        return (min(cxs) - 1, min(cys) - 1, max(cxs) + 1, max(cys) + 1)

    def add_point(self, pt) -> int:
        idx = len(self.points)
        self.points.append(pt)
        self._point_index[_cell_of(pt.x, pt.y, self.cell_size)].append(idx)
        # A clicked start/end point is a grid sample on the routing surface,
        # so it lies within the existing points' region — but expand the
        # clamp window anyway so a point right at the boundary keeps its
        # nearby obstacles bucketed.
        if self._cell_bounds is not None:
            cx, cy = _cell_of(pt.x, pt.y, self.cell_size)
            bx0, by0, bx1, by1 = self._cell_bounds
            self._cell_bounds = (min(bx0, cx - 1), min(by0, cy - 1),
                                 max(bx1, cx + 1), max(by1, cy + 1))
        else:
            self._cell_bounds = self._compute_cell_bounds()
        self._cache.clear()
        return idx

    def add_obstacle(self, rect: Rect) -> None:
        idx = len(self.obstacles)
        self.obstacles.append(rect)
        cx0, cy0 = _cell_of(rect.xmin, rect.ymin, self.cell_size)
        cx1, cy1 = _cell_of(rect.xmax, rect.ymax, self.cell_size)
        # Clamp bucketing to the grid region (see _compute_cell_bounds). The
        # full, unclamped rect is still stored in self.obstacles, so the
        # precise segment_intersects_rect test remains exact — only WHICH
        # cells trigger that test is bounded here. An obstacle entirely
        # outside the region clamps to an empty range and is simply never
        # bucketed (it can't be hit by any in-region edge anyway).
        if self._cell_bounds is not None:
            bx0, by0, bx1, by1 = self._cell_bounds
            cx0 = max(cx0, bx0); cy0 = max(cy0, by0)
            cx1 = min(cx1, bx1); cy1 = min(cy1, by1)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                self._obstacle_index[(cx, cy)].append(idx)
        self._cache.clear()

    def line_of_sight(self, p0, p1, exempt=None) -> bool:
        return segment_clear(p0, p1, self.obstacles, self._obstacle_index,
                             self.cell_size, exempt)

    def neighbors(self, idx: int, exempt=None):
        """
        [(neighbor_idx, distance), ...] — computed lazily.

        Cached only when *exempt* is empty/None (the common case). A
        non-empty per-leg exemption set (see find_path) would poison the
        persistent cache for later legs that don't share it, so those
        queries are computed fresh and left uncached — cheap, since an
        exemption only occurs when a leg endpoint sits inside an obstacle.
        """
        if not exempt:
            cached = self._cache.get(idx)
            if cached is not None:
                return cached
        p0 = self.points[idx]
        out = []
        for j in query_neighbors(self.points, self._point_index, self.cell_size, idx, self.radius):
            p1 = self.points[j]
            if self.line_of_sight(p0, p1, exempt):
                out.append((j, math.hypot(p1.x - p0.x, p1.y - p0.y)))
        if not exempt:
            self._cache[idx] = out
        return out


def build_visibility_graph(points, obstacles, neighbor_radius: float, cell_size: float) -> VisibilityGraph:
    return VisibilityGraph(points, obstacles, neighbor_radius, cell_size)


def _find_or_add_point(graph: VisibilityGraph, pt, tol: float = 1e-6) -> int:
    """Reuse an existing graph node within *tol* of *pt* instead of always
    injecting a new one — avoids duplicate nodes when a leg's start point is
    the same physical point as the previous leg's end point."""
    cx, cy = _cell_of(pt.x, pt.y, graph.cell_size)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for idx in graph._point_index.get((cx + dx, cy + dy), ()):
                if (graph.points[idx] - pt).Length <= tol:
                    return idx
    return graph.add_point(pt)


# ── angle-constrained A* ─────────────────────────────────────────────────────

def _angle_between_deg(d1, d2) -> float:
    """Angle in degrees (0..180) between two 2D direction vectors."""
    n1 = math.hypot(d1[0], d1[1])
    n2 = math.hypot(d2[0], d2[1])
    if n1 < 1e-12 or n2 < 1e-12:
        return 0.0
    cos_a = (d1[0] * d2[0] + d1[1] * d2[1]) / (n1 * n2)
    cos_a = max(-1.0, min(1.0, cos_a))
    return math.degrees(math.acos(cos_a))


def _bend_at(a, b, c) -> float:
    """Bend angle (deviation from straight, degrees) of the path a->b->c."""
    return _angle_between_deg((b.x - a.x, b.y - a.y), (c.x - b.x, c.y - b.y))


def _endpoint_exempt_obstacles(graph, p_start, p_end):
    """Indices of obstacles the leg's start or end point sits inside — the
    user is deliberately landing on those (a pad, the copper being
    connected), so they must not block this leg. Without this, clicking a
    start or end point that happens to sit on existing copper (very common
    when grid points are sampled across a whole populated board) makes every
    route from it fail immediately — the reported "No path found for that
    leg" even between two nearby points."""
    return frozenset(
        i for i, r in enumerate(graph.obstacles)
        if point_in_rect(p_start, r) or point_in_rect(p_end, r)
    )


def find_path(graph: VisibilityGraph, start, end, max_bend_deg: float,
              max_expansions: int = 200_000, exempt=None):
    """
    Angle-constrained shortest path from *start* to *end* over *graph*.

    *start*/*end* may be either an existing node index (int) or a
    FreeCAD.Vector to inject/reuse (via _find_or_add_point). Returns a list
    of FreeCAD.Vector waypoints, or None if no legal path exists.

    *exempt* is an optional set of obstacle indices to ignore for this whole
    search; when None it defaults to the obstacles containing the start or
    end point (see _endpoint_exempt_obstacles) so a leg can begin/finish on
    the pad/copper it's connecting to.

    A direct line-of-sight fast path is tried first — the common case on a
    mostly-empty routing surface needs no graph search at all.

    State is keyed on (node_id, prev_node_id), NOT (node_id, direction) —
    a node reached via a different predecessor is a genuinely different
    state with a different legal-turn set, so keying on direction/node
    alone would silently prune valid paths reached from a different
    approach angle. The heuristic (Euclidean distance to goal) stays
    admissible under the bend constraint: the constraint only removes edges
    or adds cost, it never shortens the unconstrained straight-line bound.

    Bend angle convention: deviation from straight continuation (0 degrees
    = straight through), not the interior corner angle.
    """
    if not (0.0 <= max_bend_deg <= 180.0):
        raise ValueError(f"find_path: max_bend_deg must be in [0, 180], got {max_bend_deg}")

    start_idx = start if isinstance(start, int) else _find_or_add_point(graph, start)
    end_idx   = end   if isinstance(end, int)   else _find_or_add_point(graph, end)

    if start_idx == end_idx:
        raise ValueError("find_path: start and end resolve to the same point")

    p_start = graph.points[start_idx]
    p_end   = graph.points[end_idx]
    if (p_start - p_end).Length < 1e-9:
        raise ValueError("find_path: start and end are coincident")

    if exempt is None:
        exempt = _endpoint_exempt_obstacles(graph, p_start, p_end)

    if graph.line_of_sight(p_start, p_end, exempt):
        return [p_start, p_end]

    def h(idx):
        p = graph.points[idx]
        return math.hypot(p.x - p_end.x, p.y - p_end.y)

    start_state = (start_idx, None)
    g_score = {start_state: 0.0}
    came_from = {}
    open_heap = [(h(start_idx), start_idx, start_state)]
    closed = set()
    expansions = 0

    while open_heap:
        expansions += 1
        if expansions > max_expansions:
            return None

        _, cur_idx, state = heapq.heappop(open_heap)
        if state in closed:
            continue
        closed.add(state)

        if cur_idx == end_idx:
            return _reconstruct_path(graph, came_from, state)

        _, prev_idx = state
        cur_pt = graph.points[cur_idx]
        incoming_dir = None
        if prev_idx is not None:
            prev_pt = graph.points[prev_idx]
            incoming_dir = (cur_pt.x - prev_pt.x, cur_pt.y - prev_pt.y)

        for nbr_idx, dist in graph.neighbors(cur_idx, exempt):
            if nbr_idx == prev_idx:
                continue   # no immediate backtrack — never part of a shortest path
            nbr_pt = graph.points[nbr_idx]
            if incoming_dir is not None:
                outgoing_dir = (nbr_pt.x - cur_pt.x, nbr_pt.y - cur_pt.y)
                if _angle_between_deg(incoming_dir, outgoing_dir) > max_bend_deg:
                    continue
            new_state = (nbr_idx, cur_idx)
            tentative_g = g_score[state] + dist
            if tentative_g < g_score.get(new_state, float("inf")):
                g_score[new_state] = tentative_g
                came_from[new_state] = state
                heapq.heappush(open_heap, (tentative_g + h(nbr_idx), nbr_idx, new_state))

    return None


def _reconstruct_path(graph: VisibilityGraph, came_from, end_state):
    states = [end_state]
    s = end_state
    while s in came_from:
        s = came_from[s]
        states.append(s)
    states.reverse()
    return [graph.points[idx] for idx, _prev in states]


# ── path simplification (constrained string-pulling) ───────────────────────────

def simplify_path(path, obstacles, max_bend_deg: float, obstacle_index=None,
                  cell_size=None, exempt=None):
    """
    Collapse grid-granularity zig-zag into a cleaner polyline: repeatedly
    try to remove an intermediate waypoint when the direct shortcut across
    it has clear line-of-sight AND the corners on BOTH sides of the removal
    still respect max_bend_deg afterwards (removing one point changes the
    bend angle at both of its former neighbours, not just one). Iterates
    single-hop-skip passes to a fixed point. *exempt* mirrors find_path's
    per-leg endpoint exemption so a shortcut isn't rejected by the very
    obstacle the leg legitimately starts/ends inside.
    """
    if len(path) <= 2:
        return list(path)

    pts = list(path)
    changed = True
    while changed and len(pts) > 2:
        changed = False
        i = 0
        while i < len(pts) - 2:
            a, c = pts[i], pts[i + 2]
            if segment_clear(a, c, obstacles, obstacle_index, cell_size, exempt):
                prev_ok = True
                if i > 0:
                    prev_ok = _bend_at(pts[i - 1], a, c) <= max_bend_deg
                next_ok = True
                if i + 3 < len(pts):
                    next_ok = _bend_at(a, c, pts[i + 3]) <= max_bend_deg
                if prev_ok and next_ok:
                    del pts[i + 1]
                    changed = True
                    continue
            i += 1
    return pts


# ── leg routing orchestration ───────────────────────────────────────────────────

def route_leg(graph: VisibilityGraph, start_pt, end_pt, max_bend_deg: float):
    """
    Find and simplify a waypoint path from start_pt to end_pt over *graph*.
    Returns a list of FreeCAD.Vector waypoints, or None if no legal path
    exists (degenerate start==end input is also treated as "no route" here
    rather than propagating the lower-level ValueError, since a GUI click
    on the already-current endpoint is a routine no-op, not an error).
    """
    exempt = _endpoint_exempt_obstacles(graph, start_pt, end_pt)
    try:
        raw = find_path(graph, start_pt, end_pt, max_bend_deg, exempt=exempt)
    except ValueError:
        return None
    if raw is None:
        return None
    return simplify_path(raw, graph.obstacles, max_bend_deg,
                         graph._obstacle_index, graph.cell_size, exempt=exempt)


# ── trace solid construction ────────────────────────────────────────────────────

def _dedup_points(points, tol: float = 1e-9):
    out = [points[0]]
    for p in points[1:]:
        if (p - out[-1]).Length > tol:
            out.append(p)
    return out


def _rect_profile_wire(center, tangent, width_mm: float, thickness_mm: float) -> Part.Wire:
    """Rectangle profile (width x thickness) centered at *center*, lying in
    the plane perpendicular to *tangent* — ready to sweep along a spine
    whose initial tangent is *tangent*. Width axis prefers to lie in the
    routing (XY) plane, matching how a real copper trace is drawn."""
    t2 = FreeCAD.Vector(tangent.x, tangent.y, tangent.z)
    if t2.Length < 1e-9:
        t2 = FreeCAD.Vector(1.0, 0.0, 0.0)
    else:
        t2.normalize()

    if abs(t2.z) < 0.999:
        n = FreeCAD.Vector(-t2.y, t2.x, 0.0)
        if n.Length < 1e-9:
            n = t2.cross(FreeCAD.Vector(0.0, 0.0, 1.0))
    else:
        n = FreeCAD.Vector(1.0, 0.0, 0.0)
    n.normalize()

    z = t2.cross(n)
    if z.Length < 1e-9:
        z = FreeCAD.Vector(0.0, 0.0, 1.0)
    else:
        z.normalize()

    hw, ht = width_mm / 2.0, thickness_mm / 2.0
    c0 = center + n * hw + z * ht
    c1 = center - n * hw + z * ht
    c2 = center - n * hw - z * ht
    c3 = center + n * hw - z * ht
    edges = [
        Part.LineSegment(c0, c1).toShape(), Part.LineSegment(c1, c2).toShape(),
        Part.LineSegment(c2, c3).toShape(), Part.LineSegment(c3, c0).toShape(),
    ]
    return Part.Wire(edges)


def _sweep_with_fallback(spine_wire: Part.Wire, profile: Part.Wire):
    """Round-corner makePipeShell, falling back to default-transition —
    same 3-tier robustness idea as
    wirebond.ManualWireBonding._sweep_circle_along_polyline, minus its final
    open-pipe fallback (a trace with no volume is useless here — the caller
    falls back to per-leg boxes instead, see build_trace_solid)."""
    try:
        solid = spine_wire.makePipeShell([profile], True, False, 2)   # 2 = round corner
        if solid is not None and not solid.isNull() and solid.isValid() and solid.Solids:
            return solid.Solids[0] if len(solid.Solids) == 1 else solid
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[trace_routing] round-corner makePipeShell failed: {exc}\n"
        )
    try:
        solid = spine_wire.makePipeShell([profile], True, False)
        if solid is not None and not solid.isNull() and solid.isValid() and solid.Solids:
            return solid.Solids[0] if len(solid.Solids) == 1 else solid
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[trace_routing] default-transition makePipeShell failed: {exc}\n"
        )
    return None


def _leg_box(p0, p1, width_mm: float, thickness_mm: float) -> Part.Shape:
    delta = p1 - p0
    length = delta.Length
    if length < 1e-9:
        raise ValueError("_leg_box: zero-length leg")
    box = Part.makeBox(
        length, width_mm, thickness_mm,
        FreeCAD.Vector(0.0, -width_mm / 2.0, -thickness_mm / 2.0),
    )
    # Rotation(Vector(1,0,0), delta) is the minimal-angle rotation mapping
    # local +X onto delta — deterministic and twist-free for the common
    # (planar, delta.z == 0) case, and still well-defined for a
    # non-planar leg.
    box.Placement = FreeCAD.Placement(p0, FreeCAD.Rotation(FreeCAD.Vector(1.0, 0.0, 0.0), delta))
    return box


def _fuse_leg_boxes(pts, width_mm: float, thickness_mm: float) -> Part.Shape:
    boxes = [_leg_box(pts[i], pts[i + 1], width_mm, thickness_mm) for i in range(len(pts) - 1)]
    fused = boxes[0]
    for b in boxes[1:]:
        fused = fused.fuse(b)
    try:
        fused = fused.removeSplitter()
    except Exception:
        pass
    return fused


def build_trace_solid(waypoints, width_mm: float, thickness_mm: float) -> Part.Shape:
    """
    Build a real 3D solid trace along *waypoints* (>= 2 FreeCAD.Vector),
    width_mm x thickness_mm in cross-section.

    Primary path adapts
    wirebond.ManualWireBonding._sweep_circle_along_polyline's spine-wire +
    profile + makePipeShell(...,2) round-corner sweep, swapping the
    circular profile for a rectangular one. Sharp near-max-bend-angle
    corners are more prone to invalid/self-intersecting sweeps with a
    rectangular profile than the circular case that pattern was proven on,
    so if both makePipeShell transitions fail, this falls back further to
    building each straight leg as its own oriented box and fusing them at
    the joints — more robust for sharp angles, and a reasonable model of a
    real mitered PCB trace corner.
    """
    if width_mm <= 0 or thickness_mm <= 0:
        raise ValueError("build_trace_solid: width_mm and thickness_mm must be positive")

    pts = _dedup_points(list(waypoints))
    if len(pts) < 2:
        raise ValueError("build_trace_solid: fewer than 2 distinct waypoints")

    edges = [Part.LineSegment(pts[i], pts[i + 1]).toShape() for i in range(len(pts) - 1)]
    spine_wire = Part.Wire(edges)

    first_edge = spine_wire.Edges[0]
    t0 = first_edge.tangentAt(first_edge.FirstParameter)
    profile = _rect_profile_wire(pts[0], t0, width_mm, thickness_mm)

    solid = _sweep_with_fallback(spine_wire, profile)
    if solid is not None:
        return solid

    FreeCAD.Console.PrintWarning(
        "[trace_routing] makePipeShell failed for this trace's corners — "
        "falling back to per-leg boxes fused at the joints.\n"
    )
    return _fuse_leg_boxes(pts, width_mm, thickness_mm)
