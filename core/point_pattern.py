# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Constrained point placement on a face — the maths behind copying a contact
point onto other faces interactively.

The placement model, all in the 2-D millimetre space of
core.routing_frame.SurfaceFrame:

  reference   Where the copied point "wants" to be on the target face. It is
              the SOURCE point's position expressed as a FRACTION of its own
              face's extent and re-applied to the target's — so "3 mm in from
              the left edge, half way up" carries across to a face of a
              different size, which numeric (u, v) would not.
  movement    Which axis the cursor is allowed to move the point along. The
              other axis stays pinned to the reference, which is what makes
              a row of copies line up exactly instead of merely looking
              aligned.
  snap        An explicit exact position (the middle of an axis, or the
              reference value itself). Snaps are applied AFTER the movement
              lock and deliberately override it: asking for "the middle"
              is a stronger statement of intent than "keep this axis fixed".

Everything is pure arithmetic on tuples, so the whole interaction model is
covered by headless tests even though the tool driving it is interactive.
"""

# Which axis the cursor moves the point along.
MOVE_FREE = "free"   # both axes follow the cursor
MOVE_U    = "u"      # slides along U; V pinned to the reference
MOVE_V    = "v"      # slides along V; U pinned to the reference

# Exact positions the point can be snapped to.
SNAP_NONE     = "none"
SNAP_MID_U    = "mid_u"      # u := middle of the face
SNAP_MID_V    = "mid_v"      # v := middle of the face
SNAP_MID_BOTH = "mid_both"   # dead centre of the face
SNAP_REF_U    = "ref_u"      # u := the reference's u (align with the source)
SNAP_REF_V    = "ref_v"

_SNAP_CYCLE = (SNAP_NONE, SNAP_MID_U, SNAP_MID_V, SNAP_MID_BOTH)


def next_snap(snap: str) -> str:
    """The next snap in the cycle the interactive tool steps through."""
    try:
        return _SNAP_CYCLE[(_SNAP_CYCLE.index(snap) + 1) % len(_SNAP_CYCLE)]
    except ValueError:
        return SNAP_NONE


def next_movement(move: str) -> str:
    order = (MOVE_FREE, MOVE_U, MOVE_V)
    try:
        return order[(order.index(move) + 1) % len(order)]
    except ValueError:
        return MOVE_FREE


def center_of(extent):
    """(u, v) middle of an (umin, vmin, umax, vmax) extent."""
    umin, vmin, umax, vmax = extent
    return ((umin + umax) / 2.0, (vmin + vmax) / 2.0)


def relative_position(pt, extent):
    """
    *pt* as a fraction (fu, fv) of *extent*, each in 0..1.

    A zero-width extent degenerates to 0.5 (the middle) rather than dividing
    by zero — a face can legitimately be a sliver in one direction.
    """
    umin, vmin, umax, vmax = extent
    du, dv = umax - umin, vmax - vmin
    fu = (pt[0] - umin) / du if abs(du) > 1e-12 else 0.5
    fv = (pt[1] - vmin) / dv if abs(dv) > 1e-12 else 0.5
    return (fu, fv)


def apply_relative(rel, extent):
    """The inverse of relative_position: a fraction placed on an extent."""
    umin, vmin, umax, vmax = extent
    return (umin + (umax - umin) * rel[0],
            vmin + (vmax - vmin) * rel[1])


def carry_reference(src_pt, src_extent, dst_extent):
    """
    Where a point on one face lands on another, keeping its RELATIVE
    position — the default reference when copying onto a differently-sized
    face.
    """
    return apply_relative(relative_position(src_pt, src_extent), dst_extent)


def clamp_to_extent(pt, extent):
    umin, vmin, umax, vmax = extent
    return (min(max(pt[0], umin), umax), min(max(pt[1], vmin), vmax))


def constrain(cursor, reference, extent, move: str = MOVE_FREE,
              snap: str = SNAP_NONE, clamp: bool = True):
    """
    The point that would actually be placed for a given cursor position.

    Order is deliberate: movement lock first, snap second (snaps override a
    lock — see the module docstring), clamp last so the result can never sit
    off the face regardless of what the other two asked for.
    """
    u, v = float(cursor[0]), float(cursor[1])
    ru, rv = float(reference[0]), float(reference[1])

    if move == MOVE_U:
        v = rv
    elif move == MOVE_V:
        u = ru

    cu, cv = center_of(extent)
    if snap == SNAP_MID_U:
        u = cu
    elif snap == SNAP_MID_V:
        v = cv
    elif snap == SNAP_MID_BOTH:
        u, v = cu, cv
    elif snap == SNAP_REF_U:
        u = ru
    elif snap == SNAP_REF_V:
        v = rv

    pt = (u, v)
    return clamp_to_extent(pt, extent) if clamp else pt


def crosshair_segments(pt, extent):
    """
    The two guide lines through *pt*, spanning the face: one at constant u,
    one at constant v. Returned as [((x1,y1),(x2,y2)), ...] in frame space
    for the dashed preview.
    """
    umin, vmin, umax, vmax = extent
    u, v = pt
    return [
        ((u, vmin), (u, vmax)),
        ((umin, v), (umax, v)),
    ]


def describe(pt, extent, reference=None, tol: float = 1e-6) -> str:
    """
    A short human-readable statement of where the point is, naming an exact
    position when it is on one — so the interactive tool can tell the user
    "middle" rather than making them read 12.4999 off a coordinate box.
    """
    cu, cv = center_of(extent)
    bits = []
    if abs(pt[0] - cu) <= tol:
        bits.append("u = middle")
    else:
        bits.append(f"u = {pt[0]:.3f} mm")
    if abs(pt[1] - cv) <= tol:
        bits.append("v = middle")
    else:
        bits.append(f"v = {pt[1]:.3f} mm")
    if reference is not None:
        if abs(pt[0] - reference[0]) <= tol:
            bits.append("aligned with source in u")
        if abs(pt[1] - reference[1]) <= tol:
            bits.append("aligned with source in v")
    return ", ".join(bits)
