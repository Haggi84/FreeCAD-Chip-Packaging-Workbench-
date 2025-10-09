from PySide2 import QtWidgets, QtCore, QtGui
import FreeCAD, FreeCADGui
import mymodule
from PropertyPanel import PropertyPanel
from LayeronLeadframe import configuration


# ---------------------------
# Layer selection dialog
# ---------------------------
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


        # Global options
        opt_top = QtWidgets.QVBoxLayout()
        self.check_match = QtWidgets.QCheckBox("Match KLayout view (no filters, use LYP colors)")
        self.check_match.setChecked(bool(self.options.get("match_klayout", True)))
        self.check_hl = QtWidgets.QCheckBox("Highlight bondable layers (gold)")
        self.check_hl.setChecked(bool(self.options.get("highlight_bondable", True)))
        opt_top.addWidget(self.check_match)
        opt_top.addWidget(self.check_hl)
        layout.addLayout(opt_top)

        # Add selection control buttons
        opt_row = QtWidgets.QHBoxLayout()
        self.check_all_button = QtWidgets.QCheckBox("Import All Layers")
        self.check_all_button.toggled.connect(self.toggle_all_mode)
        opt_row.addWidget(self.check_all_button)
        opt_row.addStretch(1)

        self.select_all_button = QtWidgets.QPushButton("Select All Layers")
        self.select_all_button.clicked.connect(self.select_all_layers)

        self.clear_all_button = QtWidgets.QPushButton("Clear All Layers")
        self.clear_all_button.clicked.connect(self.clear_all_layers)

        self.invert_button = QtWidgets.QPushButton("Invert")
        self.invert_button.clicked.connect(self.invert_layer_selection)

        for b in (self.check_all_button, self.select_all_button, self.clear_all_button, self.invert_button):
            opt_row.addWidget(b)

        layout.addLayout(opt_row)

        self.layer_list = QtWidgets.QListWidget()
        self.layer_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)

        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+A"), self.layer_list, activated=self.select_all_layers)

        for layer in self.layers:
            layer_name = layer.get("name", "Unknown Layer")
            layer_id = layer.get("layer_id", 0)
            datatype = layer.get("datatype", 0)
            item = QtWidgets.QListWidgetItem(f"{layer_name} ({layer_id}/{datatype})")
            item.setData(QtCore.Qt.UserRole, layer)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            item.setCheckState(QtCore.Qt.Checked if layer in self.selected_layers_prev else QtCore.Qt.Unchecked)
            self.layer_list.addItem(item)
        layout.addWidget(self.layer_list)

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        self.setLayout(layout)
        self.toggle_all_mode(False)  # Start with 'Import All Layers' unchecked

    #--- Toggle All Mode ---
    def toggle_all_mode(self, enabled):
        self.layer_list.setDisabled(enabled)
        self.select_all_button.setDisabled(enabled)
        self.clear_all_button.setDisabled(enabled)
        self.invert_button.setDisabled(enabled)
        if enabled:
            for i in range(self.layer_list.count()):
                self.layer_list.item(i).setCheckState(QtCore.Qt.Checked)

    #--- Select All Layers ---
    def select_all_layers(self):
        for i in range(self.layer_list.count()):
            self.layer_list.item(i).setCheckState(QtCore.Qt.Checked)

    #--- Clear All Layers ---
    def clear_all_layers(self):
        for i in range(self.layer_list.count()):
            self.layer_list.item(i).setCheckState(QtCore.Qt.Unchecked)
    
    #--- Invert Layer Selection ---
    def invert_layer_selection(self):
        for i in range(self.layer_list.count()):
            item = self.layer_list.item(i)
            item.setCheckState(QtCore.Qt.Unchecked if item.checkState() == QtCore.Qt.Checked else QtCore.Qt.Checked)

    # --- accept ---
    def accept(self):
        # options
        self.options["match_klayout"] = self.check_match.isChecked()
        self.options["highlight_bondable"] = self.check_hl.isChecked()

        if self.check_all_button.isChecked():
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


# --------------------------------------
# Leadframe Configuration
# --------------------------------------

