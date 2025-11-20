<<<<<<< HEAD:LayeronLeadframe.py
from PySide2 import QtWidgets
import os, FreeCAD, FreeCADGui, importlib
import mymodule, LeadframeCommand
from Color import hex_to_rgb
=======
from PySide2 import QtWidgets, QtCore
import os, sys
import FreeCAD, FreeCADGui

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from core import Core_Functionality
from leadframe.LeadframeCommand import create_leadframe, configure_leadframe
from gds.GDSCommand import load_gds_layers
from core.Color import hex_to_rgb
from Get_Path import get_icon
>>>>>>> Refactoring_Layout:leadframe/LayeronLeadframe.py

# ---- Lazy helpers (avoid circular imports) ----
def _safe_get_loader():
    try:
        G = importlib.import_module('GDSCommand')
        return getattr(G, 'load_gds_layers', None)
    except Exception as e:
        FreeCAD.Console.PrintError(f"Import error (GDSCommand): {e}\n"); return None

<<<<<<< HEAD:LayeronLeadframe.py
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
=======
        frame_length = config["frame_length"]
        frame_width  = config["frame_width"]
            
        tmp_entries = Core_Functionality.load_gds(
            gds_path, selected_layers, first_transform,
            preview_2d=False, compound_per_layer=True,
            min_area_mm2=0.0004, decimate_tol_mm=0.002, skip_fill_datatype=True
        )
        if not tmp_entries:
            QtWidgets.QMessageBox.warning(None, "Warning", "No shapes produced during measurement pass.")
            return

        bb = Core_Functionality.bbox_from_entries(tmp_entries)
        if not bb:
            QtWidgets.QMessageBox.warning(None, "Warning", "Failed to compute bounding box for GDS shapes.")
            return
        xmin, ymin, xmax, ymax = bb
        die_w = max(0.0, xmax - xmin)
        die_h = max(0.0, ymax - ymin)

        # Auto-fit scale
        base_scale = Core_Functionality.derive_base_scale_mm(gds_path)
        final_scale = base_scale
        if opts["auto_fit"] and die_w > 0 and die_h > 0:
            margin = max(0.0, opts["margin_pct"]) / 100.0
            fit_w = frame_length * (1.0 - margin)
            fit_h = frame_width * (1.0 - margin)
            fit_factor = min(fit_w / die_w, fit_h / die_h)
            if fit_factor < 1.0:
                final_scale = base_scale * fit_factor
>>>>>>> Refactoring_Layout:leadframe/LayeronLeadframe.py

    stack = mymodule.build_stack_mm(selected_layers, ihp_map, ild_um=mymodule.ILD_SPACING_UM)
    entries = mymodule.load_gds(gds_path, selected_layers, transform=tr, preview_2d=False, compound_per_layer=True, skip_fill_datatype=not options.get('match_klayout', True), stack_mm=stack)
    if not entries: return None

<<<<<<< HEAD:LayeronLeadframe.py
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
=======
        final_transform = {
            "scale": final_scale,
            "rot_deg": opts["rot_deg"],
            "mirror_y": opts["mirror_y"],
            "tx": final_tx,
            "ty": final_ty,
            "z_thickness": 0.03
        }

        # Build per-layer stack in mm (bottom Z + thickness)
        stack = Core_Functionality.build_stack_mm(selected_layers, ihp_map, ild_um=Core_Functionality.ILD_SPACING_UM)

        # 3D Import parameters derived from options
        match_klayout = bool(options.get("match_klayout", True))
        highlight_bondable = bool(options.get("highlight_bondable", True))
        skip_fill = not match_klayout
        min_area = 0.0 if match_klayout else 0.0004
        decimate = 0.0 if match_klayout else 0.002


        shapes = Core_Functionality.load_gds(
            gds_path,
            selected_layers,
            transform=final_transform,
            preview_2d=False,
            compound_per_layer=True,
            min_area_mm2=min_area,
            decimate_tol_mm=decimate,
            skip_fill_datatype=skip_fill,
            stack_mm=stack
        )
        if not shapes:
            QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found for the selected layers (final pass).")
            return

        layer_objects = {}
        for layer in selected_layers:
            layer_id = layer.get("layer_id", 0)
            datatype = layer.get("datatype", 0)
            layer_name = layer.get("name", "Unnamed")

            map_entry = ihp_map.get((layer_id, datatype))
            edi_name  = map_entry["edi_name"] if map_entry else ""
            types     = map_entry["edi_types"] if map_entry else set()

            # color decision
            if match_klayout:
                shape_rgb = hex_to_rgb(layer.get("fill-color", "#FFFFFF"))
                line_rgb  = hex_to_rgb(layer.get("frame-color", "#000000"))
                tr = 0
                if highlight_bondable and Core_Functionality.is_bondable(types):
                    shape_rgb = (0.90, 0.75, 0.20)
                    line_rgb  = (0.25, 0.20, 0.10)
                    tr = 0
            else:
                _, shape_rgb, line_rgb, tr = Core_Functionality.style_for_material(edi_name, types)
                if not highlight_bondable and Core_Functionality.is_bondable(types):
                    # neutralize highlight
                    shape_rgb = hex_to_rgb(layer.get("fill-color", "#FFFFFF"))
                    line_rgb  = hex_to_rgb(layer.get("frame-color", "#000000"))
                    tr = 0

            shape = next((s for s in shapes if s["layer_id"] == layer_id and s["datatype"] == datatype), None)
            if not shape:
                continue

            obj = doc.addObject("Part::Feature", f"Layer_{layer_name}_{layer_id}")
            obj.Shape = shape["shape"]
            obj.ViewObject.ShapeColor = shape_rgb
            obj.ViewObject.LineColor  = line_rgb
            obj.ViewObject.Transparency = tr

            if not hasattr(obj, "Bondable"):
                obj.addProperty("App::PropertyBool", "Bondable", "Technology", "Can be connected to the leadframe")
            obj.Bondable = Core_Functionality.is_bondable(types) if highlight_bondable else False

            layer_objects.setdefault((layer_id,datatype), []).append(obj)

        # Create the leadframe geometry in the same doc
        create_leadframe(config, doc, layer_objects)

        return doc, layer_objects

