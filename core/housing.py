# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
from FreeCAD import Base
import FreeCAD, Part, Sketcher, FreeCADGui


def _tag_housing_material(obj, material):
    """Record the configured housing material on the finished body or lid —
    the only trace of it otherwise is the outer sketch's name (see
    core.materials)."""
    if obj is None or not material:
        return
    if not hasattr(obj, "HousingMaterial"):
        obj.addProperty("App::PropertyString", "HousingMaterial", "Housing",
                        "Housing material chosen in the configurator")
    obj.HousingMaterial = material


def build_housing(config):
    """Create a transparent housing around a leadframe based on the provided configuration.

    Args:
        config (dict): Housing parameters. Required keys:
            frame_type (str), frame_length (float), frame_width (float),
            frame_thickness (float), wall_thickness (float), clearance (float),
            housing_height (float), include_lid (bool), lid_thickness (float),
            material (str), transparency (float 0-1).
            Optional: lead_length, qfn_pad_thickness, bga_ball_diameter
            (used to compute extra clearance per frame type).
    """
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument("Housing")

    frame_type = config["frame_type"]
    leadframe_length = config["frame_length"]
    leadframe_width = config["frame_width"]
    leadframe_thickness = config["frame_thickness"]
    wall_thickness = config["wall_thickness"]
    clearance = config["clearance"]
    housing_height = config["housing_height"]
    include_lid = config["include_lid"]
    lid_thickness = config["lid_thickness"]
    material = config["material"]
    transparency = config["transparency"]

    # Extra clearance to accommodate lead protrusions per package type
    if frame_type == "QFP (Quad Flat Package)":
        extra_clearance = config.get("lead_length", 0)
    elif frame_type == "QFN (Quad Flat No-lead)":
        extra_clearance = config.get("qfn_pad_thickness", 0)
    elif frame_type == "BGA (Ball Grid Array)":
        extra_clearance = config.get("bga_ball_diameter", 0) / 2
    else:
        extra_clearance = 0

    # The cavity floor is the plane the leadframe stands on: z = 0, where
    # core.leadframe builds its body and leads — or the underside of the
    # balls for a BGA, which hang below z = 0. The housing used to start AT
    # z = 0 with its floor above that, so the floor occupied the same space
    # as the bottom of the leadframe.
    if frame_type == "BGA (Ball Grid Array)":
        floor_z = -config.get("bga_ball_diameter", 0)
    else:
        floor_z = 0.0

    # Outer housing dimensions
    outer_length = leadframe_length + 2 * (wall_thickness + clearance + extra_clearance)
    outer_width = leadframe_width + 2 * (wall_thickness + clearance + extra_clearance)
    outer_height = housing_height + wall_thickness

    outer_x = -outer_length / 2
    outer_y = -outer_width / 2
    outer_x_end = outer_x + outer_length
    outer_y_end = outer_y + outer_width

    # Outer shell sketch
    outer_sketch = doc.addObject("Sketcher::SketchObject", f"HousingOuter_{material}")
    outer_sketch.Placement = Base.Placement(Base.Vector(0, 0, floor_z - wall_thickness),
                                            Base.Rotation(0, 0, 0, 1))
    outer_lines = [
        Part.LineSegment(Base.Vector(outer_x, outer_y, 0), Base.Vector(outer_x_end, outer_y, 0)),
        Part.LineSegment(Base.Vector(outer_x_end, outer_y, 0), Base.Vector(outer_x_end, outer_y_end, 0)),
        Part.LineSegment(Base.Vector(outer_x_end, outer_y_end, 0), Base.Vector(outer_x, outer_y_end, 0)),
        Part.LineSegment(Base.Vector(outer_x, outer_y_end, 0), Base.Vector(outer_x, outer_y, 0))
    ]
    for i, line in enumerate(outer_lines):
        outer_sketch.addGeometry(line)
        if i > 0:
            outer_sketch.addConstraint(Sketcher.Constraint('Coincident', i - 1, 2, i, 1))
    outer_sketch.addConstraint(Sketcher.Constraint('Coincident', len(outer_lines) - 1, 2, 0, 1))

    outer_body = doc.addObject("Part::Extrusion", "HousingOuterBody")
    outer_body.Base = outer_sketch
    outer_body.Dir = Base.Vector(0, 0, outer_height)
    outer_body.Solid = True

    # Inner cavity sketch
    inner_length = leadframe_length + 2 * (clearance + extra_clearance)
    inner_width = leadframe_width + 2 * (clearance + extra_clearance)
    inner_height = housing_height
    inner_x = -inner_length / 2
    inner_y = -inner_width / 2
    inner_x_end = inner_x + inner_length
    inner_y_end = inner_y + inner_width

    inner_sketch = doc.addObject("Sketcher::SketchObject", "HousingInner")
    inner_sketch.Placement = Base.Placement(Base.Vector(0, 0, floor_z), Base.Rotation(0, 0, 0, 1))
    inner_lines = [
        Part.LineSegment(Base.Vector(inner_x, inner_y, 0), Base.Vector(inner_x_end, inner_y, 0)),
        Part.LineSegment(Base.Vector(inner_x_end, inner_y, 0), Base.Vector(inner_x_end, inner_y_end, 0)),
        Part.LineSegment(Base.Vector(inner_x_end, inner_y_end, 0), Base.Vector(inner_x, inner_y_end, 0)),
        Part.LineSegment(Base.Vector(inner_x, inner_y_end, 0), Base.Vector(inner_x, inner_y, 0))
    ]
    for i, line in enumerate(inner_lines):
        inner_sketch.addGeometry(line)
        if i > 0:
            inner_sketch.addConstraint(Sketcher.Constraint('Coincident', i - 1, 2, i, 1))
    inner_sketch.addConstraint(Sketcher.Constraint('Coincident', len(inner_lines) - 1, 2, 0, 1))

    inner_cut = doc.addObject("Part::Extrusion", "HousingInnerCut")
    inner_cut.Base = inner_sketch
    inner_cut.Dir = Base.Vector(0, 0, inner_height)
    inner_cut.Solid = True

    # Boolean cut: outer shell minus inner cavity
    housing = doc.addObject("Part::Cut", "HousingBody")
    housing.Base = outer_body
    housing.Tool = inner_cut
    if FreeCAD.GuiUp:
        housing.ViewObject.Transparency = int(transparency * 100)

    # Corner alignment posts
    post_size = min(1.0, wall_thickness * 0.5)
    post_height = leadframe_thickness + clearance
    # Named distinctly from the extrusion below (matches the LidSketch/Lid
    # pattern used for the lid) — previously both sketch and extrusion were
    # named "AlignmentPosts", so FreeCAD silently renamed the extrusion to
    # "AlignmentPosts001" and any doc.getObject("AlignmentPosts") lookup
    # would get the flat sketch (zero volume) instead of the actual solid.
    post_sketch = doc.addObject("Sketcher::SketchObject", "AlignmentPostsSketch")
    post_sketch.Placement = Base.Placement(Base.Vector(0, 0, floor_z), Base.Rotation(0, 0, 0, 1))
    post_positions = [
        (inner_x + post_size / 2, inner_y + post_size / 2),
        (inner_x_end - post_size / 2, inner_y + post_size / 2),
        (inner_x + post_size / 2, inner_y_end - post_size / 2),
        (inner_x_end - post_size / 2, inner_y_end - post_size / 2)
    ]
    for px, py in post_positions:
        post_lines = [
            Part.LineSegment(Base.Vector(px - post_size / 2, py - post_size / 2, 0), Base.Vector(px + post_size / 2, py - post_size / 2, 0)),
            Part.LineSegment(Base.Vector(px + post_size / 2, py - post_size / 2, 0), Base.Vector(px + post_size / 2, py + post_size / 2, 0)),
            Part.LineSegment(Base.Vector(px + post_size / 2, py + post_size / 2, 0), Base.Vector(px - post_size / 2, py + post_size / 2, 0)),
            Part.LineSegment(Base.Vector(px - post_size / 2, py + post_size / 2, 0), Base.Vector(px - post_size / 2, py - post_size / 2, 0))
        ]
        start_idx = post_sketch.GeometryCount
        for i, line in enumerate(post_lines):
            post_sketch.addGeometry(line)
            if i > 0:
                post_sketch.addConstraint(Sketcher.Constraint('Coincident', start_idx + i - 1, 2, start_idx + i, 1))
        post_sketch.addConstraint(Sketcher.Constraint('Coincident', start_idx + len(post_lines) - 1, 2, start_idx, 1))

    post_extrusion = doc.addObject("Part::Extrusion", "AlignmentPosts")
    post_extrusion.Base = post_sketch
    post_extrusion.Dir = Base.Vector(0, 0, post_height)
    post_extrusion.Solid = True
    if FreeCAD.GuiUp:
        post_extrusion.ViewObject.Transparency = int(transparency * 100)

    # Fuse posts into housing
    final_housing = doc.addObject("Part::Fuse", "FinalHousing")
    final_housing.Base = housing
    final_housing.Tool = post_extrusion
    if FreeCAD.GuiUp:
        final_housing.ViewObject.Transparency = int(transparency * 100)
    _tag_housing_material(final_housing, material)

    # Optional lid
    if include_lid:
        lid_sketch = doc.addObject("Sketcher::SketchObject", "LidSketch")
        lid_sketch.Placement = Base.Placement(
            Base.Vector(0, 0, floor_z - wall_thickness + outer_height),
            Base.Rotation(0, 0, 0, 1))
        lid_lines = [
            Part.LineSegment(Base.Vector(outer_x, outer_y, 0), Base.Vector(outer_x_end, outer_y, 0)),
            Part.LineSegment(Base.Vector(outer_x_end, outer_y, 0), Base.Vector(outer_x_end, outer_y_end, 0)),
            Part.LineSegment(Base.Vector(outer_x_end, outer_y_end, 0), Base.Vector(outer_x, outer_y_end, 0)),
            Part.LineSegment(Base.Vector(outer_x, outer_y_end, 0), Base.Vector(outer_x, outer_y, 0))
        ]
        for i, line in enumerate(lid_lines):
            lid_sketch.addGeometry(line)
            if i > 0:
                lid_sketch.addConstraint(Sketcher.Constraint('Coincident', i - 1, 2, i, 1))
        lid_sketch.addConstraint(Sketcher.Constraint('Coincident', len(lid_lines) - 1, 2, 0, 1))

        lid_extrusion = doc.addObject("Part::Extrusion", "Lid")
        lid_extrusion.Base = lid_sketch
        lid_extrusion.Dir = Base.Vector(0, 0, lid_thickness)
        lid_extrusion.Solid = True
        if FreeCAD.GuiUp:
            lid_extrusion.ViewObject.Transparency = int(transparency * 100)
        _tag_housing_material(lid_extrusion, material)

    doc.recompute()
    if FreeCAD.GuiUp:
        FreeCADGui.activeDocument().activeView().viewIsometric()
        FreeCADGui.SendMsgToActiveView("ViewFit")

    return doc


