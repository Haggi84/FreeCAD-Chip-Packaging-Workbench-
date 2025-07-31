from PySide2 import QtWidgets, QtGui, QtCore
import mymodule
import FreeCAD
import FreeCADGui
import os
from FreeCAD import Part

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
        self.layer_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.tab_widget.addTab(self.layer_tree, "Layer Properties")

        # Color Properties tab
        self.color_table = QtWidgets.QTableWidget()
        self.color_table.setColumnCount(3)
        self.color_table.setHorizontalHeaderLabels(["Frame Color", "Fill Color", "Count"])
        self.color_table.horizontalHeader().setStretchLastSection(True)
        self.tab_widget.addTab(self.color_table, "Color Summary")

        self.layout.addWidget(self.tab_widget)

        # Button to modify layer selection
        self.modify_layers_button = QtWidgets.QPushButton("Modify Layer Selection")
        self.modify_layers_button.clicked.connect(self.modify_layer_selection)
        self.layout.addWidget(self.modify_layers_button)

        # Close button
        self.close_button = QtWidgets.QPushButton("Close Panel")
        self.close_button.clicked.connect(self.close)
        self.layout.addWidget(self.close_button)

        self.main_widget.setLayout(self.layout)
        self.setWidget(self.main_widget)

        # Store layer objects, paths, and layers for re-selection
        self.layer_objects = {}  # Dictionary: layer_id -> list of FreeCAD objects
        self.gds_path = None
        self.lyp_path = None
        self.filtered_layers = None
        self.selected_layers = None

        # Initially hide the dock widget
        self.hide()

    def update_properties(self, selected_layers, unique_colors, layer_objects):
        """
        Update the properties displayed in the dock widget for multiple selected layers.
        """
        self.layer_tree.clear()
        self.layer_objects = layer_objects  # Store the layer objects
        self.selected_layers = selected_layers  # Store the selected layers

        if selected_layers:
            for selected_layer in selected_layers:
                layer_id = selected_layer.get("layer_id", 0)
                item = QtWidgets.QTreeWidgetItem()
                item.setText(0, f"{selected_layer.get('name', 'Unnamed')} ({layer_id})")
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
        color_counts = {}
        for selected_layer in selected_layers:
            frame_color_hex = selected_layer.get("frame-color", "#000000")
            fill_color_hex = selected_layer.get("fill-color", "#FFFFFF")
            color_pair = (frame_color_hex, fill_color_hex)
            color_counts[color_pair] = color_counts.get(color_pair, 0) + 1

        self.color_table.clearContents()
        self.color_table.setRowCount(len(color_counts))
        row = 0
        for (frame_color_hex, fill_color_hex), count in color_counts.items():
            frame_color_qcolor = hex_to_qcolor(frame_color_hex)
            fill_color_qcolor = hex_to_qcolor(fill_color_hex)
            
            frame_item = QtWidgets.QTableWidgetItem(frame_color_hex)
            frame_item.setBackground(QtGui.QBrush(frame_color_qcolor))
            self.color_table.setItem(row, 0, frame_item)

            fill_item = QtWidgets.QTableWidgetItem(fill_color_hex)
            fill_item.setBackground(QtGui.QBrush(fill_color_qcolor))
            self.color_table.setItem(row, 1, fill_item)

            self.color_table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(count)))
            row += 1
        self.color_table.resizeColumnsToContents()

        self.show()

    def modify_layer_selection(self):
        """
        Reopen the layer selection dialog to modify selected layers.
        """
        if not self.gds_path or not self.lyp_path or not self.filtered_layers:
            QtWidgets.QMessageBox.critical(None, "Error", "Cannot modify layers: Missing file paths or layer data.")
            return

        dialog = LayerSelector(self.filtered_layers, self.selected_layers)
        if dialog.exec_():
            selected_layers = dialog.selected_layers
            if not selected_layers:
                QtWidgets.QMessageBox.warning(None, "Warning", "No layers selected.")
                return

            # Update the FreeCAD document with new layer selection
            doc = FreeCAD.ActiveDocument
            if not doc:
                doc = FreeCAD.newDocument("GDSII_Document")

            # Clear existing objects
            for obj in doc.Objects:
                doc.removeObject(obj.Name)

            # Load new shapes
            layer_objects = {}  # Dictionary to store objects by layer_id
            shapes = mymodule.load_gds(self.gds_path, selected_layers)

            if not shapes:
                QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found for the selected layers.")
                return

            for layer in selected_layers:
                layer_id = layer.get("layer_id", 0)
                datatype = layer.get("datatype", 0)
                layer_name = layer.get("name", "Unnamed")
                frame_color = layer.get("frame-color", "#000000")
                fill_color = layer.get("fill-color", "#FFFFFF")
                layer_objects[layer_id] = []
                for i, (shape, shape_frame_color, shape_fill_color) in enumerate(shapes):
                    if (shape_frame_color, shape_fill_color) == (frame_color, fill_color):
                        obj = doc.addObject("Part::Feature", f"Layer_{layer_name}_{layer_id}_{datatype}_{i}")
                        obj.Shape = shape
                        obj.ViewObject.ShapeColor = hex_to_rgb(fill_color)
                        obj.ViewObject.LineColor = hex_to_rgb(frame_color)
                        layer_objects[layer_id].append(obj)

            # Update the property panel with new selected layers
            self.update_properties(selected_layers, mymodule.parse_lyp(self.lyp_path)[1], layer_objects)

            doc.recompute()
            FreeCADGui.activeDocument().activeView().viewIsometric()
            FreeCADGui.SendMsgToActiveView("ViewFit")

            QtWidgets.QMessageBox.information(None, "Success", "Layer selection updated successfully.")

