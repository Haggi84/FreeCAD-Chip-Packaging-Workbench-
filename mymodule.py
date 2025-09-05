import xml.etree.ElementTree as ET
import math
import gdstk
import FreeCAD, Part
from FreeCAD import Base

ILD_SPACING_UM = 10.0

def _as_iter(obj):
    if obj is None:
        return []
    if isinstance(obj, (list, tuple)):
        return obj
    return [obj]

def _iter_xy(seq):
    """Yield (x,y) pairs from either a numpy Nx2 array, a list of pairs, or a gdstk.Polygon."""
    pts = getattr(seq, 'points', seq)
    try:
        for p in pts:
            try:
                x = float(p[0]); y = float(p[1])
            except Exception:
                continue
            yield x, y
    except TypeError:
        return

def parse_lyp(lyp_path, layer_map=None):
    try:
        tree = ET.parse(lyp_path)
        root = tree.getroot()
        layers = []
        unique_colors = set()
        for prop in root.findall('.//properties'):
            d = {c.tag: (c.text if c.text else None) for c in prop}
            source = d.get('source')
            visible = d.get('visible', 'false') == 'true'
            if not (visible and source):
                continue
            try:
                lid, dt = map(int, source.split('/'))
            except Exception:
                FreeCAD.Console.PrintWarning(f"Invalid source in LYP: {source}\n"); continue
            d['layer_id'] = lid; d['datatype'] = dt
            layers.append(d)
            unique_colors.add((d.get('frame-color', '#000000'), d.get('fill-color', '#FFFFFF')))
        return layers, unique_colors
    except FileNotFoundError:
        FreeCAD.Console.PrintError(f"LYP not found: {lyp_path}\n"); return [], set()
    except ET.ParseError as e:
        FreeCAD.Console.PrintError(f"LYP parse error: {e}\n"); return [], set()
    except Exception as e:
        FreeCAD.Console.PrintError(f"LYP error: {e}\n"); return [], set()

def parse_map(map_path):
    tech = {}
    if not map_path:
        return tech
    try:
        with open(map_path, 'r', encoding='utf-8', errors='ignore') as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#'): continue
                low = line.lower()
                if any(k in low for k in ('====','copyright','license','edi stream','version')): continue
                parts = [p for p in line.replace(',', ' ').split() if p]
                if len(parts) < 3: continue
                try:
                    lid = int(parts[0]); dt = int(parts[1])
                except Exception:
                    continue
                edi = parts[2]
                types = set(p.upper() for p in parts[3:])
                key = (lid, dt)
                rec = tech.setdefault(key, {'edi_name': edi, 'edi_types': set()})
                rec['edi_name'] = edi
                rec['edi_types'].update(types)
    except FileNotFoundError:
        FreeCAD.Console.PrintWarning(f"MAP not found: {map_path}\n"); return {}
    except Exception as e:
        FreeCAD.Console.PrintError(f"MAP error: {e}\n"); return {}
    return tech

parse_ihp_map = parse_map

def _mm_per_db_unit(lib: gdstk.Library) -> float:
    unit_m = getattr(lib, 'unit', 1e-6)
    return unit_m * 1000.0

def derive_base_scale_mm(gds_path: str) -> float:
    try:
        lib = gdstk.read_gds(gds_path)
        return _mm_per_db_unit(lib)
    except Exception:
        return 0.001

def get_gds_layer(gds_path: str):
    try:
        lib = gdstk.read_gds(gds_path)
        seen = set()
        for cell in _as_iter(getattr(lib, 'cells', [])):
            for poly in _as_iter(getattr(cell, 'polygons', [])):
                L = getattr(poly, 'layer', 0); D = getattr(poly, 'datatype', 0)
                seen.add((L, D))
            for path in _as_iter(getattr(cell, 'paths', [])):
                if hasattr(path, 'layers') and getattr(path, 'layers'):
                    dts = getattr(path, 'datatypes', None)
                    for i, L in enumerate(path.layers):
                        D = dts[i] if (dts and i < len(dts)) else 0
                        seen.add((L, D))
                else:
                    L = getattr(path, 'layer', 0); D = getattr(path, 'datatype', 0)
                    seen.add((L, D))
        return seen
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to read GDS layers: {e}\n"); return set()

def is_bondable(types):
    if not types: return False
    T = {t.upper() for t in types}
    return any(t in T for t in ("PIN","LEFPIN","BUMP","PAD"))

def style_for_material(edi_name: str, edi_types: set):
    en = (edi_name or '').upper()
    et = {t.upper() for t in (edi_types or set())}
    if is_bondable(et):
        return ("Bondable metal", (0.90,0.75,0.20), (0.25,0.20,0.10), 0)
    if "VIA" in et and "FILL" not in et:
        return ("Via metal", (0.35,0.35,0.35), (0.08,0.08,0.08), 0)
    if "FILL" in et:
        return ("Metal fill / dielectric", (0.70,0.85,1.0), (0.25,0.35,0.45), 70)
    if en.startswith("TOPMETAL") or en.startswith("METAL") or "METAL" in et:
        return ("Routing metal", (0.60,0.60,0.60), (0.12,0.12,0.12), 0)
    if en.startswith("COMP") or en.startswith("DIEAREA") or "DIE" in et:
        return ("Component/Die", (0.80,0.90,0.95), (0.25,0.35,0.45), 60)
    return ("Generic", (0.75,0.75,0.75), (0.10,0.10,0.10), 0)

