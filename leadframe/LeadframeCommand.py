from PySide2 import QtWidgets
import FreeCAD, Part, FreeCADGui, os, sys
from FreeCAD import Base

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)
from Get_Path import get_icon

def create_leadframe(config, doc=None, gds_objects=None):
    """Create a leadframe based on the provided configuration."""
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument("Leadframe")

    frame_type = config["frame_type"]
    frame_length = config["frame_length"]
    frame_width = config["frame_width"]
    frame_thickness = config.get("frame_thickness", 0.5)
    material = config["material"]

    # --- Realistic Material Color Mapping ---
    color_map = {
        "Copper": (0.80, 0.50, 0.30),    # Shiny Copper
        "Alloy 42": (0.65, 0.65, 0.70),  # Dull Silver/Grey
        "Silver": (0.90, 0.90, 0.90)     # Bright Silver
    }
    metal_color = color_map.get(material, (0.8, 0.8, 0.8))

    # Base coordinates for the package center (0,0)
    frame_x = -frame_length / 2.0
    frame_y = -frame_width / 2.0
    frame_x_end = frame_length / 2.0
    frame_y_end = frame_width / 2.0
    
    # Track the highest Z point so the silicon chip (GDS) is placed correctly
    top_z_surface = 0.0

    # QFN & QFP Generation
    if frame_type in ["QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)"]:
        left_lead_count = config["left_lead_count"]
        right_lead_count = config["right_lead_count"]
        top_lead_count = config["top_lead_count"]
        bottom_lead_count = config["bottom_lead_count"]
        
        lead_pitch = config.get("lead_pitch", 0.5)
        lead_width = config["lead_width"]
        lead_length = config["lead_length"]
        qfn_pad_thickness = config.get("qfn_pad_thickness", 0.2)

        # Determine Lead Extents & Calculate Die Paddle
        isolation_gap = 0.2  # mm gap between die paddle and leads
        
        if frame_type == "QFN (Quad Flat No-lead)":
            inward_extent = lead_length
            pad_z = qfn_pad_thickness
            pad_z_offset = -qfn_pad_thickness
            top_z_surface = 0.0
        else: # QFP
            # QFP leads extend slightly inside the package for wire bonding landing
            inward_extent = 0.4 
            pad_z = frame_thickness
            pad_z_offset = -frame_thickness
            top_z_surface = 0.0
            
        # Calculate paddle size allowing for isolation gap
        paddle_x = frame_x + inward_extent + isolation_gap if left_lead_count > 0 else frame_x + isolation_gap
        paddle_x_end = frame_x_end - inward_extent - isolation_gap if right_lead_count > 0 else frame_x_end - isolation_gap
        paddle_y = frame_y + inward_extent + isolation_gap if bottom_lead_count > 0 else frame_y + isolation_gap
        paddle_y_end = frame_y_end - inward_extent - isolation_gap if top_lead_count > 0 else frame_y_end - isolation_gap
        
        pad_w = paddle_x_end - paddle_x
        pad_h = paddle_y_end - paddle_y
        
        # 1. Draw Die Paddle Solid
        if pad_w > 0 and pad_h > 0:
            paddle_shape = Part.makeBox(pad_w, pad_h, pad_z, Base.Vector(paddle_x, paddle_y, pad_z_offset))
            paddle_obj = doc.addObject("Part::Feature", f"DiePaddle_{material}")
            paddle_obj.Shape = paddle_shape
            paddle_obj.ViewObject.ShapeColor = metal_color
        else:
            FreeCAD.Console.PrintWarning("Die Paddle skipped: Leads are too long and consume the entire package center.\n")

        # 2. Define the Base Lead Profile (Realistic QFP Gull-Wing or Flat QFN)
        # This function creates one lead centered on the X-axis, pointing Right.
        def create_base_lead():
            if frame_type == "QFP (Quad Flat Package)":
                # Complex polygon shape for realistic bent lead
                L = lead_length
                W = lead_width
                inner_L = inward_extent
                D = 0.6  # Downset depth
                T = frame_thickness 
                
                # Define the side profile points
                p0 = Base.Vector(-inner_L, 0, 0)               
                p1 = Base.Vector(L * 0.15, 0, 0)               
                p2 = Base.Vector(L * 0.55, 0, -D)              
                p3 = Base.Vector(L, 0, -D)                     
                p4 = Base.Vector(L, 0, -D - T)                 
                p5 = Base.Vector(L * 0.45, 0, -D - T)          
                p6 = Base.Vector(L * 0.05, 0, -T)              
                p7 = Base.Vector(-inner_L, 0, -T)              
                
                wire = Part.makePolygon([p0, p1, p2, p3, p4, p5, p6, p7, p0])
                face = Part.Face(wire)
                # Extrude and center width
                lead = face.extrude(Base.Vector(0, W, 0))      
                lead.translate(Base.Vector(0, -W/2.0, 0))      
                return lead
            else:
                # Simple flat box for QFN
                lead = Part.makeBox(lead_length, lead_width, qfn_pad_thickness)
                lead.translate(Base.Vector(-lead_length, -lead_width/2.0, -qfn_pad_thickness))
                return lead

        # 3. Position and Rotate Leads around the package (CENTERED MATH)
        lead_shapes = []
        
        # Calculate total span of leads to center them
        left_lead_span = (left_lead_count - 1) * lead_pitch
        right_lead_span = (right_lead_count - 1) * lead_pitch
        top_lead_span = (top_lead_count - 1) * lead_pitch
        bottom_lead_span = (bottom_lead_count - 1) * lead_pitch

        # Generate LEFT Leads
        if left_lead_count > 0:
            for i in range(left_lead_count):
                # Math ensures centering around Y=0
                lead_center_y = -(left_lead_span / 2.0) + (i * lead_pitch)
                lead = create_base_lead()
                lead.rotate(Base.Vector(0,0,0), Base.Vector(0,0,1), 180)
                lead.translate(Base.Vector(frame_x, lead_center_y, 0))
                lead_shapes.append(lead)

        # Generate RIGHT Leads
        if right_lead_count > 0:
            for i in range(right_lead_count):
                lead_center_y = -(right_lead_span / 2.0) + (i * lead_pitch)
                lead = create_base_lead()
                lead.translate(Base.Vector(frame_x_end, lead_center_y, 0))
                lead_shapes.append(lead)

        # Generate TOP Leads
        if top_lead_count > 0:
            for i in range(top_lead_count):
                lead_center_x = -(top_lead_span / 2.0) + (i * lead_pitch)
                lead = create_base_lead()
                lead.rotate(Base.Vector(0,0,0), Base.Vector(0,0,1), 90)
                lead.translate(Base.Vector(lead_center_x, frame_y_end, 0))
                lead_shapes.append(lead)

        # Generate BOTTOM Leads
        if bottom_lead_count > 0:
            for i in range(bottom_lead_count):
                lead_center_x = -(bottom_lead_span / 2.0) + (i * lead_pitch)
                lead = create_base_lead()
                lead.rotate(Base.Vector(0,0,0), Base.Vector(0,0,1), -90)
                lead.translate(Base.Vector(lead_center_x, frame_y, 0))
                lead_shapes.append(lead)
            
        # 4. Combine all leads into a single lightweight component
        if lead_shapes: 
            leads_compound = Part.makeCompound(lead_shapes)
            lead_extrusion = doc.addObject("Part::Feature", "LeadframeLeads")
            lead_extrusion.Shape = leads_compound
            lead_extrusion.ViewObject.ShapeColor = metal_color

    # BGA Generation
    elif frame_type == "BGA (Ball Grid Array)":
        bga_ball_diameter = config["bga_ball_diameter"]
        bga_ball_pitch = config["bga_ball_pitch"]

        # 1. Create the green PCB Substrate
        substrate_shape = Part.makeBox(frame_length, frame_width, frame_thickness, Base.Vector(frame_x, frame_y, 0))
        substrate = doc.addObject("Part::Feature", "BGA_Substrate")
        substrate.Shape = substrate_shape
        substrate.ViewObject.ShapeColor = (0.15, 0.45, 0.25) # PCB Green

        # 2. Create the Top Die Attach Pad (This will use the UI Material color!)
        pad_size_x = frame_length * 0.70
        pad_size_y = frame_width * 0.70
        pad_z_thickness = 0.05
        top_z_surface = frame_thickness + pad_z_thickness
        
        bga_pad_shape = Part.makeBox(pad_size_x, pad_size_y, pad_z_thickness, Base.Vector(-pad_size_x/2.0, -pad_size_y/2.0, frame_thickness))
        bga_pad = doc.addObject("Part::Feature", f"BGA_DiePad_{material}")
        bga_pad.Shape = bga_pad_shape
        bga_pad.ViewObject.ShapeColor = metal_color  # Material color is applied here!

        # 3. Create the Solder Balls
        edge_margin = max(bga_ball_diameter, 0.2) 
        usable_length = frame_length - (2 * edge_margin)
        usable_width = frame_width - (2 * edge_margin)

        balls_x = int(usable_length / bga_ball_pitch) + 1
        balls_y = int(usable_width / bga_ball_pitch) + 1

        grid_offset_x = frame_x + (frame_length - ((balls_x - 1) * bga_ball_pitch)) / 2.0
        grid_offset_y = frame_y + (frame_width - ((balls_y - 1) * bga_ball_pitch)) / 2.0

        ball_shapes = []
        for i in range(balls_x):
            for j in range(balls_y):
                ball_x = grid_offset_x + (i * bga_ball_pitch)
                ball_y = grid_offset_y + (j * bga_ball_pitch)
                
                ball = Part.makeSphere(bga_ball_diameter / 2.0, Base.Vector(ball_x, ball_y, -bga_ball_diameter/3.0))
                ball_shapes.append(ball)

        if ball_shapes:
            bga_compound = Part.makeCompound(ball_shapes)
            bga_obj = doc.addObject("Part::Feature", "BGA_Balls")
            bga_obj.Shape = bga_compound
            bga_obj.ViewObject.ShapeColor = (0.85, 0.85, 0.85) # Solder is shiny silver

    # Auto-Centre GDS Data & Auto-Resize Die
    if gds_objects:
        global_XMin = float('inf')
        global_YMin = float('inf')
        global_XMax = float('-inf')
        global_YMax = float('-inf')
        
        has_shapes = False

        # 1. Find the total physical footprint (Bounding Box) of all GDS layers
        for _, objects in gds_objects.items():
            for obj in objects:
                if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
                    bbox = obj.Shape.BoundBox
                    if bbox.XMin < global_XMin: global_XMin = bbox.XMin
                    if bbox.YMin < global_YMin: global_YMin = bbox.YMin
                    if bbox.XMax > global_XMax: global_XMax = bbox.XMax
                    if bbox.YMax > global_YMax: global_YMax = bbox.YMax
                    has_shapes = True

        if has_shapes:
            # 2. Calculate the exact center and total dimensions of the circuit
            center_x = (global_XMax + global_XMin) / 2.0
            center_y = (global_YMax + global_YMin) / 2.0
            gds_length_x = global_XMax - global_XMin
            gds_width_y = global_YMax - global_YMin

            # 3. Slide all GDS objects so they are perfectly centered at (0,0)
            for _, objects in gds_objects.items():
                for obj in objects:
                    # Shift by -center_x and -center_y, and place at the correct Z height
                    obj.Placement = Base.Placement(Base.Vector(-center_x, -center_y, top_z_surface + 0.05), Base.Rotation(0, 0, 0, 1))

            # 4. Find the Silicon Die and automatically resize it to fit!
            die_obj = doc.getObject("Silicon_Die")
            if die_obj and hasattr(die_obj, "Shape") and not die_obj.Shape.isNull():
                padding = 0.20 # Adds a 0.1mm edge margin around the circuit
                
                new_length = gds_length_x + padding
                new_width = gds_width_y + padding
                
                # We measure the old box to keep the original Z thickness
                die_height = die_obj.Shape.BoundBox.ZLength
                die_z = die_obj.Placement.Base.z
                
                # Create the box ALREADY CENTERED by telling it exactly where to start
                start_x = -new_length / 2.0
                start_y = -new_width / 2.0
                start_point = Base.Vector(start_x, start_y, die_z)
                
                # Build the new perfectly sized and centered shape
                new_shape = Part.makeBox(new_length, new_width, die_height, start_point)
                
                # Assign it back to the object
                die_obj.Shape = new_shape
                die_obj.Placement = Base.Placement(Base.Vector(0, 0, 0), Base.Rotation(0, 0, 0, 1))

    doc.recompute()
    FreeCADGui.activeDocument().activeView().viewIsometric()
    FreeCADGui.SendMsgToActiveView("ViewFit")

    return doc

def configure_leadframe():
    from leadframe.LeadframeConfigurator import LeadframeConfigurator
    dialog = LeadframeConfigurator()
    if dialog.exec_():
        return dialog.get_config()
    return None

class LeadframeCommand:
    def GetResources(self):
        return {
            "MenuText": "Leadframe Configurator",
            "ToolTip": "Configure and generate a leadframe geometry",
            "Pixmap": get_icon("Leadframe_Configurator.png")
        }

    def Activated(self):
        config = configure_leadframe()
        if config:
            create_leadframe(config)
            QtWidgets.QMessageBox.information(None, "Success", f"Leadframe created.")
        else:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Leadframe configuration cancelled.")

    def IsActive(self):
        return True

FreeCADGui.addCommand('LeadframeCommand', LeadframeCommand())