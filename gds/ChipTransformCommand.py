# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Chip Transform Tool

Opens a modeless dialog to translate and rotate all GDS chip objects
(Layer_*, IC_Body_Solid, GDS_Pin_*, GDS_PINs_*, ContactPoint_*, contact_point_*,
BondWire_*, WireBump_*, GridPt*) as a group,
or to operate on the current FreeCAD selection.

Substrate / encapsulant frame objects (Substrate_Frames group, created by
DetailLayerPanel._build_frames) are automatically included in the
"GDS Chip Objects" scope so they move together with the chip layers.

Chip Proxy (pick one):
  When multiple chips have been imported as lightweight proxies
  (core.chip_proxy), "GDS Chip Objects" moves ALL of them at once — not
  useful when placing chips one at a time into a shared package. Use this
  scope instead: pick a single "<name> (Proxy)" group from the dropdown,
  optionally include the "Package" (leadframe) group and/or the PCB, and
  only that chip (+ the checked companions) moves/rotates together.

Align to Selection:
  Pick any object in the FreeCAD 3D view, then use the Align section:
  • "Snap Z (bottom → surface)"  — moves the chip group so its lowest Z
    face sits exactly on the top face of the selected object.
  • "Center XY on click point"   — moves the chip group so its XY bounding-
    box center aligns with the XY position of the last-picked vertex/face
    center on the selected object.
  • "Both"                       — applies Z-snap and XY-center together.

Translation and rotation are applied incrementally via buttons or keyboard
shortcuts when the dialog has keyboard focus.

Keyboard shortcuts (dialog must be focused):
  ←/→         ±X translation
  ↑/↓         ±Y translation
  PgUp/PgDn   ±Z translation
  Shift+←/→   ±Rz rotation (around Z)
  Shift+↑/↓   ±Rx rotation (around X)
  Shift+PgUp/Dn ±Ry rotation (around Y)
