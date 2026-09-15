# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Applying the chip skin — the part that needs a running GUI.

The colours and the stylesheet itself live in core/theme.py, which is
Qt-free and tested headlessly. This module only decides WHERE to put the
result and how to take it back off again.

Two rules shape everything below:

* The stylesheet goes on the MAIN WINDOW, never on QApplication. Qt
  propagates a widget's stylesheet to its children, so the main window
  reaches every panel, dialog and dock without touching the application-wide
  sheet FreeCAD manages through its own preferences. That is what makes this
  reversible — FreeCAD's own theme is never overwritten, only layered over.

* Everything changed is recorded before it is changed. The 3-D background
  lives in FreeCAD's parameter store, not in Qt, so it cannot be undone by
  dropping a stylesheet; the previous values are stashed so switching the
  theme off restores the user's own colours rather than guessing at defaults.
  When no stash survives, the keys are DELETED so FreeCAD's own defaults
  return — the real defaults are not ours to guess at.

* The skin is OPT-IN. It was briefly on by default, which meant a workbench
  repainted the whole of FreeCAD before being asked to, and any loss of the
  preference brought it back. migrate_to_opt_in() switches that off once for
  anyone carrying a setting from then.
"""

import FreeCAD

import core.theme as theme

_PREFS = "User parameter:BaseApp/Preferences/Mod/DI-PASSIONATE"
_VIEW = "User parameter:BaseApp/Preferences/View"

# Keys in _PREFS.
_K_FLAVOUR = "ThemeFlavour"      # "" (or absent) means the theme is off
_K_SAVED_BG = "ThemeSavedBackground"   # JSON of the view colours we replaced
_K_OPTIN = "ThemeIsOptIn"        # marks the one-shot switch to opt-in

# Set while our stylesheet is on the main window, so Deactivated() knows
# whether there is anything to undo.
_applied = {"flavour": None, "previous_qss": None}


def _params(path=_PREFS):
    return FreeCAD.ParamGet(path)


# ── Preference ────────────────────────────────────────────────────────────

def saved_flavour() -> str:
    """
    The flavour to use, or "" when the skin is off.

    OFF is the default. It used to be theme.DEFAULT_FLAVOUR, which meant an
    absent key turned the skin ON — so a workbench repainted the whole of
    FreeCAD before being asked to, and any loss of the preference (a reset
    config, a new profile) silently brought it back. A cosmetic feature has
    to be opted into, not opted out of.
    """
    return _params().GetString(_K_FLAVOUR, "")


def set_saved_flavour(flavour: str) -> None:
    _params().SetString(_K_FLAVOUR, flavour or "")


def is_enabled() -> bool:
    return bool(saved_flavour())


def migrate_to_opt_in() -> bool:
    """
    One-shot: turn the skin off for anyone carrying a flavour set while it
    was still on-by-default, and give them their viewport colours back.

    Runs once and records that it did, so a deliberate later choice is never
    undone. Returns True if it changed anything.
    """
    p = _params()
    if p.GetBool(_K_OPTIN, False):
        return False
    p.SetBool(_K_OPTIN, True)
    if not p.GetString(_K_FLAVOUR, ""):
        return False
    p.SetString(_K_FLAVOUR, "")
    _restore_background()
    FreeCAD.Console.PrintMessage(
        "[ChipTheme] The chip skin is now opt-in and has been switched off; "
        "FreeCAD's own colours are back. Turn it on again from Chip Theme in "
        "the Workbench toolbar." + "\n")
    return True


# ── 3-D viewport ──────────────────────────────────────────────────────────
# A dark window around a bright viewport reads worse than either on its own,
# so the background follows the chrome. FreeCAD stores these as packed
# unsigned ints, and the gradient is only used when UseBackgroundColorMid /
# Simple are set appropriately — hence saving the flags too, not just the
# colours.

_BG_KEYS = ("BackgroundColor", "BackgroundColor2", "BackgroundColor3",
            "BackgroundColor4")
_BG_FLAGS = ("UseBackgroundColorMid", "Simple", "Gradient")


def _packed(hex_colour: str) -> int:
    r, g, b = theme.parse_hex(hex_colour)
    return (r << 24) | (g << 16) | (b << 8) | 0xFF


def _stash_background() -> None:
    """Record the user's current viewport colours, once."""
    import json
    p = _params()
    if p.GetString(_K_SAVED_BG, ""):
        return          # already stashed; do not overwrite with our own values
    v = _params(_VIEW)
    saved = {"colours": {k: v.GetUnsigned(k, 0) for k in _BG_KEYS},
             "flags": {k: v.GetBool(k, False) for k in _BG_FLAGS}}
    p.SetString(_K_SAVED_BG, json.dumps(saved))


