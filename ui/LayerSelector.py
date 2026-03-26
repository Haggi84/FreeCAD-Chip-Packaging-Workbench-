"""
Layer Selection Dialog
Provides UI for selecting GDS layers with quick action buttons
"""

from PySide2 import QtWidgets, QtCore, QtGui

class LayerSelector(QtWidgets.QDialog):
    """
    Layer selection dialog with quick actions:
      - 'Import all layers' checkbox
      - Select All / Clear / Invert buttons
      - Ctrl+A shortcut to select all
    """
    def __init__(self, layers, selected_layers=None, parent=None, options=None, ihp_map=None):
        super(LayerSelector, self).__init__(parent)
        self.setWindowTitle("Select Layers")
        self.layers = layers
        self.ihp_map = ihp_map or {}
        self.selected_layers = []
        self.selected_layers_prev = selected_layers or []
        self.options = dict(options or {
            "match_klayout": True,
            "highlight_bondable": True,
            "extrude_3d": False,
            "auto_pin_contacts": False,
            "contacts_only_3d": False,
        })

        layout = QtWidgets.QVBoxLayout(self)


        # Global options
        opt_top = QtWidgets.QVBoxLayout()
        self.check_match = QtWidgets.QCheckBox("Match KLayout view (no filters, use LYP colors)")
        self.check_match.setChecked(bool(self.options.get("match_klayout", True)))
        self.check_hl = QtWidgets.QCheckBox("Highlight bondable layers (gold)")
        self.check_hl.setChecked(bool(self.options.get("highlight_bondable", True)))
        self.check_3d = QtWidgets.QCheckBox("Extrude layers to 3D volumes (uses PDK thickness table)")
        self.check_3d.setChecked(bool(self.options.get("extrude_3d", False)))
        self.check_auto_pin = QtWidgets.QCheckBox(
            "Auto-detect top PIN layers and create contact points"
        )
        self.check_auto_pin.setChecked(bool(self.options.get("auto_pin_contacts", False)))
        self.check_contacts_only = QtWidgets.QCheckBox(
            "Fast 3D: render contact pads + bottom surface only "
            "(collapse all other layers to one body solid)"
        )
        self.check_contacts_only.setChecked(bool(self.options.get("contacts_only_3d", False)))
        self.check_contacts_only.setToolTip(
            "Renders only the top PIN/bondable layer(s) and the bottom contact surface as full "
            "3D geometry.\nAll intermediate layers are merged into a single bounding-box solid.\n"
            "Dramatically reduces import time for complex chips."
        )
        opt_top.addWidget(self.check_match)
        opt_top.addWidget(self.check_hl)
        opt_top.addWidget(self.check_3d)
        opt_top.addWidget(self.check_auto_pin)
        opt_top.addWidget(self.check_contacts_only)
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

        # Wire up contacts-only pre-selection after the list is fully built
        self.check_contacts_only.toggled.connect(self._on_contacts_only_toggled)
        if self.check_contacts_only.isChecked():
            self._on_contacts_only_toggled(True)

    #--- Contacts-only preset -----------------------------------------------
    def _on_contacts_only_toggled(self, checked):
        """When Fast-3D contacts-only is enabled, force 3D and pre-select contact layers."""
        if checked:
            # Imply 3D extrusion
            self.check_3d.setChecked(True)
            self._select_contact_layers()

    def _select_contact_layers(self):
        """
        Pre-select only the top PIN/bondable layers and the bottom contact-surface
        layer.  Uses identify_contact_layers() from Core_Functionality when a map
        is available; falls back to top/bottom layer_id heuristic otherwise.
        """
        try:
            from core import Core_Functionality
            top_keys, bottom_keys = Core_Functionality.identify_contact_layers(
                self.layers, self.ihp_map
            )
        except Exception:
            # Fallback: highest and lowest layer_id
            ids = sorted({L.get("layer_id", 0) for L in self.layers})
            top_keys    = {(ids[-1], 0)} if ids else set()
            bottom_keys = {(ids[0],  0)} if ids else set()

        contact_keys = top_keys | bottom_keys

        for i in range(self.layer_list.count()):
            item = self.layer_list.item(i)
            layer = item.data(QtCore.Qt.UserRole)
            key = (layer.get("layer_id", 0), layer.get("datatype", 0))
            item.setCheckState(
                QtCore.Qt.Checked if key in contact_keys else QtCore.Qt.Unchecked
            )

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
        self.options["match_klayout"]    = self.check_match.isChecked()
        self.options["highlight_bondable"] = self.check_hl.isChecked()
        self.options["extrude_3d"]       = self.check_3d.isChecked()
        self.options["auto_pin_contacts"] = self.check_auto_pin.isChecked()
        self.options["contacts_only_3d"] = self.check_contacts_only.isChecked()

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
