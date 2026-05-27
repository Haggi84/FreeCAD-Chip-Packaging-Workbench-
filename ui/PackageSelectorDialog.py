"""
PackageSelectorDialog
=====================
Modal search dialog over the local JEDEC / IPC package catalogue.

Opens from a "From library…" button inside LeadframeConfigurator.
On accept it returns a ``PackageSpec`` that the configurator uses to
pre-fill all its spinboxes in one shot.

Layout
------
  ┌────────────────────────────────────────────────────────────┐
  │  Family: [All ▼]   Search: [___________] [🔍 Search]       │
  ├────────────────────────────────────────────────────────────┤
  │  Name         Pins  Body (mm)      Pitch  Standard         │
  │  QFN-24        24   4×4×0.9        0.50   JEDEC MO-220     │
  │  QFN-32        32   5×5×0.9        0.50   JEDEC MO-220     │
  │  …                                                          │
  ├────────────────────────────────────────────────────────────┤
  │  Description / detail of selected row                      │
  ├────────────────────────────────────────────────────────────┤
  │                                [Cancel]  [Use this package] │
  └────────────────────────────────────────────────────────────┘
"""

from compat import QtWidgets, QtCore, QtGui
from leadframe.PackageDatabase import (
    ALL_PACKAGES, search_packages, families, PackageSpec
)


