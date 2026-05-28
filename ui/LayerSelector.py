"""
Layer Selection Dialog
Provides UI for selecting GDS layers with polygon-count estimates and
per-layer simplification (bounding-box) toggles.
"""

from compat import QtWidgets, QtCore, QtGui

# Layers with more polygons than this get their "Simplify" checkbox pre-ticked.
_SIMPLIFY_THRESHOLD = 5_000


class LayerSelector(QtWidgets.QDialog):
    """
    Layer selection dialog.

    Columns
    -------
    0  Import   — checkbox: include this layer in the import
    1  Layer    — name (layer_id/datatype)
    2  Polygons — estimated count, colour-coded by heaviness
    3  BBox     — checkbox: simplify this layer to a bounding-box solid
    """

    def __init__(self, layers, selected_layers=None, parent=None,
                 options=None, ihp_map=None, poly_counts=None):
        super().__init__(parent)
        self.setWindowTitle("Select Layers")
        self.layers = layers
        self.ihp_map = ihp_map or {}
        self.poly_counts = poly_counts or {}
        self.selected_layers = []
        self.selected_layers_prev = selected_layers or []
        self.options = dict(options or {
            "match_klayout":      True,
            "highlight_bondable": True,
            "extrude_3d":         False,
            "mesh_3d":            False,
            "auto_pin_contacts":  False,
            "contacts_only_3d":   False,
            "layer_bbox":         set(),
        })

        layout = QtWidgets.QVBoxLayout(self)

        # ── global options ────────────────────────────────────────────────────
        opt_top = QtWidgets.QVBoxLayout()
        self.check_match = QtWidgets.QCheckBox(
            "Match KLayout view (no filters, use LYP colors)")
        self.check_match.setChecked(bool(self.options.get("match_klayout", True)))

        self.check_hl = QtWidgets.QCheckBox("Highlight bondable layers (gold)")
        self.check_hl.setChecked(bool(self.options.get("highlight_bondable", True)))

        self.check_3d = QtWidgets.QCheckBox(
            "Extrude layers to 3D volumes (uses PDK thickness table)")
        self.check_3d.setChecked(bool(self.options.get("extrude_3d", False)))

        self.check_mesh_3d = QtWidgets.QCheckBox(
            "Fast Mesh 3D — preserve geometry, skip OCCT B-rep (slicer-style tessellation)")
        self.check_mesh_3d.setChecked(bool(self.options.get("mesh_3d", False)))
        self.check_mesh_3d.setToolTip(
            "Triangulates each GDS polygon directly into a mesh without going through OCCT.\n"
            "Preserves all polygon geometry but produces a mesh (not a solid).\n"
            "Typically 10–50× faster than full B-rep extrusion for complex layers.")

        self.check_auto_pin = QtWidgets.QCheckBox(
            "Auto-detect top PIN layers and create contact points")
        self.check_auto_pin.setChecked(bool(self.options.get("auto_pin_contacts", False)))

        self.check_contacts_only = QtWidgets.QCheckBox(
            "Fast 3D: render contact pads + bottom surface only "
            "(collapse all other layers to one body solid)")
        self.check_contacts_only.setChecked(bool(self.options.get("contacts_only_3d", False)))
        self.check_contacts_only.setToolTip(
            "Renders only the top PIN/bondable layer(s) and the bottom contact surface as full "
            "3D geometry.\nAll intermediate layers are merged into a single bounding-box solid.\n"
            "Dramatically reduces import time for complex chips.")

        for w in (self.check_match, self.check_hl, self.check_3d,
                  self.check_mesh_3d, self.check_auto_pin, self.check_contacts_only):
            opt_top.addWidget(w)

        # mutual exclusion for the three 3D modes
        self.check_3d.toggled.connect(
            lambda on: self.check_mesh_3d.setChecked(False) if on else None)
        self.check_3d.toggled.connect(
            lambda on: self.check_contacts_only.setChecked(False) if on else None)
        self.check_mesh_3d.toggled.connect(
            lambda on: self.check_3d.setChecked(False) if on else None)
        self.check_mesh_3d.toggled.connect(
            lambda on: self.check_contacts_only.setChecked(False) if on else None)
        self.check_contacts_only.toggled.connect(
            lambda on: self.check_mesh_3d.setChecked(False) if on else None)

        layout.addLayout(opt_top)

        # ── selection control row ─────────────────────────────────────────────
        opt_row = QtWidgets.QHBoxLayout()
        self.check_all_button = QtWidgets.QCheckBox("Import All Layers")
        self.check_all_button.toggled.connect(self._toggle_all_mode)
        opt_row.addWidget(self.check_all_button)
        opt_row.addStretch(1)

        self.select_all_button = QtWidgets.QPushButton("Select All")
        self.select_all_button.clicked.connect(self._select_all)
        self.clear_all_button = QtWidgets.QPushButton("Clear All")
        self.clear_all_button.clicked.connect(self._clear_all)
        self.invert_button = QtWidgets.QPushButton("Invert")
        self.invert_button.clicked.connect(self._invert)

        for b in (self.select_all_button, self.clear_all_button, self.invert_button):
            opt_row.addWidget(b)
        layout.addLayout(opt_row)

        # ── layer tree ────────────────────────────────────────────────────────
        self.layer_tree = QtWidgets.QTreeWidget()
        self.layer_tree.setColumnCount(4)
        self.layer_tree.setHeaderLabels(["Import", "Layer", "Polygons", "BBox"])
        self.layer_tree.setRootIsDecorated(False)
        self.layer_tree.setAlternatingRowColors(True)
        self.layer_tree.setSortingEnabled(True)

        hdr = self.layer_tree.header()
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
        hdr.setSectionResizeMode(3, QtWidgets.QHeaderView.Fixed)
        self.layer_tree.setColumnWidth(0, 54)
        self.layer_tree.setColumnWidth(2, 84)
        self.layer_tree.setColumnWidth(3, 44)
        self.layer_tree.header().setToolTip(
            "BBox column: tick to replace the layer with a bounding-box solid.\n"
            "Useful for fill/dummy-metal layers with millions of polygons.")

        prev_bbox = self.options.get("layer_bbox", set())
        prev_keys = {(l.get("layer_id", 0), l.get("datatype", 0))
                     for l in self.selected_layers_prev}

        for layer in self.layers:
            layer_name = layer.get("name", "Unknown Layer")
            layer_id   = layer.get("layer_id", 0)
            datatype   = layer.get("datatype", 0)
            key        = (layer_id, datatype)
            count      = self.poly_counts.get(key, 0)

            item = QtWidgets.QTreeWidgetItem()
            item.setFlags(
                item.flags()
                | QtCore.Qt.ItemIsUserCheckable
                | QtCore.Qt.ItemIsEnabled
                | QtCore.Qt.ItemIsSelectable
            )

            # col 0: Import checkbox
            item.setCheckState(
                0,
                QtCore.Qt.Checked if key in prev_keys else QtCore.Qt.Unchecked
            )

            # col 1: layer name
            item.setText(1, f"{layer_name}  ({layer_id}/{datatype})")
            item.setData(0, QtCore.Qt.UserRole, layer)

            # col 2: polygon count
            if count > 0:
                item.setText(2, f"{count:,}")
                item.setTextAlignment(2, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                if count > 50_000:
                    item.setForeground(2, QtGui.QBrush(QtGui.QColor("#ff4444")))
                    item.setToolTip(2, f"{count:,} polygons — very heavy (>50 k). "
                                       "BBox strongly recommended.")
                elif count > 10_000:
                    item.setForeground(2, QtGui.QBrush(QtGui.QColor("#ff9800")))
                    item.setToolTip(2, f"{count:,} polygons — heavy (>10 k). "
                                       "Consider enabling BBox.")
                elif count > _SIMPLIFY_THRESHOLD:
                    item.setForeground(2, QtGui.QBrush(QtGui.QColor("#ffd700")))
                    item.setToolTip(2, f"{count:,} polygons — moderate (>{_SIMPLIFY_THRESHOLD:,}). "
                                       "BBox optional.")
                else:
                    item.setForeground(2, QtGui.QBrush(QtGui.QColor("#88cc88")))
                    item.setToolTip(2, f"{count:,} polygons — light.")
            else:
                item.setText(2, "?")
                item.setForeground(2, QtGui.QBrush(QtGui.QColor("#666666")))

            # col 3: BBox checkbox — auto-tick heavy layers or restore prev state.
            # VIA-type layers are never auto-ticked: they must render as individual
            # geometry (grid of squares) — collapsing them to a bbox loses all structure.
            _edi = set()
            if self.ihp_map:
                _edi_info = self.ihp_map.get(key)
                if _edi_info:
                    _edi = {t.upper() for t in _edi_info.get("edi_types", set())}
            _is_via = bool({"VIA", "VIAFILL"} & _edi) or "via" in layer_name.lower()
            auto_bbox   = count > _SIMPLIFY_THRESHOLD and not _is_via
            use_bbox    = key in prev_bbox if prev_bbox else auto_bbox
            # second-column checkstate (Qt supports per-column check via setCheckState
            # only on col 0 in standard QTreeWidgetItem — so we use a workaround)
            item.setCheckState(3, QtCore.Qt.Checked if use_bbox else QtCore.Qt.Unchecked)
            item.setToolTip(3, "Render as bounding-box solid instead of full polygon geometry")

            self.layer_tree.addTopLevelItem(item)

        layout.addWidget(self.layer_tree)

        # ── legend ────────────────────────────────────────────────────────────
        legend = QtWidgets.QLabel(
            "<span style='color:#ff4444'>■</span> >50 k  "
            "<span style='color:#ff9800'>■</span> >10 k  "
            "<span style='color:#ffd700'>■</span> "
            f">{_SIMPLIFY_THRESHOLD:,}  "
            "<span style='color:#88cc88'>■</span> light  "
            "— polygon counts are estimates (flat cell traversal)"
        )
        legend.setStyleSheet("font-size: 9px; color: #888; padding: 2px 0;")
        layout.addWidget(legend)

        # ── dialog buttons ────────────────────────────────────────────────────
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)
        self.setMinimumWidth(560)
        self.setMinimumHeight(400)
        self._toggle_all_mode(False)

        # shortcut: Ctrl+A selects all
        QtGui.QShortcut(
            QtGui.QKeySequence("Ctrl+A"), self.layer_tree,
            activated=self._select_all)

        # contacts-only wiring
        self.check_contacts_only.toggled.connect(self._on_contacts_only_toggled)
        if self.check_contacts_only.isChecked():
            self._on_contacts_only_toggled(True)

    # ── contacts-only preset ──────────────────────────────────────────────────

    def _on_contacts_only_toggled(self, checked):
        if checked:
            self.check_3d.setChecked(True)
            self._select_contact_layers()

    def _select_contact_layers(self):
        try:
            from core import Core_Functionality
            top_keys, bottom_keys = Core_Functionality.identify_contact_layers(
                self.layers, self.ihp_map)
        except Exception:
            ids = sorted({L.get("layer_id", 0) for L in self.layers})
            top_keys    = {(ids[-1], 0)} if ids else set()
            bottom_keys = {(ids[0],  0)} if ids else set()
        contact_keys = top_keys | bottom_keys
        for i in range(self.layer_tree.topLevelItemCount()):
            item  = self.layer_tree.topLevelItem(i)
            layer = item.data(0, QtCore.Qt.UserRole)
            key   = (layer.get("layer_id", 0), layer.get("datatype", 0))
            item.setCheckState(
                0,
                QtCore.Qt.Checked if key in contact_keys else QtCore.Qt.Unchecked
            )

    # ── toolbar actions ───────────────────────────────────────────────────────

    def _toggle_all_mode(self, enabled):
        self.layer_tree.setDisabled(enabled)
        self.select_all_button.setDisabled(enabled)
        self.clear_all_button.setDisabled(enabled)
        self.invert_button.setDisabled(enabled)
        if enabled:
            for i in range(self.layer_tree.topLevelItemCount()):
                self.layer_tree.topLevelItem(i).setCheckState(0, QtCore.Qt.Checked)

    def _select_all(self):
        for i in range(self.layer_tree.topLevelItemCount()):
            self.layer_tree.topLevelItem(i).setCheckState(0, QtCore.Qt.Checked)

    def _clear_all(self):
        for i in range(self.layer_tree.topLevelItemCount()):
            self.layer_tree.topLevelItem(i).setCheckState(0, QtCore.Qt.Unchecked)

    def _invert(self):
        for i in range(self.layer_tree.topLevelItemCount()):
            item = self.layer_tree.topLevelItem(i)
            cur  = item.checkState(0)
            item.setCheckState(
                0,
                QtCore.Qt.Unchecked if cur == QtCore.Qt.Checked else QtCore.Qt.Checked
            )

    # ── accept ────────────────────────────────────────────────────────────────

    def accept(self):
        self.options["match_klayout"]      = self.check_match.isChecked()
        self.options["highlight_bondable"] = self.check_hl.isChecked()
        self.options["extrude_3d"]         = self.check_3d.isChecked()
        self.options["mesh_3d"]            = self.check_mesh_3d.isChecked()
        self.options["auto_pin_contacts"]  = self.check_auto_pin.isChecked()
        self.options["contacts_only_3d"]   = self.check_contacts_only.isChecked()

        self.selected_layers = []
        layer_bbox = set()

        if self.check_all_button.isChecked():
            self.selected_layers = list(self.layers)
            for i in range(self.layer_tree.topLevelItemCount()):
                item  = self.layer_tree.topLevelItem(i)
                layer = item.data(0, QtCore.Qt.UserRole)
                if item.checkState(3) == QtCore.Qt.Checked:
                    layer_bbox.add((layer.get("layer_id", 0), layer.get("datatype", 0)))
        else:
            for i in range(self.layer_tree.topLevelItemCount()):
                item  = self.layer_tree.topLevelItem(i)
                layer = item.data(0, QtCore.Qt.UserRole)
                if item.checkState(0) == QtCore.Qt.Checked:
                    self.selected_layers.append(layer)
                if item.checkState(3) == QtCore.Qt.Checked:
                    layer_bbox.add((layer.get("layer_id", 0), layer.get("datatype", 0)))

        self.options["layer_bbox"] = layer_bbox

        if not self.selected_layers:
            QtWidgets.QMessageBox.warning(self, "Warning", "No layers selected.")
            return
        super().accept()
