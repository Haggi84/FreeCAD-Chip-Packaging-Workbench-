# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.ico and core.desktop_shortcut — the Windows desktop
launcher.

Neither the COM call that writes the .lnk nor Qt's SVG rasteriser can run
here, and neither is where the risk is. The risk is in the parts that are
pure data: an .ico directory whose offsets are wrong renders as a blank
square with no error at all, a launcher macro with a syntax error fails
inside FreeCAD's startup where nobody sees the traceback, and a PowerShell
argument with a quote in it turns into a different command. All three are
checked by parsing back what was produced rather than by re-deriving it.
"""

import os
import struct

from _harness import TestCase

import core.ico as ico
import core.desktop_shortcut as ds


def _png_size(data):
    """(width, height) from a PNG's IHDR."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not PNG"
    assert data[12:16] == b"IHDR", "IHDR is not the first chunk"
    return struct.unpack(">II", data[16:24])


def run():
    tc = TestCase("desktop_shortcut")
    _check_ico(tc)
    _check_paths(tc)
    _check_macro(tc)
    _check_powershell(tc)
    return tc.results


# ── .ico container ────────────────────────────────────────────────────────

def _check_ico(tc):
    data = ico.render_chip_ico()
    entries = ico.read_ico_directory(data)

    tc.check("build_ico: one directory entry per size",
              len(entries) == len(ico.STANDARD_SIZES),
              f"{len(entries)} vs {len(ico.STANDARD_SIZES)}")

    widths = [w for w, _h, _n, _o in entries]
    tc.check("build_ico: entries are in ascending size order — some shells "
              "take the first entry large enough rather than the best match",
              widths == sorted(widths), f"got {widths}")
    tc.check("build_ico: every requested size is present",
              set(widths) == set(ico.STANDARD_SIZES), f"got {sorted(widths)}")

    # 256 is stored as a literal 0 in the one-byte field; getting that wrong
    # writes a 0x0 icon that renders blank with no error anywhere.
    raw = data[6:6 + 16 * len(entries)]
    byte_widths = {raw[i * 16] for i in range(len(entries))}
    tc.check("build_ico: 256 px is stored as 0 in the byte-wide field",
              0 in byte_widths and 256 not in byte_widths)
    tc.check("read_ico_directory: reports that 0 back as 256",
              256 in widths)

    ok_payloads = True
    detail = ""
    expected_offset = 6 + 16 * len(entries)
    for w, h, nbytes, offset in entries:
        if offset != expected_offset:
            ok_payloads, detail = False, f"{w}px at {offset}, expected {expected_offset}"
            break
        payload = data[offset:offset + nbytes]
        if len(payload) != nbytes:
            ok_payloads, detail = False, f"{w}px payload truncated"
            break
        try:
            pw, ph = _png_size(payload)
        except AssertionError as exc:
            ok_payloads, detail = False, f"{w}px: {exc}"
            break
        if (pw, ph) != (w, h):
            ok_payloads, detail = False, f"entry says {w}x{h}, PNG says {pw}x{ph}"
            break
        expected_offset += nbytes
    tc.check("build_ico: every payload sits at its declared offset and the "
              "PNG's own dimensions match the directory entry", ok_payloads,
              detail)

    tc.check("build_ico: payloads exactly fill the file, no gaps or slack",
              expected_offset == len(data) if ok_payloads else False,
              f"{expected_offset} vs {len(data)}")

    # Refusals
    for label, frames in (
        ("no frames at all", []),
        ("a size above 256", [(512, ico.render_chip_icon_png(16))]),
        ("a size below 1", [(0, ico.render_chip_icon_png(16))]),
        ("data that is not PNG", [(16, b"nope, just bytes")]),
    ):
        try:
            ico.build_ico(frames)
            raised = False
        except ValueError:
            raised = True
        tc.check(f"build_ico: refuses {label} rather than writing an icon "
                  f"that renders blank", raised)

    for bad in (b"", b"\x00\x00\x02\x00\x01\x00"):
        try:
            ico.read_ico_directory(bad)
            raised = False
        except ValueError:
            raised = True
        tc.check(f"read_ico_directory: rejects {bad!r} as not an .ico", raised)

    small = ico.render_chip_icon_png(16)
    tc.check("render_chip_icon_png: honours the requested size",
              _png_size(small) == (16, 16), f"got {_png_size(small)}")

    import core.theme as theme
    for flavour in theme.available_flavours():
        png = ico.render_chip_icon_png(32, flavour)
        tc.check(f"render_chip_icon_png: draws in the {flavour} flavour",
                  _png_size(png) == (32, 32))
    tc.check("render_chip_icon_png: flavours differ in the pixels, not just "
              "in name",
              ico.render_chip_icon_png(32, "copper")
              != ico.render_chip_icon_png(32, "solder"))


