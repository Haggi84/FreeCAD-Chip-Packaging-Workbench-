from PySide2 import QtWidgets, QtCore
import os, sys
import FreeCAD, FreeCADGui, Part

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from core.Core_Functionality import load_gds, bbox_from_entries, derive_base_scale_mm, build_stack_mm, is_bondable, style_for_material, parse_lyp, get_gds_layer, ILD_SPACING_UM
from leadframe.LeadframeCommand import create_leadframe, configure_leadframe
from core.Color import hex_to_rgb
from Get_Path import get_icon

from gds.Get_GDS_Path import get_gds_path
from ui.LayerSelector import LayerSelector

def configuration(doc, gds_path, selected_layers, options, ihp_map, config, opts):
        # First pass — measure bbox at base scale
        first_transform = {
            "scale": None,
            "rot_deg": opts["rot_deg"],
            "mirror_y": opts["mirror_y"],
            "tx": 0.0,
            "ty": 0.0,
            "z_thickness": 0.03
        }
            
        tmp_entries = load_gds(
            gds_path, selected_layers, first_transform,
            preview_2d=False, compound_per_layer=True,
            min_area_mm2=0.0004, decimate_tol_mm=0.002, skip_fill_datatype=True
        )
        if not tmp_entries:
            QtWidgets.QMessageBox.warning(None, "Warning", "No shapes produced during measurement pass.")
            return None

        bb = bbox_from_entries(tmp_entries)
        if not bb:
            QtWidgets.QMessageBox.warning(None, "Warning", "Failed to compute bounding box for GDS shapes.")
            return None
            
        xmin, ymin, xmax, ymax = bb
        
        # Geometry is ALREADY in millimeters from the first pass
        die_w_mm = max(0.0, xmax - xmin)
        die_h_mm = max(0.0, ymax - ymin)

        # Extract final frame dimensions to use in Auto-Fit
        frame_length = config["frame_length"]
        frame_width  = config["frame_width"]

        # 1. Fix Auto-Fit: Calculate exact scaling to fill the paddle
        base_scale = derive_base_scale_mm(gds_path)
        final_scale = base_scale
        fit_factor = 1.0
        
        if opts["auto_fit"] and die_w_mm > 0 and die_h_mm > 0:
            margin = max(0.0, opts["margin_pct"]) / 100.0
            
            if config["frame_type"] == "QFN (Quad Flat No-lead)":
                usable_w = frame_length - (2 * config["lead_length"]) - 0.4
                usable_h = frame_width - (2 * config["lead_length"]) - 0.4
            elif config["frame_type"] == "QFP (Quad Flat Package)":
                usable_w = frame_length - 0.4
                usable_h = frame_width - 0.4
            else: 
                usable_w = frame_length * 0.70
                usable_h = frame_width * 0.70
                
            if usable_w <= 0: usable_w = frame_length * 0.5
            if usable_h <= 0: usable_h = frame_width * 0.5

            fit_w = usable_w * (1.0 - margin)
            fit_h = usable_h * (1.0 - margin)
            
            fit_factor = min(fit_w / die_w_mm, fit_h / die_h_mm)
            final_scale = base_scale * fit_factor

        cx = (xmin + xmax) / 2.0
        cy = (ymin + ymax) / 2.0
        
        final_tx = -(cx * fit_factor) + opts["tx"]
        final_ty = -(cy * fit_factor) + opts["ty"]

        final_transform = {
            "scale": final_scale,
            "rot_deg": opts["rot_deg"],
            "mirror_y": opts["mirror_y"],
            "tx": final_tx,
            "ty": final_ty,
            "z_thickness": 0.03
        }

        # 2. Silicon Substrate Logic
        z_offset_base = 0.0
        if config["frame_type"] == "BGA (Ball Grid Array)":
            z_offset_base = config["frame_thickness"] + 0.05
            
        silicon_thickness = 0.3 
        z_exaggeration = 40.0 
        
        stack = build_stack_mm(selected_layers, ihp_map, ild_um= ILD_SPACING_UM)
        for key in stack:
            stack[key]["t_mm"] = stack[key]["t_mm"] * z_exaggeration
            stack[key]["z0_mm"] = (stack[key]["z0_mm"] * z_exaggeration) + (silicon_thickness + z_offset_base)

        match_klayout = bool(options.get("match_klayout", True))
        highlight_bondable = bool(options.get("highlight_bondable", True))
        skip_fill = not match_klayout
        min_area = 0.0 if match_klayout else 0.0004
        decimate = 0.0 if match_klayout else 0.002

        shapes = load_gds(
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
            return None

        layer_objects = {}
        for layer in selected_layers:
            layer_id = layer.get("layer_id", 0)
            datatype = layer.get("datatype", 0)
            layer_name = layer.get("name", "Unnamed")

            map_entry = ihp_map.get((layer_id, datatype))
            edi_name  = map_entry["edi_name"] if map_entry else ""
            types     = map_entry["edi_types"] if map_entry else set()

            if match_klayout:
                shape_rgb = hex_to_rgb(layer.get("fill-color", "#FFFFFF"))
                line_rgb  = hex_to_rgb(layer.get("frame-color", "#000000"))
                tr = 0
                if highlight_bondable and is_bondable(types):
                    shape_rgb = (0.90, 0.75, 0.20)
                    line_rgb  = (0.25, 0.20, 0.10)
                    tr = 0
            else:
                _, shape_rgb, line_rgb, tr = style_for_material(edi_name, types)
                if not highlight_bondable and is_bondable(types):
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
            obj.Bondable = is_bondable(types) if highlight_bondable else False

            layer_objects.setdefault((layer_id,datatype), []).append(obj)

        # 3. Generate the Gray Silicon Die
        die_w_final = die_w_mm * fit_factor
        die_h_final = die_h_mm * fit_factor
        
        if die_w_final > 0 and die_h_final > 0:
            die_shape = Part.makeBox(die_w_final, die_h_final, silicon_thickness, 
                                     FreeCAD.Base.Vector(-die_w_final/2.0 + opts["tx"], -die_h_final/2.0 + opts["ty"], z_offset_base))
            die_obj = doc.addObject("Part::Feature", "Silicon_Die")
            die_obj.Shape = die_shape
            die_obj.ViewObject.ShapeColor = (0.6, 0.6, 0.6) 
            
            layer_objects.setdefault("Silicon", []).append(die_obj)

        create_leadframe(config, doc, layer_objects)

        return doc, layer_objects

def create_layer_on_leadframe():
    from leadframe.LayeronLeadframeConfigurator import LayeronLeadframeConfigurator
    from ui.ExtendedPropertyPanel import ExtendedPropertyPanel

    try:
        # 1. Manually fetch the GDS paths directly
        paths = get_gds_path()
        if not paths or not paths[0]:
            return
        gds_path, lyp_path, layers, unique_colors, map_path, ihp_map = paths

        gds_layers = get_gds_layer(gds_path)
        if not gds_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No layers found in the GDS file.")
            return

        if not layers:
            layers = [{"name": f"Layer_{lid}_{dt}", "layer_id": lid, "datatype": dt, "fill-color": "#CCCCCC", "frame-color": "#000000", "visible": True} for (lid, dt) in sorted(gds_layers)]
            unique_colors.add(("#000000", "#CCCCCC"))

        filtered_layers = [layer for layer in layers if (layer.get("layer_id", 0), layer.get("datatype", 0)) in gds_layers]
        if not filtered_layers:
            filtered_layers = [{"name": f"Layer_{lid}_{dt}", "layer_id": lid, "datatype": dt, "fill-color": "#CCCCCC", "frame-color": "#000000", "visible": True} for (lid, dt) in sorted(gds_layers)]

        # 2. Trigger the Layer Selection Dialog immediately
        default_options = {"match_klayout": True, "highlight_bondable": True}
        dialog = LayerSelector(filtered_layers, options=default_options)
        if not dialog.exec_():
            return
        selected_layers = dialog.selected_layers
        options = dialog.options

        if not selected_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No layers selected.")
            return

        # 3. Configure the leadframe Dilaog
        config = configure_leadframe()
        if not config:
            FreeCAD.Console.PrintError("Leadframe configuration cancelled.\n")
            return

        # 4. Configure the Transform Dialog
        tdlg = LayeronLeadframeConfigurator()
        if tdlg.exec_() != QtWidgets.QDialog.Accepted:
            FreeCAD.Console.PrintMessage("Transform dialog cancelled by user.\n")
            return
        opts = tdlg.get_opts()

        doc = FreeCAD.newDocument("Leadframe_Assembly")
        try:
            doc.openTransaction("Final Import (stacked)")
        except Exception:
            pass

        property_panel = ExtendedPropertyPanel(FreeCADGui.getMainWindow())
        property_panel.attach_to_document(doc)
        property_panel.set_map(ihp_map, map_path)
        property_panel.gds_path = gds_path
        property_panel.lyp_path = lyp_path
        property_panel.filtered_layers = filtered_layers
        property_panel.selected_layers = selected_layers
        property_panel.options = options
        property_panel.leadframe_config = config
        property_panel.transform_opts = opts
        FreeCADGui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, property_panel)

        result_config = configuration(doc, gds_path, selected_layers, options, ihp_map, config, opts)
        if not result_config:
            FreeCAD.Console.PrintError("Configuration failed.\n")
            return
        
        doc, layer_objects = result_config

        if lyp_path:
            unique_colors = parse_lyp(lyp_path)[1]
        else:
            unique_colors = set()
            for layer in selected_layers:
                frame_color = layer.get("frame-color", "#000000")
                fill_color = layer.get("fill-color", "#FFFFFF")
                unique_colors.add((frame_color, fill_color))

        property_panel.update_properties(property_panel.selected_layers, unique_colors, layer_objects)

        try:
            doc.commitTransaction()
        except Exception:
            pass

        doc.recompute()
        FreeCADGui.activeDocument().activeView().viewIsometric()
        FreeCADGui.SendMsgToActiveView("ViewFit")

        return doc

    except Exception as e:
        FreeCAD.Console.PrintError(f"An error occurred in LayeronLeadframe: {str(e)}\n")
        import traceback
        FreeCAD.Console.PrintError(traceback.format_exc() + "\n")
        QtWidgets.QMessageBox.critical(None, "Error", f"An error occurred while creating layer on leadframe: {str(e)}")
        return None

class LayeronLeadframe:
    def GetResources(self):
        return {
            "MenuText": "Layer on Leadframe",
            "ToolTip": "Configure layers on leadframe",
            "Pixmap": get_icon("Layer on Leadframe.png")
        }
    
    def Activated(self):
        result = create_layer_on_leadframe()
        if result is None:
            pass 
        else:
            QtWidgets.QMessageBox.information(None, "Success", "GDS stacked with correct heights; imported with chosen color mode.\n")

    def IsActive(self):
        return True
    
FreeCADGui.addCommand("LayeronLeadframe", LayeronLeadframe())