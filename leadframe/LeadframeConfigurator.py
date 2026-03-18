"""
Leadframe Configuration Dialog
UI for configuring QFN, QFP, and BGA leadframe parameters
"""

from PySide2 import QtWidgets


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
        self.left_lead_count = QtWidgets.QSpinBox()
        self.left_lead_count.setRange(0, 80)
        self.left_lead_count.setValue(4)
        qfn_qfp_layout.addRow("Left Lead Count:", self.left_lead_count)

        self.right_lead_count = QtWidgets.QSpinBox()
        self.right_lead_count.setRange(0, 80)
        self.right_lead_count.setValue(4)
        qfn_qfp_layout.addRow("Right Lead Count:", self.right_lead_count)

        self.top_lead_count = QtWidgets.QSpinBox()
        self.top_lead_count.setRange(0, 80)
        self.top_lead_count.setValue(4)
        qfn_qfp_layout.addRow("Top Lead Count:", self.top_lead_count)

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

    def accept(self):
        frame_type = self.frame_type.currentText()
        errors = []
        if frame_type in ["QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)"]:
            lead_pitch = self.lead_pitch.value()
            lead_width = self.lead_width.value()
            frame_length = self.frame_length.value()
            frame_width = self.frame_width.value()
            if lead_width >= lead_pitch:
                errors.append(f"Lead width ({lead_width} mm) must be less than lead pitch ({lead_pitch} mm) to avoid overlap.")
            left_span = (self.left_lead_count.value() - 1) * lead_pitch
            right_span = (self.right_lead_count.value() - 1) * lead_pitch
            top_span = (self.top_lead_count.value() - 1) * lead_pitch
            bottom_span = (self.bottom_lead_count.value() - 1) * lead_pitch
            if max(left_span, right_span) > frame_width:
                errors.append(f"Left/right lead span ({max(left_span, right_span):.2f} mm) exceeds frame width ({frame_width} mm).")
            if max(top_span, bottom_span) > frame_length:
                errors.append(f"Top/bottom lead span ({max(top_span, bottom_span):.2f} mm) exceeds frame length ({frame_length} mm).")
        elif frame_type == "BGA (Ball Grid Array)":
            if self.bga_ball_diameter.value() >= self.bga_ball_pitch.value():
                errors.append(f"BGA ball diameter ({self.bga_ball_diameter.value()} mm) must be less than ball pitch ({self.bga_ball_pitch.value()} mm).")
        if errors:
            QtWidgets.QMessageBox.warning(self, "Invalid Configuration", "\n\n".join(errors))
            return
        super().accept()

    def update_parameter_visibility(self):
        """Update the visibility of parameters based on the selected frame type."""
        frame_type = self.frame_type.currentText()
        self.qfn_qfp.setVisible(frame_type in ["QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)"])
        self.bga.setVisible(frame_type == "BGA (Ball Grid Array)")

    def get_config(self):
        """Return only relevant configuration parameters based on the selected frame type."""
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