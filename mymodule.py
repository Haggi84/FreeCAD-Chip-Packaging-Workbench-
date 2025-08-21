import xml.etree.ElementTree as ET
import math
import gdstk
import FreeCAD
import Part
import csv
from FreeCAD import Vector

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
def get_gds_layer(gds_path):
    """
    Analyze GDS and return set of (layer_id, datatype) that contain polygons.
    """
    try:
        lib = gdstk.read_gds(gds_path)
        layer_set = set()

        def process_cell(cell):
            for polygon in cell.polygons:
                layer_set.add((polygon.layer, polygon.datatype))
            for ref in cell.references:
                process_cell(ref.cell)

        for cell in lib.cells:
            process_cell(cell)

        # FreeCAD.Console.PrintMessage(f"Found {len(layer_set)} layers in GDS file {gds_path}\n")
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
             stack_mm=None # NEW: dict(layer_name, layer_datatype) -> {'t_mm', 'z0_mm'} for 3D stacking
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

        # collect wires/faces per layer
        for cell in lib.cells:
            for poly in cell.polygons:
                key = (poly.layer, poly.datatype)
                if key not in wanted:
                    continue
                if skip_fill_datatype and poly.datatype == 22:
                    # many PDKs: datatype 22 == FILL
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
    if en.startswith("TOPMETAL") or en.startswith("METAL"):
        return ("Routing metal", (0.60, 0.60, 0.60), (0.12, 0.12, 0.12), 0)

    if en.startswith("COMP") or en.startswith("DIEAREA"):
        return ("Component/Die", (0.80, 0.90, 0.95), (0.25, 0.35, 0.45), 60)

    return ("Generic", (0.75, 0.75, 0.75), (0.10, 0.10, 0.10), 0)

# -----------------------------------------
# Layer on Leadframe Configuration Support
# .........................................

def bbox_from_entries(entries):
    if not entries:
        return None
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for entry in entries:
        bb = entry["shape"].BoundBox
        xmin = min(xmin, bb.XMin)
        ymin = min(ymin, bb.YMin)
        xmax = max(xmax, bb.XMax)
        ymax = max(ymax, bb.YMax)
    return xmin, ymin, xmax, ymax


# -----------------------------------
# Wire Bonding Configuration Support
# ...................................
def create_bond_wire(start_point, end_point, loop_height=0.5, diameter=0.025, net="Unknown"):
    """Create a 3D bond wire as a BSpline curve with circular cross-section."""
    doc = FreeCAD.activeDocument()
    mid_point = Vector(
        (start_point.x + end_point.x) / 2,
        (start_point.y + end_point.y) / 2,
        max(start_point.z, end_point.z) + loop_height
    )
    spline = Part.BSplineCurve()
    spline.interpolate([start_point, mid_point, end_point])
    edge = spline.toShape()
    wire_shape = Part.makeCircle(diameter / 2, end_point).toShape()
    bond_wire = wire_shape.extrude(edge)
    wire_obj = doc.addObject("Part::Feature", f"BondWire_{len(doc.Objects)}")
    wire_obj.Shape = bond_wire
    wire_obj.ViewObject.ShapeColor = (0.90, 0.75, 0.20)  # Gold color
    wire_obj.ViewObject.LineColor = (0.25, 0.20, 0.10)
    wire_obj.addProperty("App::PropertyString", "Net", "Wirebond", "Net name")
    wire_obj.Net = net
    return wire_obj

def check_design_rules(wires, bond_fingers, config):
    """Check wire bonding design rules."""
    violations = []
    min_spacing = config["min_wire_spacing"]
    min_length = config["min_wire_length"]
    max_length = config["max_wire_length"]
    margin = config["bond_finger_margin"]

    # Wire-to-wire spacing
    for i, wire1 in enumerate(wires):
        for j, wire2 in enumerate(wires[i+1:]):
            dist = wire1.Shape.distToShape(wire2.Shape)[0]
            if dist < min_spacing:
                violations.append(f"Wires {i} and {j} too close: {dist:.3f} mm < {min_spacing} mm")

    # Wire length
    for i, wire in enumerate(wires):
        length = wire.Shape.Length
        if length < min_length:
            violations.append(f"Wire {i} too short: {length:.3f} mm < {min_length} mm")
        if length > max_length:
            violations.append(f"Wire {i} too long: {length:.3f} mm > {max_length} mm")

    # Bond finger margin
    for i, wire in enumerate(wires):
        end_point = wire.Shape.Edges[0].Vertexes[-1].Point
        for finger in bond_fingers:
            if finger.Shape.Faces:
                face = finger.Shape.Faces[0]
                dist = face.distToShape(Part.makePoint(end_point))[0]
                if dist < margin:
                    violations.append(f"Wire {i} too close to bond finger edge: {dist:.3f} mm < {margin} mm")

    return violations

def generate_wire_bonding_table(wires, bond_pads, bond_fingers, filename="wire_bonding_table.csv"):
    """Generate a CSV wire bonding table."""
    doc = FreeCAD.activeDocument()
    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Wire ID", "Die Pad", "Bond Finger", "Length (mm)", "Net"])
        for i, wire in enumerate(wires):
            die_pad = next((pad.Label for pad in bond_pads if wire.Shape.Edges[0].Vertexes[0].Point.distanceToPoint(pad.Shape.CenterOfMass) < 0.001), "Unknown")
            bond_finger = next((finger.Label for finger in bond_fingers if wire.Shape.Edges[0].Vertexes[-1].Point.distanceToPoint(finger.Shape.CenterOfMass) < 0.001), "Unknown")
            length = wire.Shape.Length
            net = getattr(wire, "Net", "Net" + str(i))  # Fallback net name
            writer.writerow([i, die_pad, bond_finger, f"{length:.3f}", net])
