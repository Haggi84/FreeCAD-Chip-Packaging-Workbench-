"""
Technology Configuration Dialog
================================
Allows the user to manage named global technology profiles (LYP/MAP/XML paths)
and to set a session-only override without touching the global config.

Usage (from a FreeCAD command):
    from ui.TechConfigDialog import TechConfigDialog
    dlg = TechConfigDialog(FreeCADGui.getMainWindow())
    dlg.exec_()
"""

from compat import QtWidgets, QtCore, QtGui
import os


def _get_icon(name):
    try:
        from Get_Path import get_icon
        return get_icon(name)
    except Exception:
        return None


# ── Main dialog ────────────────────────────────────────────────────────────────

class TechConfigDialog(QtWidgets.QDialog):
    """Dialog for managing technology configuration profiles."""

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.Dialog)
        self.setWindowTitle("Technology Configuration")
        self.setMinimumSize(780, 520)
        self.resize(880, 560)

        # load singleton
        from core.TechConfig import tech_config
        self._tc = tech_config

        self._build_ui()
        self._refresh_profile_list()
        self._load_session_fields()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ─────────────────────────────────────────────────────────
        hdr = QtWidgets.QLabel(
            "  Technology Configuration  —  Global Profiles & Session Override"
        )
        hdr.setFixedHeight(40)
        hdr.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #1565C0, stop:1 #1976D2);"
            "color: white; font-size: 13px; font-weight: bold;"
        )
        root.addWidget(hdr)

        # ── Two-pane area ───────────────────────────────────────────────────
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setContentsMargins(8, 8, 8, 8)
        root.addWidget(splitter, 1)

        # Left: profile list
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)

        lbl_profiles = QtWidgets.QLabel("Global Profiles")
        lbl_profiles.setStyleSheet("font-weight: bold; font-size: 11px;")
        lv.addWidget(lbl_profiles)

        self._profile_list = QtWidgets.QListWidget()
        self._profile_list.setMinimumWidth(160)
        self._profile_list.currentRowChanged.connect(self._on_profile_selected)
        lv.addWidget(self._profile_list, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self._btn_new    = QtWidgets.QPushButton("New")
        self._btn_delete = QtWidgets.QPushButton("Delete")
        self._btn_new.clicked.connect(self._on_new_profile)
        self._btn_delete.clicked.connect(self._on_delete_profile)
        btn_row.addWidget(self._btn_new)
        btn_row.addWidget(self._btn_delete)
        lv.addLayout(btn_row)

        splitter.addWidget(left)

        # Right: edit panel
        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)
        rv.setSpacing(8)

        # ── Global profile editor ───────────────────────────────────────────
        grp_global = QtWidgets.QGroupBox("Edit Global Profile")
        grp_global.setStyleSheet("QGroupBox { font-weight: bold; }")
        gv = QtWidgets.QFormLayout(grp_global)
        gv.setLabelAlignment(QtCore.Qt.AlignRight)

        self._edit_name = QtWidgets.QLineEdit()
        self._edit_name.setPlaceholderText("e.g.  IHP SG13G2")
        gv.addRow("Profile name:", self._edit_name)

        self._edit_desc = QtWidgets.QLineEdit()
        self._edit_desc.setPlaceholderText("Short description (optional)")
        gv.addRow("Description:", self._edit_desc)

        self._edit_lyp, lyp_row = self._path_row("LYP file  *", "LYP Files (*.lyp *.LYP)")
        gv.addRow("LYP file:", lyp_row)

        self._edit_map, map_row = self._path_row("MAP file (optional)", "MAP Files (*.map *.MAP)")
        gv.addRow("MAP file:", map_row)

        self._edit_xml, xml_row = self._path_row("Stackup XML (optional)", "XML Files (*.xml *.XML)")
        gv.addRow("Stackup XML:", xml_row)

        btn_save_profile = QtWidgets.QPushButton("Save Profile")
        btn_save_profile.setStyleSheet(
            "QPushButton { background:#1976D2; color:white; font-weight:bold; padding:4px 12px; }"
            "QPushButton:hover { background:#1565C0; }"
        )
        btn_save_profile.clicked.connect(self._on_save_profile)

        self._btn_set_active = QtWidgets.QPushButton("Set as Active")
        self._btn_set_active.setToolTip("Use this profile as the active global profile (applies on next workbench load)")
        self._btn_set_active.clicked.connect(self._on_set_active)

        btn_row2 = QtWidgets.QHBoxLayout()
        btn_row2.addWidget(btn_save_profile)
        btn_row2.addWidget(self._btn_set_active)
        btn_row2.addStretch()
        gv.addRow("", btn_row2)

        rv.addWidget(grp_global)

        # ── Session override ────────────────────────────────────────────────
        grp_sess = QtWidgets.QGroupBox("Session Override  (current design only — does not affect global config)")
        grp_sess.setStyleSheet("QGroupBox { font-weight: bold; color: #555; }")
        sv = QtWidgets.QFormLayout(grp_sess)
        sv.setLabelAlignment(QtCore.Qt.AlignRight)

        self._sess_lyp, sess_lyp_row = self._path_row("LYP file", "LYP Files (*.lyp *.LYP)")
        sv.addRow("LYP file:", sess_lyp_row)

        self._sess_map, sess_map_row = self._path_row("MAP file", "MAP Files (*.map *.MAP)")
        sv.addRow("MAP file:", sess_map_row)

        self._sess_xml, sess_xml_row = self._path_row("Stackup XML", "XML Files (*.xml *.XML)")
        sv.addRow("Stackup XML:", sess_xml_row)

        sess_btn_row = QtWidgets.QHBoxLayout()
        btn_apply_sess = QtWidgets.QPushButton("Apply to Session")
        btn_apply_sess.setStyleSheet(
            "QPushButton { background:#388E3C; color:white; font-weight:bold; padding:4px 12px; }"
            "QPushButton:hover { background:#2E7D32; }"
        )
        btn_apply_sess.clicked.connect(self._on_apply_session)

        btn_reset_sess = QtWidgets.QPushButton("Reset to Active Profile")
        btn_reset_sess.setToolTip("Discard session overrides and reload from the active global profile")
        btn_reset_sess.clicked.connect(self._on_reset_session)

        sess_btn_row.addWidget(btn_apply_sess)
        sess_btn_row.addWidget(btn_reset_sess)
        sess_btn_row.addStretch()
        sv.addRow("", sess_btn_row)

        rv.addWidget(grp_sess)

        # ── Status line ─────────────────────────────────────────────────────
        self._status_label = QtWidgets.QLabel()
        self._status_label.setStyleSheet(
            "color: #555; font-size: 10px; padding: 4px 8px;"
        )
        rv.addWidget(self._status_label)
        rv.addStretch()

        splitter.addWidget(right)
        splitter.setSizes([200, 560])

        # ── Bottom buttons ──────────────────────────────────────────────────
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet("color: #CCC;")
        root.addWidget(sep)

        bottom = QtWidgets.QHBoxLayout()
        bottom.setContentsMargins(8, 6, 8, 8)

        self._lbl_active = QtWidgets.QLabel()
        self._lbl_active.setStyleSheet("font-size: 10px; color: #333;")
        bottom.addWidget(self._lbl_active)
        bottom.addStretch()

        btn_close = QtWidgets.QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

        self._refresh_status()

    def _path_row(self, placeholder, file_filter):
        """Return (QLineEdit, QWidget) for a path + Browse button row."""
        edit = QtWidgets.QLineEdit()
        edit.setPlaceholderText(placeholder)
        btn = QtWidgets.QPushButton("Browse…")
        btn.setFixedWidth(72)
        # closure captures correct filter
        _filter = file_filter
        btn.clicked.connect(lambda checked=False, e=edit, f=_filter: self._browse(e, f))
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit)
        h.addWidget(btn)
        return edit, w

    # ── Profile list ───────────────────────────────────────────────────────────

    def _refresh_profile_list(self):
        self._profile_list.blockSignals(True)
        self._profile_list.clear()
        active = self._tc.get_active_name()
        for name in self._tc.profile_names():
            item = QtWidgets.QListWidgetItem(name)
            if name == active:
                item.setForeground(QtGui.QBrush(QtGui.QColor("#1565C0")))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self._profile_list.addItem(item)
        self._profile_list.blockSignals(False)
        self._refresh_active_label()

    def _refresh_active_label(self):
        name = self._tc.get_active_name()
        self._lbl_active.setText(f"Active global profile:  {name or '(none)'}")

    def _refresh_status(self):
        self._status_label.setText("Session:  " + self._tc.status_summary())

    # ── Profile selection ──────────────────────────────────────────────────────

    def _on_profile_selected(self, row):
        if row < 0:
            return
        name = self._profile_list.item(row).text()
        profile = self._tc.get_profile(name)
        self._edit_name.setText(name)
        self._edit_desc.setText(profile.get("description", ""))
        self._edit_lyp.setText(profile.get("lyp_path", ""))
        self._edit_map.setText(profile.get("map_path", ""))
        self._edit_xml.setText(profile.get("xml_path", ""))

    # ── Global profile CRUD ────────────────────────────────────────────────────

    def _on_new_profile(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "New Profile", "Profile name:"
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        self._tc.set_profile(name, {"description": "", "lyp_path": "", "map_path": "", "xml_path": ""})
        self._tc.save_global()
        self._refresh_profile_list()
        # select newly created entry
        items = self._profile_list.findItems(name, QtCore.Qt.MatchExactly)
        if items:
            self._profile_list.setCurrentItem(items[0])

    def _on_delete_profile(self):
        row = self._profile_list.currentRow()
        if row < 0:
            return
        name = self._profile_list.item(row).text()
        reply = QtWidgets.QMessageBox.question(
            self, "Delete Profile",
            f"Delete profile  \"{name}\"?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self._tc.delete_profile(name)
            self._tc.save_global()
            self._refresh_profile_list()
            self._edit_name.clear()
            self._edit_desc.clear()
            self._edit_lyp.clear()
            self._edit_map.clear()
            self._edit_xml.clear()

    def _on_save_profile(self):
        name = self._edit_name.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Profile Name Required",
                                          "Please enter a profile name before saving.")
            return
        self._tc.set_profile(name, {
            "description": self._edit_desc.text().strip(),
            "lyp_path":    self._edit_lyp.text().strip(),
            "map_path":    self._edit_map.text().strip(),
            "xml_path":    self._edit_xml.text().strip(),
        })
        self._tc.save_global()
        self._refresh_profile_list()
        QtWidgets.QMessageBox.information(self, "Saved", f"Profile \"{name}\" saved.")

    def _on_set_active(self):
        name = self._edit_name.text().strip()
        if not name or name not in self._tc.profile_names():
            QtWidgets.QMessageBox.warning(self, "Save First",
                                          "Save the profile before setting it as active.")
            return
        self._tc.set_active_name(name)
        self._tc.save_global()
        self._tc.reset_local()
        self._refresh_profile_list()
        self._load_session_fields()
        self._refresh_status()
        self._refresh_toolbar()
        QtWidgets.QMessageBox.information(
            self, "Active Profile",
            f"Profile \"{name}\" is now active.\n"
            "Session paths have been updated to match."
        )

    # ── Session override ───────────────────────────────────────────────────────

    def _load_session_fields(self):
        local = self._tc.get_local()
        self._sess_lyp.setText(local.get("lyp_path", ""))
        self._sess_map.setText(local.get("map_path", ""))
        self._sess_xml.setText(local.get("xml_path", ""))

    def _on_apply_session(self):
        self._tc.set_local(
            lyp  = self._sess_lyp.text().strip() or None,
            map_ = self._sess_map.text().strip() or None,
            xml  = self._sess_xml.text().strip() or None,
        )
        self._refresh_status()
        self._refresh_toolbar()
        QtWidgets.QMessageBox.information(
            self, "Session Updated",
            "Session paths updated.\n"
            "The next GDS import will use these paths automatically."
        )

    def _on_reset_session(self):
        self._tc.reset_local()
        self._load_session_fields()
        self._refresh_status()
        self._refresh_toolbar()

    # ── Toolbar status sync ────────────────────────────────────────────────────

    @staticmethod
    def _refresh_toolbar():
        try:
            from core import TechStatusBar
            TechStatusBar.refresh()
        except Exception:
            pass

    # ── Browse helper ──────────────────────────────────────────────────────────

    @staticmethod
    def _browse(edit: QtWidgets.QLineEdit, file_filter: str):
        start = os.path.dirname(edit.text()) if edit.text() else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Select File", start, file_filter
        )
        if path:
            edit.setText(path)


# ── FreeCAD command ────────────────────────────────────────────────────────────

class TechConfigCommand:
    def GetResources(self):
        from Get_Path import get_icon
        return {
            "MenuText": "Technology Config",
            "ToolTip":  "Manage technology profiles (LYP / MAP / XML) and session overrides",
            "Pixmap":   get_icon("Tech_Config.svg"),
        }

    def Activated(self):
        import FreeCADGui
        from ui.TechConfigDialog import TechConfigDialog
        dlg = TechConfigDialog(FreeCADGui.getMainWindow())
        dlg.exec_()

    def IsActive(self):
        return True


import FreeCADGui
FreeCADGui.addCommand("TechConfigCommand", TechConfigCommand())
