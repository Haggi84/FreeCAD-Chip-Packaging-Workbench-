# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Paint a chip proxy with a picture of its own layout — optional.

A proxy imports in about a third of a second where full geometry takes
seventy (measured on a real 15 MB, 70-layer chip: 92,000 solids, 630,000
faces, 1.4 M triangles), which is what makes several chips in one document
practical at all. The price is recognition: every proxy is the same grey
box. This command rasterises the layout ONCE and hands it to the graphics
card as a texture, so the block still costs one box while looking like the
chip it stands for.

Strictly a visual aid, and therefore opt-in: a texture cannot be selected,
routed on, bonded to or measured. Everything the workflow depends on —
pads, footprint, thickness — lives in the proxy itself, exactly as before.

The rasterising is in core.chip_texture (Qt-free and headlessly tested);
this module only reads the GDS polygons and applies the finished image to
the block's top face.
"""

import os
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

import core.chip_texture as chip_texture
from Get_Path import get_icon

# Layers whose polygons are worth drawing, cheapest useful default: the
# metal stack carries the recognisable pattern, and it is also where the
# measured weight of a full import sits (the top five metal layers are
# ~85% of all faces), so a picture of them is the best value per pixel.
_DEFAULT_MAX_POLYS = 60000


def _texture_dir():
    from core.Core_Functionality import gds_cache_dir
    return gds_cache_dir().parent / "chip_textures"


def _selected_proxy_blocks():
    """Proxy blocks in the current selection."""
    out, seen = [], set()

    def take(o):
        # Selecting a group AND its block would otherwise queue the same
        # block twice, which with the toggle below means texture-on then
        # immediately texture-off — i.e. apparently nothing happening.
        if o.Name not in seen:
            seen.add(o.Name)
            out.append(o)

    try:
        sel = FreeCADGui.Selection.getSelection()
    except Exception:
        sel = []
    for o in sel:
        if getattr(o, "IsChipProxy", False):
            take(o)
            continue
        # Selecting the group is the natural thing to do; accept it too.
        for member in (getattr(o, "Group", None) or []):
            if getattr(member, "IsChipProxy", False):
                take(member)
    return out


def _gds_path_of(block):
    # SourceGDS is the property core.chip_proxy actually writes; the others
    # are only tolerated in case a proxy was made by something else.
    for prop in ("SourceGDS", "GDSPath", "SourceFile", "GdsFile"):
        val = getattr(block, prop, None)
        if val:
            return str(val)
    return ""


def _layer_polygons(gds_path, footprint, max_polys=_DEFAULT_MAX_POLYS):
    """
    [(colour_hex, alpha, [polygon, ...]), ...] for the layout, bottom layer
    first. Falls back to no colour information rather than failing — a grey
    picture of the right shapes is still far more use than a blank box.
    """
    import gdstk
    lib = gdstk.read_gds(gds_path)
    top = lib.top_level()
    if not top:
        return []
    scale = lib.unit / 1e-3          # library units -> millimetres

    colours = {}
    try:
        from core.tech.layer_info import get_layer_colors  # optional
        colours = get_layer_colors() or {}
    except Exception:
        colours = {}

    by_layer = {}
    for cell in top:
        for poly in cell.get_polygons(depth=None):
            key = (poly.layer, poly.datatype)
            by_layer.setdefault(key, []).append(poly)

    # Draw the busiest layers last so they read on top, and cap the total so
    # a million-polygon layout still renders in reasonable time.
    ordered = sorted(by_layer.items(), key=lambda kv: kv[0])
    out = []
    budget = max_polys
    for (layer, datatype), polys in ordered:
        if budget <= 0:
            break
        take = polys[:budget]
        budget -= len(take)
        hexcol = None
        entry = colours.get((layer, datatype)) if colours else None
        if isinstance(entry, dict):
            hexcol = entry.get("fill_hex") or entry.get("frame_hex")
        elif isinstance(entry, str):
            hexcol = entry
        if not hexcol:
            # Distinct grey-blue per layer id, so layers stay tellable apart
            # even with no .lyp loaded.
            shade = 90 + (layer * 37) % 140
            hexcol = f"#{shade:02x}{(shade * 3 // 4):02x}{min(255, shade + 40):02x}"
        rings = []
        for p in take:
            pts = [(float(x) * scale, float(y) * scale) for x, y in p.points]
            if len(pts) >= 3:
                rings.append(pts)
        if rings:
            out.append((hexcol, 0.85, rings))
    return out


def local_footprint(block):
    """
    The block's extent in its OWN coordinates, i.e. with any Placement
    taken back off.

    This distinction is the whole reason texturing a second chip failed.
    build_chip_proxy_object bakes the box at the GDS footprint coordinates
    and leaves Placement identity, so moving a chip afterwards changes
    Shape.BoundBox but NOT the geometry Coin traverses — that sits below
    the view provider's own SoTransform and is still at the original
    coordinates. Mapping a texture with world values therefore slides it
    off any chip that has been moved, which is every chip after the first.
    """
    sh = block.Shape.copy()
    sh.Placement = FreeCAD.Placement()
    bb = sh.BoundBox
    return (bb.XMin, bb.YMin, bb.XMax, bb.YMax)


def apply_texture(block, png_path: str, footprint=None):
    """
    Map *png_path* onto the block via Coin. Returns (ok, reason).

    Four things here are deliberate and were all wrong before:

    * Texture coordinates are given EXPLICITLY. There were none at all
      before, which left Coin to derive them from the bounding box — an
      implicit, position-dependent default. A plane mapping plus a texture
      transform pins the image to the footprint rectangle regardless of
      where the chip has been moved to.
    * Wrapping is CLAMP. The image covers the top face; without clamping
      the leftover coordinates on the four sides tile it into a smear.
    * ShapeColor is set BEFORE the texture nodes go in. Assigning a view
      property makes the view provider refresh, which rebuilds its child
      list — doing it afterwards could drop the nodes inserted a moment
      earlier.
    * The insert is VERIFIED. Coin's insertChild does not raise when the
      index is out of range, so a silent no-op was indistinguishable from
      success and reported "textured" for a block that was not.
    """
    if not FreeCAD.GuiUp:
        return False, "no GUI"
    try:
        from pivy import coin
    except Exception as exc:
        return False, f"Coin unavailable: {exc}"
    vo = getattr(block, "ViewObject", None)
    if vo is None:
        return False, "the block has no ViewObject"
    try:
        root = vo.RootNode
    except Exception as exc:
        return False, f"no RootNode: {exc}"
    if root is None:
        return False, "RootNode is None"

    try:
        remove_texture(block)
        # Modulating against white shows the picture's own colours; do this
        # first, so the refresh it triggers happens BEFORE we add our node.
        try:
            vo.ShapeColor = (1.0, 1.0, 1.0)
        except Exception:
            pass

        x0, y0, x1, y1 = footprint or local_footprint(block)
        w = max(float(x1) - float(x0), 1e-9)
        h = max(float(y1) - float(y0), 1e-9)

        tex = coin.SoTexture2()
        tex.filename = png_path
        tex.model = coin.SoTexture2.MODULATE
        tex.wrapS = coin.SoTexture2.CLAMP
        tex.wrapT = coin.SoTexture2.CLAMP

        # s = x/w and t = y/h, so the image spans exactly one footprint...
        plane = coin.SoTextureCoordinatePlane()
        plane.directionS = coin.SbVec3f(1.0 / w, 0.0, 0.0)
        plane.directionT = coin.SbVec3f(0.0, 1.0 / h, 0.0)

        # ...then shift it so the footprint's own corner lands on (0, 0)
        # rather than at x0/w — a layout rarely starts at the origin.
        xf = coin.SoTexture2Transform()
        xf.translation = coin.SbVec2f(-float(x0) / w, -float(y0) / h)

        # Go in as early as possible: Coin applies state in traversal order,
        # so all three must precede the geometry they should affect. Index 1
        # is just past the view provider's own transform, which must keep
        # acting on the geometry below.
        base = 1 if root.getNumChildren() >= 1 else 0
        for offset, node in enumerate((tex, xf, plane)):
            root.insertChild(node, base + offset)

        found = any(isinstance(root.getChild(i), coin.SoTexture2)
                    for i in range(root.getNumChildren()))
        if not found:
            return False, (f"Coin accepted no texture node (root has "
                            f"{root.getNumChildren()} children)")
        return True, (f"{w:.3f}x{h:.3f} mm at ({x0:.3f}, {y0:.3f}), "
                       f"{root.getNumChildren()} nodes")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def has_texture(block) -> bool:
    """
    Whether this block currently carries a texture, read from the scene
    graph itself.

    The state deliberately is NOT bookkept on the object: an attribute like
    block._has_chip_texture cannot be set on a FreeCAD DocumentObject at all
    (the C++ binding rejects it), so the previous toggle silently recorded
    nothing and "run again to remove" never worked. The scene graph is the
    only honest source of truth here.
    """
    if not FreeCAD.GuiUp:
        return False
    try:
        from pivy import coin
        root = block.ViewObject.RootNode
        return any(isinstance(root.getChild(i), coin.SoTexture2)
                   for i in range(root.getNumChildren()))
    except Exception:
        return False


def remove_texture(block) -> bool:
    if not FreeCAD.GuiUp:
        return False
    try:
        from pivy import coin
        root = block.ViewObject.RootNode
        # All three of the nodes apply_texture adds, or re-running would
        # leave orphaned coordinate/transform nodes behind each time.
        junk = (coin.SoTexture2, coin.SoTexture2Transform,
                coin.SoTextureCoordinatePlane)
        for i in range(root.getNumChildren() - 1, -1, -1):
            if isinstance(root.getChild(i), junk):
                root.removeChild(i)
        return True
    except Exception:
        return False


def texture_proxy(block, pixels=chip_texture.DEFAULT_PIXELS):
    """Render and apply. Returns (ok, message)."""
    gds_path = _gds_path_of(block)
    if not gds_path or not os.path.isfile(gds_path):
        return False, ("This proxy does not record the GDS file it came "
                        "from, so its layout cannot be drawn. Re-import it, "
                        "or use the file picker.")
    # The block's own coordinates, NOT world ones — the picture and the
    # mapping that places it have to agree, and the mapping is applied
    # below the view provider's transform. See local_footprint().
    foot = local_footprint(block)

    try:
        layers = _layer_polygons(gds_path, foot)
    except Exception as exc:
        return False, f"Could not read the layout: {exc}"
    if not layers:
        return False, "That layout has no polygons to draw."

    try:
        png, w, h = chip_texture.render_layout_png(layers, foot, pixels,
                                                   power_of_two=True)
    except Exception as exc:
        return False, f"Could not render the layout: {exc}"

    path = os.path.join(str(_texture_dir()), f"{block.Name}.png")
    try:
        chip_texture.write_png(path, png)
    except Exception as exc:
        return False, f"Could not write the image: {exc}"

    ok, reason = apply_texture(block, path, footprint=foot)
    if not ok:
        return False, (f"Image rendered ({w}x{h}) and written to {path}, but "
                        f"could not be applied: {reason}")
    return True, f"Textured {block.Label or block.Name} ({w}x{h} px, {reason})."


class ChipTextureCommand:
    """Toggle a layout picture on the selected chip proxy."""

    def GetResources(self):
        return {
            "MenuText": "Texture Chip Proxy",
            "ToolTip": (
                "Paint the selected chip proxy with a picture of its own\n"
                "layout, so several proxies stay tellable apart.\n\n"
                "Purely visual and optional — the picture is a texture, not\n"
                "geometry, so it costs nothing to display and cannot be\n"
                "selected or routed on. Run again to remove it."
            ),
            "Pixmap": get_icon("Chip_Texture.svg"),
        }

    def Activated(self):
        blocks = _selected_proxy_blocks()
        parent = FreeCADGui.getMainWindow()
        if not blocks:
            QtWidgets.QMessageBox.warning(
                parent, "Select a chip proxy",
                "Select an imported chip proxy (its block or its group) "
                "first.")
            return
        done, failed = [], []
        for block in blocks:
            # Second run on an already-textured proxy takes it off again.
            if has_texture(block):
                remove_texture(block)
                done.append(f"{block.Label or block.Name}: texture removed")
                continue
            ok, msg = texture_proxy(block)
            (done if ok else failed).append(
                msg if ok else f"{block.Label or block.Name}: {msg}")
        for line in done + failed:
            FreeCAD.Console.PrintMessage(f"[ChipTexture] {line}\n")
        if failed:
            QtWidgets.QMessageBox.information(
                parent, "Texture chip proxy", "\n\n".join(failed))

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ChipTextureCommand", ChipTextureCommand())
