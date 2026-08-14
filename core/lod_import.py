# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
lod_import.py
=============
Unified import logic for the Level-of-Detail workflow.

Central distinction:
  all_layers       — all LYP layers present in the GDS (complete list)
  initial_layers   — the subset that is loaded immediately on import
                     (contact layers + COMP; derived from all_layers)

The LODManager receives all_layers so it can load any layer on demand —
regardless of whether it was selected during import.
"""

from __future__ import annotations

import os
from typing import Optional

import FreeCAD

from core import Core_Functionality

# ── Constants ─────────────────────────────────────────────────────────────────

_PIN_ONLY = {"PIN", "LEFPIN"}
_NON_PIN  = {"NET", "SPNET", "VIA", "DRAWING"}

_MIN_AREA_BOND_MM2  = 0.0
# No area filter for contact layers — connection bridges between
# pads and vias are often only 10–20 µm wide (≈ 0.0001 mm²) and
# would be filtered out at any positive threshold.
# Fill layers receive separate bbox treatment and need no area filter.
_MAX_POLYS_CONTACT  = None    # No polygon cap — load all polygons
# (including narrow connection bridges between pads and vias)


# ── Categorisation ────────────────────────────────────────────────────────────

def categorize_layers(all_layers: list, ihp_map: dict) -> dict:
    """
    Splits all available layers into categories:

      'contact'  — PIN/bonding layers + COMP (load immediately as 3D)
      'routing'  — Metal, Via (lazy — load on demand)
      'fill'     — Fill/Dummy-Metal (always BBox, never tessellate)
      'pin_flat' — pure PIN markers (2D, never extrude)

    Returns {key: category}.
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


# Layer names that mean "vertical connection" across the PDKs this workbench
# handles, matched against the part of the name before the first '.'.
#
# A plain substring test, deliberately: prefix/suffix matching missed
# "TopVia1" and "TopVia2" — the exact two layers in the report that started
# this — because they neither start nor end with "via". None of the metal,
# implant or boundary layer names in either bundled PDK contains one of these
# tokens, which is what the tests pin.
_VIA_NAME_TOKENS = ("via", "licon", "mcon", "vmim")

# Kept separate: "cont" would also match nothing harmful in the bundled PDKs,
# but it is short enough to be risky in a PDK we have not seen, so it must
# match the whole base name rather than appear anywhere in it.
_VIA_NAME_EXACT = ("cont", "contact")


def _name_says_via(name: str) -> bool:
    base = str(name or "").split(".")[0].strip().lower()
    if not base:
        return False
    if base in _VIA_NAME_EXACT:
        return True
    return any(token in base for token in _VIA_NAME_TOKENS)


def via_layer_keys(all_layers: list, ihp_map: dict, stackup_data: dict) -> set:
    """
    The (layer, datatype) keys that are VIA layers.

    Three independent sources, because no PDK reliably provides all three:

      1. the stackup XML's Type="via"      — most authoritative when present
      2. the stream map's VIA type         — IHP's .map carries this
      3. the layer's own name              — the only signal for a PDK
                                              configured with neither

    Checked by (layer, datatype) BEFORE bare layer number: SKY130 puts met1
    on 68/20 and the via above it on 68/44, so a lookup by number alone
    reports the metal and every sky130 via would go unprotected.
    """
    keys = set()
    stack = stackup_data or {}
    mapping = ihp_map or {}
    for layer in all_layers or []:
        key = (layer.get("layer_id", 0), layer.get("datatype", 0))

        entry = stack.get(key)
        if entry is None:
            entry = stack.get(key[0])
        if isinstance(entry, dict) and entry.get("type") == "via":
            keys.add(key)
            continue

        # "VIA" alone is NOT enough: IHP's sg13g2.map lists VIA among the
        # types for every routing metal too ("Metal1 NET,SPNET,PIN,LEFPIN,VIA"),
        # because a metal layer can carry via shapes in the EDI stream. Taking
        # that at face value marked Metal1..Metal5 and both TopMetals as vias.
        # What separates them is NET/SPNET, which only a real routing layer has.
        m = mapping.get(key)
        if m:
            types = {t.upper() for t in m.get("edi_types", set())}
            if "VIA" in types and not (types & {"NET", "SPNET"}):
                keys.add(key)
                continue

        if _name_says_via(layer.get("name")):
            keys.add(key)
    return keys


def initial_load_layers(all_layers: list, categories: dict) -> list:
    """
    Returns the subset that is loaded immediately on import:
    contact + pin_flat layers. Routing and Fill are loaded lazily or as BBox.
    """
    return [
        l for l in all_layers
        if categories.get(
            (l.get("layer_id", 0), l.get("datatype", 0))
        ) in ("contact", "pin_flat")
    ]


