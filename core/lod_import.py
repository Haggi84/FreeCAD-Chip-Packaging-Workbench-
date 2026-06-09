"""
lod_import.py
=============
Einheitliche Import-Logik für den Level-of-Detail-Workflow.

Zentrale Unterscheidung:
  all_layers       — alle LYP-Layer die im GDS vorhanden sind (vollständige Liste)
  initial_layers   — die Teilmenge, die beim Import sofort geladen wird
                     (Kontakt-Layer + COMP; aus all_layers abgeleitet)

Der LODManager bekommt all_layers, damit er jeden beliebigen Layer
nachträglich laden kann — unabhängig davon, ob er beim Import ausgewählt war.
"""

from __future__ import annotations

import os
from typing import Optional

import FreeCAD

from core import Core_Functionality

# ── Konstanten ────────────────────────────────────────────────────────────────

_PIN_ONLY = {"PIN", "LEFPIN"}
_NON_PIN  = {"NET", "SPNET", "VIA", "DRAWING"}

_MIN_AREA_BOND_MM2  = 0.0
# Kein Flächenfilter für Kontakt-Layer — Verbindungsstege zwischen
# Pads und Vias sind oft nur 10–20 µm breit (≈ 0.0001 mm²) und
# würden bei jedem positiven Schwellwert weggefiltert.
# Fill-Layer bekommen separat bbox-Behandlung, brauchen keinen Flächenfilter.
_MAX_POLYS_CONTACT  = None    # Kein Polygon-Cap — alle Polygone laden
# (inkl. schmale Verbindungsstege zwischen Pads und Vias)


# ── Kategorisierung ───────────────────────────────────────────────────────────

def categorize_layers(all_layers: list, ihp_map: dict) -> dict:
    """
    Teilt alle verfügbaren Layer in Kategorien:

      'contact'  — PIN/Bonding-Layer + COMP (sofort als 3D laden)
      'routing'  — Metal, Via (lazy — auf Anfrage laden)
      'fill'     — Fill/Dummy-Metal (immer BBox, nie tessellieren)
      'pin_flat' — reine PIN-Marker (2D, nie extrudieren)

    Gibt {key: category} zurück.
    """
    fill_keys = {
        k for k, v in (ihp_map or {}).items()
        if "FILL" in v.get("edi_types", set())
    }
    flat_keys = {
        k for k, v in (ihp_map or {}).items()
        if _PIN_ONLY & v.get("edi_types", set())
        and not (_NON_PIN & v.get("edi_types", set()))
    }
    if not flat_keys:
        flat_keys = {
            (l["layer_id"], l["datatype"])
            for l in all_layers
            if l.get("name", "").lower().endswith(".pin")
        }

    top_keys, bottom_keys = Core_Functionality.identify_contact_layers(
        all_layers, ihp_map
    )
    contact_keys = top_keys | bottom_keys

    categories = {}
    for l in all_layers:
        key = (l.get("layer_id", 0), l.get("datatype", 0))
        if key in flat_keys:
            categories[key] = "pin_flat"
        elif key in fill_keys:
            categories[key] = "fill"
        elif key in contact_keys:
            categories[key] = "contact"
        else:
            categories[key] = "routing"

    return categories


def initial_load_layers(all_layers: list, categories: dict) -> list:
    """
    Gibt die Teilmenge zurück, die beim Import sofort geladen wird:
    contact + pin_flat-Layer. Routing und Fill werden lazy geladen bzw. BBox.
    """
    return [
        l for l in all_layers
        if categories.get(
            (l.get("layer_id", 0), l.get("datatype", 0))
        ) in ("contact", "pin_flat")
    ]


# ── Import-Parameter ──────────────────────────────────────────────────────────

