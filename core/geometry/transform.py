"""
Point and polygon coordinate transformations for GDS import.

All functions apply the same operation order:
  scale (GDS units → mm) → optional Y-mirror → rotation → translation
"""
import math

try:
    import numpy as _np
    _HAS_NP = True
except ImportError:
    _np   = None
    _HAS_NP = False


def transform_point(p, s: float, rot_deg: float, mirror_y: bool,
                    tx: float, ty: float):
    """Transform a single (x, y) point."""
    x, y = p[0] * s, p[1] * s
    if mirror_y:
        y = -y
    r  = math.radians(rot_deg)
    xr = x * math.cos(r) - y * math.sin(r)
    yr = x * math.sin(r) + y * math.cos(r)
    return xr + tx, yr + ty


def vec_transform(pts, s: float, rot_deg: float, mirror_y: bool,
                  tx: float, ty: float):
    """
    Transform all points of a polygon in one pass.
    Returns a NumPy (N, 2) array when available, else a list of (x, y) tuples.
    """
    if _HAS_NP:
        arr = _np.asarray(pts, dtype=float) * s
        if rot_deg != 0.0:
            r  = math.radians(rot_deg)
            c, sv = math.cos(r), math.sin(r)
            x  = arr[:, 0] * c  - arr[:, 1] * sv
            y  = arr[:, 0] * sv + arr[:, 1] * c
            arr = _np.column_stack((x, y))
        if mirror_y:
            arr[:, 1] = -arr[:, 1]
        arr[:, 0] += tx
        arr[:, 1] += ty
        return arr
    return [transform_point(p, s, rot_deg, mirror_y, tx, ty) for p in pts]


def arr_to_tuples(arr) -> list:
    """Convert a (N, 2) ndarray or list of pairs to list of (x, y) tuples."""
    if _HAS_NP and isinstance(arr, _np.ndarray):
        return [tuple(row) for row in arr]
    return list(arr)
