# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Confirm a chip proxy's outer dimensions before the block is built.

A proxy deliberately throws away the layout and keeps only the box, so the
box is the whole of what it asserts. Two of its three numbers can be read
straight out of the GDS, but the third cannot: die thickness is not in a GDS
at all. It comes from the stackup XML, and without one the import falls back
to a flat 0.3 mm guess — which used to be delivered as a warning after the
block already existed.

So this dialog shows all three with their provenance and lets any of them be
corrected before anything is created. Values that came from a PDK are marked
as such; values that were guessed say so plainly.
"""

import os

from compat import QtWidgets, QtCore

import core.chip_proxy as chip_proxy

# How thickness provenance reads to a user, and whether it is trustworthy.
_THICKNESS_WORDING = {
    "xml_with_substrate": (
        "from the stackup XML, including its substrate offset", True),
    "xml_interconnect_plus_default_substrate": (
        "interconnect from the stackup XML + a %g um default substrate"
        % chip_proxy.DEFAULT_SUBSTRATE_THICKNESS_UM, False),
    "no_stackup_default": (
        "a flat default — no stackup XML was supplied, and a GDS does not "
        "record die thickness", False),
    "entered_by_hand": ("entered by hand", True),
}


def describe_thickness(source: str):
    """(wording, trustworthy) for a thickness_source value."""
    return _THICKNESS_WORDING.get(
        source, (source or "unknown", False))


class ChipDimensionsDialog(QtWidgets.QDialog):
    """Review and, if needed, correct width / length / thickness."""

    def __init__(self, proxy_data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chip Proxy Dimensions")
        self._data = proxy_data

        x0, y0, x1, y1 = proxy_data["footprint_mm"]
        self._detected = (x1 - x0, y1 - y0, proxy_data["thickness_mm"])

        outer = QtWidgets.QVBoxLayout(self)

        gds = proxy_data.get("source_gds") or ""
        head = QtWidgets.QLabel(
            f"<b>{os.path.basename(gds)}</b><br>"
            f"top cell <tt>{proxy_data.get('die_cell', '?')}</tt> &middot; "
            f"{len(proxy_data.get('pads') or [])} bond pad(s) found")
        head.setTextFormat(QtCore.Qt.RichText)
        outer.addWidget(head)

        box = QtWidgets.QGroupBox("Outer dimensions")
        form = QtWidgets.QFormLayout(box)
        self._w = self._spin(self._detected[0])
        self._l = self._spin(self._detected[1])
        self._t = self._spin(self._detected[2])
        form.addRow("Width (X), mm:", self._w)
        form.addRow("Length (Y), mm:", self._l)
        form.addRow("Thickness (Z), mm:", self._t)
        outer.addWidget(box)

        outline_src = proxy_data.get("footprint_source") or "unknown"
        outer.addWidget(self._note(
            f"Width and length: {outline_src}.", True))

        wording, trusted = describe_thickness(proxy_data.get("thickness_source"))
        outer.addWidget(self._note(f"Thickness: {wording}.", trusted))

        cands = proxy_data.get("footprint_candidates") or []
        if len(cands) > 1:
            lines = "<br>".join(
                f"&middot; {c['name']} ({c['layer']}/{c['datatype']}): "
                f"{c['footprint_mm'][2] - c['footprint_mm'][0]:.4f} &times; "
                f"{c['footprint_mm'][3] - c['footprint_mm'][1]:.4f} mm"
                for c in cands)
            more = QtWidgets.QLabel(
                "<small>Outline layers found in this layout:<br>"
                f"{lines}</small>")
            more.setTextFormat(QtCore.Qt.RichText)
            outer.addWidget(more)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        reset = buttons.addButton("Reset to detected",
                                   QtWidgets.QDialogButtonBox.ResetRole)
        reset.clicked.connect(self._reset)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    @staticmethod
    def _spin(value):
        s = QtWidgets.QDoubleSpinBox()
        s.setDecimals(4)
        # A die is millimetres; the lower bound only has to exclude zero,
        # which would make a degenerate block.
        s.setRange(0.0001, 1000.0)
        s.setSingleStep(0.01)
        s.setSuffix(" mm")
        s.setValue(float(value))
        return s

    @staticmethod
    def _note(text, trusted):
        lbl = QtWidgets.QLabel(("" if trusted else "⚠  ") + text)
        lbl.setWordWrap(True)
        if not trusted:
            lbl.setStyleSheet("QLabel { color: #e0a030; }")
        return lbl

    def _reset(self):
        self._w.setValue(self._detected[0])
        self._l.setValue(self._detected[1])
        self._t.setValue(self._detected[2])

    def values(self):
        return self._w.value(), self._l.value(), self._t.value()

    def apply_to(self, proxy_data: dict) -> dict:
        """Fold whatever the user settled on back into the extraction."""
        w, l, t = self.values()
        dw, dl, dt = self._detected
        # Pass None for anything left untouched, so the provenance keeps
        # saying "measured" rather than claiming it was typed in.
        return chip_proxy.apply_dimension_overrides(
            proxy_data,
            width_mm=None if abs(w - dw) < 1e-9 else w,
            length_mm=None if abs(l - dl) < 1e-9 else l,
            thickness_mm=None if abs(t - dt) < 1e-9 else t,
        )