def _clear_our_background() -> None:
    """
    Delete the viewport keys this module writes, so FreeCAD falls back to its
    OWN built-in defaults.

    Removing beats writing a "default" value: the real defaults live in
    FreeCAD and are not ours to guess, and a guess would be wrong in exactly
    the situation this matters — restoring a native look.
    """
    v = _params(_VIEW)
    for key in _BG_KEYS:
        try:
            v.RemUnsigned(key)
        except Exception:
            pass
    for key in _BG_FLAGS:
        try:
            v.RemBool(key)
        except Exception:
            pass


def _restore_background() -> None:
    """
    Put the viewport back the way it was, or failing that, back to native.

    The stash can legitimately be missing — the preference file was reset, or
    the skin was applied by a build that did not stash. Returning early then
    left a dark viewport with no way back short of editing preferences by
    hand, which is the opposite of what switching a theme off should do.
    """
    import json
    p = _params()
    blob = p.GetString(_K_SAVED_BG, "")
    if not blob:
        _clear_our_background()
        return
    try:
        saved = json.loads(blob)
    except Exception:
        p.SetString(_K_SAVED_BG, "")
        _clear_our_background()
        return
    v = _params(_VIEW)
    for key, val in (saved.get("colours") or {}).items():
        if val:
            v.SetUnsigned(key, int(val))
        else:
            # 0 means the key was absent when stashed, not "black" — writing
            # it back would paint the viewport black.
            try:
                v.RemUnsigned(key)
            except Exception:
                pass
    for key, val in (saved.get("flags") or {}).items():
        v.SetBool(key, bool(val))
    p.SetString(_K_SAVED_BG, "")


def _apply_background(flavour: str) -> None:
    pal = theme.palette(flavour)
    _stash_background()
    v = _params(_VIEW)
    v.SetUnsigned("BackgroundColor2", _packed(pal["view_bottom"]))
    v.SetUnsigned("BackgroundColor3", _packed(pal["view_top"]))
    v.SetBool("Simple", False)
    v.SetBool("Gradient", True)
    v.SetBool("UseBackgroundColorMid", False)


def ensure_native_background() -> None:
    """
    Undo any viewport colours we left behind, when the skin is off.

    The stylesheet lives on the main window and disappears with it; the
    background lives in FreeCAD's parameters and survives a restart. Without
    this, turning the skin off in one session and restarting left a dark
    viewport inside an otherwise native FreeCAD.
    """
    if _params().GetString(_K_SAVED_BG, ""):
        _restore_background()


# ── Stylesheet ────────────────────────────────────────────────────────────

def _main_window():
    import FreeCADGui
    return FreeCADGui.getMainWindow()


def apply_theme(flavour: str = None) -> bool:
    """Put the skin on. Returns True if it was applied."""
    if not FreeCAD.GuiUp:
        return False
    flavour = flavour or saved_flavour() or theme.DEFAULT_FLAVOUR
    try:
        qss = theme.build_stylesheet(flavour)
    except ValueError as exc:
        FreeCAD.Console.PrintError(f"[ChipTheme] {exc}\n")
        return False
    try:
        mw = _main_window()
        if mw is None:
            return False
        if _applied["previous_qss"] is None:
            # Only the FIRST apply records the baseline. Re-applying (e.g.
            # switching flavour) must not stash our own sheet as "previous",
            # or switching off would restore the theme instead of removing it.
            _applied["previous_qss"] = mw.styleSheet() or ""
        mw.setStyleSheet(_applied["previous_qss"] + "\n" + qss)
        _applied["flavour"] = flavour
        _apply_background(flavour)
        _restyle_own_widgets(flavour)
        return True
    except Exception as exc:
        FreeCAD.Console.PrintError(f"[ChipTheme] could not apply: {exc}\n")
        return False