class LeadframeConfigurator(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(LeadframeConfigurator, self).__init__(parent)
        self.setWindowTitle("Leadframe Configuration")
        self.setMinimumWidth(400)

        # Layout
        layout = QtWidgets.QFormLayout()

        # Leadframe Type Dropdown
        self.frame_type = QtWidgets.QComboBox()
        self.frame_type.addItems(["QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)", "BGA (Ball Grid Array)"])
        layout.addRow("Leadframe Type:", self.frame_type)

        # Frame Dimensions
        self.frame_length = QtWidgets.QDoubleSpinBox()
        self.frame_length.setRange(1.0, 1000.0)
        self.frame_length.setSingleStep(0.5)
        self.frame_length.setSuffix(" mm")
        self.frame_length.setValue(5.0)
        layout.addRow("Length:", self.frame_length)

        self.frame_width = QtWidgets.QDoubleSpinBox()
        self.frame_width.setRange(1.0, 1000.0)
        self.frame_width.setSingleStep(0.5)
        self.frame_width.setSuffix(" mm")
        self.frame_width.setValue(5.0)
        layout.addRow("Width:", self.frame_width)

        self.frame_thickness = QtWidgets.QDoubleSpinBox()
        self.frame_thickness.setRange(0.05, 5.0)
        self.frame_thickness.setSingleStep(0.05)
        self.frame_thickness.setSuffix(" mm")
        self.frame_thickness.setValue(0.2)
        layout.addRow("Thickness:", self.frame_thickness)

        # (QFN/QFP) Parameters
        self.qfn_qfp = QtWidgets.QWidget()
        qfn_qfp_layout = QtWidgets.QFormLayout()
        
        # Lead Counts
        # Left side
        self.left_lead_count = QtWidgets.QSpinBox()
        self.left_lead_count.setRange(0, 80)
        self.left_lead_count.setValue(4)
        qfn_qfp_layout.addRow("Left Lead Count:", self.left_lead_count)

        # Right side
        self.right_lead_count = QtWidgets.QSpinBox()
        self.right_lead_count.setRange(0, 80)
        self.right_lead_count.setValue(4)
        qfn_qfp_layout.addRow("Right Lead Count:", self.right_lead_count)

        # Top side
        self.top_lead_count = QtWidgets.QSpinBox()
        self.top_lead_count.setRange(0, 80)
        self.top_lead_count.setValue(4)
        qfn_qfp_layout.addRow("Top Lead Count:", self.top_lead_count)

        # Bottom side
        self.bottom_lead_count = QtWidgets.QSpinBox()
        self.bottom_lead_count.setRange(0, 80)
        self.bottom_lead_count.setValue(4)
        qfn_qfp_layout.addRow("Bottom Lead Count:", self.bottom_lead_count)

        # Lead Parameters (QFN/QFP)
        self.lead_width = QtWidgets.QDoubleSpinBox()
        self.lead_width.setRange(0.1, 5.0)
        self.lead_width.setSingleStep(0.1)
        self.lead_width.setSuffix(" mm")
        self.lead_width.setValue(0.4)
        qfn_qfp_layout.addRow("Lead Width:", self.lead_width)

        self.lead_pitch = QtWidgets.QDoubleSpinBox()
        self.lead_pitch.setRange(0.1, 10.0)
        self.lead_pitch.setSingleStep(0.1)
        self.lead_pitch.setSuffix(" mm")
        self.lead_pitch.setValue(1.0)
        qfn_qfp_layout.addRow("Lead Pitch:", self.lead_pitch)

        self.lead_length = QtWidgets.QDoubleSpinBox()
        self.lead_length.setRange(0.5, 10.0)
        self.lead_length.setSingleStep(0.5)
        self.lead_length.setSuffix(" mm")
        self.lead_length.setValue(1.0)
        qfn_qfp_layout.addRow("Lead Length:", self.lead_length)

        # QGN Pad Thickness
        self.qfn_pad_thickness = QtWidgets.QDoubleSpinBox()
        self.qfn_pad_thickness.setRange(0.01, 2.0)
        self.qfn_pad_thickness.setSingleStep(0.01)
        self.qfn_pad_thickness.setSuffix(" mm")
        self.qfn_pad_thickness.setValue(0.05)
        qfn_qfp_layout.addRow("QFN Pad Thickness:", self.qfn_pad_thickness)

        self.qfn_qfp.setLayout(qfn_qfp_layout)
        layout.addRow(self.qfn_qfp)

        # BGA Parameters
        self.bga = QtWidgets.QWidget()
        bga_layout = QtWidgets.QFormLayout()

        self.bga_ball_diameter = QtWidgets.QDoubleSpinBox()
        self.bga_ball_diameter.setRange(0.1, 2.0)
        self.bga_ball_diameter.setSingleStep(0.1)
        self.bga_ball_diameter.setSuffix(" mm")
        self.bga_ball_diameter.setValue(0.5)
        bga_layout.addRow("BGA Ball Diameter:", self.bga_ball_diameter)

        self.bga_ball_pitch = QtWidgets.QDoubleSpinBox()
        self.bga_ball_pitch.setRange(0.1, 5.0)
        self.bga_ball_pitch.setSingleStep(0.1)
        self.bga_ball_pitch.setSuffix(" mm")
        self.bga_ball_pitch.setValue(1.0)
        bga_layout.addRow("BGA Ball Pitch:", self.bga_ball_pitch)

        self.bga.setLayout(bga_layout)
        layout.addRow(self.bga)

        # Material Selection
        self.material_combo = QtWidgets.QComboBox()
        self.material_combo.addItems(["Copper", "Alloy 42", "Silver"])
        layout.addRow("Material:", self.material_combo)

        # OK/Cancel Buttons
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)

        # Connect frame type change to update visibility
        self.frame_type.currentIndexChanged.connect(self.update_parameter_visibility)

        # Initial visibility setup
        self.update_parameter_visibility()

    def update_parameter_visibility(self):
        """
        Update the visibility of parameters based on the selected frame type.
        """
        frame_type = self.frame_type.currentText()
        self.qfn_qfp.setVisible(frame_type in ["QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)"])
        self.bga.setVisible(frame_type == "BGA (Ball Grid Array)")

    def get_config(self):
        """
        Return only relevant configuration parameters based on the selected frame type.
        """
        frame_type = self.frame_type.currentText()
        config = {
            "frame_type": frame_type,
            "frame_length": self.frame_length.value(),
            "frame_width": self.frame_width.value(),
            "frame_thickness": self.frame_thickness.value(),
            "material": self.material_combo.currentText()
        }
        if frame_type in ["QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)"]:
            config.update({
                "left_lead_count": self.left_lead_count.value(),
                "right_lead_count": self.right_lead_count.value(),
                "top_lead_count": self.top_lead_count.value(),
                "bottom_lead_count": self.bottom_lead_count.value(),
                "lead_width": self.lead_width.value(),
                "lead_pitch": self.lead_pitch.value(),
                "lead_length": self.lead_length.value(),
                "qfn_pad_thickness": self.qfn_pad_thickness.value()
            })
        elif frame_type == "BGA (Ball Grid Array)":
            config.update({
                "bga_ball_diameter": self.bga_ball_diameter.value(),
                "bga_ball_pitch": self.bga_ball_pitch.value()
            })
        return config
    

