from PySide2 import QtWidgets
import os, FreeCAD, FreeCADGui, importlib
import mymodule, LeadframeCommand
from Color import hex_to_rgb

# ---- Lazy helpers (avoid circular imports) ----
def _safe_get_loader():
    try:
        G = importlib.import_module('GDSCommand')
        return getattr(G, 'load_gds_layers', None)
    except Exception as e:
        FreeCAD.Console.PrintError(f"Import error (GDSCommand): {e}\n"); return None

def _get_LayeronLeadframeConfigurator():
    try:
        AC = importlib.import_module('All_Class')
        return getattr(AC, 'LayeronLeadframeConfigurator', None)
    except Exception as e:
        FreeCAD.Console.PrintError(f"Import error (All_Class.LayeronLeadframeConfigurator): {e}\n"); return None

# ---- Compatibility shim: some code imports `configuration` from this module ----
class configuration:
    """Callable proxy that instantiates All_Class.LayeronLeadframeConfigurator lazily.
    Usage stays compatible with: dlg = configuration(); dlg.exec_(); dlg.get_opts()
    """
    def __new__(cls, *args, **kwargs):
        Cfg = _get_LayeronLeadframeConfigurator()
        if Cfg is not None:
            return Cfg(*args, **kwargs)
        # Minimal fallback dialog (so imports don't crash)
        dlg = QtWidgets.QDialog()
        dlg.setWindowTitle("Transform Options (Fallback)")
        dlg.get_opts = lambda: dict(rot_deg=0.0, mirror_y=False, tx=0.0, ty=0.0, auto_fit=True, margin_pct=5.0)
        dlg.exec_ = lambda: QtWidgets.QDialog.Accepted
        return dlg

def _finalize_import(doc, gds_path, selected_layers, options, ihp_map, cfg, opts):
    measure_transform=dict(scale=1.0, rot_deg=opts['rot_deg'], mirror_y=opts['mirror_y'], tx=0.0, ty=0.0, z_thickness=0.03)
    tmp = mymodule.load_gds(gds_path, selected_layers, transform=measure_transform, preview_2d=True, compound_per_layer=True)
    bb = mymodule.bbox_from_entries(tmp)
    if not bb: return None
    xmin,ymin,xmax,ymax = bb; w=max(0.0,xmax-xmin); h=max(0.0,ymax-ymin)
    scale=1.0
    if options.get('match_klayout', True) is False and w>0 and h>0 and opts.get('auto_fit', True):
        margin=max(0.0,opts.get('margin_pct',0.0))/100.0
        fit_w=cfg['frame_length']*(1.0-margin); fit_h=cfg['frame_width']*(1.0-margin)
        scale=min(fit_w/w, fit_h/h)
    tx=-((xmin+xmax)/2.0) + opts['tx']; ty=-((ymin+ymax)/2.0) + opts['ty']
    tr=dict(scale=scale, rot_deg=opts['rot_deg'], mirror_y=opts['mirror_y'], tx=tx, ty=ty, z_thickness=0.03)

    stack = mymodule.build_stack_mm(selected_layers, ihp_map, ild_um=mymodule.ILD_SPACING_UM)
    entries = mymodule.load_gds(gds_path, selected_layers, transform=tr, preview_2d=False, compound_per_layer=True, skip_fill_datatype=not options.get('match_klayout', True), stack_mm=stack)
    if not entries: return None

    layer_objects = {}
    for L in selected_layers:
        lid=L.get('layer_id',0); dt=L.get('datatype',0); lname=L.get('name','Unnamed')
        m=ihp_map.get((lid,dt),{}); types=m.get('edi_types', set())
        if options.get('match_klayout', True):
            shape_rgb=hex_to_rgb(L.get('fill-color','#FFFFFF')); line_rgb=hex_to_rgb(L.get('frame-color','#000000')); trn=0
            if options.get('highlight_bondable',True) and mymodule.is_bondable(types): shape_rgb=(0.90,0.75,0.20); line_rgb=(0.25,0.20,0.10)
        else:
            _,shape_rgb,line_rgb,trn=mymodule.style_for_material(m.get('edi_name',''), types)
        e=next((e for e in entries if e['layer_id']==lid and e['datatype']==dt), None)
        if not e: continue
        obj=doc.addObject("Part::Feature", f"Layer_{lname}_{lid}_{dt}"); obj.Shape=e['shape']
        obj.ViewObject.ShapeColor=shape_rgb; obj.ViewObject.LineColor=line_rgb; obj.ViewObject.Transparency=trn
        layer_objects.setdefault((lid,dt), []).append(obj)
    return layer_objects

def create_layer_on_leadframe():
    try:
        _loader = _safe_get_loader()
        if _loader is None:
            QtWidgets.QMessageBox.critical(None, "Error", "GDSCommand.load_gds_layers konnte nicht importiert werden (Zirkularimport).");
            return None

        result = _loader()
        if not result or not result[0]: return None
        _preview_doc, _layer_objects, selected_layers, _unique_colors, gds_path, _lyp_path, options = result

        default_map = os.path.join(os.path.dirname(__file__), 'sg13g2.map')
        ihp_map = mymodule.parse_map(default_map) if os.path.exists(default_map) else {}

        # Use the compatibility shim (behaves like the old 'configuration')
        dlg = configuration()
        if dlg.exec_()!=QtWidgets.QDialog.Accepted: return None
        opts = dlg.get_opts() if hasattr(dlg, 'get_opts') else dict(rot_deg=0.0, mirror_y=False, tx=0.0, ty=0.0, auto_fit=True, margin_pct=5.0)

        # Then ask for leadframe config
        cfg = LeadframeCommand.configure_leadframe()
        if not cfg: return None

        doc = FreeCAD.newDocument("Leadframe_Assembly")
        try: doc.openTransaction("Final Import")
        except Exception: pass

        layer_objects = _finalize_import(doc, gds_path, selected_layers, options, ihp_map, cfg, opts)
        if layer_objects is None:
            QtWidgets.QMessageBox.critical(None, "Error", "Failed to import stacked layers."); return None

        try: doc.commitTransaction()
        except Exception: pass

        LeadframeCommand.create_leadframe(cfg, doc, layer_objects)
        doc.recompute()
        FreeCADGui.activeDocument().activeView().viewIsometric(); FreeCADGui.SendMsgToActiveView("ViewFit")
        return doc
    except Exception as e:
        FreeCAD.Console.PrintError(f"❌ An error occurred: {e}\n"); return None

class LayeronLeadframe:
    def GetResources(self):
        icon_path=os.path.join(os.path.dirname(__file__),"resources","icons","Layer on Leadframe.png")
        return {"MenuText":"Layer on Leadframe","ToolTip":"Import GDS on leadframe","Pixmap": icon_path if os.path.exists(icon_path) else ""}
    def Activated(self):
        res=create_layer_on_leadframe()
        if res is None: QtWidgets.QMessageBox.critical(None,"Error","Failed to create layer on leadframe.")
        else: QtWidgets.QMessageBox.information(None,"Success","Layered import + leadframe created.")
    def IsActive(self): return True

FreeCADGui.addCommand('LayeronLeadframe', LayeronLeadframe())
