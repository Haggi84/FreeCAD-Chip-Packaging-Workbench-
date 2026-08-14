# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
A Windows .ico file, written by hand.

Needed because a desktop shortcut wants an .ico and nothing in the stack
produces one: Qt writes a single size per file, FreeCAD ships .svg/.png, and
Pillow is not a dependency of this workbench and is not going to become one
for the sake of one icon.

The container is genuinely simple — a six-byte header, a sixteen-byte
directory entry per image, then the payloads — and since Windows Vista those
payloads may be PNG rather than the old DIB format. core.chip_texture
already writes PNGs with nothing but stdlib zlib, so the whole file can be
produced from parts that already exist and are already tested.

Multiple sizes in one file is the point, not a flourish: Windows picks 16 px
for the taskbar, 32 for the desktop, 48 for the alt-tab switcher and 256 for
large-icon views, and an .ico holding only one of them gets the rest by
smearing that one — which is exactly how a shortcut ends up looking blurry.
"""

import struct

import core.chip_texture as chip_texture
import core.theme as theme

# What Windows actually asks for. 256 must be last only by convention, but
# the directory is written in ascending order because some older shells stop
# at the first entry big enough rather than scanning for the best match.
STANDARD_SIZES = (16, 24, 32, 48, 64, 128, 256)

_ICONDIR = "<HHH"        # reserved, type, count
_ICONDIRENTRY = "<BBBBHHII"   # w, h, colours, reserved, planes, bpp, bytes, offset

_HEADER_LEN = 6
_ENTRY_LEN = 16
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def build_ico(frames) -> bytes:
    """
    frames: iterable of (size_px, png_bytes) -> .ico file bytes.

    Sizes must be 1..256; 256 is stored as a literal 0 in the directory,
    which is the format's way of fitting it into one byte. Getting that
    wrong writes a 0x0 icon that silently renders as blank, so it is worth
    the special case being explicit.
    """
    frames = sorted(frames, key=lambda f: f[0])
    if not frames:
        raise ValueError("An .ico needs at least one image.")
    for size, png in frames:
        if not 1 <= int(size) <= 256:
            raise ValueError(f"Icon size {size} out of range (1..256).")
        if not png.startswith(_PNG_MAGIC):
            raise ValueError(f"Frame {size} is not PNG data.")

    offset = _HEADER_LEN + _ENTRY_LEN * len(frames)
    directory, payload = [], []
    for size, png in frames:
        dim = 0 if int(size) == 256 else int(size)
        directory.append(struct.pack(
            _ICONDIRENTRY, dim, dim, 0, 0, 1, 32, len(png), offset))
        payload.append(png)
        offset += len(png)

    head = struct.pack(_ICONDIR, 0, 1, len(frames))
    return head + b"".join(directory) + b"".join(payload)


def write_ico(path: str, data: bytes) -> str:
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# ── Fallback artwork ──────────────────────────────────────────────────────
# Used when Qt cannot rasterise the workbench's own .svg (headless, or a Qt
# build without the SVG image plugin). Drawn from rectangles only, because
# that is all core.chip_texture.Raster offers and all a chip motif needs:
# a chamfered die, a core, and pads down all four sides.

def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _chamfered(x0, y0, x1, y1, c):
    """A square with its corners cut — reads as a die at small sizes where a
    rounded rectangle would just look like a blurry square."""
    return [
        (x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c),
        (x1 - c, y1), (x0 + c, y1), (x0, y1 - c), (x0, y0 + c),
    ]


def render_chip_icon_png(size: int, flavour: str = theme.DEFAULT_FLAVOUR) -> bytes:
    """One square icon frame as PNG bytes, in the given theme flavour."""
    size = max(8, int(size))
    p = theme.palette(flavour)
    base = theme.parse_hex(p["base"])
    body = theme.parse_hex(p["raised"])
    edge = theme.parse_hex(p["border"])
    accent = theme.parse_hex(p["accent"])
    accent_hi = theme.parse_hex(p["accent_hi"])
    core_col = theme.parse_hex(p["panel"])

    r = chip_texture.Raster(size, size, background=base)

    def s(v):
        """Normalised 0..1 coordinate -> pixels, so the motif scales."""
        return v * size

    # Die body, with a one-pixel-ish lighter rim so the silhouette survives
    # against a dark taskbar.
    r.fill_polygon(_chamfered(s(0.06), s(0.06), s(0.94), s(0.94), s(0.12)), edge)
    r.fill_polygon(_chamfered(s(0.09), s(0.09), s(0.91), s(0.91), s(0.11)), body)

    # Pads: five per side, inset from the rim.
    n = 5
    pad_len = 0.10
    pad_w = 0.072
    span = 0.62
    start = (1.0 - span) / 2.0
    step = span / (n - 1)
    for i in range(n):
        c = start + i * step
        # top / bottom
        r.fill_polygon(_rect(s(c - pad_w / 2), s(0.13),
                              s(c + pad_w / 2), s(0.13 + pad_len)), accent)
        r.fill_polygon(_rect(s(c - pad_w / 2), s(0.87 - pad_len),
                              s(c + pad_w / 2), s(0.87)), accent)
        # left / right
        r.fill_polygon(_rect(s(0.13), s(c - pad_w / 2),
                              s(0.13 + pad_len), s(c + pad_w / 2)), accent)
        r.fill_polygon(_rect(s(0.87 - pad_len), s(c - pad_w / 2),
                              s(0.87), s(c + pad_w / 2)), accent)

    # Core block, and a lighter inner square so there is contrast at 16 px.
    r.fill_polygon(_rect(s(0.31), s(0.31), s(0.69), s(0.69)), core_col)
    r.fill_polygon(_rect(s(0.38), s(0.38), s(0.62), s(0.62)), edge)

    # Pin-1 marker, the one asymmetry that stops the icon reading as a
    # featureless grid.
    r.fill_polygon(_rect(s(0.15), s(0.15), s(0.23), s(0.23)), accent_hi)
    return r.to_png_bytes()


def render_chip_ico(sizes=STANDARD_SIZES,
                    flavour: str = theme.DEFAULT_FLAVOUR) -> bytes:
    """A complete multi-size .ico of the fallback chip motif."""
    return build_ico([(n, render_chip_icon_png(n, flavour)) for n in sizes])


# ── Reading back ──────────────────────────────────────────────────────────

def read_ico_directory(data: bytes):
    """
    [(width, height, nbytes, offset), ...] from .ico bytes.

    Exists so the tests can verify what was written by parsing it rather
    than by re-deriving it from the same code that produced it.
    """
    if len(data) < _HEADER_LEN:
        raise ValueError("Too short to be an .ico.")
    reserved, kind, count = struct.unpack(_ICONDIR, data[:_HEADER_LEN])
    if reserved != 0 or kind != 1:
        raise ValueError(f"Not an .ico (reserved={reserved}, type={kind}).")
    out = []
    for i in range(count):
        at = _HEADER_LEN + i * _ENTRY_LEN
        w, h, _c, _r, _p, _bpp, nbytes, offset = struct.unpack(
            _ICONDIRENTRY, data[at:at + _ENTRY_LEN])
        out.append((w or 256, h or 256, nbytes, offset))
    return out
