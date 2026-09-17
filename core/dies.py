# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Dies, tiers and die attach — what a multi-die or stacked assembly is made of.

Until now a die was a block with some contact points near it, and nothing
recorded which pads belong to which die or which die sits on which. That is
enough for a single die in a package and falls apart for a stack: pads on an
upper die count as being on the die below it, a wire between two dies belongs
to neither, and a netlist proposal pairs every pad of every die as though
they formed one ring.

This module gives a die an identity:

    DieName   "U1", "U2", … — what pads, nets and reports refer to
    DieTier   0 for the die on the carrier, 1 for the one above it, …
    DieBelow  the die this one is stacked on, "" at tier 0

It is DERIVED from geometry that is already in the document and then
recorded, so an existing assembly gains it without being rebuilt, and a name
someone has changed is never overwritten.

A die is a chip proxy block, or the slabs of a die body from a full import
grouped by their footprint — with its top taken at the highest layer standing
on it, which is where its pads and its top edge are.

Qt-free, so it is testable headlessly.
"""

import FreeCAD
import Part

DIE_NAME = "DieName"
DIE_TIER = "DieTier"
DIE_BELOW = "DieBelow"
PAD_DIE = "DieName"          # the same property name, on a contact point
IS_DIE_ATTACH = "IsDieAttach"

_GROUP = "Die"

# A pad sits on the die's top face, give or take the thickness of its marker.
PAD_Z_TOL_MM = 0.05
# Two surfaces this close are touching — one die resting on another.
STACK_TOL_MM = 0.05
# Overlap smaller than this in plan view is a rounding artefact, not a stack.
_MIN_OVERLAP_MM2 = 1e-6


class Die:
    """One die in the document, with what the assembly needs to know."""

    def __init__(self, name, label, block, objects, outline, z_bottom, z_top):
        self.name = name
        self.label = label
        self.block = block           # the object the properties live on
        self.objects = objects       # every object making up the die
        self.outline = outline       # (xmin, ymin, xmax, ymax)
        self.z_bottom = z_bottom
        self.z_top = z_top
        self.tier = 0
        self.below = ""

    @property
    def width_mm(self):
        return self.outline[2] - self.outline[0]

    @property
    def length_mm(self):
        return self.outline[3] - self.outline[1]

    @property
    def thickness_mm(self):
        return self.z_top - self.z_bottom

    def covers(self, x, y):
        x0, y0, x1, y1 = self.outline
        return x0 <= x <= x1 and y0 <= y <= y1

    def __repr__(self):
        return (f"<Die {self.name} tier {self.tier} "
                f"{self.width_mm:.3f} x {self.length_mm:.3f} mm>")


# ── finding the dies ─────────────────────────────────────────────────────────

def _bound_box(obj):
    try:
        bb = obj.Shape.BoundBox
    except Exception:
        return None
    return bb if bb.isValid() and bb.XLength > 0 and bb.YLength > 0 else None


def _overlap_mm2(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = min(ax1, bx1) - max(ax0, bx0)
    dy = min(ay1, by1) - max(ay0, by0)
    return dx * dy if dx > 0 and dy > 0 else 0.0


def _layers_on(doc, outline, tol=1e-6):
    """GDS layers standing within *outline* — a die body's real top."""
    tops = []
    x0, y0, x1, y1 = outline
    for obj in doc.Objects:
        if not hasattr(obj, "GDSLayerID"):
            continue
        bb = _bound_box(obj)
        if bb is None:
            continue
        if (bb.XMin >= x0 - tol and bb.XMax <= x1 + tol
                and bb.YMin >= y0 - tol and bb.YMax <= y1 + tol):
            tops.append(bb.ZMax)
    return tops


def collect(doc):
    """
    Every die in *doc*, tiers worked out, bottom die first.

    Chip proxies are one die each. Die-body slabs are grouped by footprint,
    since substrate, epi and the dielectric fill are one die between them.
    """
    dies = []
    for obj in doc.Objects:
        if not getattr(obj, "IsChipProxy", False):
            continue
        bb = _bound_box(obj)
        if bb is None:
            continue
        outline = (bb.XMin, bb.YMin, bb.XMax, bb.YMax)
        dies.append(Die(getattr(obj, DIE_NAME, "") or "", obj.Label, obj,
                        [obj], outline, bb.ZMin, bb.ZMax))

    bodies = {}
    for obj in doc.Objects:
        if not getattr(obj, "IsDieBody", False):
            continue
        bb = _bound_box(obj)
        if bb is None:
            continue
        key = tuple(round(v, 6) for v in (bb.XMin, bb.YMin, bb.XMax, bb.YMax))
        entry = bodies.setdefault(key, {"objects": [], "z0": bb.ZMin, "z1": bb.ZMax})
        entry["objects"].append(obj)
        entry["z0"] = min(entry["z0"], bb.ZMin)
        entry["z1"] = max(entry["z1"], bb.ZMax)
    for outline, entry in bodies.items():
        block = entry["objects"][0]
        z_top = max([entry["z1"]] + _layers_on(doc, outline))
        dies.append(Die(getattr(block, DIE_NAME, "") or "", block.Label, block,
                        entry["objects"], outline, entry["z0"], z_top))

    dies.sort(key=lambda d: (d.z_bottom, d.outline))
    _assign_tiers(dies)
    return dies


