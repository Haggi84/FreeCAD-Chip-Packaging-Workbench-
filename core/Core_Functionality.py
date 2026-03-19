import xml.etree.ElementTree as ET
import math
import gdstk
import FreeCAD
import Part
from FreeCAD import Base

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
        # collinearity check via cross-product magnitude
        cross = abs((x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0))
        if cross < eps:
            continue
        out.append((x1, y1))
    out.append(points[-1])
    return out if len(out) >= 3 else points

def load_gds(gds_path,
             selected_layers,
             transform=None,
             preview_2d=False,
             compound_per_layer=True,
             min_area_mm2=0.0,
             decimate_tol_mm=0.0,
             skip_fill_datatype=True,
             stack_mm=None, # NEW: dict(layer_name, layer_datatype) -> {'t_mm', 'z0_mm'} for 3D stacking
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

        # optional progress tracking based on fully-instantiated polygons
        progress_total = None
        if progress_callback:
            progress_total = 0
            for layer, datatype, _ in polygons:
                key = (layer, datatype)
                if key not in wanted:
                    continue
                if skip_fill_datatype and datatype == 22:
                    continue
                progress_total += 1
            progress_total = max(progress_total, 1)
            progress_callback(0, progress_total, "Importing GDS layers...")

        # collect wires/faces per layer
        progress_count = 0
        for layer, datatype, poly_pts in polygons:
            poly_pts = _points_array(poly_pts)
            key = (layer, datatype)
            if key not in wanted:
                continue
            if skip_fill_datatype and datatype == 22:
                # many PDKs: datatype 22 == FILL
                continue

            pts2d = [_transform_point(p, s, rot_deg, mirror_y, tx, ty) for p in poly_pts]
            if decimate_tol_mm > 0.0:
                pts2d = _simplify_poly(pts2d, decimate_tol_mm)
            if len(pts2d) < 3:
                continue
            if min_area_mm2 > 0.0 and _polygon_area_mm2(pts2d) < min_area_mm2:
                continue

            progress_count += 1
            if progress_callback:
                message = f"Importing layer {layer}/{datatype} ({progress_count}/{progress_total})"
                if progress_callback(progress_count, progress_total, message) is False:
                    return []

            # build wire/face
            wire = Part.makePolygon([(x, y, 0.0) for (x, y) in (pts2d + [pts2d[0]])])
            if preview_2d:
                by_layer[key].append(wire)
            else:
                try:
                    face = Part.Face(wire)
                except Exception:
                    continue
                # pick thickness & offset for this layer
                if stack_mm and key in stack_mm:
                    t_mm = float(stack_mm[key]["t_mm"])
                    z0 = float(stack_mm[key]["z0_mm"])
                else:
                    t_mm = default_t_mm
                    z0 = 0.0
                shp = face.extrude(FreeCAD.Vector(0, 0, t_mm))
                # translate to its bottom Z
                if z0 != 0.0:
                    shp.translate(FreeCAD.Vector(0, 0, z0))
                by_layer[key].append(shp)

        # one compound per layer
        results = []
        for layer in selected_layers:
            lid = layer.get("layer_id", 0)
            dt = layer.get("datatype", 0)
            parts = by_layer.get((lid, dt), [])
            if not parts:
                continue
            compound = Part.makeCompound(parts) if compound_per_layer and len(parts) > 1 else parts[0]
            results.append({
                "shape": compound,
                "layer_id": lid,
                "datatype": dt,
                "frame_hex": layer.get("frame-color", "#000000"),
                "fill_hex": layer.get("fill-color", "#FFFFFF"),
            })

        if progress_callback and progress_total is not None:
            progress_callback(progress_total, progress_total, "Finalizing GDS shapes...")
        return results

    except Exception as e:
        FreeCAD.Console.PrintError(f"Error loading GDS file {gds_path}: {str(e)}\n")
        return []
    

def is_bondable(types: set) -> bool:
    if not types:
        return False
    T = {t.upper() for t in types}
    return any(t in T for t in ("PIN", "LEFPIN", "BUMP", "PAD"))

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
                            pad_pairs.append((pad_index[pad_found][0], (cx_mm, cy_mm)))
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