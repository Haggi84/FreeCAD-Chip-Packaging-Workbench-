import xml.etree.ElementTree as ET
import math
import hashlib
import os
import pickle
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import gdstk
import FreeCAD
import Part
from FreeCAD import Base

try:
    import numpy as _np
    _HAS_NP = True
except ImportError:
    _np = None
    _HAS_NP = False

# ── module-level constants ────────────────────────────────────────────────────

# Layers with more polygons than this are auto-collapsed to a bounding box
# rather than processed individually (prevents 3M-polygon FILL layers from
# taking 30 minutes).  0 = disabled.
AUTO_BBOX_POLY_THRESHOLD: int = 50_000

# Layers whose MEDIAN polygon area (in µm²) is below this threshold are
# auto-collapsed to a bounding box, regardless of polygon count.
# This catches sub-micron Fill-Metal / Dummy-Metal layers (e.g. Layer 6/DT0
# in IHP SG13G2 with 471 k rectangles each ~0.026 µm²) that would otherwise
# spend minutes in OCCT for shapes invisible at any zoom level.
# Real routing and pad layers have median areas >> 10 µm².
# Set to 0.0 to disable.
MICRO_AREA_BBOX_THRESHOLD_UM2: float = 2.0   # µm²
MICRO_AREA_SAMPLE_SIZE: int = 500

# Disk-cache directory for serialised GDS import results
def _gds_cache_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "FreeCAD" / "DI-PASSIONATE" / "gds_cache"

_GDS_CACHE_DIR = _gds_cache_dir()


# ── cache helpers ─────────────────────────────────────────────────────────────

def _cache_key(gds_path: str, selected_layers, options: dict) -> str:
    h = hashlib.sha256()
    try:
        h.update(str(Path(gds_path).stat().st_mtime_ns).encode())
    except OSError:
        h.update(gds_path.encode())
    h.update(repr([(l.get("layer_id"), l.get("datatype")) for l in selected_layers]).encode())
    h.update(repr(sorted(options.items())).encode())
    return h.hexdigest()[:24]


def _load_cache(key: str):
    p = _GDS_CACHE_DIR / f"{key}.pkl"
    if not p.exists():
        return None
    try:
        with open(p, "rb") as fh:
            payload = pickle.load(fh)
        # Restore Part.Shapes from serialised BREP strings
        results = []
        for entry in payload:
            e = dict(entry)
            if "shape_brep" in e:
                shp = Part.Shape()
                shp.importBrepFromString(e.pop("shape_brep"))
                e["shape"] = shp
            results.append(e)
        return results
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"GDS cache load failed ({exc}), re-importing.\n")
        return None


def _save_cache(key: str, results: list):
    try:
        _GDS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = []
        for entry in results:
            e = dict(entry)
            if "shape" in e and not e.get("is_mesh"):
                try:
                    e["shape_brep"] = e.pop("shape").exportBrepToString()
                except Exception:
                    return          # non-serialisable shape — skip cache
            payload.append(e)
        p = _GDS_CACHE_DIR / f"{key}.pkl"
        with open(p, "wb") as fh:
            pickle.dump(payload, fh, protocol=4)
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"GDS cache save failed: {exc}\n")


# ── vectorised geometry helpers ───────────────────────────────────────────────

def _vec_transform(pts, s: float, rot_deg: float, mirror_y: bool,
                   tx: float, ty: float):
    """
    Transform a polygon's points in one NumPy pass.
    Returns a (N,2) ndarray when NumPy is available, else a list of (x,y) tuples.
    """
    if _HAS_NP:
        arr = _np.asarray(pts, dtype=float) * s
        if rot_deg != 0.0:
            r = math.radians(rot_deg)
            c, sv = math.cos(r), math.sin(r)
            x = arr[:, 0] * c - arr[:, 1] * sv
            y = arr[:, 0] * sv + arr[:, 1] * c
            arr = _np.column_stack((x, y))
        if mirror_y:
            arr[:, 1] = -arr[:, 1]
        arr[:, 0] += tx
        arr[:, 1] += ty
        return arr
    return [_transform_point(p, s, rot_deg, mirror_y, tx, ty) for p in pts]


def _area_from_arr(arr) -> float:
    """Shoelace area on a (N,2) ndarray or list of (x,y) pairs."""
    if _HAS_NP and isinstance(arr, _np.ndarray):
        x, y = arr[:, 0], arr[:, 1]
        return float(0.5 * abs(_np.sum(x * _np.roll(y, -1) - _np.roll(x, -1) * y)))
    return _polygon_area_mm2(arr)


def _arr_to_tuples(arr) -> list:
    """Convert a (N,2) ndarray or list of pairs to list of (x,y) tuples."""
    if _HAS_NP and isinstance(arr, _np.ndarray):
        return [tuple(row) for row in arr]
    return list(arr)

# -------------------------------
# KLayout LYP parsing (colors)
# -------------------------------

def parse_lyp(lyp_path, layer_map=None):
    """
    Parse a KLayout LYP file and return:
        (layers, unique_colors)

    layers: list of dicts with keys:
        name, source, visible, frame-color, fill-color, layer_id, datatype
    unique_colors: set of (frame_color, fill_color)
    Only visible layers are returned.
    """
    try:
        tree = ET.parse(lyp_path)
        root = tree.getroot()
        layers = []
        unique_colors = set()

        for prop in root.findall(".//properties"):
            layer_dict = {}
            for child in prop:
                layer_dict[child.tag] = child.text if child.text else None

            name = layer_dict.get("name", "Unknown Layer")
            source = layer_dict.get("source", None)
            visible = layer_dict.get("visible", "false") == "true"
            frame_color = layer_dict.get("frame-color", "#000000")
            fill_color = layer_dict.get("fill-color", "#FFFFFF")

            if visible and source:
                try:
                    layer_id, datatype = map(int, source.split("/"))
                    layer_dict["layer_id"] = layer_id
                    layer_dict["datatype"] = datatype

                    layers.append(layer_dict)
                    unique_colors.add((frame_color, fill_color))
                except (ValueError, TypeError):
                    FreeCAD.Console.PrintWarning(f"Invalid source format in layer {source}: {source}\n")
                    continue
        # FreeCAD.Console.PrintMessage(f"Parsed {len(layers)} visible layers from {lyp_path}: {[(l['layer_id'], l['datatype'], l['name']) for l in layers[:5]]}\n") 
        return (layers, unique_colors)

    except ET.ParseError:
        FreeCAD.Console.PrintError(f"Error parsing LYP file {lyp_path}: Invalid format\n")
        return ([], set())
    except FileNotFoundError:
        FreeCAD.Console.PrintError(f"LYP file {lyp_path} not found\n")
        return ([], set())
    except Exception as e:
        FreeCAD.Console.PrintError(f"An error occurred while parsing LYP file {lyp_path}: {str(e)}\n")
        return ([], set())
    
# ---------------------------------------
# IHP .map parsing (technology mapping)
# ---------------------------------------

def parse_map(map_path):
    """
    Parse IHP *.map file and return dict keyed by (gds_layer, gds_datatype) -> {
        'edi_name': <str>,         # e.g. 'TopMetal2'
        'edi_types': set[str]      # e.g. {'PIN','LEFPIN'} or {'FILL'} ...
    }

    The map file can contain multiple lines mapping the same (layer,datatype) with
    different types. We merge them into a set for easier use.
    """

    try:
        layer_map = {}
        with open(map_path, 'r', encoding='utf-8', errors='ignore') as f:
            for raw in f:
                line = raw.strip()
                # Skip comments / blanks
                if not line or line.startswith("#") or '====' in line or any(keyword in line.lower() for keyword in ['date', 'copyright', 'license', 'edi stream', 'version']):
                    continue
                try:
                    parts = [p for p in line.split() if p]
                    if len(parts) < 4:
                        continue
                    edi_name, edi_types_csv = parts[0], parts[1]
                    gds_layer, gds_datatype = int(parts[2]), int(parts[3])
                except (ValueError, IndexError):
                    FreeCAD.Console.PrintWarning(f"Invalid line in .map file {map_path}: {line}\n")
                    continue
        #FreeCAD.Console.PrintMessage(f"Parsed {len(layer_map)} layer mappings from {map_path}\n")
                key = (gds_layer, gds_datatype)
                types = set([t.strip().upper() for t in edi_types_csv.split(",") if t.strip()])
                shape = layer_map.get(key, {"edi_name": edi_name, "edi_types": set()})
                shape["edi_name"] = edi_name
                shape["edi_types"].update(types)
                layer_map[key] = shape
        return layer_map
    except FileNotFoundError:
        FreeCAD.Console.PrintError(f"MAP file '{map_path}' not found\n")
        return {}
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to parse MAP file '{map_path}': {e}\n")
        return {}

# Alias for callers that use the older name
parse_ihp_map = parse_map

# ------------------------------------------------
# Stackup XML parser (KLayout / IHP format)
# ------------------------------------------------

