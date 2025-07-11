import FreeCADGui
from PySide2 import QtWidgets, QtCore


class LeadframeConfigurator(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(LeadframeConfigurator, self).__init__(parent)
        self.setWindowTitle("Leadframe Configuration")
        self.setMinimumWidth(300)

        # Layout
        layout = QtWidgets.QFormLayout()

        # Leadframe Type Dropdown
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems(["Quad Flat Package", "Dual In-line Package", "Custom"])
        layout.addRow("Leadframe Type:", self.type_combo)

        # Number of Leads
        self.num_leads = QtWidgets.QSpinBox()
        self.num_leads.setRange(2, 200)
        self.num_leads.setValue(40)
        layout.addRow("Number of Leads:", self.num_leads)

        # Material Selection
        self.material_combo = QtWidgets.QComboBox()
        self.material_combo.addItems(["Copper", "Alloy 42", "Silver-Plated"])
        layout.addRow("Material:", self.material_combo)

        # Thickness
        self.thickness_input = QtWidgets.QDoubleSpinBox()
        self.thickness_input.setRange(0.05, 2.0)
        self.thickness_input.setSingleStep(0.05)
        self.thickness_input.setSuffix(" mm")
        self.thickness_input.setValue(0.2)
        layout.addRow("Thickness:", self.thickness_input)

        # OK/Cancel Buttons
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)

    def get_config(self):
        return {
            "type": self.type_combo.currentText(),
            "num_leads": self.num_leads.value(),
            "material": self.material_combo.currentText(),
            "thickness": self.thickness_input.value()
        }


class LeadframeCommand:
    def GetResources(self):
        return {
            'MenuText': 'Leadframe Configurator',
            'ToolTip': 'Open the Leadframe configuration dialog',
            'Pixmap': ''
        }

    def Activated(self):
        dialog = LeadframeConfigurator()
        if dialog.exec_():
            config = dialog.get_config()
            # Workaround for FreeCADGui.doCommand formatting issue
            FreeCADGui.doCommand(f"print({repr('Leadframe configuration selected: ' + str(config))})")
            QtWidgets.QMessageBox.information(None, "Configuration Saved", f"Leadframe configured:\n{config}")
        else:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Leadframe configuration cancelled.")

    def IsActive(self):
        return True

FreeCADGui.addCommand('LeadframeCommand', LeadframeCommand())
