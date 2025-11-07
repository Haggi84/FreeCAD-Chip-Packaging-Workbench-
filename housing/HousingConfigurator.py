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