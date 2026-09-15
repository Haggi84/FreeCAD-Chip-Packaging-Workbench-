# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
LayerSelector
=============
Dialog for layer selection before GDS import.

In the LOD workflow the "Import" checkbox no longer means "will be loaded"
but "load immediately at import time". All other layers are still registered
in the LOD manager and can be loaded later from the Detail Layer Panel.

Removed compared to the old version:
  - "Fast Mesh 3D" (available internally, but no UI switch needed anymore)
  - "Fast 3D: render contact pads only" (is now always the default)
  - "Import All Layers" checkbox (all layers are always known; selection = immediately)
  - Legend (color coding is explained by tooltips)
"""

from compat import QtWidgets, QtCore, QtGui

_SIMPLIFY_THRESHOLD = 5_000


class LayerSelector(QtWidgets.QDialog):
    """
    Layer selection dialog.

    Columns
    -------
    0  Import    — Checkbox: load this layer immediately at import time
    1  Layer     — Name (layer_id/datatype) + category badge
    2  Polygons  — estimate, color-coded by complexity
    3  BBox      — Checkbox: simplify layer as a bounding-box solid
    """

    def __init__(self, layers, selected_layers=None, parent=None,
                 options=None, ihp_map=None, poly_counts=None):
        super().__init__(parent)
        self.setWindowTitle("Select Layers")
        self.layers       = layers
        self.ihp_map      = ihp_map or {}
        self.poly_counts  = poly_counts or {}
        self.selected_layers      = []
        self.selected_layers_prev = selected_layers or []

        self.options = dict(options or {
            "match_klayout":      True,
            "highlight_bondable": True,
            "extrude_3d":         False,
            "auto_pin_contacts":  False,
            "keep_via_detail":    True,
            "add_die_body":       True,
            "drop_to_die_surface": False,
            "fill_dielectric_gap": True,
            "klayout_exact":      False,
            "layer_bbox":         set(),
        })

        layout = QtWidgets.QVBoxLayout(self)

        # ── Global options ────────────────────────────────────────────────────
        opt_top = QtWidgets.QVBoxLayout()

        self.check_match = QtWidgets.QCheckBox(
            "Match KLayout view (no filters, use LYP colors)")
        self.check_match.setChecked(bool(self.options.get("match_klayout", True)))

        self.check_hl = QtWidgets.QCheckBox(
            "Highlight bondable layers (gold)")
        self.check_hl.setChecked(bool(self.options.get("highlight_bondable", True)))

        self.check_3d = QtWidgets.QCheckBox(
            "Extrude layers to 3D volumes (uses PDK thickness table)")
        self.check_3d.setChecked(bool(self.options.get("extrude_3d", False)))

        self.check_auto_pin = QtWidgets.QCheckBox(
            "Auto-detect top PIN layers and create contact points")
        self.check_auto_pin.setChecked(bool(self.options.get("auto_pin_contacts", False)))

        self.check_vias = QtWidgets.QCheckBox(
            "Keep VIA layers in full detail  —  untick to show them as blocks")
        self.check_vias.setChecked(bool(self.options.get("keep_via_detail", True)))
        self.check_vias.setToolTip(
            "Via layers hold the most polygons on a chip, so the automatic\n"
            "simplification collapses them first — and collapsing a via array\n"
            "to one bounding box turns separate pillars into a solid slab.\n\n"
            "On (recommended): via layers are always built as real geometry.\n"
            "Off: they are simplified like any other layer when the import\n"
            "gets heavy.\n\n"
            "Either way, switch at any time with Toggle VIA Detail in the\n"
            "Render toolbar - blocks are built on demand, so starting in\n"
            "full detail costs nothing later.\n\n"
            "A block is one cube per cluster of vias, spanning that\n"
            "cluster's X/Y outline, so a dense array becomes one shape\n"
            "while physically separate arrays stay separate."
        )

        self.check_body = QtWidgets.QCheckBox(
            "Add the die body below the layout (epi + substrate, from the stackup)")
        self.check_body.setChecked(bool(self.options.get("add_die_body", True)))
        self.check_body.setToolTip(
            "A die is mostly the silicon under the lowest drawn layer. On\n"
            "IHP SG13G2 the drawn stack is 14.23 µm and the body beneath it\n"
            "is 183.75 µm — epi 3.75 µm on 180 µm of substrate — so without\n"
            "this the imported object is under 8% of the real part.\n\n"
            "The slabs are built from the stackup XML's own <Dielectric>\n"
            "entries and span the die outline. Nothing is built if the\n"
            "stackup does not declare them."
        )

        self.check_drop = QtWidgets.QCheckBox(
            "Drop the layer stack onto the die surface (close the gap below it)")
        self.check_drop.setChecked(
            bool(self.options.get("drop_to_die_surface", False)))
        self.check_drop.setToolTip(
            "Importing only part of a stack leaves it floating. Loading just\n"
            "the top of an SG13G2 stack puts Metal5 at 5.09 µm with nothing\n"
            "beneath it, because Activ, the contacts and Metal1-4 were never\n"
            "built — a 4.89 µm gap above the die surface.\n\n"
            "On: the loaded layers slide down together so the lowest one\n"
            "starts at the die surface. Relative spacing is preserved, and\n"
            "the substrate and epi do not move.\n"
            "Off: every layer keeps its true PDK height.\n\n"
            "No effect on a full import — the lowest layer is already there."
        )

        self.check_fill = QtWidgets.QCheckBox(
            "Fill the gap below the lowest used layer with dielectric")
        self.check_fill.setChecked(
            bool(self.options.get("fill_dielectric_gap", True)))
        self.check_fill.setToolTip(
            "A PDK defines more levels than any one layout draws on. If the\n"
            "lowest layer used is Metal5, nothing sits between the silicon\n"
            "and 5.09 um - but that volume is not empty in the real part,\n"
            "it is the oxide the unused metal levels are embedded in.\n\n"
            "On: a slab of the stackup's own inter-metal dielectric fills\n"
            "the space from the die surface up to the lowest used layer,\n"
            "so every layer keeps its true PDK height.\n\n"
            "No effect on a full import - the lowest layer already sits on\n"
            "the die surface, so there is no gap to fill."
        )

        self.check_exact = QtWidgets.QCheckBox(
            "Exactly as KLayout draws it (no simplification — can be very slow)")
        self.check_exact.setChecked(bool(self.options.get("klayout_exact", False)))
        self.check_exact.setToolTip(
            "Build every polygon as drawn, with the .lyp's own colours.\n\n"
            "Switches off, together: the per-layer polygon threshold, the\n"
            "total polygon budget, the micro-area scan that collapses\n"
            "sub-micron layers, dummy-fill collapsing, area filtering,\n"
            "outline decimation, via blocks, fast-mesh baking, and\n"
            "level-of-detail loading — every selected layer is built in full.\n"
            "Bond-pad layers also keep their .lyp colour instead of being\n"
            "repainted gold.\n\n"
            "This is what those mechanisms exist to avoid. A full chip can\n"
            "carry millions of polygons and each one becomes an OCCT solid.\n"
            "Marking individual layers as BBox in the list below still works."
        )
        self.check_exact.toggled.connect(self._update_exact_warning)

        self.lbl_exact = QtWidgets.QLabel()
        self.lbl_exact.setWordWrap(True)
        self.lbl_exact.setStyleSheet("QLabel { color: #e0a030; }")
        self.lbl_exact.setVisible(False)

        for w in (self.check_match, self.check_hl, self.check_3d,
                  self.check_auto_pin, self.check_vias, self.check_body,
                  self.check_drop, self.check_fill, self.check_exact,
                  self.lbl_exact):
            opt_top.addWidget(w)

        layout.addLayout(opt_top)

        # ── Selection row ─────────────────────────────────────────────────────
        opt_row = QtWidgets.QHBoxLayout()

        # Info text instead of "Import All Layers" checkbox
        hint = QtWidgets.QLabel(
            "✓ = load immediately  —  all layers can be loaded later in the panel")
        hint.setStyleSheet("font-size: 9px; color: #888; padding: 2px 0;")
        opt_row.addWidget(hint, 1)

        self.select_all_button = QtWidgets.QPushButton("Select All")
        self.select_all_button.clicked.connect(self._select_all)
        self.clear_all_button  = QtWidgets.QPushButton("Clear All")
        self.clear_all_button.clicked.connect(self._clear_all)
        self.invert_button     = QtWidgets.QPushButton("Invert")
        self.invert_button.clicked.connect(self._invert)

        for b in (self.select_all_button, self.clear_all_button, self.invert_button):
            opt_row.addWidget(b)
        layout.addLayout(opt_row)

        # ── Layer table ───────────────────────────────────────────────────────
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
            "Import: load immediately at startup (non-critical if not set\n"
            "— layer can be loaded later in the Detail Layer Panel).\n"
            "BBox: display layer as a bounding-box solid instead of full geometry."
        )

        prev_bbox = self.options.get("layer_bbox", set())
        prev_keys = {(l.get("layer_id", 0), l.get("datatype", 0))
                     for l in self.selected_layers_prev}

        # Category badges via ihp_map
        _pin_only = {"PIN", "LEFPIN"}
        _non_pin  = {"NET", "SPNET", "VIA", "DRAWING"}

        for layer in self.layers:
            layer_name = layer.get("name", "Unknown Layer")
            layer_id   = layer.get("layer_id", 0)
            datatype   = layer.get("datatype", 0)
            key        = (layer_id, datatype)
            count      = self.poly_counts.get(key, 0)

            # EDI info
            _edi_types = set()
            _edi_name  = ""
            if self.ihp_map:
                _info = self.ihp_map.get(key)
                if _info:
                    _edi_types = {t.upper() for t in _info.get("edi_types", set())}
                    _edi_name  = _info.get("edi_name", "")

            _is_fill   = "FILL" in _edi_types
            _is_bond   = bool({"PIN","LEFPIN","PAD","BUMP"} & _edi_types) \
                         and not bool({"NET","SPNET","VIA","DRAWING"} & _edi_types)
            _is_via    = bool({"VIA","VIAFILL"} & _edi_types) \
                         or "via" in layer_name.lower()
            _is_pin_flat = bool(_pin_only & _edi_types) \
                           and not bool(_non_pin & _edi_types)

            item = QtWidgets.QTreeWidgetItem()
            item.setFlags(
                item.flags()
                | QtCore.Qt.ItemIsUserCheckable
                | QtCore.Qt.ItemIsEnabled
                | QtCore.Qt.ItemIsSelectable
            )

            # ── Column 0: Import checkbox ─────────────────────────────────
            # Default: contact/bondable layers pre-selected; others not
            default_checked = _is_bond or _is_pin_flat
            item.setCheckState(
                0,
                QtCore.Qt.Checked
                if (key in prev_keys if prev_keys else default_checked)
                else QtCore.Qt.Unchecked
            )

            # ── Column 1: Name + category badge ───────────────────────────
            display = f"{layer_name}  ({layer_id}/{datatype})"
            item.setText(1, display)
            item.setData(0, QtCore.Qt.UserRole, layer)

            # Tooltip with category
            if _is_fill:
                item.setToolTip(1, "Fill / Dummy-Metal — always displayed as BBox")
                item.setForeground(1, QtGui.QBrush(QtGui.QColor("#888888")))
            elif _is_bond:
                item.setToolTip(1, "Bondable / PIN — loaded immediately")
            elif _is_pin_flat:
                item.setToolTip(1, "PIN marker — loaded as a 2D surface")

            # ── Column 2: Polygon count ────────────────────────────────────
            if count > 0:
                item.setText(2, f"{count:,}")
                item.setTextAlignment(2, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                if count > 50_000:
                    item.setForeground(2, QtGui.QBrush(QtGui.QColor("#ff4444")))
                    item.setToolTip(2, f"{count:,} polygons — very heavy (>50k). BBox recommended.")
                elif count > 10_000:
                    item.setForeground(2, QtGui.QBrush(QtGui.QColor("#ff9800")))
                    item.setToolTip(2, f"{count:,} polygons — heavy (>10k). Consider BBox.")
                elif count > _SIMPLIFY_THRESHOLD:
                    item.setForeground(2, QtGui.QBrush(QtGui.QColor("#ffd700")))
                    item.setToolTip(2, f"{count:,} polygons — medium (>{_SIMPLIFY_THRESHOLD:,}).")
                else:
                    item.setForeground(2, QtGui.QBrush(QtGui.QColor("#88cc88")))
                    item.setToolTip(2, f"{count:,} polygons — light.")
            else:
                item.setText(2, "?")
                item.setForeground(2, QtGui.QBrush(QtGui.QColor("#666666")))

            # ── Column 3: BBox checkbox ────────────────────────────────────
            # Auto-tick: fill layers and very heavy layers; never VIAs
            auto_bbox = (_is_fill or (count > _SIMPLIFY_THRESHOLD and not _is_via))
            use_bbox  = key in prev_bbox if prev_bbox else auto_bbox
            item.setCheckState(3, QtCore.Qt.Checked if use_bbox else QtCore.Qt.Unchecked)
            item.setToolTip(3, "Display as a bounding-box solid instead of full geometry")

            # Fill layers: BBox locked (cannot be deactivated)
            if _is_fill:
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(3, QtCore.Qt.Checked)

            self.layer_tree.addTopLevelItem(item)

        layout.addWidget(self.layer_tree)

        # ── Buttons ──────────────────────────────────────────────────────────
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)
        self.setMinimumWidth(560)
        self.setMinimumHeight(400)

        # Seed the cost warning, so re-opening the dialog with exact mode
        # already remembered shows it straight away rather than only after
        # the box is toggled.
        self._update_exact_warning()

        QtGui.QShortcut(
            QtGui.QKeySequence("Ctrl+A"), self.layer_tree,
            activated=self._select_all)

    # ── Toolbar actions ───────────────────────────────────────────────────────

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

    # ── Exact-mode cost ───────────────────────────────────────────────────────

    # Rough build rate for OCCT solids, from measurements on this workbench:
    # 8,700 polygons took 15 s and 82,000 took 51 s, i.e. very roughly
    # 1,600/s once the fixed overhead is past. Only ever used to decide how
    # loudly to warn, never to make a decision for the user.
    _POLYS_PER_SECOND = 1600.0

    def _total_polygons(self):
        return sum(int(v or 0) for v in self.poly_counts.values())

    def _update_exact_warning(self, checked=None):
        """
        Say what exact mode will actually cost for THIS file.

        A generic "may be slow" is useless when the honest answer ranges from
        under a second to most of an hour depending on the layout. The
        polygon counts are already gathered for the Polygons column, so the
        estimate costs nothing.
        """
        if checked is None:
            checked = self.check_exact.isChecked()
        if not checked:
            self.lbl_exact.setVisible(False)
            return

        total = self._total_polygons()
        if total <= 0:
            self.lbl_exact.setText(
                "⚠  Every polygon will be built as a solid. No polygon count "
                "is available for this file, so the cost cannot be estimated.")
            self.lbl_exact.setVisible(True)
            return

        seconds = total / self._POLYS_PER_SECOND
        if seconds < 90:
            when = f"roughly {max(1, int(seconds))} s"
        elif seconds < 3600:
            when = f"roughly {seconds / 60.0:.0f} min"
        else:
            when = f"roughly {seconds / 3600.0:.1f} hours"
        self.lbl_exact.setText(
            f"⚠  {total:,} polygons in this layout, each built as a solid — "
            f"expect {when}, and correspondingly high memory use. Untick "
            f"layers you do not need, or mark heavy ones as BBox, to cut this "
            f"down.")
        self.lbl_exact.setVisible(True)

    # ── Accept ────────────────────────────────────────────────────────────────

    def accept(self):
        self.options["match_klayout"]      = self.check_match.isChecked()
        self.options["highlight_bondable"] = self.check_hl.isChecked()
        self.options["extrude_3d"]         = self.check_3d.isChecked()
        self.options["auto_pin_contacts"]  = self.check_auto_pin.isChecked()
        self.options["keep_via_detail"]    = self.check_vias.isChecked()
        self.options["add_die_body"]       = self.check_body.isChecked()
        self.options["drop_to_die_surface"] = self.check_drop.isChecked()
        self.options["fill_dielectric_gap"] = self.check_fill.isChecked()
        self.options["klayout_exact"]      = self.check_exact.isChecked()
        # mesh_3d and contacts_only_3d no longer in dialog — set internally
        self.options["mesh_3d"]          = False
        self.options["contacts_only_3d"] = False

        self.selected_layers = []
        layer_bbox = set()

        # "selected_layers" = what is loaded immediately (Import checkbox ticked)
        # All layers are passed as all_layers to GDSCommand (via self.layers)
        for i in range(self.layer_tree.topLevelItemCount()):
            item  = self.layer_tree.topLevelItem(i)
            layer = item.data(0, QtCore.Qt.UserRole)
            if item.checkState(0) == QtCore.Qt.Checked:
                self.selected_layers.append(layer)
            if item.checkState(3) == QtCore.Qt.Checked:
                layer_bbox.add((layer.get("layer_id", 0), layer.get("datatype", 0)))

        self.options["layer_bbox"] = layer_bbox

        # No error if nothing is ticked — LOD manager can load everything later
        super().accept()