class LayerSelector(QtWidgets.QDialog):
    def __init__(self, layers, selected_layers=None, parent=None):
        super(LayerSelector, self).__init__(parent)
        self.setWindowTitle("Select Layers")
        self.layers = layers  # List of layer dictionaries with all properties
        self.selected_layers = []
        self.selected_layers_prev = selected_layers or []  # Previously selected layers

        # Layout
        layout = QtWidgets.QVBoxLayout()

        # Layer selection list widget with checkboxes
        self.layer_list = QtWidgets.QListWidget()
        for layer in self.layers:
            layer_name = layer.get("name", "Unknown Layer")
            layer_id = layer.get("layer_id", 0)
            item = QtWidgets.QListWidgetItem(f"{layer_name} ({layer_id})")
            item.setData(QtCore.Qt.UserRole, layer)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            # Set checkbox state based on previous selection
            item.setCheckState(QtCore.Qt.Checked if layer in self.selected_layers_prev else QtCore.Qt.Unchecked)
            self.layer_list.addItem(item)
        layout.addWidget(self.layer_list)

        # Ok and Cancel buttons
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)

    def accept(self):
        self.selected_layers = []
        for i in range(self.layer_list.count()):
            item = self.layer_list.item(i)
            if item.checkState() == QtCore.Qt.Checked:
                self.selected_layers.append(item.data(QtCore.Qt.UserRole))
        if not self.selected_layers:
            QtWidgets.QMessageBox.warning(self, "Warning", "No layers selected.")
            return
        super(LayerSelector, self).accept()