"""

import os
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore, QtGui

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)
from Get_Path import get_icon

# Bump this whenever the "Current Selection" scope logic changes. Printed
# once at import so a stale/cached module (e.g. FreeCAD process not
# actually restarted after an on-disk edit) is immediately visible in the
# Report View instead of silently reproducing an already-fixed bug.
_MODULE_VERSION = "2026-07-21 per-proxy chip scope + package/pcb v6"
FreeCAD.Console.PrintMessage(
    f"[DI-PASSIONATE] ChipTransformCommand loaded — {_MODULE_VERSION}\n"
    f"    file: {os.path.abspath(__file__)}\n"
)


# ── GDS object detection ───────────────────────────────────────────────────────

# All name prefixes that belong to the GDS chip (case-sensitive match against
# the actual names produced by GDSCommand, Core_Functionality, SetContactPoints,
# ManualWireBonding, and WireBumpConfigurator).
_GDS_PREFIXES = (
    "Layer_",            # GDSCommand / LayeronLeadframe: Layer_{name}_{id}[_{dt}]
    "IC_Body_Solid",     # GDSCommand contacts_only_3d body
    "GDS_Pin_",          # GDSCommand: GDS_Pin_Instances
    "GDS_PINs_",         # Core_Functionality auto-PIN: GDS_PINs_{edi_name}  (uppercase!)
    "ContactPoint_",     # ContactPointTool / Core_Functionality: ContactPoint_{n}
    "contact_point_",    # SetContactPointsOnFaceCommand: contact_point_housing_{n}
    "BondWire_",         # ManualWireBonding: BondWire_{n}
    "WireBump_",         # WireBumpConfigurator: WireBump_{shape}_{n}
    "GridPt",            # SetContactPointsOnFaceCommand grid markers (if any remain)
)

# Names / prefixes for substrate / encapsulant frame objects produced by
# DetailLayerPanel._build_frames().  These live in the "Substrate_Frames" group
# but must move together with the GDS chip objects.
_FRAME_NAMES = (
    "Encapsulant_Frame",   # Part::Feature created by _build_frames (Name)
)


def _has_geometry(o):
    """True for objects carrying movable geometry — B-rep (Shape) or mesh (Mesh)."""
    return hasattr(o, "Shape") or hasattr(o, "Mesh")


def _obj_boundbox(o):
    """Local bounding box of *o* regardless of whether it is a B-rep or a mesh."""
    shp = getattr(o, "Shape", None)
    if shp is not None:
        try:
            return shp.BoundBox
        except Exception:
            pass
    msh = getattr(o, "Mesh", None)
    if msh is not None:
        try:
            return msh.BoundBox
        except Exception:
            pass
    return None


def _all_objects(doc):
    """
    Every object in the document that carries movable geometry and a Placement.

    Includes both B-rep (Part::Feature, .Shape) and mesh (Mesh::Feature, .Mesh)
    objects, so layers imported as meshes move with the rest of the chip.
    """
    return [
        o for o in (doc.Objects if doc else [])
        if hasattr(o, "Placement") and _has_geometry(o)
    ]


def _gds_objects(doc):
    """GDS chip objects only — excludes leadframe, housing, sketches, etc.

    Includes:
    - All objects whose Name starts with one of _GDS_PREFIXES
    - Any core.chip_proxy lightweight chip (IsChipProxy block + its
      IsContactPoint pad markers) — matched by property, not name prefix,
      since a proxy's "<name>_Block"/"<name>_Pad_NNN" names don't match
      any entry in _GDS_PREFIXES at all. Without this, "GDS Chip Objects"
      scope would silently see zero objects to move for a chip-proxy-only
      document.
    - All objects inside the Substrate_Frames group so that encapsulant frame
      extrusions move together with the chip layers.
    - The die body — the epi and substrate slabs built under the layout by
      core.substrate — matched by their IsDieBody property. Name prefixes do
      not catch them: they are called "GDS_Substrate"/"GDS_EPI" while
      _GDS_PREFIXES only lists the more specific "GDS_Pin_"/"GDS_PINs_", so
      moving a chip left its own silicon behind. Matching by property rather
      than by adding a broad "GDS_" prefix follows the same reasoning as the
      chip-proxy case above, and survives the objects being relabelled.

      This also corrects "place chip on surface": the chip's bottom is now
      the underside of the silicon rather than the lowest metal, which is
      what should actually land on a carrier.
    """
    objs = [
        o for o in _all_objects(doc)
        if any(o.Name.startswith(p) for p in _GDS_PREFIXES)
        or getattr(o, "IsChipProxy", False)
        or getattr(o, "IsContactPoint", False)
        or getattr(o, "IsDieBody", False)
        # Ports are drawn on the layout's own edges, so they belong to the
        # chip and have to travel with it — matched by property for the same
        # reason as the die body above.
        or getattr(o, "IsPort", False)
    ]

    # Pull in every frame object from the Substrate_Frames group.
    # We look up by group name/label rather than by object-name prefix so that
    # multiple frame objects (one per layer, future extension) are all included
    # automatically without touching _FRAME_NAMES.
    frames_grp = next(
        (o for o in (doc.Objects if doc else [])
         if o.Name == "Substrate_Frames" or o.Label == "Substrate Frames"),
        None,
    )
    if frames_grp is not None:
        grp_members = getattr(frames_grp, "Group", [])
        for fo in grp_members:
            if hasattr(fo, "Placement") and _has_geometry(fo) and fo not in objs:
                objs.append(fo)

    return objs


def _chip_proxy_groups(doc):
    """
    Every "<name> (Proxy)" group in *doc* — one per chip imported via
    core.chip_proxy — identified by containing at least one member with
    IsChipProxy=True (the die block). Used to populate the "Chip Proxy
    (pick one)" scope's dropdown so multiple imported chips can be told
    apart and moved independently instead of all at once.
    """
    if doc is None:
        return []
    return [
        o for o in doc.Objects
        if o.TypeId == "App::DocumentObjectGroup"
        and any(getattr(m, "IsChipProxy", False) for m in getattr(o, "Group", []))
    ]


def _package_group(doc):
    """The single "Package" (leadframe) group created by core.leadframe /
    leadframe.LeadframeLibrary, or None if no leadframe has been built."""
    if doc is None:
        return None
    return next(
        (o for o in doc.Objects
         if o.TypeId == "App::DocumentObjectGroup"
         and (o.Name == "Package" or o.Label == "Package")),
        None,
    )


def _pcb_root_objects(doc):
    """
    All root-level PCB body objects, via the IsPCBBoard-tagged anchor's
    PCBBodyObjects property (see pcb.PCBImportCommand) — a PCB import can
    produce several root-level bodies, only one of which carries the tag,
    so reading just the tagged object itself would silently leave its
    sibling bodies behind.
    """
    if doc is None:
        return []
    anchor = next((o for o in doc.Objects if getattr(o, "IsPCBBoard", False)), None)
    if anchor is None:
        return []
    names = [n for n in (getattr(anchor, "PCBBodyObjects", "") or "").split(",") if n]
    return [obj for obj in (doc.getObject(n) for n in names) if obj is not None]


# Name suffixes of "display proxy" companion objects that live in a
# SEPARATE sibling group from their source shape — see
# gds.ToggleViaDetailCommand (simplified via blocks). Whichever one is
# currently visible is what the user actually sees in the 3-D view, so
# moving only the source and not its companion (or vice versa) silently
# leaves the on-screen geometry behind even though the "wrong" (hidden)
# copy did move.
#
# "_PerfMesh" was also listed here, for the fast-mesh companions that
# gds.TogglePerformanceModeCommand baked. That feature was removed and no
# such object is created any more. Documents saved before then may still
# contain them; they are ordinary Mesh objects and move with the group they
# sit in, so nothing is stranded.
_COMPANION_SUFFIXES = ("_ViaBlock",)


def _proxy_group_of(o):
    """
    If *o* is a lightweight chip-layout proxy block (core.chip_proxy,
    tagged IsChipProxy) or one of its pad markers (a ContactPoint whose
    SourceObject is such a block), return the "<name> (Proxy)" group that
    contains both — so selecting EITHER the block alone or a single pad
    alone still moves the whole chip atomically. Without this, moving just
    the block would leave its pad markers behind (desyncing contact points
    from the die surface they're supposed to sit on), and moving just one
    pad would move nothing useful at all.

    Returns None for anything unrelated to a chip proxy (ordinary GDS
    layers, leadframe parts, bond wires, …) — a pure no-op for every other
    object type this function is asked about.
    """
    is_proxy_block = bool(getattr(o, "IsChipProxy", False))
    is_proxy_pad = False
    if not is_proxy_block and getattr(o, "IsContactPoint", False):
        doc = getattr(o, "Document", None)
        src_name = getattr(o, "SourceObject", "") or ""
        src_obj = doc.getObject(src_name) if (doc is not None and src_name) else None
        is_proxy_pad = src_obj is not None and bool(getattr(src_obj, "IsChipProxy", False))

    if not (is_proxy_block or is_proxy_pad):
        return None

    for parent in o.InList:
        if parent.TypeId == "App::DocumentObjectGroup":
            return parent
    return None


def _expand_selection(root_objects, doc=None):
    """
    Expand a list of directly-selected objects into the full set of
    geometry-bearing objects that should move/rotate/align together.
    Split out from _selected_objects() so this — the actual logic worth
    testing — doesn't require a live FreeCADGui.Selection (unavailable
    headlessly) to exercise.

    Three independent expansions happen per selected item, matching what
    "GDS Chip Objects" scope already gets "for free" via its flat
    name-prefix scan of the whole document (_gds_objects):

    1. A chip-layout proxy's block OR any single one of its pad markers is
       redirected to its containing "<name> (Proxy)" group — see
       _proxy_group_of(). Applied ONLY to the raw, top-level selected
       objects (_add_root), never during the recursive member-expansion
       below (_add_member): the group's own children include that same
       block and those same pads, and if the redirect fired there too,
       the block would just redirect straight back to the group every
       time it's visited as a child — never actually reaching the
       "append to result" branch, silently dropping it (and every pad)
       from the result entirely.

    2. Any container (an object with a .Group property — App::DocumentObjectGroup,
       App::Part, …) is ALWAYS expanded to its children, recursively, in
       preference over being treated as a movable object itself — even
       though a plain App::DocumentObjectGroup in FreeCAD 1.1 exposes its
       own synthetic Shape/Placement (for bounding-box/compound display),
       that Placement is NOT propagated to its children at all, so writing
       to it is a silent no-op. Checking hasattr(o, "Group") FIRST (before
       _has_geometry) is what makes selecting a folder like "ContactPoints"
       or "GDS_Die" actually move its contents instead of doing nothing.

    3. Any object reached this way that has a currently-displayed
       "<Name>_ViaBlock" simplified block pulls that block in too — it lives
       in a SEPARATE sibling group ("GDS Via Blocks"), so expanding only the
       source object's own group would move the (often hidden) original
       shape while the actually visible block stays put. A "<Name>_PerfMesh"
       fast-mesh companion was handled here too until that feature was
       removed.
    """
    if doc is None:
        doc = FreeCAD.activeDocument()
    result = []
    seen = set()

    def _add_member(o):
        if o.Name in seen:
            return
        seen.add(o.Name)

        if hasattr(o, "Group"):
            for child in o.Group:
                _add_member(child)
        elif _has_geometry(o):
            result.append(o)
            if doc is not None:
                for suffix in _COMPANION_SUFFIXES:
                    companion = doc.getObject(o.Name + suffix)
                    if companion is not None:
                        _add_member(companion)

    def _add_root(o):
        proxy_grp = _proxy_group_of(o)
        _add_member(proxy_grp if proxy_grp is not None else o)

    for o in root_objects:
        _add_root(o)

    return result


def _selected_objects():
    """Every geometry-bearing object referenced by the current FreeCAD
    selection — see _expand_selection() for the actual expansion rules."""
    return _expand_selection(
        [s.Object for s in FreeCADGui.Selection.getSelectionEx()]
    )


def _bounding_center(objects):
    """World-space bounding box centre of a list of shaped/mesh objects."""
    xmin = ymin = zmin = float("inf")
    xmax = ymax = zmax = float("-inf")
    for obj in objects:
        b = _obj_boundbox(obj)
        if b is None:
            continue
        xmin = min(xmin, b.XMin); xmax = max(xmax, b.XMax)
        ymin = min(ymin, b.YMin); ymax = max(ymax, b.YMax)
        zmin = min(zmin, b.ZMin); zmax = max(zmax, b.ZMax)
    if xmin == float("inf"):
        return FreeCAD.Vector(0, 0, 0)
    return FreeCAD.Vector((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)


# ── Low-level transform helpers ────────────────────────────────────────────────

def _move_contact_point(obj, how):
    """
    Move the position a contact point STORES, not only the marker drawn at it.

    A ContactPoint marker keeps its position in a property, and that property
    is what wire bonding, netlist matching and every design rule read — the
    marker's placement is only what you see. Moving a chip without it leaves
    every pad claiming to be where it used to be, so the next bond lands at
    the old spot.
    """
    point = getattr(obj, "ContactPoint", None)
    if point is None:
        return
    try:
        obj.ContactPoint = how(FreeCAD.Vector(point))
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[ChipTransform] could not move the stored position of "
            f"'{obj.Name}': {exc}\n")


def _translate_objects(objects, dx, dy, dz):
    v = FreeCAD.Vector(dx, dy, dz)
    for obj in objects:
        obj.Placement = FreeCAD.Placement(
            obj.Placement.Base + v,
            obj.Placement.Rotation,
        )
        _move_contact_point(obj, lambda p: p + v)


def _rotate_objects(objects, axis_vec, angle_deg, center):
    """Rotate objects around *center* by *angle_deg* about *axis_vec* (world frame)."""
    import math
    half = math.radians(angle_deg) / 2.0
    s = math.sin(half)
    rot = FreeCAD.Rotation(axis_vec.x * s, axis_vec.y * s, axis_vec.z * s, math.cos(half))
    for obj in objects:
        old_pos = obj.Placement.Base
        old_rot = obj.Placement.Rotation
        rel     = old_pos - center
        new_pos = rot.multVec(rel) + center
        new_rot = rot * old_rot          # * is the correct compose operator in FreeCAD
        obj.Placement = FreeCAD.Placement(new_pos, new_rot)
        _move_contact_point(obj, lambda p: rot.multVec(p - center) + center)


def _restore_placements(objects, saved):
    """Restore placements — and the positions contact points store — from a
    {name: (Placement, ContactPoint or None)} snapshot."""
    for obj in objects:
        entry = saved.get(obj.Name)
        if entry is None:
            continue
        placement, point = entry if isinstance(entry, tuple) else (entry, None)
        obj.Placement = placement.copy()
        if point is not None and hasattr(obj, "ContactPoint"):
            obj.ContactPoint = FreeCAD.Vector(point)


# ── Align helpers ──────────────────────────────────────────────────────────────

def _world_placement_of(obj):
    """Accumulated world Placement for *obj*, walking up the InList chain."""
    pl = obj.Placement.copy()
    current = obj
    while current.InList:
        parent = current.InList[0]
        if hasattr(parent, "Placement"):
            pl = parent.Placement.multiply(pl)
        current = parent
    return pl


def _parent_placement_of(obj):
    """
    The placement of everything ABOVE *obj* — its containers only, with the
    object's own Placement deliberately left out.

    This is the correct transform to apply to geometry read out of
    obj.Shape: verified against FreeCAD 1.1, a Part::Feature's Shape
    already carries its OWN Placement, while an App::Part container's
    Placement is not baked in. (_world_placement_of above includes the
    object's own placement as well, which is right for composing placements
    but double-applies it when used on shape geometry — harmless while the
    object sits at the origin, which is why it has not bitten the existing
    click-point path.)
    """
    pl = FreeCAD.Placement()
    current = obj
    while current.InList:
        parent = current.InList[0]
        if hasattr(parent, "Placement"):
            pl = parent.Placement.multiply(pl)
        current = parent
    return pl


def _picked_faces(sel_ex):
    """Every Part.Face the user clicked, in WORLD coordinates. Empty when
    the selection contains no face."""
    obj = getattr(sel_ex, "Object", None)
    if obj is None or not hasattr(obj, "Shape"):
        return []
    parent = _parent_placement_of(obj)
    out = []
    for sub_name in (getattr(sel_ex, "SubElementNames", None) or []):
        if not sub_name.startswith("Face"):
            continue
        try:
            face = obj.Shape.getElement(sub_name)
        except Exception:
            continue
        if not parent.isIdentity():
            face = face.copy()
            face.transformShape(parent.toMatrix())
        out.append(face)
    return out


def _face_center_xy(sel_ex):
    """
    World (x, y) of the CENTRE of the clicked face(s), or None if no face
    was clicked.

    Deliberately ignores PickedPoints (where exactly the click landed) and
    the area centroid — see core.face_symmetry for why the outer-boundary
    midpoint is the right notion of "the middle of this pad".

    With several faces selected the centre of their COMBINED extent is used,
    so a pad split across two faces (or a land plus its thermal tab) still
    centres on the thing as a whole rather than on whichever face happened
    to be first.
    """
    faces = _picked_faces(sel_ex)
    if not faces:
        return None

    if len(faces) == 1:
        try:
            import core.face_symmetry as face_symmetry
            c = face_symmetry.face_center_world(faces[0])
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[ChipAlign] face centre failed: {exc}\n")
            return None
        return (c.x, c.y) if c is not None else None

    # Several faces: the middle of their COMBINED extent. Deliberately the
    # union of the real extents rather than the average of the individual
    # centres, which a large face and a small one would skew toward the side
    # contributing more faces.
    xs, ys = [], []
    for f in faces:
        try:
            bb = f.BoundBox
        except Exception:
            continue
        if not bb.isValid():
            continue
        xs.extend((bb.XMin, bb.XMax))
        ys.extend((bb.YMin, bb.YMax))
    if not xs:
        return None
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def _world_bbox_of_objects(objects):
    """
    Return a world-space FreeCAD.BoundBox by applying each object's full
    accumulated Placement (including parent containers) to its local bbox corners.
    Returns None when no valid geometry is found.
    """
    xmin = ymin = zmin = float("inf")
    xmax = ymax = zmax = float("-inf")
    found = False
    for obj in objects:
        try:
            bb = _obj_boundbox(obj)
            if bb is None or not bb.isValid():
                continue
            mat = _world_placement_of(obj).toMatrix()
            for lx in (bb.XMin, bb.XMax):
                for ly in (bb.YMin, bb.YMax):
                    for lz in (bb.ZMin, bb.ZMax):
                        wp = mat.multVec(FreeCAD.Vector(lx, ly, lz))
                        if wp.x < xmin: xmin = wp.x
                        if wp.x > xmax: xmax = wp.x
                        if wp.y < ymin: ymin = wp.y
                        if wp.y > ymax: ymax = wp.y
                        if wp.z < zmin: zmin = wp.z
                        if wp.z > zmax: zmax = wp.z
            found = True
        except Exception:
            continue
    if not found:
        return None
    return FreeCAD.BoundBox(xmin, ymin, zmin, xmax, ymax, zmax)


def _chip_zmin(objects):
    """Return the world-space minimum Z coordinate (bottom face) of the chip group."""
    bb = _world_bbox_of_objects(objects)
    return bb.ZMin if bb is not None else 0.0


def _chip_xy_center(objects):
    """Return the world-space XY bounding-box centre of the chip group as (cx, cy)."""
    bb = _world_bbox_of_objects(objects)
    if bb is None:
        return 0.0, 0.0
    return (bb.XMin + bb.XMax) / 2.0, (bb.YMin + bb.YMax) / 2.0


def _target_z_top(obj):
    """Return the world-space highest Z coordinate (top surface) of a target object."""
    bb = _world_bbox_of_objects([obj])
    if bb is not None:
        return bb.ZMax
    return 0.0


def _target_z_snap(sel_ex):
    """Return the world-space Z to snap the chip bottom to.

    Priority order:
      1. PickedPoints[0].z — the exact world Z of the 3-D click position.
         Already in world space, unaffected by Placement chain issues.
         This is the same source used by _target_xy_pick for XY centering.
      2. Sub-element CenterOfMass transformed to world space (fallback when
         no click point is stored, e.g. selection loaded from Python).
      3. Whole-object world ZMax (last resort).
    """
    # Priority 1: world-space click position Z (reliable regardless of
    # Placement chain complexity or face coordinate system)
    try:
        pts = sel_ex.PickedPoints
        if pts:
            FreeCAD.Console.PrintMessage(
                f"[ChipAlign] _target_z_snap: PickedPoint Z={pts[0].z:.4f}\n"
            )
            return pts[0].z
    except Exception:
        pass

    # Priority 2: face/edge CenterOfMass in world space
    subs = list(sel_ex.SubElementNames or [])
    try:
        world_mat = _world_placement_of(sel_ex.Object).toMatrix()
        for sub_name in subs:
            sub = sel_ex.Object.Shape.getElement(sub_name)
            if hasattr(sub, "CenterOfMass"):
                wp = world_mat.multVec(sub.CenterOfMass)
                FreeCAD.Console.PrintMessage(
                    f"[ChipAlign] _target_z_snap: {sub_name} CenterOfMass world Z={wp.z:.4f}\n"
                )
                return wp.z
            if hasattr(sub, "Vertexes") and sub.Vertexes:
                z_max = max(world_mat.multVec(v.Point).z for v in sub.Vertexes)
                FreeCAD.Console.PrintMessage(
                    f"[ChipAlign] _target_z_snap: {sub_name} vertex ZMax={z_max:.4f}\n"
                )
                return z_max
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[ChipAlign] _target_z_snap sub-element failed: {exc}\n")

    # Priority 3: whole object world ZMax
    z = _target_z_top(sel_ex.Object)
    FreeCAD.Console.PrintMessage(
        f"[ChipAlign] _target_z_snap: no sub-element ({subs}), using object ZMax={z:.4f}\n"
    )
    return z


def _target_xy_pick(sel_ex):
    """
    Return world-space (x, y) of the picked point from a FreeCADGui SelectionObject.

    Priority:
      1. PickedPoints — already in world space (stored by FreeCAD's 3D picker)
      2. Sub-element vertex / face centre, transformed to world space via Placement
      3. Object world bounding-box XY centre (fallback)
    """
    # PickedPoints are already in world coordinates
    try:
        pt = sel_ex.PickedPoints
        if pt:
            return pt[0].x, pt[0].y
    except Exception:
        pass

    # Sub-element geometry centre — local coords, must apply world placement
    try:
        world_mat = _world_placement_of(sel_ex.Object).toMatrix()
        for sub_name in (sel_ex.SubElementNames or []):
            sub = sel_ex.Object.Shape.getElement(sub_name)
            if hasattr(sub, "Point"):           # Vertex
                wp = world_mat.multVec(sub.Point)
                return wp.x, wp.y
            if hasattr(sub, "CenterOfMass"):    # Face / Edge
                wp = world_mat.multVec(sub.CenterOfMass)
                return wp.x, wp.y
    except Exception:
        pass

    # Fallback: world BBox centre
    bb = _world_bbox_of_objects([sel_ex.Object])
    if bb is not None:
        return (bb.XMin + bb.XMax) / 2.0, (bb.YMin + bb.YMax) / 2.0
    return 0.0, 0.0


# ── Dialog ─────────────────────────────────────────────────────────────────────

class ChipTransformDialog(QtWidgets.QDialog):
    """
    Modeless dialog for incremental chip translation / rotation.
    Stays open so the user can make many adjustments, then close it.
    """

    _AX = FreeCAD.Vector(1, 0, 0)
    _AY = FreeCAD.Vector(0, 1, 0)
    _AZ = FreeCAD.Vector(0, 0, 1)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chip Transform")
        self.setWindowFlags(
            QtCore.Qt.Tool |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.WindowCloseButtonHint,
        )
        self.setMinimumWidth(400)

        self._dx = self._dy = self._dz = 0.0    # cumulative translation (mm)
        self._rx = self._ry = self._rz = 0.0    # cumulative rotation (deg, approx)
        self._initial_placements = {}            # {name: Placement} snapshot at open
        self._sel_object = None                  # target object for Align
        self._sel_ex     = None                  # raw SelectionObject (carries picked pt)

        self._build_ui()
        self._snapshot_placements()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)

        # Object scope
        scope_grp = QtWidgets.QGroupBox("Objects to Move")
        scope_lay = QtWidgets.QVBoxLayout(scope_grp)

        radio_row = QtWidgets.QHBoxLayout()
        self._rb_gds   = QtWidgets.QRadioButton("GDS Chip Objects")
        self._rb_all   = QtWidgets.QRadioButton("All Document Objects")
        self._rb_sel   = QtWidgets.QRadioButton("Current Selection")
        self._rb_proxy = QtWidgets.QRadioButton("Chip Proxy (pick one)")
        self._rb_gds.setChecked(True)
        for rb in (self._rb_gds, self._rb_all, self._rb_sel, self._rb_proxy):
            radio_row.addWidget(rb)
            rb.toggled.connect(self._update_proxy_controls_enabled)
        scope_lay.addLayout(radio_row)

        proxy_row = QtWidgets.QHBoxLayout()
        proxy_row.addWidget(QtWidgets.QLabel("Chip:"))
        self._proxy_combo = QtWidgets.QComboBox()
        self._proxy_combo.setToolTip(
            "Which imported chip proxy to move — repopulated from the\n"
            "'<name> (Proxy)' groups currently in the document."
        )
        proxy_row.addWidget(self._proxy_combo, 1)
        proxy_refresh_btn = QtWidgets.QPushButton("↺")
        proxy_refresh_btn.setFixedWidth(28)
        proxy_refresh_btn.setToolTip("Refresh chip list (e.g. after importing another proxy)")
        proxy_refresh_btn.clicked.connect(self._refresh_proxy_list)
        proxy_row.addWidget(proxy_refresh_btn)
        scope_lay.addLayout(proxy_row)

        chk_row = QtWidgets.QHBoxLayout()
        self._chk_package = QtWidgets.QCheckBox("+ Package / Leadframe")
        self._chk_pcb      = QtWidgets.QCheckBox("+ PCB")
        self._chk_package.setChecked(True)
        self._chk_pcb.setChecked(True)
        chk_row.addWidget(self._chk_package)
        chk_row.addWidget(self._chk_pcb)
        chk_row.addStretch()
        scope_lay.addLayout(chk_row)

        self._proxy_row_widgets = [self._proxy_combo, proxy_refresh_btn,
                                    self._chk_package, self._chk_pcb]
        root.addWidget(scope_grp)
        self._update_proxy_controls_enabled()

        # Translation
        t_grp = QtWidgets.QGroupBox("Translate")
        t_lay = QtWidgets.QGridLayout(t_grp)
        t_lay.setHorizontalSpacing(4)
        t_lay.setVerticalSpacing(4)

        self._t_step = QtWidgets.QDoubleSpinBox()
        self._t_step.setRange(0.001, 1000.0)
        self._t_step.setValue(0.1)
        self._t_step.setDecimals(3)
        self._t_step.setSuffix(" mm")
        t_lay.addWidget(QtWidgets.QLabel("Step:"), 0, 0)
        t_lay.addWidget(self._t_step, 0, 1, 1, 4)

        # XY arrow cross (row/col arrangement)
        t_lay.addWidget(self._nav_btn("↑  +Y",  lambda: self._do_translate( 0, +1,  0)), 1, 2)
        t_lay.addWidget(self._nav_btn("← -X",   lambda: self._do_translate(-1,  0,  0)), 2, 1)
        center_lbl = QtWidgets.QLabel("XY")
        center_lbl.setAlignment(QtCore.Qt.AlignCenter)
        t_lay.addWidget(center_lbl, 2, 2)
        t_lay.addWidget(self._nav_btn("+X →",   lambda: self._do_translate(+1,  0,  0)), 2, 3)
        t_lay.addWidget(self._nav_btn("↓  -Y",  lambda: self._do_translate( 0, -1,  0)), 3, 2)

        # Z column
        t_lay.addWidget(QtWidgets.QLabel("Z:"), 1, 5, alignment=QtCore.Qt.AlignRight)
        t_lay.addWidget(self._nav_btn("▲ +Z",  lambda: self._do_translate(0, 0, +1)), 2, 5)
        t_lay.addWidget(self._nav_btn("▼ -Z",  lambda: self._do_translate(0, 0, -1)), 3, 5)

        reset_pos = QtWidgets.QPushButton("Reset Position to Origin")
        reset_pos.clicked.connect(self._reset_position)
        t_lay.addWidget(reset_pos, 4, 0, 1, 6)

        root.addWidget(t_grp)

        # Rotation
        r_grp = QtWidgets.QGroupBox("Rotate (around bounding-box center)")
        r_lay = QtWidgets.QGridLayout(r_grp)
        r_lay.setHorizontalSpacing(4)
        r_lay.setVerticalSpacing(4)

        self._r_step = QtWidgets.QDoubleSpinBox()
        self._r_step.setRange(0.1, 180.0)
        self._r_step.setValue(15.0)
        self._r_step.setDecimals(1)
        self._r_step.setSuffix(" °")
        r_lay.addWidget(QtWidgets.QLabel("Step:"), 0, 0)
        r_lay.addWidget(self._r_step, 0, 1, 1, 4)

        axes = [
            ("X", self._AX, "_rx"),
            ("Y", self._AY, "_ry"),
            ("Z", self._AZ, "_rz"),
        ]
        for row, (label, axis, _) in enumerate(axes, start=1):
            r_lay.addWidget(QtWidgets.QLabel(f"Around {label}:"), row, 0)
            ax = axis
            r_lay.addWidget(self._nav_btn(f"−{label}", lambda _, a=ax: self._do_rotate(a, -1)), row, 1)
            r_lay.addWidget(self._nav_btn(f"+{label}", lambda _, a=ax: self._do_rotate(a, +1)), row, 2)

        root.addWidget(r_grp)

        # ── Align to Selection ─────────────────────────────────────────────────
        a_grp = QtWidgets.QGroupBox("Align to Selection")
        a_lay = QtWidgets.QVBoxLayout(a_grp)
        a_lay.setSpacing(4)

        # Info label showing current selection
        self._lbl_sel = QtWidgets.QLabel("No object selected")
        self._lbl_sel.setStyleSheet("font-size: 9px; color: #888; font-style: italic;")
        self._lbl_sel.setWordWrap(True)
        a_lay.addWidget(self._lbl_sel)

        # Refresh selection button
        refresh_btn = QtWidgets.QPushButton("↺  Read current FreeCAD selection")
        refresh_btn.setToolTip(
            "Click an object (or face/vertex) in the 3D view, then press this\n"
            "button to load it as the alignment target."
        )
        refresh_btn.clicked.connect(self._read_selection)
        a_lay.addWidget(refresh_btn)

        # Align action buttons
        btn_row_a = QtWidgets.QHBoxLayout()
        btn_z   = QtWidgets.QPushButton("Snap Z\n(bottom → surface)")
        btn_xy  = QtWidgets.QPushButton("Center XY\non click point")
        btn_both = QtWidgets.QPushButton("Both\n(Z + XY)")
        for b in (btn_z, btn_xy, btn_both):
            b.setFixedHeight(44)
        btn_z.setToolTip(
            "Move chip group so its lowest face sits flush on the top\n"
            "surface of the selected object."
        )
        btn_xy.setToolTip(
            "Move chip group so its XY bounding-box center aligns with\n"
            "the XY position of the last-picked vertex or face center."
        )
        btn_both.setToolTip("Apply Z-snap and XY-centering together.")
        btn_z.clicked.connect(lambda: self._do_align(snap_z=True,  center_xy=False))
        btn_xy.clicked.connect(lambda: self._do_align(snap_z=False, center_xy=True))
        btn_both.clicked.connect(lambda: self._do_align(snap_z=True,  center_xy=True))
        btn_row_a.addWidget(btn_z)
        btn_row_a.addWidget(btn_xy)
        btn_row_a.addWidget(btn_both)
        a_lay.addLayout(btn_row_a)

        # Symmetric placement — centre on the FACE itself rather than on the
        # click point, so the die lands in the middle of the pad however
        # roughly it was clicked.
        btn_row_c = QtWidgets.QHBoxLayout()
        btn_face   = QtWidgets.QPushButton("Center on face")
        btn_face_z = QtWidgets.QPushButton("Center on face\n+ Snap Z")
        for b in (btn_face, btn_face_z):
            b.setFixedHeight(44)
        _face_tip = (
            "Select a FACE in the 3D view, then press this to move the chip\n"
            "group so its centre sits exactly at the centre of that face.\n\n"
            "Unlike 'Center XY on click point', it ignores where exactly you\n"
            "clicked and uses the face's own outer boundary, so the result is\n"
            "the same wherever on the face you click — and stays correct on a\n"
            "face with cut-outs, whose area centroid is off-centre."
        )
        btn_face.setToolTip(_face_tip)
        btn_face_z.setToolTip(_face_tip + "\n\nAlso drops the chip flat onto that face.")
        btn_face.clicked.connect(lambda: self._do_center_on_face(snap_z=False))
        btn_face_z.clicked.connect(lambda: self._do_center_on_face(snap_z=True))
        btn_row_c.addWidget(btn_face)
        btn_row_c.addWidget(btn_face_z)
        a_lay.addLayout(btn_row_c)

        root.addWidget(a_grp)


        status_grp = QtWidgets.QGroupBox("Cumulative Offset (since dialog opened)")
        status_lay = QtWidgets.QVBoxLayout(status_grp)
        mono = QtGui.QFont("Courier")
        mono.setPointSize(9)
        self._lbl_pos = QtWidgets.QLabel()
        self._lbl_rot = QtWidgets.QLabel()
        self._lbl_pos.setFont(mono)
        self._lbl_rot.setFont(mono)
        status_lay.addWidget(self._lbl_pos)
        status_lay.addWidget(self._lbl_rot)
        root.addWidget(status_grp)
        self._update_status()

        # Keyboard hint
        hint = QtWidgets.QLabel(
            "<small><b>Keyboard shortcuts</b> (click this dialog first to focus it):<br>"
            "← → = ±X &nbsp;&nbsp; ↑ ↓ = ±Y &nbsp;&nbsp; PgUp/PgDn = ±Z<br>"
            "Shift + ← → = ±Rz &nbsp;&nbsp; Shift + ↑ ↓ = ±Rx &nbsp;&nbsp; "
            "Shift + PgUp/PgDn = ±Ry</small>"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        # Bottom buttons
        btn_row = QtWidgets.QHBoxLayout()
        restore_btn = QtWidgets.QPushButton("Restore Original")
        restore_btn.setToolTip("Undo all changes made in this dialog session")
        restore_btn.clicked.connect(self._restore_original)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(restore_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _nav_btn(self, label, callback):
        b = QtWidgets.QPushButton(label)
        b.setFixedWidth(62)
        b.setFixedHeight(32)
        b.clicked.connect(callback)
        return b

    # ── Chip Proxy scope ───────────────────────────────────────────────────────

    def _update_proxy_controls_enabled(self):
        enabled = self._rb_proxy.isChecked()
        for w in self._proxy_row_widgets:
            w.setEnabled(enabled)

    def _refresh_proxy_list(self):
        """Repopulate the chip dropdown from the document's current
        '<name> (Proxy)' groups, preserving the current pick by object
        Name (not list index) when it's still present."""
        doc = FreeCAD.activeDocument()
        groups = _chip_proxy_groups(doc)
        prev_name = self._proxy_combo.currentData()
        self._proxy_combo.blockSignals(True)
        self._proxy_combo.clear()
        for g in groups:
            self._proxy_combo.addItem(g.Label or g.Name, g.Name)
        if prev_name:
            idx = self._proxy_combo.findData(prev_name)
            if idx >= 0:
                self._proxy_combo.setCurrentIndex(idx)
        self._proxy_combo.blockSignals(False)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_proxy_list()

    # ── Snapshot & restore ─────────────────────────────────────────────────────

    def _snapshot_placements(self):
        """Capture current placements so 'Restore Original' can undo everything."""
        doc = FreeCAD.activeDocument()
        all_objs = _all_objects(doc) if doc else []
        self._initial_placements = {
            o.Name: (o.Placement.copy(),
                     FreeCAD.Vector(o.ContactPoint)
                     if getattr(o, "ContactPoint", None) is not None else None)
            for o in all_objs}

    def _restore_original(self):
        doc = FreeCAD.activeDocument()
        if not doc:
            return
        objs = _all_objects(doc)
        doc.openTransaction("Chip Transform: Restore Original")
        _restore_placements(objs, self._initial_placements)
        doc.commitTransaction()
        doc.recompute()
        self._dx = self._dy = self._dz = 0.0
        self._rx = self._ry = self._rz = 0.0
        self._update_status()

    # ── Object collection ──────────────────────────────────────────────────────

    def _objects(self):
        doc = FreeCAD.activeDocument()
        if self._rb_sel.isChecked():
            objs = _selected_objects()
        elif self._rb_all.isChecked():
            objs = _all_objects(doc)
        elif self._rb_proxy.isChecked():
            objs = self._proxy_scope_objects(doc)
        else:
            objs = _gds_objects(doc)
        if not objs:
            QtWidgets.QMessageBox.warning(
                self, "Nothing to move",
                "No objects found for the selected scope.\n\n"
                "• 'GDS Chip Objects' requires a GDS import (Layer_*, ContactPoint_*, …)\n"
                "• 'Current Selection' requires objects selected in the 3D view\n"
                "• 'Chip Proxy (pick one)' requires a chip picked in the dropdown\n"
                "• 'All Document Objects' requires an open document",
            )
        return objs

    def _proxy_scope_objects(self, doc):
        """Objects for the 'Chip Proxy (pick one)' scope: the selected
        proxy's block + pads, plus the Package/leadframe group and/or PCB
        root objects when their checkboxes are ticked — reuses
        _expand_selection so companion meshes/via-blocks and nested groups
        are picked up exactly as they are for 'Current Selection'."""
        if doc is None:
            return []
        grp_name = self._proxy_combo.currentData()
        grp = doc.getObject(grp_name) if grp_name else None
        if grp is None:
            return []
        roots = [grp]
        if self._chk_package.isChecked():
            pkg = _package_group(doc)
            if pkg is not None:
                roots.append(pkg)
        if self._chk_pcb.isChecked():
            roots.extend(_pcb_root_objects(doc))
        return _expand_selection(roots, doc)

    # ── Transform actions ──────────────────────────────────────────────────────

    def _do_translate(self, sx, sy, sz):
        objs = self._objects()
        if not objs:
            return
        step = self._t_step.value()
        dx, dy, dz = sx * step, sy * step, sz * step
        doc = FreeCAD.activeDocument()
        doc.openTransaction("Chip Translate")
        _translate_objects(objs, dx, dy, dz)
        doc.commitTransaction()
        # Placement changes don't require shape recomputation — just refresh the view.
        # doc.recompute() on complex STEP geometry blocks the UI thread in FreeCAD 1.1+.
        FreeCADGui.updateGui()
        self._dx += dx
        self._dy += dy
        self._dz += dz
        self._update_status()

    def _do_rotate(self, axis, sign):
        objs = self._objects()
        if not objs:
            return
        angle = sign * self._r_step.value()
        center = _bounding_center(objs)
        doc = FreeCAD.activeDocument()
        doc.openTransaction("Chip Rotate")
        _rotate_objects(objs, axis, angle, center)
        doc.commitTransaction()
        FreeCADGui.updateGui()
        if axis.x:   self._rx += angle
        elif axis.y: self._ry += angle
        elif axis.z: self._rz += angle
        self._update_status()

    def _reset_position(self):
        objs = self._objects()
        if not objs:
            return
        if self._dx == 0.0 and self._dy == 0.0 and self._dz == 0.0:
            return
        doc = FreeCAD.activeDocument()
        doc.openTransaction("Chip Reset Position")
        _translate_objects(objs, -self._dx, -self._dy, -self._dz)
        doc.commitTransaction()
        FreeCADGui.updateGui()
        self._dx = self._dy = self._dz = 0.0
        self._update_status()

    # ── Align to Selection ─────────────────────────────────────────────────────

    def _read_selection(self):
        """Read the current FreeCAD selection and store it as the align target."""
        sel = FreeCADGui.Selection.getSelectionEx()
        # Filter out GDS chip objects — aligning chip to itself makes no sense
        doc = FreeCAD.activeDocument()
        chip_names = {o.Name for o in _gds_objects(doc)} if doc else set()

        candidates = [s for s in sel
                      if hasattr(s.Object, "Shape")
                      and s.Object.Name not in chip_names]
        if not candidates:
            QtWidgets.QMessageBox.warning(
                self, "No valid selection",
                "Click a non-chip object (e.g. the PCB body) in the 3D view,\n"
                "then press '↺ Read current FreeCAD selection'."
            )
            return

        self._sel_ex     = candidates[0]
        self._sel_object = candidates[0].Object
        label = self._sel_object.Label or self._sel_object.Name

        # Show picked sub-element if any
        subs = list(candidates[0].SubElementNames or [])
        sub_txt = f"  [{subs[0]}]" if subs else ""
        self._lbl_sel.setText(f"Target: {label}{sub_txt}")
        self._lbl_sel.setStyleSheet("font-size: 9px; color: #88cc88; font-style: normal;")

    def _do_align(self, snap_z: bool, center_xy: bool):
        """Apply Z-snap and/or XY-centering of chip group to the stored target."""
        if self._sel_object is None:
            QtWidgets.QMessageBox.warning(
                self, "No target",
                "Press '↺ Read current FreeCAD selection' first to pick a target."
            )
            return

        objs = self._objects()
        if not objs:
            return

        dx = dy = dz = 0.0

        if snap_z:
            target_z = _target_z_snap(self._sel_ex)
            chip_z   = _chip_zmin(objs)
            dz = target_z - chip_z
            FreeCAD.Console.PrintMessage(
                f"[ChipAlign] Snap Z: face_z={target_z:.4f}  chip_zmin={chip_z:.4f}  dz={dz:.4f}\n"
            )

        if center_xy:
            tx, ty = _target_xy_pick(self._sel_ex)
            cx, cy = _chip_xy_center(objs)
            dx = tx - cx
            dy = ty - cy

        if dx == 0.0 and dy == 0.0 and dz == 0.0:
            return

        doc = FreeCAD.activeDocument()
        doc.openTransaction("Chip Align")
        _translate_objects(objs, dx, dy, dz)
        doc.commitTransaction()
        FreeCADGui.updateGui()
        self._dx += dx
        self._dy += dy
        self._dz += dz
        self._update_status()

    def _do_center_on_face(self, snap_z: bool):
        """Centre the chip group on the CENTRE of the selected face."""
        if self._sel_object is None:
            QtWidgets.QMessageBox.warning(
                self, "No target",
                "Press '↺ Read current FreeCAD selection' first to pick a target."
            )
            return

        target = _face_center_xy(self._sel_ex)
        if target is None:
            QtWidgets.QMessageBox.warning(
                self, "No face selected",
                "Centring on a face needs a FACE to be selected — click the "
                "face itself in the 3D view (not an edge, a vertex or the "
                "whole object), press '↺ Read current FreeCAD selection', "
                "then try again."
            )
            return

        objs = self._objects()
        if not objs:
            return

        tx, ty = target
        cx, cy = _chip_xy_center(objs)
        dx, dy = tx - cx, ty - cy

        dz = 0.0
        if snap_z:
            target_z = _target_z_snap(self._sel_ex)
            dz = target_z - _chip_zmin(objs)

        if dx == 0.0 and dy == 0.0 and dz == 0.0:
            return

        doc = FreeCAD.activeDocument()
        doc.openTransaction("Chip Center on Face")
        _translate_objects(objs, dx, dy, dz)
        doc.commitTransaction()
        FreeCADGui.updateGui()
        self._dx += dx
        self._dy += dy
        self._dz += dz
        self._update_status()
        FreeCAD.Console.PrintMessage(
            f"[ChipAlign] Centred on face at ({tx:.4f}, {ty:.4f})"
            + (f", snapped Z by {dz:.4f}" if snap_z else "") + ".\n"
        )

    # ── Status display ─────────────────────────────────────────────────────────

    def _update_status(self):
        self._lbl_pos.setText(
            f"Position:  X={self._dx:+8.3f}  Y={self._dy:+8.3f}  Z={self._dz:+8.3f}  mm"
        )
        self._lbl_rot.setText(
            f"Rotation:  Rx={self._rx:+7.1f}°  Ry={self._ry:+7.1f}°  Rz={self._rz:+7.1f}°"
        )

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key   = event.key()
        shift = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
        handled = True

        if shift:
            if   key == QtCore.Qt.Key_Right:    self._do_rotate(self._AZ, +1)
            elif key == QtCore.Qt.Key_Left:     self._do_rotate(self._AZ, -1)
            elif key == QtCore.Qt.Key_Up:       self._do_rotate(self._AX, +1)
            elif key == QtCore.Qt.Key_Down:     self._do_rotate(self._AX, -1)
            elif key == QtCore.Qt.Key_PageUp:   self._do_rotate(self._AY, +1)
            elif key == QtCore.Qt.Key_PageDown: self._do_rotate(self._AY, -1)
            else: handled = False
        else:
            if   key == QtCore.Qt.Key_Right:    self._do_translate(+1,  0,  0)
            elif key == QtCore.Qt.Key_Left:     self._do_translate(-1,  0,  0)
            elif key == QtCore.Qt.Key_Up:       self._do_translate( 0, +1,  0)
            elif key == QtCore.Qt.Key_Down:     self._do_translate( 0, -1,  0)
            elif key == QtCore.Qt.Key_PageUp:   self._do_translate( 0,  0, +1)
            elif key == QtCore.Qt.Key_PageDown: self._do_translate( 0,  0, -1)
            else: handled = False

        if handled:
            event.accept()
        else:
            super().keyPressEvent(event)


# ── Command ────────────────────────────────────────────────────────────────────

_dialog_instance = None


class ChipTransformCommand:
    def GetResources(self):
        return {
            "MenuText": "Move / Rotate Chip",
            "ToolTip": (
                "Open the Chip Transform dialog to translate and rotate\n"
                "GDS chip objects using buttons or keyboard arrow keys."
            ),
            "Pixmap": get_icon("Chip_Transform.svg"),
        }

    def Activated(self):
        global _dialog_instance
        main_win = FreeCADGui.getMainWindow()
        if _dialog_instance is None or not _dialog_instance.isVisible():
            _dialog_instance = ChipTransformDialog(main_win)
            _dialog_instance.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        _dialog_instance.show()
        _dialog_instance.raise_()
        _dialog_instance.activateWindow()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ChipTransformCommand", ChipTransformCommand())