# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The 2-D working frame a trace is routed in — flat or curved.

The router's geometry (posture heads, copper outlines, walk-around) is all
2-D. Originally that 2-D space was simply world XY, which locked routing to
horizontal faces: a side wall or a moulded package flank could not be routed
on at all. This module supplies the mapping between a face and a 2-D frame,
so exactly the same routing logic works on any face of a 3-D body.

The frame is the face's own parameter space, scaled to millimetres:

    2-D coords = (u * u_scale, v * v_scale)      3-D point = face.valueAt(u, v)

That one formula covers both cases, and is EXACT — a true isometry — for the
two surface families that actually matter here:

  * planes, whose parametrisation is already unit-speed (u_scale = v_scale = 1),
  * cylinders, where u is an angle and v a height, so u_scale = radius and
    v_scale = 1 make the unrolled frame metrically exact.

Both are developable, which is why a flat 2-D route maps onto them without
distortion. On a genuinely double-curved surface (a sphere, a filleted
corner) no such isometry exists, so the scale factors are taken at the middle
of the parameter domain and lengths/angles are approximate away from there —
see `distortion()`, which reports how bad the approximation is so callers can
warn rather than silently mislead.

Seams are handled explicitly: on a periodic surface (a full cylinder) the u
parameter wraps, so `to_2d` takes a *near* reference and returns the
representative closest to it. Without that, a trace crossing the seam jumps
the whole way around the body.
"""

import math

import FreeCAD
import Part


class SurfaceFrame:
    """2-D routing frame for one face. See the module docstring."""

    def __init__(self, face):
        self.face = face
        self.surface = face.Surface
        self.is_planar = isinstance(self.surface, Part.Plane)

        u0, u1, v0, v1 = face.ParameterRange
        self.u_range = (u0, u1)
        self.v_range = (v0, v1)

        self.u_scale, self.v_scale = self._scale_factors(u0, u1, v0, v1)

        # Planar fast path. A plane's parametrisation is an affine frame, so
        # mapping is two dot products — no need to call Surface.parameter()
        # per point, which dominated obstacle collection (7 s vs 0.5 s for a
        # board's worth of copper outlines).
        self._axes = None
        self._normal_const = None
        if self.is_planar:
            try:
                o = face.valueAt(0.0, 0.0)
                ux = face.valueAt(1.0, 0.0) - o
                vx = face.valueAt(0.0, 1.0) - o
                if ux.Length > 1e-12 and vx.Length > 1e-12:
                    self._axes = (o,
                                  FreeCAD.Vector(ux).normalize(),
                                  FreeCAD.Vector(vx).normalize())
                    try:
                        self._normal_const = FreeCAD.Vector(
                            face.normalAt(0.0, 0.0)).normalize()
                    except Exception:
                        n = self._axes[1].cross(self._axes[2])
                        if n.Length > 1e-12:
                            self._normal_const = FreeCAD.Vector(n).normalize()
            except Exception:
                self._axes = None

        self.u_periodic = bool(self._safe(self.surface.isUPeriodic, False))
        self.v_periodic = bool(self._safe(self.surface.isVPeriodic, False))
        self.u_period = float(self._safe(self.surface.UPeriod, 0.0)) if self.u_periodic else 0.0
        self.v_period = float(self._safe(self.surface.VPeriod, 0.0)) if self.v_periodic else 0.0

    # ── construction helpers ──────────────────────────────────────────────

    @staticmethod
    def _safe(fn, default):
        try:
            return fn()
        except Exception:
            return default

    def _scale_factors(self, u0, u1, v0, v1):
        """Parameter-to-millimetre scale, measured at the middle of the
        domain by finite differences. Exactly 1 for a plane and exactly the
        radius for a cylinder's u."""
        um, vm = (u0 + u1) / 2.0, (v0 + v1) / 2.0
        du = max((u1 - u0) * 1e-4, 1e-9)
        dv = max((v1 - v0) * 1e-4, 1e-9)
        try:
            p = self.face.valueAt(um, vm)
            su = p.distanceToPoint(self.face.valueAt(um + du, vm)) / du
            sv = p.distanceToPoint(self.face.valueAt(um, vm + dv)) / dv
        except Exception:
            su = sv = 1.0
        return (su if su > 1e-9 else 1.0), (sv if sv > 1e-9 else 1.0)

    def distortion(self) -> float:
        """
        How far this frame is from a true isometry, as the worst relative
        deviation of the local scale across the domain (0.0 = exact).

        Planes and cylinders return ~0. A double-curved face returns a real
        number the caller should surface to the user, because on such a face
        "45 degrees" and "0.2 mm clearance" only hold approximately.
        """
        u0, u1 = self.u_range
        v0, v1 = self.v_range
        worst = 0.0
        for fu in (0.15, 0.5, 0.85):
            for fv in (0.15, 0.5, 0.85):
                u = u0 + (u1 - u0) * fu
                v = v0 + (v1 - v0) * fv
                du = max((u1 - u0) * 1e-4, 1e-9)
                dv = max((v1 - v0) * 1e-4, 1e-9)
                try:
                    p = self.face.valueAt(u, v)
                    su = p.distanceToPoint(self.face.valueAt(u + du, v)) / du
                    sv = p.distanceToPoint(self.face.valueAt(u, v + dv)) / dv
                except Exception:
                    continue
                worst = max(worst,
                            abs(su - self.u_scale) / max(self.u_scale, 1e-9),
                            abs(sv - self.v_scale) / max(self.v_scale, 1e-9))
        return worst

    # ── mapping ───────────────────────────────────────────────────────────

    def to_2d(self, pt, near=None):
        """
        3-D point -> 2-D frame coordinates (mm).

        *near* is an optional 2-D point used to pick the right representative
        on a periodic surface, so a path crossing the seam stays continuous
        instead of jumping a full turn around the body.
        """
        if self._axes is not None:
            o, ux, vx = self._axes
            d = FreeCAD.Vector(pt.x - o.x, pt.y - o.y, pt.z - o.z)
            x, y = d.dot(ux), d.dot(vx)
        else:
            try:
                u, v = self.surface.parameter(FreeCAD.Vector(pt.x, pt.y, pt.z))
            except Exception:
                return None
            x, y = u * self.u_scale, v * self.v_scale
        if near is not None:
            x = self._unwrap(x, near[0], self.u_period * self.u_scale)
            y = self._unwrap(y, near[1], self.v_period * self.v_scale)
        return (x, y)

    @staticmethod
    def _unwrap(value, reference, period):
        if period <= 1e-12:
            return value
        while value - reference > period / 2.0:
            value -= period
        while reference - value > period / 2.0:
            value += period
        return value

    def to_3d(self, x, y):
        """2-D frame coordinates -> 3-D point on the surface."""
        if self._axes is not None:
            o, ux, vx = self._axes
            return FreeCAD.Vector(o.x + ux.x * x + vx.x * y,
                                  o.y + ux.y * x + vx.y * y,
                                  o.z + ux.z * x + vx.z * y)
        try:
            return self.face.valueAt(x / self.u_scale, y / self.v_scale)
        except Exception:
            return None

    def normal_at(self, x, y):
        """Outward surface normal at a 2-D frame position."""
        # A plane's normal is constant, and this is called once per candidate
        # obstacle face during collection — evaluating the routing face's
        # normal in OCCT thousands of times was the dominant cost there.
        if self._normal_const is not None:
            return self._normal_const
        try:
            n = self.face.normalAt(x / self.u_scale, y / self.v_scale)
            if n.Length > 1e-12:
                return FreeCAD.Vector(n).normalize()
        except Exception:
            pass
        return FreeCAD.Vector(0, 0, 1)

    def bounds_2d(self):
        u0, u1 = self.u_range
        v0, v1 = self.v_range
        return (u0 * self.u_scale, v0 * self.v_scale,
                u1 * self.u_scale, v1 * self.v_scale)

    def distance_to_surface(self, pt) -> float:
        """Distance from a 3-D point to this surface — used to decide which
        bodies are near enough to the routing surface to be obstacles."""
        try:
            if self.is_planar:
                origin = self.surface.Position
                axis = self.surface.Axis
                return abs((FreeCAD.Vector(pt.x, pt.y, pt.z) - origin).dot(axis))
            u, v = self.surface.parameter(FreeCAD.Vector(pt.x, pt.y, pt.z))
            return self.face.valueAt(u, v).distanceToPoint(
                FreeCAD.Vector(pt.x, pt.y, pt.z))
        except Exception:
            return float("inf")

    # ── densifying a 2-D route onto the surface ───────────────────────────

    def densify(self, p2, q2, max_step_mm: float = 0.5):
        """
        Sample the straight 2-D segment p2 -> q2 into 2-D points close enough
        together that the corresponding 3-D polyline follows the surface
        instead of cutting through it. A flat frame needs no subdivision.
        """
        if self.is_planar:
            return [p2, q2]
        length = math.hypot(q2[0] - p2[0], q2[1] - p2[1])
        n = max(1, int(math.ceil(length / max(max_step_mm, 1e-6))))
        return [(p2[0] + (q2[0] - p2[0]) * i / n,
                 p2[1] + (q2[1] - p2[1]) * i / n) for i in range(n + 1)]

    def path_to_3d(self, pts2d, max_step_mm: float = 0.5):
        """A 2-D route -> the 3-D polyline that follows the surface."""
        out = []
        for a, b in zip(pts2d, pts2d[1:]):
            for s in self.densify(a, b, max_step_mm):
                p = self.to_3d(*s)
                if p is None:
                    continue
                if not out or (p - out[-1]).Length > 1e-9:
                    out.append(p)
        if not out and pts2d:
            p = self.to_3d(*pts2d[0])
            if p is not None:
                out.append(p)
        return out


