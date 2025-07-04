from PySide2 import QtWidgets
import mymodule  # stellt sicher, dass dein Modul geladen wird
import FreeCAD
import FreeCADGui
import os
import Part

class LayerSelector(QtWidgets.QDialog):
    def __init__(self, layers, parent=None):
        super(LayerSelector, self).__init__(parent)
        self.setWindowTitle("Select Layers")
        self.layers = layers # List of (layer_name, layer_id, datatype)
        self.selected_layers = []


        # Layout
        layout = QtWidgets.QVBoxLayout()

        # Layer selection dropdown
        self.layer_combo = QtWidgets.QComboBox()
        for layer_name, layer_id in self.layers:
            self.layer_combo.addItem(f"{layer_name} (ID: {layer_id})", (layer_name, layer_id))
        layout.addWidget(self.layer_combo)

        # Button to load the selected layer
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)

        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)

    def accept(self):
        selected_data = self.layer_combo.currentData()
        if selected_data:
            self.selected_layers = [selected_data]
        super(LayerSelector, self).accept()

class GDSCommand:
    def GetResources(self):
        return {
            'MenuText': 'Load GDSII',
            'ToolTip': 'Load GDSII file with layer properties and select layers',
            'Pixmap': ''
        }
    
    def Activated(self):
        
        try:
            #select GDS file
            gds_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                None, "Select GDS File", "", "GDS Files (*.gds)"
                )
            
            if not gds_path or not os.path.exists(gds_path):
                QtWidgets.QMessageBox.critical(None, "Error", "GDS file not found or invalid path.")
                return
            
            # select LYP file
            lyp_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                None, "Select LYP File", "", "LYP Files (*.lyp)"
            )

            if not lyp_path or not os.path.exists(lyp_path):
                QtWidgets.QMessageBox.critical(None, "Error", "LYP file not found or invalid path.")
                return
            
            # Parse the LYP file to get layer information
            layers = mymodule.parse_lyp(lyp_path)
            if not layers:
                QtWidgets.QMessageBox.critical(None, "Error", "No layers found in the LYP file.")
                return
            
            # Analyze the GDS file to get layers with geometries
            gds_layers = mymodule.get_gds_layer(gds_path)
            if not gds_layers:
                QtWidgets.QMessageBox.Warning(None, "Error", "No layers found in the GDS file.")
                return
            
            # Filter layers that exist in the GDS file
            layers = [layer for layer in layers if (layer[1]) in gds_layers]
            if not layers:
                QtWidgets.QMessageBox.warning(None, "Error", "No matching layers found between LYP and GDS files.")
                return
            

            # Show the layer selection dialog
            dialog = LayerSelector(layers)
            if dialog.exec_():
                selected_layers = dialog.selected_layers
                if not selected_layers:
                    QtWidgets.QMessageBox.warning(None, "Error", "No layers selected.")
                    return
                
                # Load the GDSII file and display the selected layers
                shapes = mymodule.load_gds(gds_path, selected_layers)
                if not shapes:
                    QtWidgets.QMessageBox.warning(None, "Error", "No shapes found for the selected layers.")
                    return
                

                # Create a new FreeCAD document and add the shapes
                doc = FreeCAD.newDocument("GDSII_Document")

                for i, shape in enumerate(shapes):
                    obj = doc.addObject("Part::Feature", f"Layer_{selected_layers[0][0]}_{i}")
                    obj.Shape = shape

                doc.recompute()
                FreeCADGui.activeDocument().activeView().viewIsometric()
                FreeCADGui.SendMsgToActiveView("ViewFit")

                QtWidgets.QMessageBox.information(None, "Success", "GDS file loaded and layers displayed successfully.")
                
            else:
                QtWidgets.QMessageBox.information(None, "Cancelled", "Layer selection cancelled.")

        except Exception as e:
            FreeCAD.Console.PrintError(f"An error in GDSCommand: {str(e)}\n")
            QtWidgets.QMessageBox.critical(None, "Error", f"failed to process files: {str(e)}")

    def IsActive(self):
        return True

import FreeCADGui
FreeCADGui.addCommand('GDSCommand', GDSCommand())
