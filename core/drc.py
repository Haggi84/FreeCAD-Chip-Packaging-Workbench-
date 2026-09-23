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

Bond wires also get rules of their own, at wire scale rather than trace
scale: wire-to-wire spacing (the general clearance is sized for board
copper and would flag every neighbouring wire on a fine-pitch die), wires
that cross in plan view, headroom between the top of each loop and the
package lid, wire length along the loop, the angle at which a wire leaves
its die, and how close it passes to the die's edge.

Stacked dies add two more: a wire flying over the die below it on its way
out, and a pad buried under the tier above. Both are invisible from above in
the 3-D view, which is the only place they would otherwise show up.
"""

import math
from collections import namedtuple, defaultdict

import FreeCAD
import Part

# ── findings ─────────────────────────────────────────────────────────────────

DRCFinding = namedtuple("DRCFinding", ["severity", "rule", "object_names", "message", "location"])

DEFAULT_MIN_CLEARANCE_MM   = 0.2
DEFAULT_MIN_TRACE_WIDTH_MM = 0.1

# About one wire diameter (25 µm gold) is the usual floor between two wires:
# closer than that they can touch when a loop sags, or when the mould
# compound sweeps them sideways during encapsulation.
DEFAULT_MIN_WIRE_SPACING_MM = 0.025

# The lid (or the top of the housing) has to clear the highest point of every
# loop by a margin, or the wire is pressed into it when the package closes.
DEFAULT_MIN_LID_CLEARANCE_MM = 0.1

# The same bounds the Wire Bonding Configurator offers by default. Too long
# and the loop sags and sweeps; too short and it cannot form a loop at all.
DEFAULT_MIN_WIRE_LENGTH_MM = 0.5
DEFAULT_MAX_WIRE_LENGTH_MM = 5.0

# Angle in plan view between a wire and the perpendicular to the die edge it
# leaves across. Steeper wires crowd their neighbours and pull the ball bond
# sideways; assembly rules commonly cap it around 45°.
DEFAULT_MAX_BOND_ANGLE_DEG = 45.0

# Gap between a wire and the top edge of the die it leaves, where a low loop
# touches the seal ring or the die's chipped edge.
DEFAULT_MIN_DIE_EDGE_CLEARANCE_MM = 0.025

# Gap between a wire and the top of a die it flies over on its way out of a
# stack. The wire never lands on that die, so the die-edge rule says nothing
# about it, and the surface it can touch is the whole face, not just an edge.
DEFAULT_MIN_STACK_CLEARANCE_MM = 0.1

# A wire end counts as on a die when it is inside the outline and no lower
# than this below the die's top.
_ON_DIE_Z_TOL_MM = 0.1

# Duplicated from core.housing.find_housing_body for the same reason
# _inside_partdesign_body is duplicated below: this module is loaded
# standalone by file path and must not import sibling core modules.
_HOUSING_NAMES = ("FinalHousing", "HousingBody")

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


def _inside_partdesign_body(o) -> bool:
    """True for a feature INSIDE a PartDesign::Body — a history state of the
    Body's one solid, not separate copper; the Body itself represents them
    all. Same rule as core.trace_routing._partdesign_body_of, duplicated
    locally because this module is deliberately loaded standalone by file
    path (see drc/DRCPanel.py) and must not import sibling core modules."""
    try:
        grp = o.getParentGeoFeatureGroup()
        return grp is not None and grp.isDerivedFrom("PartDesign::Body")
    except Exception:
        return False


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
        if _inside_partdesign_body(o):
            continue
        try:
            is_body = o.isDerivedFrom("Part::Feature") or o.isDerivedFrom("Mesh::Feature")
        except Exception:
            is_body = False
        if not is_body:
            continue
        if getattr(o, "IsContactPoint", False):
            continue
        if getattr(o, "IsPort", False):
            continue        # a port is a surface for a solver, not copper
        if getattr(o, "IsUnusedStackLayer", False):
            continue        # an empty PDK level: nothing is drawn there, so
                            # there is no copper to keep clear of
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


# ── rule registry ────────────────────────────────────────────────────────────
#
# Each rule is a callable (ctx) -> list[DRCFinding], registered here at import
# time. run_drc() below is now just: build the shared context once, then run
# every registered rule against it. Adding a rule = one @rule("id") function;
# it never touches run_drc. This keeps run_drc from growing back into the kind
# of god-function core.Core_Functionality was refactored out of.
#
# Registration order is the order findings are produced (min-width before
# clearance, matching the original run_drc), so existing test expectations that
# don't care about order stay green either way.

RULES = []   # list[(rule_id, fn)]


def rule(rule_id):
    """Register a rule function; returns it unchanged so it stays callable."""
    def _register(fn):
        RULES.append((rule_id, fn))
        return fn
    return _register


class _DRCContext:
    """Everything the rules share, built once per run_drc call.

    Pure-property rules (min-width, future max-wire-length/loop-height) read
    only .routed and .params; geometric rules (clearance) additionally use the
    shared spatial index and the same-net exemption map. The index and exempt
    map are built eagerly here exactly as the original run_drc did — same work,
    same cost, just hoisted so every rule sees the same context.
    """

    def __init__(self, doc, routed, params):
        self.doc = doc
        self.routed = routed
        self.routed_names = {o.Name for o in routed}
        self.params = params

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
        # objects — trace-vs-copper and trace-vs-trace fall out of the same loop.
        self.copper_index = _CopperIndex(doc, max_footprint_area_mm2=max_routed_area)
        self.exempt_map = {o.Name: _exempt_solids_for(doc, o, self.copper_index)
                           for o in routed}


# ── rules ────────────────────────────────────────────────────────────────────

@rule("min-width")
def _check_min_width(ctx):
    """Trace width — a pure property read, no geometry needed."""
    min_trace_width_mm = ctx.params.get("min_trace_width_mm", DEFAULT_MIN_TRACE_WIDTH_MM)
    findings = []
    for o in ctx.routed:
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
    return findings


@rule("clearance")
def _check_clearance(ctx):
    """Clearance of every routed object against nearby copper and against each
    other, via the shared spatial index. Logic moved verbatim from the original
    run_drc main loop — dedup of symmetric pairs and same-net/shared-pad
    exemptions unchanged."""
    min_clearance_mm = ctx.params.get("min_clearance_mm", DEFAULT_MIN_CLEARANCE_MM)
    doc = ctx.doc
    copper_index = ctx.copper_index
    exempt_map = ctx.exempt_map
    routed_names = ctx.routed_names

    findings = []
    checked_pairs = set()
    for o in ctx.routed:
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
                if _is_bond_wire(o) and cname.startswith("BondWire_"):
                    continue    # wire-to-wire belongs to the wire-spacing rule
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


# ── bond-wire rules ──────────────────────────────────────────────────────────

def _cp_names(obj):
    return {n for n in (getattr(obj, "StartCP", ""), getattr(obj, "EndCP", "")) if n}


def _shares_landing(doc, a, b, tol_mm: float = 1e-6) -> bool:
    """True when two wires land on the same contact point — a double bond
    or two wires on one lead meet there by design."""
    if _cp_names(a) & _cp_names(b):
        return True
    return any((pa - pb).Length <= tol_mm
               for pa in _endpoints_of(doc, a) for pb in _endpoints_of(doc, b))


def _bond_wires(ctx):
    return [o for o in ctx.routed if _is_bond_wire(o)]


def _span_xy(doc, obj):
    """The wire's span in plan view as ((x0, y0), (x1, y1)), or None."""
    pts = [getattr(obj, p, None) for p in ("StartPoint", "EndPoint")]
    if any(p is None for p in pts):
        pts = _endpoints_of(doc, obj)
    if len(pts) != 2 or (pts[0] - pts[1]).Length < 1e-9:
        return None
    return (pts[0].x, pts[0].y), (pts[1].x, pts[1].y)


