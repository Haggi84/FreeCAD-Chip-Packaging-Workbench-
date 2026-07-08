# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
TogglePerformanceModeCommand
============================
Toggles GDS Die layers between Detail mode (full B-rep rendering) and
Fast-Mesh mode (pre-baked triangle meshes — same visual quality, fast viewport).

Detail mode   — OCCT B-rep shapes tessellated per frame by FreeCAD.
Fast-Mesh mode— shapes pre-baked once to Mesh::Feature triangle meshes.
               Same shaded appearance as Detail, but the GPU renders static
               triangles without re-tessellation → smooth pan/zoom/rotate
               even on large dies with hundreds of layers.

Workflow
--------
First toggle  → bakes one Mesh::Feature per GDS layer (shows progress),
                then hides B-rep shapes and shows meshes.
Next toggle   → restores B-rep shapes, hides meshes (instant).
Rebake        → delete the "GDS_PerfMeshes" group and toggle again.
"""

import FreeCAD
import FreeCADGui
from Get_Path import get_icon
from compat import QtWidgets, QtCore

# ── tuneable constants ─────────────────────────────────────────────────────────

_DETAIL_DEVIATION       = 0.5     # B-rep: tessellation deviation (% of bbox)
_DETAIL_ANGULAR         = 28.5    # B-rep: angular deflection (degrees)
_DISPLAY_DETAIL         = "Flat Lines"

# Kept for backward-compat (set_layer_detail fallback when no mesh is baked yet)
_FAST_DEVIATION         = 8.0
_FAST_ANGULAR           = 57.0
_DISPLAY_FAST           = "Wireframe"

# Mesh baking quality — absolute, not relative to bounding box.
# 0.01 mm linear deflection gives sub-pixel accuracy on chip geometry.
_MESH_LINEAR_DEFL       = 0.01    # mm
_MESH_ANGULAR_DEFL      = 28.5    # degrees

_PERF_MESH_SUFFIX       = "_PerfMesh"
_PERF_MESH_GROUP        = "GDS_PerfMeshes"

_FILL_LAYER_HINTS       = ("fill", "filler", "dummy", "block")

# ── module-level state ─────────────────────────────────────────────────────────

_fast_mode: bool   = False
_saved_props: dict = {}   # obj.Name → (deviation, angular, displayMode, visibility)


# ── helpers ────────────────────────────────────────────────────────────────────

def _is_fill_layer(obj) -> bool:
    name  = (obj.Name  or "").lower()
    label = (obj.Label or "").lower()
    return any(h in name or h in label for h in _FILL_LAYER_HINTS)


def _gds_layer_objects(doc):
    """Yield (obj, vobj) for every GDS layer shape in the document."""
    gds_group = next(
        (o for o in doc.Objects if o.Name == "GDS_Die" or o.Label == "GDS_Die"),
        None,
    )
    candidates = getattr(gds_group, "Group", []) if gds_group else [
        o for o in doc.Objects if o.Name.startswith("Layer_")
    ]
    for obj in candidates:
        vobj = getattr(obj, "ViewObject", None)
        if vobj is None or not hasattr(obj, "Shape"):
            continue
        # VIA layers are managed separately by ToggleViaDetailCommand
        # (simple outlined block vs full detail) — skip them here so the two
        # mechanisms never fight over a via layer's visibility.
        nm = (obj.Name or "").lower()
        lb = (obj.Label or "").lower()
        if "via" in nm or "via" in lb:
            continue
        yield obj, vobj


def _make_progress(title: str, n: int) -> QtWidgets.QProgressDialog:
    dlg = QtWidgets.QProgressDialog(
        title, None, 0, max(n, 1),
        FreeCADGui.getMainWindow(),
    )
    dlg.setWindowTitle("Rendering")
    dlg.setWindowModality(QtCore.Qt.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setMinimumWidth(420)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    return dlg


def _perf_mesh_group(doc):
    """Return (creating if absent) the group that holds all baked mesh objects."""
    for obj in doc.Objects:
        if obj.Name == _PERF_MESH_GROUP:
            return obj
    grp       = doc.addObject("App::DocumentObjectGroup", _PERF_MESH_GROUP)
    grp.Label = "GDS Performance Meshes"
    return grp


def _bake_layer_mesh(doc, obj, grp):
    """
    Tessellate obj.Shape into a Mesh::Feature and add it to grp.
    Returns the mesh object, or None if baking fails.
    Reuses an already-baked mesh when one exists in the document.
    """
    mesh_name = obj.Name + _PERF_MESH_SUFFIX
    existing  = doc.getObject(mesh_name)
    if existing is not None:
        return existing

    try:
        import MeshPart
        mesh = MeshPart.meshFromShape(
            Shape             = obj.Shape,
            LinearDeflection  = _MESH_LINEAR_DEFL,
            AngularDeflection = _MESH_ANGULAR_DEFL,
            Relative          = False,
        )

        mesh_obj       = doc.addObject("Mesh::Feature", mesh_name)
        mesh_obj.Mesh  = mesh
        mesh_obj.Label = (obj.Label or obj.Name) + " [fast]"

        # Mirror the layer's colour and transparency so it looks identical
        vobj      = obj.ViewObject
        mesh_vobj = mesh_obj.ViewObject
        for prop in ("ShapeColor", "LineColor", "Transparency"):
            try:
                setattr(mesh_vobj, prop, getattr(vobj, prop))
            except Exception:
                pass
        mesh_vobj.DisplayMode = "Shaded"   # filled faces, no triangle-edge clutter
        mesh_vobj.Visibility  = False       # hidden until fast mode is active

        grp.addObject(mesh_obj)
        return mesh_obj

    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[PerfMode] Mesh bake failed for '{obj.Name}': {exc}\n"
        )
        return None


# ── mode transitions ───────────────────────────────────────────────────────────

def _enter_mesh_mode(doc):
    """Bake meshes (once) then hide B-rep layers and show meshes."""
    global _saved_props
    _saved_props = {}

    pairs = list(_gds_layer_objects(doc))
    n     = len(pairs)
    if n == 0:
        return

    grp = _perf_mesh_group(doc)
    dlg = _make_progress(f"Building fast view…  (0 / {n})", n)

    shown = 0
    for i, (obj, vobj) in enumerate(pairs):
        label = obj.Label or obj.Name
        dlg.setLabelText(f"Building fast view — layer {i + 1} / {n}\n{label}")
        dlg.setValue(i)
        QtWidgets.QApplication.processEvents()

        # Persist current B-rep display settings so we can restore them later
        _saved_props[obj.Name] = (
            getattr(vobj, "Deviation",         _DETAIL_DEVIATION),
            getattr(vobj, "AngularDeflection", _DETAIL_ANGULAR),
            getattr(vobj, "DisplayMode",       _DISPLAY_DETAIL),
            getattr(vobj, "Visibility",        True),
        )

        # Hide fill/dummy layers entirely in fast mode
        if _is_fill_layer(obj):
            vobj.Visibility = False
            continue

        mesh_obj = _bake_layer_mesh(doc, obj, grp)
        if mesh_obj is not None:
            vobj.Visibility                = False   # hide B-rep
            mesh_obj.ViewObject.Visibility = True    # show mesh
            shown += 1
        # If baking failed, leave the B-rep visible for that layer

    dlg.setValue(n)
    dlg.setLabelText(f"Fast view ready — {shown} / {n} layer(s) using meshes")
    QtWidgets.QApplication.processEvents()
    dlg.close()
    FreeCAD.Console.PrintMessage(
        f"[PerfMode] Fast-Mesh mode ON — {shown} / {n} layer(s).\n"
    )


def _enter_detail_mode(doc):
    """Restore B-rep shapes to their saved settings; hide all mesh companions."""
    pairs = list(_gds_layer_objects(doc))
    n     = len(pairs)
    if n == 0:
        return

    dlg = _make_progress(f"Switching to Detail mode…  (0 / {n})", n)

    restored = 0
    for i, (obj, vobj) in enumerate(pairs):
        label = obj.Label or obj.Name
        dlg.setLabelText(f"Detail mode — layer {i + 1} / {n}\n{label}")
        dlg.setValue(i)
        QtWidgets.QApplication.processEvents()

        # Hide the mesh companion (if baked)
        mesh_obj = doc.getObject(obj.Name + _PERF_MESH_SUFFIX)
        if mesh_obj is not None:
            mesh_obj.ViewObject.Visibility = False

        # Restore B-rep display settings
        saved = _saved_props.get(
            obj.Name,
            (_DETAIL_DEVIATION, _DETAIL_ANGULAR, _DISPLAY_DETAIL, True),
        )
        dev, ang, disp, vis = saved
        try:
            vobj.Deviation         = dev
            vobj.AngularDeflection = ang
            vobj.DisplayMode       = disp
            vobj.Visibility        = vis
            restored += 1
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[PerfMode] Restore '{obj.Name}': {exc}\n"
            )

    dlg.setValue(n)
    dlg.setLabelText(f"Detail mode — done ({restored} layer(s))")
    QtWidgets.QApplication.processEvents()
    dlg.close()
    FreeCAD.Console.PrintMessage(
        f"[PerfMode] Detail mode ON — {restored} layer(s) restored.\n"
    )


# ── public API (called from GDSCommand / ShowDetailLayerPanelCommand) ──────────

def apply_performance_mode(doc):
    global _fast_mode
    _enter_mesh_mode(doc)
    _fast_mode = True


def apply_detail_mode(doc):
    global _fast_mode
    _enter_detail_mode(doc)
    _fast_mode = False


def set_layer_detail(obj, detail: bool):
    """
    Toggle a single layer between Detail and fast display.
    Called by the layer panel for per-layer control.
    Uses the pre-baked mesh when available; falls back to wireframe otherwise.
    """
    vobj = getattr(obj, "ViewObject", None)
    if vobj is None:
        return
    doc = FreeCAD.activeDocument()
    try:
        if detail:
            vobj.Deviation         = _DETAIL_DEVIATION
            vobj.AngularDeflection = _DETAIL_ANGULAR
            vobj.DisplayMode       = _DISPLAY_DETAIL
            vobj.Visibility        = True
            if doc:
                mesh_obj = doc.getObject(obj.Name + _PERF_MESH_SUFFIX)
                if mesh_obj is not None:
                    mesh_obj.ViewObject.Visibility = False
        else:
            # Use pre-baked mesh if it exists
            if doc:
                mesh_obj = doc.getObject(obj.Name + _PERF_MESH_SUFFIX)
                if mesh_obj is not None:
                    vobj.Visibility                = False
                    mesh_obj.ViewObject.Visibility = True
                    return
            # No mesh yet — fall back to wireframe for this layer
            vobj.Deviation         = _FAST_DEVIATION
            vobj.AngularDeflection = _FAST_ANGULAR
            vobj.DisplayMode       = _DISPLAY_FAST
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[PerfMode] set_layer_detail '{obj.Name}': {exc}\n"
        )


# ── FreeCAD command ────────────────────────────────────────────────────────────

class TogglePerformanceModeCommand:

    def GetResources(self):
        mode = "Detail" if _fast_mode else "Fast Mesh"
        return {
            "MenuText": f"Toggle Render Mode  [{mode}]",
            "ToolTip": (
                "Switch GDS layers between Detail (B-rep) and Fast-Mesh mode.\n"
                "\n"
                "Fast Mesh → pre-baked triangle meshes at full visual quality.\n"
                "            Smooth pan / zoom / rotate on large dies.\n"
                "            Meshes are baked once on first use and cached.\n"
                "Detail    → full B-rep geometry for wire bonding & measurements.\n"
                "\n"
                "To force a rebake: delete 'GDS_PerfMeshes' group and toggle again.\n"
                f"Current mode: {mode}"
            ),
            "Pixmap": get_icon("Performance_Mode.svg"),
        }

    def IsActive(self):
        doc = FreeCAD.activeDocument()
        if doc is None:
            return False
        return any(
            o.Name.startswith("Layer_") or o.Name == "GDS_Die"
            for o in doc.Objects
        )

    def Activated(self):
        global _fast_mode

        doc = FreeCAD.activeDocument()
        if doc is None:
            return

        _fast_mode = not _fast_mode

        if _fast_mode:
            _enter_mesh_mode(doc)
        else:
            _enter_detail_mode(doc)

        FreeCADGui.updateGui()
        mode_str = "Fast Mesh" if _fast_mode else "Detail"
        FreeCAD.Console.PrintMessage(f"[PerfMode] Now in {mode_str} mode.\n")


FreeCADGui.addCommand("TogglePerformanceModeCommand", TogglePerformanceModeCommand())
