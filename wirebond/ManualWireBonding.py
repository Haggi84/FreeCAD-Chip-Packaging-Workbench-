# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Manual wire bonding — contact-point filter and 3-D swept-tube geometry.

Session flow
------------
1. User runs "Manual Wire Bonding" → WirebondConfigurator dialog.
2. Session starts; only ContactPoint markers are selectable (everything
   else is rejected and immediately deselected).
3. Click 1 — first ContactPoint  (die-side or leadframe-side, order free).
   A green snap-marker appears at the selected point.
4. Click 2 — second ContactPoint (must be a different object).
   A 3-D bond wire is created between the two ContactPoint positions.
5. Repeat from step 3 for the next bond.
6. "Finish Wire Bonding" ends the session and prints a report.

Wire geometry
-------------
Ball-Wedge: oblate-spheroid ball at die pad + swept tube + flat elliptical
wedge at leadframe pad.  Wedge-Wedge: flat wedge at both ends.  All parts are
fused into one solid.  Falls back to a plain tube, then a line.

Wire profile (config['wire_profile']):
  'spline' (default) — multi-point BSpline loop, a smooth realistic arc.
  'jedec'            — simplified JEDEC-style trapezoid: one straight rise,
                        a short flat "kink" segment (1/8 of the span), then a
                        straight vertical drop into the landing pad.
