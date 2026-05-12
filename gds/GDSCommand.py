from compat import QtWidgets, QtCore
import os, sys, time
import FreeCAD, FreeCADGui

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from gds.PropertyPanel import PropertyPanel
from core import Core_Functionality
from core.Color import hex_to_rgb
from Get_Path import get_icon
from session.SessionManager import session_manager

# ----------------------------------------
# Main flow: pick files, preview document
# ----------------------------------------

def load_gds_layers():
    from ui.LayerSelector import LayerSelector

    """
    Interactively pick GDS + LYP (+ optional MAP), select visible layers present
    in the GDS, create a fast preview document, and return:
        (doc, layer_objects, selected_layers, unique_colors, gds_path, lyp_path)
    """
    try:
        gds_path, _ = QtWidgets.QFileDialog.getOpenFileName(None, "Select GDS File", "", "GDS Files (*.gds *.GDS)")
        if not gds_path or not os.path.exists(gds_path):
            QtWidgets.QMessageBox.critical(None, "Error", "GDS file not found or invalid path.")
            return None, None, None, None, None, None, None, None

        # ── Technology config: use global/session paths when available ──────
        from core.TechConfig import tech_config

        if tech_config.has_lyp():
            lyp_path = tech_config.get_lyp()
            FreeCAD.Console.PrintMessage(f"TechConfig: using LYP  {lyp_path}\n")
        else:
            lyp_path, _ = QtWidgets.QFileDialog.getOpenFileName(None, "Select LYP File", "", "LYP Files (*.lyp *.LYP)")
            if not lyp_path or not os.path.exists(lyp_path):
                QtWidgets.QMessageBox.critical(None, "Error", "LYP file not found or invalid path.")
                return None, None, None, None, None, None, None, None
            tech_config.set_local(lyp=lyp_path)

        if tech_config.has_map():
            map_path = tech_config.get_map()
            FreeCAD.Console.PrintMessage(f"TechConfig: using MAP  {map_path}\n")
        else:
            map_path, _ = QtWidgets.QFileDialog.getOpenFileName(None, "Select IHP MAP (optional)", "", "MAP Files (*.map *.MAP)")
            if not map_path:
                FreeCAD.Console.PrintWarning("No MAP file selected, proceeding without layer mapping.\n")
                map_path = None
            elif map_path:
                tech_config.set_local(map_=map_path)
        ihp_map = Core_Functionality.parse_map(map_path) if map_path else {}

        if tech_config.has_xml():
            xml_path = tech_config.get_xml()
            FreeCAD.Console.PrintMessage(f"TechConfig: using XML  {xml_path}\n")
        else:
            xml_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                None, "Select Stackup XML (optional)", "", "XML Files (*.xml *.XML)"
            )
            if not xml_path:
                FreeCAD.Console.PrintWarning("No stackup XML selected, using built-in thickness defaults.\n")
                xml_path = None
            elif xml_path:
                tech_config.set_local(xml=xml_path)
        stackup_data = Core_Functionality.parse_stackup_xml(xml_path) if xml_path else {}

        layers_with_colors = Core_Functionality.parse_lyp(lyp_path)
        if not layers_with_colors:
            QtWidgets.QMessageBox.critical(None, "Error", "No layers found in the LYP file.")
            return None, None, None, None, None, None, None, None

        layers, unique_colors = layers_with_colors
        gds_layers = Core_Functionality.get_gds_layer(gds_path)
        if not gds_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No layers found in the GDS file.")
            return None, None, None, None, None, None, None, None

        filtered_layers = [layer for layer in layers if (layer.get("layer_id", 0), layer.get("datatype", 0)) in gds_layers]
        if not filtered_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No matching layers found between LYP and GDS files.")
            return None, None, None, None, None, None, None, None
        
        doc = FreeCAD.newDocument("GDSII_Document")

        # Property panel and preview doc
        property_panel = PropertyPanel(FreeCADGui.getMainWindow())
        property_panel.attach_to_document(doc)
        property_panel.set_map(ihp_map, map_path)

        FreeCADGui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, property_panel)
        property_panel.gds_path = gds_path
        property_panel.lyp_path = lyp_path
        property_panel.filtered_layers = filtered_layers
        property_panel.update_properties([], unique_colors, {})

        # Layer selection (now with 'Import all layers')
        dialog = LayerSelector(filtered_layers, options=property_panel.options, ihp_map=ihp_map)
        if dialog.exec_():
            selected_layers = dialog.selected_layers
            options = dialog.options
            if not selected_layers:
                QtWidgets.QMessageBox.warning(None, "Warning", "No layers selected.")
                return None, None, None, None, None, None, None, None

             # save options to panel (needed for modify action)
            property_panel.options = dict(options)
            # store xml_path so session replay can reconstruct the stackup
            property_panel.options["xml_path"] = xml_path

            # params derived from options
            match_klayout = bool(options.get("match_klayout", True))
            skip_fill = False   # filler layers are now always shown as a single bbox solid
            # Collect (layer_id, datatype) pairs declared as FILL in the IHP map
            fill_layer_keys = {
                k for k, v in (ihp_map or {}).items()
                if "FILL" in v.get("edi_types", set())
            }
            # PIN layers: always flat 2D, never extruded.
            # A layer is a pure pin-marker layer when its EDI types are exclusively
            # PIN/LEFPIN with no physical-layer types (NET, SPNET, VIA, DRAWING).
            # Drawing layers share the PIN/LEFPIN tags but also carry NET/SPNET/VIA,
            # so they must NOT be flattened.
            _pin_only  = {"PIN", "LEFPIN"}
            _non_pin   = {"NET", "SPNET", "VIA", "DRAWING"}
            flat_layer_keys = {
                k for k, v in (ihp_map or {}).items()
                if _pin_only & v.get("edi_types", set())
                and not (_non_pin & v.get("edi_types", set()))
            }
            if not flat_layer_keys:
                # Fallback without MAP: LYP layer names ending with ".pin"
                flat_layer_keys = {
                    (l["layer_id"], l["datatype"])
                    for l in (selected_layers or [])
                    if l.get("name", "").lower().endswith(".pin")
                }
            min_area = 0.0 if match_klayout else 0.0004
            decimate = 0.0 if match_klayout else 0.002
            use_klayout_colors = match_klayout
            highlight_bondable   = bool(options.get("highlight_bondable", True))
            extrude_3d           = bool(options.get("extrude_3d", False))
            auto_pin_contacts    = bool(options.get("auto_pin_contacts", False))
            contacts_only_3d     = bool(options.get("contacts_only_3d", False))

            # Pre-compute which layers to render as full geometry in contacts_only mode
            contact_keys       = None
            max_polys_per_layer = None
            if contacts_only_3d:
                extrude_3d = True   # contacts-only always produces 3D output
                # Aggressive area filter: bond pads are ≥ ~32×32 µm (0.001 mm²);
                # smaller polygons are routing metal / via fill — skip them.
                min_area = max(min_area, 0.001)
                # Keep at most 3 000 polygons per contact layer (sorted largest first).
                # A chip rarely has more than a few hundred bond pads.
                max_polys_per_layer = 3000

                top_keys, bottom_keys = Core_Functionality.identify_contact_layers(
                    selected_layers, ihp_map
                )
                contact_keys = top_keys | bottom_keys
                FreeCAD.Console.PrintMessage(
                    f"Contacts-only 3D: rendering {len(contact_keys)} contact layer(s) as "
                    f"full geometry, all others as one body solid.\n"
                    f"  Top keys:    {top_keys}\n"
                    f"  Bottom keys: {bottom_keys}\n"
                    f"  min_area filter: {min_area} mm²  |  poly cap: {max_polys_per_layer}\n"
                )

            # Ensure a valid document is available
            doc = FreeCAD.activeDocument()

            try:
                doc.openTransaction("Fast Preview Import")
            except Exception:
                pass

            progress_dialog = QtWidgets.QProgressDialog("Importing GDS layers...", "Cancel", 0, 0, FreeCADGui.getMainWindow())
            progress_dialog.setWindowModality(QtCore.Qt.ApplicationModal)
            progress_dialog.setMinimumDuration(0)
            progress_dialog.setAutoClose(False)
            progress_dialog.setWindowTitle("GDS Import")
            progress_dialog.show()
            # Force the dialog to paint before the long-running import starts so
            # users always see progress feedback.
            QtWidgets.QApplication.processEvents()

            cancelled  = False
            _t_start   = time.time()

            def progress_callback(current, total, message=""):
                nonlocal cancelled
                total   = max(int(total), 1)
                current = int(current)
                progress_dialog.setMaximum(total)
                progress_dialog.setValue(current)

                # ETA calculation
                elapsed = time.time() - _t_start
                if current > 0 and elapsed > 0.5:
                    remaining = elapsed / current * (total - current)
                    if remaining < 60:
                        eta = f"~{int(remaining)}s remaining"
                    elif remaining < 3600:
                        eta = f"~{int(remaining / 60)}m {int(remaining % 60)}s remaining"
                    else:
                        eta = f"~{remaining / 3600:.1f}h remaining"
                    label = f"{message or 'Importing GDS layers...'}\n{eta}"
                else:
                    label = message or "Importing GDS layers..."

                progress_dialog.setLabelText(label)
                QtWidgets.QApplication.processEvents()
                if progress_dialog.wasCanceled():
                    cancelled = True
                    return False
                return True

            stack_mm = (
                Core_Functionality.build_stack_mm_from_xml(selected_layers, ihp_map, stackup_data)
                if extrude_3d else None
            )

            try:
                shapes = Core_Functionality.load_gds(
                    gds_path,
                    selected_layers,
                    transform=None,
                    preview_2d=not extrude_3d,
                    compound_per_layer=True,
                    min_area_mm2=min_area,
                    decimate_tol_mm=decimate,
                    skip_fill_datatype=False,
                    fill_as_bbox=True,
                    fill_layer_keys=fill_layer_keys,
                    flat_layer_keys=flat_layer_keys,
                    stack_mm=stack_mm,
                    contacts_only_3d=contacts_only_3d,
                    contact_keys=contact_keys,
                    max_polys_per_layer=max_polys_per_layer,
                    progress_callback=progress_callback
                )
            finally:
                progress_dialog.close()

            if cancelled:
                QtWidgets.QMessageBox.information(None, "Cancelled", "GDS layer import cancelled.")
                return None, None, None, None, None, None, None, None
            if not shapes:
                QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found for the selected layers.")
                return None, None, None, None, None, None, None, None

            layer_objects = {}

            # ── body solid (contacts_only_3d mode) ──────────────────────────
            body_entry = next((s for s in shapes if s.get("is_body_solid")), None)
            if body_entry:
                body_obj = doc.addObject("Part::Feature", "IC_Body_Solid")
                body_obj.Shape = body_entry["shape"]
                body_obj.ViewObject.ShapeColor = (0.55, 0.55, 0.55)
                body_obj.ViewObject.LineColor   = (0.25, 0.25, 0.25)
                body_obj.ViewObject.Transparency = 60

            for layer in selected_layers:
                layer_id = layer.get("layer_id", 0)
                datatype = layer.get("datatype", 0)
                layer_name = layer.get("name", "Unknown Layer")

                map_entry = ihp_map.get((layer_id, datatype))
                types = map_entry["edi_types"] if map_entry else set()

                # decide colors
                if use_klayout_colors:
                    shape_rgb = hex_to_rgb(layer.get("fill-color", "#FFFFFF"))
                    line_rgb  = hex_to_rgb(layer.get("frame-color", "#000000"))
                    tr = 0
                    if highlight_bondable and Core_Functionality.is_bondable(types):
                        shape_rgb = (0.90, 0.75, 0.20)
                        line_rgb  = (0.25, 0.20, 0.10)
                        tr = 0
                else:
                    _, shape_rgb, line_rgb, tr = Core_Functionality.style_for_material(map_entry["edi_name"] if map_entry else "", types)
                    if not highlight_bondable and Core_Functionality.is_bondable(types):
                        # neutralize highlight to LYP look for this layer
                        shape_rgb = hex_to_rgb(layer.get("fill-color", "#FFFFFF"))
                        line_rgb  = hex_to_rgb(layer.get("frame-color", "#000000"))
                        tr = 0

                shape = next((s for s in shapes if s["layer_id"] == layer_id and s["datatype"] == datatype), None)
                if not shape:
                    continue

                obj = doc.addObject("Part::Feature", f"Layer_{layer_name}_{layer_id}")
                obj.Shape = shape["shape"]
                obj.ViewObject.ShapeColor = shape_rgb
                obj.ViewObject.LineColor = line_rgb
                obj.ViewObject.Transparency = tr
                layer_objects.setdefault(layer_id, []).append(obj)

            try:
                doc.commitTransaction()
            except Exception:
                pass

            # ── render phase — show progress while FreeCAD tessellates shapes ──
            total_faces = sum(
                getattr(getattr(s, "Shape", None), "Faces", None) and
                len(s.Shape.Faces) or 0
                for s in (layer_objects.get(k, [None])[0]
                          for k in layer_objects) if s
            )
            n_shapes = sum(len(v) for v in layer_objects.values())
            render_dlg = QtWidgets.QProgressDialog(
                f"Rendering {n_shapes} shape(s) in FreeCAD viewport…\n"
                "FreeCAD is tessellating geometry for display.\n"
                "Please wait — this window will close automatically.",
                None, 0, 0,
                FreeCADGui.getMainWindow()
            )
            render_dlg.setWindowTitle("Rendering")
            render_dlg.setWindowModality(QtCore.Qt.ApplicationModal)
            render_dlg.setMinimumDuration(0)
            render_dlg.show()
            QtWidgets.QApplication.processEvents()

            _t_render = time.time()
            doc.recompute()
            _render_elapsed = time.time() - _t_render
            render_dlg.close()
            FreeCAD.Console.PrintMessage(
                f"Render (tessellation) completed in {_render_elapsed:.1f}s\n"
            )

            property_panel.update_properties(selected_layers, unique_colors, layer_objects)

            # ── pin cell instances (GDS cells named "pin") ─────────────────
            pin_shape = Core_Functionality.load_pin_cell_shapes(gds_path)
            if pin_shape:
                pin_obj = doc.addObject("Part::Feature", "GDS_Pin_Instances")
                pin_obj.Shape = pin_shape
                pin_obj.ViewObject.ShapeColor   = (0.20, 0.80, 0.20)
                pin_obj.ViewObject.LineColor    = (0.10, 0.50, 0.10)
                pin_obj.ViewObject.Transparency = 30
                FreeCAD.Console.PrintMessage("GDS pin cell instances imported as flat 2D shapes.\n")

            # ── auto PIN pad detection ──────────────────────────────────────
            if auto_pin_contacts:
                cp_count = Core_Functionality.import_pin_pads_as_contacts(
                    gds_path, ihp_map, doc,
                    selected_layers=selected_layers,
                    top_n=3,
                )
                if cp_count:
                    QtWidgets.QMessageBox.information(
                        None, "Auto PIN Detection",
                        f"Created {cp_count} contact point(s) on the top PIN layers.\n"
                        "Check the Python console for details on which layers were used.\n\n"
                        "They are ready for use in manual wire bonding.",
                    )
                else:
                    QtWidgets.QMessageBox.warning(
                        None, "Auto PIN Detection",
                        "No PIN pad shapes could be extracted from this GDS file.\n\n"
                        "Check the Python console for details.\n"
                        "Tip: load an IHP .map file for best results.",
                    )

            view = FreeCADGui.activeDocument().activeView()
            view.viewIsometric()
            view.fitAll()
            # NOTE: return options as 7th element, map_path as 8th element
            return doc, layer_objects, selected_layers, unique_colors, gds_path, lyp_path, options, map_path

        else:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Layer selection cancelled.")
            return None, None, None, None, None, None, None, None

    except Exception as e:
        FreeCAD.Console.PrintError(f"An error in GDSCommand: {str(e)}\n")
        QtWidgets.QMessageBox.critical(None, "Error", f"Failed to process files: {str(e)}")
        return None, None, None, None, None, None, None, None