def parse_stackup_xml(xml_path):
    """
    Parse a stackup XML file (KLayout/IHP ELayers format) and return a lookup dict.

    The dict is keyed both by layer name (upper-case str) and by GDS layer number (int):
        "METAL1"  -> { 'zmin_um', 'zmax_um', 'thickness_um', 'gds_layer', 'type' }
        8         -> { same }   (gds_layer == 8 for Metal1 in SG13G2)

    'type' is one of 'conductor', 'via', 'dielectric'.
    Returns {} on any error so callers can safely fall back to hard-coded defaults.
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        result = {}
        for layer in root.findall(".//Layer"):
            name      = layer.get("Name", "").strip()
            ltype     = layer.get("Type", "conductor").lower()
            try:
                zmin_um = float(layer.get("Zmin", 0))
                zmax_um = float(layer.get("Zmax", 0))
            except ValueError:
                continue
            gds_layer_str = layer.get("Layer", "")
            gds_layer = int(gds_layer_str) if gds_layer_str.isdigit() else -1
            entry = {
                "zmin_um":      zmin_um,
                "zmax_um":      zmax_um,
                "thickness_um": abs(zmax_um - zmin_um),
                "gds_layer":    gds_layer,
                "type":         ltype,
            }
            if name:
                result[name.upper()] = entry
            if gds_layer >= 0:
                result[gds_layer] = entry
        FreeCAD.Console.PrintMessage(
            f"Loaded stackup XML '{xml_path}': {sum(isinstance(k, str) for k in result)} layers.\n"
        )
        return result
    except FileNotFoundError:
        FreeCAD.Console.PrintError(f"Stackup XML '{xml_path}' not found.\n")
        return {}
    except ET.ParseError as e:
        FreeCAD.Console.PrintError(f"Stackup XML parse error in '{xml_path}': {e}\n")
        return {}
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to load stackup XML '{xml_path}': {e}\n")
        return {}


# ------------------------------------------------
# Thickness & stacking helpers (simple defaults)
# ------------------------------------------------

# Default metal/via thicknesses [µm] – adjust to your PDK as needed.
THICKNESS_UM = {
    # routing metals
    "METAL1": 0.9, "METAL2": 0.9, "METAL3": 0.9, "METAL4": 1.2, "METAL5": 2.0,
    # top metals
    "TOPMETAL1": 2.0, "TOPMETAL2": 3.0,
    # vias
    "VIA1": 0.5, "VIA2": 0.5, "VIA3": 0.5, "VIA4": 0.5, "TOPVIA1": 1.0, "TOPVIA2": 1.0,
    # components / comp (use a tiny plate for visibility)
    "COMP": 0.2,
}

# Default dielectric spacing (ILD) between stack levels [µm]
ILD_SPACING_UM = 0.8


def _as_iter(obj):
    """Wrap None/scalar in a list so callers can always iterate."""
    if obj is None:
        return []
    if isinstance(obj, (list, tuple)):
        return obj
    return [obj]

def _iter_xy(seq):
    """Yield (x, y) pairs from a numpy Nx2 array, list of pairs, or gdstk.Polygon."""
    pts = getattr(seq, 'points', seq)
    try:
        for p in pts:
            try:
                yield float(p[0]), float(p[1])
            except Exception:
                continue
    except TypeError:
        return

def _norm(s):
    return (s or "").upper()


def thickness_um_for_edi(edi_name: str) -> float:
    """
    Returns thickness in µm for an EDI layer name (best-effort).
    """
    n = _norm(edi_name).replace("/", "_")
    # direct hit
    if n in THICKNESS_UM:
        return THICKNESS_UM[n]
    # pattern-based
    for key in list(THICKNESS_UM.keys()):
        if n.startswith(key):
            return THICKNESS_UM[key]
    # try to pull trailing number (e.g., Metal5, TopMetal2)
    if "METAL" in n:
        # fallback for any metal
        return 1.0
    if "VIA" in n:
        return 0.5
    return 0.2

def stack_rank_for_edi(edi_name: str) -> int:
    """
    Compute a sort key for vertical order. Higher rank = closer to the top.
    TopMetal2 > TopMetal1 > Metal5 > ... > Metal1 > COMP > Vias (around their metals)
    """
    n = _norm(edi_name)
    if n.startswith("TOPMETAL"):
        # TopMetal2 -> 700, TopMetal1 -> 600
        try:
            num = int(''.join([c for c in n if c.isdigit()]) or "0")
        except Exception:
            num = 0
        return 600 + 100 * num
    if n.startswith("METAL"):
        try:
            num = int(''.join([c for c in n if c.isdigit()]) or "1")
        except Exception:
            num = 1
        return 100 * num  # Metal5=500, Metal1=100
    if n.startswith("COMP"):
        return 50
    if n.startswith("TOPVIA"):
        return 650  # around top metals
    if n.startswith("VIA"):
        # place near its upper metal (roughly)
        try:
            num = int(''.join([c for c in n if c.isdigit()]) or "1")
        except Exception:
            num = 1
        return 100 * num + 10
    return 0

def build_stack_mm(selected_layers, ihp_map, ild_um: float = ILD_SPACING_UM):
    """
    Build a per-layer stacking dictionary:
        key (layer_id, datatype) -> {'t_mm': float, 'z0_mm': float}

    z0_mm is the *bottom* Z of that layer; thickness is t_mm.
    Order is computed from edi_name (best-effort).
    """
    # Collect ranks & thickness
    entries = []
    for L in selected_layers:
        lid = L.get("layer_id", 0)
        dt = L.get("datatype", 0)
        m = ihp_map.get((lid, dt), None)
        edi = m["edi_name"] if m else L.get("name", "Metal1")
        rank = stack_rank_for_edi(edi)
        t_um = thickness_um_for_edi(edi)
        entries.append(((lid, dt), edi, rank, t_um))

    # Sort by rank ascending (bottom to top)
    entries.sort(key=lambda e: e[2])

    z_current_um = 0.0
    out = {}
    for idx, (key, edi, rank, t_um) in enumerate(entries):
        # Place at current Z, then add thickness + dielectric (except above top of stack)
        out[key] = {"t_mm": t_um / 1000.0, "z0_mm": z_current_um / 1000.0}
        z_current_um += t_um
        # add ILD spacing before next layer if that next one is a higher rank "metal-ish"
        if idx < len(entries) - 1:
            out_next = entries[idx + 1]
            if "METAL" in _norm(out_next[1]):
                z_current_um += ild_um

    return out


def build_stack_mm_from_xml(selected_layers, ihp_map, stackup_data):
    """
    Build a per-layer stacking dictionary using absolute Z positions from a
    parsed stackup XML (see parse_stackup_xml()).

    Returns the same format as build_stack_mm():
        (layer_id, datatype) -> {'t_mm': float, 'z0_mm': float}

    Lookup order for each layer:
      1. By GDS layer number (exact match in stackup_data)
      2. By EDI name from ihp_map (case-insensitive)
      3. Fall back to build_stack_mm() heuristics for anything not found.
    """
    if not stackup_data:
        return build_stack_mm(selected_layers, ihp_map)

    out = {}
    fallback_layers = []

    for L in selected_layers:
        lid = L.get("layer_id", 0)
        dt  = L.get("datatype",  0)
        key = (lid, dt)

        # 1. Match by GDS layer number
        entry = stackup_data.get(lid)

        # 2. Match by EDI name
        if entry is None:
            m = ihp_map.get(key)
            if m:
                entry = stackup_data.get(m["edi_name"].upper())

        if entry is not None:
            out[key] = {
                "t_mm":  entry["thickness_um"] / 1000.0,
                "z0_mm": entry["zmin_um"]       / 1000.0,
            }
        else:
            fallback_layers.append(L)

    # Layers not present in the XML fall back to rank-based defaults
    if fallback_layers:
        out.update(build_stack_mm(fallback_layers, ihp_map))

    return out


# ------------------------------------------------
# GDS inspection & geometry creation helpers
# ------------------------------------------------

def get_gds_layer(gds_path):
    """
    Analyze GDS and return set of (layer_id, datatype) that contain polygons or paths.
    """
    try:
        lib = gdstk.read_gds(gds_path)
        layer_set = set()

        for cell in _as_iter(getattr(lib, 'cells', [])):
            for polygon in _as_iter(getattr(cell, 'polygons', [])):
                layer_set.add((polygon.layer, polygon.datatype))
            for path in _as_iter(getattr(cell, 'paths', [])):
                layers_attr = getattr(path, 'layers', None)
                dtypes_attr = getattr(path, 'datatypes', None)
                if layers_attr:
                    for i, lyr in enumerate(layers_attr):
                        dt = dtypes_attr[i] if (dtypes_attr and i < len(dtypes_attr)) else 0
                        layer_set.add((lyr, dt))
                else:
                    layer_set.add((getattr(path, 'layer', 0), getattr(path, 'datatype', 0)))

        return layer_set

    except Exception as e:
        FreeCAD.Console.PrintError(f"Error reading GDSII file {gds_path}: {str(e)}\n")
        return set()
    
def derive_base_scale_mm(gds_path):
    """
    Return the base scale in *mm per user unit* from the GDS library.
    If the library unit is meters per user unit, mm = unit * 1000.
    Fallback assumes typical µm units: 0.001 mm per user unit.
    """
    try:
        lib = gdstk.read_gds(gds_path)
        if hasattr(lib, "unit") and lib.unit:
            return lib.unit * 1000.0
        return 0.001  # µm -> mm fallback
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to derive base scale from {gds_path}: {e}\n")
        return 0.001

def _transform_point(p, s, rot_deg, mirror_y, tx, ty):
    """Apply scale (to mm) -> optional mirror(Y) -> rotation -> translation."""
    x, y = p[0] * s, p[1] * s
    if mirror_y:
        y = -y
    r = math.radians(rot_deg)
    xr = x * math.cos(r) - y * math.sin(r)
    yr = x * math.sin(r) + y * math.cos(r)
    return xr + tx, yr + ty

def _polygon_area_mm2(pts):
    """Signed area in squared model units (mm^2 once 'pts' are scaled)."""
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return abs(a) * 0.5

def _simplify_poly(points, eps):
    """Drop almost-collinear or too-close points. eps in mm."""
    if len(points) <= 3 or eps <= 0:
        return points
    out = [points[0]]
    for i in range(1, len(points) - 1):
        x0, y0 = out[-1]
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        # distance to previous point
        if (x1 - x0) ** 2 + (y1 - y0) ** 2 < eps * eps:
            continue
        # Perpendicular distance from (x1,y1) to line (x0,y0)→(x2,y2) < eps.
        # cross has units mm²; dividing by edge length (mm) gives mm — correct.
        # Equivalent to: cross² < eps² * edge_len_sq  (avoids sqrt).
        cross = abs((x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0))
        edge_len_sq = (x2 - x0) ** 2 + (y2 - y0) ** 2
        if edge_len_sq > 0 and cross * cross < eps * eps * edge_len_sq:
            continue
        out.append((x1, y1))
    out.append(points[-1])
    return out if len(out) >= 3 else points

def _ear_clip_triangulate(pts2d):
    """
    Triangulate a simple 2D polygon (no holes) via ear clipping.
    Returns a list of (i, j, k) index tuples into pts2d.
    O(N²) — fast for typical GDS polygons (< 200 vertices).
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

    # Signed area via shoelace — negative means CW; reverse to CCW
    area2 = sum(pts2d[i][0] * (pts2d[(i + 1) % n][1] - pts2d[(i - 1) % n][1])
                for i in range(n))
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
        ax, ay = pts2d[pi];  bx, by = pts2d[ci];  cx, cy = pts2d[ni]

        if _cross(ax, ay, bx, by, cx, cy) <= 0:        # reflex vertex — skip
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