# -----------------------------------
# Housing Configuration
# -----------------------------------

class TransparentHousingConfigurator(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(TransparentHousingConfigurator, self).__init__(parent)
        self.setWindowTitle("Housing Configuration")
        self.setMinimumWidth(400)

        # Layout
        layout = QtWidgets.QFormLayout()

        # Leadframe Configuration Button
        self.leadframe_config_button = QtWidgets.QPushButton("Configure Leadframe")
        self.leadframe_config_button.clicked.connect(self.open_leadframe_config)
        layout.addRow("Leadframe:", self.leadframe_config_button)

        # Display Leadframe Parameters (read-only)
        self.frame_type_label = QtWidgets.QLabel("Not configured")
        layout.addRow("Leadframe Type:", self.frame_type_label)

        self.leadframe_length_label = QtWidgets.QLabel("Not configured")
        layout.addRow("Leadframe Length:", self.leadframe_length_label)

        self.leadframe_width_label = QtWidgets.QLabel("Not configured")
        layout.addRow("Leadframe Width:", self.leadframe_width_label)

        self.leadframe_thickness_label = QtWidgets.QLabel("Not configured")
        layout.addRow("Leadframe Thickness:", self.leadframe_thickness_label)

        # Housing Parameters
        self.wall_thickness = QtWidgets.QDoubleSpinBox()
        self.wall_thickness.setRange(0.5, 5.0)
        self.wall_thickness.setSingleStep(0.1)
        self.wall_thickness.setSuffix(" mm")
        self.wall_thickness.setValue(0.5)
        layout.addRow("Wall Thickness:", self.wall_thickness)

        self.clearance = QtWidgets.QDoubleSpinBox()
        self.clearance.setRange(0.1, 2.0)
        self.clearance.setSingleStep(0.05)
        self.clearance.setSuffix(" mm")
        self.clearance.setValue(0.2)
        layout.addRow("Clearance:", self.clearance)

        self.housing_height = QtWidgets.QDoubleSpinBox()
        self.housing_height.setRange(0.5, 20.0)
        self.housing_height.setSingleStep(0.5)
        self.housing_height.setSuffix(" mm")
        self.housing_height.setValue(0.5)
        layout.addRow("Housing Height:", self.housing_height)

        self.include_lid = QtWidgets.QCheckBox("Include Transparent Lid")
        self.include_lid.setChecked(True)
        layout.addRow(self.include_lid)

        self.lid_thickness = QtWidgets.QDoubleSpinBox()
        self.lid_thickness.setRange(0.2, 3.0)
        self.lid_thickness.setSingleStep(0.1)
        self.lid_thickness.setSuffix(" mm")
        self.lid_thickness.setValue(0.5)
        layout.addRow("Lid Thickness:", self.lid_thickness)

        # Material Selection (transparent materials)
        self.material_combo = QtWidgets.QComboBox()
        self.material_combo.addItems(["Polycarbonate", "Acrylic", "Transparent ABS"])
        layout.addRow("Material:", self.material_combo)

        # Transparency Slider (0 = fully transparent, 100 = fully opaque)
        self.transparency = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.transparency.setRange(0, 100)
        self.transparency.setValue(50)  # Default: semi-transparent
        self.transparency.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self.transparency.setTickInterval(10)
        layout.addRow("Transparency (0=Clear, 100=Opaque):", self.transparency)

        # OK/Cancel Buttons
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)
        self.leadframe_config = None

    def open_leadframe_config(self):
        dialog = LeadframeConfigurator()
        if dialog.exec_():
            self.leadframe_config = dialog.get_config()
            self.update_leadframe_display()

    def update_leadframe_display(self):
        if self.leadframe_config:
            self.frame_type_label.setText(self.leadframe_config["frame_type"])
            self.leadframe_length_label.setText(f"{self.leadframe_config['frame_length']} mm")
            self.leadframe_width_label.setText(f"{self.leadframe_config['frame_width']} mm")
            self.leadframe_thickness_label.setText(f"{self.leadframe_config['frame_thickness']} mm")

    def get_config(self):
        if not self.leadframe_config:
            FreeCAD.Console.PrintError("Leadframe configuration not set.")
        config = self.leadframe_config.copy()
        config.update({
            "wall_thickness": self.wall_thickness.value(),
            "clearance": self.clearance.value(),
            "housing_height": self.housing_height.value(),
            "include_lid": self.include_lid.isChecked(),
            "lid_thickness": self.lid_thickness.value(),
            "material": self.material_combo.currentText(),
            "transparency": self.transparency.value() / 100.0  # Convert to 0-1 scale
        })
        return config
    