# --------------------------
# Command registration
# --------------------------
class GDSCommand:
    def GetResources(self):
        return {
            "MenuText": "Load GDSII",
            "ToolTip": "Load a GDSII file fast, show technology info and apply material styles",
            "Pixmap": get_icon("Load GDS.png")
        }

    def Activated(self):
        result = load_gds_layers()
        if result and result[0]:  # Check if a document was created
            doc, layer_objects, selected_layers, unique_colors, gds_path, lyp_path, options, map_path = result
            session_manager.record_action("gds_import", {
                "gds_path":        gds_path,
                "lyp_path":        lyp_path,
                "map_path":        map_path,
                "selected_layers": selected_layers,
                "options":         options,
            })
            QtWidgets.QMessageBox.information(None, "Success", f"GDSII file loaded with layers displayed successfully.", QtWidgets.QMessageBox.Ok)

    def IsActive(self):
        return True

import FreeCADGui
FreeCADGui.addCommand('GDSCommand', GDSCommand())


# ---------------------------------------------------------------------------
# Non-interactive import  (used by session replay)
# ---------------------------------------------------------------------------

def load_gds_with_params(gds_path, lyp_path, map_path, selected_layers, options):
    """Import a GDS file without showing any dialogs.

    Uses pre-specified *selected_layers* (list of layer dicts) and *options*
    (import flags dict) directly, bypassing the LayerSelector dialog.

    Returns the same 8-tuple as load_gds_layers(), or all-None on failure.
    """
    ihp_map = Core_Functionality.parse_map(map_path) if map_path else {}

    layers_with_colors = Core_Functionality.parse_lyp(lyp_path)
    if not layers_with_colors:
        FreeCAD.Console.PrintError("load_gds_with_params: LYP parse failed.\n")
        return (None,) * 8
    layers, unique_colors = layers_with_colors

    # Match saved layers against freshly parsed LYP (re-use saved if LYP changed)
    saved_keys = {(l["layer_id"], l["datatype"]) for l in selected_layers}
    filtered   = [l for l in layers
                  if (l.get("layer_id", 0), l.get("datatype", 0)) in saved_keys]
    if not filtered:
        filtered = list(selected_layers)   # fall back to saved dicts

    doc = FreeCAD.newDocument("GDSII_Document")
    property_panel = PropertyPanel(FreeCADGui.getMainWindow())
    property_panel.attach_to_document(doc)
    property_panel.set_map(ihp_map, map_path)
    FreeCADGui.getMainWindow().addDockWidget(
        QtCore.Qt.RightDockWidgetArea, property_panel
    )
    property_panel.gds_path        = gds_path
    property_panel.lyp_path        = lyp_path
    property_panel.filtered_layers = filtered
    property_panel.options         = dict(options)
    property_panel.update_properties([], unique_colors, {})

    # Derive import parameters from options (mirrors load_gds_layers logic)
    match_klayout     = bool(options.get("match_klayout",     True))
    highlight_bondable= bool(options.get("highlight_bondable",True))
    extrude_3d        = bool(options.get("extrude_3d",        False))
    auto_pin_contacts = bool(options.get("auto_pin_contacts", False))
    contacts_only_3d  = bool(options.get("contacts_only_3d",  False))

    fill_layer_keys = {
        k for k, v in (ihp_map or {}).items()
        if "FILL" in v.get("edi_types", set())
    }
    _pin_only = {"PIN", "LEFPIN"}
    _non_pin  = {"NET", "SPNET", "VIA", "DRAWING"}
    flat_layer_keys = {
        k for k, v in (ihp_map or {}).items()
        if _pin_only & v.get("edi_types", set())
        and not (_non_pin & v.get("edi_types", set()))
    }
    if not flat_layer_keys:
        flat_layer_keys = {
            (l["layer_id"], l["datatype"])
            for l in (filtered or [])
            if l.get("name", "").lower().endswith(".pin")
        }
    min_area = 0.0 if match_klayout else 0.0004
    decimate = 0.0 if match_klayout else 0.002

    contact_keys        = None
    max_polys_per_layer = None
    if contacts_only_3d:
        extrude_3d   = True
        min_area     = max(min_area, 0.001)
        max_polys_per_layer = 3000
        top_keys, bottom_keys = Core_Functionality.identify_contact_layers(
            filtered, ihp_map
        )
        contact_keys = top_keys | bottom_keys

    xml_path     = options.get("xml_path", None)
    stackup_data = Core_Functionality.parse_stackup_xml(xml_path) if xml_path else {}
    stack_mm = (
        Core_Functionality.build_stack_mm_from_xml(filtered, ihp_map, stackup_data)
        if extrude_3d else None
    )

    try:
        doc.openTransaction("Session Replay: GDS Import")
    except Exception:
        pass

    shapes = Core_Functionality.load_gds(
        gds_path, filtered,
        transform=None, preview_2d=not extrude_3d, compound_per_layer=True,
        min_area_mm2=min_area, decimate_tol_mm=decimate,
        skip_fill_datatype=False, fill_as_bbox=True,
        fill_layer_keys=fill_layer_keys, flat_layer_keys=flat_layer_keys,
        stack_mm=stack_mm,
        contacts_only_3d=contacts_only_3d, contact_keys=contact_keys,
        max_polys_per_layer=max_polys_per_layer,
    )

    if not shapes:
        FreeCAD.Console.PrintWarning("load_gds_with_params: no shapes produced.\n")
        return (None,) * 8

    layer_objects = {}

    body_entry = next((s for s in shapes if s.get("is_body_solid")), None)
    if body_entry:
        body_obj = doc.addObject("Part::Feature", "IC_Body_Solid")
        body_obj.Shape = body_entry["shape"]
        body_obj.ViewObject.ShapeColor  = (0.55, 0.55, 0.55)
        body_obj.ViewObject.LineColor   = (0.25, 0.25, 0.25)
        body_obj.ViewObject.Transparency = 60

    for layer in filtered:
        layer_id   = layer.get("layer_id", 0)
        datatype   = layer.get("datatype",  0)
        layer_name = layer.get("name", "Unknown Layer")

        map_entry = ihp_map.get((layer_id, datatype))
        types     = map_entry["edi_types"] if map_entry else set()

        if match_klayout:
            shape_rgb = hex_to_rgb(layer.get("fill-color",  "#FFFFFF"))
            line_rgb  = hex_to_rgb(layer.get("frame-color", "#000000"))
            tr = 0
            if highlight_bondable and Core_Functionality.is_bondable(types):
                shape_rgb = (0.90, 0.75, 0.20)
                line_rgb  = (0.25, 0.20, 0.10)
                tr = 0
        else:
            _, shape_rgb, line_rgb, tr = Core_Functionality.style_for_material(
                map_entry["edi_name"] if map_entry else "", types
            )
            if not highlight_bondable and Core_Functionality.is_bondable(types):
                shape_rgb = hex_to_rgb(layer.get("fill-color",  "#FFFFFF"))
                line_rgb  = hex_to_rgb(layer.get("frame-color", "#000000"))
                tr = 0

        shape = next(
            (s for s in shapes if s["layer_id"] == layer_id and s["datatype"] == datatype),
            None,
        )
        if not shape:
            continue

        obj = doc.addObject("Part::Feature", f"Layer_{layer_name}_{layer_id}")
        obj.Shape                    = shape["shape"]
        obj.ViewObject.ShapeColor    = shape_rgb
        obj.ViewObject.LineColor     = line_rgb
        obj.ViewObject.Transparency  = tr
        layer_objects.setdefault(layer_id, []).append(obj)

    try:
        doc.commitTransaction()
    except Exception:
        pass

    doc.recompute()
    property_panel.update_properties(filtered, unique_colors, layer_objects)

    pin_shape = Core_Functionality.load_pin_cell_shapes(gds_path)
    if pin_shape:
        pin_obj = doc.addObject("Part::Feature", "GDS_Pin_Instances")
        pin_obj.Shape = pin_shape
        pin_obj.ViewObject.ShapeColor   = (0.20, 0.80, 0.20)
        pin_obj.ViewObject.LineColor    = (0.10, 0.50, 0.10)
        pin_obj.ViewObject.Transparency = 30
        FreeCAD.Console.PrintMessage("GDS pin cell instances imported as flat 2D shapes.\n")

    if auto_pin_contacts:
        Core_Functionality.import_pin_pads_as_contacts(
            gds_path, ihp_map, doc, selected_layers=filtered, top_n=3
        )

    view = FreeCADGui.activeDocument().activeView()
    view.viewIsometric()
    view.fitAll()

    return doc, layer_objects, filtered, unique_colors, gds_path, lyp_path, options, map_path
