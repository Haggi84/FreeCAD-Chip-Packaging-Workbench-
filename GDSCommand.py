from PySide2 import QtWidgets, QtGui, QtCore
import mymodule  # stellt sicher, dass dein Modul geladen wird
import FreeCAD
import FreeCADGui
import os
import Part

def hex_to_rgb(hex_color):
    """
    Convert a hex color string to an RGB tuple.
    """
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

def hex_to_qcolor(hex_color):
    """
    Convert a hex color string to a QColor object.
    """
    return QtGui.QColor(hex_color)

class PropertyPanel(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super(PropertyPanel, self).__init__("Layer and Color Properties", parent)
        self.setAllowedAreas(QtCore.Qt.RightDockWidgetArea | QtCore.Qt.LeftDockWidgetArea)
        
        # Main widget
        self.main_widget = QtWidgets.QWidget()

        # Layout
        self.layout = QtWidgets.QVBoxLayout()

        # Tab widget for Layer Properties and Color Summary
        self.tab_widget = QtWidgets.QTabWidget()

        # Layer Properties tab
        self.layer_tree = QtWidgets.QTreeWidget()
        self.layer_tree.setHeaderLabels(["Property", "Value"])
        self.tab_widget.addTab(self.layer_tree, "Layer Properties")

        # Color Properties tab
        self.color_table = QtWidgets.QTableWidget()
        self.color_table.setColumnCount(3)
        self.color_table.setHorizontalHeaderLabels(["Frame Color", "Fill Color", "Count"])
        self.color_table.horizontalHeader().setStretchLastSection(True)
        self.tab_widget.addTab(self.color_table, "Color Summary")

        self.layout.addWidget(self.tab_widget)

        # Close button
        self.close_button = QtWidgets.QPushButton("Close Panel")
        self.close_button.clicked.connect(self.close)
        self.layout.addWidget(self.close_button)

        self.main_widget.setLayout(self.layout)
        self.setWidget(self.main_widget)

        # Initally hide the dock widget
        self.hide()

    def update_properties(self, selected_layer, unique_colors):
        """
        Update the properties displayed in the dock widget.
        """
        self.layer_tree.clear()
        if selected_layer is not None:
            item = QtWidgets.QTreeWidgetItem([f"{selected_layer.get('name', 'Unnamed')} ({selected_layer.get('layer_id', 0)})"])
        
            for key, value in selected_layer.items():
                if key == "frame-color":
                    color_qcolor = hex_to_qcolor(value)
                    color_item = QtWidgets.QTreeWidgetItem([key, value])
                    color_item.setBackground(0, QtGui.QBrush(color_qcolor))
                    color_item.setBackground(1, QtGui.QBrush(color_qcolor))
                    item.addChild(color_item)
                elif key == "fill-color":
                    color_qcolor = hex_to_qcolor(value)
                    color_item = QtWidgets.QTreeWidgetItem([key, value])
                    color_item.setBackground(0, QtGui.QBrush(color_qcolor))
                    color_item.setBackground(1, QtGui.QBrush(color_qcolor))
                    item.addChild(color_item)
                elif key not in ["frame-color", "fill-color"]:
                    child = QtWidgets.QTreeWidgetItem([key, str(value)])
                    item.addChild(child)
            self.layer_tree.addTopLevelItem(item)
            self.layer_tree.expandAll()
        else:
            item = QtWidgets.QTreeWidgetItem(["No layer selected", "Please select a layer"])
            self.layer_tree.addTopLevelItem(item)

        # Update color summary tab
        # Count occurrences of each color pair
        color_counts = {}
        if selected_layer is not None:
            frame_color_hex = selected_layer.get("frame-color", "#000000")
            fill_color_hex = selected_layer.get("fill-color", "#FFFFFF")
            # frame_color_qcolor = hex_to_qcolor(frame_color_hex)
            # fill_color_qcolor = hex_to_qcolor(fill_color_hex)
            color_pair = (frame_color_hex, fill_color_hex)
            color_counts[color_pair] = color_counts.get(color_pair, 0) + 1

        self.color_table.setRowCount(len(color_counts))
        row = 0
        for (frame_color_hex, fill_color_hex), count in color_counts.items():
            frame_color_qcolor = hex_to_qcolor(frame_color_hex)
            fill_color_qcolor = hex_to_qcolor(fill_color_hex)
            item = self.color_table.item(row, 0) or QtWidgets.QTableWidgetItem(frame_color_hex)
            item.setBackground(QtGui.QBrush(frame_color_qcolor))
            self.color_table.setItem(row, 0, item)

            item = self.color_table.item(row, 1) or QtWidgets.QTableWidgetItem(fill_color_hex)
            item.setBackground(QtGui.QBrush(fill_color_qcolor))
            self.color_table.setItem(row, 1, item)

            self.color_table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(count)))
            row += 1
        self.color_table.resizeColumnsToContents()

        self.show()
