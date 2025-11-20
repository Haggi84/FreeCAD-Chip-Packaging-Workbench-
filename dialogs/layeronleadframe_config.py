from PySide2 import QtWidgets, QtCore, QtGui
import FreeCAD, FreeCADGui
class LayeronLeadframeConfigurator(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transform Options")
        # ... body ...

# ---------------------------
# Wire Bonding Configuration
# ---------------------------