# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.routing_frame — the 2-D working frame that lets the
router run on any face of a 3-D body, flat or curved.

The property that matters is ISOMETRY: routing maths (45-degree postures,
clearances, trace widths) is done in the frame's 2-D space, so a millimetre
there must be a millimetre on the real surface. That holds exactly for planes
at any orientation and for cylinders (both developable); on a double-curved
face it cannot, and `distortion()` must report that rather than let the
router silently lie about clearances.
"""

import math

import FreeCAD
import Part

from _harness import TestCase

from core.routing_frame import SurfaceFrame, build_surface_trace_solid

V = FreeCAD.Vector


def _mid_2d(fr):
    u = (fr.u_range[0] + fr.u_range[1]) / 2.0
    v = (fr.v_range[0] + fr.v_range[1]) / 2.0
    return (u * fr.u_scale, v * fr.v_scale)


def run():
    tc = TestCase("routing_frame")

    box = Part.makeBox(20.0, 20.0, 10.0)
    top = max(box.Faces, key=lambda f: f.BoundBox.ZMax)
    side = next(f for f in box.Faces if abs(f.normalAt(0, 0).z) < 0.1)

    # ── flat faces, any orientation ──────────────────────────────────────────
    for name, face in (("horizontal top", top), ("vertical side wall", side)):
        fr = SurfaceFrame(face)
        tc.check(f"{name}: recognised as planar", fr.is_planar)
        tc.check(f"{name}: parameter scale is unity (already millimetres)",
                  abs(fr.u_scale - 1.0) < 1e-9 and abs(fr.v_scale - 1.0) < 1e-9,
                  f"scales {fr.u_scale}, {fr.v_scale}")
        tc.check(f"{name}: reports itself as an exact isometry",
                  fr.distortion() < 1e-6, f"distortion {fr.distortion()}")

        p2 = _mid_2d(fr)
        p3 = fr.to_3d(*p2)
        tc.check(f"{name}: 2-D -> 3-D -> 2-D round-trips",
                  p3 is not None and fr.to_2d(p3) is not None
                  and abs(fr.to_2d(p3)[0] - p2[0]) < 1e-6
                  and abs(fr.to_2d(p3)[1] - p2[1]) < 1e-6)

        # a 3-4-5 triangle in the frame must measure 5 mm on the real face
        q2 = (p2[0] + 3.0, p2[1] + 4.0)
        d3 = (fr.to_3d(*q2) - fr.to_3d(*p2)).Length
        tc.check(f"{name}: distance in the frame equals distance on the surface",
                  abs(d3 - 5.0) < 1e-6, f"got {d3}")

    # The vertical wall is the case that was impossible before: its own frame
    # must span the wall, not collapse onto world XY.
    fr_side = SurfaceFrame(side)
    x0, y0, x1, y1 = fr_side.bounds_2d()
    tc.check("vertical wall: the frame has real 2-D extent in both axes",
              (x1 - x0) > 1.0 and (y1 - y0) > 1.0, f"bounds {(x0, y0, x1, y1)}")

    # ── curved face: a cylinder is developable, so still exact ──────────────
    radius = 5.0
    cyl = Part.makeCylinder(radius, 10.0)
    cface = next(f for f in cyl.Faces if not isinstance(f.Surface, Part.Plane))
    fc = SurfaceFrame(cface)
    tc.check("cylinder: recognised as curved", not fc.is_planar)
    tc.check("cylinder: u scale equals the radius, so the unrolled frame is metric",
              abs(fc.u_scale - radius) < 1e-6, f"got {fc.u_scale}")
    tc.check("cylinder: v scale is unity (height is already in mm)",
              abs(fc.v_scale - 1.0) < 1e-6, f"got {fc.v_scale}")
    tc.check("cylinder: reports itself as an exact isometry (it is developable)",
              fc.distortion() < 1e-6, f"distortion {fc.distortion()}")

    # Arc length along the surface must match the frame distance.
    a2 = (0.0, 5.0)
    b2 = (fc.u_scale * 0.4, 5.0)          # 0.4 rad along the circumference
    expected_arc = radius * 0.4
    got = [fc.to_3d(*(a2[0] + (b2[0] - a2[0]) * i / 64.0, 5.0)) for i in range(65)]
    arc = sum((q - p).Length for p, q in zip(got, got[1:]))
    tc.check("cylinder: a frame distance really is that arc length on the surface",
              abs(arc - expected_arc) < 1e-3, f"got {arc}, expected {expected_arc}")

    tc.check("cylinder: detected as periodic in u",
              fc.u_periodic and abs(fc.u_period * fc.u_scale - 2 * math.pi * radius) < 1e-6,
              f"period {fc.u_period * fc.u_scale}")

    # ── seam handling ────────────────────────────────────────────────────────
    period_mm = fc.u_period * fc.u_scale
    just_past_seam = fc.to_3d(0.05, 5.0)
    plain = fc.to_2d(just_past_seam)
    unwrapped = fc.to_2d(just_past_seam, near=(period_mm - 0.1, 5.0))
    tc.check("seam: without a reference the point maps near u = 0",
              plain is not None and plain[0] < 1.0, f"got {plain}")
    tc.check("seam: with a reference just before the seam it is unwrapped past it, "
              "so a trace crossing the seam stays continuous",
              unwrapped is not None and abs(unwrapped[0] - (period_mm + 0.05)) < 1e-3,
              f"got {unwrapped}, period {period_mm}")

    # ── densifying onto the surface ─────────────────────────────────────────
    flat_fr = SurfaceFrame(top)
    tc.check("densify: a flat frame needs no subdivision",
              len(flat_fr.densify((0, 0), (10, 0), 0.5)) == 2)
    dens = fc.densify((0.0, 5.0), (10.0, 5.0), 0.5)
    tc.check("densify: a curved frame subdivides so the path follows the surface",
              len(dens) > 10, f"got {len(dens)} samples")

    pts3 = fc.path_to_3d([(0.0, 2.0), (10.0, 6.0)], max_step_mm=0.5)
    tc.check("path_to_3d: every sample lies on the cylinder",
              all(abs(math.hypot(p.x, p.y) - radius) < 1e-6 for p in pts3),
              "some samples left the surface")

    # ── surface-following trace solid ───────────────────────────────────────
    solid = build_surface_trace_solid(fc, [(0.0, 2.0), (8.0, 5.0), (16.0, 8.0)],
                                       width_mm=0.4, thickness_mm=0.05)
    tc.check("curved trace solid: valid with positive volume",
              solid is not None and solid.isValid() and not solid.isNull()
              and solid.Volume > 0, f"volume={getattr(solid, 'Volume', None)}")
    tc.check("curved trace solid: hugs the cylinder rather than cutting across it",
              solid.BoundBox.XLength < 2 * radius + 1.0
              and solid.BoundBox.YLength < 2 * radius + 1.0,
              f"bbox {solid.BoundBox.XLength} x {solid.BoundBox.YLength}")

    flat_solid = build_surface_trace_solid(flat_fr, [(2.0, 2.0), (12.0, 2.0)],
                                            width_mm=0.4, thickness_mm=0.05)
    tc.check("flat trace solid: still built correctly through the same path",
              flat_solid is not None and flat_solid.isValid() and flat_solid.Volume > 0)
    tc.check("flat trace solid: volume is about length x width x thickness",
              abs(flat_solid.Volume - 10.0 * 0.4 * 0.05) < 1e-3,
              f"got {flat_solid.Volume}")

    def _too_short():
        build_surface_trace_solid(flat_fr, [(0.0, 0.0)], 0.4, 0.05)
    ok = False
    try:
        _too_short()
    except ValueError:
        ok = True
    except Exception:
        ok = False
    tc.check("build_surface_trace_solid: rejects a single-point path", ok)

    # ── double-curved surface reports its distortion honestly ───────────────
    sph = Part.makeSphere(10.0)
    sface = sph.Faces[0]
    fs = SurfaceFrame(sface)
    tc.check("sphere: distortion is reported as significant, so the caller can warn "
              "that clearances only hold approximately",
              fs.distortion() > 0.05, f"distortion {fs.distortion()}")

    return tc.results