# ── Standalone lid placement ──────────────────────────────────────────────────
#
# build_housing() above can only add a lid at housing-creation time (the
# include_lid flag). In practice a package is often built OPEN on purpose —
# so the die and bond wires stay physically accessible for wire bonding —
# and only closed up afterward. add_lid_to_housing() adds (or replaces) a
# lid on whatever housing already exists in the document, independent of
# how or when that housing was built.

def find_housing_body(doc):
    """
    Return the assembled housing object in *doc*: "FinalHousing" (body +
    alignment posts, the normal result of build_housing()) if present,
    else the plain "HousingBody" boolean-cut shell. Returns None if
    neither exists or neither has valid geometry.
    """
    if doc is None:
        return None
    for name in ("FinalHousing", "HousingBody"):
        obj = doc.getObject(name)
        if obj is not None and hasattr(obj, "Shape") and not obj.Shape.isNull():
            return obj
    return None


def add_lid_to_housing(doc, lid_thickness, transparency=None, housing_obj=None):
    """
    Add (or replace) a transparent lid on an existing housing.

    The lid's footprint and height are read directly from the housing
    object's own Shape.BoundBox rather than from a saved config dict — so
    this works regardless of how the housing was originally parameterized,
    and correctly reflects any manual edits made to the housing since.

    Typical use: build the housing via Housing Configurator with "Include
    Transparent Lid" unchecked (leaving it open so the die and bond wires
    stay accessible), do wire bonding, then call this once bonding is
    finished to close the package up.

    Args:
        doc: FreeCAD document.
        lid_thickness (float): lid thickness in mm.
        transparency (float, 0-1, optional): defaults to the housing's own
            current ViewObject.Transparency when available, else 0.5.
        housing_obj (optional): explicit housing object to lid; the housing
            is auto-detected via find_housing_body(doc) when omitted.

    Returns the new Lid object, or None if no housing could be found.
    """
    housing = housing_obj or find_housing_body(doc)
    if housing is None:
        FreeCAD.Console.PrintWarning(
            "[Housing] add_lid_to_housing: no housing found in the "
            "document — build one first via Housing Configurator.\n"
        )
        return None

    bb = housing.Shape.BoundBox
    outer_x, outer_y         = bb.XMin, bb.YMin
    outer_x_end, outer_y_end = bb.XMax, bb.YMax
    top_z                    = bb.ZMax

    if transparency is None:
        if FreeCAD.GuiUp and hasattr(housing, "ViewObject") and housing.ViewObject is not None:
            transparency = getattr(housing.ViewObject, "Transparency", 50) / 100.0
        else:
            transparency = 0.5

    # Replace an existing lid cleanly rather than stacking a duplicate on
    # top of it — re-running this after adjusting lid_thickness should
    # update the lid, not accumulate copies.
    for name in ("Lid", "LidSketch"):
        old = doc.getObject(name)
        if old is not None:
            try:
                doc.removeObject(name)
            except Exception:
                pass

    lid_sketch = doc.addObject("Sketcher::SketchObject", "LidSketch")
    lid_sketch.Placement = Base.Placement(Base.Vector(0, 0, top_z), Base.Rotation(0, 0, 0, 1))
    lid_lines = [
        Part.LineSegment(Base.Vector(outer_x, outer_y, 0), Base.Vector(outer_x_end, outer_y, 0)),
        Part.LineSegment(Base.Vector(outer_x_end, outer_y, 0), Base.Vector(outer_x_end, outer_y_end, 0)),
        Part.LineSegment(Base.Vector(outer_x_end, outer_y_end, 0), Base.Vector(outer_x, outer_y_end, 0)),
        Part.LineSegment(Base.Vector(outer_x, outer_y_end, 0), Base.Vector(outer_x, outer_y, 0)),
    ]
    for i, line in enumerate(lid_lines):
        lid_sketch.addGeometry(line)
        if i > 0:
            lid_sketch.addConstraint(Sketcher.Constraint('Coincident', i - 1, 2, i, 1))
    lid_sketch.addConstraint(Sketcher.Constraint('Coincident', len(lid_lines) - 1, 2, 0, 1))

    lid_extrusion = doc.addObject("Part::Extrusion", "Lid")
    lid_extrusion.Base = lid_sketch
    lid_extrusion.Dir = Base.Vector(0, 0, lid_thickness)
    lid_extrusion.Solid = True
    if FreeCAD.GuiUp:
        lid_extrusion.ViewObject.Transparency = int(transparency * 100)
    _tag_housing_material(lid_extrusion, getattr(housing, "HousingMaterial", ""))

    doc.recompute()
    FreeCAD.Console.PrintMessage(
        f"[Housing] Lid added on '{housing.Name}': "
        f"{bb.XLength:.3f} x {bb.YLength:.3f} mm, thickness {lid_thickness:.3f} mm, "
        f"top at z={top_z:.3f} mm.\n"
    )
    return lid_extrusion
