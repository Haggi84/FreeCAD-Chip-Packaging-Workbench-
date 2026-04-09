"""
About dialog — shows workbench version and project information.
"""

import os
import sys

import FreeCAD
import FreeCADGui
from PySide2 import QtCore, QtGui, QtWidgets

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from Get_Path import get_icon
from version import VERSION_STRING


class AboutDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About DI-PASSIONATE Workbench")
        self.setFixedWidth(420)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 20)

        # ── Logo ──────────────────────────────────────────────────────────────
        logo_path = get_icon("Workbench_logo.png")
        if logo_path:
            logo_label = QtWidgets.QLabel()
            pixmap = QtGui.QPixmap(logo_path).scaledToHeight(
                72, QtCore.Qt.SmoothTransformation
            )
            logo_label.setPixmap(pixmap)
            logo_label.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(logo_label)

        # ── Title ─────────────────────────────────────────────────────────────
        title = QtWidgets.QLabel("DI-PASSIONATE FreeCAD Workbench")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #1a2332;")
        layout.addWidget(title)

        # ── Version badge ─────────────────────────────────────────────────────
        version_label = QtWidgets.QLabel(f"Version {VERSION_STRING}")
        version_label.setAlignment(QtCore.Qt.AlignCenter)
        version_label.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: white;"
            "background-color: #27ae60; border-radius: 8px; padding: 3px 14px;"
        )
        version_label.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed
        )

        badge_row = QtWidgets.QHBoxLayout()
        badge_row.addStretch()
        badge_row.addWidget(version_label)
        badge_row.addStretch()
        layout.addLayout(badge_row)

        # ── Separator ────────────────────────────────────────────────────────
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(sep)

        # ── Info grid ─────────────────────────────────────────────────────────
        grid = QtWidgets.QFormLayout()
        grid.setLabelAlignment(QtCore.Qt.AlignRight)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        def _row(label, value, url=None):
            lbl = QtWidgets.QLabel(f"<b>{label}</b>")
            if url:
                val = QtWidgets.QLabel(f'<a href="{url}">{value}</a>')
                val.setOpenExternalLinks(True)
            else:
                val = QtWidgets.QLabel(value)
            val.setWordWrap(True)
            grid.addRow(lbl, val)

        _row("Project", "BMBF DI-PASSIONATE")
        _row("Purpose", "Chip-packaging workflows in FreeCAD")
        _row(
            "Features",
            "GDSII import · Leadframe design · Wire bonding · Housing",
        )
        _row("FreeCAD min.", "0.21")
        _row(
            "Repository",
            "github.com/Haggi84/DI-PASSIONATE-FreeCAD",
            url="https://github.com/Haggi84/DI-PASSIONATE-FreeCAD",
        )
        _row(
            "Authors",
            "Haggi84 (github.com/Haggi84) · FreeCAD community contributors",
        )

        layout.addLayout(grid)

        # ── Versioning note ───────────────────────────────────────────────────
        note = QtWidgets.QLabel(
            "Versioning follows <a href='https://semver.org'>Semantic Versioning</a> "
            "(MAJOR.MINOR.PATCH)."
        )
        note.setOpenExternalLinks(True)
        note.setStyleSheet("color: #666; font-size: 10px;")
        note.setAlignment(QtCore.Qt.AlignCenter)
        note.setWordWrap(True)
        layout.addWidget(note)

        # ── Close button ──────────────────────────────────────────────────────
        btn = QtWidgets.QPushButton("Close")
        btn.setDefault(True)
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)


class AboutCommand:
    def GetResources(self):
        return {
            "MenuText": "About",
            "ToolTip": f"DI-PASSIONATE Workbench v{VERSION_STRING}",
            "Pixmap": get_icon("Workbench_logo.png"),
        }

    def Activated(self):
        dialog = AboutDialog(FreeCADGui.getMainWindow())
        dialog.exec_()

    def IsActive(self):
        return True


FreeCADGui.addCommand("AboutCommand", AboutCommand())