def build_stack_mm(selected_layers, ihp_map, ild_um: float = ILD_SPACING_UM):
    order = sorted(selected_layers, key=lambda L: (L.get('layer_id',0), L.get('datatype',0)))
    z=0.0; out={}
    for L in order:
        lid=L.get('layer_id',0); dt=L.get('datatype',0)
        entry = ihp_map.get((lid,dt), {})
        types = entry.get('edi_types', set())
        name  = (entry.get('edi_name') or L.get('name','')).upper()
        if 'FILL' in types: th=0.01
        elif 'VIA' in types: th=0.02
        elif any(s in types for s in ('PIN','BUMP','PAD')) or 'METAL' in name: th=0.03
        else: th=0.02
        out[(lid,dt)] = (z, th)
        z += th + (ild_um/1000.0)
    return out

def _apply_transform_xy(x, y, scale, rot_deg, mirror_y, tx, ty):
    x*=scale; y*=scale
    if mirror_y: y=-y
    if rot_deg:
        r = math.radians(rot_deg)
        x, y = (x*math.cos(r)-y*math.sin(r), x*math.sin(r)+y*math.cos(r))
    return x+tx, y+ty

def _polygon_to_face(points2d):
    pts = [Base.Vector(px, py, 0) for (px,py) in points2d]
    if not pts:
        return None
    if pts[0].x != pts[-1].x or pts[0].y != pts[-1].y:
        pts.append(Base.Vector(pts[0].x, pts[0].y, 0))
    wire = Part.makePolygon(pts)
    try:
        return Part.Face(wire)
    except Exception:
        return wire

def bbox_from_entries(entries):
    bb=None
    for e in entries or []:
        try: b=e['shape'].BoundBox
        except Exception: continue
        if bb is None: bb=[b.XMin,b.YMin,b.XMax,b.YMax]
        else: bb=[min(bb[0],b.XMin),min(bb[1],b.YMin),max(bb[2],b.XMax),max(bb[3],b.YMax)]
    return tuple(bb) if bb else None

def load_gds(gds_path, selected_layers, transform=None, preview_2d=True,
             compound_per_layer=True, min_area_mm2=0.0, decimate_tol_mm=0.0,
             skip_fill_datatype=False, stack_mm=None):
    try:
        lib = gdstk.read_gds(gds_path)
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to read GDS: {e}\n"); return []

    mm_per_db = _mm_per_db_unit(lib)
    selected = {(L.get('layer_id',0), L.get('datatype',0)) for L in (selected_layers or [])}

    tr = transform or {}
    scale = tr.get('scale', 1.0) or 1.0
    rot_deg = tr.get('rot_deg', 0.0)
    mirror_y = bool(tr.get('mirror_y', False))
    tx = tr.get('tx', 0.0); ty = tr.get('ty', 0.0)
    z_thickness = float(tr.get('z_thickness', 0.03))

    per_key = {}

    for cell in _as_iter(getattr(lib, 'cells', [])):
        for poly in _as_iter(getattr(cell, 'polygons', [])):
            key = (getattr(poly, 'layer', 0), getattr(poly, 'datatype', 0))
            if selected and key not in selected: continue
            if skip_fill_datatype and key[1] != 0: continue
            pts_mm = []
            for (x, y) in _iter_xy(poly.points):
                xm = x * mm_per_db; ym = y * mm_per_db
                X, Y = _apply_transform_xy(xm, ym, scale, rot_deg, mirror_y, tx, ty)
                pts_mm.append((X, Y))
            face = _polygon_to_face(pts_mm)
            if face is None: continue
            if not preview_2d:
                z0, th = (stack_mm.get(key) if (stack_mm and key in stack_mm) else (0.0, z_thickness))
                try:
                    solid = face.extrude(Base.Vector(0,0,th)); solid.translate(Base.Vector(0,0,z0)); shape = solid
                except Exception:
                    shape = face
            else:
                shape = face
            per_key.setdefault(key, []).append(shape)

        for path in _as_iter(getattr(cell, 'paths', [])):
            if hasattr(path, 'layers') and getattr(path, 'layers'):
                L = path.layers[0]; D = path.datatypes[0] if getattr(path,'datatypes', None) else 0
            else:
                L = getattr(path, 'layer', 0); D = getattr(path, 'datatype', 0)
            key = (L, D)
            if selected and key not in selected: continue
            if skip_fill_datatype and key[1] != 0: continue
            try:
                polys = path.to_polygons()
            except Exception:
                polys = []
            for raw in _as_iter(polys):
                pts_mm = []
                for (x, y) in _iter_xy(raw):
                    xm = x * mm_per_db; ym = y * mm_per_db
                    X, Y = _apply_transform_xy(xm, ym, scale, rot_deg, mirror_y, tx, ty)
                    pts_mm.append((X, Y))
                face = _polygon_to_face(pts_mm)
                if face is None: continue
                if not preview_2d:
                    z0, th = (stack_mm.get(key) if (stack_mm and key in stack_mm) else (0.0, z_thickness))
                    try:
                        solid = face.extrude(Base.Vector(0,0,th)); solid.translate(Base.Vector(0,0,z0)); shape = solid
                    except Exception:
                        shape = face
                else:
                    shape = face
                per_key.setdefault(key, []).append(shape)

    entries = []
    for (lid, dt), shapes in per_key.items():
        try:
            compound = Part.makeCompound(shapes) if (compound_per_layer and len(shapes) > 1) else shapes[0]
        except Exception:
            compound = shapes[0]
        entries.append({"layer_id": lid, "datatype": dt, "shape": compound})
    return entries