def load_gds_layers():
    """
    Load GDS layers

    Returns:
        list: A list of dictionaries containing layer properties for example freecad document,
        layer objects, selected layers, and unique colors.
    """

    try:
        # Select GDS file
        gds_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Select GDS File", "", "GDS Files (*.gds)"
        )
            
        if not gds_path or not os.path.exists(gds_path):
            QtWidgets.QMessageBox.critical(None, "Error", "GDS file not found or invalid path.")
            return None, None, None, None            
        
        # Select LYP file
        lyp_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Select LYP File", "", "LYP Files (*.lyp)"
        )

        if not lyp_path or not os.path.exists(lyp_path):
            QtWidgets.QMessageBox.critical(None, "Error", "LYP file not found or invalid path.")
            return None, None, None, None

        # Parse the LYP file to get layer information
        layers_with_colors = mymodule.parse_lyp(lyp_path)
        if not layers_with_colors:
            QtWidgets.QMessageBox.critical(None, "Error", "No layers found in the LYP file.")
            return None, None, None, None

        layers, unique_colors = layers_with_colors

        # Analyze the GDS file to get layers with geometries
        gds_layers = mymodule.get_gds_layer(gds_path)
        if not gds_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No layers found in the GDS file.")
            return None, None, None, None

        # Filter layers that exist in the GDS file
        filtered_layers = [layer for layer in layers if (layer.get("layer_id", 0), layer.get("datatype", 0)) in gds_layers]
        if not filtered_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No matching layers found between LYP and GDS files.")
            return None, None, None, None
            
        # Create a new property panel
        property_panel = PropertyPanel(FreeCADGui.getMainWindow())
        FreeCADGui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, property_panel)
        property_panel.gds_path = gds_path
        property_panel.lyp_path = lyp_path
        property_panel.filtered_layers = filtered_layers
        property_panel.update_properties([], unique_colors, {})

        # Show the layer selection dialog
        dialog = LayerSelector(filtered_layers)
        if dialog.exec_():
            selected_layers = dialog.selected_layers
            if not selected_layers:
                QtWidgets.QMessageBox.warning(None, "Warning", "No layers selected.")
                return None, None, None, None

            # Create a new FreeCAD document and add the shapes
            doc = FreeCAD.newDocument("GDSII_Document")
            layer_objects = {}  # Dictionary to store objects by layer_id

            # Load the GDSII file and display the selected layers
            shapes = mymodule.load_gds(gds_path, selected_layers)

            if not shapes:
                QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found for the selected layers.")
                return None, None, None, None

            for layer in selected_layers:
                layer_id = layer.get("layer_id", 0)
                datatype = layer.get("datatype", 0)
                layer_name = layer.get("name", "Unnamed")
                frame_color = layer.get("frame-color", "#000000")
                fill_color = layer.get("fill-color", "#FFFFFF")
                layer_objects[layer_id] = []
                for i, (shape, shape_frame_color, shape_fill_color) in enumerate(shapes):
                    if (shape_frame_color, shape_fill_color) == (frame_color, fill_color):
                        obj = doc.addObject("Part::Feature", f"Layer_{layer_name}_{layer_id}_{datatype}_{i}")
                        obj.Shape = shape
                        obj.ViewObject.ShapeColor = hex_to_rgb(fill_color)
                        obj.ViewObject.LineColor = hex_to_rgb(frame_color)
                        layer_objects[layer_id].append(obj)

            # Update the property panel with all selected layers and their objects
            property_panel.update_properties(selected_layers, unique_colors, layer_objects)

            doc.recompute()
            FreeCADGui.activeDocument().activeView().viewIsometric()
            FreeCADGui.SendMsgToActiveView("ViewFit")

            return doc, layer_objects, selected_layers, unique_colors
                
        else:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Layer selection cancelled.")
            return None, None, None, None

    except Exception as e:
        FreeCAD.Console.PrintError(f"An error in GDSCommand: {str(e)}\n")
        QtWidgets.QMessageBox.critical(None, "Error", f"Failed to process files: {str(e)}")
        return None, None, None, None

class GDSCommand:
    def GetResources(self):
        return {
            'MenuText': 'Load GDSII',
            'ToolTip': 'Load GDSII file with layer properties and select layers',
            'Pixmap': ''
        }
    
    def Activated(self):
        doc, layer_objects, selected_layers, unique_colors = load_gds_layers()
        if doc and layer_objects:
            QtWidgets.QMessageBox.information(
                None,
                "Success",
                "GDSII file loaded and layers displayed successfully."
            )
            

    def IsActive(self):
        return True

import FreeCADGui
FreeCADGui.addCommand('GDSCommand', GDSCommand())