# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.chip_texture — rasterising a die layout so a chip
proxy can wear a picture of itself.

Applying the image to a face needs a 3-D view and cannot be tested here, so
the split is deliberate: this module does the rasterising only, and that
part is pure arithmetic on a pixel buffer. What matters is that the image
puts things where the layout actually has them — a texture that is mirrored,
rotated or stretched would misrepresent where the pads are, which is the one
thing a proxy has to get right.
"""

import struct
import zlib

from _harness import TestCase

import core.chip_texture as ct


def _pixel(raster, x, y):
    i = (y * raster.w + x) * 3
    return tuple(raster.buf[i:i + 3])


def _decode_png(data):
    """(width, height, rows) from our own PNG bytes, so the tests read the
    real encoded output rather than trusting the buffer it came from."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    width = height = None
    idat = b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", payload[:10])
            assert depth == 8 and colour == 2
        elif tag == b"IDAT":
            idat += payload
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 3
    rows = []
    for y in range(height):
        start = y * (stride + 1)
        assert raw[start] == 0          # filter type None
        rows.append(raw[start + 1:start + 1 + stride])
    return width, height, rows


def run():
    tc = TestCase("chip_texture")

    # ── colour parsing ──────────────────────────────────────────────────────
    tc.check("_parse_hex: reads a #rrggbb colour",
              ct._parse_hex("#ff8000") == (255, 128, 0))
    tc.check("_parse_hex: tolerates a missing hash",
              ct._parse_hex("00ff00") == (0, 255, 0))
    tc.check("_parse_hex: a malformed or missing colour falls back rather "
              "than stopping the whole render",
              ct._parse_hex(None) == (128, 128, 128)
              and ct._parse_hex("nope") == (128, 128, 128))

    # ── polygon fill ────────────────────────────────────────────────────────
    r = ct.Raster(10, 10, background=(0, 0, 0))
    r.fill_polygon([(2, 2), (8, 2), (8, 8), (2, 8)], (255, 0, 0))
    tc.check("fill_polygon: fills the inside", _pixel(r, 5, 5) == (255, 0, 0))
    tc.check("fill_polygon: leaves the outside alone",
              _pixel(r, 0, 0) == (0, 0, 0) and _pixel(r, 9, 9) == (0, 0, 0))

    r2 = ct.Raster(10, 10, background=(0, 0, 0))
    r2.fill_polygon([(-50, -50), (50, -50), (50, 50), (-50, 50)], (9, 9, 9))
    tc.check("fill_polygon: a polygon far larger than the image is clipped, "
              "not an error",
              _pixel(r2, 5, 5) == (9, 9, 9))

    r3 = ct.Raster(10, 10, background=(0, 0, 0))
    r3.fill_polygon([(2, 2), (8, 2)], (255, 255, 255))
    tc.check("fill_polygon: fewer than three points draws nothing",
              _pixel(r3, 5, 2) == (0, 0, 0))

    r4 = ct.Raster(10, 10, background=(0, 0, 0))
    r4.fill_polygon([(0, 0), (10, 0), (10, 10), (0, 10)], (200, 100, 50), alpha=0.5)
    px = _pixel(r4, 5, 5)
    tc.check("fill_polygon: alpha blends against what is already there",
              px != (200, 100, 50) and px != (0, 0, 0) and px[0] > 50,
              f"got {px}")

    # ── whole-layout render ─────────────────────────────────────────────────
    # A single square in the LOWER-LEFT quarter of a wide die. Getting this
    # one right is what proves the mapping: the layout's +Y is up, an image's
    # +Y is down, so a missing flip would put it in the upper-left instead.
    foot = (0.0, 0.0, 4.0, 2.0)
    marker = [[(0.2, 0.2), (1.8, 0.2), (1.8, 0.8), (0.2, 0.8)]]
    png, w, h = ct.render_layout_png([("#ff0000", 1.0, marker)], foot, pixels=200)
    tc.check("render_layout_png: keeps the die's aspect ratio (4:2 -> 2:1)",
              w == 200 and h == 100, f"got {w} x {h}")

    dw, dh, rows = _decode_png(png)
    tc.check("render_layout_png: the encoded PNG matches the reported size",
              (dw, dh) == (w, h), f"got {dw} x {dh}")

    def at(px, py):
        return tuple(rows[py][px * 3:px * 3 + 3])

    tc.check("render_layout_png: the shape lands in the LOWER half, as it is "
              "in the layout (image Y is flipped)",
              at(50, 75) == (255, 0, 0), f"got {at(50, 75)}")
    tc.check("render_layout_png: ...and not in the upper half",
              at(50, 25) != (255, 0, 0), f"got {at(50, 25)}")
    tc.check("render_layout_png: the shape is on the LEFT, matching its x",
              at(25, 75) == (255, 0, 0) and at(180, 75) != (255, 0, 0),
              f"left {at(25, 75)} right {at(180, 75)}")
    tc.check("render_layout_png: bare die shows the background",
              at(195, 5) == (24, 24, 28), f"got {at(195, 5)}")

    # Later layers paint over earlier ones, so callers control the stack.
    png2, _w2, _h2 = ct.render_layout_png(
        [("#ff0000", 1.0, [[(0, 0), (4, 0), (4, 2), (0, 2)]]),
         ("#00ff00", 1.0, [[(0, 0), (4, 0), (4, 2), (0, 2)]])],
        foot, pixels=64)
    _dw2, _dh2, rows2 = _decode_png(png2)
    tc.check("render_layout_png: layers are drawn in the order given, so a "
              "caller can stack metals bottom-up",
              tuple(rows2[16][30:33]) == (0, 255, 0),
              f"got {tuple(rows2[16][30:33])}")

    # ── guards ──────────────────────────────────────────────────────────────
    # A long thin die: the aspect ratio must survive. Clamping only the
    # derived dimension up to the minimum would stretch the image, which
    # moves where the pads appear to be.
    _p, tw, th = ct.render_layout_png([], (0.0, 0.0, 1.0, 5.0), pixels=100)
    tc.check("render_layout_png: a 1:5 die keeps its 1:5 aspect ratio",
              abs((tw / th) - (1.0 / 5.0)) < 0.02, f"got {tw} x {th}")
    tc.check("render_layout_png: ...and is still at least the minimum size",
              min(tw, th) >= ct._MIN_PIXELS, f"got {tw} x {th}")

    # Textures must have power-of-two sides: OpenGL 1.x rejects anything
    # else without an extension, which surfaces as an OpenGL error rather
    # than a blank face. The picture stays correct because it is mapped
    # across the face's 0..1 range and the face has the real proportions.
    def _is_pow2(n):
        return n > 0 and (n & (n - 1)) == 0
    for foot_t in [(0.0, 0.0, 4.0, 1.7), (0.0, 0.0, 1.0, 5.0),
                   (0.0, 0.0, 3.0, 3.0), (0.0, 0.0, 7.3, 2.1)]:
        _pp, pw, ph = ct.render_layout_png([], foot_t, pixels=1024,
                                            power_of_two=True)
        tc.check("render_layout_png: power_of_two gives valid texture sides "
                  f"for a {foot_t[2]:.1f} x {foot_t[3]:.1f} die",
                  _is_pow2(pw) and _is_pow2(ph), f"got {pw} x {ph}")
    _pp, nw, nh = ct.render_layout_png([], (0.0, 0.0, 4.0, 1.7), pixels=1024)
    tc.check("render_layout_png: without the flag the size is left "
              "aspect-exact (right for a picture, wrong for a texture)",
              not (_is_pow2(nw) and _is_pow2(nh)), f"got {nw} x {nh}")
    tc.check("_to_power_of_two: snaps to the nearest power of two",
              ct._to_power_of_two(437) in (256, 512)
              and ct._to_power_of_two(1000) == 1024
              and ct._to_power_of_two(1) == ct._MIN_PIXELS)

    _p, ew, eh = ct.render_layout_png([], (0.0, 0.0, 3.0, 3.0), pixels=256)
    tc.check("render_layout_png: a square die renders square",
              ew == eh == 256, f"got {ew} x {eh}")

    try:
        ct.render_layout_png([], (0.0, 0.0, 0.0, 0.0))
        degenerate_raised = False
    except ValueError:
        degenerate_raised = True
    tc.check("render_layout_png: a degenerate footprint is refused rather "
              "than producing a zero-sized image",
              degenerate_raised)

    tc.check("pixel size is clamped to something sane",
              ct._clamp_size(1) == ct._MIN_PIXELS
              and ct._clamp_size(10 ** 6) == ct._MAX_PIXELS)

    _check_local_footprint(tc)
    return tc.results