# ── surface-following trace geometry ───────────────────────────────────────────

def build_surface_trace_solid(frame: SurfaceFrame, pts2d, width_mm: float,
                              thickness_mm: float, max_step_mm: float = 0.5):
    """
    A trace solid that hugs the routing surface.

    core.trace_routing.build_trace_solid sweeps a rectangle along a polyline
    with a fixed profile orientation, which is right on a flat face but wrong
    on a curved one — the trace would leave the surface and its cross-section
    would not stay tangential. Here a rectangle is built at every densified
    point, oriented by the LOCAL surface normal, and the result is lofted, so
    the trace follows the curvature and keeps its width in the surface.

    Falls back to fusing one oriented box per segment if the loft fails,
    which is more forgiving of sharp corners.
    """
    if width_mm <= 0 or thickness_mm <= 0:
        raise ValueError("build_surface_trace_solid: width and thickness must be positive")

    samples = []
    for a, b in zip(pts2d, pts2d[1:]):
        for s in frame.densify(a, b, max_step_mm):
            if not samples or (abs(s[0] - samples[-1][0]) > 1e-9
                               or abs(s[1] - samples[-1][1]) > 1e-9):
                samples.append(s)
    if len(samples) < 2:
        raise ValueError("build_surface_trace_solid: fewer than 2 distinct points")

    pts3 = [frame.to_3d(*s) for s in samples]
    pts3 = [p for p in pts3 if p is not None]
    if len(pts3) < 2:
        raise ValueError("build_surface_trace_solid: path does not lie on the surface")

    wires = []
    for i, s in enumerate(samples):
        p = frame.to_3d(*s)
        if p is None:
            continue
        nxt = pts3[min(i + 1, len(pts3) - 1)]
        prv = pts3[max(i - 1, 0)]
        tangent = nxt - prv
        if tangent.Length < 1e-9:
            continue
        tangent.normalize()
        normal = frame.normal_at(*s)
        side = tangent.cross(normal)
        if side.Length < 1e-9:
            continue
        side.normalize()
        hw, ht = width_mm / 2.0, thickness_mm / 2.0
        c0 = p + side * hw + normal * ht
        c1 = p - side * hw + normal * ht
        c2 = p - side * hw - normal * ht
        c3 = p + side * hw - normal * ht
        wires.append(Part.makePolygon([c0, c1, c2, c3, c0]))

    if len(wires) >= 2:
        try:
            solid = Part.makeLoft(wires, True, True)
            if solid is not None and not solid.isNull() and solid.isValid() and solid.Volume > 0:
                return solid
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[routing_frame] surface loft failed, fusing per-segment boxes: {exc}\n")

    return _fuse_oriented_boxes(frame, samples, width_mm, thickness_mm)