# ── Import parameters ─────────────────────────────────────────────────────────

def build_lod_import_params(
    all_layers: list,
    ihp_map: dict,
    stackup_data: dict,
    options: dict,
) -> tuple[dict, dict]:
    """
    Derives all load_gds() parameters.

    Returns (load_kwargs, aux).
    load_kwargs  → spread directly into Core_Functionality.load_gds()
    aux          → auxiliary flags for GDSCommand and LODManager
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
        # Load only contact layers + PIN-Flat immediately
        layers_to_load = initial_load_layers(all_layers, categories)
        extrude_3d     = True
        min_area       = _MIN_AREA_BOND_MM2
        decimate       = 0.0
        max_polys      = _MAX_POLYS_CONTACT

        FreeCAD.Console.PrintMessage(
            f"[LOD] Immediate import: {len(layers_to_load)} layers "
            f"({len(contact_keys)} contact + {len(flat_layer_keys)} PIN-Flat)\n"
            f"      Routing layers ({sum(1 for c in categories.values() if c=='routing')}) "
            f"→ lazy\n"
        )
    else:
        # Full import — all selected layers immediately
        layers_to_load = all_layers
        extrude_3d     = bool(options.get("extrude_3d", False)) or mesh_3d
        min_area       = 0.0 if match_klayout else 0.0004
        decimate       = 0.0 if match_klayout else 0.002
        max_polys      = None

    stack_mm = None
    if extrude_3d:
        stack_mm = Core_Functionality.build_stack_mm_from_xml(
            all_layers, ihp_map, stackup_data   # always across the full stack
        )
        # Close the gap a partial import leaves under the loaded stack.
        #
        # Measured over all_layers — which is LYP-intersect-GDS, i.e. the
        # layers this FILE actually contains, not the whole PDK. That is the
        # right set: a file holding only the top of the stack has nothing
        # below Metal5 to sit on, while a file holding Activ already reaches
        # the die surface and the shift comes out as zero. Using it also
        # means a layer promoted later from the detail panel lands
        # consistently with the ones imported immediately.
        if bool(options.get("drop_to_die_surface", True)):
            from core.tech.stackup import drop_stack_to_die_surface
            stack_mm, shift_mm = drop_stack_to_die_surface(stack_mm)
            if shift_mm:
                FreeCAD.Console.PrintMessage(
                    f"[LOD] Layer stack dropped {shift_mm * 1000.0:.4f} µm so "
                    f"its lowest layer sits on the die surface. Substrate and "
                    f"epi are unaffected.\n")

    all_keys  = {(l["layer_id"], l["datatype"]) for l in all_layers}
    excl_bbox = all_keys - user_bbox_keys

    # VIA layers are protected from automatic simplification unless the user
    # turns that off, or has explicitly asked for THIS layer as a bounding box
    # (force_bbox wins — an explicit choice must beat a blanket protection).
    keep_via_detail = bool(options.get("keep_via_detail", True))
    via_keys = (via_layer_keys(all_layers, ihp_map, stackup_data) - user_bbox_keys
                 if keep_via_detail else set())
    if via_keys:
        FreeCAD.Console.PrintMessage(
            f"[LOD] VIA detail protected on {len(via_keys)} layer(s): "
            + ", ".join(f"{l}/{d}" for l, d in sorted(via_keys)) + "\n"
        )

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
        protect_via_keys        = via_keys,
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
        layers_to_load     = layers_to_load,   # what is loaded during import
        all_layers         = all_layers,        # all available layers
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
        via_keys           = via_keys,
    )

    return load_kwargs, aux


def get_lazy_load_params(layer_dict: dict, gds_path: str, aux: dict,
                          preview_2d: bool = False) -> dict:
    """
    Parameters for lazily loading a single layer by the LOD manager.

    preview_2d=True  → fast 2D polygons (preview step)
    preview_2d=False → full 3D extrusion (volume step)
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
        # Promoting a via layer from the detail panel is the main way vias
        # reach the document at all — LOD mode folds every non-contact layer
        # into one body box at import time. Without this the micro-area scan
        # still flattens the layer the user just asked to see.
        protect_via_keys        = {key} & set(aux.get("via_keys") or ()),
        ihp_map                 = aux.get("ihp_map", {}),
        stack_mm                = single_stack,
        contacts_only_3d        = False,
        mesh_3d                 = aux.get("mesh_3d", False),
        use_cache               = True,
        auto_bbox_threshold     = 0,
        parallel_workers        = max(1, (os.cpu_count() or 2) - 1),
    )
