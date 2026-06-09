"""
2D polygon area, simplification, and iteration helpers.
"""
try:
    import numpy as _np
    _HAS_NP = True
except ImportError:
    _np   = None
    _HAS_NP = False


def polygon_area_mm2(pts) -> float:
    """Shoelace area in model units squared (mm² when pts are already in mm)."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) * 0.5


def area_from_arr(arr) -> float:
    """Shoelace area on a (N, 2) ndarray or list of (x, y) pairs."""
    if _HAS_NP and isinstance(arr, _np.ndarray):
        x, y = arr[:, 0], arr[:, 1]
        return float(0.5 * abs(_np.sum(x * _np.roll(y, -1) - _np.roll(x, -1) * y)))
    return polygon_area_mm2(arr)


def simplify_poly(points, eps: float) -> list:
    """
    Drop almost-collinear or too-close points to reduce polygon complexity.
    *eps* is in mm.  Returns the original list unchanged when eps ≤ 0 or
    the polygon would become degenerate.
    """
    if len(points) <= 3 or eps <= 0:
        return points
    out = [points[0]]
    for i in range(1, len(points) - 1):
        x0, y0 = out[-1]
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        if (x1 - x0) ** 2 + (y1 - y0) ** 2 < eps * eps:
            continue
        cross       = abs((x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0))
        edge_len_sq = (x2 - x0) ** 2 + (y2 - y0) ** 2
        if edge_len_sq > 0 and cross * cross < eps * eps * edge_len_sq:
            continue
        out.append((x1, y1))
    out.append(points[-1])
    return out if len(out) >= 3 else points


def iter_xy(seq):
    """Yield (x, y) pairs from a NumPy Nx2 array, list of pairs, or gdstk.Polygon."""
    pts = getattr(seq, "points", seq)
    try:
        for p in pts:
            try:
                yield float(p[0]), float(p[1])
            except Exception:
                continue
    except TypeError:
        return