# ── path resolution ───────────────────────────────────────────────────────

def _check_paths(tc):
    # OneDrive Known Folder Move is the norm on managed Windows 11; writing
    # to %USERPROFILE%\Desktop there puts the icon where nobody sees it.
    env = {"OneDrive": r"C:\Users\x\OneDrive", "USERPROFILE": r"C:\Users\x"}
    got = ds.desktop_dir(env, isdir=lambda p: "OneDrive" in p)
    tc.check("desktop_dir: prefers the OneDrive-redirected Desktop when it "
              "is the one that exists",
              got == os.path.join(r"C:\Users\x\OneDrive", "Desktop"), got)

    got = ds.desktop_dir(env, isdir=lambda p: "OneDrive" not in p)
    tc.check("desktop_dir: falls back to the profile Desktop when OneDrive's "
              "does not exist",
              got == os.path.join(r"C:\Users\x", "Desktop"), got)

    got = ds.desktop_dir(env, isdir=lambda p: False)
    tc.check("desktop_dir: with neither present, still returns the profile "
              "path — the right place to create it",
              got == os.path.join(r"C:\Users\x", "Desktop"), got)

    got = ds.desktop_dir({"USERPROFILE": r"C:\Users\y"}, isdir=lambda p: True)
    tc.check("desktop_dir: no OneDrive variable at all is not an error",
              got == os.path.join(r"C:\Users\y", "Desktop"), got)

    root = ds.workbench_root()
    tc.check("workbench_root: points at the workbench, not at core/",
              os.path.isfile(os.path.join(root, "InitGui.py")), root)

    p = ds.plan(desktop=r"C:\Desk")
    for key in ("lnk", "target", "arguments", "workdir", "icon", "macro",
                "description"):
        tc.check(f"plan: provides '{key}'", bool(p.get(key)), repr(p.get(key)))
    tc.check("plan: the shortcut is a .lnk on the given desktop",
              p["lnk"] == os.path.join(r"C:\Desk", f"{ds.SHORTCUT_NAME}.lnk"),
              p["lnk"])
    tc.check("plan: the icon is an .ico", p["icon"].endswith(".ico"), p["icon"])
    tc.check("plan: targets the GUI executable, never freecadcmd — a "
              "shortcut to the console build opens and exits at once",
              "freecadcmd" not in os.path.basename(p["target"]).lower(),
              p["target"])
    tc.check("plan: the macro path is quoted in the arguments, because the "
              "install path contains spaces",
              p["arguments"].startswith('"') and p["arguments"].endswith('"'),
              p["arguments"])
    tc.check("plan: the argument is the launcher macro",
              ds.LAUNCHER_BASENAME in p["arguments"], p["arguments"])
    tc.check("plan: paths are normalised — FreeCAD.getHomePath() ends with a "
              "separator, which otherwise shows up as a doubled backslash in "
              "the shortcut's properties dialog",
              "\\\\" not in p["target"] and "\\\\" not in p["workdir"],
              f"{p['target']} | {p['workdir']}")
    tc.check("plan: the description stays ASCII, since it travels through a "
              "generated .ps1 and the Windows shell's property store",
              p["description"].isascii(), p["description"])