def _polygon_to_mesh_facets(pts2d, z0, z1):
    """
    Convert a 2D polygon + z-range to a flat list of triangle tuples suitable
    for Mesh.Mesh() construction.  Each entry is ((x0,y0,z0),(x1,y1,z1),(x2,y2,z2)).
    """
    tris = _ear_clip_triangulate(pts2d)
    if not tris:
        return []
    facets = []
    n = len(pts2d)
    for a, b, c in tris:                                # bottom (CW → downward normal)
        facets.append(((pts2d[a][0], pts2d[a][1], z0),
                       (pts2d[c][0], pts2d[c][1], z0),
                       (pts2d[b][0], pts2d[b][1], z0)))
    for a, b, c in tris:                                # top (CCW → upward normal)
        facets.append(((pts2d[a][0], pts2d[a][1], z1),
                       (pts2d[b][0], pts2d[b][1], z1),
                       (pts2d[c][0], pts2d[c][1], z1)))
    for i in range(n):                                  # side walls
        j = (i + 1) % n
        x0, y0 = pts2d[i];  x1, y1 = pts2d[j]
        facets.append(((x0, y0, z0), (x1, y1, z0), (x1, y1, z1)))
        facets.append(((x0, y0, z0), (x1, y1, z1), (x0, y0, z1)))
    return facets


