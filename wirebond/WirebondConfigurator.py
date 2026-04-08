"""
Wire Bonding Configuration Dialog
UI for configuring wire bonding parameters
"""

from PySide2 import QtWidgets


class WirebondConfigurator(QtWidgets.QDialog):
    """Dialog to configure wire bonding parameters."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wire Bonding Configuration")
        layout = QtWidgets.QVBoxLayout()

        # Wire parameters
        self.loop_height = QtWidgets.QDoubleSpinBox()
        self.loop_height.setRange(0.1, 5.0)
        self.loop_height.setSingleStep(0.1)
        self.loop_height.setValue(0.5)
        self.loop_height.setSuffix(" mm")
        layout.addWidget(QtWidgets.QLabel("Loop Height:"))
        layout.addWidget(self.loop_height)

        self.diameter = QtWidgets.QDoubleSpinBox()
        self.diameter.setRange(0.001, 0.1)
        self.diameter.setDecimals(3)
        self.diameter.setSingleStep(0.005)
        self.diameter.setValue(0.025)
        self.diameter.setSuffix(" mm")
        layout.addWidget(QtWidgets.QLabel("Wire Diameter:"))
        layout.addWidget(self.diameter)

        # Design rule constraints
        self.min_wire_spacing = QtWidgets.QDoubleSpinBox()
        self.min_wire_spacing.setRange(0.05, 1.0)
        self.min_wire_spacing.setSingleStep(0.05)
        self.min_wire_spacing.setValue(0.1)
        self.min_wire_spacing.setSuffix(" mm")
        layout.addWidget(QtWidgets.QLabel("Minimum Wire-to-Wire Spacing:"))
        layout.addWidget(self.min_wire_spacing)

        self.min_wire_length = QtWidgets.QDoubleSpinBox()
        self.min_wire_length.setRange(0.1, 10.0)
        self.min_wire_length.setSingleStep(0.1)
        self.min_wire_length.setValue(0.5)
        self.min_wire_length.setSuffix(" mm")
        layout.addWidget(QtWidgets.QLabel("Minimum Wire Length:"))
        layout.addWidget(self.min_wire_length)

        self.max_wire_length = QtWidgets.QDoubleSpinBox()
        self.max_wire_length.setRange(0.5, 20.0)
        self.max_wire_length.setSingleStep(0.5)
        self.max_wire_length.setValue(5.0)
        self.max_wire_length.setSuffix(" mm")
        layout.addWidget(QtWidgets.QLabel("Maximum Wire Length:"))
        layout.addWidget(self.max_wire_length)

        self.bond_finger_margin = QtWidgets.QDoubleSpinBox()
        self.bond_finger_margin.setRange(0.01, 0.5)
        self.bond_finger_margin.setSingleStep(0.01)
        self.bond_finger_margin.setValue(0.05)
        self.bond_finger_margin.setSuffix(" mm")
        layout.addWidget(QtWidgets.QLabel("Bond Finger Margin:"))
        layout.addWidget(self.bond_finger_margin)

        # Buttons
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        self.setLayout(layout)

    def get_config(self):
        """Return wire bonding configuration."""
        return {
            "loop_height": self.loop_height.value(),
            "diameter": self.diameter.value(),
            "min_wire_spacing": self.min_wire_spacing.value(),
            "min_wire_length": self.min_wire_length.value(),
            "max_wire_length": self.max_wire_length.value(),
            "bond_finger_margin": self.bond_finger_margin.value()
        }