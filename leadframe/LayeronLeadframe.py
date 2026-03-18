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

    frame_length = config["frame_length"]
    frame_width = config["frame_width"]

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

    cx = (xmin + xmax) / 2.0
    cy = (ymin + ymax) / 2.0
    final_tx = -cx + opts["tx"]
    final_ty = -cy + opts["ty"]

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

    # 3D import parameters derived from options
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
        edi_name = map_entry["edi_name"] if map_entry else ""
        types = map_entry["edi_types"] if map_entry else set()

        # Color decision
        if match_klayout:
            shape_rgb = hex_to_rgb(layer.get("fill-color", "#FFFFFF"))
            line_rgb = hex_to_rgb(layer.get("frame-color", "#000000"))
            tr = 0
            if highlight_bondable and Core_Functionality.is_bondable(types):
                shape_rgb = (0.90, 0.75, 0.20)
                line_rgb = (0.25, 0.20, 0.10)
                tr = 0
        else:
            _, shape_rgb, line_rgb, tr = Core_Functionality.style_for_material(edi_name, types)
            if not highlight_bondable and Core_Functionality.is_bondable(types):
                shape_rgb = hex_to_rgb(layer.get("fill-color", "#FFFFFF"))
                line_rgb = hex_to_rgb(layer.get("frame-color", "#000000"))
                tr = 0

        shape = next((s for s in shapes if s["layer_id"] == layer_id and s["datatype"] == datatype), None)
        if not shape:
            continue

        obj = doc.addObject("Part::Feature", f"Layer_{layer_name}_{layer_id}_{datatype}")
        obj.Shape = shape["shape"]
        obj.ViewObject.ShapeColor = shape_rgb
        obj.ViewObject.LineColor = line_rgb
        obj.ViewObject.Transparency = tr

        if not hasattr(obj, "Bondable"):
            obj.addProperty("App::PropertyBool", "Bondable", "Technology", "Can be connected to the leadframe")
        obj.Bondable = Core_Functionality.is_bondable(types) if highlight_bondable else False

        layer_objects.setdefault((layer_id, datatype), []).append(obj)

    # Create the leadframe geometry in the same doc
    create_leadframe(config, doc, layer_objects)

    return doc, layer_objects

def create_layer_on_leadframe():
    from ui.LayeronLeadframeConfigurator import LayeronLeadframeConfigurator
    from ui.ExtendedPropertyPanel import ExtendedPropertyPanel

    try:
        # Pick + preview (fast 2D) -> returns options as 7th item
        result = load_gds_layers()
        if not result or result[0] is None:
            FreeCAD.Console.PrintError("Failed to load GDS layers.\n")
            return

        # backward compatible unpacking
        if len(result) >= 7:
            preview_doc, _, selected_layers, _, gds_path, lyp_path, options = result
        else:
            preview_doc, _, selected_layers, _, gds_path, lyp_path = result
            options = {"match_klayout": True, "highlight_bondable": True}

        if not selected_layers or not gds_path:
            FreeCAD.Console.PrintError("Missing selected layers or GDS path.\n")
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
            FreeCAD.Console.PrintError("Leadframe configuration cancelled.\n")
            return

        # Transform options
        tdlg = LayeronLeadframeConfigurator()
        if tdlg.exec_() != QtWidgets.QDialog.Accepted:
            FreeCAD.Console.PrintMessage("Transform dialog cancelled by user.\n")
            return
        opts = tdlg.get_opts()

        # Final import with material styles / LYP colors and correct Z stacking
        doc = FreeCAD.newDocument("Leadframe_Assembly")
        try:
            doc.openTransaction("Final Import (stacked)")
        except Exception:
            pass

        # Initialize PropertyPanel
        property_panel = ExtendedPropertyPanel(FreeCADGui.getMainWindow())
        property_panel.set_map(ihp_map, default_map)
        property_panel.gds_path = gds_path
        property_panel.lyp_path = lyp_path
        property_panel.filtered_layers = filtered_layers  # Initially, filtered_layers are the selected ones
        property_panel.selected_layers = selected_layers
        property_panel.options = options
        property_panel.leadframe_config = config  # Store leadframe config
        property_panel.transform_opts = opts  # Store transform options
        FreeCADGui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, property_panel)

        # Configuration fuction
        result = configuration(doc, gds_path, selected_layers, options, ihp_map, config, opts)
        if not result:
            FreeCAD.Console.PrintError("Configuration failed.\n")
            return
        
        doc, layer_objects = result

        property_panel.update_properties(property_panel.selected_layers, Core_Functionality.parse_lyp(lyp_path)[1], layer_objects)

        try:
            doc.commitTransaction()
        except Exception:
            pass

        doc.recompute()
        FreeCADGui.activeDocument().activeView().viewIsometric()
        FreeCADGui.SendMsgToActiveView("ViewFit")

        return doc

    except Exception as e:
        FreeCAD.Console.PrintError(f"An error occurred: {str(e)}\n")
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
            QtWidgets.QMessageBox.critical(None, "Error", "Failed to create layer on leadframe.\n")
        else:
            QtWidgets.QMessageBox.information(None, "Success", "GDS stacked with correct heights; imported with chosen color mode.\n")

    def IsActive(self):
        """
        Check if the command is active.
        """
        return True
    

FreeCADGui.addCommand("LayeronLeadframe", LayeronLeadframe())