def load_gds(gds_path,
             selected_layers,
             transform=None,
             preview_2d=False,
             compound_per_layer=True,
             min_area_mm2=0.0,
             decimate_tol_mm=0.0,
             skip_fill_datatype=True,
             fill_as_bbox=True,       # replace filler polygons with a single bounding-box solid
             fill_layer_keys=None,    # extra (layer_id, datatype) pairs to treat as filler
             ihp_map=None,            # full EDI map — used to detect FILL layers by edi_types tag
             stack_mm=None,
             contacts_only_3d=False,  # render only contact_keys as full 3D; collapse rest to one body
             contact_keys=None,       # set of (layer_id, datatype) to keep as full geometry
             max_polys_per_layer=None,# cap on polygons per contact layer (sorted largest-area first)
             flat_layer_keys=None,    # (layer_id, datatype) always rendered as flat 2D face (never extruded)
             mesh_3d=False,           # bypass OCCT B-rep: build Mesh.Mesh directly from triangulated polygons
             auto_bbox_threshold=None,# collapse layer to bbox when polygon count exceeds this (None = use module default)
             use_gdstk_union=False,   # merge overlapping polygons per layer in C++ before building shapes
             use_cache=False,         # serialise result to disk; second import is near-instant
             parallel_workers=0,      # number of threads for data-prep phase (0 = serial)
             progress_callback=None
             ):
    """
    GDS loader:
    - builds ONE compound Part shape per selected (layer,datatype)
    - optional 2D preview (wires only) or 3D with per-layer thickness/offset
    - filters tiny polygons and optional FILL (datatype 22) by default

    Returns: list of dicts (one entry per selected layer/DT), each:
        {
          'shape': Part.Shape,
          'layer_id': int,
          'datatype': int,
          'frame_hex': '#rrggbb',
          'fill_hex':  '#rrggbb',
        }
    """
    _bbox_threshold = AUTO_BBOX_POLY_THRESHOLD if auto_bbox_threshold is None else int(auto_bbox_threshold)

    # ── disk-cache check ──────────────────────────────────────────────────────
    _cache_options = {
        "preview_2d": preview_2d, "min_area": min_area_mm2,
        "decimate": decimate_tol_mm, "fill_bbox": fill_as_bbox,
        "mesh_3d": mesh_3d, "bbox_thresh": _bbox_threshold,
    }
    _ck = _cache_key(gds_path, selected_layers, _cache_options) if use_cache else None
    if _ck:
        _hit = _load_cache(_ck)
        if _hit is not None:
            FreeCAD.Console.PrintMessage(f"GDS cache hit ({_ck[:8]}…) — skipping import.\n")
            return _hit

    try:
        lib = gdstk.read_gds(gds_path)
        if transform is None:
            transform = {}

        # base scale: mm per user unit
        s = transform.get("scale", None)
        if s is None:
            s = (lib.unit * 1000.0) if hasattr(lib, "unit") and lib.unit else 0.001

        rot_deg = float(transform.get("rot_deg", 0.0))
        mirror_y = bool(transform.get("mirror_y", False))
        tx = float(transform.get("tx", 0.0))
        ty = float(transform.get("ty", 0.0))

        # default thickness if no stack provided (thin preview)
        default_t_mm = float(transform.get("z_thickness", 3)) if not preview_2d else 0.0

        wanted = {(l.get("layer_id", 0), l.get("datatype", 0)) for l in selected_layers}
        by_layer = {key: [] for key in wanted}

        # Filler detection: DT=22 convention + any explicitly declared fill keys
        # + any key whose EDI type set contains "FILL" (catches Drawing layers like
        # 29/0 that carry millions of dummy-metal polygons but use DT=0, not DT=22)
        _fill_keys = set(fill_layer_keys or [])
        _flat_keys = set(flat_layer_keys or [])

        def _is_filler(lyr, dt):
            if dt == 22 or (lyr, dt) in _fill_keys:
                return True
            # If the EDI map explicitly tags this key as FILL, collapse it to a
            # bounding box regardless of datatype or other co-assigned EDI types.
            if ihp_map:
                entry = ihp_map.get((lyr, dt))
                if entry and "FILL" in entry.get("edi_types", set()):
                    return True
            return False

        # Running bounding extents for filler layers — filled in bbox mode
        fill_extents = {}   # (layer, dt) -> [xmin, ymin, xmax, ymax]

        # contacts_only_3d: accumulate ALL non-contact layers into one body bbox
        _contact_keys = set(contact_keys or [])
        _body_extents = None   # [xmin, ymin, xmax, ymax] for the combined body solid

        top_cells = lib.top_level() or lib.cells

        def _points_array(obj):
            """
            Normalize polygon-like objects to a plain point array.

            Some gdstk versions return Polygon objects instead of numpy arrays
            both from get_polygons and from path flattening helpers. Handling
            it in one place keeps downstream loops simple.
            """

            return obj.points if hasattr(obj, "points") else obj

        def _polygons_from_cell(cell, depth=None, include_paths=True):
            """
            Return {(layer, datatype): [points, ...]} for a cell, flattening references.

            Tries the newer get_polygons(by_spec=...) signature first, and falls back
            to copying + flattening for older gdstk versions that don't support the
            by_spec keyword.
            """
            try:
                poly_map = cell.get_polygons(by_spec=True, include_paths=include_paths, depth=depth)
                # Normalize polygon payloads to raw point arrays in case the
                # gdstk version returns Polygon objects instead of numpy arrays.
                if isinstance(poly_map, dict):
                    norm = {}
                    for key, polys in poly_map.items():
                        norm[key] = [p.points if hasattr(p, "points") else p for p in polys]
                    return norm
                # Some versions without by_spec support return a flat list.
                if isinstance(poly_map, (list, tuple)):
                    norm = {}
                    for poly in poly_map:
                        pts = poly.points if hasattr(poly, "points") else poly
                        lyr = getattr(poly, "layer", 0)
                        dtype = getattr(poly, "datatype", 0)
                        norm.setdefault((lyr, dtype), []).append(pts)
                    return norm
                return poly_map
            except TypeError:
                pass

            clone = cell.copy(name=f"{cell.name}_flat_tmp")
            try:
                clone.flatten(depth=depth)
            except TypeError:
                try:
                    # Older gdstk versions don't accept the depth keyword; try positional
                    clone.flatten(depth)
                except TypeError:
                    # Oldest versions ignore depth entirely
                    clone.flatten()

            poly_map = {}
            for poly in getattr(clone, "polygons", []):
                pts = poly.points if hasattr(poly, "points") else poly
                poly_map.setdefault((poly.layer, poly.datatype), []).append(pts)
            if include_paths:
                for path in getattr(clone, "paths", []):
                    polys = [_points_array(p) for p in path.to_polygons()]
                    layers_attr = getattr(path, "layers", None)
                    dtypes_attr = getattr(path, "datatypes", None)

                    # Normalize to list lengths matching polygon count
                    def _expand(attr, fallback_name):
                        if attr is None:
                            val = getattr(path, fallback_name, 0)
                            return [val] * len(polys)
                        if not isinstance(attr, (list, tuple)):
                            return [attr] * len(polys)
                        if len(attr) >= len(polys):
                            return list(attr[:len(polys)])
                        if attr:
                            return list(attr) + [attr[-1]] * (len(polys) - len(attr))
                        return [0] * len(polys)

                    layers = _expand(layers_attr, "layer")
                    dtypes = _expand(dtypes_attr, "datatype")

                    for pts, lyr, dtype in zip(polys, layers, dtypes):
                        poly_map.setdefault((lyr, dtype), []).append(pts)
            return poly_map

        def iter_polygons():
            for cell in top_cells:
                poly_map = _polygons_from_cell(cell, depth=None, include_paths=True)
                for (layer, datatype), polys in poly_map.items():
                    for pts in polys:
                        yield layer, datatype, pts

        polygons = list(iter_polygons())

        # ── gdstk Boolean Union: merge overlapping polygons per layer in C++ ─
        # Dramatically reduces polygon count for dense routing layers before any
        # Python-level processing.  Operates on raw gdstk objects, not Part shapes.
        if use_gdstk_union and not preview_2d:
            from collections import defaultdict as _dd
            _raw_by_key = _dd(list)
            for lyr, dt, pts in polygons:
                if (lyr, dt) in wanted and not _is_filler(lyr, dt):
                    _raw_by_key[(lyr, dt)].append(
                        gdstk.Polygon(pts if not hasattr(pts, "points") else pts.points,
                                      layer=lyr, datatype=dt)
                    )
            _merged = {}
            for key, polys in _raw_by_key.items():
                try:
                    result = gdstk.boolean(polys, [], "or", layer=key[0], datatype=key[1])
                    _merged[key] = [(key[0], key[1], p.points) for p in result]
                    FreeCAD.Console.PrintMessage(
                        f"  gdstk union layer {key[0]}/{key[1]}: "
                        f"{len(polys):,} → {len(result):,} polygons\n"
                    )
                except Exception as _ue:
                    FreeCAD.Console.PrintWarning(f"  gdstk union failed for {key}: {_ue}\n")
            # Rebuild polygons list: replace unioned keys, keep filler/non-selected as-is
            _non_union = [(lyr, dt, pts) for lyr, dt, pts in polygons
                          if (lyr, dt) not in _merged]
            _union_flat = [t for tlist in _merged.values() for t in tlist]
            polygons = _non_union + _union_flat

        # ── auto-bbox threshold ───────────────────────────────────────────────
        # Count polygons per (layer, dt).  Any selected, non-fill key that
        # exceeds the threshold is added to _fill_keys so the main loop
        # collapses it to a single bounding-box solid instead of processing
        # each polygon individually.
        if _bbox_threshold > 0:
            _key_counts: dict = {}
            for lyr, dt, _ in polygons:
                k = (lyr, dt)
                if k in wanted:
                    _key_counts[k] = _key_counts.get(k, 0) + 1
            for k, cnt in _key_counts.items():
                if cnt > _bbox_threshold and not _is_filler(k[0], k[1]):
                    _fill_keys.add(k)
                    FreeCAD.Console.PrintWarning(
                        f"  Auto-bbox: layer {k[0]}/{k[1]} has {cnt:,} polygons "
                        f"(threshold {_bbox_threshold:,}) → bounding box\n"
                    )

        # ── Micro-area pre-scan: collapse sub-micron Fill-Metal layers ────────
        # Many process nodes (e.g. IHP SG13G2) insert hundreds of thousands of
        # sub-micron dummy-metal rectangles for CMP planarisation.  These are
        # invisible in any 3-D view and catastrophically slow to process in OCCT.
        # Strategy: sample up to MICRO_AREA_SAMPLE_SIZE polygons per layer,
        # compute the median area in µm², and collapse any layer below
        # MICRO_AREA_BBOX_THRESHOLD_UM2 to a single bounding-box solid.
        if MICRO_AREA_BBOX_THRESHOLD_UM2 > 0.0 and _HAS_NP:
            # Group raw polygon point arrays by key for sampling
            _key_sample: dict = {}
            for lyr, dt, poly_pts_raw in polygons:
                k = (lyr, dt)
                if k not in wanted or k in _fill_keys:
                    continue
                if _is_filler(lyr, dt):
                    continue
                _key_sample.setdefault(k, [])
                if len(_key_sample[k]) < MICRO_AREA_SAMPLE_SIZE:
                    _key_sample[k].append(_points_array(poly_pts_raw))

            # unit² → µm²: 1 unit = lib.unit metres; 1 µm = 1e-6 m
            # area_um2 = area_units² × (lib.unit / 1e-6)²
            _unit_to_um = (lib.unit / 1e-6) if hasattr(lib, "unit") and lib.unit else 1.0
            _unit_to_um2 = _unit_to_um ** 2

            for k, sample_pts in _key_sample.items():
                if not sample_pts:
                    continue
                areas_um2 = []
                for pts in sample_pts:
                    try:
                        arr = _np.asarray(pts, dtype=float)
                        if arr.shape[0] < 3:
                            continue
                        x, y = arr[:, 0], arr[:, 1]
                        area = abs(float(_np.dot(x, _np.roll(y, 1)) -
                                         _np.dot(y, _np.roll(x, 1)))) / 2.0
                        areas_um2.append(area * _unit_to_um2)
                    except Exception:
                        pass
                if not areas_um2:
                    continue
                median_um2 = float(_np.median(areas_um2))
                if median_um2 < MICRO_AREA_BBOX_THRESHOLD_UM2:
                    _fill_keys.add(k)
                    n_polys = sum(1 for lyr, dt, _ in polygons if (lyr, dt) == k)
                    FreeCAD.Console.PrintWarning(
                        f"  Micro-area bbox: layer {k[0]}/{k[1]} "
                        f"median={median_um2:.4f} µm² < {MICRO_AREA_BBOX_THRESHOLD_UM2} µm² "
                        f"({n_polys:,} polygons) → bounding box\n"
                    )

        # ── contacts_only_3d: cap polygon count per contact layer ────────────
        # Sort each contact layer's polygons by area (largest first) and keep
        # only the top max_polys_per_layer entries.  This drops tiny routing
        # wires and via fills while keeping the large bond pads.
        if contacts_only_3d and _contact_keys and max_polys_per_layer and max_polys_per_layer > 0:
            staging  = {}   # contact key → [(raw_area, layer, dt, pts), ...]
            rest_out = []
            for lyr, dt, pts in polygons:
                key = (lyr, dt)
                if key in _contact_keys:
                    pts_arr = _points_array(pts)
                    raw_xy  = [(float(p[0]) * s, float(p[1]) * s)
                               for p in (pts_arr if hasattr(pts_arr, '__iter__') else [])]
                    area    = _polygon_area_mm2(raw_xy) if len(raw_xy) >= 3 else 0.0
                    staging.setdefault(key, []).append((area, lyr, dt, pts))
                else:
                    rest_out.append((lyr, dt, pts))

            filtered_contact = []
            for key, items in staging.items():
                items.sort(key=lambda x: x[0], reverse=True)   # sort by area only — avoids numpy array comparison fallthrough
                kept = items[:max_polys_per_layer]
                filtered_contact.extend((l, d, p) for _, l, d, p in kept)
                FreeCAD.Console.PrintMessage(
                    f"  Poly-cap layer {key}: {len(items):,} polygons → "
                    f"kept {len(kept):,} largest\n"
                )
            polygons = filtered_contact + rest_out

        # ── Phase 1: bbox accumulators (serial) + bucket normal polygons ────────
        _raw_normals: dict = {}   # key -> [pts_array, ...]
        for layer, datatype, poly_pts_raw in polygons:
            poly_pts = _points_array(poly_pts_raw)
            key = (layer, datatype)
            if key not in wanted:
                continue

            # contacts_only_3d: non-contact layers → body bbox accumulator
            if contacts_only_3d and key not in _contact_keys:
                _arr_b = _vec_transform(poly_pts, s, rot_deg, mirror_y, tx, ty)
                if len(_arr_b) > 0:
                    if _HAS_NP and isinstance(_arr_b, _np.ndarray):
                        _xmn, _xmx = float(_arr_b[:, 0].min()), float(_arr_b[:, 0].max())
                        _ymn, _ymx = float(_arr_b[:, 1].min()), float(_arr_b[:, 1].max())
                    else:
                        _xmn, _xmx = min(p[0] for p in _arr_b), max(p[0] for p in _arr_b)
                        _ymn, _ymx = min(p[1] for p in _arr_b), max(p[1] for p in _arr_b)
                    if _body_extents is None:
                        _body_extents = [_xmn, _ymn, _xmx, _ymx]
                    else:
                        _body_extents[0] = min(_body_extents[0], _xmn)
                        _body_extents[1] = min(_body_extents[1], _ymn)
                        _body_extents[2] = max(_body_extents[2], _xmx)
                        _body_extents[3] = max(_body_extents[3], _ymx)
                continue

            if _is_filler(layer, datatype):
                if fill_as_bbox:
                    _arr_f = _vec_transform(poly_pts, s, rot_deg, mirror_y, tx, ty)
                    if len(_arr_f) > 0:
                        if _HAS_NP and isinstance(_arr_f, _np.ndarray):
                            _xmn, _xmx = float(_arr_f[:, 0].min()), float(_arr_f[:, 0].max())
                            _ymn, _ymx = float(_arr_f[:, 1].min()), float(_arr_f[:, 1].max())
                        else:
                            _xmn, _xmx = min(p[0] for p in _arr_f), max(p[0] for p in _arr_f)
                            _ymn, _ymx = min(p[1] for p in _arr_f), max(p[1] for p in _arr_f)
                        if key in fill_extents:
                            e = fill_extents[key]
                            e[0] = min(e[0], _xmn); e[1] = min(e[1], _ymn)
                            e[2] = max(e[2], _xmx); e[3] = max(e[3], _ymx)
                        else:
                            fill_extents[key] = [_xmn, _ymn, _xmx, _ymx]
                continue

            _raw_normals.setdefault(key, []).append(poly_pts)

        # progress total from normal-polygon count
        _all_raw_count = sum(len(v) for v in _raw_normals.values())
        progress_total = max(_all_raw_count, 1) if progress_callback else None
        if progress_callback:
            progress_callback(0, progress_total, "Importing GDS layers...")

        # ── Phase 2: transform + filter — parallel when parallel_workers > 0 ──
        # NumPy releases the GIL during array math, so threads give real speedup
        # on transform-heavy layers even with CPython.  OCCT calls happen only
        # in Phase 3 (main thread), so thread-safety is not a concern here.
        def _prep_one(poly_pts_raw):
            _arr = _vec_transform(poly_pts_raw, s, rot_deg, mirror_y, tx, ty)
            pts2d = _arr_to_tuples(_arr)
            if decimate_tol_mm > 0.0:
                pts2d = _simplify_poly(pts2d, decimate_tol_mm)
                _arr = _np.array(pts2d) if _HAS_NP else pts2d
            if len(pts2d) < 3:
                return None
            if min_area_mm2 > 0.0 and _area_from_arr(_arr) < min_area_mm2:
                return None
            return pts2d

        def _prep_key_batch(item):
            key, raw_list = item
            out = []
            for p in raw_list:
                r = _prep_one(p)
                if r is not None:
                    out.append(r)
            return key, out

        _prepped: dict = {}   # key -> [pts2d, ...]

        if parallel_workers > 0:
            _n_workers = max(1, int(parallel_workers))
            with ThreadPoolExecutor(max_workers=_n_workers) as _pool:
                for _k, _pts_list in _pool.map(_prep_key_batch, _raw_normals.items()):
                    _prepped[_k] = _pts_list
            FreeCAD.Console.PrintMessage(
                f"Parallel data-prep done ({_n_workers} workers, "
                f"{sum(len(v) for v in _prepped.values()):,} polygons passed filter).\n"
            )
        else:
            for _k, _out in map(_prep_key_batch, _raw_normals.items()):
                _prepped[_k] = _out

        # ── Phase 3: OCCT / mesh shape creation (always main thread) ─────────
        progress_count = 0
        for key, pts2d_list in _prepped.items():
            layer_k, dt_k = key
            for pts2d in pts2d_list:
                progress_count += 1
                if progress_callback and progress_total:
                    msg = f"Importing layer {layer_k}/{dt_k} ({progress_count}/{progress_total})"
                    if progress_callback(progress_count, progress_total, msg) is False:
                        return []

                wire = Part.makePolygon([(x, y, 0.0) for (x, y) in (pts2d + [pts2d[0]])])
                if preview_2d or (_flat_keys and key in _flat_keys):
                    try:
                        by_layer[key].append(Part.Face(wire))
                    except Exception:
                        by_layer[key].append(wire)
                elif mesh_3d:
                    if stack_mm and key in stack_mm:
                        t_mm = float(stack_mm[key]["t_mm"])
                        z0   = float(stack_mm[key]["z0_mm"])
                    else:
                        t_mm = default_t_mm
                        z0   = 0.0
                    facets = _polygon_to_mesh_facets(pts2d, z0, z0 + t_mm)
                    if facets:
                        by_layer[key].extend(facets)
                else:
                    try:
                        face = Part.Face(wire)
                    except Exception:
                        try:
                            wire = Part.Wire(wire.Edges)
                            face = Part.Face(wire)
                        except Exception:
                            continue
                    if stack_mm and key in stack_mm:
                        t_mm = float(stack_mm[key]["t_mm"])
                        z0 = float(stack_mm[key]["z0_mm"])
                    else:
                        t_mm = default_t_mm
                        z0 = 0.0
                    shp = face.extrude(FreeCAD.Vector(0, 0, t_mm))
                    if z0 != 0.0:
                        shp.translate(FreeCAD.Vector(0, 0, z0))
                    by_layer[key].append(shp)

        # Build a single bounding-box shape for each filler layer
        for key, (xmin, ymin, xmax, ymax) in fill_extents.items():
            w = xmax - xmin
            h = ymax - ymin
            if w <= 0 or h <= 0:
                continue
            if preview_2d:
                bbox_wire = Part.makePolygon([
                    FreeCAD.Vector(xmin, ymin, 0),
                    FreeCAD.Vector(xmax, ymin, 0),
                    FreeCAD.Vector(xmax, ymax, 0),
                    FreeCAD.Vector(xmin, ymax, 0),
                    FreeCAD.Vector(xmin, ymin, 0),
                ])
                try:
                    by_layer[key] = [Part.Face(bbox_wire)]
                except Exception:
                    by_layer[key] = [bbox_wire]
            else:
                lid_f, dt_f = key
                if stack_mm and key in stack_mm:
                    t_mm = float(stack_mm[key]["t_mm"])
                    z0   = float(stack_mm[key]["z0_mm"])
                else:
                    t_mm = default_t_mm
                    z0   = 0.0
                box = Part.makeBox(w, h, max(t_mm, 1e-4),
                                   FreeCAD.Vector(xmin, ymin, z0))
                by_layer[key] = [box]

        # one compound (or mesh) per layer
        results = []
        for layer in selected_layers:
            lid = layer.get("layer_id", 0)
            dt  = layer.get("datatype", 0)
            parts = by_layer.get((lid, dt), [])
            if not parts:
                continue
            if mesh_3d and not preview_2d:
                # parts is a flat list of triangle tuples — build one Mesh per layer
                try:
                    import Mesh as _Mesh
                    mesh_obj = _Mesh.Mesh(parts)
                    results.append({
                        "mesh":     mesh_obj,
                        "is_mesh":  True,
                        "layer_id": lid,
                        "datatype": dt,
                        "frame_hex": layer.get("frame-color", "#000000"),
                        "fill_hex":  layer.get("fill-color", "#FFFFFF"),
                    })
                except Exception as exc:
                    FreeCAD.Console.PrintWarning(
                        f"Mesh assembly failed for layer {lid}/{dt}: {exc}\n"
                    )
            else:
                compound = Part.makeCompound(parts) if compound_per_layer and len(parts) > 1 else parts[0]
                results.append({
                    "shape":    compound,
                    "layer_id": lid,
                    "datatype": dt,
                    "frame_hex": layer.get("frame-color", "#000000"),
                    "fill_hex":  layer.get("fill-color", "#FFFFFF"),
                })

        # Build the combined body solid for contacts_only_3d mode
        if contacts_only_3d and _body_extents is not None:
            xmin, ymin, xmax, ymax = _body_extents
            w = xmax - xmin
            h = ymax - ymin
            if w > 0 and h > 0:
                if stack_mm:
                    total_z = max(v["z0_mm"] + v["t_mm"] for v in stack_mm.values())
                else:
                    total_z = default_t_mm if default_t_mm > 0 else 0.008
                total_z = max(total_z, 1e-4)
                body_box = Part.makeBox(w, h, total_z, FreeCAD.Vector(xmin, ymin, 0))
                results.append({
                    "shape":        body_box,
                    "layer_id":     -1,
                    "datatype":     -1,
                    "frame_hex":    "#555555",
                    "fill_hex":     "#aaaaaa",
                    "is_body_solid": True,
                })

        if progress_callback and progress_total is not None:
            progress_callback(progress_total, progress_total, "Finalizing GDS shapes...")
        if _ck:
            _save_cache(_ck, results)
        return results

    except Exception as e:
        FreeCAD.Console.PrintError(f"Error loading GDS file {gds_path}: {str(e)}\n")
        return []
    

