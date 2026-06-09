# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
from compat import QtWidgets

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