# ── the generated launcher ────────────────────────────────────────────────

def _check_macro(tc):
    text = ds.launcher_macro_text()

    try:
        compile(text, "<launcher>", "exec")
        compiles = True
        why = ""
    except SyntaxError as exc:
        compiles, why = False, f"{exc}"
    tc.check("launcher_macro_text: is valid Python — a syntax error here "
              "fails inside FreeCAD's startup where nobody sees it",
              compiles, why)

    tc.check("launcher_macro_text: names the workbench it should open",
              repr(ds.WORKBENCH_NAME) in text)
    tc.check("launcher_macro_text: checks the workbench is registered before "
              "activating it", "listWorkbenches" in text)
    tc.check("launcher_macro_text: retries, because Mod/ discovery is on a "
              "timer and one immediate call is a race",
              "singleShot" in text)
    tc.check("launcher_macro_text: supports both Qt bindings the workbench "
              "supports", "PySide6" in text and "PySide2" in text)
    tc.check("launcher_macro_text: gives up eventually rather than retrying "
              "for the whole session",
              "_LEFT" in text and "PrintWarning" in text)

    custom = ds.launcher_macro_text("SomeOtherWorkbench", retries=3)
    tc.check("launcher_macro_text: honours a custom workbench name",
              "'SomeOtherWorkbench'" in custom)
    tc.check("launcher_macro_text: honours a custom retry count",
              "[3]" in custom)


# ── PowerShell generation ─────────────────────────────────────────────────

def _check_powershell(tc):
    script = ds.shortcut_script(
        r"C:\Desk\App.lnk", r"C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe",
        arguments='"C:\\Mod\\launch.FCMacro"', icon=r"C:\Mod\app.ico",
        workdir=r"C:\Program Files\FreeCAD 1.1\bin", description="Desc")

    tc.check("shortcut_script: uses WScript.Shell", "WScript.Shell" in script)
    tc.check("shortcut_script: saves the shortcut", "$s.Save()" in script)
    tc.check("shortcut_script: stops on the first error, so a failure is "
              "reported rather than half-applied",
              "$ErrorActionPreference = 'Stop'" in script)
    tc.check("shortcut_script: appends the ',0' icon index — without it the "
              "shell sometimes falls back to the target's own icon",
              r"'C:\Mod\app.ico,0'" in script, script)
    tc.check("shortcut_script: sets every field it was given",
              all(f"$s.{f}" in script for f in
                  ("TargetPath", "Arguments", "WorkingDirectory",
                   "IconLocation", "Description")))

    lean = ds.shortcut_script(r"C:\a.lnk", r"C:\b.exe")
    tc.check("shortcut_script: omits fields it was not given rather than "
              "setting them empty",
              "$s.Arguments" not in lean and "$s.IconLocation" not in lean)

    # PowerShell escapes a single quote by doubling it. A path with an
    # apostrophe in it — "C:\Users\O'Brien\Desktop" — would otherwise end the
    # string early and run whatever followed as code.
    tricky = ds.shortcut_script(r"C:\Users\O'Brien\Desk\a.lnk", r"C:\b.exe")
    tc.check("shortcut_script: doubles a single quote in a path instead of "
              "letting it terminate the string",
              "'C:\\Users\\O''Brien\\Desk\\a.lnk'" in tricky, tricky)
    tc.check("_ps_quote: wraps in single quotes",
              ds._ps_quote("plain") == "'plain'")
    tc.check("_ps_quote: escapes every quote, not just the first",
              ds._ps_quote("a'b'c") == "'a''b''c'")

    tc.check("shortcut_arguments: quotes the macro path",
              ds.shortcut_arguments(r"C:\Program Files\x.FCMacro")
              == '"C:\\Program Files\\x.FCMacro"')
