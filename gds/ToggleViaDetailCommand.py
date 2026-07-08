# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
ToggleViaDetailCommand
======================
VIA layers in a GDS die are dense arrays of many tiny cuts — expensive to
render.  By default each VIA layer is replaced by a simplified block
representation so the viewport stays fast; a toolbar button loads the full
VIA geometry on demand.

The simplified block is not one box for the whole layer (that would bridge
every gap and visually fuse separate via arrays/pads together) — it is one
box per proximity CLUSTER of via solids (see core.via_clustering), so each
tightly packed via array reads as one filled block while physically separate
arrays/pads stay visually separate.

State per VIA layer:
  Simplified (default) — show <Layer>_ViaBlock, hide the detailed B-rep.
  Detail               — hide the block, show the detailed B-rep.

VIA layers are detected by name/label containing "via".  They are managed
here independently of the fast-mesh render toggle, which skips them.
"""

import FreeCAD
import FreeCADGui
import Part
from Get_Path import get_icon
from core.via_clustering import cluster_boxes, DEFAULT_CLUSTER_GAP_MM

_VIA_BLOCK_SUFFIX = "_ViaBlock"
_VIA_BLOCK_GROUP  = "GDS_ViaBlocks"
_PERF_MESH_SUFFIX = "_PerfMesh"

# Module state: False = simplified blocks (default), True = full detail.
_via_detailed = False


# ── detection ──────────────────────────────────────────────────────────────────

def _is_via_layer(obj) -> bool:
    name  = (getattr(obj, "Name",  "") or "").lower()
    label = (getattr(obj, "Label", "") or "").lower()
    if not name.startswith("layer_"):
        return False
    # Never treat our own generated proxies as via source layers.
    if name.endswith(_VIA_BLOCK_SUFFIX.lower()) or name.endswith(_PERF_MESH_SUFFIX.lower()):
        return False
    return "via" in name or "via" in label


def _via_layer_objects(doc):
    """Yield (obj, vobj) for every detailed GDS via-layer B-rep."""
    gds_group = next(
        (o for o in doc.Objects if o.Name == "GDS_Die" or o.Label == "GDS_Die"),
        None,
    )
    candidates = getattr(gds_group, "Group", []) if gds_group else [
        o for o in doc.Objects if o.Name.startswith("Layer_")
    ]
    for obj in candidates:
        if not _is_via_layer(obj):
            continue
        vobj = getattr(obj, "ViewObject", None)
        if vobj is not None and hasattr(obj, "Shape"):
            yield obj, vobj


# ── block construction ─────────────────────────────────────────────────────────

def _via_block_group(doc):
    for obj in doc.Objects:
        if obj.Name == _VIA_BLOCK_GROUP:
            return obj
    grp       = doc.addObject("App::DocumentObjectGroup", _VIA_BLOCK_GROUP)
    grp.Label = "GDS Via Blocks"
    return grp


def _build_via_block(doc, obj, grp):
    """
    Create (once) a simplified block for via layer *obj* — one bounding box
    per proximity CLUSTER of via/pad solids (see core.via_clustering), so a
    tightly packed via array collapses into one filled block while physically
    separate arrays/pads stay separate blocks.
    Returns the block object, or None on failure.  Reuses an existing block.
    """
    block_name = obj.Name + _VIA_BLOCK_SUFFIX
    existing   = doc.getObject(block_name)
    if existing is not None:
        return existing
    try:
        block_shape = cluster_boxes(obj.Shape, DEFAULT_CLUSTER_GAP_MM)
        if block_shape is None or block_shape.isNull():
            return None
        block       = doc.addObject("Part::Feature", block_name)
        block.Shape = block_shape
        block.Label = (obj.Label or obj.Name) + " [block]"

        # Mirror the layer's colour so the block reads as the same layer.
        vobj  = obj.ViewObject
        bvobj = block.ViewObject
        for prop in ("ShapeColor", "LineColor", "Transparency"):
            try:
                setattr(bvobj, prop, getattr(vobj, prop))
            except Exception:
                pass
        try:
            bvobj.DisplayMode = "Flat Lines"   # shaded faces + outline edges
        except Exception:
            pass
        bvobj.Visibility = False
        grp.addObject(block)
        return block
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[ViaBlock] build failed for '{obj.Name}': {exc}\n"
        )
        return None


# ── public API ─────────────────────────────────────────────────────────────────

def apply_via_simplified(doc):
    """Show a simple block for every via layer; hide the detailed via geometry."""
    global _via_detailed
    if doc is None:
        return
    grp = _via_block_group(doc)
    n = 0
    for obj, vobj in _via_layer_objects(doc):
        block = _build_via_block(doc, obj, grp)
        try:
            vobj.Visibility = False
            mesh = doc.getObject(obj.Name + _PERF_MESH_SUFFIX)
            if mesh is not None:
                mesh.ViewObject.Visibility = False
            if block is not None:
                block.ViewObject.Visibility = True
                n += 1
        except Exception:
            pass
    _via_detailed = False
    FreeCAD.Console.PrintMessage(
        f"[ViaBlock] {n} via layer(s) simplified to blocks.\n"
    )


def apply_via_detail(doc):
    """Hide the via blocks; show the full detailed via geometry."""
    global _via_detailed
    if doc is None:
        return
    n = 0
    for obj, vobj in _via_layer_objects(doc):
        block = doc.getObject(obj.Name + _VIA_BLOCK_SUFFIX)
        if block is not None:
            try:
                block.ViewObject.Visibility = False
            except Exception:
                pass
        try:
            vobj.Visibility = True
            n += 1
        except Exception:
            pass
    _via_detailed = True
    FreeCAD.Console.PrintMessage(
        f"[ViaBlock] {n} via layer(s) shown in full detail.\n"
    )


# ── FreeCAD command ────────────────────────────────────────────────────────────

class ToggleViaDetailCommand:

    def GetResources(self):
        mode = "Detail" if _via_detailed else "Blocks"
        return {
            "MenuText": f"Toggle VIA Detail  [{mode}]",
            "ToolTip": (
                "Switch VIA layers between simple outlined blocks and full detail.\n"
                "\n"
                "Blocks → each VIA layer shown as one outlined bounding block\n"
                "         (fast; the default).\n"
                "Detail → full VIA cut geometry (slower).\n"
                "\n"
                "To force a rebuild: delete the 'GDS_ViaBlocks' group and toggle.\n"
                f"Current: {mode}"
            ),
            "Pixmap": get_icon("Via_Detail.svg"),
        }

    def IsActive(self):
        doc = FreeCAD.activeDocument()
        if doc is None:
            return False
        return any(_is_via_layer(o) for o in doc.Objects)

    def Activated(self):
        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        if _via_detailed:
            apply_via_simplified(doc)
        else:
            apply_via_detail(doc)
        FreeCADGui.updateGui()


FreeCADGui.addCommand("ToggleViaDetailCommand", ToggleViaDetailCommand())
