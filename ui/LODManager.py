# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
LODManager.py
=============
Manages the LOD state of all GDS layers in a document.

Three states per layer:
  SOLID    ▣  Not yet loaded. The IC_Body_Solid bridges the stack visually.
  LOADING  ⟳  Tessellation is running in the background thread.
  DETAIL   ◉  Full geometry visible in the document.

Important: The manager knows ALL layers available in the LYP+GDS (all_layers),
not only those that were selected during import. This allows it to load any layer
after the fact — including those that were not checked in the import dialog.

Contact layers (PIN, COMP) and PIN-Flat layers start directly as DETAIL,
since they are loaded immediately during import. All routing layers start as SOLID.
Fill layers remain permanently SOLID (BBox, never fully tessellate).

Thread safety: load_gds() runs in a QThread. The OCCT objects
are passed as BREP strings and written into the document in the main thread
via Qt signal.

Relationship to the other GDS display mechanisms
------------------------------------------------------
This is one of three independent, cooperating mechanisms that control how a
layer looks in the viewport. Each answers a different question:

  1. LODManager (here)                    — does the layer's real geometry
                                             exist in the document AT ALL?
                                             (lazy/progressive import loading)
  2. gds.ToggleViaDetailCommand           — for VIA layers specifically, is
                                             the real via array shown, or a
                                             proximity-clustered simplified
                                             block? (intentionally LESS detail)
  3. ui.DetailLayerPanel._simplify_layer  — manual, per-row, destructive
                                             swap of an already-loaded
                                             layer's Shape for its bounding
                                             box (independent of #2)

There was a fourth — gds.TogglePerformanceModeCommand, which swapped each
layer for a pre-baked triangulated mesh. It was removed: it made the viewport
fast by displaying an approximation of the geometry rather than the geometry.
What survived is gds.LayerDisplay, which only sets per-layer tessellation
quality and display mode, both of which leave the solid untouched.

When a layer is promoted here (_insert_layer / _show_layer), it hands off to
gds.LayerDisplay.sync_new_layer_display() — that routes a via layer to
mechanism 2 so it matches the rest of the document instead of appearing in
full detail on its own.
"""

from __future__ import annotations

import enum
import os

import FreeCAD
import FreeCADGui
import Part
from compat import QtCore, QtWidgets

from core import Core_Functionality
from core.lod_import import get_lazy_load_params
from gds.LayerDisplay import sync_new_layer_display

# Global registry: doc.Name → LODManager
# Necessary because FreeCAD App.Document (C++) does not allow Python attributes
_LOD_REGISTRY: dict = {}


def get_lod_manager(doc):
    """Returns the LODManager for a document, or None."""
    if doc is None:
        return None
    return _LOD_REGISTRY.get(doc.Name)


def register_lod_manager(doc, manager):
    """Registers a LODManager for a document."""
    if doc is not None:
        _LOD_REGISTRY[doc.Name] = manager


# ── State model ───────────────────────────────────────────────────────────────

class LODState(enum.Enum):
    SOLID    = "solid"     # placeholder box visible
    LOADING  = "loading"   # background thread active
    PREVIEW  = "preview"   # 2D polygons from GDS (fast, no Z)
    DETAIL   = "detail"    # fully 3D extruded


# ── Background worker ─────────────────────────────────────────────────────────

class _LayerLoadWorker(QtCore.QThread):
    """Loads a single GDS layer in the background thread."""

    finished = QtCore.Signal(object, object)   # (layer_key, shapes | None)

    def __init__(self, gds_path: str, layer_dict: dict, load_kwargs: dict):
        super().__init__()
        self._gds_path   = gds_path
        self._layer_dict = layer_dict
        self._kwargs     = load_kwargs

    def run(self):
        key = (self._layer_dict.get("layer_id", 0),
               self._layer_dict.get("datatype",  0))
        try:
            shapes = Core_Functionality.load_gds(
                self._gds_path, [self._layer_dict], **self._kwargs
            )
            self.finished.emit(key, shapes)
        except Exception as e:
            FreeCAD.Console.PrintWarning(
                f"[LOD] Worker error {self._layer_dict.get('name')}: {e}\n"
            )
            self.finished.emit(key, None)


# ── Main manager ──────────────────────────────────────────────────────────────

class LODManager(QtCore.QObject):
    """
    Coordinates the step-by-step loading of GDS layers.

    Public API
    ----------
    promote(layer_key)            — SOLID → DETAIL (starts loading if necessary)
    promote_all()                 — load all SOLID layers
    demote(layer_key)             — DETAIL → SOLID (hidden, geometry is retained)
    state(layer_key) → LODState   — query current state
    all_layer_dicts() → list      — all known layer dicts (for panel construction)
    """

    state_changed = QtCore.Signal(tuple, str)   # (layer_key, LODState.value)
    body_hidden   = QtCore.Signal()

    def __init__(self, doc, gds_path: str, aux: dict):
        """
        doc       — FreeCAD document
        gds_path  — path to the GDS file
        aux       — aux dict from lod_import.build_lod_import_params()
                    must contain 'all_layers', 'categories', 'contact_keys',
                    'fill_layer_keys'
        """
        super().__init__()

        self._doc     = doc
        self._gds_path = gds_path
        self._aux     = aux

        self._workers: dict   = {}   # key → _LayerLoadWorker
        self._states: dict    = {}   # key → LODState
        self._layer_map: dict = {}   # key → layer_dict (LYP-Dict)
        self._obj_map: dict   = {}   # key → FreeCAD-Objekt (nach Laden)

        all_layers   = aux.get("all_layers",       [])
        categories   = aux.get("categories",       {})
        fill_keys    = aux.get("fill_layer_keys",  set())

        for l in all_layers:
            key = (l.get("layer_id", 0), l.get("datatype", 0))
            self._layer_map[key] = l
            cat = categories.get(key, "routing")

            if cat in ("contact", "pin_flat"):
                # Loaded immediately during import
                self._states[key] = LODState.DETAIL
            elif cat == "fill":
                # Always remains BBox — loading not possible
                self._states[key] = LODState.DETAIL   # "done" from LOD perspective
            else:
                # Routing layer: lazy
                self._states[key] = LODState.SOLID

        # Register FreeCAD objects already created during import
        self._scan_existing_objects()

        # Read XY extent of the chip from IC_Body_Solid (once)
        self._chip_xy: tuple = self._read_chip_xy()

        # Create per-layer placeholders for all SOLID layers
        self._create_layer_placeholders()

        n_solid = sum(1 for s in self._states.values() if s == LODState.SOLID)
        FreeCAD.Console.PrintMessage(
            f"[LOD] Manager: {len(all_layers)} layers known, "
            f"{n_solid} lazily loadable\n"
        )

    # ── Public methods ───────────────────────────────────────────────────────

    def state(self, key: tuple) -> LODState:
        return self._states.get(key, LODState.SOLID)

    def is_all_loaded(self) -> bool:
        return all(s != LODState.SOLID for s in self._states.values())

    def all_layer_dicts(self) -> list:
        """All known layer dicts, sorted by stack_rank (top → bottom)."""
        from core.Core_Functionality import stack_rank_for_edi
        ihp_map = self._aux.get("ihp_map", {})

        def rank(key):
            m = ihp_map.get(key)
            return stack_rank_for_edi(m["edi_name"] if m else "")

        return sorted(self._layer_map.values(),
                      key=lambda l: -rank((l.get("layer_id", 0),
                                           l.get("datatype",  0))))

    def promote(self, key: tuple, preview_only: bool = False):
        """
        Promotes a layer.

        preview_only=False (default): SOLID → DETAIL (full 3D geometry)
        preview_only=True:            SOLID/PREVIEW → PREVIEW (fast 2D polygons)

        Idempotent: already in target state → only set visibility.
        """
        st = self._states.get(key)
        if st is None:
            FreeCAD.Console.PrintWarning(f"[LOD] promote: unknown key {key}\n")
            return
        if st == LODState.LOADING:
            return

        # Determine target state
        target = LODState.PREVIEW if preview_only else LODState.DETAIL

        # Already at target state or higher
        if st == LODState.DETAIL and not preview_only:
            self._show_layer(key)
            return
        if st == LODState.PREVIEW and preview_only:
            self._show_layer(key)
            return
        if st == LODState.PREVIEW and not preview_only:
            # Upgrade from PREVIEW → DETAIL
            pass

        layer_dict = self._layer_map.get(key)
        if not layer_dict:
            return

        self._states[key] = LODState.LOADING
        self.state_changed.emit(key, LODState.LOADING.value)

        kwargs = get_lazy_load_params(
            layer_dict, self._gds_path, self._aux,
            preview_2d=preview_only
        )
        w = _LayerLoadWorker(self._gds_path, layer_dict, kwargs)
        w.finished.connect(lambda k, s, t=target: self._on_worker_done(k, s, t))
        self._workers[key] = w
        w.start()

        mode = "Preview 2D" if preview_only else "Volume 3D"
        name = layer_dict.get("name", str(key))
        FreeCAD.Console.PrintMessage(f"[LOD] Starting {mode}: {name} {key}\n")

    def promote_all(self, preview_only: bool = False):
        """Starts loading all layers that have not yet been fully loaded."""
        for key, st in list(self._states.items()):
            if st == LODState.SOLID:
                self.promote(key, preview_only=preview_only)
            elif st == LODState.PREVIEW and not preview_only:
                self.promote(key, preview_only=False)

    def demote(self, key: tuple):
        """Hides a layer (geometry is retained and can be made visible again immediately)."""
        obj = self._obj_map.get(key)
        if obj:
            try:
                obj.ViewObject.Visibility = False
            except Exception:
                pass
        self._states[key] = LODState.SOLID
        self.state_changed.emit(key, LODState.SOLID.value)
        self._update_body_solid()

    def pending_keys(self) -> list:
        return [k for k, s in self._states.items() if s == LODState.SOLID]

    def loading_keys(self) -> list:
        return [k for k, s in self._states.items() if s == LODState.LOADING]

    def detail_keys(self) -> list:
        return [k for k, s in self._states.items() if s == LODState.DETAIL]

    # ── Internal ─────────────────────────────────────────────────────────────

    # ── Placeholder management ───────────────────────────────────────────────

    def _read_chip_xy(self) -> tuple:
        """
        Reads XY extent from aux["chip_xy"] (set by GDSCommand during import).
        Fallback: derive from existing layer shapes in the document.
        """
        # Preferred: directly from the aux dict (body_solid XY without showing the object)
        if "chip_xy" in self._aux:
            return self._aux["chip_xy"]

        # Fallback: derive from existing FreeCAD layer shapes
        doc = self._doc
        if doc is None:
            return (0.0, 0.0, 1.0, 1.0)
        xmin, ymin, xmax, ymax = None, None, None, None
        for obj in doc.Objects:
            if getattr(obj, "GDSLayerID", None) is None:
                continue
            try:
                bb = obj.Shape.BoundBox
                xmin = bb.XMin if xmin is None else min(xmin, bb.XMin)
                ymin = bb.YMin if ymin is None else min(ymin, bb.YMin)
                xmax = bb.XMax if xmax is None else max(xmax, bb.XMax)
                ymax = bb.YMax if ymax is None else max(ymax, bb.YMax)
            except Exception:
                continue
        if xmin is not None:
            return (xmin, ymin, xmax, ymax)
        return (0.0, 0.0, 1.0, 1.0)

    def _create_layer_placeholders(self):
        """
        Creates a simplified box for each SOLID routing layer with the correct
        Z position from stack_mm and the XY extent of the chip.

        These placeholders are marked with IsLayerPlaceholder=True and are
        replaced by GDS geometry when actually loaded.
        The global IC_Body_Solid is then hidden (placeholders take over
        its visual role, but layer by layer).
        """
        doc = self._doc
        if doc is None:
            return

        stack_mm = self._aux.get("stack_mm") or {}
        xmin, ymin, xmax, ymax = self._chip_xy
        w = xmax - xmin
        h = ymax - ymin
        if w <= 0 or h <= 0:
            return

        import Part
        from core.Color import hex_to_rgb

        grp = next(
            (o for o in doc.Objects if o.Name == "GDS_Die" or o.Label == "GDS_Die"),
            None
        )

        n_created = 0
        try:
            doc.openTransaction("LOD: Layer placeholders")
        except Exception:
            pass

        for key, state in self._states.items():
            if state != LODState.SOLID:
                continue
            if key in self._obj_map:
                continue   # bereits vorhanden

            layer_dict = self._layer_map.get(key)
            if not layer_dict:
                continue

            # Z position from stack_mm
            sm = stack_mm.get(key)
            if sm:
                z0   = float(sm["z0_mm"])
                t_mm = float(sm["t_mm"])
            else:
                z0   = 0.0
                t_mm = 0.001   # 1 µm minimum thickness

            t_mm = max(t_mm, 1e-4)

            name     = layer_dict.get("name", f"Layer_{key[0]}")
            obj_name = f"Layer_{name}_{key[0]}"

            try:
                box = Part.makeBox(w, h, t_mm, FreeCAD.Vector(xmin, ymin, z0))
                obj = doc.addObject("Part::Feature", obj_name)
                obj.Shape = box

                # SAY SO IN THE LABEL. A placeholder is a plain die-sized
                # box carrying the layer's own name and colour, which is
                # indistinguishable in the tree from that layer loaded and
                # collapsed to a bounding box — a real and repeated source of
                # "why is this layer just a rectangle?". The label is the
                # only place the difference is visible without clicking
                # through to the IsLayerPlaceholder property.
                obj.Label = f"{obj_name}  [not loaded]"

                # Colour from LYP (dimmed to be recognisable as a placeholder)
                fc = layer_dict.get("fill-color", "#888888")
                try:
                    r, g, b = hex_to_rgb(fc)
                except Exception:
                    r, g, b = 0.5, 0.5, 0.5
                obj.ViewObject.ShapeColor   = (r, g, b)
                obj.ViewObject.Transparency = 80   # ghosted — not real geometry
                obj.ViewObject.LineColor    = (0.3, 0.3, 0.3)

                # Metadata
                for prop, val in (("GDSLayerID", key[0]), ("GDSDatatype", key[1])):
                    try:
                        obj.addProperty("App::PropertyInteger", prop, "LOD", prop)
                        setattr(obj, prop, val)
                    except Exception:
                        pass
                try:
                    obj.addProperty("App::PropertyBool", "IsLayerPlaceholder", "LOD",
                                    "Simplified placeholder — replaced by GDS geometry")
                    obj.IsLayerPlaceholder = True
                except Exception:
                    pass

                if grp:
                    try:
                        grp.addObject(obj)
                    except Exception:
                        pass

                self._obj_map[key] = obj
                n_created += 1

            except Exception as e:
                FreeCAD.Console.PrintWarning(
                    f"[LOD] Placeholder for {name} failed: {e}\n"
                )

        try:
            doc.commitTransaction()
        except Exception:
            pass

        if n_created:
            doc.recompute()
            FreeCADGui.updateGui()
            FreeCAD.Console.PrintMessage(
                f"[LOD] {n_created} layer placeholders created.\n"
            )

        # Hide IC_Body_Solid — placeholders now take over the visual role
        body = next(
            (o for o in doc.Objects
             if getattr(o, "IsBodySolid", False) or o.Name == "IC_Body_Solid"),
            None
        )
        if body:
            try:
                body.ViewObject.Visibility = False
            except Exception:
                pass

    def _scan_existing_objects(self):
        """
        Registers FreeCAD objects that were already created during import,
        and reconciles self._states against their actual restored
        condition. Without this, a layer that was promoted to DETAIL in a
        prior session (e.g. after reopening a saved document) would be
        misreported as still-SOLID, since self._states was only ever seeded
        from `categories` before this scan runs — and a later promote_all()
        would needlessly re-tessellate it with today's code, which is
        exactly the "replay produces different geometry than what was
        saved" bug this workbench-state redesign exists to eliminate.

        Safe for the normal (non-restore) construction path too: contact /
        pin_flat / fill categories are already seeded as DETAIL in
        __init__() before this scan ever runs, so the `== SOLID` guard
        below is a no-op for them — it only fires for routing-category
        keys that were promoted in a previous session.
        """
        doc = self._doc
        if doc is None:
            return
        for obj in doc.Objects:
            lid = getattr(obj, "GDSLayerID",  None)
            dt  = getattr(obj, "GDSDatatype", None)
            if lid is not None and dt is not None:
                key = (lid, dt)
                self._obj_map[key] = obj
                if (self._states.get(key) == LODState.SOLID
                        and not getattr(obj, "IsLayerPlaceholder", False)):
                    self._states[key] = LODState.DETAIL

    def _on_worker_done(self, key: tuple, shapes, target: LODState = None):
        if target is None:
            target = LODState.DETAIL
        w = self._workers.pop(key, None)
        if w:
            w.deleteLater()

        if not shapes:
            FreeCAD.Console.PrintWarning(
                f"[LOD] No shapes for {key} — remains SOLID\n"
            )
            self._states[key] = LODState.SOLID
            self.state_changed.emit(key, LODState.SOLID.value)
            return

        self._insert_layer(key, shapes, target)

    def _insert_layer(self, key: tuple, shapes: list,
                      target: LODState = None):
        """Creates or updates the FreeCAD feature. Runs in the main thread."""
        if target is None:
            target = LODState.DETAIL
        doc = self._doc
        if doc is None:
            return

        # Find the matching shape entry
        shp_entry = next(
            (s for s in shapes
             if s.get("layer_id") == key[0] and s.get("datatype") == key[1]),
            None,
        )
        if not shp_entry:
            self._states[key] = LODState.SOLID
            self.state_changed.emit(key, LODState.SOLID.value)
            return

        layer_dict = self._layer_map[key]
        name       = layer_dict.get("name", f"Layer_{key[0]}")
        obj_name   = f"Layer_{name}_{key[0]}"

        # Find existing object or create a new one
        existing = self._obj_map.get(key) or next(
            (o for o in doc.Objects if o.Name == obj_name), None
        )

        try:
            doc.openTransaction(f"LOD: {name}")
        except Exception:
            pass

        if shp_entry.get("is_mesh"):
            import Mesh as _Mesh
            if existing is None:
                existing = doc.addObject("Mesh::Feature", obj_name)
            existing.Mesh = shp_entry["mesh"]
        else:
            if existing is None:
                existing = doc.addObject("Part::Feature", obj_name)
            existing.Shape = shp_entry["shape"]

        # Set LOD metadata (if new)
        for prop, val in (("GDSLayerID", key[0]), ("GDSDatatype", key[1])):
            try:
                if not hasattr(existing, prop):
                    existing.addProperty("App::PropertyInteger", prop, "LOD", prop)
                setattr(existing, prop, val)
            except Exception:
                pass

        # Colour
        from gds.GDSCommand import _layer_colors
        sr, lr, tr = _layer_colors(
            layer_dict,
            self._aux.get("ihp_map", {}),
            self._aux.get("match_klayout", True),
            self._aux.get("highlight_bondable", True),
        )
        # Remove / update placeholder marker, including the "[not loaded]"
        # label — leaving that on a layer that now holds real geometry would
        # be worse than never having marked it.
        try:
            if hasattr(existing, "IsLayerPlaceholder"):
                existing.IsLayerPlaceholder = False
            if "[not loaded]" in (existing.Label or ""):
                existing.Label = existing.Label.split("[not loaded]")[0].strip()
        except Exception:
            pass

        # PREVIEW: slight transparency as a hint that it is not real 3D
        display_tr = 30 if target == LODState.PREVIEW else tr
        try:
            existing.ViewObject.ShapeColor   = sr
            existing.ViewObject.LineColor    = lr
            existing.ViewObject.Transparency = display_tr
            existing.ViewObject.Visibility   = True
            if target == LODState.DETAIL:
                # Follow the document's current display convention instead
                # of always forcing full detail — a via layer loaded while
                # every other via shows as a block must become a block too.
                # PREVIEW's flat 2D polygons need no such handling.
                sync_new_layer_display(doc, existing)
        except Exception:
            pass

        # Add to GDS_Die group (if not already in it)
        try:
            grp = next(
                (o for o in doc.Objects
                 if o.Name == "GDS_Die" or o.Label == "GDS_Die"),
                None
            )
            if grp and existing.Name not in {o.Name for o in grp.Group}:
                grp.addObject(existing)
        except Exception:
            pass

        try:
            doc.commitTransaction()
        except Exception:
            pass

        doc.recompute()
        FreeCADGui.updateGui()

        self._obj_map[key] = existing
        self._states[key]  = target
        self.state_changed.emit(key, target.value)

        FreeCAD.Console.PrintMessage(f"[LOD] {name} loaded ✓\n")
        self._update_body_solid()

    def _show_layer(self, key: tuple):
        """Makes an already-loaded object visible again, respecting the
        document's current via-detail state (see sync_new_layer_display)."""
        obj = self._obj_map.get(key)
        if obj:
            try:
                sync_new_layer_display(self._doc, obj)
            except Exception:
                pass
        FreeCADGui.updateGui()

    def _update_body_solid(self):
        """
        IC_Body_Solid remains permanently hidden — placeholders take over
        the visual role layer by layer. This method only signals
        when all layers are fully loaded.
        """
        if self.is_all_loaded():
            FreeCAD.Console.PrintMessage("[LOD] All layers loaded.\n")
            self.body_hidden.emit()
        FreeCADGui.updateGui()


# ── Workbench-state save/restore provider ───────────────────────────────────
#
# A reopened document has no LODManager registered at all (_LOD_REGISTRY is a
# process-local dict) — without reconstruction, "promote layer to detail"
# clicks would break entirely after a save/reopen cycle. The computed aux
# dict itself is NOT persisted directly: ihp_map/stack_mm use tuple dict
# keys, which are not cleanly JSON-serialisable/parseable back. Instead only
# the cheap raw inputs are persisted, and aux is rebuilt fresh at restore
# time via the same build_lod_import_params() call used at import time.

def _save_lod_state(doc):
    mgr = get_lod_manager(doc)
    if mgr is None:
        return None
    aux      = mgr._aux
    gds_path = mgr._gds_path
    lyp_path = aux.get("lyp_path")
    map_path = aux.get("map_path")
    options  = aux.get("options")
    if not gds_path or not lyp_path or options is None:
        FreeCAD.Console.PrintWarning(
            "[LOD] save-state: lyp_path/map_path/options missing from aux "
            "— cannot persist LOD manager state for this document.\n"
        )
        return None

    # JSON-safety: options["layer_bbox"] is a set of (layer_id, datatype)
    # tuples (see ui/LayerSelector.py) — not directly serialisable.
    opts_safe = dict(options)
    opts_safe["layer_bbox"] = sorted(
        [lid, dt] for (lid, dt) in options.get("layer_bbox", set())
    )

    return {
        "gds_path":        gds_path,
        "lyp_path":        lyp_path,
        "map_path":        map_path,
        "options":         opts_safe,
        "selected_layers": aux.get("all_layers", []),
    }


def _restore_lod_state(doc, data):
    gds_path = data.get("gds_path")
    lyp_path = data.get("lyp_path")
    map_path = data.get("map_path")
    options  = dict(data.get("options") or {})
    options["layer_bbox"] = {tuple(p) for p in options.get("layer_bbox", [])}
    selected_layers = data.get("selected_layers") or []

    if not gds_path or not lyp_path:
        return
    if not (os.path.exists(gds_path) and os.path.exists(lyp_path)):
        FreeCAD.Console.PrintWarning(
            f"[LOD] restore: source files no longer exist "
            f"(gds={gds_path!r}, lyp={lyp_path!r}) — layer promote/demote "
            "will be unavailable until a fresh GDS import.\n"
        )
        return

    from core.lod_import import build_lod_import_params

    ihp_map      = Core_Functionality.parse_map(map_path) if map_path else {}
    xml_path     = options.get("xml_path")
    stackup_data = Core_Functionality.parse_stackup_xml(xml_path) if xml_path else {}

    _, aux = build_lod_import_params(selected_layers, ihp_map, stackup_data, options)
    # Carry the raw inputs forward so a second save/restore cycle still works.
    aux["lyp_path"] = lyp_path
    aux["map_path"] = map_path
    aux["options"]  = options

    mgr = LODManager(doc, gds_path, aux)
    register_lod_manager(doc, mgr)
    FreeCAD.Console.PrintMessage(
        "[LOD] Manager reconstructed from saved document state.\n"
    )


from session.WorkbenchState import register_state_provider  # noqa: E402
register_state_provider("lod_manager", _save_lod_state, _restore_lod_state)
