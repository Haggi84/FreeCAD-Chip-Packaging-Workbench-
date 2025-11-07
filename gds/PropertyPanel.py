from PySide2 import QtWidgets, QtGui, QtCore
import Part, os, sys
import FreeCAD, FreeCADGui

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from core.Core_Functionality import style_for_material, is_bondable, parse_lyp
from core.Color import hex_to_rgb, hex_to_qcolor

# -----------------------------------
# Dock panel for properties & tech
# -----------------------------------
 
class PropertyPanel(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super(PropertyPanel, self).__init__("Layer and Color Properties", parent)
        self.setAllowedAreas(QtCore.Qt.RightDockWidgetArea | QtCore.Qt.LeftDockWidgetArea)
        self.attached_doc = None

        self.main_widget = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout()

        self.tabs = QtWidgets.QTabWidget()

        # Tab 1: Layer properties (raw keys from LYP)
        self.layer_tree = QtWidgets.QTreeWidget()
        self.layer_tree.setHeaderLabels(["Property", "Value"])
        self.layer_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.tabs.addTab(self.layer_tree, "Layer Properties")

        # Tab 2: Color summary
        self.color_table = QtWidgets.QTableWidget()
        self.color_table.setColumnCount(3)
        self.color_table.setHorizontalHeaderLabels(["Frame Color", "Fill Color", "Count"])
        self.color_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.color_table, "Color Summary")

        # Tab 3: Technology (from IHP .map)
        self.tech_table = QtWidgets.QTableWidget()
        self.tech_table.setColumnCount(6)
        self.tech_table.setHorizontalHeaderLabels(["GDS Layer", "Datatype", "EDI Name", "EDI Types", "Material", "Bondable"])
        self.tech_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.tech_table, "Technology")

        self.layout.addWidget(self.tabs)

        # Buttons
        self.modify_layers_button = QtWidgets.QPushButton("Modify Layer Selection")
        self.modify_layers_button.clicked.connect(self.modify_layer_selection)
        self.layout.addWidget(self.modify_layers_button)

        self.close_button = QtWidgets.QPushButton("Close Panel")
        self.close_button.clicked.connect(self.close)
        self.layout.addWidget(self.close_button)

        self.main_widget.setLayout(self.layout)
        self.setWidget(self.main_widget)

        # Stored context for re-selection
        self.layer_objects = {}
        self.gds_path = None
        self.lyp_path = None
        self.map_path = None
        self.filtered_layers = None
        self.selected_layers = None
        self.ihp_map = {}  # (layer,datatype)->{edi_name,edi_types}
        self.options = {"match_klayout": True, "highlight_bondable": True}

        self.hide()

        self.doc_observer = self.make_doc_observer()
        FreeCADGui.addDocumentObserver(self.doc_observer)

    def attach_to_document(self, doc):
        self.attached_doc = doc

    def make_doc_observer(self):
        panel = self
        class Observer:
            def DeletedDocument(self, doc):
                if panel.attached_doc and doc == panel.attached_doc:
                   try:
                       # Close and delete the property panel
                       panel.setParent(None)
                       panel.close()
                   except Exception as e:
                       FreeCAD.Console.PrintError(f"Error closing property panel: {str(e)}\n")
        return Observer()

    def set_map(self, map_dict, map_path):
        self.ihp_map = map_dict or {}
        self.map_path = map_path

    def update_properties(self, selected_layers, unique_colors, layer_objects):
        """Populate the dock widgets with layer properties, color summary & technology."""
        self.layer_tree.clear()
        self.layer_objects = layer_objects
        self.selected_layers = selected_layers

        # ---- Layer properties
        if selected_layers:
            for selected_layer in selected_layers:
                layer_id = selected_layer.get("layer_id", 0)
                layer_datatype = selected_layer.get("datatype", 0)
                layer_name = selected_layer.get("name", "Unnamed")
                item = QtWidgets.QTreeWidgetItem()
                item.setText(0, f"{layer_name} ({layer_id}: {layer_datatype})")
                for key, value in selected_layer.items():
                    if key == "frame-color" or key == "fill-color":
                        c = hex_to_qcolor(value)
                        color_item = QtWidgets.QTreeWidgetItem([key, value])
                        color_item.setBackground(0, QtGui.QBrush(c))
                        color_item.setBackground(1, QtGui.QBrush(c))
                        item.addChild(color_item)
                    elif key not in ["frame-color", "fill-color"]:
                        child = QtWidgets.QTreeWidgetItem([key, str(value)])
                        item.addChild(child)
                self.layer_tree.addTopLevelItem(item)
            self.layer_tree.expandAll()
        else:
            self.layer_tree.addTopLevelItem(QtWidgets.QTreeWidgetItem(["No layer selected", "Please select a layer"]))

        # ---- Color summary -------
        color_counts = {}
        for selected_layer in selected_layers:
            frame_hex = selected_layer.get("frame-color", "#000000")
            fill_hex = selected_layer.get("fill-color", "#FFFFFF")
            color_counts[(frame_hex, fill_hex)] = color_counts.get((frame_hex, fill_hex), 0) + 1

        self.color_table.clearContents()
        self.color_table.setRowCount(len(color_counts))
        row = 0
        for (frame_hex, fill_hex), count in color_counts.items():
            frame_item = QtWidgets.QTableWidgetItem(frame_hex)
            frame_item.setBackground(QtGui.QBrush(hex_to_qcolor(frame_hex)))
            self.color_table.setItem(row, 0, frame_item)

            fill_item = QtWidgets.QTableWidgetItem(fill_hex)
            fill_item.setBackground(QtGui.QBrush(hex_to_qcolor(fill_hex)))
            self.color_table.setItem(row, 1, fill_item)

            self.color_table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(count)))
            row += 1
        self.color_table.resizeColumnsToContents()

        # Technology table
        self.tech_table.clearContents()
        self.tech_table.setRowCount(len(selected_layers))
        for r, layer in enumerate(selected_layers):
            lid = int(layer.get("layer_id", 0))
            dt = int(layer.get("datatype", 0))
            map_entry = self.ihp_map.get((lid, dt), None)
            edi_name = map_entry["edi_name"] if map_entry else "-"
            types = map_entry["edi_types"] if map_entry else set()
            edi_types = ", ".join(sorted(types))
            material_label, shape_rgb, line_rgb, tr = style_for_material(edi_name, types)
            bondable = "yes" if is_bondable(types) else "no"

            self.tech_table.setItem(r, 0, QtWidgets.QTableWidgetItem(str(lid)))
            self.tech_table.setItem(r, 1, QtWidgets.QTableWidgetItem(str(dt)))
            self.tech_table.setItem(r, 2, QtWidgets.QTableWidgetItem(edi_name))
            self.tech_table.setItem(r, 3, QtWidgets.QTableWidgetItem(edi_types))

            mat_item = QtWidgets.QTableWidgetItem(material_label)
            c = QtGui.QColor.fromRgbF(*shape_rgb)
            mat_item.setBackground(QtGui.QBrush(c))
            self.tech_table.setItem(r, 4, mat_item)

            self.tech_table.setItem(r, 5, QtWidgets.QTableWidgetItem(bondable))

        self.tech_table.resizeColumnsToContents()
    
        self.show()

    def modify_layer_selection(self):
        from ui.LayerSelector import LayerSelector
        from core import Core_Functionality

        """Reopen the layer selection dialog to modify selected layers."""
        if not self.gds_path or not self.lyp_path or not self.filtered_layers:
            QtWidgets.QMessageBox.critical(None, "Error", "Cannot modify layers: Missing file paths or layer data.")
            return

        dialog = LayerSelector(self.filtered_layers, self.selected_layers, options=self.options)
        if dialog.exec_():
            selected_layers = dialog.selected_layers
            options = dialog.options
            if not selected_layers:
                QtWidgets.QMessageBox.warning(None, "Warning", "No layers selected.")
                return

            self.options = dict(options)

            # New/clean document for preview update
            doc = FreeCAD.activeDocument()
            if not doc:
                doc = FreeCAD.newDocument("GDSII_Document")

            try:
                doc.openTransaction("Update Layer Selection")
            except Exception:
                pass

            # compute preview params from options
            match_klayout = bool(options.get("match_klayout", True))
            skip_fill = not match_klayout
            min_area = 0.0 if match_klayout else 0.0004
            decimate = 0.0 if match_klayout else 0.002


            layer_objects = {}
            shapes = Core_Functionality.load_gds(
                self.gds_path,
                selected_layers,
                transform=None,
                preview_2d=True,
                compound_per_layer=True,
                min_area_mm2=min_area,
                decimate_tol_mm=decimate,
                skip_fill_datatype=skip_fill
            )
            if not shapes:
                QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found for the selected layers.")
                return
            
            # Clear existing objects
            try:
                for obj in doc.Objects:
                    doc.removeObject(obj.Name)

            except Exception as e:
                        FreeCAD.Console.PrintError(f"Error removing object {obj.Name}: {str(e)}\n")

            use_klayout_colors = match_klayout
            highlight_bondable = bool(options.get("highlight_bondable", True))
            
            for layer in selected_layers:
                layer_id = layer.get("layer_id", 0)
                datatype = layer.get("datatype", 0)
                layer_name = layer.get("name", "Unnamed")
                frame_color = layer.get("frame-color", "#000000")
                fill_color = layer.get("fill-color", "#FFFFFF")
                
                map_entry = self.ihp_map.get((layer_id, datatype))
                types = map_entry["edi_types"] if map_entry else set()

                # decide colors
                if use_klayout_colors:
                    shape_rgb = hex_to_rgb(fill_color)
                    line_rgb = hex_to_rgb(frame_color)
                    tr = 0

                    if highlight_bondable and is_bondable(types):
                        shape_rgb = (0.95, 0.75, 0.20)
                        line_rgb = (0.95, 0.75, 0.20)
                        tr = 0

                else:
                    _, shape_rgb, line_rgb, tr = style_for_material(map_entry["edi_name"] if map_entry else "", types)

                    if not highlight_bondable and is_bondable(types):
                        # neutralize colors
                        shape_rgb = hex_to_rgb(fill_color)
                        line_rgb = hex_to_rgb(frame_color)
                        tr = 0


                shape = next((s for s in shapes if s["layer_id"] == layer_id and s["datatype"] == datatype), None)
                if not shape:
                    continue
                
                obj = doc.addObject("Part::Feature", f"Layer_{layer_name}_{layer_id}_{datatype}")
                obj.Shape = shape["shape"]
                obj.ViewObject.ShapeColor = shape_rgb
                obj.ViewObject.LineColor = line_rgb
                obj.ViewObject.Transparency = tr
                layer_objects.setdefault((layer_id, datatype), []).append(obj)

            try:
                obj.commitTransaction()
            except Exception:
                pass

            doc.recompute()
            self.update_properties(selected_layers, parse_lyp(self.lyp_path)[1], layer_objects)
            FreeCADGui.activeDocument().activeView().viewIsometric()
            FreeCADGui.SendMsgToActiveView("ViewFit")

            QtWidgets.QMessageBox.information(None, "Success", "Layer selection updated successfully.")