from PySide2 import QtWidgets, QtCore, QtGui
import FreeCAD, FreeCADGui
class TransparentHousingConfigurator(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(TransparentHousingConfigurator, self).__init__(parent)
        self.setWindowTitle("Housing Configuration")
        # ... body ...

# ----------------------------------
# Layer on Leadframe Configuration
# ----------------------------------