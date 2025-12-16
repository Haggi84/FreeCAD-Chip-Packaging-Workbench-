from PySide2 import QtWidgets
import FreeCADGui as Gui
from ComponentLibrary import list_types, get_param_fields, build_component

class CreatePackageDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(CreatePackageDialog, self).__init__(parent)
        self.setWindowTitle("Create Package")
        self.layout = QtWidgets.QFormLayout(self)

        # Package type dropdown
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems(list_types())
        self.layout.addRow("Package Type", self.type_combo)

        # Area where parameters appear
        self.param_area = QtWidgets.QWidget()
        self.param_layout = QtWidgets.QFormLayout(self.param_area)
        self.layout.addRow(self.param_area)

        # Export checkbox
        self.export_checkbox = QtWidgets.QCheckBox("Export STEP")
        self.layout.addRow(self.export_checkbox)

        # OK/Cancel buttons
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.layout.addRow(buttons)

        # Build fields for the first time
        self.fields = {}
        self.type_combo.currentIndexChanged.connect(self.update_fields)
        self.update_fields()

    def update_fields(self):
        # Clear old fields
        while self.param_layout.count():
            child = self.param_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.fields = {}

        pkg_type = self.type_combo.currentText()
        for name, typ, default in get_param_fields(pkg_type):
            box = QtWidgets.QLineEdit(str(default))
            self.param_layout.addRow(name, box)
            self.fields[name] = (box, typ)

    def get_values(self):
        pkg_type = self.type_combo.currentText()
        params = {}
        for name, (widget, typ) in self.fields.items():
            params[name] = typ(widget.text())
        return pkg_type, params, self.export_checkbox.isChecked()

def show_dialog():
    dlg = CreatePackageDialog(Gui.getMainWindow())
    if dlg.exec_():
        pkg_type, params, export_flag = dlg.get_values()
        build_component(pkg_type, params, export_flag)
