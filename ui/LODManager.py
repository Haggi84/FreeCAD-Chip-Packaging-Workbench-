"""
LODManager.py
=============
Verwaltet den LOD-Zustand aller GDS-Layer eines Dokuments.

Drei Zustände pro Layer:
  SOLID    ▣  Noch nicht geladen. Der IC_Body_Solid überbrückt den Stack optisch.
  LOADING  ⟳  Tessellierung läuft im Hintergrund-Thread.
  DETAIL   ◉  Vollgeometrie im Dokument sichtbar.

Wichtig: Der Manager kennt ALLE in der LYP+GDS verfügbaren Layer (all_layers),
nicht nur die die beim Import ausgewählt wurden. Damit kann er jeden Layer
nachträglich laden — auch solche die im Import-Dialog nicht angehakt waren.

Kontakt-Layer (PIN, COMP) und PIN-Flat-Layer starten direkt als DETAIL,
da sie beim Import sofort geladen werden. Alle Routing-Layer starten als SOLID.
Fill-Layer bleiben dauerhaft SOLID (BBox, nie vollständig tessellieren).

Thread-Sicherheit: load_gds() läuft in einem QThread. Die OCCT-Objekte
werden als BREP-String übergeben und im Haupt-Thread per Qt-Signal ins
Dokument geschrieben.
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
from gds.TogglePerformanceModeCommand import set_layer_detail

# Globale Registry: doc.Name → LODManager
# Nötig weil FreeCAD App.Document (C++) keine Python-Attribute erlaubt
_LOD_REGISTRY: dict = {}


def get_lod_manager(doc):
    """Gibt den LODManager für ein Dokument zurück, oder None."""
    if doc is None:
        return None
    return _LOD_REGISTRY.get(doc.Name)


def register_lod_manager(doc, manager):
    """Registriert einen LODManager für ein Dokument."""
    if doc is not None:
        _LOD_REGISTRY[doc.Name] = manager


# ── Zustandsmodell ────────────────────────────────────────────────────────────

class LODState(enum.Enum):
    SOLID    = "solid"     # Platzhalter-Quader sichtbar
    LOADING  = "loading"   # Hintergrund-Thread aktiv
    PREVIEW  = "preview"   # 2D-Polygone aus GDS (schnell, kein Z)
    DETAIL   = "detail"    # vollständig 3D extrudiert


# ── Hintergrund-Worker ────────────────────────────────────────────────────────

class _LayerLoadWorker(QtCore.QThread):
    """Lädt einen einzelnen GDS-Layer im Hintergrund-Thread."""

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
                f"[LOD] Worker-Fehler {self._layer_dict.get('name')}: {e}\n"
            )
            self.finished.emit(key, None)


# ── Haupt-Manager ─────────────────────────────────────────────────────────────

class LODManager(QtCore.QObject):
    """
    Koordiniert das stufenweise Laden von GDS-Layern.

    Öffentliche API
    ---------------
    promote(layer_key)            — SOLID → DETAIL (startet Laden wenn nötig)
    promote_all()                 — alle SOLID-Layer laden
    demote(layer_key)             — DETAIL → SOLID (versteckt, Geometrie bleibt)
    state(layer_key) → LODState   — aktuellen Zustand abfragen
    all_layer_dicts() → list      — alle bekannten Layer-Dicts (für Panel-Aufbau)
    """

    state_changed = QtCore.Signal(tuple, str)   # (layer_key, LODState.value)
    body_hidden   = QtCore.Signal()

    def __init__(self, doc, gds_path: str, aux: dict):
        """
        doc       — FreeCAD-Dokument
        gds_path  — Pfad zur GDS-Datei
        aux       — aux-Dict aus lod_import.build_lod_import_params()
                    muss 'all_layers', 'categories', 'contact_keys',
                    'fill_layer_keys' enthalten
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
                # Sofort beim Import geladen
                self._states[key] = LODState.DETAIL
            elif cat == "fill":
                # Bleibt immer BBox — kein Laden möglich
                self._states[key] = LODState.DETAIL   # "fertig" aus LOD-Sicht
            else:
                # Routing-Layer: lazy
                self._states[key] = LODState.SOLID

        # Bereits im Dokument vorhandene FreeCAD-Objekte registrieren
        self._scan_existing_objects()

        # XY-Ausdehnung des Chips aus dem IC_Body_Solid lesen (einmalig)
        self._chip_xy: tuple = self._read_chip_xy()

        # Pro-Layer-Platzhalter erstellen für alle SOLID-Layer
        self._create_layer_placeholders()

        n_solid = sum(1 for s in self._states.values() if s == LODState.SOLID)
        FreeCAD.Console.PrintMessage(
            f"[LOD] Manager: {len(all_layers)} Layer bekannt, "
            f"{n_solid} lazy-ladbar\n"
        )

    # ── Öffentliche Methoden ──────────────────────────────────────────────────

    def state(self, key: tuple) -> LODState:
        return self._states.get(key, LODState.SOLID)

    def is_all_loaded(self) -> bool:
        return all(s != LODState.SOLID for s in self._states.values())

    def all_layer_dicts(self) -> list:
        """Alle bekannten Layer-Dicts, sortiert nach stack_rank (oben → unten)."""
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
        Stuft einen Layer hoch.

        preview_only=False (Standard): SOLID → DETAIL (vollständige 3D-Geometrie)
        preview_only=True:             SOLID/PREVIEW → PREVIEW (schnelle 2D-Polygone)

        Idempotent: bereits in Zielzustand → nur Visibility setzen.
        """
        st = self._states.get(key)
        if st is None:
            FreeCAD.Console.PrintWarning(f"[LOD] promote: unbekannter Key {key}\n")
            return
        if st == LODState.LOADING:
            return

        # Zielzustand bestimmen
        target = LODState.PREVIEW if preview_only else LODState.DETAIL

        # Bereits im Zielzustand oder höher
        if st == LODState.DETAIL and not preview_only:
            self._show_layer(key)
            return
        if st == LODState.PREVIEW and preview_only:
            self._show_layer(key)
            return
        if st == LODState.PREVIEW and not preview_only:
            # Upgrade von PREVIEW → DETAIL
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
        FreeCAD.Console.PrintMessage(f"[LOD] Starte {mode}: {name} {key}\n")

    def promote_all(self, preview_only: bool = False):
        """Startet das Laden aller noch nicht vollständig geladenen Layer."""
        for key, st in list(self._states.items()):
            if st == LODState.SOLID:
                self.promote(key, preview_only=preview_only)
            elif st == LODState.PREVIEW and not preview_only:
                self.promote(key, preview_only=False)

    def demote(self, key: tuple):
        """Versteckt einen Layer (Geometrie bleibt, kann sofort wieder eingeblendet werden)."""
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

    # ── Internes ─────────────────────────────────────────────────────────────

    # ── Platzhalter-Verwaltung ───────────────────────────────────────────────

    def _read_chip_xy(self) -> tuple:
        """
        Liest XY-Ausdehnung aus aux["chip_xy"] (vom GDSCommand beim Import gesetzt).
        Fallback: aus vorhandenen Layer-Shapes im Dokument ableiten.
        """
        # Bevorzugt: direkt aus dem aux-Dict (body_solid XY ohne das Objekt anzuzeigen)
        if "chip_xy" in self._aux:
            return self._aux["chip_xy"]

        # Fallback: aus vorhandenen FreeCAD-Layer-Shapes ableiten
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
        Erstellt für jeden SOLID-Routing-Layer einen vereinfachten Quader
        mit korrekter Z-Position aus stack_mm und der XY-Ausdehnung des Chips.

        Diese Platzhalter sind mit IsLayerPlaceholder=True markiert und
        werden beim echten Laden durch die GDS-Geometrie ersetzt.
        Der globale IC_Body_Solid wird danach ausgeblendet (Platzhalter
        übernehmen seine optische Funktion, aber layerweise).
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

            # Z-Position aus stack_mm
            sm = stack_mm.get(key)
            if sm:
                z0   = float(sm["z0_mm"])
                t_mm = float(sm["t_mm"])
            else:
                z0   = 0.0
                t_mm = 0.001   # 1 µm Mindestdicke

            t_mm = max(t_mm, 1e-4)

            name     = layer_dict.get("name", f"Layer_{key[0]}")
            obj_name = f"Layer_{name}_{key[0]}"

            try:
                box = Part.makeBox(w, h, t_mm, FreeCAD.Vector(xmin, ymin, z0))
                obj = doc.addObject("Part::Feature", obj_name)
                obj.Shape = box

                # Farbe aus LYP (gedimmt um als Platzhalter erkennbar zu sein)
                fc = layer_dict.get("fill-color", "#888888")
                try:
                    r, g, b = hex_to_rgb(fc)
                except Exception:
                    r, g, b = 0.5, 0.5, 0.5
                obj.ViewObject.ShapeColor   = (r, g, b)
                obj.ViewObject.Transparency = 60   # deutlich transparent = Platzhalter
                obj.ViewObject.LineColor    = (0.3, 0.3, 0.3)

                # Metadaten
                for prop, val in (("GDSLayerID", key[0]), ("GDSDatatype", key[1])):
                    try:
                        obj.addProperty("App::PropertyInteger", prop, "LOD", prop)
                        setattr(obj, prop, val)
                    except Exception:
                        pass
                try:
                    obj.addProperty("App::PropertyBool", "IsLayerPlaceholder", "LOD",
                                    "Vereinfachter Platzhalter — wird durch GDS-Geometrie ersetzt")
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
                    f"[LOD] Platzhalter für {name} fehlgeschlagen: {e}\n"
                )

        try:
            doc.commitTransaction()
        except Exception:
            pass

        if n_created:
            doc.recompute()
            FreeCADGui.updateGui()
            FreeCAD.Console.PrintMessage(
                f"[LOD] {n_created} Layer-Platzhalter erstellt.\n"
            )

        # IC_Body_Solid ausblenden — Platzhalter übernehmen jetzt die optische Rolle
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
        """Registriert FreeCAD-Objekte die schon beim Import erstellt wurden."""
        doc = self._doc
        if doc is None:
            return
        for obj in doc.Objects:
            lid = getattr(obj, "GDSLayerID",  None)
            dt  = getattr(obj, "GDSDatatype", None)
            if lid is not None and dt is not None:
                self._obj_map[(lid, dt)] = obj

    def _on_worker_done(self, key: tuple, shapes, target: LODState = None):
        if target is None:
            target = LODState.DETAIL
        w = self._workers.pop(key, None)
        if w:
            w.deleteLater()

        if not shapes:
            FreeCAD.Console.PrintWarning(
                f"[LOD] Keine Shapes für {key} — bleibt SOLID\n"
            )
            self._states[key] = LODState.SOLID
            self.state_changed.emit(key, LODState.SOLID.value)
            return

        self._insert_layer(key, shapes, target)

    def _insert_layer(self, key: tuple, shapes: list,
                      target: LODState = None):
        """Erstellt oder aktualisiert das FreeCAD-Feature. Läuft im Haupt-Thread."""
        if target is None:
            target = LODState.DETAIL
        doc = self._doc
        if doc is None:
            return

        # Den passenden Shape-Eintrag finden
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

        # Vorhandenes Objekt suchen oder neu anlegen
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

        # LOD-Metadaten setzen (falls neu)
        for prop, val in (("GDSLayerID", key[0]), ("GDSDatatype", key[1])):
            try:
                if not hasattr(existing, prop):
                    existing.addProperty("App::PropertyInteger", prop, "LOD", prop)
                setattr(existing, prop, val)
            except Exception:
                pass

        # Farbe
        from gds.GDSCommand import _layer_colors
        sr, lr, tr = _layer_colors(
            layer_dict,
            self._aux.get("ihp_map", {}),
            self._aux.get("match_klayout", True),
            self._aux.get("highlight_bondable", True),
        )
        # Platzhalter-Markierung entfernen / aktualisieren
        try:
            if hasattr(existing, "IsLayerPlaceholder"):
                existing.IsLayerPlaceholder = False
        except Exception:
            pass

        # PREVIEW: leichte Transparenz als Hinweis dass es kein echtes 3D ist
        display_tr = 30 if target == LODState.PREVIEW else tr
        try:
            existing.ViewObject.ShapeColor   = sr
            existing.ViewObject.LineColor    = lr
            existing.ViewObject.Transparency = display_tr
            existing.ViewObject.Visibility   = True
            set_layer_detail(existing, True)
        except Exception:
            pass

        # In GDS_Die-Gruppe aufnehmen (falls noch nicht drin)
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

        FreeCAD.Console.PrintMessage(f"[LOD] {name} geladen ✓\n")
        self._update_body_solid()

    def _show_layer(self, key: tuple):
        """Macht ein bereits geladenes Objekt wieder sichtbar."""
        obj = self._obj_map.get(key)
        if obj:
            try:
                obj.ViewObject.Visibility = True
                set_layer_detail(obj, True)
            except Exception:
                pass
        FreeCADGui.updateGui()

    def _update_body_solid(self):
        """
        IC_Body_Solid bleibt dauerhaft ausgeblendet — Platzhalter übernehmen
        die optische Rolle layerweise. Diese Methode signalisiert nur noch
        wenn alle Layer vollständig geladen sind.
        """
        if self.is_all_loaded():
            FreeCAD.Console.PrintMessage("[LOD] All layers loaded.\n")
            self.body_hidden.emit()
        FreeCADGui.updateGui()
