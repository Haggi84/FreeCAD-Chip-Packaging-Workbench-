import xml.etree.ElementTree as ET
import math
import os
import gdstk
import FreeCAD
from FreeCAD import Part


# --- add this helper near the top of mymodule.py (after imports) ---

def _polygons_by_spec_compat(cell):
    """
    Return dict {(layer, datatype): [ndarray Nx2, ...]} for a gdstk Cell.
    Tries multiple gdstk API signatures; falls back to manual flatten + paths.
    """
    # 1) Newer gdstk: keyword args by_spec/include_paths
    try:
        res = cell.get_polygons(by_spec=True, include_paths=True)
        if isinstance(res, dict):
            return { (int(k[0]), int(k[1])): v for k, v in res.items() }
    except TypeError:
        pass
    except Exception:
        pass

    # 2) Older gdstk: positional args (by_spec, depth, include_paths)
    try:
        res = cell.get_polygons(True, None, True)
        if isinstance(res, dict):
            return { (int(k[0]), int(k[1])): v for k, v in res.items() }
    except TypeError:
        pass
    except Exception:
        pass

    # 3) Oldest fallback: copy, flatten, then harvest polygons + paths ourselves
    try:
        tmp = cell.copy(name=f"{cell.name}_tmp_flat")
    except Exception:
        tmp = cell
    try:
        tmp.flatten()  # apply SREF/AREF transforms
    except Exception:
        pass

    out = {}
    # polygons
    for p in getattr(tmp, "polygons", []) or []:
        try:
            key = (int(getattr(p, "layer", 0)), int(getattr(p, "datatype", 0)))
            out.setdefault(key, []).append(p.points)
        except Exception:
            continue
    # paths -> polygons
    for path in getattr(tmp, "paths", []) or []:
        try:
            arrs = path.to_polygons()
        except Exception:
            arrs = []
        try:
            lyr = int(getattr(path, "layer", 0))
            dt  = int(getattr(path, "datatype", 0))
        except Exception:
            lyr, dt = 0, 0
        for arr in arrs:
            out.setdefault((lyr, dt), []).append(arr)

    return out


