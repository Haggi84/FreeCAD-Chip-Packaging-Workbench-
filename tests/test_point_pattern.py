# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.point_pattern — the constrained-placement model
behind copying a contact point onto other faces.

The tool driving this is interactive and cannot be exercised headlessly, so
the whole INTERACTION MODEL lives here as plain arithmetic and is tested
directly: what a given cursor position, movement lock and snap actually
place, and that the result can never end up off the face.
"""

import FreeCAD
import Part

from _harness import TestCase, new_document

import core.point_pattern as pp
import core.face_symmetry as fs
import core.routing_frame as rf

V = FreeCAD.Vector


def run():
    tc = TestCase("point_pattern")

    ext = (0.0, 0.0, 40.0, 20.0)      # a 40 x 20 face
    ref = (10.0, 5.0)

    # ── relative position carries across differently-sized faces ────────────
    rel = pp.relative_position((10.0, 5.0), ext)
    tc.check("relative_position: expressed as a fraction of the extent",
              abs(rel[0] - 0.25) < 1e-9 and abs(rel[1] - 0.25) < 1e-9, f"got {rel}")
    tc.check("apply_relative: round-trips back to the same point",
              pp.apply_relative(rel, ext) == (10.0, 5.0),
              f"got {pp.apply_relative(rel, ext)}")

    big = (0.0, 0.0, 80.0, 40.0)
    carried = pp.carry_reference((10.0, 5.0), ext, big)
    tc.check("carry_reference: the same RELATIVE spot on a face of double the "
              "size, not the same numeric coordinates",
              abs(carried[0] - 20.0) < 1e-9 and abs(carried[1] - 10.0) < 1e-9,
              f"got {carried}")

    thin = (0.0, 7.0, 40.0, 7.0)      # zero height
    tc.check("relative_position: a zero-width axis degenerates to the middle "
              "instead of dividing by zero",
              pp.relative_position((10.0, 7.0), thin)[1] == 0.5,
              f"got {pp.relative_position((10.0, 7.0), thin)}")

    # ── movement locks ──────────────────────────────────────────────────────
    free = pp.constrain((30.0, 15.0), ref, ext, pp.MOVE_FREE)
    tc.check("MOVE_FREE: both axes follow the cursor",
              free == (30.0, 15.0), f"got {free}")

    along_u = pp.constrain((30.0, 15.0), ref, ext, pp.MOVE_U)
    tc.check("MOVE_U: slides along u, v pinned to the reference "
              "(so a row of copies lines up exactly)",
              along_u == (30.0, 5.0), f"got {along_u}")

    along_v = pp.constrain((30.0, 15.0), ref, ext, pp.MOVE_V)
    tc.check("MOVE_V: slides along v, u pinned to the reference",
              along_v == (10.0, 15.0), f"got {along_v}")

    # ── snaps ───────────────────────────────────────────────────────────────
    mid_u = pp.constrain((3.0, 15.0), ref, ext, pp.MOVE_FREE, pp.SNAP_MID_U)
    tc.check("SNAP_MID_U: u lands exactly on the middle of the face",
              mid_u == (20.0, 15.0), f"got {mid_u}")

    mid_v = pp.constrain((3.0, 15.0), ref, ext, pp.MOVE_FREE, pp.SNAP_MID_V)
    tc.check("SNAP_MID_V: v lands exactly on the middle",
              mid_v == (3.0, 10.0), f"got {mid_v}")

    mid_both = pp.constrain((3.0, 15.0), ref, ext, pp.MOVE_FREE, pp.SNAP_MID_BOTH)
    tc.check("SNAP_MID_BOTH: dead centre of the face",
              mid_both == (20.0, 10.0), f"got {mid_both}")

    ref_u = pp.constrain((33.0, 15.0), ref, ext, pp.MOVE_FREE, pp.SNAP_REF_U)
    tc.check("SNAP_REF_U: aligns with the source point's u",
              ref_u == (10.0, 15.0), f"got {ref_u}")

    # A snap must WIN over a movement lock — asking for "the middle" is a
    # stronger statement of intent than "keep this axis fixed", and the two
    # combine constantly in practice (lock to a row, snap to the centre).
    both = pp.constrain((3.0, 15.0), ref, ext, pp.MOVE_U, pp.SNAP_MID_V)
    tc.check("a snap overrides the movement lock on the axis it names",
              both == (3.0, 10.0), f"got {both}")
    locked_then_snapped = pp.constrain((3.0, 15.0), ref, ext, pp.MOVE_U,
                                        pp.SNAP_MID_U)
    tc.check("...and combines with it on the other axis (lock v to the "
              "reference, snap u to the middle)",
              locked_then_snapped == (20.0, 5.0), f"got {locked_then_snapped}")

    # ── clamping ────────────────────────────────────────────────────────────
    outside = pp.constrain((500.0, -70.0), ref, ext, pp.MOVE_FREE)
    tc.check("a cursor beyond the face is clamped onto it, so a point can "
              "never be placed off the part",
              outside == (40.0, 0.0), f"got {outside}")
    unclamped = pp.constrain((500.0, -70.0), ref, ext, pp.MOVE_FREE,
                              pp.SNAP_NONE, clamp=False)
    tc.check("clamping can be turned off explicitly",
              unclamped == (500.0, -70.0), f"got {unclamped}")

    # ── preview geometry ────────────────────────────────────────────────────
    segs = pp.crosshair_segments((12.0, 8.0), ext)
    tc.check("crosshair_segments: two guide lines", len(segs) == 2, f"got {segs}")
    tc.check("crosshair_segments: the first spans v at constant u",
              segs[0] == ((12.0, 0.0), (12.0, 20.0)), f"got {segs[0]}")
    tc.check("crosshair_segments: the second spans u at constant v",
              segs[1] == ((0.0, 8.0), (40.0, 8.0)), f"got {segs[1]}")

    # The point marker is drawn as a screen-space Coin marker (constant pixel
    # size), so there is no model-space size to compute or test here — the
    # cross-hair segments above are the only preview geometry with real
    # coordinates. Sizing a marker in model space was tried and abandoned:
    # scaled to the face it was invisible on a 2.00 x 0.50 mm pad and far too
    # big on a 1.20 x 1.20 mm one, and it changed size as the view zoomed.

    # ── mode cycling ────────────────────────────────────────────────────────
    tc.check("next_movement cycles free -> u -> v -> free",
              (pp.next_movement(pp.MOVE_FREE) == pp.MOVE_U
               and pp.next_movement(pp.MOVE_U) == pp.MOVE_V
               and pp.next_movement(pp.MOVE_V) == pp.MOVE_FREE))
    tc.check("next_snap cycles through the four snap states and back",
              (pp.next_snap(pp.SNAP_NONE) == pp.SNAP_MID_U
               and pp.next_snap(pp.SNAP_MID_BOTH) == pp.SNAP_NONE))
    tc.check("cycling helpers tolerate an unknown state rather than raising",
              pp.next_snap("nonsense") == pp.SNAP_NONE
              and pp.next_movement("nonsense") == pp.MOVE_FREE)

    # ── readable status ─────────────────────────────────────────────────────
    tc.check("describe: names an exact middle rather than printing 20.000",
              "u = middle" in pp.describe((20.0, 3.0), ext),
              f"got {pp.describe((20.0, 3.0), ext)}")
    tc.check("describe: reports a plain coordinate when not on an exact spot",
              "u = 12.000 mm" in pp.describe((12.0, 3.0), ext),
              f"got {pp.describe((12.0, 3.0), ext)}")
    tc.check("describe: calls out alignment with the source point",
              "aligned with source in u" in pp.describe((10.0, 3.0), ext, ref),
              f"got {pp.describe((10.0, 3.0), ext, ref)}")

    # ── the session: face selection, placement, undo and abort ──────────────
    # The interactive tool cannot be driven headlessly, but the session it
    # sits on can — and that is where the destructive operations live.
    import wirebond.ContactPointPatternCommand as cpp
    from wirebond.SetContactPointsOnFaceCommand import (
        _add_to_contact_points_group, _create_housing_marker,
        _next_housing_index)

    doc = new_document("TestPointPatternSession")
    try:
        body = doc.addObject("Part::Feature", "Body")
        body.Shape = Part.makeBox(40, 20, 10, V(0, 0, 0))
        doc.recompute()
        faces = body.Shape.Faces
        top = faces[max(range(len(faces)),
                         key=lambda i: (faces[i].Area, faces[i].BoundBox.ZMax))]
        walls = [f for f in faces
                 if abs(f.normalAt(0, 0).z) < 0.5 and abs(f.normalAt(0, 0).y) > 0.5]

        tframe = rf.SurfaceFrame(top)
        text = fs.outer_extent_2d(top, tframe)
        src_world = fs.to_world([pp.apply_relative((0.25, 0.25), text)], tframe)[0]
        src = _create_housing_marker(doc, body.Name, src_world,
                                      _next_housing_index(doc))
        _add_to_contact_points_group(doc, src)
        doc.recompute()

        picked = [(top, body.Name)] + [(w, body.Name) for w in walls]
        started = cpp.SESSION.start(doc, src, picked)
        try:
            tc.check("session: starts with a source point and target faces",
                      started and cpp.SESSION.is_active)
            tc.check("session: EVERY selected face is a target, including the "
                      "one the source already sits on (adding more points to "
                      "it is an ordinary thing to want)",
                      len(cpp.SESSION.faces) == len(picked),
                      f"got {len(cpp.SESSION.faces)} of {len(picked)} picked")
            tc.check("session: the source's RELATIVE position is what carries "
                      "across, not its raw coordinates",
                      abs(cpp.SESSION.source_rel[0] - 0.25) < 1e-9
                      and abs(cpp.SESSION.source_rel[1] - 0.25) < 1e-9,
                      f"got {cpp.SESSION.source_rel}")

            cur_ext = cpp.SESSION.current()[3]
            ref_here = cpp.SESSION.reference()
            rel_here = pp.relative_position(ref_here, cur_ext)
            tc.check("session: the reference lands at the same relative spot "
                      "on a differently-shaped target face",
                      abs(rel_here[0] - 0.25) < 1e-9 and abs(rel_here[1] - 0.25) < 1e-9,
                      f"got {rel_here}")

            m1 = cpp.SESSION.place(pp.constrain(
                (0.0, 0.0), ref_here, cur_ext, pp.MOVE_U, pp.SNAP_MID_BOTH))
            tc.check("session: placing creates a real marker immediately",
                      m1 is not None and getattr(m1, "IsContactPoint", False))

            cpp.SESSION.next_face()
            ext2 = cpp.SESSION.current()[3]
            m2 = cpp.SESSION.place(pp.constrain(
                (0.0, 0.0), cpp.SESSION.reference(), ext2, pp.MOVE_FREE,
                pp.SNAP_MID_BOTH))
            tc.check("session: the next face can be placed on too",
                      m2 is not None and len(cpp.SESSION.placed) == 2,
                      f"placed {cpp.SESSION.placed}")

            n_all = len([o for o in doc.Objects
                          if getattr(o, "IsContactPoint", False)])
            cpp.SESSION.undo_last()
            tc.check("session: undo removes the LAST point only",
                      len(cpp.SESSION.placed) == 1
                      and len([o for o in doc.Objects
                                if getattr(o, "IsContactPoint", False)]) == n_all - 1,
                      f"placed {cpp.SESSION.placed}")

            # Placing somewhere off the face must be refused rather than
            # creating a marker floating beside the part.
            before = len(cpp.SESSION.placed)
            off = cpp.SESSION.place((cur_ext[2] + 500.0, cur_ext[3] + 500.0))
            tc.check("session: a position outside the face is refused, not "
                      "placed off the part",
                      off is None and len(cpp.SESSION.placed) == before)

            cpp.SESSION.end(keep=False)
            remaining = sorted(o.Name for o in doc.Objects
                                if getattr(o, "IsContactPoint", False))
            tc.check("session: abort removes every point the session created, "
                      "leaving the original source untouched",
                      remaining == [src.Name], f"got {remaining}")
            tc.check("session: abort ends the session", not cpp.SESSION.is_active)
        finally:
            if cpp.SESSION.is_active:
                cpp.SESSION.end(keep=False)

        # Confirm keeps what was placed.
        cpp.SESSION.start(doc, src, picked)
        try:
            cur = cpp.SESSION.current()
            kept = cpp.SESSION.place(pp.constrain(
                (0.0, 0.0), cpp.SESSION.reference(), cur[3], pp.MOVE_FREE,
                pp.SNAP_MID_BOTH))
            kept_name = kept.Name if kept else None
            # Placed points persist from the moment they are placed — they
            # are real objects shown in a pending style, not transient
            # previews that vanish when the mouse moves on.
            tc.check("session: a placed point exists in the document before "
                      "the session is confirmed",
                      kept_name is not None and doc.getObject(kept_name) is not None,
                      f"{kept_name} missing while still pending")
            cpp.SESSION.end(keep=True)
            tc.check("session: confirm KEEPS the placed points",
                      kept_name is not None and doc.getObject(kept_name) is not None,
                      f"{kept_name} missing after confirm")
            tc.check("session: confirm ends the session", not cpp.SESSION.is_active)
        finally:
            if cpp.SESSION.is_active:
                cpp.SESSION.end(keep=False)

        # The source's own face alone is a perfectly valid session: this is
        # how you extend a single point into a row on the same face.
        same_face_only = cpp.SESSION.start(doc, src, [(top, body.Name)])
        try:
            tc.check("session: the source's own face alone is a valid target",
                      same_face_only and len(cpp.SESSION.faces) == 1,
                      f"started={same_face_only}, faces={len(cpp.SESSION.faces)}")
            if same_face_only:
                ext_same = cpp.SESSION.current()[3]
                ref_same = cpp.SESSION.reference()
                src_uv = fs.world_to_frame_points(
                    [FreeCAD.Vector(src.ContactPoint)], top, tframe)
                tc.check("session: on the source's own face the reference is "
                          "the source point itself, so locking an axis extends "
                          "it into an aligned row",
                          src_uv and abs(ref_same[0] - src_uv[0][0]) < 1e-6
                          and abs(ref_same[1] - src_uv[0][1]) < 1e-6,
                          f"reference {ref_same} vs source {src_uv}")

                # Slide along u with v locked to the source: a second point
                # on the same face, exactly in line with the first.
                row = pp.constrain((ext_same[2], 0.0), ref_same, ext_same,
                                    pp.MOVE_U)
                m = cpp.SESSION.place(row)
                tc.check("session: a second point can be placed on the "
                          "source's own face", m is not None)
                if m is not None:
                    placed_uv = fs.world_to_frame_points(
                        [FreeCAD.Vector(m.ContactPoint)], top, tframe)
                    tc.check("session: ...and it lines up exactly with the "
                              "source in the locked axis",
                              placed_uv and abs(placed_uv[0][1] - src_uv[0][1]) < 1e-6,
                              f"got {placed_uv} vs source {src_uv}")
        finally:
            if cpp.SESSION.is_active:
                cpp.SESSION.end(keep=False)

        # ── no template point at all ────────────────────────────────────
        # Selecting an existing contact point is optional: without one the
        # session simply places new points, referenced to each face's centre.
        no_src = cpp.SESSION.start(doc, None, picked)
        try:
            tc.check("session: starts with NO template contact point",
                      no_src and cpp.SESSION.is_active)
            tc.check("session: without a template the reference falls back to "
                      "the face's own centre",
                      abs(cpp.SESSION.source_rel[0] - 0.5) < 1e-9
                      and abs(cpp.SESSION.source_rel[1] - 0.5) < 1e-9,
                      f"got {cpp.SESSION.source_rel}")
            if no_src:
                ext_ns = cpp.SESSION.current()[3]
                ref_ns = cpp.SESSION.reference()
                mid = pp.center_of(ext_ns)
                tc.check("session: ...which really is the middle of the face",
                          abs(ref_ns[0] - mid[0]) < 1e-9
                          and abs(ref_ns[1] - mid[1]) < 1e-9,
                          f"reference {ref_ns} vs centre {mid}")
                m_ns = cpp.SESSION.place(ref_ns)
                tc.check("session: a point can be placed with no template",
                          m_ns is not None and getattr(m_ns, "IsContactPoint", False))
        finally:
            if cpp.SESSION.is_active:
                cpp.SESSION.end(keep=False)

        # ── shared placement mode ───────────────────────────────────────
        # The dialog and the 3-D tool must read ONE state, not two copies.
        cpp.SESSION.start(doc, src, picked)
        try:
            cpp.SESSION.set_mode(move=pp.MOVE_U, snap=pp.SNAP_MID_BOTH)
            tc.check("session: set_mode updates the shared movement/snap state",
                      cpp.SESSION.move == pp.MOVE_U
                      and cpp.SESSION.snap == pp.SNAP_MID_BOTH,
                      f"got {cpp.SESSION.move} / {cpp.SESSION.snap}")
            cpp.SESSION.set_mode(snap=pp.SNAP_NONE)
            tc.check("session: set_mode leaves the other setting alone",
                      cpp.SESSION.move == pp.MOVE_U
                      and cpp.SESSION.snap == pp.SNAP_NONE,
                      f"got {cpp.SESSION.move} / {cpp.SESSION.snap}")
            before = cpp.SESSION.index
            cpp.SESSION.next_face()
            tc.check("session: next_face advances even with no tool or dialog "
                      "attached (they are notified only if present)",
                      cpp.SESSION.index == (before + 1) % len(cpp.SESSION.faces),
                      f"got {cpp.SESSION.index}")
        finally:
            if cpp.SESSION.is_active:
                cpp.SESSION.end(keep=False)
        tc.check("session: mode resets with the session",
                  cpp.SESSION.move == pp.MOVE_FREE
                  and cpp.SESSION.snap == pp.SNAP_NONE,
                  f"got {cpp.SESSION.move} / {cpp.SESSION.snap}")

        # Genuinely nothing selected is still refused.
        tc.check("session: refuses to start with no faces at all",
                  not cpp.SESSION.start(doc, src, []))
        if cpp.SESSION.is_active:
            cpp.SESSION.end(keep=False)
        tc.check("session: refuses to start with neither a point nor faces",
                  not cpp.SESSION.start(doc, None, []))
        if cpp.SESSION.is_active:
            cpp.SESSION.end(keep=False)
    finally:
        FreeCAD.closeDocument(doc.Name)

    return tc.results
