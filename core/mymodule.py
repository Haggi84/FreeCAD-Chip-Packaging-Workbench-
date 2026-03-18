"""
Compatibility shim — all functionality has been consolidated into Core_Functionality.py.
Importing from this module continues to work but callers should migrate to Core_Functionality.
"""
from core.Core_Functionality import (
    parse_lyp,
    parse_map,
    parse_ihp_map,
    get_gds_layer,
    derive_base_scale_mm,
    load_gds,
    is_bondable,
    style_for_material,
    build_stack_mm,
    bbox_from_entries,
    ILD_SPACING_UM,
    THICKNESS_UM,
    _norm,
    _as_iter,
    _iter_xy,
    _transform_point,
    _polygon_area_mm2,
    _simplify_poly,
)