# -------------------------------
# KLayout LYP parsing (colors)
# -------------------------------
def parse_lyp(lyp_path):
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
                    FreeCAD.Console.PrintWarning(f"Invalid source format in layer {name}: {source}\n")
                    continue

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
def parse_ihp_map(map_path):
    """
    Parse IHP *.map file and return dict keyed by (gds_layer, gds_datatype) -> {
        'edi_name': <str>,         # e.g. 'TopMetal2'
        'edi_types': set[str]      # e.g. {'PIN','LEFPIN'} or {'FILL'} ...
    }

    The map file can contain multiple lines mapping the same (layer,datatype) with
    different types. We merge them into a set for easier use.
    """
    mapping = {}
    if not map_path or not os.path.exists(map_path):
        FreeCAD.Console.PrintWarning("IHP MAP file not found. Technology table and stacking may be incomplete.\n")
        return mapping

    try:
        with open(map_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue

                parts = [p for p in line.split() if p]
                if len(parts) < 4:
                    continue

                edi_name = parts[0]
                edi_types_csv = parts[1]
                try:
                    gds_layer = int(parts[2])
                    gds_datatype = int(parts[3])
                except ValueError:
                    continue

                key = (gds_layer, gds_datatype)
                types = set([t.strip().upper() for t in edi_types_csv.split(",") if t.strip()])
                entry = mapping.get(key, {"edi_name": edi_name, "edi_types": set()})
                entry["edi_name"] = edi_name
                entry["edi_types"].update(types)
                mapping[key] = entry
        return mapping
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to parse MAP file '{map_path}': {e}\n")
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


# ------------------------------------------------
# GDS inspection & geometry creation helpers
# ------------------------------------------------
# --- replace your get_gds_layer(...) with this ---

def get_gds_layer(gds_path):
    """
    Analyze GDS and return set of (layer_id, datatype) that contain geometry.
    Uses top-level flattening and includes PATHs; works across gdstk versions.
    """
    try:
        lib = gdstk.read_gds(gds_path)
        specs = set()
        tops = lib.top_level() or list(lib.cells)
        for top in tops:
            poly_dict = _polygons_by_spec_compat(top)
            for (layer, datatype), polys in (poly_dict or {}).items():
                if polys:
                    specs.add((int(layer), int(datatype)))
        return specs
    except Exception as e:
        FreeCAD.Console.PrintError(f"Error reading GDSII file {gds_path}: {e}\n")
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
        # collinearity check via cross-product magnitude
        cross = abs((x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0))
        if cross < eps:
            continue
        out.append((x1, y1))
    out.append(points[-1])
    return out if len(out) >= 3 else points


# --- replace your load_gds_fast(...) with this ---

def load_gds_fast(
    gds_path,
    selected_layers,
    transform=None,
    *,
    compound_per_layer=True,
    preview_2d=False,
    min_area_mm2=0.0,
    decimate_tol_mm=0.0,
    skip_fill_datatype=True,
    stack_mm=None  # (layer,dt)->{'t_mm','z0_mm'}
):
    """
    Versions-sicherer GDS-Loader mit korrekter Platzierung:
    - Flatten aus Top-Cells (SREF/AREF angewendet)
    - PATHS werden in Polygone umgewandelt
    - Optional 2D (Wires) oder 3D-Extrusion mit Z-Stack
    """
    try:
        lib = gdstk.read_gds(gds_path)
        if transform is None:
            transform = {}

        # mm per user unit
        s = transform.get("scale", None)
        if s is None:
            s = (lib.unit * 1000.0) if hasattr(lib, "unit") and lib.unit else 0.001

        rot_deg  = float(transform.get("rot_deg", 0.0))
        mirror_y = bool(transform.get("mirror_y", False))
        tx       = float(transform.get("tx", 0.0))
        ty       = float(transform.get("ty", 0.0))

        default_t_mm = float(transform.get("z_thickness", 0.03)) if not preview_2d else 0.0

        wanted   = {(l.get("layer_id", 0), l.get("datatype", 0)) for l in selected_layers}
        by_layer = {key: [] for key in wanted}

        tops = lib.top_level() or list(lib.cells)
        for top in tops:
            poly_dict = _polygons_by_spec_compat(top)  # <-- robust per-version API
            if not poly_dict:
                continue

            for (layer_id, datatype), poly_list in poly_dict.items():
                key = (int(layer_id), int(datatype))
                if key not in wanted:
                    continue
                if skip_fill_datatype and key[1] == 22:
                    continue

                for arr in poly_list:
                    pts2d = [_transform_point((float(x), float(y)), s, rot_deg, mirror_y, tx, ty)
                             for (x, y) in arr.tolist()]
                    if decimate_tol_mm > 0.0:
                        pts2d = _simplify_poly(pts2d, decimate_tol_mm)
                    if len(pts2d) < 3:
                        continue
                    if min_area_mm2 > 0.0 and _polygon_area_mm2(pts2d) < min_area_mm2:
                        continue

                    wire = Part.makePolygon([(x, y, 0.0) for (x, y) in (pts2d + [pts2d[0]])])
                    if preview_2d:
                        by_layer[key].append(wire)
                    else:
                        try:
                            face = Part.Face(wire)
                        except Exception:
                            continue
                        if stack_mm and key in stack_mm:
                            t_mm = float(stack_mm[key]["t_mm"])
                            z0   = float(stack_mm[key]["z0_mm"])
                        else:
                            t_mm = default_t_mm
                            z0   = 0.0
                        shp = face.extrude(FreeCAD.Vector(0, 0, t_mm))
                        if z0:
                            shp.translate(FreeCAD.Vector(0, 0, z0))
                        by_layer[key].append(shp)

        results = []
        for layer in selected_layers:
            lid = layer.get("layer_id", 0)
            dt  = layer.get("datatype", 0)
            parts = by_layer.get((lid, dt), [])
            if not parts:
                continue
            compound = Part.makeCompound(parts) if compound_per_layer and len(parts) > 1 else parts[0]
            results.append({
                "shape": compound,
                "layer_id": lid,
                "datatype": dt,
                "frame_hex": layer.get("frame-color", "#000000"),
                "fill_hex":  layer.get("fill-color", "#FFFFFF"),
            })
        return results

    except Exception as e:
        FreeCAD.Console.PrintError(f"Error loading GDS (fast) {gds_path}: {e}\n")
        return []
