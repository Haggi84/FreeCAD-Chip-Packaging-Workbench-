# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Point at the top contact areas in the GDS and make pads of them.

Automatic pad detection recognises the conventions it knows (see
core.gds_pads for which, and why SKY130 is not among them). When it finds
nothing, or finds the wrong thing, the layout itself still knows perfectly
well where its pads are — so this shows the file's own structure and lets
them be pointed at:

    Layers            the pad opening is a layer; picking it takes one pad
                      per opening drawn on it
    Cells (structure) the padframe places a pad cell; picking that cell
                      takes one pad per placement, arrays included

Both can be picked at once — they usually describe the same pads, and pads
landing on top of each other are dropped rather than doubled.

The size bounds decide what counts as a pad. They are shown because they are
a judgement, not a fact: 10 to 500 µm covers wire-bond pads, and a micro-bump
field needs the lower bound moved down.
"""

import os

from compat import QtWidgets, QtCore

import core.gds_pads as gds_pads

_NAME, _COUNT, _PADLIKE, _SIZE = range(4)


def _um(value_mm):
    return f"{value_mm * 1000.0:.1f}"


class PadPickerDialog(QtWidgets.QDialog):
    """Choose the layers and cells that hold this chip's contact areas."""

    def __init__(self, gds_path, target_label="", parent=None,
                 selected_layers=None, ihp_map=None, has_pads=False):
        super().__init__(parent)
        self.setWindowTitle("Define Pads from GDS")
        self._gds = gds_path
        self._selected_layers = selected_layers
        self._ihp_map = ihp_map
        self._cells_loaded = False

        outer = QtWidgets.QVBoxLayout(self)

        title = f"<b>{os.path.basename(gds_path)}</b>"
        if target_label:
            title += f" &rarr; pads on <b>{target_label}</b>"
        head = QtWidgets.QLabel(title)
        head.setTextFormat(QtCore.Qt.RichText)
        outer.addWidget(head)

        hint = QtWidgets.QLabel(
            "Tick the layer the pad openings are drawn on, or the cell the "
            "padframe places — whichever this layout uses. The Pad-sized "
            "column counts the shapes that fall within the bounds below.")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self._tree = QtWidgets.QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["Layer / cell", "Shapes", "Pad-sized",
                                    "Typical size (µm)"])
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.itemChanged.connect(self._selection_changed)
        self._tree.itemExpanded.connect(self._expanded)
        outer.addWidget(self._tree, 1)

        bounds = QtWidgets.QHBoxLayout()
        bounds.addWidget(QtWidgets.QLabel("A pad is between"))
        self._min = self._bound(gds_pads.DEFAULT_MIN_PAD_MM)
        self._max = self._bound(gds_pads.DEFAULT_MAX_PAD_MM)
        bounds.addWidget(self._min)
        bounds.addWidget(QtWidgets.QLabel("and"))
        bounds.addWidget(self._max)
        bounds.addWidget(QtWidgets.QLabel("µm across."))
        rescan = QtWidgets.QPushButton("Recount")
        rescan.setToolTip("Count the shapes again with these bounds")
        rescan.clicked.connect(self._load_layers)
        bounds.addWidget(rescan)
        bounds.addStretch()
        outer.addLayout(bounds)

        self._replace = QtWidgets.QCheckBox(
            "Remove the pads this chip already has")
        self._replace.setToolTip(
            "Picking again otherwise adds to what is there — useful for a "
            "second pad ring, wrong for a correction.")
        self._replace.setEnabled(has_pads)
        if not has_pads:
            self._replace.setToolTip("This chip has no pads yet.")
        outer.addWidget(self._replace)

        self._summary = QtWidgets.QLabel("")
        outer.addWidget(self._summary)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)

        self.resize(680, 520)
        self._load_layers()

    # ── building the tree ──────────────────────────────────────────────

    @staticmethod
    def _bound(value_mm):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(0.01, 100000.0)
        spin.setDecimals(1)
        spin.setValue(value_mm * 1000.0)
        spin.setSuffix(" µm")
        return spin

    def _bounds_mm(self):
        return self._min.value() / 1000.0, self._max.value() / 1000.0

    def _busy(self, busy):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor) if busy \
            else QtWidgets.QApplication.restoreOverrideCursor()
        QtWidgets.QApplication.processEvents()

    def _load_layers(self):
        """(Re)build the layer branch with the current size bounds."""
        checked = set(self.layer_keys())
        min_mm, max_mm = self._bounds_mm()
        self._busy(True)
        try:
            rows = gds_pads.layer_rows(self._gds, self._selected_layers,
                                       self._ihp_map, min_mm, max_mm)
        except Exception as exc:
            rows = []
            self._summary.setText(f"Could not read the layers: {exc}")
        finally:
            self._busy(False)

        self._tree.blockSignals(True)
        self._tree.clear()

        self._layer_root = QtWidgets.QTreeWidgetItem(self._tree, ["Layers"])
        self._layer_root.setFirstColumnSpanned(False)
        suggested = self._suggest(rows)
        for row in rows:
            key = (row["layer"], row["datatype"])
            item = QtWidgets.QTreeWidgetItem(self._layer_root, [
                f"{row['name']}  ({row['layer']}/{row['datatype']})",
                f"{row['polygons']:,}",
                f"{row['pad_like']:,}",
                f"{_um(row['median_w_mm'])} × {_um(row['median_h_mm'])}",
            ])
            item.setData(_NAME, QtCore.Qt.UserRole, ("layer", key))
            state = QtCore.Qt.Checked if (key in checked or
                                          (not checked and key == suggested)) \
                else QtCore.Qt.Unchecked
            item.setCheckState(_NAME, state)
            if key == suggested:
                item.setToolTip(_NAME, "The most pad-like layer in this file "
                                       "— highest in the stack with pad-sized "
                                       "shapes on it.")
        self._layer_root.setExpanded(True)

        self._cell_root = QtWidgets.QTreeWidgetItem(
            self._tree, ["Cells (structure)"])
        self._cell_root.setChildIndicatorPolicy(
            QtWidgets.QTreeWidgetItem.ShowIndicator)
        self._cells_loaded = False

        self._tree.blockSignals(False)
        for column in range(4):
            self._tree.resizeColumnToContents(column)
        self._selection_changed()

    @staticmethod
    def _suggest(rows):
        """
        The layer most likely to hold the pads: the one with pad-sized shapes
        that sits highest in the stack. Pads are the top of the stack by
        definition — a lower layer with more pad-sized shapes is a via array.
        """
        candidates = [row for row in rows if row["pad_like"] > 0]
        if not candidates:
            return None
        best = max(candidates, key=lambda row: (row["layer"], row["pad_like"]))
        return (best["layer"], best["datatype"])

    def _expanded(self, item):
        if item is self._cell_root and not self._cells_loaded:
            self._load_cells()

    def _load_cells(self):
        self._cells_loaded = True
        self._busy(True)
        try:
            tree = gds_pads.structure_tree(self._gds)
        except Exception as exc:
            tree = []
            self._summary.setText(f"Could not read the structure: {exc}")
        finally:
            self._busy(False)

        self._tree.blockSignals(True)
        for node in tree:
            self._add_cell(self._cell_root, node)
        self._tree.blockSignals(False)
        self._cell_root.setExpanded(True)

    def _add_cell(self, parent, node):
        item = QtWidgets.QTreeWidgetItem(parent, [
            node["name"],
            f"{node['instances']:,}",
            "",
            f"{_um(node['width_mm'])} × {_um(node['height_mm'])}",
        ])
        item.setData(_NAME, QtCore.Qt.UserRole, ("cell", node["name"]))
        item.setCheckState(_NAME, QtCore.Qt.Unchecked)
        for child in node["children"]:
            self._add_cell(item, child)
        return item

    # ── selection ──────────────────────────────────────────────────────

    def _checked(self, kind):
        found = []
        iterator = QtWidgets.QTreeWidgetItemIterator(self._tree)
        while iterator.value():
            item = iterator.value()
            data = item.data(_NAME, QtCore.Qt.UserRole)
            if data and data[0] == kind and item.checkState(_NAME) == QtCore.Qt.Checked:
                found.append(data[1])
            iterator += 1
        return found

    def _selection_changed(self, *_args):
        layers = self._checked("layer")
        cells = self._checked("cell")
        estimate = 0
        iterator = QtWidgets.QTreeWidgetItemIterator(self._tree)
        while iterator.value():
            item = iterator.value()
            data = item.data(_NAME, QtCore.Qt.UserRole)
            if data and item.checkState(_NAME) == QtCore.Qt.Checked:
                text = item.text(_PADLIKE if data[0] == "layer" else _COUNT)
                estimate += int(text.replace(",", "") or 0)
            iterator += 1

        if not layers and not cells:
            self._summary.setText("Nothing picked yet.")
        else:
            what = []
            if layers:
                what.append(f"{len(layers)} layer(s)")
            if cells:
                what.append(f"{len(cells)} cell(s)")
            self._summary.setText(
                f"{' and '.join(what)} picked — about {estimate:,} pad(s). "
                f"Pads landing on the same spot are counted once.")
        if self._ok is not None:
            self._ok.setEnabled(bool(layers or cells))

    # ── result ─────────────────────────────────────────────────────────

    def layer_keys(self):
        return self._checked("layer")

    def cell_names(self):
        return self._checked("cell")

    def size_bounds_mm(self):
        return self._bounds_mm()

    def replace_existing(self):
        return self._replace.isChecked()
