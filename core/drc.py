# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Design Rule Check — post-hoc clearance and trace-width verification.

Nothing in this workbench checks a FINISHED design for violations after the
fact. The interactive router prevents them live while a trace is being
drawn, but a trace moved manually, a trace from the OTHER (grid) router, or
an obstacle added after a trace was routed can all leave the document in a
state nothing flags. This module is that check, run on demand.

Scope: this checks the workbench's OWN output (Trace_NNN / BondWire_NNN)
against each other and against nearby pre-existing copper — not an
exhaustive all-pairs scan of every solid in the document. A real imported
board can carry 900+ individual copper/pad solids (confirmed against a real
customer board earlier in this project); checking all of those against each
other is both too slow for an on-demand check and not really "checking OUR
design," since that copper came from an already-manufactured board. Checking
only the NEW geometry against its surroundings is what actually matters and
stays fast — a spatial index (see _CopperIndex) keeps each check to a
handful of nearby candidates, not the whole document.

Every geometric check is real 3-D Shape.distToShape, not the 2-D
PolyField/SurfaceFrame machinery core.trace_obstacles uses for live routing.
That machinery needs to know which face/frame a trace was routed in, and
Trace_NNN objects don't persist that (routing/InteractiveRouterCommand.py's
bake_trace uses it only transiently) — so a 3-D check is not just simpler,
it's the only one that's correct in general, including for two traces routed
on different faces of the same body.

