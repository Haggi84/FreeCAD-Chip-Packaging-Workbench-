# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
GDS polygon extraction and shape building.

This module re-exports from core.Core_Functionality for now.
A full extraction into focused classes (GDSReader, ShapeBuilder, PinDetector)
is tracked as a follow-up task — see REFACTORING.md.
"""
from core.Core_Functionality import (
    load_gds,
    load_pin_cell_shapes,
    import_pin_pads_as_contacts,
)

__all__ = ["load_gds", "load_pin_cell_shapes", "import_pin_pads_as_contacts"]
