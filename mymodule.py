import xml.etree.ElementTree as ET
import math
import gdstk
import FreeCAD
from FreeCAD import Part


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


def load_gds(gds_path, selected_layers, transform=None):
    """
    Load GDS file and return a list of tuples: (Part.Shape, frame_hex, fill_hex).

    Args:
        gds_path (str)
        selected_layers (list[dict]): each dict with at least layer_id, datatype,
                                      frame-color, fill-color
        transform (dict or None): {
            'scale': None or float (mm per user unit; None => derive from GDS),
            'rot_deg': float,
            'mirror_y': bool,
            'tx': float (mm),
            'ty': float (mm),
            'z_thickness': float (mm)  # optional, default 0.03 mm
        }

    Notes:
        - Coordinates in GDS are in user units; we convert to mm using 'scale'.
        - We extrude to a thin volume so shapes are visible in 3D.
    """
    try:
        lib = gdstk.read_gds(gds_path)
        if transform is None:
            transform = {}

        s = transform.get("scale", None)
        if s is None:
            # mm per user unit
            s = (lib.unit * 1000.0) if hasattr(lib, "unit") and lib.unit else 0.001

        rot_deg = float(transform.get("rot_deg", 0.0))
        mirror_y = bool(transform.get("mirror_y", False))
        tx = float(transform.get("tx", 0.0))
        ty = float(transform.get("ty", 0.0))
        z_thickness = float(transform.get("z_thickness", 0.03))  # 30 µm

        shapes = []
        layer_set = {(layer.get("layer_id", 0), layer.get("datatype", 0)) for layer in selected_layers}

        for cell in lib.cells:
            for polygon in cell.polygons:
                if (polygon.layer, polygon.datatype) in layer_set:
                    pts2d = [_transform_point(p, s, rot_deg, mirror_y, tx, ty) for p in polygon.points]
                    if len(pts2d) > 2:
                        wire = Part.makePolygon([(x, y, 0.0) for (x, y) in pts2d])
                        # Close wire if needed
                        if not wire.isClosed():
                            wire = Part.makePolygon([(x, y, 0.0) for (x, y) in pts2d] + [(*pts2d[0], 0.0)])
                        face = Part.Face(wire)
                        extrude_shape = face.extrude(FreeCAD.Vector(0, 0, z_thickness))
                        # color mapping via selected layer tuple (frame/fill)
                        for layer in selected_layers:
                            if (layer.get("layer_id", 0), layer.get("datatype", 0)) == (polygon.layer, polygon.datatype):
                                frame_color = layer.get("frame-color", "#000000")
                                fill_color = layer.get("fill-color", "#FFFFFF")
                                shapes.append((extrude_shape, frame_color, fill_color))
                                break

        return shapes

    except Exception as e:
        FreeCAD.Console.PrintError(f"Error loading GDS file {gds_path}: {str(e)}\n")
        return []