def _apply_gds_ref_transform(pts, origin, rotation_rad, magnification, x_reflection):
    """
    Apply a single GDS reference transform to a list of (x, y) points.
    GDS transform order: magnify → x_reflect → rotate → translate.
    """
    ox  = float(origin[0]) if origin else 0.0
    oy  = float(origin[1]) if origin else 0.0
    mag = float(magnification) if magnification else 1.0
    rot = float(rotation_rad)  if rotation_rad  else 0.0
    xr  = bool(x_reflection)

    if rot != 0.0:
        cos_r = math.cos(rot)
        sin_r = math.sin(rot)
    else:
        cos_r, sin_r = 1.0, 0.0

    result = []
    for p in pts:
        x = float(p[0]) * mag
        y = float(p[1]) * mag
        if xr:
            y = -y
        if rot != 0.0:
            x, y = x * cos_r - y * sin_r, x * sin_r + y * cos_r
        result.append((x + ox, y + oy))
    return result


def load_pin_cell_shapes(gds_path, transform=None):
    """
    Recursively walk the full GDS cell-reference hierarchy and collect all
    instances of cells named exactly 'pin' (case-insensitive).

    Returns a flat 2D Part.Shape compound (faces at Z=0), or None if nothing found.
    These shapes are intentionally never extruded.

    The recursive walk is needed because 'pin' cells are often referenced from
    sub-cells rather than directly from the top-level cell.  Each level of
    ancestor transforms is tracked and composed to produce correct absolute
    coordinates.
    """
    try:
        lib = gdstk.read_gds(gds_path)
        if transform is None:
            transform = {}

        s        = transform.get("scale", None)
        if s is None:
            s = (lib.unit * 1000.0) if hasattr(lib, "unit") and lib.unit else 0.001
        rot_deg  = float(transform.get("rot_deg",  0.0))
        mirror_y = bool(transform.get("mirror_y", False))
        tx       = float(transform.get("tx", 0.0))
        ty       = float(transform.get("ty", 0.0))

        pin_cell_names = {c.name.lower() for c in lib.cells if c.name.lower() == "pin"}
        if not pin_cell_names:
            FreeCAD.Console.PrintMessage("load_pin_cell_shapes: no cell named 'pin' found in GDS library.\n")
            return None

        top_cells = lib.top_level() or lib.cells

        def collect(cell, ancestor_transforms, visited):
            """
            ancestor_transforms: list of (origin, rotation_rad, magnification, x_reflection)
            built from the outermost ancestor down to the current cell.
            ref.get_polygons() returns coords in the *current cell's* coordinate space.
            To reach top-cell space we apply ancestor_transforms in reverse order
            (innermost → outermost).
            """
            if id(cell) in visited:
                return []
            visited.add(id(cell))

            raw_pts_list = []
            for ref in getattr(cell, "references", []):
                ref_cell = getattr(ref, "cell", None)
                if ref_cell is None:
                    continue

                ref_origin  = getattr(ref, "origin",        (0.0, 0.0))
                ref_rot     = float(getattr(ref, "rotation",       0.0) or 0.0)
                ref_mag     = float(getattr(ref, "magnification",  1.0) or 1.0)
                ref_xr      = bool(getattr(ref,  "x_reflection",  False))

                if ref_cell.name.lower() in pin_cell_names:
                    # ref.get_polygons() gives coords in THIS cell's (parent) space,
                    # already including ref's own transform.
                    try:
                        raw_polys = ref.get_polygons()
                    except Exception:
                        continue
                    for poly in raw_polys:
                        pts = list(poly.points if hasattr(poly, "points") else poly)
                        # Walk up through ancestors to reach top-cell space.
                        for (a_orig, a_rot, a_mag, a_xr) in reversed(ancestor_transforms):
                            pts = _apply_gds_ref_transform(pts, a_orig, a_rot, a_mag, a_xr)
                        raw_pts_list.append(pts)
                else:
                    new_ancestors = ancestor_transforms + [(ref_origin, ref_rot, ref_mag, ref_xr)]
                    raw_pts_list.extend(collect(ref_cell, new_ancestors, visited))

            return raw_pts_list

        all_raw = []
        for cell in top_cells:
            all_raw.extend(collect(cell, [], set()))

        faces = []
        for pts in all_raw:
            pts2d = [_transform_point(p, s, rot_deg, mirror_y, tx, ty) for p in pts]
            if len(pts2d) < 3:
                continue
            try:
                wire = Part.makePolygon(
                    [FreeCAD.Vector(x, y, 0.0) for (x, y) in pts2d]
                    + [FreeCAD.Vector(pts2d[0][0], pts2d[0][1], 0.0)]
                )
                faces.append(Part.Face(wire))
            except Exception:
                pass

        FreeCAD.Console.PrintMessage(
            f"load_pin_cell_shapes: found {len(faces)} pin shape(s) across full hierarchy.\n"
        )
        if not faces:
            return None
        return Part.makeCompound(faces) if len(faces) > 1 else faces[0]

    except Exception as e:
        FreeCAD.Console.PrintError(f"load_pin_cell_shapes: {e}\n")
        return None


