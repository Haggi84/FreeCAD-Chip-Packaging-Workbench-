# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
A picture of the layout for a chip proxy to wear.

A proxy block loads in about a third of a second where the full geometry
takes seventy (measured on a real 15 MB, 70-layer chip: 92,000 solids,
630,000 faces, 1.4 M triangles). What it costs is recognition — every proxy
is the same grey box, so with several chips on a board you cannot tell at a
glance which is which, or which way round one is.

Painting the layout onto the block's top face buys that back for nothing:
the polygons are rasterised ONCE into a bitmap and handed to the graphics
card as a texture, so the scene still contains a single box. Layer colours
come from the same KLayout .lyp palette the full import uses, so a textured
proxy and the real thing read alike.

Deliberately NOT a replacement for the real geometry: a texture cannot be
selected, routed on, or measured. It is a visual aid, which is why it is an
option rather than the default.

This module owns the rasterising only — turning polygons into an image. It
holds no FreeCAD view code, so it is covered by headless tests; applying the
image to a face is the caller's job (see gds/ChipTextureCommand.py).
"""

import math
import os
import struct
import zlib


DEFAULT_PIXELS = 1024
# Anything below this many pixels across is not worth the rasterising time.
_MIN_PIXELS = 32
_MAX_PIXELS = 8192


def _clamp_size(pixels: int) -> int:
    return max(_MIN_PIXELS, min(int(pixels), _MAX_PIXELS))


def _parse_hex(colour, default=(128, 128, 128)):
    """'#rrggbb' -> (r, g, b). Tolerates None and malformed values, because
    a layer with no colour in the .lyp must not stop the whole render."""
    if not colour:
        return default
    s = str(colour).strip().lstrip("#")
    if len(s) != 6:
        return default
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return default


class Raster:
    """
    A plain RGB pixel buffer with polygon fill.

    Deliberately self-contained rather than built on Qt or PIL: this runs
    inside FreeCAD's interpreter during an import, where an extra imaging
    dependency would be one more thing to install, and the only drawing
    needed is "fill a polygon".
    """

    def __init__(self, width: int, height: int, background=(24, 24, 28)):
        self.w = int(width)
        self.h = int(height)
        r, g, b = background
        self.buf = bytearray(bytes((r, g, b)) * (self.w * self.h))

    def _blend(self, x: int, y: int, rgb, alpha: float):
        i = (y * self.w + x) * 3
        if alpha >= 1.0:
            self.buf[i] = rgb[0]
            self.buf[i + 1] = rgb[1]
            self.buf[i + 2] = rgb[2]
            return
        inv = 1.0 - alpha
        self.buf[i] = int(self.buf[i] * inv + rgb[0] * alpha)
        self.buf[i + 1] = int(self.buf[i + 1] * inv + rgb[1] * alpha)
        self.buf[i + 2] = int(self.buf[i + 2] * inv + rgb[2] * alpha)

    def fill_polygon(self, pts, rgb, alpha: float = 1.0):
        """
        Scanline fill of a polygon given in PIXEL coordinates.

        Even-odd rule, matching how layout tools treat self-overlapping
        outlines, and clipped to the buffer so a polygon reaching past the
        die edge costs nothing extra.
        """
        if len(pts) < 3 or alpha <= 0.0:
            return
        ys = [p[1] for p in pts]
        y0 = max(0, int(math.floor(min(ys))))
        y1 = min(self.h - 1, int(math.ceil(max(ys))))
        if y1 < y0:
            return
        n = len(pts)
        for y in range(y0, y1 + 1):
            yc = y + 0.5
            xs = []
            for i in range(n):
                ax, ay = pts[i]
                bx, by = pts[(i + 1) % n]
                if (ay > yc) == (by > yc):
                    continue
                if abs(by - ay) < 1e-12:
                    continue
                xs.append(ax + (yc - ay) * (bx - ax) / (by - ay))
            if not xs:
                continue
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                xa = max(0, int(math.floor(xs[k] + 0.5)))
                xb = min(self.w - 1, int(math.ceil(xs[k + 1] - 0.5)))
                for x in range(xa, xb + 1):
                    self._blend(x, y, rgb, alpha)

    def to_png_bytes(self) -> bytes:
        """Encode as PNG. Written out by hand (zlib is in the standard
        library) so no imaging package is required inside FreeCAD."""
        raw = bytearray()
        stride = self.w * 3
        for y in range(self.h):
            raw.append(0)                      # filter type 0 (None)
            raw += self.buf[y * stride:(y + 1) * stride]

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        header = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
                + chunk(b"IEND", b""))


def _to_power_of_two(n: int) -> int:
    """Nearest power of two, at least _MIN_PIXELS."""
    n = max(int(n), 1)
    lo = 1 << (n.bit_length() - 1)
    hi = lo << 1
    best = lo if (n - lo) <= (hi - n) else hi
    return max(best, _MIN_PIXELS)


def render_layout_png(polygons_by_layer, footprint_mm, pixels: int = DEFAULT_PIXELS,
                      background=(24, 24, 28), power_of_two: bool = False):
    """
    Rasterise a die layout to PNG bytes.

    *polygons_by_layer* is an ordered sequence of
    (colour_hex, alpha, [polygon, ...]) with each polygon a list of (x, y)
    in millimetres; drawn in the order given, so callers put lower metal
    first. *footprint_mm* is (xmin, ymin, xmax, ymax) — the die outline the
    image maps onto.

    The image keeps the die's ASPECT RATIO: the texture is applied to the
    block's top face, and a stretched layout would misrepresent where the
    pads are, which is the one thing a proxy has to get right.
    """
    xmin, ymin, xmax, ymax = footprint_mm
    w_mm, h_mm = xmax - xmin, ymax - ymin
    if w_mm <= 0 or h_mm <= 0:
        raise ValueError("render_layout_png: degenerate footprint")

    size = _clamp_size(pixels)
    if w_mm >= h_mm:
        width, height = size, int(round(size * h_mm / w_mm))
    else:
        height, width = size, int(round(size * w_mm / h_mm))
    # A long thin die derives a very small second dimension. Raise BOTH to
    # clear the minimum rather than clamping one of them, which would
    # silently stretch the image — and a stretched layout misplaces the pads,
    # the one thing the picture has to get right.
    width, height = max(width, 1), max(height, 1)
    if min(width, height) < _MIN_PIXELS:
        k = _MIN_PIXELS / float(min(width, height))
        width, height = int(round(width * k)), int(round(height * k))
    if max(width, height) > _MAX_PIXELS:
        k = _MAX_PIXELS / float(max(width, height))
        width, height = max(1, int(round(width * k))), max(1, int(round(height * k)))

    if power_of_two:
        # OpenGL 1.x — which Coin still targets and which this workbench
        # cannot assume away — only accepts textures whose sides are powers
        # of two unless the non-power-of-two extension is present. A
        # 1024 x 437 image is a perfectly good PICTURE and an invalid
        # TEXTURE, which surfaces as an OpenGL error rather than a blank
        # face.
        #
        # Snapping the sides costs nothing visually: the image is mapped
        # across the face's 0..1 range, and the face already has the die's
        # real proportions, so the layout still lands in the right place —
        # only the pixels stop being exactly square.
        width, height = _to_power_of_two(width), _to_power_of_two(height)

    img = Raster(width, height, background)
    sx = width / w_mm
    sy = height / h_mm
    for colour, alpha, polys in polygons_by_layer:
        rgb = _parse_hex(colour)
        for poly in polys:
            if len(poly) < 3:
                continue
            # Y is flipped: image rows run downward, layout Y runs upward.
            img.fill_polygon(
                [((px - xmin) * sx, height - (py - ymin) * sy) for px, py in poly],
                rgb, alpha)
    return img.to_png_bytes(), width, height


def write_png(path: str, data: bytes) -> str:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path