def _assign_tiers(dies):
    """A die is on the highest die whose top it rests on and overlaps."""
    for die in dies:
        base = None
        for other in dies:
            if other is die or other.z_top > die.z_bottom + STACK_TOL_MM:
                continue
            if _overlap_mm2(die.outline, other.outline) <= _MIN_OVERLAP_MM2:
                continue
            if base is None or other.z_top > base.z_top:
                base = other
        if base is not None:
            die.below = base.name or base.block.Name
            die.tier = base.tier + 1


def _ensure(obj, prop, ptype, doc_string):
    if not hasattr(obj, prop):
        obj.addProperty(ptype, prop, _GROUP, doc_string)


def identify(doc, prefix="U"):
    """
    Give every die a name, a tier and its base, and every pad the name of the
    die it is on. Names already set — by an earlier run or by hand — are kept.

    Returns the dies, bottom first.
    """
    dies = collect(doc)
    used = {die.name for die in dies if die.name}
    counter = 1
    for die in dies:
        if not die.name:
            while f"{prefix}{counter}" in used:
                counter += 1
            die.name = f"{prefix}{counter}"
            used.add(die.name)
        _ensure(die.block, DIE_NAME, "App::PropertyString",
                "Name this die is referred to by in netlists and reports")
        _ensure(die.block, DIE_TIER, "App::PropertyInteger",
                "0 on the carrier, 1 for the die above it, and so on")
        _ensure(die.block, DIE_BELOW, "App::PropertyString",
                "The die this one is stacked on")
        die.block.DieName = die.name
        die.block.DieTier = die.tier
        die.block.DieBelow = die.below

    # The tiers were worked out before the names existed, so a base recorded
    # by object name is turned into the die name now.
    by_object = {die.block.Name: die.name for die in dies}
    for die in dies:
        if die.below in by_object:
            die.below = by_object[die.below]
            die.block.DieBelow = die.below

    for die in dies:
        for pad in pads_of(doc, die):
            _ensure(pad, PAD_DIE, "App::PropertyString",
                    "The die this pad belongs to")
            pad.DieName = die.name
    return dies


# ── pads ─────────────────────────────────────────────────────────────────────

def _contact_points(doc):
    return [o for o in doc.Objects if getattr(o, "IsContactPoint", False)]


def _position(pad):
    point = getattr(pad, "ContactPoint", None)
    return FreeCAD.Vector(point) if point is not None else None


def on_die(point, die, tol_mm=PAD_Z_TOL_MM):
    """
    True when *point* is a pad of *die*: inside its outline and on its top
    face, within a marker's thickness EITHER WAY.

    The upper bound is what tells the tiers of a stack apart. Without it a pad
    on the die above — inside the same outline, simply higher — counts as
    being on this one as well.
    """
    if point is None or not die.covers(point.x, point.y):
        return False
    return abs(point.z - die.z_top) <= tol_mm


def die_of(dies, point, tol_mm=PAD_Z_TOL_MM):
    """
    The die *point* sits on, or None.

    The one whose top is NEAREST, not merely the first within tolerance: a
    thin die's pads are within a marker's thickness of the die below it too,
    so "the first that fits" gives that die's pads to both tiers.
    """
    candidates = [die for die in dies if on_die(point, die, tol_mm)]
    if not candidates:
        return None
    return min(candidates, key=lambda die: (abs(point.z - die.z_top), -die.z_top))


def pads_of(doc, die, tol_mm=PAD_Z_TOL_MM, dies=None):
    """The contact points on *die*: the ones its own block made, plus any
    placed by hand on its top face and nearer to it than to any other die."""
    others = collect(doc) if dies is None else dies
    pads = []
    for pad in _contact_points(doc):
        if getattr(pad, "SourceObject", "") == die.block.Name:
            pads.append(pad)
            continue
        owner = die_of(others, _position(pad), tol_mm)
        if owner is not None and owner.block.Name == die.block.Name:
            pads.append(pad)
    return pads


def covered_pads(doc, dies):
    """
    [(pad, die, covering die)] for every pad a higher die sits over.

    A pad the tier above covers cannot be bonded — it is the mistake stacking
    dies invites, and it is invisible from above in the 3-D view.
    """
    found = []
    for die in dies:
        for pad in pads_of(doc, die, dies=dies):
            point = _position(pad)
            if point is None:
                continue
            for other in dies:
                if other is die or other.z_bottom < die.z_top - STACK_TOL_MM:
                    continue
                if other.covers(point.x, point.y):
                    found.append((pad, die, other))
                    break
    return found


def overhang_mm2(die, base):
    """How much of *die*'s footprint hangs over nothing — its area minus the
    part supported by *base*."""
    area = die.width_mm * die.length_mm
    return max(0.0, area - _overlap_mm2(die.outline, base.outline))