def _segments_cross(p1, p2, q1, q2, tol: float = 1e-12) -> bool:
    """True when two plan-view segments cross at a point inside both.
    Touching at an end, or running collinear, is not a crossing."""
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def opposite(u, v):
        return (u > tol and v < -tol) or (u < -tol and v > tol)

    return (opposite(orient(q1, q2, p1), orient(q1, q2, p2))
            and opposite(orient(p1, p2, q1), orient(p1, p2, q2)))


def _boxes_within(a, b, gap_mm: float) -> bool:
    return not (a.XMin - gap_mm > b.XMax or b.XMin - gap_mm > a.XMax
                or a.YMin - gap_mm > b.YMax or b.YMin - gap_mm > a.YMax
                or a.ZMin - gap_mm > b.ZMax or b.ZMin - gap_mm > a.ZMax)


@rule("wire-spacing")
def _check_wire_spacing(ctx):
    """
    Minimum gap between two bond wires.

    Deliberately not the shared same-net exemption the clearance rule uses:
    that finds pads by proximity within _PAD_LOCATE_TOL_MM (0.5 mm), which on
    a fine-pitch die reaches the neighbouring pads too — every adjacent pair
    of wires would count as "sharing a pad" and never be checked. Only wires
    that land on the same contact point are exempt here.
    """
    min_spacing = ctx.params.get("min_wire_spacing_mm", DEFAULT_MIN_WIRE_SPACING_MM)
    wires = _bond_wires(ctx)
    findings = []
    for i, a in enumerate(wires):
        bb_a = a.Shape.BoundBox
        for b in wires[i + 1:]:
            if not _boxes_within(bb_a, b.Shape.BoundBox, min_spacing):
                continue
            if _shares_landing(ctx.doc, a, b):
                continue
            try:
                dist = a.Shape.distToShape(b.Shape)[0]
            except Exception as exc:
                findings.append(DRCFinding(
                    "violation", "eval-failed", [a.Name, b.Name],
                    f"Could not evaluate spacing between {a.Name} and {b.Name}: {exc}",
                    bb_a.Center))
                continue
            if dist < min_spacing:
                findings.append(DRCFinding(
                    "violation", "wire-spacing", [a.Name, b.Name],
                    f"{a.Name} is {dist:.4f} mm from {b.Name} "
                    f"(minimum wire spacing {min_spacing:g} mm)",
                    bb_a.Center))
    return findings


