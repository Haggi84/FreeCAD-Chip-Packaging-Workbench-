from PySide2 import QtWidgets, QtCore, QtGui
import FreeCAD, FreeCADGui
class LeadframeConfigurator(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(LeadframeConfigurator, self).__init__(parent)
        self.setWindowTitle("Leadframe Configuration")
        # ... body ...

# -----------------------------------
# Housing Configuration
# -----------------------------------