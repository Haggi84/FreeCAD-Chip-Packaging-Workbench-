# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The workbench's own look — a dark "silicon and copper" skin for FreeCAD.

Why a theme at all: this workbench spends its whole life in one visual
register — dark dies, copper traces, gold bond wires — and FreeCAD's stock
light chrome fights it. More practically, several widgets this workbench
adds itself had colours hard-coded for a light background (the technology
status label, the toolbar category captions), which turn into white-on-white
smears the moment anyone uses a dark FreeCAD. Naming the colours once, here,
is what lets those widgets ask for the right one instead of guessing.

Qt-free on purpose. Everything here is string and integer arithmetic over a
palette, so the palette maths and the generated stylesheet are testable
headlessly; ui/ChipTheme.py does the actual applying and is the only part
that needs a running GUI.

The flavours differ ONLY in accent colour — the dark base is shared. That is
deliberate: the accent is the part that carries the "which material am I
looking at" cue (copper trace, gold wire, solder mask), and keeping one base
means a user switching flavours does not have to re-learn the interface.
"""

from string import Template

# ── Palette ───────────────────────────────────────────────────────────────
# Base tones, shared by every flavour. Ordered dark -> light.
BASE = {
    "base":    "#10141a",   # window background, the deepest tone
    "panel":   "#171d26",   # docks, panels, menus
    "raised":  "#212a36",   # inputs, buttons, headers — one step up
    "hover":   "#2b3644",   # hovered raised surfaces
    "border":  "#303c4b",
    "text":    "#c9d4df",
    "dim":     "#7b8a99",   # secondary text, captions, disabled
    "warn":    "#e0a030",
    "error":   "#e05252",
    "ok":      "#4caf7d",
}

# Each flavour is one accent colour, named for the material it evokes.
FLAVOURS = {
    "copper": "#c87137",    # routed trace — the default
    "gold":   "#d4a017",    # bond wire
    "solder": "#3f8f5a",    # solder mask
    "silicon": "#5a7fa8",   # bare die, for anyone who finds warm accents loud
}

DEFAULT_FLAVOUR = "copper"

# 3-D view background, as a two-stop vertical gradient. Kept in step with the
# chrome so the viewport does not read as a bright hole in a dark window.
VIEW_GRADIENT = ("#0b0e13", "#1c2530")


def _clamp_byte(v) -> int:
    return max(0, min(255, int(round(v))))


def parse_hex(colour: str):
    """'#rrggbb' -> (r, g, b). Accepts a missing '#' and any case."""
    s = str(colour).strip().lstrip("#")
    if len(s) == 3:                       # '#abc' shorthand
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"Not a #rrggbb colour: {colour!r}")
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise ValueError(f"Not a #rrggbb colour: {colour!r}")


def to_hex(rgb) -> str:
    r, g, b = (_clamp_byte(c) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def shade(colour: str, factor: float) -> str:
    """
    Lighten (factor > 1) or darken (factor < 1) a colour.

    Clamped, so shading an already-bright colour saturates instead of
    wrapping around to black — the failure mode that makes a hover state
    suddenly invert.
    """
    return to_hex(c * factor for c in parse_hex(colour))


def mix(a: str, b: str, t: float) -> str:
    """Blend a -> b, t in [0, 1]."""
    t = max(0.0, min(1.0, float(t)))
    ca, cb = parse_hex(a), parse_hex(b)
    return to_hex(x + (y - x) * t for x, y in zip(ca, cb))


def rgba(colour: str, alpha: float) -> str:
    """Qt stylesheets need rgba() literals for translucency."""
    r, g, b = parse_hex(colour)
    a = max(0.0, min(1.0, float(alpha)))
    return f"rgba({r}, {g}, {b}, {a:.3f})"


def palette(flavour: str = DEFAULT_FLAVOUR) -> dict:
    """
    The full token set for one flavour: the shared base plus every accent
    variant derived from it.

    Callers outside the stylesheet use this too — see ui/ChipTheme.py, which
    hands 'dim' and 'accent' to the toolbar captions and the technology
    status label so those stop hard-coding light-theme colours.
    """
    key = str(flavour or DEFAULT_FLAVOUR).lower()
    accent = FLAVOURS.get(key)
    if accent is None:
        raise ValueError(
            f"Unknown flavour {flavour!r}; known: {', '.join(sorted(FLAVOURS))}")

    p = dict(BASE)
    p["flavour"] = key
    p["accent"] = accent
    p["accent_hi"] = shade(accent, 1.25)
    p["accent_lo"] = shade(accent, 0.72)
    # Selections are the accent laid over the dark base rather than the accent
    # itself: full-strength accent behind text of any colour is unreadable.
    p["select"] = mix(BASE["panel"], accent, 0.55)
    p["select_dim"] = mix(BASE["panel"], accent, 0.28)
    p["accent_ghost"] = rgba(accent, 0.16)
    p["view_top"], p["view_bottom"] = VIEW_GRADIENT
    return p


def available_flavours():
    """Flavour names, default first — the order a menu should offer them."""
    rest = sorted(k for k in FLAVOURS if k != DEFAULT_FLAVOUR)
    return [DEFAULT_FLAVOUR] + rest


# ── Stylesheet ────────────────────────────────────────────────────────────
# string.Template, NOT str.format: QSS is made of braces, and format() would
# need every one of them doubled. A stray '$name' with no matching token
# raises from substitute(), so a typo fails loudly instead of shipping a
# stylesheet with a literal '$accent' in it.
#
# Deliberately NO bare 'QWidget' selector. That would repaint the container
# the 3-D viewport lives in, and on some drivers the OpenGL surface then
# renders behind a solid fill — an expensive lesson to re-learn, so the rules
# below always name a concrete widget class.
_QSS = Template("""
/* ── menus and menu bar ─────────────────────────────────────────────── */
QMenuBar {
    background: $base;
    color: $text;
    border-bottom: 1px solid $border;
}
QMenuBar::item { background: transparent; padding: 4px 10px; }
QMenuBar::item:selected { background: $select_dim; color: $text; }
QMenuBar::item:pressed  { background: $select; }

