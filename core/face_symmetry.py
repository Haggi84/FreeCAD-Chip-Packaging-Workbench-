# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Symmetry about a face's own centre — the shared primitive behind centring a
chip on a PCB face and placing contact points symmetrically.

Everything here works in the 2-D space of core.routing_frame.SurfaceFrame,
so it applies to ANY face of any body — horizontal, vertical, slanted or
curved — not just a board's top face, and distances stay in millimetres.

Two things this deliberately does NOT use:

  * the face's area centroid (Shape.CenterOfMass). That equals the visual
    centre only for a shape with no cut-outs and a symmetric outline; a pad
    face with a notch, or an L-shaped land, has its centroid pulled off the
    axis of symmetry. The centre used here is the mid-point of the face's
    OUTER BOUNDARY extent, which is the point a symmetric pattern should be
    built around.
  * the face's raw parameter range. On a trimmed face the parameter box is
    larger than the face, so its middle is not the face's middle — this
    measures the real outer wire instead.

Qt-free and document-free: everything takes a face/frame plus plain
coordinate tuples, so it is covered by headless tests.
"""

import math

import FreeCAD

# Mirror modes for mirror_points_2d / symmetrize_points_2d.
MIRROR_U    = "u"       # across the line u = centre_u (left <-> right)
MIRROR_V    = "v"       # across the line v = centre_v (near <-> far)
MIRROR_BOTH = "both"    # both axes at once -> full four-fold symmetry

_DEFAULT_TOL_MM = 0.05


# ── the face's own centre and extent ─────────────────────────────────────────

def outer_extent_2d(face, frame, deflection: float = 0.05):
    """
    (umin, vmin, umax, vmax) of the face's OUTER boundary in *frame*'s 2-D
    millimetre space, or None.

    The outer wire is the one with the largest bounding box — the same rule
    core.trace_obstacles.face_outer_poly_on_frame uses to tell a face's
    outline from its holes.
    """
    try:
        wires = list(face.Wires)
    except Exception:
        return None
    if not wires:
        return None
    outer = max(wires, key=lambda w: w.BoundBox.XLength * w.BoundBox.YLength)
    try:
        verts = outer.discretize(Deflection=deflection)
    except Exception:
        return None

    xs, ys = [], []
    near = None
    for v in verts:
        xy = frame.to_2d(v, near=near)
        if xy is None:
            continue
        xs.append(xy[0])
        ys.append(xy[1])
        near = xy
    if len(xs) < 2:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def face_center_2d(face, frame, deflection: float = 0.05):
    """(u, v) mid-point of the face's outer extent, or None."""
    ext = outer_extent_2d(face, frame, deflection)
    if ext is None:
        return None
    umin, vmin, umax, vmax = ext
    return ((umin + umax) / 2.0, (vmin + vmax) / 2.0)


def face_center_world(face, frame=None, deflection: float = 0.05):
    """
    World-space FreeCAD.Vector at the face's own centre, or None.

    This is the point a chip's centre should be moved to when "centre this
    die on that pad" is the intent — see gds/ChipTransformCommand.py.
    """
    if frame is None:
        import core.routing_frame as routing_frame
        try:
            frame = routing_frame.SurfaceFrame(face)
        except Exception:
            return None
    c = face_center_2d(face, frame, deflection)
    if c is None:
        return None
    try:
        return frame.to_3d(c[0], c[1])
    except Exception:
        return None


def is_inside_face(pt2d, face, frame) -> bool:
    """True when a frame-space point lies within the face's trimmed domain.
    Generated patterns are laid out over the face's rectangular extent, so a
    non-rectangular face needs them filtered against the real outline."""
    try:
        u = pt2d[0] / frame.u_scale
        v = pt2d[1] / frame.v_scale
    except Exception:
        return False
    try:
        return bool(face.isPartOfDomain(u, v))
    except AttributeError:
        pass
    except Exception:
        return False
    try:
        return bool(face.isInside(frame.to_3d(pt2d[0], pt2d[1]), 1e-3, True))
    except Exception:
        return True


# ── mirroring ────────────────────────────────────────────────────────────────