def _fuse_oriented_boxes(frame: SurfaceFrame, samples, width_mm, thickness_mm):
    """Fallback: one box per densified segment, each oriented by the local
    surface normal, fused together."""
    solids = []
    for a, b in zip(samples, samples[1:]):
        pa, pb = frame.to_3d(*a), frame.to_3d(*b)
        if pa is None or pb is None:
            continue
        delta = pb - pa
        if delta.Length < 1e-9:
            continue
        normal = frame.normal_at(*a)
        tangent = FreeCAD.Vector(delta).normalize()
        side = tangent.cross(normal)
        if side.Length < 1e-9:
            continue
        side.normalize()
        normal = side.cross(tangent).normalize()
        box = Part.makeBox(delta.Length, width_mm, thickness_mm,
                           FreeCAD.Vector(0, -width_mm / 2.0, -thickness_mm / 2.0))
        box.Placement = FreeCAD.Placement(
            pa, FreeCAD.Rotation(tangent, side, normal, "XYZ"))
        solids.append(box)
    if not solids:
        raise ValueError("_fuse_oriented_boxes: nothing to build")
    fused = solids[0]
    for s in solids[1:]:
        fused = fused.fuse(s)
    try:
        fused = fused.removeSplitter()
    except Exception:
        pass
    return fused