Same-net endpoint exemption is NOT optional polish: every routed object
legitimately touches (distance 0) the pad it starts and ends on — that is
the entire point of a connection. Skipping this would flag every single
trace/wire against its own landing pad on the very first run.
"""

import math
from collections import namedtuple, defaultdict

import FreeCAD
import Part

# ── findings ─────────────────────────────────────────────────────────────────

DRCFinding = namedtuple("DRCFinding", ["severity", "rule", "object_names", "message", "location"])

DEFAULT_MIN_CLEARANCE_MM   = 0.2
DEFAULT_MIN_TRACE_WIDTH_MM = 0.1

# Same construction-geometry guard used throughout core.trace_routing /
# core.trace_obstacles — anything this large is FreeCAD Origin datum
# geometry, never real copper.
_MAX_EXTENT_MM = 1.0e6

_PAD_LOCATE_TOL_MM = 0.5   # same tolerance as ManualWireBonding._locate_pad_solid

# A candidate solid whose XY footprint dwarfs every routed object's own is
# almost certainly the board/substrate/package body a trace is routed ON,
# not a nearby copper FEATURE it should keep clearance from — a trace sits
# flush on its own board along its whole length (distance 0 throughout),
# which is expected, not a violation. Confirmed against a real board: without
# this, every single baked trace flagged itself against the PCB substrate
# (and the giant board bbox, bucketed into thousands of 2mm grid cells,
# dominated the check's runtime too). Dropping such candidates from the
# spatial index entirely — rather than exempting them per-endpoint like a
# pad — fixes both at once: a candidate that's never indexed is also never
# tested, so it can't produce a false positive OR a slow query.
_SUBSTRATE_AREA_RATIO = 20.0


# ── object classification ───────────────────────────────────────────────────

def _is_routing_trace(o) -> bool:
    return bool(getattr(o, "IsRoutingTrace", False))


def _is_bond_wire(o) -> bool:
    return o.Name.startswith("BondWire_")


def _has_geometry(o) -> bool:
    shp = getattr(o, "Shape", None)
    return shp is not None and not shp.isNull() and shp.isValid()


def _endpoints_of(doc, obj):
    """
    World-space start/end points of a routed object, or [] if unknown.

    Trace_NNN stores its own polyline (Waypoints); BondWire_NNN instead
    stores the NAMES of the two ContactPoint objects it connects (StartCP /
    EndCP), so its endpoints are resolved one level indirect via each CP's
    own ContactPoint (Vector) property.
    """
    wp = getattr(obj, "Waypoints", None)
    if wp:
        try:
            pts = list(wp)
        except Exception:
            pts = []
        if len(pts) >= 2:
            return [pts[0], pts[-1]]
        if len(pts) == 1:
            return [pts[0]]

    pts = []
    for prop in ("StartCP", "EndCP"):
        name = getattr(obj, prop, None)
        if not name:
            continue
        cp = doc.getObject(name)
        pos = getattr(cp, "ContactPoint", None) if cp is not None else None
        if pos is not None:
            pts.append(FreeCAD.Vector(pos))
    return pts


# ── spatial index over candidate "copper" (all physical bodies, including
#    the workbench's own routed objects — see run_drc for why one shared
#    index covers both trace-vs-copper and trace-vs-trace checks) ──────────

def _cell_of(x: float, y: float, cell: float):
    return (math.floor(x / cell), math.floor(y / cell))


def _footprint_area(bb) -> float:
    return max(bb.XLength, 1e-9) * max(bb.YLength, 1e-9)


def _copper_candidates(doc, exclude_names, max_footprint_area_mm2=None):
    """
    Every physical-body sub-solid eligible as a DRC candidate: (name, solid)
    pairs. Same filtering as core.trace_obstacles.collect_obstacle_polys —
    Part::Feature/Mesh::Feature bodies only, contact-point/grid-marker
    helpers and Origin-sized construction geometry excluded, each object
    decomposed into its individual solids so one compound copper layer isn't
    treated as a single giant obstacle.

    *max_footprint_area_mm2*, when a positive number, additionally drops any
    sub-solid whose own XY footprint exceeds it by more than
    _SUBSTRATE_AREA_RATIO — see that constant's comment for why.
    """
    out = []
    exclude = set(exclude_names or ())
    substrate_cutoff = None
    if max_footprint_area_mm2 and max_footprint_area_mm2 > 0:
        substrate_cutoff = max_footprint_area_mm2 * _SUBSTRATE_AREA_RATIO
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
        try:
            obb = o.Shape.BoundBox
        except Exception:
            continue
        if not obb.isValid() or obb.XLength > _MAX_EXTENT_MM or obb.YLength > _MAX_EXTENT_MM:
            continue
        for s in (o.Shape.Solids or [o.Shape]):
            try:
                bb = s.BoundBox
            except Exception:
                continue
            if not bb.isValid() or bb.XLength > _MAX_EXTENT_MM:
                continue
            if substrate_cutoff is not None and _footprint_area(bb) > substrate_cutoff:
                continue
            out.append((o.Name, s))
    return out


class _CopperIndex:
    """
    Bucket candidate solids by XY cell — same bucketing technique as
    core.via_clustering.cluster_boxes — so a query shape only tests nearby
    candidates instead of the whole document.
    """

    def __init__(self, doc, exclude_names=None, cell_mm: float = 2.0,
                 max_footprint_area_mm2=None):
        self.cell = max(cell_mm, 1e-6)
        self.items = _copper_candidates(doc, exclude_names, max_footprint_area_mm2)
        self.index = defaultdict(list)
        for i, (_name, solid) in enumerate(self.items):
            bb = solid.BoundBox
            cx0, cy0 = _cell_of(bb.XMin, bb.YMin, self.cell)
            cx1, cy1 = _cell_of(bb.XMax, bb.YMax, self.cell)
            for cx in range(cx0, cx1 + 1):
                for cy in range(cy0, cy1 + 1):
                    self.index[(cx, cy)].append(i)

    def nearby(self, bbox, margin_mm: float):
        """[(name, solid), ...] whose (unexpanded) cell bucket overlaps
        *bbox* expanded by *margin_mm* — a candidate list, not an exact
        distance test."""
        cx0, cy0 = _cell_of(bbox.XMin - margin_mm, bbox.YMin - margin_mm, self.cell)
        cx1, cy1 = _cell_of(bbox.XMax + margin_mm, bbox.YMax + margin_mm, self.cell)
        seen = set()
        out = []
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                for i in self.index.get((cx, cy), ()):
                    if i in seen:
                        continue
                    seen.add(i)
                    out.append(self.items[i])
        return out

    def containing_solids(self, pt, tol: float = _PAD_LOCATE_TOL_MM, exclude_names=None):
        """
        EVERY solid within *tol* of *pt* — plural, not "the tightest fit
        wins" the way ManualWireBonding._locate_pad_solid picks a single
        pad, and PROXIMITY rather than strict isInside() containment.

        Two reasons a single, containment-only match is not enough,
        confirmed against a real customer board:

        - A pad is frequently more than one touching solid in the same
          layer object (e.g. a short trace stub fused straight into a pad)
          — a trace landing there legitimately touches all of them, so all
          of them must be exempted, not just the smallest/tightest one.
        - A plated-through-hole pad is a copper ANNULUS around a drilled
          hole: the landing point our own tooling places at the pad's
          center sits in that hole, genuinely outside the copper's own
          volume — isInside() correctly says "not inside" while the copper
          is still only a few tenths of a millimetre away, at the same
          spot a rendered trace's body legitimately grazes while landing.
          Proximity, not containment, is what "this is the pad I'm landing
          on" actually means once real pad geometry is hollow.

        *exclude_names* MUST include the object this point is an endpoint
        OF: a trace's own geometry is thin (small volume) and trivially
        sits at distance 0 from its own start/end point, so without
        excluding it, it would be returned as if it were its own landing
        pad.
        """
        exclude = set(exclude_names or ())
        cx, cy = _cell_of(pt.x, pt.y, self.cell)
        found = []
        seen = set()
        vertex = Part.Vertex(pt)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for name, solid in ((self.items[i]) for i in self.index.get((cx + dx, cy + dy), ())):
                    if name in exclude or solid.Volume < 1e-9:
                        continue
                    if id(solid) in seen:
                        continue
                    try:
                        if solid.distToShape(vertex)[0] <= tol:
                            seen.add(id(solid))
                            found.append(solid)
                    except Exception:
                        continue
        return found


def _exempt_solids_for(doc, obj, copper_index: _CopperIndex):
    """Solid identities (id()) this routed object legitimately touches at
    its own endpoints — the same-net exemption set."""
    exempt = set()
    for pt in _endpoints_of(doc, obj):
        for s in copper_index.containing_solids(pt, exclude_names={obj.Name}):
            exempt.add(id(s))
    return exempt


# ── clearance check ──────────────────────────────────────────────────────────

def _clearance_check(name_a, shape_a, name_b, shape_b, min_clearance_mm: float, location=None):
    """One pairwise clearance test -> [] or a single-element finding list.
    A distToShape failure becomes its own finding rather than propagating —
    same defensive treatment this codebase already gives OCCT operations
    that can fail on degenerate geometry (see
    wirebond.ManualWireBonding's boolean-op try/excepts)."""
    try:
        dist = shape_a.distToShape(shape_b)[0]
    except Exception as exc:
        return [DRCFinding(
            "violation", "eval-failed", [name_a, name_b],
            f"Could not evaluate clearance between {name_a} and {name_b}: {exc}",
            location,
        )]
    if dist < min_clearance_mm:
        return [DRCFinding(
            "violation", "clearance", [name_a, name_b],
            f"{name_a} is {dist:.4f} mm from {name_b} (minimum {min_clearance_mm:g} mm)",
            location,
        )]
    return []


# ── entry point ──────────────────────────────────────────────────────────────

def run_drc(doc, min_clearance_mm: float = DEFAULT_MIN_CLEARANCE_MM,
           min_trace_width_mm: float = DEFAULT_MIN_TRACE_WIDTH_MM):
    """
    Check every Trace_NNN / BondWire_NNN in *doc* for clearance violations
    (against each other and against nearby pre-existing copper) and, for
    traces, a minimum width. Returns a list of DRCFinding.
    """
    findings = []
    if doc is None:
        return findings

    routed = [o for o in doc.Objects
              if (_is_routing_trace(o) or _is_bond_wire(o)) and _has_geometry(o)]
    if not routed:
        return findings

    # Trace width — a pure property read, no geometry needed.
    for o in routed:
        if not _is_routing_trace(o):
            continue
        w = getattr(o, "TraceWidth", None)
        if w is None:
            continue
        try:
            w = float(w)
        except Exception:
            continue
        if w < min_trace_width_mm:
            findings.append(DRCFinding(
                "violation", "min-width", [o.Name],
                f"{o.Name} width {w:.4f} mm is below the minimum {min_trace_width_mm:g} mm",
                None,
            ))

    routed_names = {o.Name for o in routed}
    # Scale reference for the substrate heuristic (see _SUBSTRATE_AREA_RATIO):
    # a candidate far bigger than the largest routed object itself is a
    # board/paddle/body the object is routed ON, not a feature beside it.
    max_routed_area = 0.0
    for o in routed:
        try:
            max_routed_area = max(max_routed_area, _footprint_area(o.Shape.BoundBox))
        except Exception:
            continue

    # ONE shared spatial index over copper AND the workbench's own routed
    # objects: a routed object's "nearby candidates" are just as likely to
    # be another trace/wire as pre-existing copper, and using a single index
    # means trace-vs-copper and trace-vs-trace/wire-vs-wire fall out of the
    # same loop instead of needing two separate passes.
    copper_index = _CopperIndex(doc, max_footprint_area_mm2=max_routed_area)
    exempt_map = {o.Name: _exempt_solids_for(doc, o, copper_index) for o in routed}

    checked_pairs = set()
    for o in routed:
        name = o.Name
        shape = o.Shape
        exempt_ids = exempt_map[name]
        bb = shape.BoundBox

        for cname, solid in copper_index.nearby(bb, min_clearance_mm):
            if cname == name:
                continue        # a sub-solid of the object being checked itself
            if id(solid) in exempt_ids:
                continue        # the pad this object legitimately lands on

            if cname in routed_names:
                # Checking two routed objects against each other: dedupe the
                # symmetric (A,B)/(B,A) pair, and exempt two traces/wires
                # that both legitimately converge on the SAME pad (they will
                # be very close to each other right at that shared point,
                # which is expected, not a violation).
                pair_key = tuple(sorted((name, cname)))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)
                if exempt_ids & exempt_map.get(cname, ()):
                    continue
                other = doc.getObject(cname)
                if other is None or not _has_geometry(other):
                    continue
                findings.extend(_clearance_check(
                    name, shape, cname, other.Shape, min_clearance_mm, bb.Center))
            else:
                findings.extend(_clearance_check(
                    name, shape, cname, solid, min_clearance_mm, bb.Center))

    return findings