def _reflect(pt, center, kind: str):
    """One reflection of *pt*: 'u' across the u axis, 'v' across the v axis,
    'uv' across both (the diagonal image). Each is its own inverse, which is
    what lets symmetrize_points_2d place an exact partner by reflecting the
    corrected point back."""
    u, v = pt[0], pt[1]
    cu, cv = center[0], center[1]
    if kind == "u":
        return (2.0 * cu - u, v)
    if kind == "v":
        return (u, 2.0 * cv - v)
    return (2.0 * cu - u, 2.0 * cv - v)


def _kinds(mode: str):
    if mode == MIRROR_U:
        return ("u",)
    if mode == MIRROR_V:
        return ("v",)
    return ("u", "v", "uv")


def mirror_images(pt, center, mode: str = MIRROR_U) -> list:
    """
    The mirror image(s) of *pt* about *center*, excluding *pt* itself.

    MIRROR_BOTH yields THREE images (the two axis reflections plus the
    diagonal one), which is what makes a pattern four-fold symmetric rather
    than merely point-symmetric.
    """
    return [_reflect(pt, center, k) for k in _kinds(mode)]


def _close(a, b, tol: float) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def mirror_points_2d(pts, center, mode: str = MIRROR_U,
                     tol: float = _DEFAULT_TOL_MM) -> list:
    """
    The NEW points needed to make *pts* symmetric — the mirror images that
    are not already present (within *tol*). Returns only the additions, so
    the caller can create markers for exactly those.

    A point sitting ON the mirror axis is its own image and adds nothing.
    """
    out = []
    for p in pts:
        for img in mirror_images(p, center, mode):
            if any(_close(img, q, tol) for q in pts):
                continue
            if any(_close(img, q, tol) for q in out):
                continue
            out.append(img)
    return out


def symmetrize_points_2d(pts, center, mode: str = MIRROR_U,
                         tol: float = _DEFAULT_TOL_MM):
    """
    Make a hand-placed set exactly symmetric: snap near-symmetric pairs onto
    exact mirror positions, snap near-axis points onto the axis, then add
    whatever images are still missing.

    Returns (points, n_snapped, n_added) where *points* is the full
    symmetric set in the original order, followed by the additions.

    *tol* is how far a point may be from exact symmetry and still count as
    "meant to be symmetric" — clicked points are never pixel-perfect, and
    without this pass every one of them would merely gain a near-duplicate
    neighbour instead of being tidied up.
    """
    pts = [(float(p[0]), float(p[1])) for p in pts]
    c = (float(center[0]), float(center[1]))
    cu, cv = c
    n_snapped = 0
    eps = 1e-9

    # Pass 1 — pair up points that are nearly each other's mirror image and
    # move BOTH onto exactly symmetric positions. This runs BEFORE any
    # axis snapping: two points straddling the axis closer than *tol* are a
    # legitimate symmetric pair, and snapping first would collapse them
    # both onto the axis and silently merge two pads into one.
    paired = set()
    for i, p in enumerate(pts):
        if i in paired:
            continue
        matched = False
        for kind in _kinds(mode):
            img = _reflect(p, c, kind)
            for j, q in enumerate(pts):
                if j == i or j in paired or _close(q, p, eps):
                    continue
                if not _close(img, q, tol):
                    continue
                # Reflect the partner back across the SAME axis that matched
                # and average: that is the symmetric position for p, and q
                # then follows by exact reflection of it.
                back = _reflect(q, c, kind)
                new_p = ((p[0] + back[0]) / 2.0, (p[1] + back[1]) / 2.0)
                new_q = _reflect(new_p, c, kind)
                if not _close(new_p, p, eps):
                    n_snapped += 1
                if not _close(new_q, q, eps):
                    n_snapped += 1
                pts[i] = new_p
                pts[j] = new_q
                paired.add(i)
                paired.add(j)
                matched = True
                break
            if matched:
                break

    # Pass 2 — a point with no partner that sits very near an axis was
    # meant to be ON it (a centre pad), so snap it there; that also stops
    # pass 3 from adding a near-duplicate a hair away from it.
    for i, p in enumerate(pts):
        if i in paired:
            continue
        u, v = p
        if mode in (MIRROR_U, MIRROR_BOTH) and eps < abs(u - cu) <= tol:
            u = cu
            n_snapped += 1
        if mode in (MIRROR_V, MIRROR_BOTH) and eps < abs(v - cv) <= tol:
            v = cv
            n_snapped += 1
        pts[i] = (u, v)

    added = mirror_points_2d(pts, c, mode, tol)
    return pts + added, n_snapped, len(added)


