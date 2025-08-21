from PySide2 import QtWidgets, QtGui, QtCore
import os
import FreeCAD
import FreeCADGui

import mymodule  # local helper module


# -----------------------
# Color / style helpers
# -----------------------
def hex_to_rgb(hex_color):
    """Convert a hex color string to an RGB tuple in 0..1."""
    hex_color = (hex_color or "#000000").lstrip('#')
    return tuple(int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def hex_to_qcolor(hex_color):
    """Convert a hex color string to a QColor."""
    return QtGui.QColor(hex_color or "#000000")


def _is_bondable(types: set) -> bool:
    if not types:
        return False
    T = {t.upper() for t in types}
    return any(t in T for t in ("PIN", "LEFPIN", "BUMP", "PAD"))


def style_for_material(edi_name: str, edi_types: set):
    """
    Return a simple material style tuple:
        (material_label:str, shape_rgb:tuple, line_rgb:tuple, transparency:int[0..100])
    Bondable layers get a gold style.
    """
    en = (edi_name or "").upper()
    et = {t.upper() for t in (edi_types or set())}

    # Gold highlight for bondable
    if _is_bondable(et):
        return ("Bondable metal", (0.90, 0.75, 0.20), (0.25, 0.20, 0.10), 0)

    # Vias darker
    if "VIA" in et and "FILL" not in et:
        return ("Via metal", (0.35, 0.35, 0.35), (0.08, 0.08, 0.08), 0)

    # Fill semi transparent
    if "FILL" in et:
        return ("Metal fill / dielectric", (0.70, 0.85, 1.0), (0.25, 0.35, 0.45), 70)

    # Routing metals
    if en.startswith("TOPMETAL") or en.startswith("METAL"):
        return ("Routing metal", (0.60, 0.60, 0.60), (0.12, 0.12, 0.12), 0)

    if en.startswith("COMP") or en.startswith("DIEAREA"):
        return ("Component/Die", (0.80, 0.90, 0.95), (0.25, 0.35, 0.45), 60)

    return ("Generic", (0.75, 0.75, 0.75), (0.10, 0.10, 0.10), 0)


# -----------------------------------
# Dock panel for properties & tech
# -----------------------------------
class PropertyPanel(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super(PropertyPanel, self).__init__("Layer and Color Properties", parent)
        self.setAllowedAreas(QtCore.Qt.RightDockWidgetArea | QtCore.Qt.LeftDockWidgetArea)

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

        # Stored context
        self.layer_objects = {}
        self.gds_path = None
        self.lyp_path = None
        self.map_path = None
        self.filtered_layers = None
        self.selected_layers = None
        self.ihp_map = {}   # (layer,datatype)->{edi_name,edi_types}
        self.options = {"match_klayout": False, "highlight_bondable": True}

        self.hide()

    def set_map(self, map_dict, map_path):
        self.ihp_map = map_dict or {}
        self.map_path = map_path

    def update_properties(self, selected_layers, unique_colors, layer_objects):
        """Populate the dock widgets with layer properties, color summary & technology."""
        self.layer_tree.clear()
        self.layer_objects = layer_objects
        self.selected_layers = selected_layers

        # Layer properties
        if selected_layers:
            for selected_layer in selected_layers:
                layer_id = selected_layer.get("layer_id", 0)
                item = QtWidgets.QTreeWidgetItem()
                item.setText(0, f"{selected_layer.get('name', 'Unnamed')} ({layer_id})")
                for key, value in selected_layer.items():
                    if key == "frame-color":
                        c = hex_to_qcolor(value)
                        color_item = QtWidgets.QTreeWidgetItem([key, value])
                        color_item.setBackground(0, QtGui.QBrush(c))
                        color_item.setBackground(1, QtGui.QBrush(c))
                        item.addChild(color_item)
                    elif key == "fill-color":
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

        # Color summary
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
            bondable = "yes" if _is_bondable(types) else "no"

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
            doc = FreeCAD.ActiveDocument or FreeCAD.newDocument("GDSII_Document")
            try:
                doc.openTransaction("Update Layer Preview")
            except Exception:
                pass

            # compute preview params from options
            match_klayout = bool(options.get("match_klayout", False))
            skip_fill = not match_klayout
            min_area = 0.0 if match_klayout else 0.0004
            decimate = 0.0 if match_klayout else 0.002

            # Reload shapes (fast 2D preview)
            layer_objects = {}
            entries = mymodule.load_gds_fast(
                self.gds_path,
                selected_layers,
                transform=None,
                preview_2d=True,
                compound_per_layer=True,
                min_area_mm2=min_area,
                decimate_tol_mm=decimate,
                skip_fill_datatype=skip_fill
            )
            if not entries:
                QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found for the selected layers.")
                return

            # build quick lookup to avoid repeated linear searches
            entry_map = {(e["layer_id"], e["datatype"]): e for e in entries}

            # Clear existing objects
            for obj in list(doc.Objects):
                doc.removeObject(obj.Name)

            use_klayout_colors = match_klayout
            highlight_bondable = bool(options.get("highlight_bondable", True))

            for layer in selected_layers:
                lid = layer.get("layer_id", 0)
                dt = layer.get("datatype", 0)
                lname = layer.get("name", "Unnamed")

                map_entry = self.ihp_map.get((lid, dt))
                types = map_entry["edi_types"] if map_entry else set()

                # decide colors
                if use_klayout_colors:
                    shape_rgb = hex_to_rgb(layer.get("fill-color", "#FFFFFF"))
                    line_rgb  = hex_to_rgb(layer.get("frame-color", "#000000"))
                    tr = 0
                    if highlight_bondable and _is_bondable(types):
                        shape_rgb = (0.90, 0.75, 0.20)
                        line_rgb  = (0.25, 0.20, 0.10)
                        tr = 0
                else:
                    _, shape_rgb, line_rgb, tr = style_for_material(
                        map_entry["edi_name"] if map_entry else "",
                        types
                    )
                    if not highlight_bondable and _is_bondable(types):
                        # neutralize highlight
                        shape_rgb = hex_to_rgb(layer.get("fill-color", "#FFFFFF"))
                        line_rgb  = hex_to_rgb(layer.get("frame-color", "#000000"))
                        tr = 0

                entry = entry_map.get((lid, dt))
                if not entry:
                    continue
                obj = doc.addObject("Part::Feature", f"Layer_{lname}_{lid}_{dt}")
                obj.Shape = entry["shape"]
                obj.ViewObject.ShapeColor = shape_rgb
                obj.ViewObject.LineColor = line_rgb
                obj.ViewObject.Transparency = tr
                layer_objects.setdefault(lid, []).append(obj)

            try:
                doc.commitTransaction()
            except Exception:
                pass

            doc.recompute()
            self.update_properties(selected_layers, mymodule.parse_lyp(self.lyp_path)[1], layer_objects)
            FreeCADGui.activeDocument().activeView().viewIsometric()
            FreeCADGui.SendMsgToActiveView("ViewFit")
            QtWidgets.QMessageBox.information(None, "Success", "Layer selection updated successfully.")


# ---------------------------
# Layer selection dialog
# ---------------------------
class LayerSelector(QtWidgets.QDialog):
    """
    Layer selection dialog with quick actions and options:
      - 'Import all layers'
      - 'Match KLayout view (no filters, use LYP colors)'
      - 'Highlight bondable layers (gold)'
      - Select All / Clear / Invert buttons
      - Ctrl+A shortcut to select all
    """
    def __init__(self, layers, selected_layers=None, parent=None, options=None):
        super(LayerSelector, self).__init__(parent)
        self.setWindowTitle("Select Layers")
        self.layers = layers
        self.selected_layers = []
        self.selected_layers_prev = selected_layers or []
        self.options = dict(options or {"match_klayout": False, "highlight_bondable": True})

        main = QtWidgets.QVBoxLayout(self)

        # Global options
        opt_top = QtWidgets.QVBoxLayout()
        self.chk_match = QtWidgets.QCheckBox("Match KLayout view (no filters, use LYP colors)")
        self.chk_match.setChecked(bool(self.options.get("match_klayout", False)))
        self.chk_hl = QtWidgets.QCheckBox("Highlight bondable layers (gold)")
        self.chk_hl.setChecked(bool(self.options.get("highlight_bondable", True)))
        opt_top.addWidget(self.chk_match)
        opt_top.addWidget(self.chk_hl)
        main.addLayout(opt_top)

        # Quick select row
        opt_row = QtWidgets.QHBoxLayout()
        self.chk_all = QtWidgets.QCheckBox("Import all layers")
        self.chk_all.toggled.connect(self._toggle_all_mode)
        opt_row.addWidget(self.chk_all)
        opt_row.addStretch(1)
        self.btn_sel_all = QtWidgets.QPushButton("Select All")
        self.btn_sel_all.clicked.connect(self._select_all)
        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_clear.clicked.connect(self._clear_all)
        self.btn_invert = QtWidgets.QPushButton("Invert")
        self.btn_invert.clicked.connect(self._invert)
        for b in (self.btn_sel_all, self.btn_clear, self.btn_invert):
            opt_row.addWidget(b)
        main.addLayout(opt_row)

        # Layer list
        self.layer_list = QtWidgets.QListWidget()
        self.layer_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+A"), self.layer_list, activated=self._select_all)

        for layer in self.layers:
            layer_name = layer.get("name", "Unknown Layer")
            layer_id = layer.get("layer_id", 0)
            datatype = layer.get("datatype", 0)
            item = QtWidgets.QListWidgetItem(f"{layer_name} ({layer_id}/{datatype})")
            item.setData(QtCore.Qt.UserRole, layer)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
            item.setCheckState(QtCore.Qt.Checked if layer in self.selected_layers_prev else QtCore.Qt.Unchecked)
            self.layer_list.addItem(item)
        main.addWidget(self.layer_list)

        # Buttons
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main.addWidget(button_box)

        self.setLayout(main)
        self._toggle_all_mode(False)

    # --- quick actions ---
    def _toggle_all_mode(self, enabled):
        self.layer_list.setDisabled(enabled)
        self.btn_sel_all.setDisabled(enabled)
        self.btn_clear.setDisabled(enabled)
        self.btn_invert.setDisabled(enabled)
        if enabled:
            for i in range(self.layer_list.count()):
                self.layer_list.item(i).setCheckState(QtCore.Qt.Checked)

    def _select_all(self):
        for i in range(self.layer_list.count()):
            self.layer_list.item(i).setCheckState(QtCore.Qt.Checked)

    def _clear_all(self):
        for i in range(self.layer_list.count()):
            self.layer_list.item(i).setCheckState(QtCore.Qt.Unchecked)

    def _invert(self):
        for i in range(self.layer_list.count()):
            it = self.layer_list.item(i)
            it.setCheckState(QtCore.Qt.Unchecked if it.checkState() == QtCore.Qt.Checked else QtCore.Qt.Checked)

    # --- accept ---
    def accept(self):
        # options
        self.options["match_klayout"] = self.chk_match.isChecked()
        self.options["highlight_bondable"] = self.chk_hl.isChecked()

        if self.chk_all.isChecked():
            self.selected_layers = list(self.layers)
        else:
            self.selected_layers = []
            for i in range(self.layer_list.count()):
                item = self.layer_list.item(i)
                if item.checkState() == QtCore.Qt.Checked:
                    self.selected_layers.append(item.data(QtCore.Qt.UserRole))
        if not self.selected_layers:
            QtWidgets.QMessageBox.warning(self, "Warning", "No layers selected.")
            return
        super(LayerSelector, self).accept()


# ----------------------------------------
# Main flow: pick files, preview document
# ----------------------------------------
def _default_map_path():
    """Try to find sg13g2.map next to this module."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, "sg13g2.map")
    return candidate if os.path.exists(candidate) else None


def load_gds_layers():
    """
    Interactively pick GDS + LYP (+ optional MAP), select visible layers present
    in the GDS, create a fast preview document, and return:
        (doc, layer_objects, selected_layers, unique_colors, gds_path, lyp_path, options)
    """
    try:
        gds_path, _ = QtWidgets.QFileDialog.getOpenFileName(None, "Select GDS File", "", "GDS Files (*.gds *.GDS)")
        if not gds_path or not os.path.exists(gds_path):
            QtWidgets.QMessageBox.critical(None, "Error", "GDS file not found or invalid path.")
            return None, None, None, None, None, None, None

        lyp_path, _ = QtWidgets.QFileDialog.getOpenFileName(None, "Select LYP File", "", "LYP Files (*.lyp *.LYP)")
        if not lyp_path or not os.path.exists(lyp_path):
            QtWidgets.QMessageBox.critical(None, "Error", "LYP file not found or invalid path.")
            return None, None, None, None, None, None, None

        # Optional MAP (technology)
        map_path, _ = QtWidgets.QFileDialog.getOpenFileName(None, "Select IHP MAP (optional)", "", "MAP Files (*.map *.MAP)")
        if not map_path:
            map_path = _default_map_path()

        ihp_map = mymodule.parse_ihp_map(map_path) if map_path else {}

        layers_with_colors = mymodule.parse_lyp(lyp_path)
        if not layers_with_colors:
            QtWidgets.QMessageBox.critical(None, "Error", "No layers found in the LYP file.")
            return None, None, None, None, None, None, None

        layers, unique_colors = layers_with_colors
        gds_layers = mymodule.get_gds_layer(gds_path)
        if not gds_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No layers found in the GDS file.")
            return None, None, None, None, None, None, None

        filtered_layers = [layer for layer in layers if (layer.get("layer_id", 0), layer.get("datatype", 0)) in gds_layers]
        if not filtered_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No matching layers found between LYP and GDS files.")
            return None, None, None, None, None, None, None

        property_panel = PropertyPanel(FreeCADGui.getMainWindow())
        property_panel.set_map(ihp_map, map_path)
        FreeCADGui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, property_panel)
        property_panel.gds_path = gds_path
        property_panel.lyp_path = lyp_path
        property_panel.filtered_layers = filtered_layers
        property_panel.update_properties([], unique_colors, {})

        dialog = LayerSelector(filtered_layers, options=property_panel.options)
        if dialog.exec_():
            selected_layers = dialog.selected_layers
            options = dialog.options
            if not selected_layers:
                QtWidgets.QMessageBox.warning(None, "Warning", "No layers selected.")
                return None, None, None, None, None, None, None

            # save options to panel (needed for modify action)
            property_panel.options = dict(options)

            # params derived from options
            match_klayout = bool(options.get("match_klayout", False))
            skip_fill = not match_klayout
            min_area = 0.0 if match_klayout else 0.0004
            decimate = 0.0 if match_klayout else 0.002
            use_klayout_colors = match_klayout
            highlight_bondable = bool(options.get("highlight_bondable", True))

            # Fast preview document (2D wires + compounds)
            doc = FreeCAD.newDocument("GDSII_Document")
            try:
                doc.openTransaction("Fast Preview Import")
            except Exception:
                pass

            entries = mymodule.load_gds_fast(
                gds_path,
                selected_layers,
                transform=None,
                preview_2d=True,
                compound_per_layer=True,
                min_area_mm2=min_area,
                decimate_tol_mm=decimate,
                skip_fill_datatype=skip_fill
            )
            if not entries:
                QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found for the selected layers.")
                return None, None, None, None, None, None, None

            entry_map = {(e["layer_id"], e["datatype"]): e for e in entries}

            layer_objects = {}
            for layer in selected_layers:
                lid = layer.get("layer_id", 0)
                dt = layer.get("datatype", 0)
                lname = layer.get("name", "Unnamed")

                map_entry = ihp_map.get((lid, dt))
                types = map_entry["edi_types"] if map_entry else set()

                # decide colors
                if use_klayout_colors:
                    shape_rgb = hex_to_rgb(layer.get("fill-color", "#FFFFFF"))
                    line_rgb  = hex_to_rgb(layer.get("frame-color", "#000000"))
                    tr = 0
                    if highlight_bondable and _is_bondable(types):
                        shape_rgb = (0.90, 0.75, 0.20)
                        line_rgb  = (0.25, 0.20, 0.10)
                        tr = 0
                else:
                    _, shape_rgb, line_rgb, tr = style_for_material(
                        map_entry["edi_name"] if map_entry else "",
                        types
                    )
                    if not highlight_bondable and _is_bondable(types):
                        # neutralize highlight to LYP look for this layer
                        shape_rgb = hex_to_rgb(layer.get("fill-color", "#FFFFFF"))
                        line_rgb  = hex_to_rgb(layer.get("frame-color", "#000000"))
                        tr = 0

                entry = entry_map.get((lid, dt))
                if not entry:
                    continue

                obj = doc.addObject("Part::Feature", f"Layer_{lname}_{lid}_{dt}")
                obj.Shape = entry["shape"]
                obj.ViewObject.ShapeColor = shape_rgb
                obj.ViewObject.LineColor = line_rgb
                obj.ViewObject.Transparency = tr
                layer_objects.setdefault(lid, []).append(obj)

            try:
                doc.commitTransaction()
            except Exception:
                pass

            doc.recompute()

            property_panel.update_properties(selected_layers, unique_colors, layer_objects)
            FreeCADGui.activeDocument().activeView().viewIsometric()
            FreeCADGui.SendMsgToActiveView("ViewFit")
            # NOTE: return options as 7th element
            return doc, layer_objects, selected_layers, unique_colors, gds_path, lyp_path, options

        else:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Layer selection cancelled.")
            return None, None, None, None, None, None, None

    except Exception as e:
        FreeCAD.Console.PrintError(f"An error in GDSCommand: {str(e)}\n")
        QtWidgets.QMessageBox.critical(None, "Error", f"Failed to process files: {str(e)}")
        return None, None, None, None, None, None, None


# --------------------------
# Command registration
# --------------------------
class GDSCommand:
    def GetResources(self):
        return {
            'MenuText': 'Load GDSII',
            'ToolTip': 'Load a GDSII file fast, show technology info; KLayout-view option included',
            'Pixmap': ''
        }

    def Activated(self):
        out = load_gds_layers()
        if out and out[0]:
            QtWidgets.QMessageBox.information(None, "Success", "GDSII file loaded (preview).")

    def IsActive(self):
        return True


import FreeCADGui
FreeCADGui.addCommand('GDSCommand', GDSCommand())
