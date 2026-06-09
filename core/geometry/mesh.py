"""
Mesh triangulation helpers — ear-clipping and facet generation.

Used in the mesh_3d rendering path (bypasses OCCT B-rep for maximum GPU performance).
"""


def ear_clip_triangulate(pts2d) -> list:
    """
    Triangulate a simple 2D polygon (no holes) via ear clipping.

    Returns a list of (i, j, k) index tuples into pts2d.
    O(N²) — fast enough for typical GDS polygons (< 200 vertices).
    """
    n = len(pts2d)
    if n < 3:
        return []
    if n == 3:
        return [(0, 1, 2)]

    def _cross(ox, oy, ax, ay, bx, by):
        return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)

    def _in_tri(px, py, ax, ay, bx, by, cx, cy):
        d1 = _cross(ax, ay, bx, by, px, py)
        d2 = _cross(bx, by, cx, cy, px, py)
        d3 = _cross(cx, cy, ax, ay, px, py)
        return not (((d1 < 0) or (d2 < 0) or (d3 < 0)) and
                    ((d1 > 0) or (d2 > 0) or (d3 > 0)))

    area2 = sum(
        pts2d[i][0] * (pts2d[(i + 1) % n][1] - pts2d[(i - 1) % n][1])
        for i in range(n)
    )
    indices = list(range(n))
    if area2 < 0:
        indices.reverse()

    tris   = []
    safety = n * n + n
    i      = 0
    while len(indices) > 3 and safety > 0:
        safety -= 1
        m  = len(indices)
        pi = indices[(i - 1) % m]
        ci = indices[i % m]
        ni = indices[(i + 1) % m]
        ax, ay = pts2d[pi]; bx, by = pts2d[ci]; cx, cy = pts2d[ni]

        if _cross(ax, ay, bx, by, cx, cy) <= 0:
            i = (i + 1) % m
            continue

        if not any(_in_tri(pts2d[oi][0], pts2d[oi][1], ax, ay, bx, by, cx, cy)
                   for oi in indices if oi not in (pi, ci, ni)):
            tris.append((pi, ci, ni))
            indices.pop(i % m)
            m -= 1
            i = i % m if m else 0
        else:
            i = (i + 1) % m

    if len(indices) == 3:
        tris.append(tuple(indices))
    return tris


def polygon_to_mesh_facets(pts2d, z0: float, z1: float) -> list:
    """
    Convert a 2D polygon + z-range into triangle tuples for Mesh.Mesh().

    Each entry is ((x0,y0,z0), (x1,y1,z1), (x2,y2,z2)).
    Includes bottom cap, top cap, and side walls with correct winding order.
    """
    tris = ear_clip_triangulate(pts2d)
    if not tris:
        return []

    facets = []
    n = len(pts2d)

    for a, b, c in tris:   # bottom — CW → downward normal
        facets.append(((pts2d[a][0], pts2d[a][1], z0),
                       (pts2d[c][0], pts2d[c][1], z0),
                       (pts2d[b][0], pts2d[b][1], z0)))
    for a, b, c in tris:   # top — CCW → upward normal
        facets.append(((pts2d[a][0], pts2d[a][1], z1),
                       (pts2d[b][0], pts2d[b][1], z1),
                       (pts2d[c][0], pts2d[c][1], z1)))
    for i in range(n):     # side walls
        j = (i + 1) % n
        x0, y0 = pts2d[i]; x1, y1 = pts2d[j]
        facets.append(((x0, y0, z0), (x1, y1, z0), (x1, y1, z1)))
        facets.append(((x0, y0, z0), (x1, y1, z1), (x0, y0, z1)))

    return facets
