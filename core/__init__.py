"""
Core module for DI-PASSIONATE FreeCAD Workbench
Contains fundamental utilities for GDS processing and geometry operations
"""

from core.Core_Functionality import (
    parse_lyp,
    parse_map,
    get_gds_layer,
    _norm,
    load_gds,
    derive_base_scale_mm,
    is_bondable,
    style_for_material,
    _polygon_area_mm2,
    _simplify_poly,
    _transform_point,
    thickness_um_for_edi,
    stack_rank_for_edi,
    build_stack_mm,
    bbox_from_entries,
    THICKNESS_UM,
    ILD_SPACING_UM
)

from core.Color import (
    hex_to_rgb,
    hex_to_qcolor
)

__all__ = [
    'parse_lyp',
    'parse_map',
    'get_gds_layer',
    '_norm',
    'load_gds',
    'derive_base_scale_mm',
    'is_bondable',
    'style_for_material',
    '_polygon_area_mm2',
    '_simplify_poly',
    '_transform_point',
    'thickness_um_for_edi',
    'stack_rank_for_edi',
    'build_stack_mm',
    'bbox_from_entries',
    'hex_to_rgb',
    'hex_to_qcolor',
    'THICKNESS_UM',
    'ILD_SPACING_UM'
]
