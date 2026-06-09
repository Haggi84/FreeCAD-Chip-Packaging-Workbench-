# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Technology-data subpackage — stackup parsing, layer classification, style mapping.
"""
from .parsers import parse_lyp, parse_map, parse_ihp_map, parse_stackup_xml
from .stackup import (
    THICKNESS_UM,
    ILD_SPACING_UM,
    thickness_um_for_edi,
    stack_rank_for_edi,
    build_stack_mm,
    build_stack_mm_from_xml,
)
from .layer_info import (
    is_bondable,
    identify_contact_layers,
    style_for_material,
)

__all__ = [
    "parse_lyp", "parse_map", "parse_ihp_map", "parse_stackup_xml",
    "THICKNESS_UM", "ILD_SPACING_UM",
    "thickness_um_for_edi", "stack_rank_for_edi",
    "build_stack_mm", "build_stack_mm_from_xml",
    "is_bondable", "identify_contact_layers", "style_for_material",
]