@rule("wire-crossing")
def _check_wire_crossing(ctx):
    """
    Wires that cross in plan view.

    A warning rather than a violation: one loop can legitimately pass over a
    shorter one with enough height between them, and the 3-D gap is in the
    message. But a crossing is where a sagging loop or mould-compound sweep
    turns into a short, so it should never go unnoticed.
    """
    spans = [(w, _span_xy(ctx.doc, w)) for w in _bond_wires(ctx)]
    spans = [(w, s) for w, s in spans if s is not None]
    findings = []
    for i, (a, sa) in enumerate(spans):
        for b, sb in spans[i + 1:]:
            if not _segments_cross(sa[0], sa[1], sb[0], sb[1]):
                continue
            if _shares_landing(ctx.doc, a, b):
                continue
            try:
                gap = f"{a.Shape.distToShape(b.Shape)[0]:.4f} mm apart in 3-D"
            except Exception:
                gap = "3-D gap could not be evaluated"
            findings.append(DRCFinding(
                "warning", "wire-crossing", [a.Name, b.Name],
                f"{a.Name} crosses {b.Name} in plan view ({gap})",
                None))
    return findings


def _package_ceiling(doc):
    """
    The surface bond loops must stay under, as (z, object name, bound box):
    the underside of the lid, or the top of the housing when it has no lid
    yet — a lid added later sits exactly there. None when there is neither.
    """
    lid = doc.getObject("Lid")
    if lid is not None and _has_geometry(lid):
        bb = lid.Shape.BoundBox
        return bb.ZMin, lid.Name, bb
    for name in _HOUSING_NAMES:
        housing = doc.getObject(name)
        if housing is not None and _has_geometry(housing):
            bb = housing.Shape.BoundBox
            return bb.ZMax, housing.Name, bb
    return None