class PackageSelectorDialog(QtWidgets.QDialog):
    """Select an IC package from the local JEDEC catalogue."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IC Package Library")
        self.setMinimumWidth(680)
        self.setMinimumHeight(480)
        self._selected: PackageSpec | None = None
        self._build_ui()
        self._populate_table(ALL_PACKAGES)

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)

        # ── search bar ────────────────────────────────────────────────
        bar = QtWidgets.QHBoxLayout()

        bar.addWidget(QtWidgets.QLabel("Family:"))
        self._cb_family = QtWidgets.QComboBox()
        self._cb_family.addItem("All families")
        for f in families():
            self._cb_family.addItem(f)
        self._cb_family.setFixedWidth(110)
        bar.addWidget(self._cb_family)

        bar.addSpacing(8)
        bar.addWidget(QtWidgets.QLabel("Search:"))
        self._le_search = QtWidgets.QLineEdit()
        self._le_search.setPlaceholderText("name, pin count, tag …")
        self._le_search.returnPressed.connect(self._on_search)
        bar.addWidget(self._le_search, 1)

        btn_search = QtWidgets.QPushButton("🔍")
        btn_search.setFixedWidth(32)
        btn_search.clicked.connect(self._on_search)
        bar.addWidget(btn_search)

        btn_all = QtWidgets.QPushButton("Show all")
        btn_all.setFixedWidth(70)
        btn_all.clicked.connect(self._show_all)
        bar.addWidget(btn_all)

        root.addLayout(bar)

        # ── results table ─────────────────────────────────────────────
        self._table = QtWidgets.QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Family", "Pins", "Body (mm)", "Pitch (mm)", "Standard"]
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            4, QtWidgets.QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet("font-size: 10px;")
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.doubleClicked.connect(self._on_double_click)
        root.addWidget(self._table, 1)

        # ── detail label ──────────────────────────────────────────────
        self._lbl_detail = QtWidgets.QLabel("Select a package to see details.")
        self._lbl_detail.setStyleSheet(
            "background: #1a1a2e; color: #93c5fd; padding: 6px 8px; "
            "border-radius: 4px; font-size: 10px; font-family: monospace;"
        )
        self._lbl_detail.setWordWrap(True)
        self._lbl_detail.setMinimumHeight(54)
        root.addWidget(self._lbl_detail)

        # ── result count ──────────────────────────────────────────────
        self._lbl_count = QtWidgets.QLabel("")
        self._lbl_count.setStyleSheet("color: #888; font-size: 9px;")
        root.addWidget(self._lbl_count)

        # ── buttons ───────────────────────────────────────────────────
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self._btn_use = QtWidgets.QPushButton("Use this package")
        self._btn_use.setDefault(True)
        self._btn_use.setEnabled(False)
        self._btn_use.clicked.connect(self.accept)
        btns.addButton(self._btn_use, QtWidgets.QDialogButtonBox.AcceptRole)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ── populate ──────────────────────────────────────────────────────

    def _populate_table(self, packages: list[PackageSpec]):
        tbl = self._table
        tbl.setRowCount(0)

        _white  = QtGui.QColor("#e0e0e0")
        _yellow = QtGui.QColor("#fbbf24")   # open-cavity / special tags

        for pkg in packages:
            row = tbl.rowCount()
            tbl.insertRow(row)

            body = (f"{pkg.body_length_mm}×{pkg.body_width_mm}×"
                    f"{pkg.body_height_mm}")
            pitch = (f"{pkg.bga_ball_pitch_mm}" if pkg.family == "BGA"
                     else f"{pkg.lead_pitch_mm}")

            cells = [pkg.name, pkg.family, str(pkg.total_pins), body,
                     pitch, pkg.standard]
            for col, text in enumerate(cells):
                cell = QtWidgets.QTableWidgetItem(text)
                color = _yellow if pkg.tags else _white
                cell.setForeground(QtGui.QBrush(color))
                cell.setData(QtCore.Qt.UserRole, pkg)
                tbl.setItem(row, col, cell)

        n = tbl.rowCount()
        self._lbl_count.setText(
            f"{n} package(s) found  "
            + ("  (highlighted = special tags: open-cavity, RF, MEMS…)"
               if any(p.tags for p in packages) else "")
        )
        self._btn_use.setEnabled(False)
        self._lbl_detail.setText("Select a package to see details.")

    # ── slots ─────────────────────────────────────────────────────────

    def _on_search(self):
        q = self._le_search.text().strip()
        fam_text = self._cb_family.currentText()
        fam = None if fam_text == "All families" else fam_text

        if not q and fam is None:
            self._populate_table(ALL_PACKAGES)
            return

        if q:
            results = search_packages(q, family=fam)
        else:
            results = [p for p in ALL_PACKAGES if p.family == fam]

        self._populate_table(results)

    def _show_all(self):
        self._le_search.clear()
        self._cb_family.setCurrentIndex(0)
        self._populate_table(ALL_PACKAGES)

    def _on_selection_changed(self):
        items = self._table.selectedItems()
        if not items:
            self._lbl_detail.setText("Select a package to see details.")
            self._btn_use.setEnabled(False)
            self._selected = None
            return

        pkg: PackageSpec = items[0].data(QtCore.Qt.UserRole)
        self._selected = pkg
        self._btn_use.setEnabled(True)

        if pkg.family == "BGA":
            extra = (f"Ball ⌀ {pkg.bga_ball_dia_mm} mm  |  "
                     f"Ball pitch {pkg.bga_ball_pitch_mm} mm")
        else:
            extra = (f"Lead pitch {pkg.lead_pitch_mm} mm  |  "
                     f"Lead width {pkg.lead_width_mm} mm  |  "
                     f"Inner finger {pkg.inner_lead_mm} mm")
            if pkg.pins_tb == 0:
                extra += "  |  2-sided (no top/bottom leads)"

        tags_str = f"  🏷 {', '.join(pkg.tags)}" if pkg.tags else ""
        self._lbl_detail.setText(
            f"{pkg.name}  —  {pkg.description}\n"
            f"Body: {pkg.body_length_mm}×{pkg.body_width_mm}×{pkg.body_height_mm} mm  |  "
            f"{pkg.total_pins} pins  |  {extra}  |  {pkg.standard}{tags_str}"
        )

    def _on_double_click(self, _index):
        if self._selected is not None:
            self.accept()

    # ── result ────────────────────────────────────────────────────────

    def selected_package(self) -> PackageSpec | None:
        return self._selected