class LayerSelector(QtWidgets.QDialog):
    def __init__(self, layers, parent=None):
        super(LayerSelector, self).__init__(parent)
        self.setWindowTitle("Select Layers")
        self.layers = layers # List of layer dictionaries with all properties
        self.selected_layers = []


        # Layout
        layout = QtWidgets.QVBoxLayout()

        # Layer selection dropdown
        self.layer_combo = QtWidgets.QComboBox()
        for layer in self.layers:
            layer_name = layer.get("name", "Unknown Layer")
            layer_id = layer.get("layer_id", 0)
            self.layer_combo.addItem(f"{layer_name} ({layer_id})", layer)
        layout.addWidget(self.layer_combo)


        # Ok and Cancel buttons
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
            layers_with_colors = mymodule.parse_lyp(lyp_path)
            if not layers_with_colors:
                QtWidgets.QMessageBox.critical(None, "Error", "No layers found in the LYP file.")
                return
            
            layers, unique_colors = layers_with_colors

            # Analyze the GDS file to get layers with geometries
            gds_layers = mymodule.get_gds_layer(gds_path)
            if not gds_layers:
                QtWidgets.QMessageBox.warning(None, "Warning", "No layers found in the GDS file.")
                return
            
            # Filter layers that exist in the GDS file
            filtered_layers = [layer for layer in layers if layer.get("layer_id") in gds_layers]
            if not filtered_layers:
                QtWidgets.QMessageBox.warning(None, "Warning", "No matching layers found between LYP and GDS files.")
                return
            
            # Create a new property panel
            property_panel = PropertyPanel(FreeCADGui.getMainWindow())
            FreeCADGui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, property_panel)
            property_panel.update_properties(None, unique_colors)

            # Show the layer selection dialog
            dialog = LayerSelector(filtered_layers)
            if dialog.exec_():
                selected_layers = dialog.selected_layers
                if not selected_layers:
                    QtWidgets.QMessageBox.warning(None, "Warning", "No layers selected.")
                    return
                
                selected_layer = selected_layers[0]  # Use the first selected layer

                # Update the property panel with the selected layer
                property_panel.update_properties(selected_layer, unique_colors)

                # Create a new FreeCAD document and add the shapes
                doc = FreeCAD.newDocument("GDSII_Document")

                # Load the GDSII file and display the selected layers
                shapes = mymodule.load_gds(gds_path, selected_layers)

                if not shapes:
                    QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found for the selected layers.")
                    return

                for i, (shape, frame_color, fill_color) in enumerate(shapes):
                    obj = doc.addObject("Part::Feature", f"Layer_{selected_layers[0].get('name', 'Unnamed')}_{i}")
                    obj.Shape = shape

                    obj.ViewObject.ShapeColor = hex_to_rgb(fill_color)
                    obj.ViewObject.LineColor = hex_to_rgb(frame_color)

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