@rule("lid-clearance")
def _check_lid_clearance(ctx):
    """Headroom between the top of each bond loop and the package ceiling."""
    ceiling = _package_ceiling(ctx.doc)
    if ceiling is None:
        return []
    z_ceiling, ceiling_name, cbb = ceiling
    min_clearance = ctx.params.get("min_lid_clearance_mm", DEFAULT_MIN_LID_CLEARANCE_MM)

    findings = []
    for o in _bond_wires(ctx):
        bb = o.Shape.BoundBox
        # A wire outside the package footprint — on a board elsewhere in the
        # same document — is not under this lid at all.
        if (bb.XMax < cbb.XMin or bb.XMin > cbb.XMax
                or bb.YMax < cbb.YMin or bb.YMin > cbb.YMax):
            continue
        headroom = z_ceiling - bb.ZMax
        if headroom >= min_clearance:
            continue
        if headroom < 0.0:
            msg = (f"{o.Name} loop top at z={bb.ZMax:.4f} mm goes through "
                   f"{ceiling_name} by {-headroom:.4f} mm")
        else:
            msg = (f"{o.Name} loop top at z={bb.ZMax:.4f} mm is {headroom:.4f} mm "
                   f"below {ceiling_name} (minimum {min_clearance:g} mm)")
        findings.append(DRCFinding(
            "violation", "lid-clearance", [o.Name, ceiling_name], msg, bb.Center))
    return findings


# ── wire length, bond angle, die edge ────────────────────────────────────────

def _wire_points(doc, obj):
    """[start, end] of a bond wire, or [] when not known."""
    pts = [getattr(obj, p, None) for p in ("StartPoint", "EndPoint")]
    if any(p is None for p in pts):
        pts = _endpoints_of(doc, obj)
    return pts if len(pts) == 2 else []


def _wire_length(doc, obj):
    """The length along the loop the wire records; for a wire placed before
    that was recorded, the straight distance between its ends."""
    value = getattr(obj, "WireLength", None)
    try:
        length = float(getattr(value, "Value", value))
        if length > 0.0:
            return length
    except (TypeError, ValueError):
        pass
    pts = _wire_points(doc, obj)
    return (pts[1] - pts[0]).Length if pts else None


@rule("wire-length")
def _check_wire_length(ctx):
    shortest = ctx.params.get("min_wire_length_mm", DEFAULT_MIN_WIRE_LENGTH_MM)
    longest = ctx.params.get("max_wire_length_mm", DEFAULT_MAX_WIRE_LENGTH_MM)
    findings = []
    for o in _bond_wires(ctx):
        length = _wire_length(ctx.doc, o)
        if length is None:
            continue
        if longest > 0.0 and length > longest:
            findings.append(DRCFinding(
                "violation", "wire-length", [o.Name],
                f"{o.Name} is {length:.3f} mm long (maximum {longest:g} mm)", None))
        elif length < shortest:
            findings.append(DRCFinding(
                "violation", "wire-length", [o.Name],
                f"{o.Name} is {length:.3f} mm long (minimum {shortest:g} mm)", None))
    return findings


