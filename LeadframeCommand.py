import FreeCADGui
from PySide2 import QtWidgets, QtCore
import FreeCAD, Part, Sketcher
from FreeCAD import Base

class LeadframeConfigurator(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(LeadframeConfigurator, self).__init__(parent)
        self.setWindowTitle("Leadframe Configuration")
        self.setMinimumWidth(400)

        # Layout
        layout = QtWidgets.QFormLayout()

        # Leadframe Type Dropdown
        self.frame_type = QtWidgets.QComboBox()
        self.frame_type.addItems(["QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)", "BGA (Ball Grid Array)"])
        layout.addRow("Leadframe Type:", self.frame_type)

        # Frame Dimensions
        self.frame_length = QtWidgets.QDoubleSpinBox()
        self.frame_length.setRange(1.0, 1000.0)
        self.frame_length.setSingleStep(0.5)
        self.frame_length.setSuffix(" mm")
        self.frame_length.setValue(5.0)
        layout.addRow("Length:", self.frame_length)

        self.frame_width = QtWidgets.QDoubleSpinBox()
        self.frame_width.setRange(1.0, 1000.0)
        self.frame_width.setSingleStep(0.5)
        self.frame_width.setSuffix(" mm")
        self.frame_width.setValue(5.0)
        layout.addRow("Width:", self.frame_width)

        self.frame_thickness = QtWidgets.QDoubleSpinBox()
        self.frame_thickness.setRange(0.05, 5.0)
        self.frame_thickness.setSingleStep(0.05)
        self.frame_thickness.setSuffix(" mm")
        self.frame_thickness.setValue(0.2)
        layout.addRow("Thickness:", self.frame_thickness)

        # (QFN/QFP) Parameters
        self.qfn_qfp = QtWidgets.QWidget()
        qfn_qfp_layout = QtWidgets.QFormLayout()
        
        # Lead Counts
        # Left side
        self.left_lead_count = QtWidgets.QSpinBox()
        self.left_lead_count.setRange(0, 80)
        self.left_lead_count.setValue(4)
        qfn_qfp_layout.addRow("Left Lead Count:", self.left_lead_count)

        # Right side
        self.right_lead_count = QtWidgets.QSpinBox()
        self.right_lead_count.setRange(0, 80)
        self.right_lead_count.setValue(4)
        qfn_qfp_layout.addRow("Right Lead Count:", self.right_lead_count)

        # Top side
        self.top_lead_count = QtWidgets.QSpinBox()
        self.top_lead_count.setRange(0, 80)
        self.top_lead_count.setValue(4)
        qfn_qfp_layout.addRow("Top Lead Count:", self.top_lead_count)

        # Bottom side
        self.bottom_lead_count = QtWidgets.QSpinBox()
        self.bottom_lead_count.setRange(0, 80)
        self.bottom_lead_count.setValue(4)
        qfn_qfp_layout.addRow("Bottom Lead Count:", self.bottom_lead_count)

        # Lead Parameters (QFN/QFP)
        self.lead_width = QtWidgets.QDoubleSpinBox()
        self.lead_width.setRange(0.1, 5.0)
        self.lead_width.setSingleStep(0.1)
        self.lead_width.setSuffix(" mm")
        self.lead_width.setValue(0.4)
        qfn_qfp_layout.addRow("Lead Width:", self.lead_width)

        self.lead_pitch = QtWidgets.QDoubleSpinBox()
        self.lead_pitch.setRange(0.1, 10.0)
        self.lead_pitch.setSingleStep(0.1)
        self.lead_pitch.setSuffix(" mm")
        self.lead_pitch.setValue(1.0)
        qfn_qfp_layout.addRow("Lead Pitch:", self.lead_pitch)

        self.lead_length = QtWidgets.QDoubleSpinBox()
        self.lead_length.setRange(0.5, 10.0)
        self.lead_length.setSingleStep(0.5)
        self.lead_length.setSuffix(" mm")
        self.lead_length.setValue(1.0)
        qfn_qfp_layout.addRow("Lead Length:", self.lead_length)

        # QGN Pad Thickness
        self.qfn_pad_thickness = QtWidgets.QDoubleSpinBox()
        self.qfn_pad_thickness.setRange(0.01, 2.0)
        self.qfn_pad_thickness.setSingleStep(0.01)
        self.qfn_pad_thickness.setSuffix(" mm")
        self.qfn_pad_thickness.setValue(0.05)
        qfn_qfp_layout.addRow("QFN Pad Thickness:", self.qfn_pad_thickness)

        self.qfn_qfp.setLayout(qfn_qfp_layout)
        layout.addRow(self.qfn_qfp)

        # BGA Parameters
        self.bga = QtWidgets.QWidget()
        bga_layout = QtWidgets.QFormLayout()

        self.bga_ball_diameter = QtWidgets.QDoubleSpinBox()
        self.bga_ball_diameter.setRange(0.1, 2.0)
        self.bga_ball_diameter.setSingleStep(0.1)
        self.bga_ball_diameter.setSuffix(" mm")
        self.bga_ball_diameter.setValue(0.5)
        bga_layout.addRow("BGA Ball Diameter:", self.bga_ball_diameter)

        self.bga_ball_pitch = QtWidgets.QDoubleSpinBox()
        self.bga_ball_pitch.setRange(0.1, 5.0)
        self.bga_ball_pitch.setSingleStep(0.1)
        self.bga_ball_pitch.setSuffix(" mm")
        self.bga_ball_pitch.setValue(1.0)
        bga_layout.addRow("BGA Ball Pitch:", self.bga_ball_pitch)

        self.bga.setLayout(bga_layout)
        layout.addRow(self.bga)

        # Material Selection
        self.material_combo = QtWidgets.QComboBox()
        self.material_combo.addItems(["Copper", "Alloy 42", "Silver"])
        layout.addRow("Material:", self.material_combo)

        # OK/Cancel Buttons
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)

        # Connect frame type change to update visibility
        self.frame_type.currentIndexChanged.connect(self.update_parameter_visibility)

        # Initial visibility setup
        self.update_parameter_visibility()

    def update_parameter_visibility(self):
        """
        Update the visibility of parameters based on the selected frame type.
        """
        frame_type = self.frame_type.currentText()
        self.qfn_qfp.setVisible(frame_type in ["QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)"])
        self.bga.setVisible(frame_type == "BGA (Ball Grid Array)")

    def get_config(self):
        """
        Return only relevant configuration parameters based on the selected frame type.
        """
        frame_type = self.frame_type.currentText()
        config = {
            "frame_type": frame_type,
            "frame_length": self.frame_length.value(),
            "frame_width": self.frame_width.value(),
            "frame_thickness": self.frame_thickness.value(),
            "material": self.material_combo.currentText()
        }
        if frame_type in ["QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)"]:
            config.update({
                "left_lead_count": self.left_lead_count.value(),
                "right_lead_count": self.right_lead_count.value(),
                "top_lead_count": self.top_lead_count.value(),
                "bottom_lead_count": self.bottom_lead_count.value(),
                "lead_width": self.lead_width.value(),
                "lead_pitch": self.lead_pitch.value(),
                "lead_length": self.lead_length.value(),
                "qfn_pad_thickness": self.qfn_pad_thickness.value()
            })
        elif frame_type == "BGA (Ball Grid Array)":
            config.update({
                "bga_ball_diameter": self.bga_ball_diameter.value(),
                "bga_ball_pitch": self.bga_ball_pitch.value()
            })
        return config