def is_bondable(types: set) -> bool:
    if not types:
        return False
    T = {t.upper() for t in types}
    return any(t in T for t in ("PIN", "LEFPIN", "BUMP", "PAD"))


def identify_contact_layers(selected_layers, ihp_map):
    """
    Return (top_keys, bottom_keys) as sets of (layer_id, datatype).

    top_keys   — bondable / PIN layers at the highest stack rank
                 (bond pads on TopMetal2/TopMetal1).
    bottom_keys — lowest-rank non-via / non-fill layer
                 (COMP / active-device surface).

    If ihp_map is empty or None a simple heuristic based on layer_id
    ordering is used as a fallback.
    """
    entries = []
    for L in selected_layers:
        lid = L.get("layer_id", 0)
        dt  = L.get("datatype", 0)
        key = (lid, dt)
        m   = (ihp_map or {}).get(key)
        edi   = m["edi_name"]  if m else ""
        types = m["edi_types"] if m else set()
        rank  = stack_rank_for_edi(edi)
        entries.append((key, rank, is_bondable(types), edi, types))

    if not entries:
        return set(), set()

    # ── top contacts ──────────────────────────────────────────────────────────
    bondable = [(k, r) for k, r, b, *_ in entries if b]
    if bondable:
        max_rank = max(r for _, r in bondable)
        # include all bondable layers within one metal tier of the top
        top_keys = {k for k, r in bondable if r >= max_rank - 100}
    else:
        # no bondable info: pick the single highest-rank layer(s)
        max_rank = max(r for _, r, *_ in entries)
        top_keys = {k for k, r, *_ in entries if r == max_rank}

    # ── bottom contacts ───────────────────────────────────────────────────────
    non_via_fill = [
        (k, r) for k, r, _, edi, types in entries
        if "VIA" not in edi.upper()
        and "FILL" not in {t.upper() for t in types}
    ]
    if non_via_fill:
        min_rank = min(r for _, r in non_via_fill)
        bottom_keys = {k for k, r in non_via_fill if r == min_rank}
    else:
        min_rank = min(r for _, r, *_ in entries)
        bottom_keys = {k for k, r, *_ in entries if r == min_rank}

    bottom_keys -= top_keys   # never overlap
    return top_keys, bottom_keys