def _die_outlines(doc):
    """
    (xmin, ymin, xmax, ymax, z_top, name, z_bottom) for every die: each chip
    proxy, and each die body — its slabs grouped by footprint, topped by the
    highest GDS layer standing on it, which is where its pads and its top edge
    are. The underside is what tells a die stacked on another from one beside
    it.
    """
    dies = []
    for o in doc.Objects:
        if getattr(o, "IsChipProxy", False) and _has_geometry(o):
            bb = o.Shape.BoundBox
            dies.append((bb.XMin, bb.YMin, bb.XMax, bb.YMax, bb.ZMax, o.Name, bb.ZMin))

    bodies = {}
    for o in doc.Objects:
        if getattr(o, "IsDieBody", False) and _has_geometry(o):
            bb = o.Shape.BoundBox
            key = tuple(round(v, 6) for v in (bb.XMin, bb.YMin, bb.XMax, bb.YMax))
            top, name, bottom = bodies.get(key, (bb.ZMax, o.Name, bb.ZMin))
            bodies[key] = (max(top, bb.ZMax), name, min(bottom, bb.ZMin))
    tol = 1e-6
    for (x0, y0, x1, y1), (top, name, bottom) in bodies.items():
        for o in doc.Objects:
            if not hasattr(o, "GDSLayerID") or not _has_geometry(o):
                continue
            bb = o.Shape.BoundBox
            if (bb.XMin >= x0 - tol and bb.XMax <= x1 + tol
                    and bb.YMin >= y0 - tol and bb.YMax <= y1 + tol):
                top = max(top, bb.ZMax)
        dies.append((x0, y0, x1, y1, top, name, bottom))
    return dies


def _on_die(point, die):
    """
    True when *point* is a pad ON this die: inside its outline and on its top
    face, within a marker's thickness EITHER WAY.

    The upper bound is what tells the tiers of a stack apart. Without it every
    pad of an upper die — inside the same outline, simply higher up — counts
    as being on the die below as well, so its wires are measured against the
    wrong die, and a wire between two tiers looks like it leaves neither.
    """
    x0, y0, x1, y1, top = die[:5]
    return (x0 <= point.x <= x1 and y0 <= point.y <= y1
            and abs(point.z - top) <= _ON_DIE_Z_TOL_MM)


def _leaving_die(doc, wire, dies):
    """[(die, inside_point, outside_point)] for each die the wire leaves —
    exactly one end on it. A wire between two pads of one die leaves none; a
    wire between two dies leaves both, and is listed once for each."""
    pts = _wire_points(doc, wire)
    if not pts:
        return []
    out = []
    for die in dies:
        on = [_on_die(p, die) for p in pts]
        if on.count(True) == 1:
            inside, outside = (pts[0], pts[1]) if on[0] else (pts[1], pts[0])
            out.append((die, inside, outside))
    return out


@rule("bond-angle")
def _check_bond_angle(ctx):
    """
    Plan-view angle between a wire leaving a die and the perpendicular to the
    die edge it crosses. A warning: the limit is an assembly-house rule, and
    a steep wire is sometimes the only way to reach a corner lead.
    """
    limit = ctx.params.get("max_bond_angle_deg", DEFAULT_MAX_BOND_ANGLE_DEG)
    dies = _die_outlines(ctx.doc)
    findings = []
    if not dies:
        return findings
    for o in _bond_wires(ctx):
        for die, inside, outside in _leaving_die(ctx.doc, o, dies):
            x0, y0, x1, y1, _top, die_name = die[:6]
            dx, dy = outside.x - inside.x, outside.y - inside.y
            span = math.hypot(dx, dy)
            if span < 1e-9:
                continue
            # Which edge the wire crosses: the one its direction reaches first.
            tx = ((x1 - inside.x) / dx if dx > 0 else (x0 - inside.x) / dx) if dx else math.inf
            ty = ((y1 - inside.y) / dy if dy > 0 else (y0 - inside.y) / dy) if dy else math.inf
            along_normal = abs(dx) if tx <= ty else abs(dy)
            angle = math.degrees(math.acos(max(-1.0, min(1.0, along_normal / span))))
            if angle > limit:
                findings.append(DRCFinding(
                    "warning", "bond-angle", [o.Name, die_name],
                    f"{o.Name} leaves {die_name} at {angle:.1f}° to the edge "
                    f"normal (maximum {limit:g}°)", inside))
    return findings