def create_layer_on_leadframe():
    from ui.LayeronLeadframeConfigurator import LayeronLeadframeConfigurator
    from ui.ExtendedPropertyPanel import ExtendedPropertyPanel

>>>>>>> Refactoring_Layout:leadframe/LayeronLeadframe.py
    try:
        _loader = _safe_get_loader()
        if _loader is None:
            QtWidgets.QMessageBox.critical(None, "Error", "GDSCommand.load_gds_layers konnte nicht importiert werden (Zirkularimport).");
            return None

        result = _loader()
        if not result or not result[0]: return None
        _preview_doc, _layer_objects, selected_layers, _unique_colors, gds_path, _lyp_path, options = result

<<<<<<< HEAD:LayeronLeadframe.py
        default_map = os.path.join(os.path.dirname(__file__), 'sg13g2.map')
        ihp_map = mymodule.parse_map(default_map) if os.path.exists(default_map) else {}

        # Use the compatibility shim (behaves like the old 'configuration')
        dlg = configuration()
        if dlg.exec_()!=QtWidgets.QDialog.Accepted: return None
        opts = dlg.get_opts() if hasattr(dlg, 'get_opts') else dict(rot_deg=0.0, mirror_y=False, tx=0.0, ty=0.0, auto_fit=True, margin_pct=5.0)
=======
        if not selected_layers or not gds_path:
            FreeCAD.Console.PrintError("❌ Missing selected layers or GDS path.\n")
            return
        
        # Load GDS Layers
        layers, _ = Core_Functionality.parse_lyp(lyp_path)
        if not layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No layers found in the LYP file.")
            return

        gds_layers = Core_Functionality.get_gds_layer(gds_path)
        if not gds_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No layers found in the GDS file.")
            return
        
        filtered_layers = [layer for layer in layers if (layer.get("layer_id", 0), layer.get("datatype", 0)) in gds_layers]
        if not filtered_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No matching layers found between LYP and GDS files.")
            return

        # IHP mapping (try default next to this module)
        default_map = os.path.join(os.path.dirname(__file__), "sg13g2.map")
        ihp_map = Core_Functionality.parse_map(default_map) if os.path.exists(default_map) else {}

        # Leadframe configuration
        config = configure_leadframe()
        if not config:
            FreeCAD.Console.PrintError("❌ Leadframe configuration cancelled.\n")
            return
>>>>>>> Refactoring_Layout:leadframe/LayeronLeadframe.py

        # Then ask for leadframe config
        cfg = LeadframeCommand.configure_leadframe()
        if not cfg: return None

        doc = FreeCAD.newDocument("Leadframe_Assembly")
        try: doc.openTransaction("Final Import")
        except Exception: pass

        layer_objects = _finalize_import(doc, gds_path, selected_layers, options, ihp_map, cfg, opts)
        if layer_objects is None:
            QtWidgets.QMessageBox.critical(None, "Error", "Failed to import stacked layers."); return None

<<<<<<< HEAD:LayeronLeadframe.py
        try: doc.commitTransaction()
        except Exception: pass
=======
        # Configuration fuction
        result = configuration(doc, gds_path, selected_layers, options, ihp_map, config, opts)
        if not result:
            FreeCAD.Console.PrintError("❌ Configuration failed.\n")
            return
        
        doc, layer_objects = result

        property_panel.update_properties(property_panel.selected_layers, Core_Functionality.parse_lyp(lyp_path)[1], layer_objects)

        try:
            doc.commitTransaction()
        except Exception:
            pass
>>>>>>> Refactoring_Layout:leadframe/LayeronLeadframe.py

        LeadframeCommand.create_leadframe(cfg, doc, layer_objects)
        doc.recompute()
        FreeCADGui.activeDocument().activeView().viewIsometric(); FreeCADGui.SendMsgToActiveView("ViewFit")
        return doc
    except Exception as e:
        FreeCAD.Console.PrintError(f"❌ An error occurred: {e}\n"); return None

class LayeronLeadframe:
    def GetResources(self):
<<<<<<< HEAD:LayeronLeadframe.py
        icon_path=os.path.join(os.path.dirname(__file__),"resources","icons","Layer on Leadframe.png")
        return {"MenuText":"Layer on Leadframe","ToolTip":"Import GDS on leadframe","Pixmap": icon_path if os.path.exists(icon_path) else ""}
=======
        return {
            "MenuText": "Layer on Leadframe",
            "ToolTip": "Configure layers on leadframe",
            "Pixmap": get_icon("Layer on Leadframe.png")
        }
    
>>>>>>> Refactoring_Layout:leadframe/LayeronLeadframe.py
    def Activated(self):
        res=create_layer_on_leadframe()
        if res is None: QtWidgets.QMessageBox.critical(None,"Error","Failed to create layer on leadframe.")
        else: QtWidgets.QMessageBox.information(None,"Success","Layered import + leadframe created.")
    def IsActive(self): return True

FreeCADGui.addCommand('LayeronLeadframe', LayeronLeadframe())
