# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Lightweight GDS file inspection — no OCCT geometry, no FreeCAD objects.

These functions are cheap to call and are used to populate the layer selector
UI before the user has committed to a full import.
"""
import gdstk
import FreeCAD


def _as_iter(obj):
    if obj is None:
        return []
    if isinstance(obj, (list, tuple)):
        return obj
    return [obj]


def get_gds_layer(gds_path) -> set:
    """Return set of (layer_id, datatype) that contain polygons or paths."""
    try:
        lib = gdstk.read_gds(gds_path)
        layer_set = set()
        for cell in _as_iter(getattr(lib, "cells", [])):
            for polygon in _as_iter(getattr(cell, "polygons", [])):
                layer_set.add((polygon.layer, polygon.datatype))
            for path in _as_iter(getattr(cell, "paths", [])):
                layers_attr = getattr(path, "layers", None)
                dtypes_attr = getattr(path, "datatypes", None)
                if layers_attr:
                    for i, lyr in enumerate(layers_attr):
                        dt = dtypes_attr[i] if (dtypes_attr and i < len(dtypes_attr)) else 0
                        layer_set.add((lyr, dt))
                else:
                    layer_set.add((getattr(path, "layer", 0), getattr(path, "datatype", 0)))
        return layer_set
    except Exception as e:
        FreeCAD.Console.PrintError(f"Error reading GDSII file {gds_path}: {str(e)}\n")
        return set()


def estimate_polygon_counts(gds_path: str) -> dict:
    """
    Count polygons per (layer_id, datatype) without building any geometry.
    Returns {(layer_id, datatype): count}.  Fast — reads the file once, no OCCT.
    """
    try:
        lib = gdstk.read_gds(gds_path)
        counts: dict = {}
        for cell in _as_iter(getattr(lib, "cells", [])):
            for polygon in _as_iter(getattr(cell, "polygons", [])):
                k = (polygon.layer, polygon.datatype)
                counts[k] = counts.get(k, 0) + 1
            for path in _as_iter(getattr(cell, "paths", [])):
                layers_attr = getattr(path, "layers", None)
                dtypes_attr = getattr(path, "datatypes", None)
                if layers_attr:
                    for i, lyr in enumerate(layers_attr):
                        dt = dtypes_attr[i] if (dtypes_attr and i < len(dtypes_attr)) else 0
                        k = (lyr, dt)
                        counts[k] = counts.get(k, 0) + 1
                else:
                    k = (getattr(path, "layer", 0), getattr(path, "datatype", 0))
                    counts[k] = counts.get(k, 0) + 1
        return counts
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"estimate_polygon_counts: {e}\n")
        return {}


def derive_base_scale_mm(gds_path) -> float:
    """
    Return the base scale in *mm per user unit* from the GDS library.
    Falls back to 0.001 (typical µm units).
    """
    try:
        lib = gdstk.read_gds(gds_path)
        if hasattr(lib, "unit") and lib.unit:
            return lib.unit * 1000.0
        return 0.001
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to derive base scale from {gds_path}: {e}\n")
        return 0.001