def style_for_material(edi_name: str, edi_types: set):
    """
    Return a simple material style tuple:
        (material_label:str, shape_rgb:tuple, line_rgb:tuple, transparency:int[0..100])
    Bondable layers get a gold style.
    """
    en = (edi_name or "").upper()
    et = {t.upper() for t in (edi_types or set())}

    # Gold highlight for bondable
    if is_bondable(et):
        return ("Bondable metal", (0.90, 0.75, 0.20), (0.25, 0.20, 0.10), 0)

    # Vias darker
    if "VIA" in et and "FILL" not in et:
        return ("Via metal", (0.35, 0.35, 0.35), (0.08, 0.08, 0.08), 0)

    # Fill semi transparent
    if "FILL" in et:
        return ("Metal fill / dielectric", (0.70, 0.85, 1.0), (0.25, 0.35, 0.45), 70)

    # Routing metals
    if en.startswith("TOPMETAL") or en.startswith("METAL") or "METAL" in et:
        return ("Routing metal", (0.60, 0.60, 0.60), (0.12, 0.12, 0.12), 0)

    if en.startswith("COMP") or en.startswith("DIEAREA") or "DIE" in et:
        return ("Component/Die", (0.80, 0.90, 0.95), (0.25, 0.35, 0.45), 60)

    return ("Generic", (0.75, 0.75, 0.75), (0.10, 0.10, 0.10), 0)

# -----------------------------------------
# Auto PIN pad detection & contact points
# -----------------------------------------

def _resolve_edi_name(layer_id: int, datatype: int, ihp_map: dict,
                      selected_layers: list) -> str:
    """
    Best-effort EDI name for a (layer_id, datatype) pair.
    Tries: map exact → map drawing DT → selected_layers by layer_id → fallback.
    """
    if ihp_map:
        entry = ihp_map.get((layer_id, datatype))
        if entry:
            return entry["edi_name"]
        # drawing entry often carries the layer name even when PIN uses DT!=0
        draw = ihp_map.get((layer_id, 0))
        if draw:
            return draw["edi_name"]
    if selected_layers:
        for sl in selected_layers:
            if sl.get("layer_id") == layer_id:
                return sl.get("name", f"Layer_{layer_id}")
    return f"Layer_{layer_id}"


def _get_layer_polygons(cell, layer_id, datatype):
    """
    Return a list of Polygon objects for (layer_id, datatype) from *cell*,
    flattening the full cell hierarchy.

    Handles three gdstk API generations:
      New  : cell.get_polygons(layer=L, datatype=DT) → list[Polygon]
      Mid  : cell.get_polygons(by_spec=True)         → dict{(L,DT): list[Polygon]}
      Old  : clone.flatten() + manual attribute filter
    """
    try:
        return cell.get_polygons(layer=layer_id, datatype=datatype)
    except TypeError:
        pass
    try:
        spec_map = cell.get_polygons(by_spec=True)
        if isinstance(spec_map, dict):
            return spec_map.get((layer_id, datatype), [])
        # flat list – filter by attributes
        return [p for p in spec_map
                if getattr(p, "layer", None) == layer_id
                and getattr(p, "datatype", None) == datatype]
    except TypeError:
        pass
    # Oldest gdstk: clone + flatten
    try:
        clone = cell.copy(name=f"__tmp_{cell.name}_{layer_id}_{datatype}")
        clone.flatten()
    except Exception:
        return []
    return [p for p in getattr(clone, "polygons", [])
            if getattr(p, "layer", None) == layer_id
            and getattr(p, "datatype", None) == datatype]


def _find_pin_layer_keys(gds_path: str, ihp_map: dict,
                         selected_layers: list, top_n: int):
    """
    Return (candidates, strategy_label) where candidates is a list of
    (layer_id, datatype, edi_name) tuples ordered best-first.

    Three cascading strategies
    --------------------------
    S1 — IHP map PIN/LEFPIN types
         Requires .map with is_bondable() entries that exist in the GDS.
    S2 — DT=2 convention (Cadence/IHP: datatype 2 = PIN polygon)
         Works without a map file. Covers ALL_LNA.gds and similar real
         full-chip GDS files where DT=2 marks the exact bonding area.
    S3 — Top drawing layers by stack rank / layer number
         Last resort for simplified or custom GDS that only have DT=0.
         Uses layer name from LYP (selected_layers) or map for ranking.
    """
    present = get_gds_layer(gds_path)   # set of (layer, dt) pairs in GDS

    # ── Strategy 1: IHP map bondable types ──────────────────────────────────
    if ihp_map:
        s1 = [
            (k[0], k[1], v["edi_name"])
            for k, v in ihp_map.items()
            if is_bondable(v.get("edi_types", set())) and k in present
        ]
        if s1:
            s1.sort(key=lambda t: stack_rank_for_edi(t[2]), reverse=True)
            return s1[:top_n], "IHP map PIN/LEFPIN types"

    # ── Strategy 2: datatype-2 convention ───────────────────────────────────
    dt2 = [(l, 2) for (l, d) in present if d == 2]
    if dt2:
        candidates = []
        for (l, d) in dt2:
            name = _resolve_edi_name(l, d, ihp_map, selected_layers)
            candidates.append((l, d, name))
        # Primary sort: stack rank (knows TopMetal2 > TopMetal1 > Metal5 ...)
        # Secondary sort: layer number descending (IHP higher layer = higher metal)
        candidates.sort(key=lambda t: (stack_rank_for_edi(t[2]), t[0]), reverse=True)
        return candidates[:top_n], "DT=2 PIN polygon convention (Cadence/IHP)"

    # ── Strategy 3: top drawing layers ──────────────────────────────────────
    dt0 = [(l, 0) for (l, d) in present if d == 0]
    candidates = []
    for (l, d) in dt0:
        name = _resolve_edi_name(l, d, ihp_map, selected_layers)
        candidates.append((l, d, name))
    candidates.sort(key=lambda t: (stack_rank_for_edi(t[2]), t[0]), reverse=True)
    return candidates[:top_n], "top drawing layers by stack rank (no PIN type info)"


