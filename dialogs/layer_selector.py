from PySide2 import QtWidgets, QtCore, QtGui
import FreeCAD, FreeCADGui
class LayerSelector(QtWidgets.QDialog):
    """
    Layer selection dialog with quick actions:
      - 'Import all layers' checkbox
      - Select All / Clear / Invert buttons
      - Ctrl+A shortcut to select all
    """
    def __init__(self, layers, selected_layers=None, parent=None, options=None):
        super(LayerSelector, self).__init__(parent)
        self.setWindowTitle("Select Layers")
        self.layers = layers
        self.selected_layers = []
        self.selected_layers_prev = selected_layers or []
        self.options = dict(options or {"match_klayout": True, "highlight_bondable": True})

        layout = QtWidgets.QVBoxLayout(self)
        # ... (rest of implementation omitted in this embedded snippet) ...

# --------------------------------------
# Leadframe Configuration
# --------------------------------------