def create_leadframe(config, doc=None, gds_objects=None):
    """Create a leadframe based on the provided configuration.

    Args:
        config (dict): A dictionary containing leadframe parameters.
    """
    if not doc:
        doc = FreeCAD.newDocument("Leadframe")

    frame_type = config["frame_type"]
    FreeCAD.Console.PrintMessage(f"Creating leadframe of type: {frame_type}\n")
    frame_length = config["frame_length"]
    frame_width = config["frame_width"]
    frame_thickness = config["frame_thickness"]
    material = config["material"]

    # Create a new sketch in the XY plane
    sketch = doc.addObject("Sketcher::SketchObject", f"FrameSketch_{material}")
    sketch.Placement = Base.Placement(Base.Vector(0, 0, 0), Base.Rotation(0, 0, 0, 1))

    # Create the main frame rectangle
    frame_x = -frame_length / 2
    frame_y = -frame_width / 2
    frame_x_end = frame_x + frame_length
    frame_y_end = frame_y + frame_width
    
    lines = [
        Part.LineSegment(Base.Vector(frame_x, frame_y, 0), Base.Vector(frame_x_end, frame_y, 0)),
        Part.LineSegment(Base.Vector(frame_x_end, frame_y, 0), Base.Vector(frame_x_end, frame_y_end, 0)),
        Part.LineSegment(Base.Vector(frame_x_end, frame_y_end, 0), Base.Vector(frame_x, frame_y_end, 0)),
        Part.LineSegment(Base.Vector(frame_x, frame_y_end, 0), Base.Vector(frame_x, frame_y, 0))
    ]

    # Add lines to the sketch and constrain them
    for i, line in enumerate(lines):
        sketch.addGeometry(line)
        if i > 0:
            sketch.addConstraint(Sketcher.Constraint('Coincident', i-1, 2, i, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', len(lines)-1, 2, 0, 1))
    
    # Extrude the frame to create the 3D body
    frame_body = doc.addObject("Part::Extrusion", "LeadframeBody")
    frame_body.Base = sketch
    frame_body.Dir = Base.Vector(0, 0, frame_thickness)
    frame_body.Solid = True

    def add_leads(lead_x_start, lead_x_end, lead_y_start, lead_y_end, lead_sketch):
        """
        Helper function to add leads to the sketch.
        """
        if lead_x_start != lead_x_end and lead_y_start != lead_y_end:
            lead_lines = [
                Part.LineSegment(Base.Vector(lead_x_start, lead_y_start, 0), Base.Vector(lead_x_end, lead_y_start, 0)),
                Part.LineSegment(Base.Vector(lead_x_end, lead_y_start, 0), Base.Vector(lead_x_end, lead_y_end, 0)),
                Part.LineSegment(Base.Vector(lead_x_end, lead_y_end, 0), Base.Vector(lead_x_start, lead_y_end, 0)),
                Part.LineSegment(Base.Vector(lead_x_start, lead_y_end, 0), Base.Vector(lead_x_start, lead_y_start, 0))
            ]

            start_idx = lead_sketch.GeometryCount
            for j, line in enumerate(lead_lines):
                lead_sketch.addGeometry(line)
                if j > 0:
                    lead_sketch.addConstraint(Sketcher.Constraint('Coincident', start_idx + j - 1, 2, start_idx + j, 1))
            lead_sketch.addConstraint(Sketcher.Constraint('Coincident', start_idx + len(lead_lines) - 1, 2, start_idx, 1))


    if frame_type in ["QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)"]:
        # Extract lead parameters
        left_lead_count = config["left_lead_count"]
        right_lead_count = config["right_lead_count"]
        top_lead_count = config["top_lead_count"]
        bottom_lead_count = config["bottom_lead_count"]
        lead_width = config["lead_width"]
        lead_pitch = config["lead_pitch"]
        lead_length = config["lead_length"]
        qfn_pad_thickness = config["qfn_pad_thickness"]

        # Check possible lead counts
        if left_lead_count <= frame_width and right_lead_count <= frame_width and top_lead_count <= frame_length and bottom_lead_count <= frame_length:
            
            # Calculate the total span for leads on each side
            left_lead_span = (left_lead_count - 1) * lead_pitch if left_lead_count > 0 else 0
            right_lead_span = (right_lead_count - 1) * lead_pitch if right_lead_count > 0 else 0
            top_lead_span = (top_lead_count - 1) * lead_pitch if top_lead_count > 0 else 0
            bottom_lead_span = (bottom_lead_count - 1) * lead_pitch if bottom_lead_count > 0 else 0

            # Ensure the frame is large enough to accommodate the leads
            if max(left_lead_span, right_lead_span) > frame_width:
                FreeCAD.Console.PrintError("Frame width is too small for the number of leads on the left or right side.")
            if max(top_lead_span, bottom_lead_span) > frame_length:
                FreeCAD.Console.PrintError("Frame length is too small for the number of leads on the top or bottom side.")

            # Create a new sketch for leads
            lead_sketch = doc.addObject("Sketcher::SketchObject", "LeadSketch")
            lead_sketch.Placement = Base.Placement(Base.Vector(0, 0, 0), Base.Rotation(0, 0, 0, 1))

            # QFP: Leads extend outwards from the frame; QFN: Leads are flush with the frame
            lead_extension = lead_length if frame_type == "QFP (Quad Flat Package)" else lead_width

            # Add leads to left side
            for i in range(left_lead_count):
                lead_y = frame_y + (i * lead_pitch) - (left_lead_span / 2) + (lead_pitch / 2) if left_lead_count > 1 else frame_y + frame_width / 2
                lead_x_start = frame_x - lead_extension if frame_type == "QFP (Quad Flat Package)" else frame_x - lead_width
                lead_x_end = frame_x
                lead_y_start = lead_y - lead_width / 2
                lead_y_end = lead_y + lead_width / 2

                new_lead_Y = left_lead_count + lead_y
                if new_lead_Y < -(lead_y_start) :
                    lead_y_start = new_lead_Y - (lead_width / 2)
                    lead_y_end = new_lead_Y + (lead_width / 2)

                add_leads(lead_x_start, lead_x_end, lead_y_start, lead_y_end, lead_sketch)

            # Add leads to right side
            for i in range(right_lead_count):
                lead_y = frame_y + (i * lead_pitch) - (right_lead_span / 2) + (lead_pitch / 2) if right_lead_count > 1 else frame_y + frame_width / 2
                lead_x_start = frame_x + frame_length
                lead_x_end = frame_x + frame_length + lead_extension if frame_type == "QFP (Quad Flat Package)" else frame_x + frame_length + lead_width
                lead_y_start = lead_y - lead_width / 2
                lead_y_end = lead_y + lead_width / 2

                new_lead_Y = right_lead_count + lead_y
                if new_lead_Y < -(lead_y_start) :
                    lead_y_start = new_lead_Y - (lead_width / 2)
                    lead_y_end = new_lead_Y + (lead_width / 2)
            
                add_leads(lead_x_start, lead_x_end, lead_y_start, lead_y_end, lead_sketch)

            # Add leads to top side
            for i in range(top_lead_count):
                lead_x = frame_x + (i * lead_pitch) - (top_lead_span / 2) + (lead_pitch / 2) if top_lead_count > 1 else frame_x + frame_length / 2
                lead_y_start = frame_y + frame_width
                lead_y_end = frame_y + frame_width + lead_extension if frame_type == "QFP (Quad Flat Package)" else frame_y + frame_width + lead_width
                lead_x_start = lead_x - lead_width / 2
                lead_x_end = lead_x + lead_width / 2

                new_lead_X = top_lead_count + lead_x
                if new_lead_X < -(lead_x_start) :
                    lead_x_start = new_lead_X - (lead_width / 2)
                    lead_x_end = new_lead_X + (lead_width / 2)

                add_leads(lead_x_start, lead_x_end, lead_y_start, lead_y_end, lead_sketch)

            # Add leads to bottom side
            for i in range(bottom_lead_count):
                lead_x = frame_x + (i * lead_pitch) - (bottom_lead_span / 2) + (lead_pitch / 2) if bottom_lead_count > 1 else frame_x + frame_length / 2
                lead_y_start = frame_y - lead_extension if frame_type == "QFP (Quad Flat Package)" else frame_y - lead_width
                lead_y_end = frame_y
                lead_x_start = lead_x - lead_width / 2
                lead_x_end = lead_x + lead_width / 2

                new_lead_X = bottom_lead_count + lead_x
                if new_lead_X < -(lead_x_start) :
                    lead_x_start = new_lead_X - (lead_width / 2)
                    lead_x_end = new_lead_X + (lead_width / 2)

                add_leads(lead_x_start, lead_x_end, lead_y_start, lead_y_end, lead_sketch)
            
            # Extrude the lead (QFN pads are placed on the bottom of the frame)
            if lead_sketch.GeometryCount > 0: # Ensure there are leads to extrude
                lead_extrusion = doc.addObject("Part::Extrusion", "LeadframeLeads")
                lead_extrusion.Base = lead_sketch
                lead_extrusion.Dir = Base.Vector(0, 0, qfn_pad_thickness if frame_type == "QFN (Quad Flat No-lead)" else frame_thickness)
                lead_extrusion.Solid = True
                if frame_type == "QFN (Quad Flat No-lead)":
                    lead_extrusion.Placement = Base.Placement(Base.Vector(0, 0, -qfn_pad_thickness), Base.Rotation(0, 0, 0, 1))

        else:
            FreeCAD.Console.PrintError("Invalid Lead counts!. Lead counts are out of range for the frame dimensions. Please adjust the lead counts.\n")

    elif frame_type == "BGA (Ball Grid Array)":
        # Extract BGA parameters
        bga_ball_diameter = config["bga_ball_diameter"]
        bga_ball_pitch = config["bga_ball_pitch"]

        # Calculate the number of balls based on frame dimensions and ball pitch
        balls_x = int(frame_length / bga_ball_pitch) + 1
        balls_y = int(frame_width / bga_ball_pitch) + 1

        # Validate frame dimensions
        if balls_x < 1 or balls_y < 1:
            FreeCAD.Console.PrintError("Frame dimensions are too small for BGA ball grid.")

        # Create a compound object for BGA balls
        ball_objects = []
        for i in range(balls_x):
            for j in range(balls_y):
                ball_x = frame_x + (i * bga_ball_pitch)
                ball_y = frame_y + (j * bga_ball_pitch)
                ball = doc.addObject("Part::Sphere", f"BGA_Ball_{i}_{j}")
                ball.Radius = bga_ball_diameter / 2
                ball.Placement = Base.Placement(Base.Vector(ball_x, ball_y, -bga_ball_diameter / 2), Base.Rotation(0, 0, 0, 1))
                ball_objects.append(ball)

        # Create a compound of all BGA balls
        if ball_objects:
            bga_compound = doc.addObject("Part::Compound", "BGA_Balls")
            bga_compound.Links = ball_objects

    if gds_objects:
        # If GDS objects are provided, add them to the document
        for layer_id, objects in gds_objects.items():
            for obj in objects:
                obj.Placement = Base.Placement(Base.Vector(0, 0, frame_thickness + 0.01), Base.Rotation(0, 0, 0, 1))

    doc.recompute()
    FreeCADGui.activeDocument().activeView().viewIsometric()
    FreeCADGui.SendMsgToActiveView("ViewFit")

    return doc

def configure_leadframe():
    """
    Open the leadframe configuration dialog and create a leadframe based on user input.

    Returns: configuration
    """
    dialog = LeadframeConfigurator()
    if dialog.exec_():
        return dialog.get_config()
    return None

class LeadframeCommand:
    def GetResources(self):
        return {
            'MenuText': 'Leadframe Configurator',
            'ToolTip': 'Configure and generate a leadframe geometry',
            'Pixmap': ''
        }

    def Activated(self):
        config = configure_leadframe()
        if config:
            create_leadframe(config)
            QtWidgets.QMessageBox.information(None, "Success", f"Leadframe created:\n{config}")
        else:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Leadframe configuration cancelled.")

    def IsActive(self):
        return True

FreeCADGui.addCommand('LeadframeCommand', LeadframeCommand())