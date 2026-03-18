import FreeCAD
from core import Core_Functionality
from core.Color import hex_to_rgb

def finalize_import(doc, gds_path, selected_layers, options, ihp_map, cfg, opts):
    measure_transform = dict(scale=1.0, rot_deg=opts['rot_deg'], mirror_y=opts['mirror_y'], tx=0.0, ty=0.0, z_thickness=0.03)
    tmp = Core_Functionality.load_gds(gds_path, selected_layers, transform=measure_transform, preview_2d=True, compound_per_layer=True)
    bb = Core_Functionality.bbox_from_entries(tmp)
    if not bb:
        return None
    xmin, ymin, xmax, ymax = bb
    w = max(0.0, xmax - xmin)
    h = max(0.0, ymax - ymin)
    scale = 1.0
    if options.get('match_klayout', True) is False and w > 0 and h > 0 and opts.get('auto_fit', True):
        margin = max(0.0, opts.get('margin_pct', 0.0)) / 100.0
        fit_w = cfg['frame_length'] * (1.0 - margin)
        fit_h = cfg['frame_width'] * (1.0 - margin)
        scale = min(fit_w / w, fit_h / h)
    tx = -((xmin + xmax) / 2.0) + opts['tx']
    ty = -((ymin + ymax) / 2.0) + opts['ty']
    tr = dict(scale=scale, rot_deg=opts['rot_deg'], mirror_y=opts['mirror_y'], tx=tx, ty=ty, z_thickness=0.03)

    stack = Core_Functionality.build_stack_mm(selected_layers, ihp_map, ild_um=Core_Functionality.ILD_SPACING_UM)
    entries = Core_Functionality.load_gds(
        gds_path, selected_layers, transform=tr, preview_2d=False,
        compound_per_layer=True,
        skip_fill_datatype=not options.get('match_klayout', True),
        stack_mm=stack
    )
    if not entries:
        return None

    layer_objects = {}
    for L in selected_layers:
        lid = L.get('layer_id', 0)
        dt = L.get('datatype', 0)
        lname = L.get('name', 'Unnamed')
        m = ihp_map.get((lid, dt), {})
        types = m.get('edi_types', set())
        if options.get('match_klayout', True):
            shape_rgb = hex_to_rgb(L.get('fill-color', '#FFFFFF'))
            line_rgb = hex_to_rgb(L.get('frame-color', '#000000'))
            trn = 0
            if options.get('highlight_bondable', True) and Core_Functionality.is_bondable(types):
                shape_rgb = (0.90, 0.75, 0.20)
                line_rgb = (0.25, 0.20, 0.10)
        else:
            _, shape_rgb, line_rgb, trn = Core_Functionality.style_for_material(m.get('edi_name', ''), types)
        e = next((e for e in entries if e['layer_id'] == lid and e['datatype'] == dt), None)
        if not e:
            continue
        obj = doc.addObject("Part::Feature", f"Layer_{lname}_{lid}_{dt}")
        obj.Shape = e['shape']
        obj.ViewObject.ShapeColor = shape_rgb
        obj.ViewObject.LineColor = line_rgb
        obj.ViewObject.Transparency = trn
        layer_objects.setdefault((lid, dt), []).append(obj)
    return layer_objects
