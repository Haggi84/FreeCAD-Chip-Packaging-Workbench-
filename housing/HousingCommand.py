from PySide2 import QtWidgets
import FreeCAD, Part, Sketcher, FreeCADGui, os, sys
from FreeCAD import Base

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from housing.HousingConfigurator import TransparentHousingConfigurator
from Get_Path import get_icon

def create_housing(config):
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

    housing_color_map = {
        "Polycarbonate": (0.75, 0.85, 0.95),  
        "Acrylic": (0.95, 0.95, 0.95),        
        "Transparent ABS": (0.95, 0.90, 0.75), 
        "Black Epoxy": (0.15, 0.15, 0.15)      
    }
    housing_color = housing_color_map.get(material, (0.8, 0.8, 0.8))

    is_solid_mold = (material == "Black Epoxy")

    # Calculate Housing Dimensions
    if "QFN" in frame_type:
        lead_len = config.get("lead_length", 0.5) 
        outer_length = leadframe_length + (2 * lead_len)
        outer_width = leadframe_width + (2 * lead_len)
    else:
        outer_length = leadframe_length
        outer_width = leadframe_width

    outer_height = housing_height + wall_thickness

    outer_x = -outer_length / 2
    outer_y = -outer_width / 2
    outer_x_end = outer_x + outer_length
    outer_y_end = outer_y + outer_width

    chamfer_size = 0.4
    base_height = max(0.01, outer_height - chamfer_size)

    # 1. Base Box
    base_box = Part.makeBox(outer_length, outer_width, base_height, Base.Vector(outer_x, outer_y, 0))

    # 2. Sloped Roof (Loft Geometry)
    p1 = Base.Vector(outer_x, outer_y, base_height)
    p2 = Base.Vector(outer_x_end, outer_y, base_height)
    p3 = Base.Vector(outer_x_end, outer_y_end, base_height)
    p4 = Base.Vector(outer_x, outer_y_end, base_height)
    w_base = Part.Wire([Part.LineSegment(p1, p2).toShape(), Part.LineSegment(p2, p3).toShape(), Part.LineSegment(p3, p4).toShape(), Part.LineSegment(p4, p1).toShape()])

    t1 = Base.Vector(outer_x + chamfer_size, outer_y + chamfer_size, outer_height)
    t2 = Base.Vector(outer_x_end - chamfer_size, outer_y + chamfer_size, outer_height)
    t3 = Base.Vector(outer_x_end - chamfer_size, outer_y_end - chamfer_size, outer_height)
    t4 = Base.Vector(outer_x + chamfer_size, outer_y_end - chamfer_size, outer_height)
    w_top = Part.Wire([Part.LineSegment(t1, t2).toShape(), Part.LineSegment(t2, t3).toShape(), Part.LineSegment(t3, t4).toShape(), Part.LineSegment(t4, t1).toShape()])

    # Loft them together (True = make solid, True = ruled/straight edges)
    roof_solid = Part.makeLoft([w_base, w_top], True, True)

    # Fuse base and roof into one seamless shape
    full_outer_shape = base_box.fuse(roof_solid)

    # Add to document
    outer_body = doc.addObject("Part::Feature", "HousingOuterBody")
    outer_body.Shape = full_outer_shape
    outer_body.ViewObject.ShapeColor = housing_color
    outer_body.ViewObject.Transparency = int(transparency * 100)

    # Solid mold logic
    if is_solid_mold:
        # Real molded packages are solid! No internal cavity, no alignment posts.
        outer_body.Label = "FinalHousing"
        doc.recompute()
        FreeCADGui.activeDocument().activeView().viewIsometric()
        FreeCADGui.SendMsgToActiveView("ViewFit")
        return

    # Create Inner Cavity
    inner_length = outer_length - 2 * wall_thickness
    inner_width = outer_width - 2 * wall_thickness
    inner_height = max(0.01, housing_height - wall_thickness)
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
            inner_sketch.addConstraint(Sketcher.Constraint('Coincident', i-1, 2, i, 1))
    inner_sketch.addConstraint(Sketcher.Constraint('Coincident', len(inner_lines)-1, 2, 0, 1))

    # Extrude inner cavity to cut
    inner_cut = doc.addObject("Part::Extrusion", "HousingInnerCut")
    inner_cut.Base = inner_sketch
    inner_cut.Dir = Base.Vector(0, 0, inner_height)
    inner_cut.Solid = True
    inner_cut.ViewObject.Visibility = False 

    # Perform cut operation
    housing = doc.addObject("Part::Cut", "HousingBody")
    housing.Base = outer_body
    housing.Tool = inner_cut

    # Apply transparency to housing
    housing_obj = doc.getObject("HousingBody")
    housing_obj.ViewObject.ShapeColor = housing_color
    housing_obj.ViewObject.Transparency = int(transparency * 100)

    # Add Alignment Posts
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

    for idx, (px, py) in enumerate(post_positions):
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
    post_extrusion.ViewObject.Visibility = False

    # Fuse posts with housing
    final_housing = doc.addObject("Part::Fuse", "FinalHousing")
    final_housing.Base = housing
    final_housing.Tool = post_extrusion
    final_housing.ViewObject.ShapeColor = housing_color
    final_housing.ViewObject.Transparency = int(transparency * 100)

    # Create Lid
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
                lid_sketch.addConstraint(Sketcher.Constraint('Coincident', i-1, 2, i, 1))
        lid_sketch.addConstraint(Sketcher.Constraint('Coincident', len(lid_lines)-1, 2, 0, 1))

        lid_extrusion = doc.addObject("Part::Extrusion", "Lid")
        lid_extrusion.Base = lid_sketch
        lid_extrusion.Dir = Base.Vector(0, 0, lid_thickness)
        lid_extrusion.Solid = True
        lid_extrusion.ViewObject.ShapeColor = (0.60, 0.80, 0.90)  # Light Glass Blue
        lid_extrusion.ViewObject.Transparency = int(transparency * 100)
        lid_sketch.ViewObject.Visibility = False

    # Hide all intermediate shapes and blueprints
    inner_sketch.ViewObject.Visibility = False
    post_sketch.ViewObject.Visibility = False
    outer_body.ViewObject.Visibility = False

    doc.recompute()
    FreeCADGui.activeDocument().activeView().viewIsometric()
    FreeCADGui.SendMsgToActiveView("ViewFit")

class HousingCommand:
    def GetResources(self):
        return {
            "MenuText": "Housing Configurator",
            "ToolTip": "Configure and generate a transparent housing for a leadframe",
            "Pixmap": get_icon("Housing_Configurator.png")
        }

    def Activated(self):
        dialog = TransparentHousingConfigurator()
        if dialog.exec_():
            config = dialog.get_config()
            create_housing(config)
            QtWidgets.QMessageBox.information(None, "Success", f"Housing created:\n{config}")
        else:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Housing configuration cancelled.")

    def IsActive(self):
        return True

FreeCADGui.addCommand('HousingCommand', HousingCommand())