from PySide2 import QtWidgets, QtCore
<<<<<<< HEAD:GDSCommand.py
import os, FreeCAD, FreeCADGui, importlib
import mymodule
from Color import hex_to_rgb
# from All_Class import LayerSelector  # removed (lazy import)
=======
import os, sys
import FreeCAD, FreeCADGui

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from gds.PropertyPanel import PropertyPanel
from core import Core_Functionality
from core.Color import hex_to_rgb
from Get_Path import get_icon
>>>>>>> Refactoring_Layout:gds/GDSCommand.py

def _default_map_path():
    here=os.path.dirname(os.path.abspath(__file__)); p=os.path.join(here,'sg13g2.map'); return p if os.path.exists(p) else None

def _get_LayerSelector():
    try:
        AC = importlib.import_module('All_Class')
        return getattr(AC, 'LayerSelector', None)
    except Exception as e:
        FreeCAD.Console.PrintError(f"Import error (All_Class.LayerSelector): {e}\n"); return None

def load_gds_layers():
<<<<<<< HEAD:GDSCommand.py
    try:
        gds_path,_ = QtWidgets.QFileDialog.getOpenFileName(None, "Select GDS", "", "GDS Files (*.gds *.GDS)")
        if not gds_path: return (None,)*7
        lyp_path,_ = QtWidgets.QFileDialog.getOpenFileName(None, "Select LYP", "", "LYP Files (*.lyp *.LYP)")
        if not lyp_path: return (None,)*7
        map_path,_ = QtWidgets.QFileDialog.getOpenFileName(None, "Select MAP (optional)", "", "MAP Files (*.map *.MAP)") 
        if not map_path: map_path=_default_map_path()
        ihp_map = mymodule.parse_map(map_path) if map_path else {}

        layers, unique_colors = mymodule.parse_lyp(lyp_path)
        present = mymodule.get_gds_layer(gds_path)
        filtered = [L for L in layers if (L.get('layer_id',0), L.get('datatype',0)) in present]
        if not filtered:
            QtWidgets.QMessageBox.warning(None, "Warning", "No matching layers between LYP and GDS."); return (None,)*7

=======
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
        
>>>>>>> Refactoring_Layout:gds/GDSCommand.py
        doc = FreeCAD.newDocument("GDSII_Document")
        try:
            from PropertyPanel import PropertyPanel
            panel = PropertyPanel(FreeCADGui.getMainWindow())
        except Exception:
            panel = None
        if panel:
            if hasattr(panel, 'attach_to_document'): panel.attach_to_document(doc)
            FreeCADGui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
            if hasattr(panel, 'set_map'): panel.set_map(ihp_map, map_path)
            panel.gds_path=gds_path; panel.lyp_path=lyp_path; panel.filtered_layers=filtered
            if hasattr(panel, 'update_properties'): panel.update_properties([], unique_colors, {})

        LayerSelector = _get_LayerSelector()
        if LayerSelector is None:
            QtWidgets.QMessageBox.critical(None, "Error", "LayerSelector konnte nicht geladen werden (All_Class).");
            return (None,)*7

        dlg = LayerSelector(filtered, options=getattr(panel, 'options', {"match_klayout": True, "highlight_bondable": True}) if panel else {"match_klayout": True, "highlight_bondable": True})
        if dlg.exec_()!=QtWidgets.QDialog.Accepted:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Layer selection cancelled."); return (None,)*7
        selected_layers = dlg.selected_layers; options = dlg.options
        if not selected_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No layers selected."); return (None,)*7
        if panel and hasattr(panel, 'options'):
            panel.options=dict(options)

        match_klayout = bool(options.get('match_klayout', True)); skip_fill = not match_klayout; highlight = bool(options.get('highlight_bondable', True))

        try: doc.openTransaction("Preview Import")
        except Exception: pass

        entries = mymodule.load_gds(gds_path, selected_layers, preview_2d=True, compound_per_layer=True, skip_fill_datatype=skip_fill)
        if not entries:
            QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found for selected layers."); return (None,)*7

<<<<<<< HEAD:GDSCommand.py
        layer_objects = {}
        for L in selected_layers:
            lid=L.get('layer_id',0); dt=L.get('datatype',0); lname=L.get('name','Unnamed')
            m=ihp_map.get((lid,dt),{}); types=m.get('edi_types', set())
            if match_klayout:
                shape_rgb = hex_to_rgb(L.get('fill-color','#FFFFFF')); line_rgb = hex_to_rgb(L.get('frame-color','#000000')); tr=0
                if highlight and mymodule.is_bondable(types): shape_rgb=(0.90,0.75,0.20); line_rgb=(0.25,0.20,0.10)
            else:
                _,shape_rgb,line_rgb,tr = mymodule.style_for_material(m.get('edi_name',''), types)
            entry = next((e for e in entries if e['layer_id']==lid and e['datatype']==dt), None)
            if not entry: continue
            obj = doc.addObject("Part::Feature", f"Layer_{lname}_{lid}_{dt}"); obj.Shape = entry['shape']
            obj.ViewObject.ShapeColor = shape_rgb; obj.ViewObject.LineColor = line_rgb; obj.ViewObject.Transparency = tr
            layer_objects.setdefault((lid,dt), []).append(obj)
=======
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
>>>>>>> Refactoring_Layout:gds/GDSCommand.py

        try: doc.commitTransaction()
        except Exception: pass
        doc.recompute()
        if panel and hasattr(panel, 'update_properties'):
            panel.update_properties(selected_layers, unique_colors, layer_objects)
        FreeCADGui.activeDocument().activeView().viewIsometric(); FreeCADGui.SendMsgToActiveView("ViewFit")
        return doc, layer_objects, selected_layers, unique_colors, gds_path, lyp_path, options
    except Exception as e:
        FreeCAD.Console.PrintError(f"An error in GDSCommand: {e}\n")
        QtWidgets.QMessageBox.critical(None, "Error", f"Failed to process files: {e}")
        return (None,)*7

class GDSCommand:
    def GetResources(self):
<<<<<<< HEAD:GDSCommand.py
        icon_path = os.path.join(os.path.dirname(__file__),"resources","icons","Load GDS.png")
        return {"MenuText":"Load GDSII","ToolTip":"Load a GDSII file","Pixmap": icon_path if os.path.exists(icon_path) else ""}
=======
        return {
            "MenuText": "Load GDSII",
            "ToolTip": "Load a GDSII file fast, show technology info and apply material styles",
            "Pixmap": get_icon("Load GDS.png")
        }

>>>>>>> Refactoring_Layout:gds/GDSCommand.py
    def Activated(self):
        res = load_gds_layers()
        if res and res[0]:
            QtWidgets.QMessageBox.information(None, "Success", "GDSII preview created.")
    def IsActive(self): return True

<<<<<<< HEAD:GDSCommand.py
=======
    def IsActive(self):
        return True

import FreeCADGui
>>>>>>> Refactoring_Layout:gds/GDSCommand.py
FreeCADGui.addCommand('GDSCommand', GDSCommand())