# ── pattern generation ───────────────────────────────────────────────────────

def _spread(lo: float, hi: float, n: int, pitch=None, inclusive: bool = True):
    """
    *n* positions in [lo, hi], always symmetric about the mid-point.

    With *pitch*, positions sit exactly that far apart and are centred (so a
    real pad pitch is honoured); the span may then exceed [lo, hi], which
    the caller is expected to check. Without it they are distributed across
    the span — *inclusive* puts the first and last exactly on lo/hi (right
    for a grid), otherwise they are inset by half a step (right for the
    points along one side of a ring, so corners are not doubled).
    """
    if n <= 0:
        return []
    mid = (lo + hi) / 2.0
    if n == 1:
        return [mid]
    if pitch:
        total = (n - 1) * float(pitch)
        start = mid - total / 2.0
        return [start + i * float(pitch) for i in range(n)]
    if inclusive:
        return [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    return [lo + (hi - lo) * (i + 0.5) / n for i in range(n)]


def symmetric_grid_2d(extent, n_u: int, n_v: int, margin: float = 0.0,
                      pitch_u=None, pitch_v=None) -> list:
    """
    An n_u x n_v array of points, symmetric about the centre of *extent*
    (umin, vmin, umax, vmax), inset by *margin* on every side.
    """
    umin, vmin, umax, vmax = extent
    us = _spread(umin + margin, umax - margin, int(n_u), pitch_u, inclusive=True)
    vs = _spread(vmin + margin, vmax - margin, int(n_v), pitch_v, inclusive=True)
    return [(u, v) for v in vs for u in us]


def symmetric_ring_2d(extent, per_side: int, margin: float = 0.0,
                      pitch=None) -> list:
    """
    *per_side* points along each of the four sides of *extent* inset by
    *margin* — the bond-pad ring pattern, symmetric about both axes.

    Points are inset by half a step along each side so the four corners are
    not occupied twice, and each side's run is centred, which is what keeps
    the whole ring symmetric under both reflections.
    """
    umin, vmin, umax, vmax = extent
    u0, v0 = umin + margin, vmin + margin
    u1, v1 = umax - margin, vmax - margin
    n = int(per_side)
    if n <= 0:
        return []
    us = _spread(u0, u1, n, pitch, inclusive=False)
    vs = _spread(v0, v1, n, pitch, inclusive=False)
    pts = []
    pts.extend((u, v0) for u in us)     # bottom
    pts.extend((u, v1) for u in us)     # top
    pts.extend((u0, v) for v in vs)     # left
    pts.extend((u1, v) for v in vs)     # right
    return pts


# ── mapping back to the document ─────────────────────────────────────────────

def to_world(pts2d, frame) -> list:
    """Frame-space points -> world FreeCAD.Vectors, skipping any that cannot
    be mapped back onto the surface."""
    out = []
    for p in pts2d:
        try:
            w = frame.to_3d(p[0], p[1])
        except Exception:
            continue
        if w is not None:
            out.append(FreeCAD.Vector(w.x, w.y, w.z))
    return out


def world_to_frame_points(points, face, frame, band_mm: float = 1.0) -> list:
    """
    World points -> frame-space (u, v), keeping only those actually lying on
    (or within *band_mm* of) the face.

    Existing contact-point markers carry world positions, so mirroring them
    means bringing them into the face's frame first — and a marker belonging
    to some other face must not be dragged into this face's symmetry.
    """
    out = []
    near = None
    for p in points:
        try:
            if frame.distance_to_surface(p) > band_mm:
                continue
        except Exception:
            pass
        xy = frame.to_2d(p, near=near)
        if xy is None:
            continue
        out.append(xy)
        near = xy
    return out
