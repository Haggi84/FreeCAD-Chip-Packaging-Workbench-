# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.theme — the workbench's dark chip skin.

Applying a stylesheet needs a running GUI and is not tested here. What is
testable, and what actually breaks, is everything before that: the colour
arithmetic that derives hover and selection tones, and whether the generated
QSS is complete. A stylesheet with an unsubstituted '$accent' left in it is
not a crash — Qt quietly drops the rule it appears in and the interface comes
up subtly wrong, which is far harder to notice than an exception.
"""

from _harness import TestCase

import core.theme as theme


def run():
    tc = TestCase("theme")

    # ── colour parsing ────────────────────────────────────────────────────
    tc.check("parse_hex: reads #rrggbb",
              theme.parse_hex("#c87137") == (200, 113, 55))
    tc.check("parse_hex: tolerates a missing '#' and upper case",
              theme.parse_hex("C87137") == (200, 113, 55))
    tc.check("parse_hex: expands #abc shorthand the way CSS does",
              theme.parse_hex("#abc") == (170, 187, 204))
    for bad in ("#12345", "not a colour", "#gggggg", ""):
        try:
            theme.parse_hex(bad)
            raised = False
        except ValueError:
            raised = True
        tc.check(f"parse_hex: refuses {bad!r} rather than returning a "
                  f"silently wrong colour", raised)

    tc.check("to_hex: round-trips",
              theme.to_hex((200, 113, 55)) == "#c87137")

    # ── shading ───────────────────────────────────────────────────────────
    tc.check("shade: >1 lightens, <1 darkens",
              theme.parse_hex(theme.shade("#808080", 1.5))[0] > 128
              and theme.parse_hex(theme.shade("#808080", 0.5))[0] < 128)
    tc.check("shade: clamps at white instead of wrapping around to black — "
              "the failure that makes a hover state invert",
              theme.shade("#f0f0f0", 4.0) == "#ffffff")
    tc.check("shade: clamps at black",
              theme.shade("#101010", 0.0) == "#000000")

    tc.check("mix: t=0 and t=1 are the endpoints",
              theme.mix("#000000", "#ffffff", 0.0) == "#000000"
              and theme.mix("#000000", "#ffffff", 1.0) == "#ffffff")
    tc.check("mix: t=0.5 lands halfway",
              theme.mix("#000000", "#ffffff", 0.5) in ("#7f7f7f", "#808080"))
    tc.check("mix: t outside 0..1 is clamped, not extrapolated",
              theme.mix("#000000", "#ffffff", -3.0) == "#000000"
              and theme.mix("#000000", "#ffffff", 9.0) == "#ffffff")

    tc.check("rgba: emits a Qt-readable literal",
              theme.rgba("#c87137", 0.5) == "rgba(200, 113, 55, 0.500)")

    # ── palette ───────────────────────────────────────────────────────────
    flavours = theme.available_flavours()
    tc.check("available_flavours: offers the default first",
              flavours and flavours[0] == theme.DEFAULT_FLAVOUR,
              f"got {flavours}")
    tc.check("available_flavours: lists every defined flavour",
              set(flavours) == set(theme.FLAVOURS))

    try:
        theme.palette("chartreuse")
        unknown_raised = False
    except ValueError:
        unknown_raised = True
    tc.check("palette: an unknown flavour raises rather than silently "
              "falling back to the default", unknown_raised)

    required = {"base", "panel", "raised", "hover", "border", "text", "dim",
                "accent", "accent_hi", "accent_lo", "select", "select_dim",
                "accent_ghost", "view_top", "view_bottom"}
    for name in flavours:
        p = theme.palette(name)
        tc.check(f"palette({name}): defines every token the stylesheet uses",
                  required <= set(p), f"missing {sorted(required - set(p))}")
        tc.check(f"palette({name}): accent_hi is lighter than accent_lo",
                  sum(theme.parse_hex(p["accent_hi"]))
                  > sum(theme.parse_hex(p["accent_lo"])))
        tc.check(f"palette({name}): the selection tone is darker than the raw "
                  f"accent, so white text stays readable on it",
                  sum(theme.parse_hex(p["select"]))
                  < sum(theme.parse_hex(p["accent"])))

    accents = {theme.palette(n)["accent"] for n in flavours}
    tc.check("every flavour has a distinct accent — otherwise the menu "
              "offers choices that look identical",
              len(accents) == len(flavours))

    bases = {theme.palette(n)["base"] for n in flavours}
    tc.check("flavours share one base tone, so switching accent does not "
              "relearn the whole interface", len(bases) == 1)

    # ── stylesheet ────────────────────────────────────────────────────────
    for name in flavours:
        qss = theme.build_stylesheet(name)
        tc.check(f"build_stylesheet({name}): produces a non-trivial sheet",
                  len(qss) > 2000, f"{len(qss)} chars")
        tc.check(f"build_stylesheet({name}): leaves no unsubstituted $token — "
                  f"Qt drops such a rule silently",
                  "$" not in qss,
                  qss[max(0, qss.find("$") - 40):qss.find("$") + 40])
        tc.check(f"build_stylesheet({name}): braces balance",
                  qss.count("{") == qss.count("}"),
                  f"{qss.count('{')} open, {qss.count('}')} close")
        tc.check(f"build_stylesheet({name}): actually carries this flavour's "
                  f"accent", theme.palette(name)["accent"] in qss)

    default_qss = theme.build_stylesheet()
    tc.check("build_stylesheet: defaults to the default flavour",
              default_qss == theme.build_stylesheet(theme.DEFAULT_FLAVOUR))

    # A bare QWidget rule would repaint the container the 3-D viewport lives
    # in; on some drivers the OpenGL surface then renders behind a solid fill.
    import re
    bare_widget = re.search(r"(^|[,\s])QWidget\s*(\{|,)", default_qss)
    tc.check("build_stylesheet: never styles a bare QWidget, which would "
              "reach the 3-D viewport's own container",
              bare_widget is None,
              bare_widget.group(0) if bare_widget else "")

    try:
        theme.build_stylesheet("nope")
        bad_flavour_raised = False
    except ValueError:
        bad_flavour_raised = True
    tc.check("build_stylesheet: refuses an unknown flavour",
              bad_flavour_raised)

    # ── the hand-styled widgets ───────────────────────────────────────────
    # ChipTheme._restyle_own_widgets finds these two by matching a fragment
    # of their stylesheet text. If either fragment ever disappears the
    # restyle silently stops working, so pin them here.
    cap = theme.caption_style()
    tc.check("caption_style: keeps the 'font-size: 9px' marker "
              "ChipTheme matches on", "font-size: 9px" in cap)
    tc.check("caption_style: uses the dim token, not a hard-coded grey",
              theme.palette()["dim"] in cap)

    status = theme.status_label_style()
    tc.check("status_label_style: keeps both markers ChipTheme matches on",
              "border-radius: 3px" in status and "padding: 2px 6px" in status)
    tc.check("status_label_style: is dark, not the old near-white #F5F5F5",
              "#F5F5F5" not in status and theme.palette()["base"] in status)

    _check_opt_in(tc)
    return tc.results


def _check_opt_in(tc):
    """
    The skin must be OPT-IN.

    It was briefly on unless switched off, which meant a workbench repainted
    the whole of FreeCAD before being asked to — and, worse, any loss of the
    preference silently brought it back, because an absent key resolved to
    the default flavour rather than to "off". Applying the skin is a
    deliberate choice; nothing else may turn it on.
    """
    import FreeCAD
    import ui.ChipTheme as chip_theme

    params = FreeCAD.ParamGet(chip_theme._PREFS)
    before_flavour = params.GetString(chip_theme._K_FLAVOUR, "")
    before_optin = params.GetBool(chip_theme._K_OPTIN, False)
    try:
        params.RemString(chip_theme._K_FLAVOUR)
        tc.check("an absent preference means the skin is OFF, not the "
                  "default flavour",
                  chip_theme.saved_flavour() == ""
                  and chip_theme.is_enabled() is False,
                  repr(chip_theme.saved_flavour()))

        chip_theme.set_saved_flavour("copper")
        tc.check("an explicit choice still enables it",
                  chip_theme.is_enabled() is True)

        # The one-shot migration clears a flavour carried over from the
        # on-by-default era, and does not fire twice.
        params.SetBool(chip_theme._K_OPTIN, False)
        tc.check("migrate_to_opt_in switches an inherited flavour off",
                  chip_theme.migrate_to_opt_in() is True
                  and chip_theme.is_enabled() is False)
        chip_theme.set_saved_flavour("gold")
        tc.check("migrate_to_opt_in never fires a second time, so a "
                  "deliberate later choice survives",
                  chip_theme.migrate_to_opt_in() is False
                  and chip_theme.is_enabled() is True)
    finally:
        params.SetString(chip_theme._K_FLAVOUR, before_flavour)
        params.SetBool(chip_theme._K_OPTIN, before_optin)