@rule("die-edge-clearance")
def _check_die_edge_clearance(ctx):
    """Gap between a wire and the top edge of the die it leaves."""
    minimum = ctx.params.get("min_die_edge_clearance_mm", DEFAULT_MIN_DIE_EDGE_CLEARANCE_MM)
    dies = _die_outlines(ctx.doc)
    findings = []
    if not dies:
        return findings
    edges = {}
    for o in _bond_wires(ctx):
        for die, inside, _outside in _leaving_die(ctx.doc, o, dies):
            x0, y0, x1, y1, top, die_name = die[:6]
            if die not in edges:
                V = FreeCAD.Vector
                edges[die] = Part.makePolygon([V(x0, y0, top), V(x1, y0, top),
                                               V(x1, y1, top), V(x0, y1, top),
                                               V(x0, y0, top)])
            try:
                gap = o.Shape.distToShape(edges[die])[0]
            except Exception as exc:
                findings.append(DRCFinding(
                    "violation", "eval-failed", [o.Name, die_name],
                    f"Could not evaluate {o.Name} against the edge of {die_name}: {exc}",
                    inside))
                continue
            if gap < minimum:
                findings.append(DRCFinding(
                    "violation", "die-edge-clearance", [o.Name, die_name],
                    f"{o.Name} passes {gap:.4f} mm from the edge of {die_name} "
                    f"(minimum {minimum:g} mm)", inside))
    return findings


# ── stacked dies ─────────────────────────────────────────────────────────────

def _contact_points(doc):
    return [o for o in (doc.Objects if doc else [])
            if getattr(o, "IsContactPoint", False)]


def _segment_hits_box(p0, p1, box):
    """True when the segment touches the axis-aligned box in plan view."""
    x0, y0, x1, y1 = box
    for point in (p0, p1):
        if x0 <= point.x <= x1 and y0 <= point.y <= y1:
            return True
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    a0, a1 = (p0.x, p0.y), (p1.x, p1.y)
    return any(_segments_cross(a0, a1, corners[i], corners[(i + 1) % 4])
               for i in range(4))


def _flies_over(doc, wire, dies):
    """Dies the wire passes over without landing on either end: its span
    crosses their outline in plan view and their top is below it."""
    pts = _wire_points(doc, wire)
    if not pts:
        return []
    highest = max(p.z for p in pts)
    over = []
    for die in dies:
        if any(_on_die(p, die) for p in pts):
            continue
        if die[4] > highest:
            continue
        if _segment_hits_box(pts[0], pts[1], die[:4]):
            over.append(die)
    return over


def _die_top_face(die):
    x0, y0, x1, y1, top = die[:5]
    V = FreeCAD.Vector
    return Part.Face(Part.makePolygon([
        V(x0, y0, top), V(x1, y0, top), V(x1, y1, top), V(x0, y1, top), V(x0, y0, top)]))


@rule("stack-clearance")
def _check_stack_clearance(ctx):
    """
    Gap between a wire and the top of a die it flies over.

    In a stack, the wires of an upper die run out across the die below, and
    that die's surface is what they can touch. The wire lands on neither end
    of it, so the die-edge rule — which only looks at the die a wire leaves —
    never sees it.
    """
    minimum = ctx.params.get("min_stack_clearance_mm", DEFAULT_MIN_STACK_CLEARANCE_MM)
    dies = _die_outlines(ctx.doc)
    findings, faces = [], {}
    if not dies:
        return findings
    for o in _bond_wires(ctx):
        for die in _flies_over(ctx.doc, o, dies):
            die_name = die[5]
            if die_name not in faces:
                try:
                    faces[die_name] = _die_top_face(die)
                except Exception:
                    continue
            try:
                gap = o.Shape.distToShape(faces[die_name])[0]
            except Exception as exc:
                findings.append(DRCFinding(
                    "violation", "eval-failed", [o.Name, die_name],
                    f"Could not evaluate {o.Name} over {die_name}: {exc}", None))
                continue
            if gap < minimum:
                findings.append(DRCFinding(
                    "violation", "stack-clearance", [o.Name, die_name],
                    f"{o.Name} passes {gap:.4f} mm over {die_name} "
                    f"(minimum {minimum:g} mm)", None))
    return findings


