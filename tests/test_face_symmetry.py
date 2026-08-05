# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.face_symmetry — the face-centre / mirror /
symmetrize / pattern primitives behind symmetric chip placement and
symmetric contact-point placement.

The properties worth pinning down are the ones that are easy to get subtly
wrong and impossible to eyeball afterwards:

  * the centre must come from the face's OUTER BOUNDARY, not its area
    centroid (which a cut-out drags off-axis) and not its parameter range
    (which is larger than a trimmed face).
  * symmetrize must not merge a genuine close-straddling pair into one
    point on the axis — the failure mode that would silently delete a pad.
  * generated patterns must be exactly invariant under the mirror they
    claim to be symmetric about.
"""

import FreeCAD
import Part

from _harness import TestCase, new_document

import core.face_symmetry as fs
import core.routing_frame as rf

V = FreeCAD.Vector


def _top_face(shape):
    return shape.Faces[max(range(len(shape.Faces)),
                            key=lambda i: (shape.Faces[i].Area,
                                            shape.Faces[i].BoundBox.ZMax))]


def _mirrored_set_matches(pts, center, kind, tol=1e-6):
    """True when reflecting every point reproduces the same set."""
    imgs = [fs._reflect(p, center, kind) for p in pts]
    for img in imgs:
        if not any(abs(img[0] - q[0]) <= tol and abs(img[1] - q[1]) <= tol
                   for q in pts):
            return False
    return True


def run():
    tc = TestCase("face_symmetry")

    # ── face centre from the outer boundary ─────────────────────────────────
    box = Part.makeBox(40, 20, 2, V(0, 0, 0))
    top = _top_face(box)
    frame = rf.SurfaceFrame(top)

    ext = fs.outer_extent_2d(top, frame)
    tc.check("outer_extent_2d: reports the face's real extent", ext is not None)
    if ext is not None:
        umin, vmin, umax, vmax = ext
        tc.check("outer_extent_2d: extent matches the 40 x 20 face",
                  abs((umax - umin) - 40.0) < 0.1 and abs((vmax - vmin) - 20.0) < 0.1,
                  f"got {ext}")

    c = fs.face_center_2d(top, frame)
    tc.check("face_center_2d: centre of a plain rectangle is its middle",
              c is not None and abs(c[0] - 20.0) < 0.1 and abs(c[1] - 10.0) < 0.1,
              f"got {c}")

    cw = fs.face_center_world(top)
    tc.check("face_center_world: maps back to the world centre of the face",
              cw is not None and abs(cw.x - 20.0) < 0.1
              and abs(cw.y - 10.0) < 0.1 and abs(cw.z - 2.0) < 0.1,
              f"got {cw}")

    # A face with a NOTCH: its area centroid is pulled off-centre, but the
    # symmetric centre we want is still the middle of the outer boundary.
    notched = box.cut(Part.makeBox(8, 6, 4, V(0, 0, -1)))
    ntop = _top_face(notched)
    nframe = rf.SurfaceFrame(ntop)
    nc = fs.face_center_2d(ntop, nframe)
    com = ntop.CenterOfMass
    tc.check("face_center_2d: a notched face still centres on its outer "
              "boundary, not on its (off-centre) area centroid",
              nc is not None and abs(nc[0] - 20.0) < 0.2 and abs(nc[1] - 10.0) < 0.2,
              f"centre {nc}, centroid ({com.x:.2f}, {com.y:.2f})")
    tc.check("face_center_2d fixture: the centroid really IS off-centre here, "
              "so the two rules are genuinely distinguishable",
              abs(com.x - 20.0) > 0.5 or abs(com.y - 10.0) > 0.5,
              f"centroid ({com.x:.3f}, {com.y:.3f})")

    # ── mirroring ───────────────────────────────────────────────────────────
    ctr = (0.0, 0.0)
    tc.check("mirror_images: U mode reflects u only",
              fs.mirror_images((3.0, 5.0), ctr, fs.MIRROR_U) == [(-3.0, 5.0)])
    tc.check("mirror_images: V mode reflects v only",
              fs.mirror_images((3.0, 5.0), ctr, fs.MIRROR_V) == [(3.0, -5.0)])
    tc.check("mirror_images: BOTH yields three images (four-fold symmetry, "
              "not merely point symmetry)",
              set(fs.mirror_images((3.0, 5.0), ctr, fs.MIRROR_BOTH))
              == {(-3.0, 5.0), (3.0, -5.0), (-3.0, -5.0)})

    new = fs.mirror_points_2d([(3.0, 5.0)], ctr, fs.MIRROR_U)
    tc.check("mirror_points_2d: returns only the missing image",
              new == [(-3.0, 5.0)], f"got {new}")
    none_needed = fs.mirror_points_2d([(3.0, 5.0), (-3.0, 5.0)], ctr, fs.MIRROR_U)
    tc.check("mirror_points_2d: an already-symmetric pair needs nothing added",
              none_needed == [], f"got {none_needed}")
    on_axis = fs.mirror_points_2d([(0.0, 5.0)], ctr, fs.MIRROR_U)
    tc.check("mirror_points_2d: a point on the axis is its own image",
              on_axis == [], f"got {on_axis}")

    # ── symmetrize ──────────────────────────────────────────────────────────
    pts, snapped, added = fs.symmetrize_points_2d(
        [(3.02, 5.0), (-2.98, 5.0)], ctr, fs.MIRROR_U, tol=0.1)
    tc.check("symmetrize: a nearly-symmetric pair is snapped exactly "
              "symmetric, not duplicated",
              len(pts) == 2 and added == 0 and snapped > 0,
              f"pts={pts} snapped={snapped} added={added}")
    if len(pts) == 2:
        tc.check("symmetrize: ...and the snapped pair really is exact",
                  abs(pts[0][0] + pts[1][0]) < 1e-9 and abs(pts[0][1] - pts[1][1]) < 1e-9,
                  f"got {pts}")

    # The regression that matters: two points straddling the axis CLOSER
    # than tol are still a pair, and must not both collapse onto the axis.
    pts, snapped, added = fs.symmetrize_points_2d(
        [(0.02, 4.0), (-0.02, 4.0)], ctr, fs.MIRROR_U, tol=0.1)
    tc.check("symmetrize: two points straddling the axis closer than the "
              "tolerance stay TWO points (never merged onto the axis)",
              len(pts) == 2 and abs(pts[0][0] - pts[1][0]) > 1e-9,
              f"got {pts}")

    pts, snapped, added = fs.symmetrize_points_2d(
        [(0.01, 4.0)], ctr, fs.MIRROR_U, tol=0.1)
    tc.check("symmetrize: a lone point near the axis is snapped ONTO it "
              "rather than gaining a near-duplicate twin",
              len(pts) == 1 and abs(pts[0][0]) < 1e-9,
              f"got {pts}")

    pts, snapped, added = fs.symmetrize_points_2d(
        [(3.0, 5.0)], ctr, fs.MIRROR_BOTH, tol=0.01)
    tc.check("symmetrize: one off-axis point becomes a full four-fold set",
              len(pts) == 4 and added == 3, f"got {pts}")
    tc.check("symmetrize: the four-fold result is invariant under both mirrors",
              _mirrored_set_matches(pts, ctr, "u")
              and _mirrored_set_matches(pts, ctr, "v"), f"got {pts}")

    # ── generated patterns ──────────────────────────────────────────────────
    extent = (0.0, 0.0, 40.0, 20.0)
    gc = (20.0, 10.0)

    grid = fs.symmetric_grid_2d(extent, 4, 3, margin=2.0)
    tc.check("symmetric_grid_2d: produces n_u x n_v points",
              len(grid) == 12, f"got {len(grid)}")
    tc.check("symmetric_grid_2d: the grid is symmetric about both axes",
              _mirrored_set_matches(grid, gc, "u")
              and _mirrored_set_matches(grid, gc, "v"), f"got {grid}")
    tc.check("symmetric_grid_2d: margin is honoured",
              all(2.0 - 1e-9 <= u <= 38.0 + 1e-9 and 2.0 - 1e-9 <= v <= 18.0 + 1e-9
                  for u, v in grid), f"got {grid}")

    single = fs.symmetric_grid_2d(extent, 1, 1, margin=2.0)
    tc.check("symmetric_grid_2d: a 1x1 pattern sits exactly on the centre",
              len(single) == 1 and abs(single[0][0] - 20.0) < 1e-9
              and abs(single[0][1] - 10.0) < 1e-9, f"got {single}")

    pitched = fs.symmetric_grid_2d(extent, 3, 1, margin=0.0, pitch_u=5.0)
    tc.check("symmetric_grid_2d: an explicit pitch is honoured and stays centred",
              len(pitched) == 3
              and abs(pitched[0][0] - 15.0) < 1e-9
              and abs(pitched[1][0] - 20.0) < 1e-9
              and abs(pitched[2][0] - 25.0) < 1e-9, f"got {pitched}")

    ring = fs.symmetric_ring_2d(extent, 5, margin=2.0)
    tc.check("symmetric_ring_2d: 5 points on each of the four sides",
              len(ring) == 20, f"got {len(ring)}")
    tc.check("symmetric_ring_2d: the ring is symmetric about both axes",
              _mirrored_set_matches(ring, gc, "u")
              and _mirrored_set_matches(ring, gc, "v"), f"got {ring}")
    tc.check("symmetric_ring_2d: no point is duplicated (corners are not "
              "occupied twice)",
              len({(round(u, 6), round(v, 6)) for u, v in ring}) == len(ring),
              f"got {ring}")
    tc.check("symmetric_ring_2d: zero per side yields nothing rather than raising",
              fs.symmetric_ring_2d(extent, 0) == [])

    # ── face-domain filtering / world mapping ───────────────────────────────
    tc.check("is_inside_face: a point in the middle of the face is inside",
              fs.is_inside_face((20.0, 10.0), top, frame))
    tc.check("is_inside_face: a point well outside the face is not",
              not fs.is_inside_face((200.0, 10.0), top, frame))

    world = fs.to_world([(20.0, 10.0)], frame)
    tc.check("to_world: maps frame points back onto the surface",
              len(world) == 1 and abs(world[0].x - 20.0) < 0.1
              and abs(world[0].z - 2.0) < 0.1, f"got {world}")

    back = fs.world_to_frame_points([V(20, 10, 2)], top, frame)
    tc.check("world_to_frame_points: round-trips a point on the face",
              len(back) == 1 and abs(back[0][0] - 20.0) < 0.1
              and abs(back[0][1] - 10.0) < 0.1, f"got {back}")
    off = fs.world_to_frame_points([V(20, 10, 50)], top, frame, band_mm=1.0)
    tc.check("world_to_frame_points: a point far off the face is dropped, so "
              "another face's markers are never pulled into this symmetry",
              off == [], f"got {off}")

    # ── document level: finding the points on a face, and creating markers ──
    import wirebond.ContactPointSymmetryCommand as cps
    from wirebond.SetContactPointsOnFaceCommand import (
        _add_to_contact_points_group, _create_gds_marker,
        _create_housing_marker, _next_gds_index, _next_housing_index)

    doc = new_document("TestFaceSymmetryDoc")
    try:
        pkg = doc.addObject("Part::Feature", "Package")
        pkg.Shape = Part.makeBox(40, 20, 3, V(0, 0, 0))
        doc.recompute()
        ptop = _top_face(pkg.Shape)
        pframe = rf.SurfaceFrame(ptop)
        pcenter = fs.face_center_2d(ptop, pframe)

        # Marker creation must work with no GUI at all — it is otherwise
        # impossible to cover any of this headlessly, and a purely cosmetic
        # ViewObject step would take the whole operation down with it.
        m = _create_housing_marker(doc, pkg.Name, V(6, 4, 3), _next_housing_index(doc))
        _add_to_contact_points_group(doc, m)
        tc.check("contact-point markers can be created headlessly "
                  "(ViewObject styling is guarded, not assumed)",
                  m is not None and getattr(m, "IsContactPoint", False))
        g = _create_gds_marker(doc, pkg.Name, V(6, 10, 3), _next_gds_index(doc))
        _add_to_contact_points_group(doc, g)
        tc.check("...for die-side markers too", getattr(g, "IsContactPoint", False))

        for x, y in [(6, 16)]:
            mm = _create_housing_marker(doc, pkg.Name, V(x, y, 3),
                                         _next_housing_index(doc))
            _add_to_contact_points_group(doc, mm)
        doc.recompute()

        found = cps._contact_points_on(doc, pframe)
        tc.check("_contact_points_on: finds every contact point lying on the face",
                  len(found) == 3, f"got {len(found)}")

        # A marker belonging to a different face must not be swept in — a
        # symmetry operation should never move points off some other face.
        far = _create_housing_marker(doc, pkg.Name, V(20, 10, 40),
                                      _next_housing_index(doc))
        _add_to_contact_points_group(doc, far)
        doc.recompute()
        tc.check("_contact_points_on: a marker off the face is excluded",
                  len(cps._contact_points_on(doc, pframe)) == 3,
                  f"got {len(cps._contact_points_on(doc, pframe))}")

        pts = [xy for _mk, xy in cps._contact_points_on(doc, pframe)]
        images = fs.mirror_points_2d(pts, pcenter, fs.MIRROR_U)
        tc.check("end to end: mirroring one-sided points about the face centre "
                  "proposes exactly the missing opposite side",
                  len(images) == 3 and all(abs(u - 34.0) < 1e-6 for u, _v in images),
                  f"got {images}")
        tc.check("end to end: every mirrored point lands inside the face",
                  all(fs.is_inside_face(p, ptop, pframe) for p in images))
    finally:
        FreeCAD.closeDocument(doc.Name)

    return tc.results
