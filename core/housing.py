from FreeCAD import Base
import FreeCAD, Part, Sketcher, FreeCADGui


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
    outer_sketch.Placement = Base.Placement(Base.Vector(0, 0, 0), Base.Rotation(0, 0, 0, 1))
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
    inner_sketch.Placement = Base.Placement(Base.Vector(0, 0, wall_thickness), Base.Rotation(0, 0, 0, 1))
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
    housing.ViewObject.Transparency = int(transparency * 100)

    # Corner alignment posts
    post_size = min(1.0, wall_thickness * 0.5)
    post_height = leadframe_thickness + clearance
    post_sketch = doc.addObject("Sketcher::SketchObject", "AlignmentPosts")
    post_sketch.Placement = Base.Placement(Base.Vector(0, 0, wall_thickness), Base.Rotation(0, 0, 0, 1))
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
    post_extrusion.ViewObject.Transparency = int(transparency * 100)

    # Fuse posts into housing
    final_housing = doc.addObject("Part::Fuse", "FinalHousing")
    final_housing.Base = housing
    final_housing.Tool = post_extrusion
    final_housing.ViewObject.Transparency = int(transparency * 100)

    # Optional lid
    if include_lid:
        lid_sketch = doc.addObject("Sketcher::SketchObject", "LidSketch")
        lid_sketch.Placement = Base.Placement(Base.Vector(0, 0, outer_height), Base.Rotation(0, 0, 0, 1))
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
        lid_extrusion.ViewObject.Transparency = int(transparency * 100)

    doc.recompute()
    FreeCADGui.activeDocument().activeView().viewIsometric()
    FreeCADGui.SendMsgToActiveView("ViewFit")

    return doc
