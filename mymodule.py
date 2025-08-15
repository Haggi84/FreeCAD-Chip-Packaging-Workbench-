import xml.etree.ElementTree as ET
import math
import os
import gdstk
import FreeCAD
from FreeCAD import Part


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
        FreeCAD.Console.PrintWarning("IHP MAP file not found. Technology table and material styles may be incomplete.\n")
        return mapping

    try:
        with open(map_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                # Skip comments / blanks
                if not line or line.startswith("#"):
                    continue

                # Expected tokens, separated by whitespace or tabs:
                # <EDI_NAME> <EDI_TYPES_csv> <GDS_LAYER> <GDS_DATATYPE>
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
                entry["edi_name"] = edi_name  # last one wins (usually identical)
                entry["edi_types"].update(types)
                mapping[key] = entry

        return mapping
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to parse MAP file '{map_path}': {e}\n")
        return {}


# ------------------------------------------------
# GDS inspection & geometry creation helpers
# ------------------------------------------------
def get_gds_layer(gds_path):
    """
    Analyze GDS and return set of (layer_id, datatype) that contain polygons.
    """
    try:
        lib = gdstk.read_gds(gds_path)
        layer_set = set()
        for cell in lib.cells:
            for polygon in cell.polygons:
                layer_set.add((polygon.layer, polygon.datatype))
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


def load_gds_fast(
    gds_path,
    selected_layers,
    transform=None,
    *,
    compound_per_layer=True,
    preview_2d=False,
    min_area_mm2=0.0,
    decimate_tol_mm=0.0,
    skip_fill_datatype=True
):
    """
    Faster GDS loader:
    - builds ONE compound Part shape per selected (layer,datatype)
    - optional 2D preview (wires only) or thin extrusion
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
        z_thickness = float(transform.get("z_thickness", 0.03))  # 30 µm default

        wanted = {(l.get("layer_id", 0), l.get("datatype", 0)) for l in selected_layers}
        by_layer = {key: [] for key in wanted}

        # collect wires/faces per layer
        for cell in lib.cells:
            for poly in cell.polygons:
                key = (poly.layer, poly.datatype)
                if key not in wanted:
                    continue
                if skip_fill_datatype and poly.datatype == 22:
                    # IHP: datatype 22 is FILL on many metals -> skip by default
                    continue

                pts2d = [_transform_point(p, s, rot_deg, mirror_y, tx, ty) for p in poly.points]
                if decimate_tol_mm > 0.0:
                    pts2d = _simplify_poly(pts2d, decimate_tol_mm)
                if len(pts2d) < 3:
                    continue
                if min_area_mm2 > 0.0 and _polygon_area_mm2(pts2d) < min_area_mm2:
                    continue

                # build wire/face
                wire = Part.makePolygon([(x, y, 0.0) for (x, y) in (pts2d + [pts2d[0]])])
                if preview_2d:
                    by_layer[key].append(wire)
                else:
                    try:
                        face = Part.Face(wire)
                    except Exception:
                        # OCC can fail on degenerate wires; skip safely
                        continue
                    if z_thickness > 0:
                        shp = face.extrude(FreeCAD.Vector(0, 0, z_thickness))
                    else:
                        shp = face
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
        return results
    except Exception as e:
        FreeCAD.Console.PrintError(f"Error loading GDS (fast) {gds_path}: {e}\n")
        return []