def remove_theme() -> bool:
    """Take the skin off and put the user's own colours back."""
    if not FreeCAD.GuiUp:
        return False
    try:
        mw = _main_window()
        if mw is not None:
            mw.setStyleSheet(_applied["previous_qss"] or "")
        _applied["flavour"] = None
        _applied["previous_qss"] = None
        _restore_background()
        return True
    except Exception as exc:
        FreeCAD.Console.PrintError(f"[ChipTheme] could not remove: {exc}\n")
        return False


def active_flavour():
    """The flavour currently on screen, or None."""
    return _applied["flavour"]


def _restyle_own_widgets(flavour: str) -> None:
    """
    Re-colour the two widgets this workbench styles inline.

    InitGui.py sets an explicit stylesheet on the toolbar category captions
    and on the technology status label. An explicit per-widget stylesheet
    beats anything inherited, so those two would keep their light-theme
    colours no matter what the main window says — the status label in
    particular was a near-white box, which is exactly what it should not be
    on a dark background.
    """
    try:
        from compat import QtWidgets
        mw = _main_window()
        if mw is None:
            return
        caption = theme.caption_style(flavour)
        status = theme.status_label_style(flavour)
        for lbl in mw.findChildren(QtWidgets.QLabel):
            sheet = lbl.styleSheet() or ""
            if "font-size: 9px" in sheet:
                lbl.setStyleSheet(caption)
            elif "border-radius: 3px" in sheet and "padding: 2px 6px" in sheet:
                lbl.setStyleSheet(status)
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[ChipTheme] own-widget restyle skipped: {exc}\n")


# ── Command ───────────────────────────────────────────────────────────────

class ChipThemeCommand:
    """Pick a flavour, or switch the skin off."""

    def GetResources(self):
        from Get_Path import get_icon
        return {
            "MenuText": "Chip Theme",
            "ToolTip": (
                "Skin FreeCAD in the workbench's own dark silicon-and-copper\n"
                "look, and match the 3-D background to it.\n\n"
                "Pick an accent flavour, or switch it off to get your own\n"
                "FreeCAD theme back — nothing is overwritten, the skin is\n"
                "layered on top and removed cleanly."
            ),
            "Pixmap": get_icon("Chip_Theme.svg"),
        }

    def Activated(self):
        from compat import QtWidgets, QtGui
        import FreeCADGui

        menu = QtWidgets.QMenu()
        current = active_flavour()
        entries = []
        for name in theme.available_flavours():
            pal = theme.palette(name)
            act = menu.addAction(_swatch(pal["accent"]), name.capitalize())
            act.setCheckable(True)
            act.setChecked(name == current)
            entries.append((act, name))
        menu.addSeparator()
        off = menu.addAction("Off (use my FreeCAD theme)")
        off.setCheckable(True)
        off.setChecked(current is None)

        chosen = menu.exec_(QtGui.QCursor.pos())
        if chosen is None:
            return
        if chosen == off:
            remove_theme()
            set_saved_flavour("")
            FreeCAD.Console.PrintMessage("[ChipTheme] off\n")
            return
        for act, name in entries:
            if chosen == act:
                if apply_theme(name):
                    set_saved_flavour(name)
                    FreeCAD.Console.PrintMessage(f"[ChipTheme] {name}\n")
                break

    def IsActive(self):
        return True


def _swatch(colour: str):
    """A small filled square, so the menu shows the accent it is offering."""
    from compat import QtGui
    pm = QtGui.QPixmap(16, 16)
    pm.fill(QtGui.QColor(colour))
    painter = QtGui.QPainter(pm)
    painter.setPen(QtGui.QColor(theme.BASE["border"]))
    painter.drawRect(0, 0, 15, 15)
    painter.end()
    return QtGui.QIcon(pm)


if FreeCAD.GuiUp:
    import FreeCADGui
    FreeCADGui.addCommand("ChipThemeCommand", ChipThemeCommand())
