# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Choose the technology for one import.

The Technology Configuration dialog configures the session: one active PDK,
which is right when every chip in the package comes from the same process.
A multi-die or stacked package routinely does not — a SKY130 die next to an
SG13G2 die — and then the question "which PDK" has to be asked per import,
not per session, because the answer differs per die and has to be recorded
on the die (see core.gds_tech).

So this dialog is shown at import time, starting from the session's active
profile: with one PDK configured it is one Enter press, and with several it
is one click. The files can also be pointed at directly, for a PDK that has
not been set up as a profile at all.

Returns a technology in core.gds_tech's shape, or None if cancelled.
"""

import os

from compat import QtWidgets, QtCore

import core.gds_tech as gds_tech

_CUSTOM = "Choose the files myself…"


class TechnologyDialog(QtWidgets.QDialog):
    """Pick the PDK this GDS is to be imported with."""

    def __init__(self, parent=None, gds_name="", technology=None, config=None,
                 require_lyp=False):
        super().__init__(parent)
        self.setWindowTitle("Technology for this import")
        self._require_lyp = require_lyp
        self._profiles = gds_tech.config_profiles(config)
        self._starting = technology or gds_tech.from_config(None, config)

        outer = QtWidgets.QVBoxLayout(self)

        if gds_name:
            head = QtWidgets.QLabel(f"<b>{os.path.basename(gds_name)}</b>")
            head.setTextFormat(QtCore.Qt.RichText)
            outer.addWidget(head)

        outer.addWidget(QtWidgets.QLabel(
            "Which technology is this chip made in? It is recorded on the "
            "chip, so dies from different PDKs can sit in one package."))

        self._combo = QtWidgets.QComboBox()
        for profile in self._profiles:
            self._combo.addItem(profile["name"] or "(unnamed)")
        self._combo.addItem(_CUSTOM)
        outer.addWidget(self._combo)

        self._description = QtWidgets.QLabel("")
        self._description.setStyleSheet("color: #888; font-size: 12px;")
        self._description.setWordWrap(True)
        outer.addWidget(self._description)

        box = QtWidgets.QGroupBox("PDK files")
        form = QtWidgets.QFormLayout(box)
        self._lyp = self._file_row(form, "Layer properties (.lyp):",
                                   "LYP Files (*.lyp *.LYP)")
        self._map = self._file_row(form, "Layer map (.map):",
                                   "MAP Files (*.map *.MAP)")
        self._xml = self._file_row(form, "Stackup (.xml):",
                                   "XML Files (*.xml *.XML)")
        outer.addWidget(box)

        self._note = QtWidgets.QLabel("")
        self._note.setWordWrap(True)
        outer.addWidget(self._note)

        self._remember = QtWidgets.QCheckBox(
            "Also use this for the rest of the session")
        self._remember.setChecked(True)
        self._remember.setToolTip(
            "The next import starts from this technology. The chip records "
            "it either way.")
        outer.addWidget(self._remember)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)

        self._combo.currentIndexChanged.connect(self._profile_chosen)
        self._select_starting()
        self._refresh()

    # ── construction helpers ───────────────────────────────────────────

    def _file_row(self, form, label, file_filter):
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QtWidgets.QLineEdit()
        edit.setMinimumWidth(320)
        edit.textChanged.connect(self._refresh)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(lambda: self._browse(edit, label, file_filter))
        layout.addWidget(edit)
        layout.addWidget(browse)
        form.addRow(label, row)
        return edit

    def _browse(self, edit, title, file_filter):
        start = os.path.dirname(edit.text()) if edit.text() else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, title.rstrip(":"), start, file_filter)
        if path:
            edit.setText(path)

    def _select_starting(self):
        name = self._starting.get("name", "")
        for index, profile in enumerate(self._profiles):
            if profile["name"] == name:
                self._combo.setCurrentIndex(index)
                self._fill(self._starting)
                return
        self._combo.setCurrentIndex(self._combo.count() - 1)
        self._fill(self._starting)

    def _fill(self, technology):
        self._lyp.setText(technology.get("lyp_path", "") or "")
        self._map.setText(technology.get("map_path", "") or "")
        self._xml.setText(technology.get("xml_path", "") or "")

    # ── state ──────────────────────────────────────────────────────────

    def _profile_chosen(self, index):
        if 0 <= index < len(self._profiles):
            self._fill(self._profiles[index])
            self._description.setText(self._profiles[index].get("description", ""))
        else:
            self._description.setText("")
        self._refresh()

    def _missing(self):
        return [label for label, edit in (("LYP", self._lyp), ("MAP", self._map),
                                          ("XML", self._xml))
                if edit.text() and not os.path.isfile(edit.text())]

    def _refresh(self, *_args):
        missing = self._missing()
        messages = []
        if missing:
            messages.append("Not found: " + ", ".join(missing) + ".")
        if not self._xml.text():
            messages.append("Without a stackup the die thickness is a guess "
                            "and layers have no height.")
        if not self._lyp.text():
            messages.append("Without layer properties the layers have no "
                            "names or colours.")
        self._note.setText(" ".join(messages))
        self._note.setStyleSheet("color: %s; font-size: 12px;"
                                 % ("#c0392b" if missing else "#888"))
        if self._ok is not None:
            blocked = bool(missing) or (self._require_lyp
                                        and not os.path.isfile(self._lyp.text()))
            self._ok.setEnabled(not blocked)

    # ── result ─────────────────────────────────────────────────────────

    def technology(self):
        index = self._combo.currentIndex()
        name = (self._profiles[index]["name"] if 0 <= index < len(self._profiles)
                else "Custom")
        return gds_tech.profile(name, self._lyp.text(), self._map.text(),
                                self._xml.text())

    def remember(self):
        return self._remember.isChecked()


def choose_technology(parent=None, gds_name="", technology=None,
                      require_lyp=False):
    """
    Ask for this import's technology. Returns a profile, or None if cancelled.

    When the user asks for it to be kept, the session's configuration is
    updated too, so the next import starts there and everything that still
    reads the session config agrees with what was just imported.
    """
    dialog = TechnologyDialog(parent, gds_name, technology,
                              require_lyp=require_lyp)
    if dialog.exec_() != QtWidgets.QDialog.Accepted:
        return None
    technology = dialog.technology()
    if dialog.remember():
        try:
            from core.TechConfig import tech_config
            tech_config.set_local(lyp=technology["lyp_path"],
                                  map_=technology["map_path"],
                                  xml=technology["xml_path"])
        except Exception:
            pass
    return technology
