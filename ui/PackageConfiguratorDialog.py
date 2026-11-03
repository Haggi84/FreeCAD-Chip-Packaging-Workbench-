# SPDX-License-Identifier: GPL-3.0-or-later
"""Requirements-driven package selection dialog."""

from compat import QtWidgets, QtCore
from core.package_configurator import (
    ChipInfo, PackageRequirements, extract_chip_info, recommend_packages,
)
from leadframe.PackageDatabase import families


class PackageConfiguratorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, document=None):
        super().__init__(parent)
        self.setWindowTitle("Select Package")
        self.setMinimumSize(820, 650)
        self._document = document
        self._recommendations = []
        self._build_ui()
        self._load_detected_chip()
        self._recommend()

    def _spin(self, minimum=0.0, maximum=1000.0, value=0.0):
        w = QtWidgets.QDoubleSpinBox()
        w.setRange(minimum, maximum)
        w.setValue(value)
        w.setDecimals(3)
        w.setSuffix(" mm")
        return w

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        detected = QtWidgets.QGroupBox("Chip information (GDS detected where available)")
        form = QtWidgets.QFormLayout(detected)
        self.chip_source = QtWidgets.QLabel()
        self.chip_length = self._spin()
        self.chip_width = self._spin()
        self.chip_thickness = self._spin(0, 100, 0)
        self.chip_pads = QtWidgets.QSpinBox()
        self.chip_pads.setRange(0, 100000)
        for label, widget in (
            ("Source:", self.chip_source),
            ("Die length:", self.chip_length),
            ("Die width:", self.chip_width),
            ("Die thickness:", self.chip_thickness),
            ("Bond pads / electrical pins:", self.chip_pads),
        ):
            form.addRow(label, widget)
        root.addWidget(detected)

        constraints = QtWidgets.QGroupBox("Package Size Constraints")
        cform = QtWidgets.QFormLayout(constraints)
        self.max_length = self._spin()
        self.max_width = self._spin()
        self.max_height = self._spin(0, 100, 0)
        self.min_pitch = self._spin(0, 10, 0)
        self.family = QtWidgets.QComboBox()
        self.family.addItem("Select", "")
        for item in families():
            self.family.addItem(item, item)
        self.mounting = QtWidgets.QComboBox()
        self.mounting.addItems(["Select", "Surface mount", "Through hole"])
        for label, widget in (
            ("Maximum package length:", self.max_length),
            ("Maximum package width:", self.max_width),
            ("Maximum package height:", self.max_height),
            ("Minimum supported package pitch:", self.min_pitch),
        ):
            cform.addRow(label, widget)
        root.addWidget(constraints)

        other = QtWidgets.QGroupBox("Other Constraints")
        oform = QtWidgets.QFormLayout(other)
        self.thermal = QtWidgets.QComboBox()
        self.thermal.addItems(["Select", "Low-Moderate", "Moderate", "Good", "Excellent"])
        self.electrical = QtWidgets.QComboBox()
        self.electrical.addItems(["Select", "Low", "Good", "Excellent"])
        self.rework = QtWidgets.QComboBox()
        self.rework.addItems(["Select", "Easy", "Medium", "Hard", "Very Hard"])
        self.cost = QtWidgets.QComboBox()
        self.cost.addItems(["Select", "Low", "Medium", "High"])
        for label, widget in (
            ("Thermal performance:", self.thermal),
            ("Electrical performance:", self.electrical),
            ("Rework:", self.rework),
            ("Cost:", self.cost),
            ("Mounting:", self.mounting),
            ("Recommended family:", self.family),
        ):
            oform.addRow(label, widget)
        root.addWidget(other)

        self.results = QtWidgets.QTableWidget(0, 5)
        self.results.setHorizontalHeaderLabels(
            ["Package", "Family", "Pins", "Body (mm)", "Why it fits"])
        self.results.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.results.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.results.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.results.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.results, 1)
        self.status = QtWidgets.QLabel()
        root.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Cancel | QtWidgets.QDialogButtonBox.Ok)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Use or generate package")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        for widget in (self.max_length, self.max_width, self.max_height,
                       self.min_pitch, self.family, self.thermal,
                       self.electrical, self.rework, self.cost, self.mounting):
            signal = getattr(widget, "valueChanged", None) or getattr(widget, "currentIndexChanged", None)
            if signal:
                signal.connect(self._recommend)

    def _load_detected_chip(self):
        try:
            import FreeCAD
            info = extract_chip_info(self._document or FreeCAD.activeDocument())
        except ImportError:
            info = ChipInfo()
        self.chip_source.setText(info.source)
        self.chip_length.setValue(info.length_mm)
        self.chip_width.setValue(info.width_mm)
        self.chip_thickness.setValue(info.thickness_mm)
        self.chip_pads.setValue(info.pad_count)

    def _requirements(self):
        def optional(widget):
            value = widget.value()
            return value if value > 0 else None
        return ChipInfo(self.chip_length.value(), self.chip_width.value(),
                        self.chip_thickness.value(), self.chip_pads.value(),
                        self.chip_source.text()), PackageRequirements(
            max_length_mm=optional(self.max_length),
            max_width_mm=optional(self.max_width),
            max_height_mm=optional(self.max_height),
            min_supported_pitch_mm=optional(self.min_pitch),
            thermal_performance=(
                "" if self.thermal.currentText() == "Select"
                else self.thermal.currentText()
            ),
            electrical_performance=(
                "" if self.electrical.currentText() == "Select"
                else self.electrical.currentText()
            ),
            rework="" if self.rework.currentText() == "Select" else self.rework.currentText(),
            cost="" if self.cost.currentText() == "Select" else self.cost.currentText(),
            preferred_family=self.family.currentData() or "",
            mounting=(
                "" if self.mounting.currentText() == "Select"
                else self.mounting.currentText()
            ),
        )

    def _recommend(self):
        chip, req = self._requirements()
        self._recommendations = recommend_packages(chip, req)
        self.results.setRowCount(0)
        for recommendation in self._recommendations:
            pkg = recommendation.package
            row = self.results.rowCount()
            self.results.insertRow(row)
            body = f"{pkg.body_length_mm} × {pkg.body_width_mm} × {pkg.body_height_mm}"
            values = [pkg.name, pkg.family, str(pkg.total_pins), body,
                      f"{recommendation.score:.1f} — "
                      f"{', '.join(recommendation.reasons) or 'compatible'}"]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, recommendation)
                self.results.setItem(row, col, item)
        self.status.setText(f"{len(self._recommendations)} compatible package(s) found.")

    def selected_package(self):
        items = self.results.selectedItems()
        return items[0].data(QtCore.Qt.UserRole).package if items else None

    def get_config(self):
        chip, requirements = self._requirements()
        package = self.selected_package()
        return {"package": package, "chip": chip, "requirements": requirements}

    def accept(self):
        if self.selected_package() is None:
            QtWidgets.QMessageBox.warning(self, "Select a package",
                                          "Select a recommended package first.")
            return
        super().accept()
