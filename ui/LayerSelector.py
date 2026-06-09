"""
LayerSelector
=============
Dialog zur Layer-Auswahl vor dem GDS-Import.

Im LOD-Workflow bedeutet die "Import"-Checkbox NICHT mehr "wird geladen",
sondern "sofort beim Import laden". Alle anderen Layer werden trotzdem
im LOD-Manager registriert und können im Detail Layer Panel nachgeladen werden.

Entfernt gegenüber der alten Version:
  - "Fast Mesh 3D" (intern verfügbar, aber kein UI-Schalter mehr nötig)
  - "Fast 3D: render contact pads only" (ist jetzt immer der Standard)
  - "Import All Layers" Checkbox (alle Layer sind immer bekannt; Auswahl = sofort)
  - Legende (Farbcodierung erklärt sich durch Tooltips)
"""

from compat import QtWidgets, QtCore, QtGui

_SIMPLIFY_THRESHOLD = 5_000


class LayerSelector(QtWidgets.QDialog):
    """
    Layer-Auswahl-Dialog.

    Spalten
    -------
    0  Import    — Checkbox: diesen Layer sofort beim Import laden
    1  Layer     — Name (layer_id/datatype) + Kategorie-Badge
    2  Polygons  — Schätzung, farblich nach Schwere
    3  BBox      — Checkbox: Layer als Bounding-Box-Solid vereinfachen
    """

    def __init__(self, layers, selected_layers=None, parent=None,
                 options=None, ihp_map=None, poly_counts=None):
        super().__init__(parent)
        self.setWindowTitle("Layer auswählen")
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
            "layer_bbox":         set(),
        })

        layout = QtWidgets.QVBoxLayout(self)

        # ── Globale Optionen ───────────────────────────────────────────────────
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

        for w in (self.check_match, self.check_hl, self.check_3d, self.check_auto_pin):
            opt_top.addWidget(w)

        layout.addLayout(opt_top)

        # ── Auswahlzeile ──────────────────────────────────────────────────────
        opt_row = QtWidgets.QHBoxLayout()

        # Hinweistext statt "Import All Layers"-Checkbox
        hint = QtWidgets.QLabel(
            "✓ = sofort laden  —  alle Layer sind im Panel nachladbar")
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

        # ── Layer-Tabelle ──────────────────────────────────────────────────────
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
            "Import: sofort beim Start laden (unkritisch wenn nicht gesetzt\n"
            "— Layer kann danach im Detail Layer Panel nachgeladen werden).\n"
            "BBox: Layer als Bounding-Box-Solid darstellen statt volle Geometrie."
        )

        prev_bbox = self.options.get("layer_bbox", set())
        prev_keys = {(l.get("layer_id", 0), l.get("datatype", 0))
                     for l in self.selected_layers_prev}

        # Kategorie-Badges via ihp_map
        _pin_only = {"PIN", "LEFPIN"}
        _non_pin  = {"NET", "SPNET", "VIA", "DRAWING"}

        for layer in self.layers:
            layer_name = layer.get("name", "Unknown Layer")
            layer_id   = layer.get("layer_id", 0)
            datatype   = layer.get("datatype", 0)
            key        = (layer_id, datatype)
            count      = self.poly_counts.get(key, 0)

            # EDI-Info
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

            # ── Spalte 0: Import-Checkbox ──────────────────────────────────
            # Standard: Kontakt/Bondable-Layer vorausgewählt; Rest nicht
            default_checked = _is_bond or _is_pin_flat
            item.setCheckState(
                0,
                QtCore.Qt.Checked
                if (key in prev_keys if prev_keys else default_checked)
                else QtCore.Qt.Unchecked
            )

            # ── Spalte 1: Name + Kategorie-Badge ──────────────────────────
            display = f"{layer_name}  ({layer_id}/{datatype})"
            item.setText(1, display)
            item.setData(0, QtCore.Qt.UserRole, layer)

            # Tooltip mit Kategorie
            if _is_fill:
                item.setToolTip(1, "Fill / Dummy-Metal — wird immer als BBox dargestellt")
                item.setForeground(1, QtGui.QBrush(QtGui.QColor("#888888")))
            elif _is_bond:
                item.setToolTip(1, "Bondable / PIN — wird sofort geladen")
            elif _is_pin_flat:
                item.setToolTip(1, "PIN-Marker — wird als 2D-Fläche geladen")

            # ── Spalte 2: Polygon-Anzahl ───────────────────────────────────
            if count > 0:
                item.setText(2, f"{count:,}")
                item.setTextAlignment(2, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                if count > 50_000:
                    item.setForeground(2, QtGui.QBrush(QtGui.QColor("#ff4444")))
                    item.setToolTip(2, f"{count:,} Polygone — sehr schwer (>50k). BBox empfohlen.")
                elif count > 10_000:
                    item.setForeground(2, QtGui.QBrush(QtGui.QColor("#ff9800")))
                    item.setToolTip(2, f"{count:,} Polygone — schwer (>10k). BBox erwägen.")
                elif count > _SIMPLIFY_THRESHOLD:
                    item.setForeground(2, QtGui.QBrush(QtGui.QColor("#ffd700")))
                    item.setToolTip(2, f"{count:,} Polygone — mittel (>{_SIMPLIFY_THRESHOLD:,}).")
                else:
                    item.setForeground(2, QtGui.QBrush(QtGui.QColor("#88cc88")))
                    item.setToolTip(2, f"{count:,} Polygone — leicht.")
            else:
                item.setText(2, "?")
                item.setForeground(2, QtGui.QBrush(QtGui.QColor("#666666")))

            # ── Spalte 3: BBox-Checkbox ────────────────────────────────────
            # Auto-Tick: Fill-Layer und sehr schwere Layer; nie VIAs
            auto_bbox = (_is_fill or (count > _SIMPLIFY_THRESHOLD and not _is_via))
            use_bbox  = key in prev_bbox if prev_bbox else auto_bbox
            item.setCheckState(3, QtCore.Qt.Checked if use_bbox else QtCore.Qt.Unchecked)
            item.setToolTip(3, "Als Bounding-Box-Solid darstellen statt voller Geometrie")

            # Fill-Layer: BBox gesperrt (kann nicht deaktiviert werden)
            if _is_fill:
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(3, QtCore.Qt.Checked)

            self.layer_tree.addTopLevelItem(item)

        layout.addWidget(self.layer_tree)

        # ── Buttons ───────────────────────────────────────────────────────────
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)
        self.setMinimumWidth(560)
        self.setMinimumHeight(400)

        QtGui.QShortcut(
            QtGui.QKeySequence("Ctrl+A"), self.layer_tree,
            activated=self._select_all)

    # ── Toolbar-Aktionen ──────────────────────────────────────────────────────

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

    # ── Accept ────────────────────────────────────────────────────────────────

    def accept(self):
        self.options["match_klayout"]      = self.check_match.isChecked()
        self.options["highlight_bondable"] = self.check_hl.isChecked()
        self.options["extrude_3d"]         = self.check_3d.isChecked()
        self.options["auto_pin_contacts"]  = self.check_auto_pin.isChecked()
        # mesh_3d und contacts_only_3d nicht mehr im Dialog — werden intern gesetzt
        self.options["mesh_3d"]          = False
        self.options["contacts_only_3d"] = False

        self.selected_layers = []
        layer_bbox = set()

        # "selected_layers" = was sofort geladen wird (Import-Checkbox angehakt)
        # Alle Layer werden als all_layers an GDSCommand übergeben (via self.layers)
        for i in range(self.layer_tree.topLevelItemCount()):
            item  = self.layer_tree.topLevelItem(i)
            layer = item.data(0, QtCore.Qt.UserRole)
            if item.checkState(0) == QtCore.Qt.Checked:
                self.selected_layers.append(layer)
            if item.checkState(3) == QtCore.Qt.Checked:
                layer_bbox.add((layer.get("layer_id", 0), layer.get("datatype", 0)))

        self.options["layer_bbox"] = layer_bbox

        # Kein Fehler wenn nichts angehakt — LOD-Manager kann alles nachladen
        super().accept()
