"""
Leadframe Configuration Dialog
Supports QFN, QFP, and BGA package types with die paddle and realistic lead geometry.
"""

from compat import QtWidgets, QtCore


class LeadframeConfigurator(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Leadframe Configuration")
        self.setMinimumWidth(440)

        root = QtWidgets.QVBoxLayout(self)

        # ── Library shortcut ───────────────────────────────────────────────
        lib_row = QtWidgets.QHBoxLayout()
        btn_lib = QtWidgets.QPushButton("📦  From library…")
        btn_lib.setToolTip(
            "Browse the built-in JEDEC / IPC package catalogue and\n"
            "pre-fill all parameters from a standard package definition."
        )
        btn_lib.clicked.connect(self._open_library)
        self._lbl_pkg = QtWidgets.QLabel("")
        self._lbl_pkg.setStyleSheet("color: #93c5fd; font-size: 10px;")
        lib_row.addWidget(btn_lib)
        lib_row.addWidget(self._lbl_pkg, 1)
        root.addLayout(lib_row)

        # ── Package type ───────────────────────────────────────────────────
        type_box = QtWidgets.QGroupBox("Package Type")
        type_form = QtWidgets.QFormLayout(type_box)
        self.frame_type = QtWidgets.QComboBox()
        self.frame_type.addItems([
            "QFN (Quad Flat No-lead)",
            "QFP (Quad Flat Package)",
            "BGA (Ball Grid Array)",
        ])
        type_form.addRow("Type:", self.frame_type)
        root.addWidget(type_box)

        # ── Package body dimensions ────────────────────────────────────────
        body_box = QtWidgets.QGroupBox("Package Body")
        body_form = QtWidgets.QFormLayout(body_box)

        self.frame_length = self._dspin(1.0, 1000.0, 5.0, " mm", 0.5)
        self.frame_width  = self._dspin(1.0, 1000.0, 5.0, " mm", 0.5)
        self.frame_thickness = self._dspin(0.05, 5.0, 0.20, " mm", 0.05)
        self.material_combo = QtWidgets.QComboBox()
        self.material_combo.addItems(["Copper", "Alloy 42", "Silver"])

        body_form.addRow("Length (X):", self.frame_length)
        body_form.addRow("Width  (Y):", self.frame_width)
        body_form.addRow("Thickness:", self.frame_thickness)
        body_form.addRow("Material:", self.material_combo)
        root.addWidget(body_box)

        # ── QFN / QFP section ──────────────────────────────────────────────
        self.qfnqfp_box = QtWidgets.QGroupBox("Lead Fingers  (QFN / QFP)")
        qfnqfp = QtWidgets.QVBoxLayout(self.qfnqfp_box)

        # Lead counts (2-column grid)
        counts_form = QtWidgets.QFormLayout()
        self.left_lead_count   = self._spin(0, 128, 4)
        self.right_lead_count  = self._spin(0, 128, 4)
        self.top_lead_count    = self._spin(0, 128, 4)
        self.bottom_lead_count = self._spin(0, 128, 4)
        counts_form.addRow("Left leads:",   self.left_lead_count)
        counts_form.addRow("Right leads:",  self.right_lead_count)
        counts_form.addRow("Top leads:",    self.top_lead_count)
        counts_form.addRow("Bottom leads:", self.bottom_lead_count)
        qfnqfp.addLayout(counts_form)

        # Lead dimensions
        dims_form = QtWidgets.QFormLayout()
        self.lead_width        = self._dspin(0.05, 5.0, 0.25, " mm", 0.05)
        self.lead_pitch        = self._dspin(0.10, 10.0, 0.50, " mm", 0.05)
        self.inner_lead_length = self._dspin(0.10, 10.0, 0.50, " mm", 0.05)
        self.lead_length       = self._dspin(0.10, 10.0, 0.80, " mm", 0.05)  # QFP outer
        dims_form.addRow("Lead width:",             self.lead_width)
        dims_form.addRow("Lead pitch (c-to-c):",    self.lead_pitch)
        dims_form.addRow("Inner finger length:",    self.inner_lead_length)

        # QFP-only outer lead length — hidden for QFN
        self.outer_lead_row_label = QtWidgets.QLabel("Outer lead length (QFP):")
        dims_form.addRow(self.outer_lead_row_label, self.lead_length)
        qfnqfp.addLayout(dims_form)

        # Span hint label
        self.span_hint = QtWidgets.QLabel()
        self.span_hint.setStyleSheet("color: grey; font-size: 10px;")
        qfnqfp.addWidget(self.span_hint)
        root.addWidget(self.qfnqfp_box)

        # ── Die paddle section (QFN / QFP) ────────────────────────────────
        self.paddle_box = QtWidgets.QGroupBox("Die Paddle")
        self.paddle_box.setCheckable(True)
        self.paddle_box.setChecked(True)
        paddle_form = QtWidgets.QFormLayout(self.paddle_box)

        self.die_paddle_length = self._dspin(0.5, 500.0, 3.0, " mm", 0.1)
        self.die_paddle_width  = self._dspin(0.5, 500.0, 3.0, " mm", 0.1)
        paddle_form.addRow("Paddle length:", self.die_paddle_length)
        paddle_form.addRow("Paddle width:",  self.die_paddle_width)
        root.addWidget(self.paddle_box)

        # ── BGA section ────────────────────────────────────────────────────
        self.bga_box = QtWidgets.QGroupBox("BGA Balls")
        bga_form = QtWidgets.QFormLayout(self.bga_box)
        self.bga_ball_diameter = self._dspin(0.05, 2.0, 0.40, " mm", 0.05)
        self.bga_ball_pitch    = self._dspin(0.10, 5.0, 0.80, " mm", 0.05)
        bga_form.addRow("Ball diameter:", self.bga_ball_diameter)
        bga_form.addRow("Ball pitch:",    self.bga_ball_pitch)

        # Info label showing computed grid
        self.bga_hint = QtWidgets.QLabel()
        self.bga_hint.setStyleSheet("color: grey; font-size: 10px;")
        bga_form.addRow(self.bga_hint)
        root.addWidget(self.bga_box)

        # ── Buttons ────────────────────────────────────────────────────────
        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

        # ── Connections ────────────────────────────────────────────────────
        self.frame_type.currentIndexChanged.connect(self._update_visibility)
        for w in (self.left_lead_count, self.right_lead_count,
                  self.top_lead_count, self.bottom_lead_count,
                  self.lead_pitch, self.frame_length, self.frame_width):
            w.valueChanged.connect(self._update_hints)
        for w in (self.bga_ball_pitch, self.frame_length, self.frame_width):
            w.valueChanged.connect(self._update_bga_hint)
        # Propagate frame size to die paddle defaults
        self.frame_length.valueChanged.connect(self._sync_paddle_defaults)
        self.frame_width.valueChanged.connect(self._sync_paddle_defaults)

        self._update_visibility()
        self._update_hints()
        self._update_bga_hint()

    # ── library integration ────────────────────────────────────────────────

    def _open_library(self):
        """Open the JEDEC package browser and apply the chosen package."""
        from ui.PackageSelectorDialog import PackageSelectorDialog
        dlg = PackageSelectorDialog(self)
        if dlg.exec_() == PackageSelectorDialog.Accepted:
            pkg = dlg.selected_package()
            if pkg is not None:
                self.apply_package(pkg)

    def apply_package(self, pkg):
        """
        Pre-fill all configurator widgets from a PackageSpec.
        Called by the library browser or externally (e.g. session replay).
        """
        from leadframe.PackageDatabase import PackageSpec

        # Package type
        type_map = {
            "QFN":   "QFN (Quad Flat No-lead)",
            "QFP":   "QFP (Quad Flat Package)",
            "DIP":   "QFP (Quad Flat Package)",   # DIP uses 2-sided QFP geometry
            "SOIC":  "QFP (Quad Flat Package)",
            "TSSOP": "QFP (Quad Flat Package)",
            "BGA":   "BGA (Ball Grid Array)",
        }
        ft = type_map.get(pkg.family, "QFN (Quad Flat No-lead)")
        idx = self.frame_type.findText(ft)
        if idx >= 0:
            self.frame_type.setCurrentIndex(idx)

        # Body
        self.frame_length.setValue(pkg.body_length_mm)
        self.frame_width.setValue(pkg.body_width_mm)
        self.frame_thickness.setValue(pkg.body_height_mm)

        if pkg.family in ("QFN", "QFP", "DIP", "SOIC", "TSSOP"):
            self.left_lead_count.setValue(pkg.pins_lr)
            self.right_lead_count.setValue(pkg.pins_lr)
            self.top_lead_count.setValue(pkg.pins_tb)
            self.bottom_lead_count.setValue(pkg.pins_tb)
            self.lead_pitch.setValue(pkg.lead_pitch_mm)
            self.lead_width.setValue(pkg.lead_width_mm)
            self.inner_lead_length.setValue(pkg.inner_lead_mm)
            self.lead_length.setValue(pkg.outer_lead_mm)

            has_paddle = pkg.has_die_paddle
            self.paddle_box.setChecked(has_paddle)
            if has_paddle:
                pl = pkg.paddle_length_mm or round(pkg.body_length_mm * 0.55, 3)
                pw = pkg.paddle_width_mm  or round(pkg.body_width_mm  * 0.55, 3)
                self.die_paddle_length.setValue(pl)
                self.die_paddle_width.setValue(pw)

        elif pkg.family == "BGA":
            self.bga_ball_diameter.setValue(pkg.bga_ball_dia_mm)
            self.bga_ball_pitch.setValue(pkg.bga_ball_pitch_mm)

        # Label
        self._lbl_pkg.setText(f"← {pkg.name}  ({pkg.standard})")

        self._update_visibility()
        self._update_hints()
        self._update_bga_hint()

    # ── widget factories ───────────────────────────────────────────────────

    @staticmethod
    def _dspin(lo, hi, val, suffix="", step=0.1):
        w = QtWidgets.QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setValue(val)
        w.setSingleStep(step)
        w.setDecimals(3)
        if suffix:
            w.setSuffix(suffix)
        return w

    @staticmethod
    def _spin(lo, hi, val):
        w = QtWidgets.QSpinBox()
        w.setRange(lo, hi)
        w.setValue(val)
        return w

    # ── dynamic UI helpers ─────────────────────────────────────────────────

    def _frame_type(self) -> str:
        return self.frame_type.currentText()

    def _update_visibility(self):
        t = self._frame_type()
        is_qfnqfp = t in ("QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)")
        is_qfp    = (t == "QFP (Quad Flat Package)")
        is_bga    = (t == "BGA (Ball Grid Array)")

        self.qfnqfp_box.setVisible(is_qfnqfp)
        self.paddle_box.setVisible(is_qfnqfp)
        self.bga_box.setVisible(is_bga)
        self.outer_lead_row_label.setVisible(is_qfp)
        self.lead_length.setVisible(is_qfp)
        self.adjustSize()

    def _update_hints(self):
        """Show how much of each side's width the selected leads will occupy."""
        n_lr  = max(self.left_lead_count.value(), self.right_lead_count.value())
        n_tb  = max(self.top_lead_count.value(), self.bottom_lead_count.value())
        pitch = self.lead_pitch.value()
        fl    = self.frame_length.value()
        fw    = self.frame_width.value()

        span_lr = (n_lr - 1) * pitch if n_lr > 0 else 0.0
        span_tb = (n_tb - 1) * pitch if n_tb > 0 else 0.0

        warn_lr = " !" if span_lr > fw else ""
        warn_tb = " !" if span_tb > fl else ""
        self.span_hint.setText(
            f"Left/Right span: {span_lr:.2f} mm  (frame width {fw:.2f} mm){warn_lr}   "
            f"Top/Bottom span: {span_tb:.2f} mm  (frame length {fl:.2f} mm){warn_tb}"
        )

    def _update_bga_hint(self):
        import math
        pitch = self.bga_ball_pitch.value()
        nx    = max(1, round(self.frame_length.value() / pitch))
        ny    = max(1, round(self.frame_width.value()  / pitch))
        self.bga_hint.setText(f"Grid: {nx} × {ny} = {nx * ny} balls")

    def _sync_paddle_defaults(self):
        """Keep paddle defaults at ~55% of frame whenever frame size changes."""
        self.die_paddle_length.setValue(round(self.frame_length.value() * 0.55, 3))
        self.die_paddle_width.setValue( round(self.frame_width.value()  * 0.55, 3))

    # ── validation ─────────────────────────────────────────────────────────

    def accept(self):
        t      = self._frame_type()
        errors = []

        if t in ("QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)"):
            lw    = self.lead_width.value()
            lp    = self.lead_pitch.value()
            ill   = self.inner_lead_length.value()
            fl    = self.frame_length.value()
            fw    = self.frame_width.value()

            if lw >= lp:
                errors.append(
                    f"Lead width ({lw} mm) must be less than lead pitch ({lp} mm)."
                )

            span_lr = (max(self.left_lead_count.value(),
                           self.right_lead_count.value()) - 1) * lp
            span_tb = (max(self.top_lead_count.value(),
                           self.bottom_lead_count.value()) - 1) * lp

            if span_lr > fw:
                errors.append(
                    f"Left/right lead span ({span_lr:.2f} mm) exceeds frame width ({fw} mm)."
                )
            if span_tb > fl:
                errors.append(
                    f"Top/bottom lead span ({span_tb:.2f} mm) exceeds frame length ({fl} mm)."
                )
            if ill >= fl / 2:
                errors.append(
                    f"Inner lead length ({ill} mm) must be less than half frame length ({fl/2} mm)."
                )
            if ill >= fw / 2:
                errors.append(
                    f"Inner lead length ({ill} mm) must be less than half frame width ({fw/2} mm)."
                )

            if self.paddle_box.isChecked():
                dp_l = self.die_paddle_length.value()
                dp_w = self.die_paddle_width.value()
                # Check that paddle doesn't reach lead inner tips (need at least 0.05 mm gap)
                gap_l = fl / 2 - ill - dp_l / 2
                gap_w = fw / 2 - ill - dp_w / 2
                if gap_l < 0.05:
                    errors.append(
                        f"Die paddle length ({dp_l} mm) overlaps with lead fingers on left/right "
                        f"(gap = {gap_l:.3f} mm). Reduce paddle or inner lead length."
                    )
                if gap_w < 0.05:
                    errors.append(
                        f"Die paddle width ({dp_w} mm) overlaps with lead fingers on top/bottom "
                        f"(gap = {gap_w:.3f} mm). Reduce paddle or inner lead length."
                    )

        elif t == "BGA (Ball Grid Array)":
            if self.bga_ball_diameter.value() >= self.bga_ball_pitch.value():
                errors.append(
                    f"Ball diameter ({self.bga_ball_diameter.value()} mm) must be less than "
                    f"ball pitch ({self.bga_ball_pitch.value()} mm)."
                )

        if errors:
            QtWidgets.QMessageBox.warning(
                self, "Invalid Configuration", "\n\n".join(errors)
            )
            return

        super().accept()

    # ── config dict ────────────────────────────────────────────────────────

    def get_config(self) -> dict:
        t = self._frame_type()
        cfg = {
            "frame_type":      t,
            "frame_length":    self.frame_length.value(),
            "frame_width":     self.frame_width.value(),
            "frame_thickness": self.frame_thickness.value(),
            "material":        self.material_combo.currentText(),
        }

        if t in ("QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)"):
            cfg.update({
                "left_lead_count":   self.left_lead_count.value(),
                "right_lead_count":  self.right_lead_count.value(),
                "top_lead_count":    self.top_lead_count.value(),
                "bottom_lead_count": self.bottom_lead_count.value(),
                "lead_width":        self.lead_width.value(),
                "lead_pitch":        self.lead_pitch.value(),
                "inner_lead_length": self.inner_lead_length.value(),
                "lead_length":       self.lead_length.value(),
                "qfn_pad_thickness": self.frame_thickness.value(),  # compat
                "has_die_paddle":    self.paddle_box.isChecked(),
                "die_paddle_length": self.die_paddle_length.value(),
                "die_paddle_width":  self.die_paddle_width.value(),
            })

        elif t == "BGA (Ball Grid Array)":
            cfg.update({
                "bga_ball_diameter": self.bga_ball_diameter.value(),
                "bga_ball_pitch":    self.bga_ball_pitch.value(),
            })

        return cfg