# ----------------------------------
# Layer on Leadframe Configuration
# ----------------------------------

class LayeronLeadframeConfigurator(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transform Options")
        self.setMinimumWidth(320)

        form = QtWidgets.QFormLayout()

        self.auto_fit = QtWidgets.QCheckBox("Auto-fit die into frame opening (keep aspect)")
        self.auto_fit.setChecked(True)
        form.addRow(self.auto_fit)

        self.margin_pct = QtWidgets.QDoubleSpinBox()
        self.margin_pct.setRange(0.0, 40.0)
        self.margin_pct.setSingleStep(1.0)
        self.margin_pct.setSuffix(" %")
        self.margin_pct.setValue(10.0)
        form.addRow("Fit Margin:", self.margin_pct)

        self.rot_deg = QtWidgets.QDoubleSpinBox()
        self.rot_deg.setRange(-360.0, 360.0)
        self.rot_deg.setSingleStep(1.0)
        self.rot_deg.setSuffix(" °")
        self.rot_deg.setValue(0.0)
        form.addRow("Rotation:", self.rot_deg)

        self.mirror_y = QtWidgets.QCheckBox("Mirror in Y (flip top/bottom)")
        self.mirror_y.setChecked(False)
        form.addRow(self.mirror_y)

        self.tx = QtWidgets.QDoubleSpinBox()
        self.tx.setRange(-10000.0, 10000.0)
        self.tx.setDecimals(4)
        self.tx.setSingleStep(0.1)
        self.tx.setSuffix(" mm")
        self.tx.setValue(0.0)
        form.addRow("Offset X:", self.tx)

        self.ty = QtWidgets.QDoubleSpinBox()
        self.ty.setRange(-10000.0, 10000.0)
        self.ty.setDecimals(4)
        self.ty.setSingleStep(0.1)
        self.ty.setSuffix(" mm")
        self.ty.setValue(0.0)
        form.addRow("Offset Y:", self.ty)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def get_opts(self):
        return {
            "auto_fit": self.auto_fit.isChecked(),
            "margin_pct": self.margin_pct.value(),
            "rot_deg": self.rot_deg.value(),
            "mirror_y": self.mirror_y.isChecked(),
            "tx": self.tx.value(),
            "ty": self.ty.value()
        }
    
# ---------------------------
# Wire Bonding Configuration
# ---------------------------

class WirebondConfigurator(QtWidgets.QDialog):
    """Dialog to configure wire bonding parameters."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wire Bonding Configuration")
        layout = QtWidgets.QVBoxLayout()

        # Wire parameters
        self.loop_height = QtWidgets.QDoubleSpinBox()
        self.loop_height.setRange(0.1, 5.0)
        self.loop_height.setValue(0.5)
        self.loop_height.setSuffix(" mm")
        layout.addWidget(QtWidgets.QLabel("Loop Height:"))
        layout.addWidget(self.loop_height)

        self.diameter = QtWidgets.QDoubleSpinBox()
        self.diameter.setRange(0.01, 0.1)
        self.diameter.setValue(0.025)
        self.diameter.setSuffix(" mm")
        layout.addWidget(QtWidgets.QLabel("Wire Diameter:"))
        layout.addWidget(self.diameter)

        # Design rule constraints
        self.min_wire_spacing = QtWidgets.QDoubleSpinBox()
        self.min_wire_spacing.setRange(0.05, 1.0)
        self.min_wire_spacing.setValue(0.1)
        self.min_wire_spacing.setSuffix(" mm")
        layout.addWidget(QtWidgets.QLabel("Minimum Wire-to-Wire Spacing:"))
        layout.addWidget(self.min_wire_spacing)

        self.min_wire_length = QtWidgets.QDoubleSpinBox()
        self.min_wire_length.setRange(0.1, 10.0)
        self.min_wire_length.setValue(0.5)
        self.min_wire_length.setSuffix(" mm")
        layout.addWidget(QtWidgets.QLabel("Minimum Wire Length:"))
        layout.addWidget(self.min_wire_length)

        self.max_wire_length = QtWidgets.QDoubleSpinBox()
        self.max_wire_length.setRange(0.5, 20.0)
        self.max_wire_length.setValue(5.0)
        self.max_wire_length.setSuffix(" mm")
        layout.addWidget(QtWidgets.QLabel("Maximum Wire Length:"))
        layout.addWidget(self.max_wire_length)

        self.bond_finger_margin = QtWidgets.QDoubleSpinBox()
        self.bond_finger_margin.setRange(0.01, 0.5)
        self.bond_finger_margin.setValue(0.05)
        self.bond_finger_margin.setSuffix(" mm")
        layout.addWidget(QtWidgets.QLabel("Bond Finger Margin:"))
        layout.addWidget(self.bond_finger_margin)

        # Buttons
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        self.setLayout(layout)

    def get_config(self):
        """Return wire bonding configuration."""
        return {
            "loop_height": self.loop_height.value(),
            "diameter": self.diameter.value(),
            "min_wire_spacing": self.min_wire_spacing.value(),
            "min_wire_length": self.min_wire_length.value(),
            "max_wire_length": self.max_wire_length.value(),
            "bond_finger_margin": self.bond_finger_margin.value()
        }

from FreeCAD import Base
import Part


class ManualWireBonding:
    """Manual 2D wire bonding with controlled object selection."""
    
    def __init__(self):
        self.selected_objects = []  # Store [die_pad, bond_finger] pairs
        self.current_selection = None
        self.doc = FreeCAD.activeDocument()
        self.config = None
        self.is_active = False
        self.selection_mode = "waiting"  # "waiting", "select_die", "select_bond"
        
    def start_bonding_session(self, config):
        """Start manual wire bonding session."""
        self.config = config
        self.selected_objects = []
        self.is_active = True
        self.selection_mode = "select_die"
        self.doc = FreeCAD.activeDocument()
        
        if not self.doc:
            self.doc = FreeCAD.newDocument("WireBonding")
        
        FreeCAD.Console.PrintMessage("=== MANUAL 2D WIRE BONDING STARTED ===\n")
        FreeCAD.Console.PrintMessage("STEP 1: Click on a DIE PAD (starting point)\n")
        FreeCAD.Console.PrintMessage("STEP 2: Click on a BOND FINGER (ending point)\n") 
        FreeCAD.Console.PrintMessage("• Use 'Finish Wire Bonding' command to complete\n")
        
        # Set selection observer
        FreeCADGui.Selection.addObserver(self)
        
    def addSelection(self, doc, obj, sub, pos):
        """Handle selection events - but only for valid objects."""
        if not self.is_active or not self.config:
            return
            
        try:
            # Get the actual FreeCAD object
            freecad_obj = None
            if isinstance(obj, str):
                freecad_obj = self.doc.getObject(obj)
            else:
                freecad_obj = obj
                
            if not freecad_obj:
                FreeCAD.Console.PrintWarning("No valid object selected\n")
                return
                
            # Get object name for identification
            obj_name = freecad_obj.Name.lower()
            obj_label = freecad_obj.Label.lower() if hasattr(freecad_obj, 'Label') else ""
            
            FreeCAD.Console.PrintMessage(f"Selected: {freecad_obj.Name} | Mode: {self.selection_mode}\n")
            
            # STEP 1: Select DIE PAD
            if self.selection_mode == "select_die":
                # Check if this is a die pad (you can customize these conditions)
                if self.is_die_pad(freecad_obj, obj_name, obj_label):
                    # Get connection point from die pad
                    die_point = self.get_die_pad_connection_point(freecad_obj, sub)
                    
                    self.current_selection = {
                        'die_pad': freecad_obj,
                        'die_point': die_point,
                        'die_sub': sub
                    }
                    
                    FreeCAD.Console.PrintMessage(f"✓ DIE PAD selected: {freecad_obj.Name}\n")
                    FreeCAD.Console.PrintMessage(f"  Connection point: ({die_point.x:.3f}, {die_point.y:.3f})\n")
                    
                    # Create visual feedback
                    self.create_temp_marker(die_point, "die")
                    
                    # Move to next step
                    self.selection_mode = "select_bond"
                    FreeCAD.Console.PrintMessage("STEP 2: Now click on a BOND FINGER\n")
                    
                else:
                    FreeCAD.Console.PrintWarning("Please select a DIE PAD object\n")
                    FreeCAD.Console.PrintMessage("Look for objects named like: die_pad, pad, bond_pad, etc.\n")
            
            # STEP 2: Select BOND FINGER  
            elif self.selection_mode == "select_bond":
                # Check if this is a bond finger (you can customize these conditions)
                if self.is_bond_finger(freecad_obj, obj_name, obj_label):
                    # Get connection point from bond finger
                    bond_point = self.get_bond_finger_connection_point(freecad_obj, sub)
                    
                    # Create the bond wire
                    die_point = self.current_selection['die_point']
                    self.create_2d_bond_wire(die_point, bond_point)
                    
                    # Store the pair
                    self.selected_objects.append({
                        'die_pad': self.current_selection['die_pad'],
                        'bond_finger': freecad_obj,
                        'die_point': die_point,
                        'bond_point': bond_point
                    })
                    
                    FreeCAD.Console.PrintMessage(f"✓ BOND FINGER selected: {freecad_obj.Name}\n")
                    FreeCAD.Console.PrintMessage(f"✓ Bond created between {self.current_selection['die_pad'].Name} → {freecad_obj.Name}\n")
                    
                    # Reset for next bond
                    self.current_selection = None
                    self.selection_mode = "select_die"
                    FreeCAD.Console.PrintMessage("STEP 1: Click on next DIE PAD\n")
                    
                else:
                    FreeCAD.Console.PrintWarning("Please select a BOND FINGER object\n")
                    FreeCAD.Console.PrintMessage("Look for objects named like: bond_finger, finger, lead, etc.\n")
                    
        except Exception as e:
            FreeCAD.Console.PrintError(f"Error in selection: {str(e)}\n")
            import traceback
            FreeCAD.Console.PrintError(traceback.format_exc())
    
    def is_die_pad(self, obj, obj_name, obj_label):
        """Check if object is a die pad (customize these conditions)."""
        # Add your specific conditions for identifying die pads
        die_keywords = ['die', 'pad', 'bond_pad', 'chip_pad', 'ic_pad', 'metal1', 'drawing']
        
        # Check name and label
        for keyword in die_keywords:
            if keyword in obj_name or keyword in obj_label:
                return True
                
        # Check object type or properties
        if hasattr(obj, 'TypeId'):
            if 'Pad' in obj.TypeId or 'Face' in obj.TypeId:
                return True
                
        # If no specific identification, ask user to confirm
        FreeCAD.Console.PrintMessage(f"Object '{obj.Name}' selected as die pad. Is this correct? (Y/N)\n")
        # For now, assume yes - you can add user confirmation later
        return True
    
    def is_bond_finger(self, obj, obj_name, obj_label):
        """Check if object is a bond finger (customize these conditions)."""
        # Add your specific conditions for identifying bond fingers
        finger_keywords = ['finger', 'bond', 'lead', 'terminal', 'pin', 'leadframe']
        
        # Check name and label
        for keyword in finger_keywords:
            if keyword in obj_name or keyword in obj_label:
                return True
                
        # Check object type or properties
        if hasattr(obj, 'TypeId'):
            if 'Lead' in obj.TypeId or 'Terminal' in obj.TypeId:
                return True
                
        # If no specific identification, ask user to confirm
        FreeCAD.Console.PrintMessage(f"Object '{obj.Name}' selected as bond finger. Is this correct? (Y/N)\n")
        # For now, assume yes - you can add user confirmation later
        return True
    
    def get_die_pad_connection_point(self, die_pad, sub):
        """Get the connection point on a die pad."""
        try:
            if sub and "Vertex" in sub:
                # Use selected vertex
                vertex_index = int(sub.replace("Vertex", "")) - 1
                if vertex_index < len(die_pad.Shape.Vertexes):
                    return die_pad.Shape.Vertexes[vertex_index].Point
            elif sub and "Face" in sub:
                # Use face center
                face_index = int(sub.replace("Face", "")) - 1
                if face_index < len(die_pad.Shape.Faces):
                    return die_pad.Shape.Faces[face_index].CenterOfMass
            elif sub and "Edge" in sub:
                # Use edge midpoint
                edge_index = int(sub.replace("Edge", "")) - 1
                if edge_index < len(die_pad.Shape.Edges):
                    curve = die_pad.Shape.Edges[edge_index].Curve
                    if hasattr(curve, 'value'):
                        return curve.value(0.5)
        except:
            pass
            
        # Default: use object center
        return die_pad.Shape.BoundBox.Center
    
    def get_bond_finger_connection_point(self, bond_finger, sub):
        """Get the connection point on a bond finger."""
        try:
            if sub and "Vertex" in sub:
                # Use selected vertex
                vertex_index = int(sub.replace("Vertex", "")) - 1
                if vertex_index < len(bond_finger.Shape.Vertexes):
                    return bond_finger.Shape.Vertexes[vertex_index].Point
            elif sub and "Face" in sub:
                # Use face center
                face_index = int(sub.replace("Face", "")) - 1
                if face_index < len(bond_finger.Shape.Faces):
                    return bond_finger.Shape.Faces[face_index].CenterOfMass
            elif sub and "Edge" in sub:
                # Use edge midpoint
                edge_index = int(sub.replace("Edge", "")) - 1
                if edge_index < len(bond_finger.Shape.Edges):
                    curve = bond_finger.Shape.Edges[edge_index].Curve
                    if hasattr(curve, 'value'):
                        return curve.value(0.5)
        except:
            pass
            
        # Default: use object center
        return bond_finger.Shape.BoundBox.Center
    
    def create_temp_marker(self, position, marker_type):
        """Create temporary visual marker."""
        if not self.doc:
            return
            
        try:
            marker = self.doc.addObject("Part::Sphere", f"TempMarker_{marker_type}")
            marker.Radius = 0.15  # Slightly larger for visibility
            marker.Placement.Base = position
            
            # Color coding
            if marker_type == "die":
                marker.ViewObject.ShapeColor = (0.0, 1.0, 0.0)  # Green for die
            else:
                marker.ViewObject.ShapeColor = (0.0, 0.0, 1.0)  # Blue for bond
                
            marker.ViewObject.Transparency = 30
            self.doc.recompute()
            
            # Auto-remove after 2 seconds
            QtCore.QTimer.singleShot(2000, lambda: self.remove_temp_marker(marker))
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"Could not create temp marker: {e}\n")
    
    def remove_temp_marker(self, marker):
        """Remove temporary marker."""
        try:
            if marker and marker in self.doc.Objects:
                self.doc.removeObject(marker.Name)
                if self.doc:
                    self.doc.recompute()
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"Could not remove temp marker: {e}\n")
    
    def create_2d_bond_wire(self, start_point, end_point):
        """Create a 2D bond wire between die pad and bond finger."""
        if not self.doc:
            return None
            
        try:
            # Convert to 2D (XY plane)
            start_2d = Base.Vector(start_point.x, start_point.y, 0)
            end_2d = Base.Vector(end_point.x, end_point.y, 0)
            
            # Create wire
            if self.config.get('wire_style', 'straight') == 'arc':
                # Create arc wire
                arc_height = self.config.get('arc_height', 2.0)
                mid_point = Base.Vector(
                    (start_2d.x + end_2d.x) / 2,
                    (start_2d.y + end_2d.y) / 2,
                    arc_height
                )
                arc = Part.Arc(start_2d, mid_point, end_2d)
                wire_shape = arc.toShape()
            else:
                # Straight line wire
                wire_shape = Part.makeLine(start_2d, end_2d)
            
            # Create wire object
            bond_count = len(self.selected_objects)
            wire_obj = self.doc.addObject("Part::Feature", f"BondWire_{bond_count+1:03d}")
            wire_obj.Shape = wire_shape
            
            # Styling
            wire_obj.ViewObject.LineWidth = 3
            wire_obj.ViewObject.LineColor = (0.90, 0.75, 0.20)  # Gold
            wire_obj.ViewObject.PointSize = 5
            
            # Add properties
            wire_obj.addProperty("App::PropertyVector", "StartPoint", "Wirebond", "Die pad connection")
            wire_obj.addProperty("App::PropertyVector", "EndPoint", "Wirebond", "Bond finger connection")
            wire_obj.addProperty("App::PropertyLength", "WireLength", "Wirebond", "Wire length")
            wire_obj.addProperty("App::PropertyString", "NetName", "Wirebond", "Net name")
            
            wire_obj.StartPoint = start_point
            wire_obj.EndPoint = end_point
            wire_obj.WireLength = wire_shape.Length
            wire_obj.NetName = f"Net_{bond_count+1:03d}"
            
            self.doc.recompute()
            FreeCAD.Console.PrintMessage(f"✓ Bond wire created: {wire_obj.Name}\n")
            return wire_obj
            
        except Exception as e:
            FreeCAD.Console.PrintError(f"Error creating bond wire: {str(e)}\n")
            return None
    
    def finish_session(self):
        """Finish bonding session."""
        if not self.is_active:
            return 0
            
        self.is_active = False
        try:
            FreeCADGui.Selection.removeObserver(self)
        except:
            pass
        
        FreeCAD.Console.PrintMessage(f"=== WIRE BONDING FINISHED ===\n")
        FreeCAD.Console.PrintMessage(f"• Total bonds created: {len(self.selected_objects)}\n")
        self.generate_report()
        return len(self.selected_objects)
    
    def generate_report(self):
        """Generate bonding report."""
        if not self.selected_objects:
            FreeCAD.Console.PrintMessage("• No bonds were created.\n")
            return
            
        report = "=== WIRE BONDING REPORT ===\n"
        total_length = 0
        
        for i, bond in enumerate(self.selected_objects):
            length = bond['die_point'].distanceTo(bond['bond_point'])
            total_length += length
            report += f"Bond {i+1}: {bond['die_pad'].Name} → {bond['bond_finger'].Name} | Length: {length:.3f} mm\n"
        
        report += f"\nSUMMARY:\n"
        report += f"Total bonds: {len(self.selected_objects)}\n"
        report += f"Total wire length: {total_length:.3f} mm\n"
        report += f"Average length: {total_length/len(self.selected_objects):.3f} mm\n"
        
        FreeCAD.Console.PrintMessage(report)
    
    def cancel_session(self):
        """Cancel bonding session."""
        self.is_active = False
        try:
            FreeCADGui.Selection.removeObserver(self)
        except:
            pass
        self.selected_objects = []
        self.current_selection = None
        self.selection_mode = "waiting"
        FreeCAD.Console.PrintMessage("✗ Wire bonding cancelled.\n")

# Global instance
manual_bonder = ManualWireBonding()


# -------------------------
# Extended Property Panel
# -------------------------

# Extend PropertyPanel to support leadframe modification, wire bonding modification
class ExtendedPropertyPanel(PropertyPanel):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Add leadframe_config and transform_opts
        self.modify_layers_button.setText("Modify Leadframe, Layers or Wire Bonds")
        self.leadframe_config = None
        self.transform_opts = None
        self.wire_bonding_config = None

    def modify_layer_selection(self):
        import LeadframeCommand
        import WirebondCommand

        """Override to allow modifying either layer selection or leadframe configuration."""
        if not self.gds_path or not self.lyp_path:
            QtWidgets.QMessageBox.critical(None, "Error", "Cannot modify: Missing file paths or layer data.")
            return

        # Prompt user to choose what to modify
        choice_dialog = QtWidgets.QDialog(self)
        choice_dialog.setWindowTitle("Modify Selection")
        layout = QtWidgets.QVBoxLayout()
        message = QtWidgets.QLabel("What would you like to modify?")
        layout.addWidget(message)
        layer_button = QtWidgets.QPushButton("Modify Layer Selection")
        leadframe_button = QtWidgets.QPushButton("Modify Leadframe Configuration")
        wire_bonds_button = QtWidgets.QPushButton("Modify Wire Bonds")
        cancel_button = QtWidgets.QPushButton("Cancel")
        layout.addWidget(layer_button)
        layout.addWidget(leadframe_button)
        layout.addWidget(wire_bonds_button)
        layout.addWidget(cancel_button)
        choice_dialog.setLayout(layout)

        modify_type = [None]  # Use list to allow modification in slots

        def set_layer_modification():
            modify_type[0] = "layers"
            choice_dialog.accept()

        def set_leadframe_modification():
            modify_type[0] = "leadframe"
            choice_dialog.accept()

        def set_wire_bonds_modification():
            modify_type[0] = "wire_bonds"
            choice_dialog.accept()

        layer_button.clicked.connect(set_layer_modification)
        leadframe_button.clicked.connect(set_leadframe_modification)
        wire_bonds_button.clicked.connect(set_wire_bonds_modification)
        cancel_button.clicked.connect(choice_dialog.reject)

        if choice_dialog.exec_() != QtWidgets.QDialog.Accepted or not modify_type[0]:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Modification cancelled.")
            return

        doc = FreeCAD.activeDocument()
        if not doc:
            QtWidgets.QMessageBox.critical(None, "Error", "No active document found.")
            return

        try:
            doc.openTransaction("Update Layer or Leadframe")
        except Exception:
            pass

        if modify_type[0] == "layers":
            # Modify layer selection (similar to original PropertyPanel logic)
            dialog = LayerSelector(self.filtered_layers, self.selected_layers, options=self.options)
            if dialog.exec_():
                selected_layers = dialog.selected_layers
                options = dialog.options
                if not selected_layers:
                    QtWidgets.QMessageBox.warning(None, "Warning", "No layers selected.")
                    return

                self.options = dict(options)

                # Clear existing objects
                for obj in doc.Objects:
                    try:
                        doc.removeObject(obj.Name)
                    except Exception as e:
                        FreeCAD.Console.PrintError(f"Error removing object {obj.Name}: {str(e)}\n")

                result = configuration(doc, self.gds_path, selected_layers, self.options, self.ihp_map, self.leadframe_config, self.transform_opts)
                if not result:
                    QtWidgets.QMessageBox.warning(None, "Warning", "Failed to create layer on leadframe.")
                    return

                doc, layer_objects = result

                # Update PropertyPanel
                self.selected_layers = selected_layers
                self.update_properties(self.selected_layers, mymodule.parse_lyp(self.lyp_path)[1], layer_objects)

        elif modify_type[0] == "leadframe":
            # Modify leadframe configuration
            new_config = LeadframeCommand.configure_leadframe()
            if not new_config:
                QtWidgets.QMessageBox.information(None, "Cancelled", "Leadframe configuration cancelled.")
                return

            # Update leadframe config
            self.leadframe_config = new_config

            # Clear existing objects
            for obj in doc.Objects:
                try:
                    doc.removeObject(obj.Name)
                except Exception as e:
                    FreeCAD.Console.PrintError(f"Error removing object {obj.Name}: {str(e)}\n")

            result = configuration(doc, self.gds_path, self.selected_layers, self.options, self.ihp_map, self.leadframe_config, self.transform_opts)
            if not result:
                QtWidgets.QMessageBox.warning(None, "Warning", "Failed to create layer on leadframe.")
                return

            doc, layer_objects = result

            # Update PropertyPanel
            self.update_properties(self.selected_layers, mymodule.parse_lyp(self.lyp_path)[1], layer_objects)

        elif modify_type[0] == "wire_bonds":
            dialog = WirebondConfigurator()
            if dialog.exec_():
                new_config = dialog.get_config()
                self.wire_bond_config = new_config
                for obj in doc.Objects:
                    if obj.Label.startswith("BondWire"):
                        doc.removeObject(obj.Name)
                result = WirebondCommand.create_wire_bonds(new_config, self.layer_objects, self.leadframe_config)
                if not result:
                    QtWidgets.QMessageBox.warning(None, "Warning", "Failed to create wire bonds.")
                    return
                doc, wires = result
                QtWidgets.QMessageBox.information(None, "Success", "Wire bonds updated successfully.")
            else:
                QtWidgets.QMessageBox.information(None, "Cancelled", "Wire bonds configuration cancelled.")
                return

        try:
            doc.commitTransaction()
        except Exception:
            pass

        doc.recompute()
        FreeCADGui.activeDocument().activeView().viewIsometric()
        FreeCADGui.SendMsgToActiveView("ViewFit")
        QtWidgets.QMessageBox.information(None, "Success", f"{'Layer selection' if modify_type[0] == 'layers' else 'Leadframe configuration' if modify_type[0] == 'leadframe' else 'Wire bonds'} updated successfully.")