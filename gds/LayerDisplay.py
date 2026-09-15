# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
How an already-loaded GDS layer is displayed.

What remains of gds/TogglePerformanceModeCommand.py, which was removed. That
module's headline feature — baking each layer into a "<Name>_PerfMesh"
triangulation and showing that instead of the real solid — is gone: it made
the viewport fast by displaying an approximation of the geometry, and this
workbench has settled on showing the geometry it actually built.

Two of its responsibilities were never about meshes, and both are kept here:

  * per-layer display QUALITY — tessellation deviation, angular deflection
    and display mode. This is how FreeCAD draws a B-rep; the solid is
    untouched either way. ui/DetailLayerPanel.py drives it per layer.
  * dispatching a NEWLY-LOADED layer to the right handler. The LOD manager
    promotes layers long after import, and a via layer promoted while the
    rest of the document shows via blocks has to be handed to
    gds.ToggleViaDetailCommand rather than left in full detail on its own.

Nothing here creates, caches or hides a companion object.
"""

import FreeCAD

# Tessellation quality for a layer shown in full detail. These control how
# finely OCCT triangulates the B-rep for DISPLAY only — the solid itself is
# never modified, so switching back and forth is lossless.
_DETAIL_DEVIATION = 0.5      # percent of bounding-box size
_DETAIL_ANGULAR   = 28.5     # degrees
_DISPLAY_DETAIL   = "Flat Lines"     # shaded faces plus outline edges

# The cheap alternative for a single heavy layer: coarse triangulation and
# wireframe. Still the real geometry, just drawn with less of it.
_FAST_DEVIATION = 8.0
_FAST_ANGULAR   = 57.0
_DISPLAY_FAST   = "Wireframe"


def set_layer_detail(obj, detail: bool) -> None:
    """
    Switch one layer between full-quality and coarse DISPLAY.

    detail=False no longer swaps in a pre-baked mesh — there are none. It
    lowers the tessellation quality and draws the layer as wireframe, which
    is the same fallback the old code used whenever a mesh had not been
    baked yet.
    """
    vobj = getattr(obj, "ViewObject", None)
    if vobj is None:
        return
    try:
        if detail:
            vobj.Deviation         = _DETAIL_DEVIATION
            vobj.AngularDeflection = _DETAIL_ANGULAR
            vobj.DisplayMode       = _DISPLAY_DETAIL
            vobj.Visibility        = True
        else:
            vobj.Deviation         = _FAST_DEVIATION
            vobj.AngularDeflection = _FAST_ANGULAR
            vobj.DisplayMode       = _DISPLAY_FAST
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[LayerDisplay] set_layer_detail '{obj.Name}': {exc}\n")


def sync_new_layer_display(doc, obj) -> None:
    """
    Apply the document's current display convention to a layer that has just
    finished loading — e.g. the LOD manager promoting a lazily-loaded layer
    from a placeholder to real geometry.

    Via layers are handed to gds.ToggleViaDetailCommand, which owns whether
    vias show as clustered blocks or real arrays. Without that, a via layer
    promoted while every other via in the document is a block would appear in
    full detail on its own, inconsistently.

    Everything else is simply shown in full detail: with fast-mesh gone there
    is no global "render everything coarsely" state left to inherit.
    """
    name  = (getattr(obj, "Name", "") or "").lower()
    label = (getattr(obj, "Label", "") or "").lower()
    try:
        from core.lod_import import _name_says_via
        is_via = _name_says_via(name) or _name_says_via(label)
    except Exception:
        is_via = "via" in name or "via" in label

    if is_via:
        try:
            from gds.ToggleViaDetailCommand import sync_new_via_layer
            sync_new_via_layer(doc, obj)
            return
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[LayerDisplay] via dispatch for '{obj.Name}': {exc}\n")
    set_layer_detail(obj, True)
