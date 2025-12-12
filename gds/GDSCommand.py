from PySide2 import QtWidgets, QtCore
import os, sys
import FreeCAD, FreeCADGui

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from gds.PropertyPanel import PropertyPanel
from core import Core_Functionality
from core.Color import hex_to_rgb
from Get_Path import get_icon

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
            return None, None, None, None, None, None, None

        lyp_path, _ = QtWidgets.QFileDialog.getOpenFileName(None, "Select LYP File", "", "LYP Files (*.lyp *.LYP)")
        if not lyp_path or not os.path.exists(lyp_path):
            QtWidgets.QMessageBox.critical(None, "Error", "LYP file not found or invalid path.")
            return None, None, None, None, None, None, None

        # Optional: choose MAP (technology). If cancelled, try default.
        map_path, _ = QtWidgets.QFileDialog.getOpenFileName(None, "Select IHP MAP (optional)", "", "MAP Files (*.map *.MAP)")
        if not map_path:
            FreeCAD.Console.PrintWarning("No MAP file selected, proceeding without layer mapping.\n")
            map_path = None
        ihp_map = Core_Functionality.parse_map(map_path) if map_path else {}

        layers_with_colors = Core_Functionality.parse_lyp(lyp_path)
        if not layers_with_colors:
            QtWidgets.QMessageBox.critical(None, "Error", "No layers found in the LYP file.")
            return None, None, None, None, None, None, None

        layers, unique_colors = layers_with_colors
        gds_layers = Core_Functionality.get_gds_layer(gds_path)
        if not gds_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No layers found in the GDS file.")
            return None, None, None, None, None, None, None

        filtered_layers = [layer for layer in layers if (layer.get("layer_id", 0), layer.get("datatype", 0)) in gds_layers]
        if not filtered_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No matching layers found between LYP and GDS files.")
            return None, None, None, None, None, None, None
        
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
        dialog = LayerSelector(filtered_layers, options=property_panel.options)
        if dialog.exec_():
            selected_layers = dialog.selected_layers
            options = dialog.options
            if not selected_layers:
                QtWidgets.QMessageBox.warning(None, "Warning", "No layers selected.")
                return None, None, None, None, None, None, None

             # save options to panel (needed for modify action)
            property_panel.options = dict(options)

            # params derived from options
            match_klayout = bool(options.get("match_klayout", True))
            skip_fill = not match_klayout
            min_area = 0.0 if match_klayout else 0.0004
            decimate = 0.0 if match_klayout else 0.002
            use_klayout_colors = match_klayout
            highlight_bondable = bool(options.get("highlight_bondable", True))

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

            cancelled = False

            def progress_callback(current, total, message=""):
                nonlocal cancelled
                total = max(int(total), 1)
                progress_dialog.setMaximum(total)
                progress_dialog.setValue(int(current))
                progress_dialog.setLabelText(message or "Importing GDS layers...")
                QtWidgets.QApplication.processEvents()
                if progress_dialog.wasCanceled():
                    cancelled = True
                    return False
                return True

            try:
                shapes = Core_Functionality.load_gds(
                    gds_path,
                    selected_layers,
                    transform=None,
                    preview_2d=True,
                    compound_per_layer=True,
                    min_area_mm2=min_area,
                    decimate_tol_mm=decimate,
                    skip_fill_datatype=skip_fill,
                    progress_callback=progress_callback
                )
            finally:
                progress_dialog.close()

            if cancelled:
                QtWidgets.QMessageBox.information(None, "Cancelled", "GDS layer import cancelled.")
                return None, None, None, None, None, None, None
            if not shapes:
                QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found for the selected layers.")
                return None, None, None, None, None, None, None

            layer_objects = {}
            
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

            doc.recompute()

            property_panel.update_properties(selected_layers, unique_colors, layer_objects)
            FreeCADGui.activeDocument().activeView().viewIsometric()
            FreeCADGui.SendMsgToActiveView("ViewFit")
            # NOTE: return options as 7th element
            return doc, layer_objects, selected_layers, unique_colors, gds_path, lyp_path, options

        else:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Layer selection cancelled.")
            return None, None, None, None, None, None, None

    except Exception as e:
        FreeCAD.Console.PrintError(f"An error in GDSCommand: {str(e)}\n")
        QtWidgets.QMessageBox.critical(None, "Error", f"Failed to process files: {str(e)}")
        return None, None, None, None, None, None, None


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
            doc, layer_objects, selected_layers, unique_colors, gds_path, lyp_path, options = result
            QtWidgets.QMessageBox.information(None, "Success", f"GDSII file loaded with layers displayed successfully.", QtWidgets.QMessageBox.Ok)

    def IsActive(self):
        return True

import FreeCADGui
FreeCADGui.addCommand('GDSCommand', GDSCommand())