def _check_local_footprint(tc):
    """
    The rectangle a texture is mapped onto must be the block's OWN, not its
    world position.

    This is the defect that made texturing work on one chip and fail on the
    next: a proxy's box is baked at the layout coordinates with an identity
    Placement, so moving the chip changes Shape.BoundBox while the geometry
    Coin actually traverses — below the view provider's transform — stays
    where it was. Mapping with world values slid the picture off every chip
    that had been moved, which is every chip after the first.
    """
    import FreeCAD
    import Part
    from _harness import new_document

    doc = new_document("TextureFootprint")
    try:
        block = doc.addObject("Part::Feature", "Blk")
        block.Shape = Part.makeBox(3.0, 2.0, 0.1,
                                    FreeCAD.Vector(5.0, 7.0, 0.0))
        doc.recompute()

        from gds.ChipTextureCommand import local_footprint
        before = local_footprint(block)
        tc.check("local_footprint: reports the block's baked extent",
                  all(abs(a - b) < 1e-9
                      for a, b in zip(before, (5.0, 7.0, 8.0, 9.0))),
                  f"got {before}")

        # Move it the way a user positions a second chip on the carrier.
        block.Placement = FreeCAD.Placement(
            FreeCAD.Vector(100.0, 200.0, 0.0), FreeCAD.Rotation())
        doc.recompute()

        after = local_footprint(block)
        tc.check("local_footprint: a moved chip keeps the same mapping "
                  "rectangle, so its texture does not slide off",
                  all(abs(a - b) < 1e-9 for a, b in zip(before, after)),
                  f"{before} -> {after}")

        bb = block.Shape.BoundBox
        tc.check("local_footprint: differs from the world bounding box once "
                  "moved — pins down which of the two the mapping needs",
                  abs(bb.XMin - after[0]) > 1.0,
                  f"world XMin {bb.XMin}, local {after[0]}")
    finally:
        FreeCAD.closeDocument(doc.Name)