def build_lod_import_params(
    all_layers: list,
    ihp_map: dict,
    stackup_data: dict,
    options: dict,
) -> tuple[dict, dict]:
    """
    Leitet alle load_gds()-Parameter ab.

    Gibt (load_kwargs, aux) zurück.
    load_kwargs  → direkt in Core_Functionality.load_gds() spreaden
    aux          → Hilfs-Flags für GDSCommand und LODManager
    """
    match_klayout      = bool(options.get("match_klayout",      True))
    highlight_bondable = bool(options.get("highlight_bondable", True))
    auto_pin_contacts  = bool(options.get("auto_pin_contacts",  False))
    mesh_3d            = bool(options.get("mesh_3d",            False))
    lod_mode           = bool(options.get("lod_mode",           True))
    user_bbox_keys     = set(options.get("layer_bbox",          set()))

    categories = categorize_layers(all_layers, ihp_map)

    fill_layer_keys = {k for k, c in categories.items() if c == "fill"}
    flat_layer_keys = {k for k, c in categories.items() if c == "pin_flat"}
    contact_keys    = {k for k, c in categories.items() if c == "contact"}

    if lod_mode:
        # Nur Kontakt-Layer + PIN-Flat sofort laden
        layers_to_load = initial_load_layers(all_layers, categories)
        extrude_3d     = True
        min_area       = _MIN_AREA_BOND_MM2
        decimate       = 0.0
        max_polys      = _MAX_POLYS_CONTACT

        FreeCAD.Console.PrintMessage(
            f"[LOD] Sofortiger Import: {len(layers_to_load)} Layer "
            f"({len(contact_keys)} Kontakt + {len(flat_layer_keys)} PIN-Flat)\n"
            f"      Routing-Layer ({sum(1 for c in categories.values() if c=='routing')}) "
            f"→ lazy\n"
        )
    else:
        # Vollimport — alle ausgewählten Layer sofort
        layers_to_load = all_layers
        extrude_3d     = bool(options.get("extrude_3d", False)) or mesh_3d
        min_area       = 0.0 if match_klayout else 0.0004
        decimate       = 0.0 if match_klayout else 0.002
        max_polys      = None

    stack_mm = None
    if extrude_3d:
        stack_mm = Core_Functionality.build_stack_mm_from_xml(
            all_layers, ihp_map, stackup_data   # immer über den gesamten Stack
        )

    all_keys  = {(l["layer_id"], l["datatype"]) for l in all_layers}
    excl_bbox = all_keys - user_bbox_keys

    load_kwargs = dict(
        transform               = None,
        preview_2d              = not extrude_3d,
        compound_per_layer      = True,
        min_area_mm2            = min_area,
        decimate_tol_mm         = decimate,
        skip_fill_datatype      = False,
        fill_as_bbox            = True,
        fill_layer_keys         = fill_layer_keys,
        flat_layer_keys         = flat_layer_keys,
        force_bbox_keys         = user_bbox_keys,
        exclude_auto_bbox_keys  = excl_bbox,
        ihp_map                 = ihp_map,
        stack_mm                = stack_mm,
        contacts_only_3d        = lod_mode,
        contact_keys            = contact_keys if lod_mode else None,
        max_polys_per_layer     = max_polys,
        mesh_3d                 = mesh_3d,
        use_cache               = True,
        auto_bbox_threshold     = 5_000,
    )

    aux = dict(
        lod_mode           = lod_mode,
        extrude_3d         = extrude_3d,
        layers_to_load     = layers_to_load,   # was beim Import geladen wird
        all_layers         = all_layers,        # alle verfügbaren Layer
        categories         = categories,        # key → 'contact'/'routing'/'fill'/'pin_flat'
        contact_keys       = contact_keys,
        fill_layer_keys    = fill_layer_keys,
        flat_layer_keys    = flat_layer_keys,
        stack_mm           = stack_mm,
        match_klayout      = match_klayout,
        highlight_bondable = highlight_bondable,
        auto_pin_contacts  = auto_pin_contacts,
        mesh_3d            = mesh_3d,
        ihp_map            = ihp_map,
        stackup_data       = stackup_data,
    )

    return load_kwargs, aux


def get_lazy_load_params(layer_dict: dict, gds_path: str, aux: dict,
                          preview_2d: bool = False) -> dict:
    """
    Parameter für das Nachladen eines einzelnen Layers durch den LOD-Manager.

    preview_2d=True  → schnelle 2D-Polygone (Preview-Stufe)
    preview_2d=False → vollständige 3D-Extrusion (Volume-Stufe)
    """
    stack_mm = aux.get("stack_mm") or {}
    key = (layer_dict.get("layer_id", 0), layer_dict.get("datatype", 0))
    single_stack = {key: stack_mm[key]} if key in stack_mm else None

    fill_layer_keys = aux.get("fill_layer_keys", set())
    flat_layer_keys = aux.get("flat_layer_keys", set())

    return dict(
        transform               = None,
        preview_2d              = preview_2d,
        compound_per_layer      = True,
        min_area_mm2            = 0.0,
        decimate_tol_mm         = 0.0,
        skip_fill_datatype      = False,
        fill_as_bbox            = True,
        fill_layer_keys         = fill_layer_keys,
        flat_layer_keys         = flat_layer_keys,
        force_bbox_keys         = set(),
        exclude_auto_bbox_keys  = {key},
        ihp_map                 = aux.get("ihp_map", {}),
        stack_mm                = single_stack,
        contacts_only_3d        = False,
        mesh_3d                 = aux.get("mesh_3d", False),
        use_cache               = True,
        auto_bbox_threshold     = 0,
        parallel_workers        = max(1, (os.cpu_count() or 2) - 1),
    )
