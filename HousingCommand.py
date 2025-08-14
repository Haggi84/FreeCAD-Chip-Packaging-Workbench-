import FreeCADGui
from PySide2 import QtWidgets, QtCore
import FreeCAD, Part, Sketcher
from FreeCAD import Base
from LeadframeCommand import LeadframeConfigurator  # Import to reuse leadframe config

class TransparentHousingConfigurator(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(TransparentHousingConfigurator, self).__init__(parent)
        self.setWindowTitle("Housing Configuration")
        self.setMinimumWidth(400)

        # Layout
        layout = QtWidgets.QFormLayout()

        # Leadframe Configuration Button
        self.leadframe_config_button = QtWidgets.QPushButton("Configure Leadframe")
        self.leadframe_config_button.clicked.connect(self.open_leadframe_config)
        layout.addRow("Leadframe:", self.leadframe_config_button)

        # Display Leadframe Parameters (read-only)
        self.frame_type_label = QtWidgets.QLabel("Not configured")
        layout.addRow("Leadframe Type:", self.frame_type_label)

        self.leadframe_length_label = QtWidgets.QLabel("Not configured")
        layout.addRow("Leadframe Length:", self.leadframe_length_label)

        self.leadframe_width_label = QtWidgets.QLabel("Not configured")
        layout.addRow("Leadframe Width:", self.leadframe_width_label)

        self.leadframe_thickness_label = QtWidgets.QLabel("Not configured")
        layout.addRow("Leadframe Thickness:", self.leadframe_thickness_label)

        # Housing Parameters
        self.wall_thickness = QtWidgets.QDoubleSpinBox()
        self.wall_thickness.setRange(0.5, 5.0)
        self.wall_thickness.setSingleStep(0.1)
        self.wall_thickness.setSuffix(" mm")
        self.wall_thickness.setValue(0.5)
        layout.addRow("Wall Thickness:", self.wall_thickness)

        self.clearance = QtWidgets.QDoubleSpinBox()
        self.clearance.setRange(0.1, 2.0)
        self.clearance.setSingleStep(0.05)
        self.clearance.setSuffix(" mm")
        self.clearance.setValue(0.2)
        layout.addRow("Clearance:", self.clearance)

        self.housing_height = QtWidgets.QDoubleSpinBox()
        self.housing_height.setRange(0.5, 20.0)
        self.housing_height.setSingleStep(0.5)
        self.housing_height.setSuffix(" mm")
        self.housing_height.setValue(0.5)
        layout.addRow("Housing Height:", self.housing_height)

        self.include_lid = QtWidgets.QCheckBox("Include Transparent Lid")
        self.include_lid.setChecked(True)
        layout.addRow(self.include_lid)

        self.lid_thickness = QtWidgets.QDoubleSpinBox()
        self.lid_thickness.setRange(0.2, 3.0)
        self.lid_thickness.setSingleStep(0.1)
        self.lid_thickness.setSuffix(" mm")
        self.lid_thickness.setValue(0.5)
        layout.addRow("Lid Thickness:", self.lid_thickness)

        # Material Selection (transparent materials)
        self.material_combo = QtWidgets.QComboBox()
        self.material_combo.addItems(["Polycarbonate", "Acrylic", "Transparent ABS"])
        layout.addRow("Material:", self.material_combo)

        # Transparency Slider (0 = fully transparent, 100 = fully opaque)
        self.transparency = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.transparency.setRange(0, 100)
        self.transparency.setValue(50)  # Default: semi-transparent
        self.transparency.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self.transparency.setTickInterval(10)
        layout.addRow("Transparency (0=Clear, 100=Opaque):", self.transparency)

        # OK/Cancel Buttons
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)
        self.leadframe_config = None

    def open_leadframe_config(self):
        dialog = LeadframeConfigurator()
        if dialog.exec_():
            self.leadframe_config = dialog.get_config()
            self.update_leadframe_display()

    def update_leadframe_display(self):
        if self.leadframe_config:
            self.frame_type_label.setText(self.leadframe_config["frame_type"])
            self.leadframe_length_label.setText(f"{self.leadframe_config['frame_length']} mm")
            self.leadframe_width_label.setText(f"{self.leadframe_config['frame_width']} mm")
            self.leadframe_thickness_label.setText(f"{self.leadframe_config['frame_thickness']} mm")

    def get_config(self):
        if not self.leadframe_config:
            FreeCAD.Console.PrintError("Leadframe configuration not set.")
        config = self.leadframe_config.copy()
        config.update({
            "wall_thickness": self.wall_thickness.value(),
            "clearance": self.clearance.value(),
            "housing_height": self.housing_height.value(),
            "include_lid": self.include_lid.isChecked(),
            "lid_thickness": self.lid_thickness.value(),
            "material": self.material_combo.currentText(),
            "transparency": self.transparency.value() / 100.0  # Convert to 0-1 scale
        })
        return config

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

    # Adjust for lead extensions (QFP) or pad thickness (QFN)
    extra_clearance = 0
    if frame_type == "QFP (Quad Flat Package)":
        extra_clearance = config.get("lead_length", 0)
    elif frame_type == "QFN (Quad Flat No-lead)":
        extra_clearance = config.get("qfn_pad_thickness", 0)
    elif frame_type == "BGA (Ball Grid Array)":
        extra_clearance = config.get("bga_ball_diameter", 0) / 2

    # Calculate outer dimensions
    outer_length = leadframe_length + 2 * (wall_thickness + clearance + extra_clearance)
    outer_width = leadframe_width + 2 * (wall_thickness + clearance + extra_clearance)
    outer_height = housing_height + wall_thickness

    # Create outer housing sketch
    outer_sketch = doc.addObject("Sketcher::SketchObject", f"HousingOuter_{material}")
    outer_sketch.Placement = Base.Placement(Base.Vector(0, 0, 0), Base.Rotation(0, 0, 0, 1))

    outer_x = -outer_length / 2
    outer_y = -outer_width / 2
    outer_x_end = outer_x + outer_length
    outer_y_end = outer_y + outer_width

    outer_lines = [
        Part.LineSegment(Base.Vector(outer_x, outer_y, 0), Base.Vector(outer_x_end, outer_y, 0)),
        Part.LineSegment(Base.Vector(outer_x_end, outer_y, 0), Base.Vector(outer_x_end, outer_y_end, 0)),
        Part.LineSegment(Base.Vector(outer_x_end, outer_y_end, 0), Base.Vector(outer_x, outer_y_end, 0)),
        Part.LineSegment(Base.Vector(outer_x, outer_y_end, 0), Base.Vector(outer_x, outer_y, 0))
    ]

    for i, line in enumerate(outer_lines):
        outer_sketch.addGeometry(line)
        if i > 0:
            outer_sketch.addConstraint(Sketcher.Constraint('Coincident', i-1, 2, i, 1))
    outer_sketch.addConstraint(Sketcher.Constraint('Coincident', len(outer_lines)-1, 2, 0, 1))

    # Extrude outer housing
    outer_body = doc.addObject("Part::Extrusion", "HousingOuterBody")
    outer_body.Base = outer_sketch
    outer_body.Dir = Base.Vector(0, 0, outer_height)
    outer_body.Solid = True

    # Create inner cavity sketch
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
            inner_sketch.addConstraint(Sketcher.Constraint('Coincident', i-1, 2, i, 1))
    inner_sketch.addConstraint(Sketcher.Constraint('Coincident', len(inner_lines)-1, 2, 0, 1))

    # Extrude inner cavity to cut
    inner_cut = doc.addObject("Part::Extrusion", "HousingInnerCut")
    inner_cut.Base = inner_sketch
    inner_cut.Dir = Base.Vector(0, 0, inner_height)
    inner_cut.Solid = True

    # Perform cut operation
    housing = doc.addObject("Part::Cut", "HousingBody")
    housing.Base = outer_body
    housing.Tool = inner_cut

    # Apply transparency to housing
    housing_obj = doc.getObject("HousingBody")
    housing_obj.ViewObject.Transparency = int(transparency * 100)  # FreeCAD expects 0-100

    # Add alignment posts (4 corners)
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

    # Apply transparency to posts
    post_extrusion.ViewObject.Transparency = int(transparency * 100)

    # Fuse posts with housing
    final_housing = doc.addObject("Part::Fuse", "FinalHousing")
    final_housing.Base = housing
    final_housing.Tool = post_extrusion
    final_housing.ViewObject.Transparency = int(transparency * 100)

    # Create lid if requested
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
        lid_extrusion.ViewObject.Transparency = int(transparency * 100)

    doc.recompute()
    FreeCADGui.activeDocument().activeView().viewIsometric()
    FreeCADGui.SendMsgToActiveView("ViewFit")

class HousingCommand:
    def GetResources(self):
        return {
            'MenuText': 'Housing Configurator',
            'ToolTip': 'Configure and generate a transparent housing for a leadframe',
            'Pixmap': ''
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