def import_pin_pads_as_contacts(gds_path: str, ihp_map: dict, doc,
                                selected_layers=None, top_n: int = 3) -> int:
    """
    Detect the top-N PIN/bond-pad layers, extrude the actual pad metal geometry
    to PDK thickness, and place a ContactPoint marker at every pin location.

    Key insight — IHP/Cadence layer convention
    ------------------------------------------
    DT=2  PIN markers  : tiny 2-5 µm shapes that mark *where* to bond.
                         Used as contact point locations (accurate).
    DT=0  drawing      : large physical metal shapes (bond pad + routing).
                         Used as 3D pad geometry (visually meaningful).

    For each DT=2 marker the function finds the DT=0 polygon that contains it
    and extrudes that as the 3D pad.  When no DT=2 markers exist (S3 fallback)
    large DT=0 polygons are used directly (min-dimension ≥ 10 µm).

    GDS extraction uses gdstk's native layer/datatype C++ filter:
      cell.get_polygons(layer=L, datatype=DT)
    For a 4375-cell / 1.3 M-polygon GDS this is ~0.002 s per query vs ~0.5 s
    for a full Python-side iteration.

    Returns the number of ContactPoint markers created.
    """
    candidates, strategy = _find_pin_layer_keys(
        gds_path, ihp_map, selected_layers or [], top_n
    )
    if not candidates:
        FreeCAD.Console.PrintWarning(
            "Auto PIN detection: no candidate layers found in the GDS file.\n"
        )
        return 0

    FreeCAD.Console.PrintMessage(
        f"Auto PIN detection — strategy: {strategy}\n"
        "  Candidate layers: "
        + ", ".join(f"{name} ({lid}/{dt})" for lid, dt, name in candidates)
        + "\n"
    )

    # ── read GDS ─────────────────────────────────────────────────────────────
    try:
        lib = gdstk.read_gds(gds_path)
    except Exception as e:
        FreeCAD.Console.PrintError(f"Auto PIN detection: cannot read GDS: {e}\n")
        return 0

    scale     = (lib.unit * 1000.0) if getattr(lib, "unit", None) else 0.001
    top_cells = lib.top_level() or lib.cells

    # ── ray-casting point-in-polygon (GDS units, avoids mm conversion) ───────
    def _contains(poly_pts, px, py):
        n       = len(poly_pts)
        inside  = False
        j       = n - 1
        for i in range(n):
            xi, yi = float(poly_pts[i][0]), float(poly_pts[i][1])
            xj, yj = float(poly_pts[j][0]), float(poly_pts[j][1])
            if ((yi > py) != (yj > py)) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside

    # Minimum pad side in mm — keeps bond pads, drops routing wires
    _MIN_PAD_MM = 0.010   # 10 µm

    # ── stack info for Z position / thickness ─────────────────────────────────
    stack_mm = build_stack_mm(
        [{"layer_id": lid, "datatype": dt, "name": name} for lid, dt, name in candidates],
        ihp_map,
    )

    # ── helper: top-face centre ────────────────────────────────────────────────
    def _top_face_center(shape):
        if shape.Faces:
            return Base.Vector(
                max(shape.Faces, key=lambda f: f.CenterOfMass.z).CenterOfMass
            )
        bb = shape.BoundBox
        return Base.Vector(
            (bb.XMin + bb.XMax) / 2.0,
            (bb.YMin + bb.YMax) / 2.0,
            bb.ZMax,
        )

    _ORANGE = (0.90, 0.30, 0.10)   # die-side orange — matches ContactPointTool

    # ── create FreeCAD objects ────────────────────────────────────────────────
    doc.openTransaction("Auto PIN Contact Points")
    try:
        existing = sum(1 for o in doc.Objects if o.Name.startswith("ContactPoint_"))
        cp_idx   = existing + 1
        cp_count = 0

        for lid, dt, _ in candidates:
            edi_name = _resolve_edi_name(lid, dt, ihp_map, selected_layers)
            s_info   = stack_mm.get((lid, dt), {"t_mm": 0.001, "z0_mm": 0.0})
            t_mm     = max(float(s_info["t_mm"]), 1e-4)
            z0_mm    = float(s_info["z0_mm"])

            # pad_pairs: list of (pad_pts_in_gds_units, contact_xy_mm)
            pad_pairs = []

            for cell in top_cells:
                if dt == 2:
                    # ── DT=2 PIN marker strategy ──────────────────────────────
                    # Contact location  = centroid of DT=2 marker (accurate)
                    # Pad geometry      = DT=0 polygon containing that centroid
                    pin_polys  = _get_layer_polygons(cell, lid, 2)
                    draw_polys = _get_layer_polygons(cell, lid, 0)

                    # Pre-build pad index: only keep pad-sized DT=0 polygons
                    pad_index = []   # [(pts_raw, (xlo,ylo,xhi,yhi))]
                    for p0 in draw_polys:
                        pts = p0.points
                        xs  = [float(p[0]) for p in pts]
                        ys  = [float(p[1]) for p in pts]
                        w   = (max(xs) - min(xs)) * scale
                        h   = (max(ys) - min(ys)) * scale
                        if w >= _MIN_PAD_MM and h >= _MIN_PAD_MM:
                            pad_index.append((pts, (min(xs), min(ys), max(xs), max(ys))))

                    used_pads = set()
                    for pp in pin_polys:
                        pts2  = pp.points
                        cx_g  = sum(float(p[0]) for p in pts2) / len(pts2)
                        cy_g  = sum(float(p[1]) for p in pts2) / len(pts2)
                        cx_mm = cx_g * scale
                        cy_mm = cy_g * scale

                        pad_found = None
                        for idx, (pts_raw, (xlo, ylo, xhi, yhi)) in enumerate(pad_index):
                            if idx in used_pads:
                                continue
                            # fast bbox pre-check
                            if cx_g < xlo or cx_g > xhi or cy_g < ylo or cy_g > yhi:
                                continue
                            if _contains(pts_raw, cx_g, cy_g):
                                pad_found = idx
                                break

                        if pad_found is not None:
                            used_pads.add(pad_found)
                            pts_pad, (xlo, ylo, xhi, yhi) = pad_index[pad_found]
                            # Use center of DT=0 pad polygon, not the DT=2 marker position.
                            # DT=2 markers are often placed at the edge/corner of a pad,
                            # so using the pad's bounding-box centre gives "middle of pad".
                            pad_cx_mm = ((xlo + xhi) / 2.0) * scale
                            pad_cy_mm = ((ylo + yhi) / 2.0) * scale
                            pad_pairs.append((pts_pad, (pad_cx_mm, pad_cy_mm)))
                        else:
                            # No large DT=0 pad found — fall back to DT=2 shape itself
                            pad_pairs.append((pts2, (cx_mm, cy_mm)))

                else:
                    # ── DT=0 drawing layer strategy (S3 fallback) ─────────────
                    # Filter to pad-sized polygons; use polygon centroid for CP.
                    draw_polys = _get_layer_polygons(cell, lid, 0)
                    for p0 in draw_polys:
                        pts = p0.points
                        xs  = [float(p[0]) for p in pts]
                        ys  = [float(p[1]) for p in pts]
                        w   = (max(xs) - min(xs)) * scale
                        h   = (max(ys) - min(ys)) * scale
                        if w >= _MIN_PAD_MM and h >= _MIN_PAD_MM:
                            cx_mm = ((min(xs) + max(xs)) / 2.0) * scale
                            cy_mm = ((min(ys) + max(ys)) / 2.0) * scale
                            pad_pairs.append((pts, (cx_mm, cy_mm)))

            if not pad_pairs:
                FreeCAD.Console.PrintWarning(
                    f"  No pads found for {edi_name} ({lid}/{dt}).\n"
                )
                continue

            FreeCAD.Console.PrintMessage(
                f"  ({lid}/{dt}) {edi_name}: {len(pad_pairs)} pad(s)\n"
            )

            # ── extrude pad polygons → solids ─────────────────────────────────
            solids   = []
            contacts = []   # Base.Vector snap points (one per pad)

            for pts_raw, (cx_mm, cy_mm) in pad_pairs:
                pts2d = [(float(p[0]) * scale, float(p[1]) * scale) for p in pts_raw]
                if len(pts2d) < 3:
                    continue
                try:
                    wire  = Part.makePolygon(
                        [(x, y, z0_mm) for (x, y) in (pts2d + [pts2d[0]])]
                    )
                    face  = Part.Face(wire)
                    solid = face.extrude(FreeCAD.Vector(0, 0, t_mm))
                    solids.append(solid)
                    contacts.append(Base.Vector(cx_mm, cy_mm, z0_mm + t_mm))
                except Exception:
                    continue

            if not solids:
                FreeCAD.Console.PrintWarning(
                    f"  No valid solids for {edi_name} ({lid}/{dt}).\n"
                )
                continue

            compound = Part.makeCompound(solids) if len(solids) > 1 else solids[0]

            # one display object for the whole PIN layer
            pad_grp = doc.addObject("Part::Feature", f"GDS_PINs_{edi_name}")
            pad_grp.Shape = compound
            pad_grp.ViewObject.ShapeColor   = _ORANGE
            pad_grp.ViewObject.Transparency = 0
            pad_grp.addProperty("App::PropertyBool",   "IsGDSPin", "GDS",
                                 "Auto-detected PIN pad layer")
            pad_grp.addProperty("App::PropertyString", "EDIName",  "GDS",
                                 "EDI layer name")
            pad_grp.IsGDSPin = True
            pad_grp.EDIName  = edi_name

            # one ContactPoint marker per pad (at DT=2 centroid or poly centroid)
            for snap_pt in contacts:
                marker = doc.addObject("Part::Feature", f"ContactPoint_{cp_idx:03d}")
                marker.Shape = Part.Vertex(snap_pt.x, snap_pt.y, snap_pt.z)

                marker.addProperty("App::PropertyVector", "ContactPoint", "Wirebond",
                                    "Snap point for wire bonding")
                marker.addProperty("App::PropertyString", "SourceObject",  "Wirebond",
                                    "Source GDS pad object")
                marker.addProperty("App::PropertyBool",   "IsContactPoint", "Wirebond",
                                    "Wire-bond contact point marker")

                marker.ContactPoint   = snap_pt
                marker.SourceObject   = pad_grp.Name
                marker.IsContactPoint = True

                marker.ViewObject.PointSize   = 8
                marker.ViewObject.PointColor  = _ORANGE
                marker.ViewObject.DisplayMode = "Points"

                cp_idx  += 1
                cp_count += 1

        doc.commitTransaction()
        FreeCAD.Console.PrintMessage(
            f"Auto PIN detection: created {cp_count} contact point(s) "
            f"across {len(candidates)} layer(s).\n"
        )
        return cp_count

    except Exception as e:
        doc.abortTransaction()
        FreeCAD.Console.PrintError(f"Auto PIN detection failed: {e}\n")
        import traceback
        FreeCAD.Console.PrintError(traceback.format_exc())
        return 0


# -----------------------------------------
# Layer on Leadframe Configuration Support
# .........................................

def bbox_from_entries(entries):
    bb = None
    for entry in (entries or []):
        try:
            b = entry["shape"].BoundBox
        except Exception:
            continue
        if bb is None:
            bb = [b.XMin, b.YMin, b.XMax, b.YMax]
        else:
            bb = [min(bb[0], b.XMin), min(bb[1], b.YMin), max(bb[2], b.XMax), max(bb[3], b.YMax)]
    return tuple(bb) if bb else None