"""

import math

import FreeCAD
import FreeCADGui
import Part
from FreeCAD import Base

import os, sys
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)
from session.SessionManager import session_manager


# ── build marker ────────────────────────────────────────────────────────────────
# Bump this string whenever the geometry changes.  It is printed once at import
# and again per wire, so the FreeCAD Report view immediately reveals whether the
# freshly edited module is the one actually running (vs. a cached / installed
# copy, or stale .pyc in __pycache__).
_GEOM_VERSION = "2026-07-07 jedec-trapezoid-profile v8"
FreeCAD.Console.PrintMessage(
    f"[DI-PASSIONATE wirebond] ManualWireBonding loaded — geometry {_GEOM_VERSION}\n"
    f"    file: {os.path.abspath(__file__)}\n"
)


# ── colour constants ───────────────────────────────────────────────────────────
_COLOR_WIRE       = (0.90, 0.75, 0.20)   # gold
_COLOR_SNAP_VALID = (0.10, 0.90, 0.10)   # green  — valid hover / first pick
_COLOR_SNAP_WAIT  = (0.10, 0.50, 0.90)   # blue   — first pick locked in


# ── snap-point resolution ──────────────────────────────────────────────────────

def resolve_snap_point(obj) -> Base.Vector:
    """
    Return the stored ContactPoint position of *obj*.
    Falls back to top-face centre, then BoundBox centre.
    """
    if getattr(obj, "IsContactPoint", False):
        cp = getattr(obj, "ContactPoint", None)
        if cp is not None:
            return Base.Vector(cp)

    shape = getattr(obj, "Shape", None)
    if shape is None:
        return Base.Vector(0, 0, 0)
    if shape.Faces:
        top_face = max(shape.Faces, key=lambda f: f.CenterOfMass.z)
        return top_face.CenterOfMass
    return Base.Vector(shape.BoundBox.Center)


# ── 3-D bond-wire geometry ─────────────────────────────────────────────────────
#
# Realistic bond-wire shapes
# ──────────────────────────
# Ball-Wedge:  oblate-spheroid ball at die side  +  BSpline-swept tube loop
#              +  flattened elliptical wedge at leadframe side.
# Wedge-Wedge: flattened elliptical wedge at both ends  +  BSpline-swept tube.
#
# Industry proportions (multiples of wire diameter d):
#   Ball:  equatorial radius 1.1d, height 0.65d
#   Wedge: long axis 3d, short axis 1.5d, height 0.30d
#
# All three parts are fused into one solid when OCCT allows it; otherwise a
# compound is returned so the viewer always gets something.

def _make_oblate_ball(ball_r: float, ball_h: float,
                      center: Base.Vector) -> Part.Shape:
    """
    Oblate spheroid (squashed sphere) with its flat base at *center*.
    ball_r = equatorial radius, ball_h = polar (Z) height.
    """
    sphere = Part.makeSphere(ball_r)
    mat = FreeCAD.Matrix()
    mat.scale(1.0, 1.0, ball_h / (2.0 * ball_r))
    ball = sphere.transformGeometry(mat)
    ball.translate(Base.Vector(center.x, center.y,
                               center.z - ball.BoundBox.ZMin))
    return ball


def _make_wedge_solid(wedge_l: float, wedge_w: float, wedge_h: float,
                      center: Base.Vector,
                      dir_xy: Base.Vector) -> Part.Shape:
    """
    Flattened elliptical disc (wedge bond), long axis aligned with dir_xy.
    Base sits at center.z.

    Built by scaling a unit cylinder in XY — more robust across FreeCAD
    versions than the Part.Ellipse() API.
    """
    # Unit cylinder, then stretch X → long axis, Y → short axis
    cyl = Part.makeCylinder(1.0, wedge_h)
    mat = FreeCAD.Matrix()
    mat.scale(wedge_l / 2.0, wedge_w / 2.0, 1.0)
    solid = cyl.transformGeometry(mat)
    # Rotate long axis to align with incoming wire direction
    angle = math.degrees(math.atan2(dir_xy.y, dir_xy.x))
    solid.rotate(Base.Vector(0, 0, 0), Base.Vector(0, 0, 1), angle)
    solid.translate(center)
    return solid


def _swept_tube(p0: Base.Vector, p1: Base.Vector, r: float,
                loop_height: float) -> Part.Shape:
    """
    BSpline-swept circular tube from p0 to p1 with a parabolic arc.

    Returns a closed *solid* whenever OCCT can build one (the normal case via
    ``makePipeShell(solid=True)``).  Falls back to the manual shell+cap path,
    and finally to the open swept shell, so the caller always gets a shape.
    A solid is strongly preferred because the downstream boolean trims rely on
    a non-zero volume to detect a successful cut.
    """
    z_top = max(p0.z, p1.z) + loop_height
    peak  = Base.Vector((p0.x + p1.x) / 2.0,
                        (p0.y + p1.y) / 2.0, z_top)
    q1    = Base.Vector((p0.x + peak.x) / 2.0,
                        (p0.y + peak.y) / 2.0,
                        (p0.z + peak.z) / 2.0)
    q2    = Base.Vector((peak.x + p1.x) / 2.0,
                        (peak.y + p1.y) / 2.0,
                        (peak.z + p1.z) / 2.0)
    bsp = Part.BSplineCurve()
    bsp.interpolate([p0, q1, peak, q2, p1])
    spine_edge = bsp.toShape()
    spine_wire = Part.Wire([spine_edge])

    t0 = spine_edge.tangentAt(spine_edge.FirstParameter).normalize()
    t1 = spine_edge.tangentAt(spine_edge.LastParameter).normalize()

    circ0   = Part.makeCircle(r, p0, t0)
    profile = Part.Wire([Part.Edge(circ0)])

    # ── Primary: makePipeShell as a closed solid ───────────────────────────
    try:
        solid = spine_wire.makePipeShell([profile], True, False)
        if (solid is not None and not solid.isNull()
                and solid.isValid() and solid.Solids):
            return solid.Solids[0] if len(solid.Solids) == 1 else solid
    except Exception:
        pass

    # ── Fallback: manual shell + end caps ─────────────────────────────────
    try:
        pipe  = spine_wire.makePipe(profile)
        cap0  = Part.Face(profile)
        circ1 = Part.makeCircle(r, p1, t1)
        cap1  = Part.Face(Part.Wire([Part.Edge(circ1)]))
        shell = Part.makeShell(list(pipe.Faces) + [cap0, cap1])
        solid = Part.makeSolid(shell)
        if solid.isValid() and not solid.isNull() and solid.Solids:
            return solid
        return pipe
    except Exception:
        return spine_wire.makePipe(profile)


def _fuse_parts(*parts) -> Part.Shape:
    """
    Try to fuse all *parts* into one solid.
    Collects failures and returns a compound of everything so the caller
    always gets a renderable shape even when OCCT rejects a boolean op.
    """
    accumulated = list(parts[:1])
    current = parts[0]
    for p in parts[1:]:
        try:
            candidate = current.fuse(p)
            if candidate.isValid() and not candidate.isNull():
                current = candidate
            else:
                accumulated.append(p)
        except Exception:
            accumulated.append(p)
    # If every fuse succeeded current == fully fused solid
    if len(accumulated) == 1:
        return current
    # Some parts couldn't be fused — return them all as a compound
    return Part.makeCompound([current] + accumulated[1:])


def _dir_xy(start: Base.Vector, end: Base.Vector) -> Base.Vector:
    """Unit vector from start to end projected onto the XY plane."""
    v = Base.Vector(end.x - start.x, end.y - start.y, 0.0)
    return v.normalize() if v.Length > 1e-6 else Base.Vector(1.0, 0.0, 0.0)


def _spine_point(a_xy: Base.Vector, e_xy: Base.Vector,
                 length: float, t: float, z: float) -> Base.Vector:
    """
    Absolute spine point: start at *a_xy*, move t·length along the horizontal
    unit vector *e_xy*, at absolute height *z*.
    """
    return Base.Vector(a_xy.x + e_xy.x * (length * t),
                       a_xy.y + e_xy.y * (length * t),
                       z)


def _sweep_circle_along(points, r: float) -> Part.Shape:
    """
    Interpolate a smooth BSpline through *points* and sweep a circular
    profile of radius *r* along it.

    Returns a closed solid via ``makePipeShell(solid=True)`` whenever OCCT
    can build one (the normal case).  Falls back to the open swept pipe so the
    caller always gets a shape.  Building a real solid matters because the
    wire is fused with its foot solids afterwards.
    """
    bsp = Part.BSplineCurve()
    bsp.interpolate(points)
    spine_edge = bsp.toShape()
    spine_wire = Part.Wire([spine_edge])

    t0 = spine_edge.tangentAt(spine_edge.FirstParameter).normalize()
    circ0   = Part.makeCircle(r, points[0], t0)
    profile = Part.Wire([Part.Edge(circ0)])

    try:
        solid = spine_wire.makePipeShell([profile], True, False)
        if (solid is not None and not solid.isNull()
                and solid.isValid() and solid.Solids):
            return solid.Solids[0] if len(solid.Solids) == 1 else solid
    except Exception:
        pass
    return spine_wire.makePipe(profile)


def _jedec_trapezoid_points(a_xy: Base.Vector, e_xy: Base.Vector, L: float,
                            z_start: float, z_end: float, peak_z: float,
                            kink_frac: float = 0.125):
    """
    Return the 4 key spine points of a JEDEC-style trapezoidal bond-wire
    profile — the simplified alternative to the multi-point BSpline loop:

        start ────────────────────── peak
                                         \\  flat top, length = kink_frac · L
                                          kink
                                           |   vertical drop
                                          end

    One long straight rise from *start* to *peak* (at height *peak_z*), a
    short flat segment of horizontal length ``kink_frac * L`` to *kink* (same
    height, same XY as *end*), then a straight vertical drop into *end*.
    ``kink_frac`` defaults to 1/8, matching the "d/8" proportion of the
    reference JEDEC wire-bond profile diagram.
    """
    t_peak = max(0.0, 1.0 - kink_frac)
    return [
        _spine_point(a_xy, e_xy, L, 0.00,   z_start),
        _spine_point(a_xy, e_xy, L, t_peak, peak_z),
        _spine_point(a_xy, e_xy, L, 1.00,   peak_z),
        _spine_point(a_xy, e_xy, L, 1.00,   z_end),
    ]


def _sweep_circle_along_polyline(points, r: float) -> Part.Shape:
    """
    Sweep a circular profile of radius *r* along a straight-segment polyline
    through *points* — no BSpline smoothing.  Used for the simplified/JEDEC
    wire profile, whose shape is defined by sharp corner points rather than a
    smooth loop.  Consecutive duplicate points (zero-length segments, e.g. the
    vertical stub inserted for a 'cut' wedge end) are merged since OCCT
    rejects degenerate edges.

    Uses a rounded-corner sweep transition so OCCT does not self-intersect at
    the direction change — a slightly filleted kink instead of a mathematically
    sharp corner, and far more robust than forcing a sharp join.
    """
    pts = [points[0]]
    for p in points[1:]:
        if (p - pts[-1]).Length > 1e-9:
            pts.append(p)
    if len(pts) < 2:
        raise ValueError("_sweep_circle_along_polyline: fewer than 2 distinct points")

    edges = [Part.LineSegment(pts[i], pts[i + 1]).toShape()
             for i in range(len(pts) - 1)]
    spine_wire = Part.Wire(edges)

    first_edge = spine_wire.Edges[0]
    t0 = first_edge.tangentAt(first_edge.FirstParameter).normalize()
    circ0   = Part.makeCircle(r, pts[0], t0)
    profile = Part.Wire([Part.Edge(circ0)])

    try:
        solid = spine_wire.makePipeShell([profile], True, False, 2)   # 2 = round corner
        if (solid is not None and not solid.isNull()
                and solid.isValid() and solid.Solids):
            return solid.Solids[0] if len(solid.Solids) == 1 else solid
    except Exception:
        pass
    try:
        solid = spine_wire.makePipeShell([profile], True, False)
        if (solid is not None and not solid.isNull()
                and solid.isValid() and solid.Solids):
            return solid.Solids[0] if len(solid.Solids) == 1 else solid
    except Exception:
        pass
    return spine_wire.makePipe(profile)


# ── public entry point ─────────────────────────────────────────────────────────

def _cut_below_contact(shape: Part.Shape, p_end: Base.Vector,
                       p_other: Base.Vector, z_cut: float) -> Part.Shape:
    """
    Clip everything in *shape* below *z_cut* on *p_end*'s half of the bond
    (split at the perpendicular bisector between p_end and p_other).

    Used to terminate a *wedge* end by cutting the swept tube flat at the pad
    plane instead of fusing a separate wedge solid — the classic wedge/stitch
    look (the wire itself flattened against the pad).  The cutter is an oriented
    half-space box that covers p_end's whole half and stops at the midpoint, so
    it never touches the other end (which may be a ball at a different height).
    """
    _DEPTH = 1000.0
    try:
        bb = shape.BoundBox
        reach = math.hypot(bb.XLength, bb.YLength) + 5.0
        dx, dy = p_other.x - p_end.x, p_other.y - p_end.y
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            box = Part.makeBox(
                2 * reach, 2 * reach, _DEPTH,
                Base.Vector(p_end.x - reach, p_end.y - reach, z_cut - _DEPTH))
        else:
            ux, uy = dx / dist, dy / dist
            angle  = math.degrees(math.atan2(uy, ux))
            length = reach + dist / 2.0
            box = Part.makeBox(length, 2 * reach, _DEPTH)
            box.translate(Base.Vector(-reach, -reach, z_cut - _DEPTH))
            box.rotate(Base.Vector(0, 0, 0), Base.Vector(0, 0, 1), angle)
            box.translate(Base.Vector(p_end.x, p_end.y, 0.0))
        result = shape.cut(box)
        if (result is not None and result.isValid()
                and not result.isNull() and result.Solids):
            return _drop_slivers(result)
    except Exception as e:
        FreeCAD.Console.PrintWarning(
            f"[wirebond] wedge flat-cut at z={z_cut:.4f} failed: {e}\n")
    return shape


def create_bond_wire_3d(start: Base.Vector, end: Base.Vector,
                        config: dict) -> Part.Shape:
    """
    Build a realistic 3-D bond-wire solid between *start* and *end*.

    Construction (no boolean cutting)
    ---------------------------------
    The bond feet are created sitting *on* the pad surfaces (their base at the
    contact-point Z), and the wire loop is swept so that its spine — and hence
    the whole tube — always stays at or above the pad surface.  Because the
    geometry never reaches below the pads in the first place, no trimming /
    boolean cut against the pads is needed (those cuts were the source of the
    previous penetration and "partial cut" problems).

    Bond type is read from config['bond_type']:
      'Ball-Wedge'  — ball bond at *start* (vertical neck, loop apex near the
                      ball) and a flat wedge/stitch at *end* (shallow landing),
                      matching the classic ball-stitch profile.
      'Wedge-Wedge' — flat wedge at both ends, wire entering/leaving at a
                      shallow angle (aluminium wedge bonding).

    Proportions scale with config['diameter'].  Falls back to a plain swept
    tube, then a straight line, on OCCT failure.
    """
    bond_type    = config.get("bond_type", "Ball-Wedge")
    wedge_style  = config.get("wedge_style", "cut")   # 'cut' or 'solid'
    wire_profile = config.get("wire_profile", "spline")  # 'spline' or 'jedec'
    loop_height  = float(config.get("loop_height", 0.3))
    d            = float(config.get("diameter",    0.025))
    r            = d / 2.0

    FreeCAD.Console.PrintMessage(
        f"[wirebond] create_bond_wire_3d {_GEOM_VERSION}: "
        f"type={bond_type} wedge_style={wedge_style} profile={wire_profile} "
        f"d={d:.4f} loop={loop_height:.4f}\n"
    )

    A = Base.Vector(start)
    B = Base.Vector(end)
    e_xy = _dir_xy(A, B)
    L = math.hypot(B.x - A.x, B.y - A.y)
    if L < 1e-6:
        L = max(d, 1e-3)          # degenerate stacked contacts — avoid /0
    a_xy = Base.Vector(A.x, A.y, 0.0)

    zA, zB = A.z, B.z
    z_hi = max(zA, zB)

    # Apex height above the higher pad.  A real bond loop is a modest fraction
    # of its span; cap the requested loop_height to 35 % of the bond length so
    # an over-large setting (e.g. a 2 mm loop on a 2.5 mm bond) cannot produce
    # a near-vertical strand.  Never let it collapse below 2·d for thick wires.
    H_req = max(loop_height, 2.0 * d)
    H     = max(min(H_req, 0.35 * L), 2.0 * d)
    if H < H_req - 1e-6:
        FreeCAD.Console.PrintWarning(
            f"[wirebond] loop_height {loop_height:.3f} mm capped to {H:.3f} mm "
            f"for a {L:.3f} mm span (≤35 % of span).\n"
        )

    # Foot dimensions (multiples of wire diameter d)
    b_r, b_h      = d * 1.1, d * 0.75            # ball: equatorial r, height
    w_l, w_w, w_h = d * 3.2, d * 1.6, d * 0.45   # wedge: length, width, height

    # Tube-centre clearance at a landing so the tube bottom (= centre − r)
    # stays above the pad surface.
    clr = r + 0.10 * d

    cut_wedge = (wedge_style != "solid")

    # 'cut' wedge style flattens the swept tube against the pad with a flat cut
    # at the contact Z.  For that cut to leave a *complete* contact face (rather
    # than a half-open tube scoop hovering over the pad) the spine must dip a
    # little BELOW the pad so the tube fully crosses the contact plane; the cut
    # at the contact Z then trims it flush.  'pen' is that penetration depth —
    # at least a tube radius so the terminal cross-section is wholly below the
    # cut plane and the flat face comes out complete.
    pen = max(1.5 * d, r + 0.02)

    # Reference Z for an approach/landing point that must stay *above* the pad.
    wedge_end_z = lambda z: (z if cut_wedge else z + clr)
    # Reference Z for the true wire terminal: dips below the pad for a 'cut'
    # end (so the flat cut is full), floats a tube-radius above for 'solid'.
    term_z      = lambda z: (z - pen if cut_wedge else z + clr)

    try:
        if bond_type == "Wedge-Wedge":
            # ── Wedge at both ends — shallow entry/exit ────────────────────
            if wire_profile == "jedec":
                pts = _jedec_trapezoid_points(
                    a_xy, e_xy, L, wedge_end_z(zA), wedge_end_z(zB), z_hi + H)
                sweep_fn = _sweep_circle_along_polyline
            else:
                pts = [
                    _spine_point(a_xy, e_xy, L, 0.00, wedge_end_z(zA)),
                    _spine_point(a_xy, e_xy, L, 0.20, z_hi + 0.80 * H),
                    _spine_point(a_xy, e_xy, L, 0.50, z_hi + H),
                    _spine_point(a_xy, e_xy, L, 0.80, z_hi + 0.80 * H),
                    _spine_point(a_xy, e_xy, L, 1.00, wedge_end_z(zB)),
                ]
                sweep_fn = _sweep_circle_along
            if cut_wedge:
                # Dip straight *down* at each contact XY (a short vertical stub)
                # so the flat cut lands a full face exactly on the contact point
                # — not short of it, which a diagonal descent would cause.
                pts.insert(0, _spine_point(a_xy, e_xy, L, 0.00, term_z(zA)))
                pts.append(   _spine_point(a_xy, e_xy, L, 1.00, term_z(zB)))
            tube = sweep_fn(pts, r)

            if cut_wedge:
                # Flatten the tube on each pad — no separate wedge solids.
                tube = _cut_below_contact(tube, A, B, zA)
                tube = _cut_below_contact(tube, B, A, zB)
                return tube
            footA = _make_wedge_solid(w_l, w_w, w_h, A, e_xy)
            footB = _make_wedge_solid(w_l, w_w, w_h, B, e_xy)
            return _fuse_parts(footA, tube, footB)

        else:
            # ── Ball-Wedge (ball-stitch) ───────────────────────────────────
            # Ball at A with a vertical neck; loop apex near the ball; long,
            # gentle descent to the stitch/wedge at B.
            footA  = _make_oblate_ball(b_r, b_h, A)
            z_neck = zA + b_h * 0.80            # tube exits near the ball top

            if wire_profile == "jedec":
                pts = _jedec_trapezoid_points(
                    a_xy, e_xy, L, z_neck, wedge_end_z(zB), z_hi + H)
                sweep_fn = _sweep_circle_along_polyline
            else:
                pts = [
                    _spine_point(a_xy, e_xy, L, 0.00, z_neck),
                    _spine_point(a_xy, e_xy, L, 0.05, z_neck + 0.55 * H),   # steep neck
                    _spine_point(a_xy, e_xy, L, 0.28, z_hi + H),            # apex near ball
                    _spine_point(a_xy, e_xy, L, 0.62, z_hi + 0.42 * H),
                    _spine_point(a_xy, e_xy, L, 0.87, wedge_end_z(zB) + 0.15 * H),
                    _spine_point(a_xy, e_xy, L, 1.00, wedge_end_z(zB)),     # stitch landing
                ]
                sweep_fn = _sweep_circle_along
            if cut_wedge:
                # Dip straight down at the contact XY (short vertical stub) so
                # the flat cut leaves a full face exactly on the contact point.
                pts.append(_spine_point(a_xy, e_xy, L, 1.00, term_z(zB)))
            tube = sweep_fn(pts, r)

            if cut_wedge:
                # Ball foot stays; the stitch end is the tube cut flat on the
                # pad (no separate wedge solid).  Only the B half is clipped,
                # so the ball at A is untouched.
                wire = _fuse_parts(footA, tube)
                return _cut_below_contact(wire, B, A, zB)
            footB = _make_wedge_solid(w_l, w_w, w_h, B, e_xy)
            return _fuse_parts(footA, tube, footB)

    except Exception as e:
        import traceback
        FreeCAD.Console.PrintError(
            f"[wirebond] Realistic geometry FAILED ({e}) — falling back to a "
            f"plain symmetric tube WITHOUT feet. This is why the wire may look "
            f"like the old version. Traceback:\n" + traceback.format_exc()
        )

    # ── Fallback: plain tube (logged loudly above) ─────────────────────────────
    try:
        return _swept_tube(A, B, r, loop_height)
    except Exception:
        return Part.makeLine(A, B)


# ── geometry helpers ──────────────────────────────────────────────────────────

def _keep_largest_solid(shape: Part.Shape) -> Part.Shape:
    """
    After a boolean cut discard slivers that are less than 1 % of the largest
    solid's volume.  Keeps all significant pieces (ball + wire + wedge) and
    drops only tiny offcuts produced by the pad trimming step.
    """
    solids = shape.Solids
    if not solids:
        return shape
    if len(solids) == 1:
        return solids[0]
    max_vol = max(s.Volume for s in solids)
    kept = [s for s in solids if s.Volume >= max_vol * 0.01]
    if len(kept) == 1:
        return kept[0]
    if len(kept) > 1:
        return Part.makeCompound(kept)
    return max(solids, key=lambda s: s.Volume)


def _drop_slivers(shape: Part.Shape, min_vol: float = 1e-6) -> Part.Shape:
    """
    Return all solids of *shape* whose volume exceeds *min_vol* as a compound,
    discarding only tiny OCCT offcuts.

    Unlike ``_keep_largest_solid`` this keeps *every* significant solid, so a
    bond made of separate ball / tube / wedge pieces (when fusing failed) is
    not reduced to just the largest one after a boolean cut.  Falls back to the
    input shape when it has no solids (e.g. an open shell), so the cut result
    is never silently dropped.
    """
    solids = shape.Solids
    if not solids:
        return shape
    kept = [s for s in solids if s.Volume >= min_vol]
    if not kept:
        return shape
    if len(kept) == 1:
        return kept[0]
    return Part.makeCompound(kept)


# ── contact-point filter ───────────────────────────────────────────────────────

def _is_contact_point(obj) -> bool:
    return getattr(obj, "IsContactPoint", False)


class _ContactPointGate:
    """
    FreeCAD SelectionGate that allows only ContactPoint objects.
    Installed when a wire bonding session starts so that non-CP objects
    receive no hover highlight and cannot be clicked at all.

    FreeCAD 1.x passes the Document / DocumentObject directly; older builds
    pass name strings.  Both cases are handled below.
    """
    def allow(self, doc, obj, sub) -> bool:
        try:
            fc_obj = obj if not isinstance(obj, str) else (
                (FreeCAD.getDocument(doc) if isinstance(doc, str) else doc).getObject(obj)
            )
            return fc_obj is not None and getattr(fc_obj, "IsContactPoint", False)
        except Exception:
            return False


# ── state constants ────────────────────────────────────────────────────────────

class _State:
    IDLE         = "idle"
    AWAIT_FIRST  = "await_first"
    AWAIT_SECOND = "await_second"


# ── main class ─────────────────────────────────────────────────────────────────

class ManualWireBonding:
    """
    Wire bonding session controller with strict ContactPoint-only filter.

    Only objects with IsContactPoint = True are accepted.  Clicking any
    other object clears the selection immediately so the user gets clear
    visual feedback that the click was rejected.
    """

    def __init__(self):
        self.bonds             = []          # list of completed bond dicts
        self.first_cp          = None        # first ContactPoint object
        self.first_pt          = None        # its resolved snap point
        self.doc               = None
        self.config            = None
        self.is_active         = False
        self.state             = _State.IDLE
        self._highlighted      = None
        self._highlighted_orig = None

    # ── session lifecycle ──────────────────────────────────────────────────

    def start_bonding_session(self, config: dict):
        if self.is_active:
            self.cancel_session()

        self.config    = config
        self.bonds     = []
        self.first_cp  = None
        self.first_pt  = None
        self.is_active = True
        self.state     = _State.AWAIT_FIRST
        self.doc       = FreeCAD.activeDocument() or FreeCAD.newDocument("WireBonding")

        FreeCADGui.Selection.addObserver(self)
        FreeCADGui.Selection.addSelectionGate(_ContactPointGate())
        self._set_status("Wire bonding — click the first contact point (die pad)")
        FreeCAD.Console.PrintMessage(
            "Wire bonding started.\n"
            "  Step 1: click a ContactPoint on the die.\n"
            "  Step 2: click a ContactPoint on the leadframe.\n"
            "  Repeat. Use 'Finish Wire Bonding' when done.\n"
        )

    def finish_session(self) -> int:
        if not self.is_active:
            return 0
        self._teardown()
        count = len(self.bonds)
        FreeCAD.Console.PrintMessage(f"Wire bonding finished — {count} bond(s).\n")
        self._report()
        return count

    def cancel_session(self):
        self._teardown()
        self.bonds    = []
        self.first_cp = None
        self.first_pt = None
        FreeCAD.Console.PrintMessage("Wire bonding cancelled.\n")

    def _teardown(self):
        self.is_active = False
        self.state     = _State.IDLE
        self._clear_highlight()
        try:
            FreeCADGui.Selection.removeSelectionGate()
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"removeSelectionGate: {e}\n")
        try:
            FreeCADGui.Selection.removeObserver(self)
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"removeObserver: {e}\n")
        self._set_status("")

    # ── FreeCAD Selection observer callbacks ───────────────────────────────

    def setPreselection(self, doc, obj_name, sub):
        """Highlight ContactPoint objects green on hover; ignore everything else."""
        if not self.is_active:
            return
        try:
            obj = FreeCAD.getDocument(doc).getObject(obj_name)
            if obj is None or obj is self._highlighted:
                return
            if _is_contact_point(obj):
                self._clear_highlight()
                self._highlighted      = obj
                self._highlighted_orig = obj.ViewObject.LineColor
                obj.ViewObject.LineColor = _COLOR_SNAP_VALID
        except Exception:
            pass

    def removePreselection(self, doc, obj_name, sub):
        if not self.is_active:
            return
        self._clear_highlight()

    def addSelection(self, doc, obj_name, sub, pos):
        """
        Accept only ContactPoint objects.
        Any other click is immediately cleared from the FreeCAD selection
        so the 3D view gives clear visual feedback of the rejection.
        """
        if not self.is_active or not self.config:
            return
        try:
            obj = FreeCAD.getDocument(doc).getObject(obj_name)
            if obj is None:
                return

            # ── Reject non-ContactPoint objects ────────────────────────────
            if not _is_contact_point(obj):
                FreeCADGui.Selection.clearSelection()
                self._set_status(
                    "Wire bonding — only ContactPoint markers can be selected"
                )
                FreeCAD.Console.PrintWarning(
                    f"'{obj.Name}' is not a ContactPoint — skipped.\n"
                    "Select a ContactPoint marker (blue dot on leadframe or orange dot on die).\n"
                )
                return

            # ── First pick ─────────────────────────────────────────────────
            if self.state == _State.AWAIT_FIRST:
                self.first_cp = obj
                self.first_pt = resolve_snap_point(obj)
                self._create_temp_marker(self.first_pt)
                self.state = _State.AWAIT_SECOND
                self._set_status(
                    f"First point: {obj.Label} — now click the second contact point"
                )
                FreeCAD.Console.PrintMessage(
                    f"  First CP: {obj.Name}  "
                    f"pos=({self.first_pt.x:.3f}, {self.first_pt.y:.3f}, {self.first_pt.z:.3f})\n"
                )

            # ── Second pick ────────────────────────────────────────────────
            elif self.state == _State.AWAIT_SECOND:
                if obj is self.first_cp:
                    FreeCAD.Console.PrintWarning(
                        "Same contact point selected — pick a different one.\n"
                    )
                    return
                second_pt = resolve_snap_point(obj)
                self._place_wire(self.first_pt, second_pt, self.first_cp, obj)
                # Reset for the next bond
                self.first_cp = None
                self.first_pt = None
                self.state    = _State.AWAIT_FIRST
                self._set_status("Wire bonding — click the first contact point (die pad)")

        except Exception as e:
            FreeCAD.Console.PrintError(f"Wire bonding error: {e}\n")
            import traceback
            FreeCAD.Console.PrintError(traceback.format_exc())

    # ── wire placement ─────────────────────────────────────────────────────

    def _locate_pad_solid(self, cp, cp_pos: Base.Vector):
        """
        Return the specific solid (pad/lead/board face) that this CP is on.

        Priority:
        1. Closest sub-solid of cp.SourceObject.
        2. Document scan: smallest solid that contains cp_pos within 0.5 mm.
           Prefers geometrically tighter fits so a specific copper pad wins
           over the whole PCB substrate.
        """
        # ── Primary: SourceObject reference ───────────────────────────────
        src_name = getattr(cp, "SourceObject", None)
        if src_name:
            src_obj = self.doc.getObject(src_name)
            if (src_obj is not None
                    and hasattr(src_obj, "Shape")
                    and src_obj.Shape.isValid()):
                solids = src_obj.Shape.Solids
                if solids:
                    def _dist(s):
                        c = s.BoundBox.Center
                        return ((c.x - cp_pos.x)**2
                                + (c.y - cp_pos.y)**2
                                + (c.z - cp_pos.z)**2) ** 0.5
                    return min(solids, key=_dist)
                return src_obj.Shape

        # ── Fallback: scan document for solid that contains cp_pos ────────
        tol = 0.5   # 500 µm — catches CPs placed slightly inside a body
        best        = None
        best_vol    = float("inf")
        for obj in self.doc.Objects:
            if not hasattr(obj, "Shape") or not obj.Shape.isValid():
                continue
            if getattr(obj, "IsContactPoint", False):
                continue
            if obj.Name.startswith("BondWire_") or obj.Name.startswith("_Snap"):
                continue
            for s in (obj.Shape.Solids or []):
                if s.Volume < 1e-9:
                    continue
                try:
                    if s.isInside(cp_pos, tol, True) and s.Volume < best_vol:
                        best_vol = s.Volume
                        best     = s
                except Exception:
                    pass
        return best

    def _surface_z_at(self, solid: Part.Shape, cp_pos: Base.Vector) -> float:
        """
        Return the z coordinate of *solid*'s upward-facing surface at the
        (cp_pos.x, cp_pos.y) location.

        Finds the closest upward-facing face and projects (cp_pos.x, cp_pos.y)
        onto its plane.  This is correct for both flat horizontal pads and
        tilted surfaces (e.g. angled PCB boards).
        Falls back to BoundBox.ZMax when no upward face is found.
        """
        try:
            up_faces = [f for f in solid.Faces if f.normalAt(0, 0).z > 0.3]
            if not up_faces:
                return solid.BoundBox.ZMax
            # Closest upward face in XY distance to the CP
            def _xy_dist(f):
                c = f.CenterOfMass
                return (c.x - cp_pos.x)**2 + (c.y - cp_pos.y)**2
            face = min(up_faces, key=_xy_dist)
            n    = face.normalAt(0, 0)
            c    = face.CenterOfMass
            if abs(n.z) > 0.1:
                # Plane eq: n·(P − c) = 0  →  z = cz − (nx(x−cx) + ny(y−cy)) / nz
                return c.z - (n.x * (cp_pos.x - c.x)
                              + n.y * (cp_pos.y - c.y)) / n.z
            return face.BoundBox.ZMax
        except Exception:
            return solid.BoundBox.ZMax

    def _trim_wire_at_pads(self, shape: Part.Shape, cp1, cp2) -> Part.Shape:
        """
        Cut the wire body at each endpoint so it does not penetrate the pad.

        Per contact point:

        Stage 1 — Exact solid cut
          Cut the wire against the specific pad sub-solid.  OCCT handles any
          pad orientation (horizontal, tilted PCB, angled lead) correctly in
          a boolean difference.  This is the primary and most accurate path.

        Stage 2 — Z-plane fallback
          If the solid cut returns an invalid/null result, fall back to a flat
          box cutter.  z_cut is derived by projecting (cp_pos.x, cp_pos.y)
          onto the pad face plane — giving the correct surface z even for
          tilted bodies — rather than using BoundBox.ZMax (which for a tilted
          PCB is the far corner, far above the contact point).
          The XY footprint is capped at 15 mm per side; large bodies fall back
          to a 2 mm radius around the contact point so the cutter never spans
          the whole scene.
        """
        _PAD_MAX_SIDE  = 15.0   # mm — above this the source body is "too large"
        _FALLBACK_R    = 2.0    # mm — fixed-radius box when source is large/absent
        _Z_EPS         = 0.005  # mm — tiny lift to avoid coplanar-face degeneracy
        _DEPTH         = 1000.0 # mm — depth of z-plane cutter below z_cut
        _MARGIN        = 0.05   # mm — XY inflation on the pad bounding box

        for cp in (cp1, cp2):
            cp_pos    = resolve_snap_point(cp)
            pad_solid = self._locate_pad_solid(cp, cp_pos)
            if pad_solid is None:
                FreeCAD.Console.PrintWarning(
                    f"Wire trim: no pad solid found for '{cp.Name}' — skipping.\n"
                )
                continue

            # ── Stage 1: Exact solid cut ───────────────────────────────────
            trimmed = None
            try:
                result = shape.cut(pad_solid)
                if result.isValid() and not result.isNull():
                    candidate = _keep_largest_solid(result)
                    if candidate.Volume > 1e-9:
                        trimmed = candidate
            except Exception:
                pass

            if trimmed is not None:
                shape = trimmed
                continue

            # ── Stage 2: Z-plane fallback ─────────────────────────────────
            # Project (cp_pos.x, cp_pos.y) onto the pad face to get the
            # correct surface z even when the pad is tilted.
            z_cut = self._surface_z_at(pad_solid, cp_pos) + _Z_EPS

            bb = pad_solid.BoundBox
            if bb.XLength <= _PAD_MAX_SIDE and bb.YLength <= _PAD_MAX_SIDE:
                xmin, ymin = bb.XMin, bb.YMin
                xlen, ylen = bb.XLength, bb.YLength
            else:
                # Source body too large → fixed radius around the CP
                xmin, ymin = cp_pos.x - _FALLBACK_R, cp_pos.y - _FALLBACK_R
                xlen, ylen = 2 * _FALLBACK_R,         2 * _FALLBACK_R

            try:
                box = Part.makeBox(
                    xlen + 2 * _MARGIN,
                    ylen + 2 * _MARGIN,
                    _DEPTH,
                    Base.Vector(xmin - _MARGIN, ymin - _MARGIN, z_cut - _DEPTH),
                )
                result = shape.cut(box)
                if result.isValid() and not result.isNull():
                    candidate = _keep_largest_solid(result)
                    if candidate.Volume > 1e-9:
                        shape = candidate
                    else:
                        FreeCAD.Console.PrintWarning(
                            f"Z-plane trim at '{cp.Name}': result empty "
                            f"(z_cut={z_cut:.4f}).\n"
                        )
                else:
                    FreeCAD.Console.PrintWarning(
                        f"Z-plane trim at '{cp.Name}': cut invalid/null "
                        f"(z_cut={z_cut:.4f}).\n"
                    )
            except Exception as e:
                FreeCAD.Console.PrintWarning(
                    f"Z-plane trim at pad '{cp.Name}' failed: {e}\n"
                )

        return shape

    def _clip_wire_below_contacts(self, shape: Part.Shape, cp1, cp2) -> Part.Shape:
        """
        Remove every part of the wire body that lies below the contact-point
        reference plane.

        The contact points define the surface the wire sits on: their stored
        Z value is the bottom of the wire foot.  Anything beneath that plane is
        the part of the ball/wedge/tube that overhangs a small pad and dips
        into the lower substrate/die underneath — exactly what should be cut.

        Strategy
        --------
        For each endpoint a half-space cutter is built that removes *all*
        material below that endpoint's contact-Z, over the endpoint's entire
        half of the bond (split at the perpendicular bisector between the two
        contacts).  The cutter is:

          * a box whose top face is at the contact-Z,
          * extending far downward,
          * covering the full XY extent of the wire on that endpoint's side
            (so no penetrating section is ever missed — this is what fixes the
            previous "only partially cut" behaviour), and
          * oriented along the bond axis so it stops exactly at the midpoint
            and never touches the other endpoint's (possibly lower) foot.

        This is a pure horizontal-plane clip, so it works whether or not a pad
        solid was found and regardless of the foot overhanging the pad. It only
        ever removes material.  Order of cp1/cp2 does not matter.
        """
        _Z_EPS = 0.002      # mm — hair of material so the cut face is never
                            #      exactly coplanar with the wire base
        _DEPTH = 1000.0     # mm — how far below the cut plane the cutter reaches

        p1 = resolve_snap_point(cp1)
        p2 = resolve_snap_point(cp2)

        # Generous lateral/longitudinal reach: must cover the whole wire on
        # the endpoint's side.  Derived from the wire bounding box diagonal so
        # it is always large enough regardless of wire size or diameter.
        bb = shape.BoundBox
        reach = math.hypot(bb.XLength, bb.YLength) + 5.0   # mm, + safety margin

        dx, dy = p2.x - p1.x, p2.y - p1.y
        dist = math.hypot(dx, dy)

        for cp, cp_pos, sign in ((cp1, p1, +1.0), (cp2, p2, -1.0)):
            z_cut = cp_pos.z + _Z_EPS
            try:
                if dist < 1e-6:
                    # Degenerate: contacts coincide in XY → simple full box.
                    box = Part.makeBox(
                        2 * reach, 2 * reach, _DEPTH,
                        Base.Vector(cp_pos.x - reach,
                                    cp_pos.y - reach,
                                    z_cut - _DEPTH),
                    )
                else:
                    # Unit vector from this foot toward the other foot.
                    ux, uy = sign * dx / dist, sign * dy / dist
                    angle  = math.degrees(math.atan2(uy, ux))
                    # Local box: x' along the bond axis, from -reach (behind the
                    # foot) up to dist/2 (the midpoint); y' full width; z' down.
                    length = reach + dist / 2.0
                    box = Part.makeBox(length, 2 * reach, _DEPTH)
                    # Position in local frame, then rotate onto the bond axis
                    # and translate to the contact point.
                    box.translate(Base.Vector(-reach, -reach, z_cut - _DEPTH))
                    box.rotate(Base.Vector(0, 0, 0), Base.Vector(0, 0, 1), angle)
                    box.translate(Base.Vector(cp_pos.x, cp_pos.y, 0.0))

                result = shape.cut(box)
                if result is not None and result.isValid() and not result.isNull():
                    shape = _drop_slivers(result)
                else:
                    FreeCAD.Console.PrintWarning(
                        f"Contact-Z clip at '{cp.Name}': cut invalid/null "
                        f"(z_cut={z_cut:.4f}) — skipped.\n"
                    )
            except Exception as e:
                FreeCAD.Console.PrintWarning(
                    f"Contact-Z clip at '{cp.Name}' failed: {e}\n"
                )

        return shape

    def _subtract_obstacles(self, shape: Part.Shape, cp1, cp2) -> Part.Shape:
        """
        Subtract every other solid body the wire overlaps (PCB substrate,
        encapsulant, vias, chips, pads) from the wire solid, so any part of the
        wire embedded inside another body is removed.

        Why this and not a Z-plane clip:
        --------------------------------
        The wire is built between the two contact points and never goes below
        their Z.  But the bodies it visually pierces (the PCB block, the GDS
        chip, vias) have their *own* top surfaces at a different Z than the
        contact points, so a contact-Z plane cannot remove the embedded part.
        Subtracting the real obstacle solids removes exactly the intersection —
        independent of how the surfaces are positioned.

        This is robust now because:
          * the wire is a real solid (``makePipeShell(solid=True)``), so the
            boolean has a non-zero volume to work with;
          * we subtract *all* overlapping obstacle solids, not just one thin
            pad, so there is no "partial cut" left over;
          * obstacles are pre-filtered by bounding-box overlap and subtracted
            one by one (sequential cuts are far more reliable than one giant
            fused tool), each guarded individually.

        Contact-point markers and other bond wires are never subtracted.
        Note: a wire end that floats *above* every body (a contact point placed
        in mid-air) has nothing to subtract — that is a contact-placement issue,
        not something a cut can fix.
        """
        try:
            wire_bb = shape.BoundBox
        except Exception:
            return shape

        obstacles = []
        for obj in self.doc.Objects:
            if getattr(obj, "IsContactPoint", False):
                continue
            name = obj.Name
            if (name.startswith("BondWire_") or name.startswith("_Snap")
                    or name.startswith("ContactPoint")):
                continue
            sh = getattr(obj, "Shape", None)
            if sh is None:
                continue
            try:
                if not sh.isValid() or sh.isNull():
                    continue
                for s in (sh.Solids or []):
                    if s.Volume < 1e-9:
                        continue
                    # Bounding-box overlap pre-filter — only bodies that could
                    # actually touch the wire are considered.  Manual AABB test
                    # (the BoundBox.intersect/intersected API differs between
                    # FreeCAD versions).
                    b = s.BoundBox
                    if (b.XMin <= wire_bb.XMax and b.XMax >= wire_bb.XMin and
                            b.YMin <= wire_bb.YMax and b.YMax >= wire_bb.YMin and
                            b.ZMin <= wire_bb.ZMax and b.ZMax >= wire_bb.ZMin):
                        obstacles.append((name, s))
            except Exception:
                continue

        if not obstacles:
            return shape

        out = shape
        cut_count = 0
        for name, solid in obstacles:
            try:
                result = out.cut(solid)
                if (result is not None and result.isValid()
                        and not result.isNull() and result.Solids):
                    # Only accept the cut if it actually changed something and
                    # left a meaningful body (avoid losing the wire to a failed
                    # boolean that returns near-empty).
                    trimmed = _drop_slivers(result)
                    if trimmed.Volume > 1e-9 and trimmed.Volume < out.Volume - 1e-9:
                        out = trimmed
                        cut_count += 1
            except Exception as e:
                FreeCAD.Console.PrintWarning(
                    f"[wirebond] obstacle cut against '{name}' failed: {e}\n"
                )

        FreeCAD.Console.PrintMessage(
            f"[wirebond] subtract_obstacles: {len(obstacles)} candidate solids, "
            f"{cut_count} actually trimmed the wire.\n"
        )
        return out

    def _place_wire(self, start: Base.Vector, end: Base.Vector, cp1, cp2):
        """Create one bond wire in its own undo transaction."""
        doc = self.doc
        doc.openTransaction("Place Bond Wire")
        try:
            # Build the wire foot-first (no penetration below the contact Z),
            # then subtract any real bodies it is embedded in (PCB, encapsulant,
            # vias, chips, pads) so nothing sticks through a surface.
            shape    = create_bond_wire_3d(start, end, self.config)
            shape    = self._subtract_obstacles(shape, cp1, cp2)
            idx      = len(self.bonds) + 1
            wire_obj = doc.addObject("Part::Feature", f"BondWire_{idx:03d}")
            wire_obj.Shape = shape

            wire_obj.ViewObject.ShapeColor = _COLOR_WIRE
            wire_obj.ViewObject.LineColor  = _COLOR_WIRE
            wire_obj.ViewObject.LineWidth  = 2

            def _prop(ptype, name, grp, desc):
                if not hasattr(wire_obj, name):
                    wire_obj.addProperty(ptype, name, grp, desc)

            _prop("App::PropertyVector", "StartPoint",  "Wirebond", "First contact point position")
            _prop("App::PropertyVector", "EndPoint",    "Wirebond", "Second contact point position")
            _prop("App::PropertyString", "StartCP",     "Wirebond", "First ContactPoint object name")
            _prop("App::PropertyString", "EndCP",       "Wirebond", "Second ContactPoint object name")
            _prop("App::PropertyString", "NetName",     "Wirebond", "Net identifier")
            _prop("App::PropertyLength", "WireLength",  "Wirebond", "Wire arc length (mm)")

            wire_obj.StartPoint = start
            wire_obj.EndPoint   = end
            wire_obj.StartCP    = cp1.Name
            wire_obj.EndCP      = cp2.Name
            wire_obj.NetName    = f"Net_{idx:03d}"
            # Use straight-line distance as a meaningful arc-length approximation.
            # shape.Length on a swept solid returns total edge length (all profile
            # circles included), which is not the wire arc length.
            wire_obj.WireLength = (start - end).Length

            doc.commitTransaction()
            doc.recompute()

            self.bonds.append({
                "cp1": cp1, "cp2": cp2,
                "start": start, "end": end,
                "wire": wire_obj,
            })

            # Update session record with the full cumulative bond list
            session_manager.record_action("wirebond_placements", {
                "config": self.config,
                "bonds": [
                    {
                        "start":    [b["start"].x, b["start"].y, b["start"].z],
                        "end":      [b["end"].x,   b["end"].y,   b["end"].z],
                        "start_cp": b["cp1"].Name,
                        "end_cp":   b["cp2"].Name,
                        "net_name": getattr(b["wire"], "NetName", f"Net_{j+1:03d}"),
                    }
                    for j, b in enumerate(self.bonds)
                ],
            })

            FreeCAD.Console.PrintMessage(
                f"  Bond {idx:03d}: {cp1.Name} -> {cp2.Name}  "
                f"length={shape.Length:.3f} mm\n"
            )

            # Refresh the Contact Point Browser so newly connected CPs are
            # greyed out and the netlist row appears immediately.
            try:
                from compat import QtWidgets as _QW
                mw = FreeCADGui.getMainWindow()
                panel = mw.findChild(_QW.QDockWidget, "ContactPointPanel")
                if panel is not None:
                    panel.populate()
            except Exception:
                pass

        except Exception as e:
            doc.abortTransaction()
            FreeCAD.Console.PrintError(f"Wire placement failed: {e}\n")

    # ── helpers ────────────────────────────────────────────────────────────

    def _create_temp_marker(self, pos: Base.Vector):
        """Green sphere at the first pick; auto-removed after 3 s."""
        try:
            from compat import QtCore
            m = self.doc.addObject("Part::Sphere", "_SnapMarker")
            m.Radius = 0.06
            # Must assign a new Placement object — modifying .Base in-place
            # only changes a Python copy and has no effect on the actual object.
            m.Placement = FreeCAD.Placement(pos, FreeCAD.Rotation(0, 0, 0, 1))
            m.ViewObject.ShapeColor = _COLOR_SNAP_VALID
            m.ViewObject.Transparency = 20
            # Lightweight viewport refresh instead of full doc.recompute() —
            # a sphere marker does not depend on any other shape, so a full
            # OCCT rebuild of the entire document is unnecessary here.
            try:
                import FreeCADGui as _Gui
                _Gui.updateGui()
            except Exception:
                self.doc.recompute()
            QtCore.QTimer.singleShot(3000, lambda: self._remove_marker(m))
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"Snap marker: {e}\n")

    def _remove_marker(self, marker):
        try:
            if self.doc and marker in self.doc.Objects:
                self.doc.removeObject(marker.Name)
                # Lightweight refresh — marker removal does not require
                # recomputing the full document shape graph.
                try:
                    import FreeCADGui as _Gui
                    _Gui.updateGui()
                except Exception:
                    self.doc.recompute()
        except Exception:
            pass

    def _clear_highlight(self):
        if self._highlighted is not None:
            try:
                self._highlighted.ViewObject.LineColor = self._highlighted_orig
            except Exception:
                pass
            self._highlighted      = None
            self._highlighted_orig = None

    @staticmethod
    def _set_status(msg: str):
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            if hasattr(view, "setStatusBarMessage"):
                view.setStatusBarMessage(msg)
        except Exception:
            pass

    def _report(self):
        if not self.bonds:
            FreeCAD.Console.PrintMessage("No bonds were created.\n")
            return
        total = sum((b["start"] - b["end"]).Length for b in self.bonds)
        lines = [
            f"  Bond {i+1:03d}: {b['cp1'].Name} -> {b['cp2'].Name}"
            f"  {(b['start'] - b['end']).Length:.3f} mm"
            for i, b in enumerate(self.bonds)
        ]
        FreeCAD.Console.PrintMessage(
            "=== Wire Bonding Report ===\n"
            + "\n".join(lines)
            + f"\n  Total: {len(self.bonds)} bonds, {total:.3f} mm wire\n"
        )


# Module-level singleton shared across all WirebondCommand instances.
manual_bonder = ManualWireBonding()
