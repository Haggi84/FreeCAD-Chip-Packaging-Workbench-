"""
DI-PASSIONATE FreeCAD Workbench – Help Guide
Modern sidebar-based help dialog.
"""

import os
import sys
import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore, QtGui

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)
from Get_Path import get_icon, get_html


# ── Content sections ───────────────────────────────────────────────────────────

SECTIONS = [
    ("Overview",        "overview.html",        "🏠"),
    ("Quick Start",     "quickstart.html",       "🚀"),
    ("Tool Reference",  "tools.html",            "🔧"),
    ("Workflows",       "workflows.html",         "🔄"),
    ("Troubleshooting", "troubleshooting.html",  "🛠"),
]

# Inline CSS injected into every HTML page so QTextBrowser renders nicely
_CSS = """
<style>
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px;
       color: #2c3e50; background: #f8f9fa; margin: 0; padding: 0; }
h1 { font-size: 20px; color: #1a5276; border-bottom: 2px solid #2980b9;
     padding-bottom: 6px; margin-top: 18px; }
h2 { font-size: 16px; color: #1f618d; margin-top: 16px; }
h3 { font-size: 13px; color: #2471a3; margin-top: 12px; }
p  { line-height: 1.6; margin: 6px 0; }
ul, ol { margin: 4px 0 8px 18px; line-height: 1.7; }
code { background: #e8f4f8; color: #c0392b; padding: 1px 5px;
       border-radius: 3px; font-family: Consolas, monospace; font-size: 12px; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; }
th { background: #2980b9; color: white; padding: 7px 10px; text-align: left;
     font-size: 12px; }
td { padding: 6px 10px; border-bottom: 1px solid #d5e8f4; font-size: 12px; }
tr:nth-child(even) td { background: #eaf4fb; }
.card { background: white; border-left: 4px solid #2980b9;
        padding: 10px 14px; margin: 10px 0; }
.warn { background: #fef9e7; border-left: 4px solid #f39c12;
        padding: 10px 14px; margin: 10px 0; }
.tip  { background: #eafaf1; border-left: 4px solid #27ae60;
        padding: 10px 14px; margin: 10px 0; }
.step { display: inline-block; background: #2980b9; color: white;
        width: 22px; height: 22px; border-radius: 11px;
        text-align: center; line-height: 22px; font-weight: bold;
        margin-right: 6px; font-size: 12px; }
b { color: #1a5276; }
</style>
"""


def _load_section(html_file):
    path = get_html(html_file)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
    else:
        body = f"<p><i>Content file <code>{html_file}</code> not found.</i></p>"
    return _CSS + body


# ── Dialog ─────────────────────────────────────────────────────────────────────

class HelpGuideDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DI-PASSIONATE Workbench — Help")
        self.resize(1020, 720)
        self.setMinimumSize(800, 560)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ────────────────────────────────────────────────────
        header = QtWidgets.QWidget()
        header.setFixedHeight(52)
        header.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #1a5276, stop:1 #2980b9);"
        )
        h_lay = QtWidgets.QHBoxLayout(header)
        h_lay.setContentsMargins(16, 0, 16, 0)

        logo_lbl = QtWidgets.QLabel("?")
        logo_lbl.setStyleSheet(
            "color: white; font-size: 28px; font-weight: bold; "
            "border: 2px solid white; border-radius: 18px; "
            "min-width: 36px; max-width: 36px; "
            "min-height: 36px; max-height: 36px; "
            "text-align: center; padding-left: 1px;"
        )
        logo_lbl.setAlignment(QtCore.Qt.AlignCenter)

        title_lbl = QtWidgets.QLabel("DI-PASSIONATE FreeCAD Workbench — Help Guide")
        title_lbl.setStyleSheet(
            "color: white; font-size: 16px; font-weight: bold; margin-left: 12px;"
        )

        h_lay.addWidget(logo_lbl)
        h_lay.addWidget(title_lbl)
        h_lay.addStretch()
        root.addWidget(header)

        # ── Body (sidebar + content) ───────────────────────────────────────
        body_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        body_split.setHandleWidth(1)
        body_split.setStyleSheet("QSplitter::handle { background: #d0d0d0; }")

        # Sidebar
        sidebar = QtWidgets.QWidget()
        sidebar.setFixedWidth(190)
        sidebar.setStyleSheet("background: #f0f3f6; border-right: 1px solid #c8d0d8;")
        sb_lay = QtWidgets.QVBoxLayout(sidebar)
        sb_lay.setContentsMargins(0, 8, 0, 8)
        sb_lay.setSpacing(2)

        self._nav_btns = []
        for i, (label, _, emoji) in enumerate(SECTIONS):
            btn = QtWidgets.QPushButton(f"  {emoji}  {label}")
            btn.setFlat(True)
            btn.setCheckable(True)
            btn.setStyleSheet(
                "QPushButton { text-align: left; padding: 10px 14px; "
                "font-size: 13px; color: #2c3e50; border: none; "
                "border-radius: 0; background: transparent; } "
                "QPushButton:checked { background: #2980b9; color: white; "
                "font-weight: bold; } "
                "QPushButton:hover:!checked { background: #d5e8f4; }"
            )
            btn.clicked.connect(lambda checked=False, idx=i: self._show_section(idx))
            sb_lay.addWidget(btn)
            self._nav_btns.append(btn)

        sb_lay.addStretch()

        # Version tag at bottom of sidebar
        ver_lbl = QtWidgets.QLabel("v1.0  —  DI-PASSIONATE")
        ver_lbl.setStyleSheet(
            "color: #95a5a6; font-size: 10px; padding: 6px 14px;"
        )
        sb_lay.addWidget(ver_lbl)

        body_split.addWidget(sidebar)

        # Content pane
        self._browser = QtWidgets.QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        self._browser.setStyleSheet(
            "QTextBrowser { background: #f8f9fa; border: none; "
            "padding: 16px; font-size: 13px; }"
        )
        body_split.addWidget(self._browser)
        body_split.setStretchFactor(1, 1)

        root.addWidget(body_split, stretch=1)

        # ── Footer ─────────────────────────────────────────────────────────
        footer = QtWidgets.QWidget()
        footer.setStyleSheet("background: #ecf0f1; border-top: 1px solid #bdc3c7;")
        f_lay = QtWidgets.QHBoxLayout(footer)
        f_lay.setContentsMargins(16, 6, 16, 6)
        f_lay.addStretch()
        close_btn = QtWidgets.QPushButton("  Close  ")
        close_btn.setStyleSheet(
            "QPushButton { background: #2980b9; color: white; border: none; "
            "border-radius: 4px; padding: 6px 20px; font-size: 13px; } "
            "QPushButton:hover { background: #1a6fa0; }"
        )
        close_btn.clicked.connect(self.accept)
        f_lay.addWidget(close_btn)
        root.addWidget(footer)

        # Show first section
        self._show_section(0)

    def _show_section(self, index):
        for i, btn in enumerate(self._nav_btns):
            btn.setChecked(i == index)
        _, html_file, _ = SECTIONS[index]
        self._browser.setHtml(_load_section(html_file))
        self._browser.verticalScrollBar().setValue(0)


# ── Command ────────────────────────────────────────────────────────────────────

class HelpGuideCommand:
    def GetResources(self):
        return {
            "MenuText": "Help Guide",
            "ToolTip":  "Open the DI-PASSIONATE workbench help guide",
            "Pixmap":   get_icon("Help_Guide.svg"),
        }

    def Activated(self):
        dlg = HelpGuideDialog(FreeCADGui.getMainWindow())
        dlg.exec_()

    def IsActive(self):
        return True


FreeCADGui.addCommand("HelpGuideCommand", HelpGuideCommand())
