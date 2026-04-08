"""
Housing Configuration Dialog
UI for configuring transparent housing around leadframes
"""

from PySide2 import QtWidgets, QtCore
import FreeCAD, os, sys

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from leadframe.LeadframeConfigurator import LeadframeConfigurator


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
        self.material_combo.addItems(["Polycarbonate", "Acrylic", "Transparent ABS", "Black Epoxy"])
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

        self.auto_detect_leadframe()

    def open_leadframe_config(self):
        dialog = LeadframeConfigurator()
        if dialog.exec_():
            self.leadframe_config = dialog.get_config()
            self.update_leadframe_display()

    def auto_detect_leadframe(self):
        import FreeCAD
        doc = FreeCAD.activeDocument()
        if not doc:
            return

        # 1. Find the 3D leadframe objects
        lf_objects = []
        for obj in doc.Objects:
            name = obj.Name.lower()
            if "leadframeleads" in name or "diepaddle" in name or "bga_substrate" in name:
                lf_objects.append(obj)

        if not lf_objects:
            return

        # 2. Detect the exact leadframe type
        detected_type = "Unknown Leadframe"
        if any("bga_substrate" in obj.Name.lower() for obj in lf_objects):
            detected_type = "BGA (Ball Grid Array)"
        else:
            # Look at the leads to tell QFP apart from QFN
            leads_obj = next((obj for obj in lf_objects if "leadframeleads" in obj.Name.lower()), None)
            if leads_obj and hasattr(leads_obj, "Shape") and not leads_obj.Shape.isNull():
                # QFP leads bend down, making them tall in the Z-axis (usually > 0.4mm)
                # QFN leads are flat pads (usually < 0.3mm)
                if leads_obj.Shape.BoundBox.ZLength > 0.4:
                    detected_type = "QFP (Quad Flat Package)"
                else:
                    detected_type = "QFN (Quad Flat No-lead)"

        # 3. Measure their absolute max dimensions
        xmin = ymin = zmin = float('inf')
        xmax = ymax = zmax = float('-inf')

        for obj in lf_objects:
            if hasattr(obj, "Shape") and not obj.Shape.isNull():
                bb = obj.Shape.BoundBox
                xmin = min(xmin, bb.XMin)
                ymin = min(ymin, bb.YMin)
                zmin = min(zmin, bb.ZMin)
                xmax = max(xmax, bb.XMax)
                ymax = max(ymax, bb.YMax)
                zmax = max(zmax, bb.ZMax)

        if xmin != float('inf'):
            length = round(xmax - xmin, 2)
            width = round(ymax - ymin, 2)
            thickness = round(zmax - zmin, 2)

            # 4. Auto-fill the configuration dictionary
            self.leadframe_config = {
                "frame_type": detected_type,
                "frame_length": length - 1.0,
                "frame_width": width - 1.0,
                "frame_thickness": thickness - 0.6,
                "material": "Auto-Detected"
            }
            
            # 5. Visual feedback
            self.leadframe_config_button.setText("Leadframe Auto-Detected!")
            self.leadframe_config_button.setStyleSheet("background-color: #a8e6cf; color: black; font-weight: bold;")
            
            self.update_leadframe_display()

    def update_leadframe_display(self):
        if self.leadframe_config:
            leadframe_length = self.leadframe_config.get("frame_length", 0.0)
            leadframe_width = self.leadframe_config.get("frame_width", 0.0)
            leadframe_thickness = self.leadframe_config.get("frame_thickness", 0.0)

            self.frame_type_label.setText(self.leadframe_config["frame_type"])
            self.leadframe_length_label.setText(f"{leadframe_length:0.2f} mm")
            self.leadframe_width_label.setText(f"{leadframe_width:0.2f} mm")
            self.leadframe_thickness_label.setText(f"{leadframe_thickness:0.2f} mm")

    def get_config(self):
        if not self.leadframe_config:
            FreeCAD.Console.PrintError("Leadframe configuration not set.\n")
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