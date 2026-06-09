# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Geometry utilities subpackage — coordinate transforms, polygon math, mesh triangulation.
"""
from .transform import transform_point, vec_transform, arr_to_tuples
from .polygon import polygon_area_mm2, simplify_poly, area_from_arr
from .mesh import ear_clip_triangulate, polygon_to_mesh_facets

__all__ = [
    "transform_point", "vec_transform", "arr_to_tuples",
    "polygon_area_mm2", "simplify_poly", "area_from_arr",
    "ear_clip_triangulate", "polygon_to_mesh_facets",
]
