# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
GDS I/O subpackage — disk cache, file inspection, polygon extraction.
"""
from .cache import cache_key, load_cache, save_cache
from .inspect import get_gds_layer, estimate_polygon_counts, derive_base_scale_mm
from .extract import (
    load_gds,
    load_pin_cell_shapes,
    import_pin_pads_as_contacts,
)

__all__ = [
    "cache_key", "load_cache", "save_cache",
    "get_gds_layer", "estimate_polygon_counts", "derive_base_scale_mm",
    "load_gds", "load_pin_cell_shapes", "import_pin_pads_as_contacts",
]