@rule("covered-pad")
def _check_covered_pads(ctx):
    """
    A pad with a die sitting over it.

    Stacking hides it from above in the 3-D view and it cannot be bonded at
    all — the mistake stacking invites, and an expensive one to find late.
    Unlike every other rule here this one is about pads, so it is worth
    running before a single wire exists.
    """
    dies = _die_outlines(ctx.doc)
    findings = []
    if len(dies) < 2:
        return findings
    for cp in _contact_points(ctx.doc):
        point = getattr(cp, "ContactPoint", None)
        if point is None:
            continue
        holder = next((d for d in dies if _on_die(point, d)), None)
        if holder is None:
            continue
        for die in dies:
            if die is holder or die[6] < holder[4] - _ON_DIE_Z_TOL_MM:
                continue
            x0, y0, x1, y1 = die[:4]
            if x0 <= point.x <= x1 and y0 <= point.y <= y1:
                findings.append(DRCFinding(
                    "violation", "covered-pad", [cp.Name, die[5]],
                    f"{cp.Name} on {holder[5]} is under {die[5]}, which sits "
                    f"{die[6] - holder[4]:.4f} mm above it — it cannot be bonded",
                    FreeCAD.Vector(point)))
                break
    return findings


# ── entry point ──────────────────────────────────────────────────────────────

def run_drc(doc, min_clearance_mm: float = DEFAULT_MIN_CLEARANCE_MM,
           min_trace_width_mm: float = DEFAULT_MIN_TRACE_WIDTH_MM,
           min_wire_spacing_mm: float = DEFAULT_MIN_WIRE_SPACING_MM,
           min_lid_clearance_mm: float = DEFAULT_MIN_LID_CLEARANCE_MM,
           min_wire_length_mm: float = DEFAULT_MIN_WIRE_LENGTH_MM,
           max_wire_length_mm: float = DEFAULT_MAX_WIRE_LENGTH_MM,
           max_bond_angle_deg: float = DEFAULT_MAX_BOND_ANGLE_DEG,
           min_die_edge_clearance_mm: float = DEFAULT_MIN_DIE_EDGE_CLEARANCE_MM,
           min_stack_clearance_mm: float = DEFAULT_MIN_STACK_CLEARANCE_MM):
    """
    Check every Trace_NNN / BondWire_NNN in *doc* for violations, by running
    every registered rule (see RULES) against a shared context. Returns
    list[DRCFinding]; severity is "violation" or "warning".
    """
    findings = []
    if doc is None:
        return findings

    routed = [o for o in doc.Objects
              if (_is_routing_trace(o) or _is_bond_wire(o)) and _has_geometry(o)]
    # The covered-pad rule is about pads, not wires: a stack can bury a pad
    # the moment a die is placed, which is exactly when it is worth hearing
    # about, long before anything is bonded.
    if not routed and not _contact_points(doc):
        return findings

    params = {
        "min_clearance_mm": min_clearance_mm,
        "min_trace_width_mm": min_trace_width_mm,
        "min_wire_spacing_mm": min_wire_spacing_mm,
        "min_lid_clearance_mm": min_lid_clearance_mm,
        "min_wire_length_mm": min_wire_length_mm,
        "max_wire_length_mm": max_wire_length_mm,
        "max_bond_angle_deg": max_bond_angle_deg,
        "min_die_edge_clearance_mm": min_die_edge_clearance_mm,
        "min_stack_clearance_mm": min_stack_clearance_mm,
    }
    ctx = _DRCContext(doc, routed, params)

    for _rule_id, fn in RULES:
        findings.extend(fn(ctx))
    return findings