# ── moving a die ─────────────────────────────────────────────────────────────

def objects_of(doc, die):
    """Everything that must move with the die: its own objects, its pads, and
    the die attach under it."""
    moving = list(die.objects)
    names = {o.Name for o in moving}
    for pad in pads_of(doc, die):
        if pad.Name not in names:
            moving.append(pad)
            names.add(pad.Name)
    for obj in doc.Objects:
        if (getattr(obj, IS_DIE_ATTACH, False)
                and getattr(obj, DIE_NAME, "") == die.name
                and obj.Name not in names):
            moving.append(obj)
            names.add(obj.Name)
    return moving


def move_die(doc, die, delta):
    """
    Move a die and everything belonging to it by *delta*.

    A contact point's stored position moves with its marker: it is a property,
    not geometry, so a placement alone leaves it behind — pointing at where
    the pad used to be, which is where a wire would then be bonded.
    """
    for obj in objects_of(doc, die):
        placement = obj.Placement
        placement.Base = placement.Base + delta
        obj.Placement = placement
        point = getattr(obj, "ContactPoint", None)
        if point is not None:
            obj.ContactPoint = FreeCAD.Vector(point) + delta
    die.outline = (die.outline[0] + delta.x, die.outline[1] + delta.y,
                   die.outline[2] + delta.x, die.outline[3] + delta.y)
    die.z_bottom += delta.z
    die.z_top += delta.z
    return die


# ── die attach ───────────────────────────────────────────────────────────────

def build_die_attach(doc, die, base, thickness_mm, material="Die attach epoxy"):
    """
    The adhesive layer between a die and what it sits on, spanning the part of
    the die that is actually supported.

    It is real geometry because it carries the heat out of the die and sets
    the height of everything above it: leaving it out makes a stack look
    thinner than it is and understates every loop height in it.
    """
    if thickness_mm <= 0.0:
        return None
    x0 = max(die.outline[0], base.outline[0])
    y0 = max(die.outline[1], base.outline[1])
    x1 = min(die.outline[2], base.outline[2])
    y1 = min(die.outline[3], base.outline[3])
    if x1 - x0 <= 0.0 or y1 - y0 <= 0.0:
        raise ValueError(
            f"{die.name} does not sit over {base.name}, so there is nothing "
            f"for the die attach to bond to.")

    obj = doc.addObject("Part::Feature", f"DieAttach_{die.name}")
    obj.Shape = Part.makeBox(x1 - x0, y1 - y0, thickness_mm,
                             FreeCAD.Vector(x0, y0, die.z_bottom - thickness_mm))
    obj.Label = f"Die attach under {die.name} ({thickness_mm * 1000.0:.0f} µm)"
    _ensure(obj, IS_DIE_ATTACH, "App::PropertyBool",
            "Adhesive layer between a die and what it sits on")
    _ensure(obj, DIE_NAME, "App::PropertyString", "The die this attaches")
    obj.IsDieAttach = True
    obj.DieName = die.name

    try:
        from core import materials
        materials.set_material(obj, material, "default: die attach")
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[Dies] die attach built without a material: {exc}\n")

    if FreeCAD.GuiUp and getattr(obj, "ViewObject", None) is not None:
        obj.ViewObject.ShapeColor = (0.35, 0.30, 0.25)
        obj.ViewObject.Transparency = 20
    return obj


def stack_die(doc, die, base, attach_mm=0.025, centre=True):
    """
    Put *die* on *base*: move it onto the base's top face with the die attach
    between them, build that attach, and report what the stack now hides.

    Returns {"delta", "attach", "overhang_mm2", "covered_pads", "tier"}.
    """
    if die is base:
        raise ValueError("A die cannot be stacked on itself.")
    dx = dy = 0.0
    if centre:
        die_cx = (die.outline[0] + die.outline[2]) / 2.0
        die_cy = (die.outline[1] + die.outline[3]) / 2.0
        base_cx = (base.outline[0] + base.outline[2]) / 2.0
        base_cy = (base.outline[1] + base.outline[3]) / 2.0
        dx, dy = base_cx - die_cx, base_cy - die_cy
    dz = (base.z_top + attach_mm) - die.z_bottom
    delta = FreeCAD.Vector(dx, dy, dz)
    move_die(doc, die, delta)

    attach = build_die_attach(doc, die, base, attach_mm)
    die.tier = base.tier + 1
    die.below = base.name
    _ensure(die.block, DIE_TIER, "App::PropertyInteger",
            "0 on the carrier, 1 for the die above it, and so on")
    _ensure(die.block, DIE_BELOW, "App::PropertyString",
            "The die this one is stacked on")
    die.block.DieTier = die.tier
    die.block.DieBelow = die.below

    dies = collect(doc)
    covered = [(pad.Name, holder.name, cover.name)
               for pad, holder, cover in covered_pads(doc, dies)]
    return {
        "delta": delta,
        "attach": attach,
        "overhang_mm2": overhang_mm2(die, base),
        "covered_pads": covered,
        "tier": die.tier,
    }