QMenu {
    background: $panel;
    color: $text;
    border: 1px solid $border;
    padding: 4px;
}
QMenu::item { padding: 5px 26px 5px 22px; border-radius: 3px; }
QMenu::item:selected { background: $select; color: #ffffff; }
QMenu::item:disabled { color: $dim; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }
QMenu::icon { padding-left: 6px; }

/* ── toolbars ───────────────────────────────────────────────────────── */
QToolBar {
    background: $base;
    border: none;
    border-bottom: 1px solid $border;
    spacing: 1px;
    padding: 1px;
}
QToolBar::separator {
    background: $accent_lo;
    width: 1px;
    margin: 5px 6px;
}
QToolButton {
    background: transparent;
    color: $text;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 3px;
}
QToolButton:hover {
    background: $accent_ghost;
    border: 1px solid $accent_lo;
}
QToolButton:pressed, QToolButton:checked {
    background: $select_dim;
    border: 1px solid $accent;
}
QToolButton:disabled { color: $dim; }
QToolButton::menu-indicator { image: none; }

/* ── docks ──────────────────────────────────────────────────────────── */
QDockWidget {
    color: $text;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}
QDockWidget::title {
    background: $raised;
    color: $text;
    padding: 5px 8px;
    border-left: 3px solid $accent;
}
QMainWindow::separator { background: $border; width: 3px; height: 3px; }
QMainWindow::separator:hover { background: $accent; }

/* ── trees, lists, tables ───────────────────────────────────────────── */
QTreeView, QTreeWidget, QListView, QListWidget, QTableView, QTableWidget,
QPlainTextEdit, QTextEdit, QTextBrowser {
    background: $panel;
    alternate-background-color: $base;
    color: $text;
    border: 1px solid $border;
    border-radius: 3px;
    selection-background-color: $select;
    selection-color: #ffffff;
}
QTreeView::item, QListView::item { padding: 2px; }
QTreeView::item:hover, QListView::item:hover,
QTableView::item:hover { background: $accent_ghost; }
QTreeView::item:selected, QListView::item:selected,
QTableView::item:selected { background: $select; color: #ffffff; }
QHeaderView::section {
    background: $raised;
    color: $dim;
    border: none;
    border-right: 1px solid $border;
    border-bottom: 1px solid $border;
    padding: 4px 6px;
    font-weight: bold;
}

/* ── buttons ────────────────────────────────────────────────────────── */
QPushButton {
    background: $raised;
    color: $text;
    border: 1px solid $border;
    border-radius: 4px;
    padding: 5px 14px;
    min-height: 18px;
}
QPushButton:hover  { background: $hover; border-color: $accent_lo; }
QPushButton:pressed { background: $select_dim; border-color: $accent; }
QPushButton:default { border: 1px solid $accent; }
QPushButton:disabled { background: $panel; color: $dim; border-color: $border; }

/* ── inputs ─────────────────────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QAbstractSpinBox {
    background: $base;
    color: $text;
    border: 1px solid $border;
    border-radius: 3px;
    padding: 3px 6px;
    selection-background-color: $select;
    selection-color: #ffffff;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QAbstractSpinBox:focus { border: 1px solid $accent; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled, QAbstractSpinBox:disabled { color: $dim; background: $panel; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: $panel;
    color: $text;
    border: 1px solid $accent_lo;
    selection-background-color: $select;
    selection-color: #ffffff;
}

QCheckBox, QRadioButton { color: $text; spacing: 6px; }
QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; }
QCheckBox::indicator:unchecked, QRadioButton::indicator:unchecked {
    background: $base; border: 1px solid $border;
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background: $accent; border: 1px solid $accent_hi;
}
QRadioButton::indicator { border-radius: 7px; }
QCheckBox::indicator { border-radius: 3px; }

/* ── grouping ───────────────────────────────────────────────────────── */
QGroupBox {
    background: transparent;
    border: 1px solid $border;
    border-radius: 4px;
    margin-top: 14px;
    padding-top: 6px;
    color: $text;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 5px;
    color: $accent;
    font-weight: bold;
}

QTabWidget::pane { border: 1px solid $border; background: $panel; top: -1px; }
QTabBar::tab {
    background: $base;
    color: $dim;
    border: 1px solid $border;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 5px 12px;
    margin-right: 2px;
}
QTabBar::tab:hover { color: $text; background: $raised; }
QTabBar::tab:selected {
    background: $panel;
    color: $text;
    border-top: 2px solid $accent;
}

/* ── scrollbars ─────────────────────────────────────────────────────── */
QScrollBar:vertical   { background: $base; width: 11px; margin: 0; }
QScrollBar:horizontal { background: $base; height: 11px; margin: 0; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: $raised;
    border-radius: 5px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:hover { background: $accent_lo; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ── feedback ───────────────────────────────────────────────────────── */
QProgressBar {
    background: $base;
    border: 1px solid $border;
    border-radius: 3px;
    text-align: center;
    color: $text;
}
QProgressBar::chunk { background: $accent; border-radius: 2px; }

QStatusBar { background: $base; color: $dim; border-top: 1px solid $border; }
QStatusBar::item { border: none; }

QToolTip {
    background: $raised;
    color: $text;
    border: 1px solid $accent_lo;
    padding: 4px 6px;
}

QSplitter::handle { background: $border; }
QSplitter::handle:hover { background: $accent; }

QSlider::groove:horizontal { background: $base; height: 4px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent_lo; border-radius: 2px; }
QSlider::handle:horizontal {
    background: $accent;
    border: 1px solid $accent_hi;
    width: 12px;
    margin: -5px 0;
    border-radius: 6px;
}

QDialog, QMessageBox { background: $panel; color: $text; }
QLabel { color: $text; background: transparent; }
""")


def build_stylesheet(flavour: str = DEFAULT_FLAVOUR) -> str:
    """The complete QSS for one flavour."""
    return _QSS.substitute(palette(flavour)).strip() + "\n"


# ── Colours for widgets this workbench builds by hand ─────────────────────
# InitGui.py's toolbar captions and technology status label set their own
# stylesheets inline. They ask here rather than hard-coding, which is what
# stops them from staying light-theme grey on a dark window.

def caption_style(flavour: str = DEFAULT_FLAVOUR) -> str:
    p = palette(flavour)
    return ("QLabel {"
            f"  color: {p['dim']};"
            "  background: transparent;"
            "  font-size: 9px;"
            "  font-weight: bold;"
            "}")


def status_label_style(flavour: str = DEFAULT_FLAVOUR) -> str:
    p = palette(flavour)
    return ("QLabel {"
            f"  background: {p['base']};"
            f"  color: {p['text']};"
            f"  border: 1px solid {p['border']};"
            f"  border-left: 3px solid {p['accent']};"
            "  border-radius: 3px;"
            "  padding: 2px 6px;"
            "  margin: 2px 4px;"
            "}")
