from PySide2 import QtWidgets, QtCore, QtGui
import FreeCAD, FreeCADGui
import mymodule
from Color import hex_to_rgb

class PropertyPanel(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super().__init__('Layer & Technology', parent)
        self.setAllowedAreas(QtCore.Qt.RightDockWidgetArea | QtCore.Qt.LeftDockWidgetArea)
        self.gds_path=None; self.lyp_path=None; self.filtered_layers=[]; self.selected_layers=[]; self.options={"match_klayout": True, "highlight_bondable": True}; self.ihp_map={}; self.map_path=None
        self.doc_name=None

        w = QtWidgets.QWidget(); v=QtWidgets.QVBoxLayout(w); self.tabs=QtWidgets.QTabWidget(); v.addWidget(self.tabs)

        self.layer_tree=QtWidgets.QTreeWidget(); self.layer_tree.setHeaderLabels(["Property","Value"]); self.tabs.addTab(self.layer_tree, "Layer Properties")
        self.tech_table=QtWidgets.QTableWidget(0, 6); self.tech_table.setHorizontalHeaderLabels(["LayerID","Datatype","Name","Types","Material","Bondable"]); self.tech_table.horizontalHeader().setStretchLastSection(True); self.tabs.addTab(self.tech_table, "Technology")

        self.btn=QtWidgets.QPushButton("Modify Layers…"); self.btn.clicked.connect(self.modify_layer_selection); v.addWidget(self.btn)
        self.setWidget(w)

    # --- NEW: keep API used by GDSCommand ---
    def attach_to_document(self, doc):
        """Remember the active document name for context (non-critical)."""
        try:
            self.doc_name=getattr(doc, 'Name', None)
        except Exception:
            self.doc_name=None

    def set_map(self, ihp_map: dict, map_path: str=None):
        self.ihp_map=ihp_map or {}; self.map_path=map_path

    def _fill_layer_properties(self, layers):
        self.layer_tree.clear()
        for L in layers or []:
            p=QtWidgets.QTreeWidgetItem(self.layer_tree, ["Layer", L.get('name','Unnamed')])
            for k in ("layer_id","datatype","frame-color","fill-color","source"): QtWidgets.QTreeWidgetItem(p,[k,str(L.get(k))])
        self.layer_tree.expandAll()

    def _fill_tech_table(self, selected_layers, unique_colors, layer_objects):
        self.tech_table.setRowCount(0)
        for r, L in enumerate(selected_layers or []):
            lid=L.get('layer_id',0); dt=L.get('datatype',0); name=L.get('name','')
            m=self.ihp_map.get((lid,dt), {}); edi=m.get('edi_name',''); types=m.get('edi_types', set())
            mat_label, shape_rgb, line_rgb, _ = mymodule.style_for_material(edi, types)
            bondable = 'yes' if mymodule.is_bondable(types) else 'no'
            self.tech_table.insertRow(r)
            self.tech_table.setItem(r,0,QtWidgets.QTableWidgetItem(str(lid)))
            self.tech_table.setItem(r,1,QtWidgets.QTableWidgetItem(str(dt)))
            self.tech_table.setItem(r,2,QtWidgets.QTableWidgetItem(edi or name))
            self.tech_table.setItem(r,3,QtWidgets.QTableWidgetItem(','.join(sorted(types))))
            it=QtWidgets.QTableWidgetItem(mat_label); it.setBackground(QtGui.QBrush(QtGui.QColor.fromRgbF(*shape_rgb))); self.tech_table.setItem(r,4,it)
            self.tech_table.setItem(r,5,QtWidgets.QTableWidgetItem(bondable))
        self.tech_table.resizeColumnsToContents()

    def update_properties(self, selected_layers, unique_colors, layer_objects):
        self.selected_layers=list(selected_layers or [])
        self._fill_layer_properties(self.filtered_layers or [])
        self._fill_tech_table(self.selected_layers, unique_colors, layer_objects)

    def modify_layer_selection(self):
        from All_Class import LayerSelector
        if not self.gds_path or not self.lyp_path or not self.filtered_layers:
            QtWidgets.QMessageBox.critical(None, "Error", "Missing file paths or layer metadata."); return
        dlg = LayerSelector(self.filtered_layers, self.selected_layers, options=self.options)
        if dlg.exec_()!=QtWidgets.QDialog.Accepted: return
        selected_layers=dlg.selected_layers; self.options=dict(dlg.options)
        if not selected_layers:
            QtWidgets.QMessageBox.warning(None, "Warning", "No layers selected."); return

        doc = FreeCAD.activeDocument() or FreeCAD.newDocument("GDSII_Document")
        try: doc.openTransaction("Update Layer Selection")
        except Exception: pass

        match_klayout = bool(self.options.get("match_klayout", True))
        skip_fill = not match_klayout
        highlight_bondable = bool(self.options.get("highlight_bondable", True))

        # remove old objects
        for o in list(doc.Objects):
            try: doc.removeObject(o.Name)
            except Exception: pass

        entries = mymodule.load_gds(self.gds_path, selected_layers, preview_2d=True, compound_per_layer=True, skip_fill_datatype=skip_fill)
        if not entries:
            QtWidgets.QMessageBox.warning(None, "Warning", "No shapes for selection."); return

        layer_objects = {}
        for L in selected_layers:
            lid=L.get('layer_id',0); dt=L.get('datatype',0); lname=L.get('name','Unnamed')
            m=self.ihp_map.get((lid,dt), {}); types=m.get('edi_types', set())
            if match_klayout:
                shape_rgb = hex_to_rgb(L.get('fill-color', '#FFFFFF')); line_rgb = hex_to_rgb(L.get('frame-color', '#000000')); tr=0
                if highlight_bondable and mymodule.is_bondable(types): shape_rgb=(0.95,0.80,0.25)
            else:
                _, shape_rgb, line_rgb, tr = mymodule.style_for_material(m.get('edi_name',''), types)
            e = next((e for e in entries if e['layer_id']==lid and e['datatype']==dt), None)
            if not e: continue
            obj = doc.addObject("Part::Feature", f"Layer_{lname}_{lid}_{dt}"); obj.Shape = e['shape']
            obj.ViewObject.ShapeColor = shape_rgb; obj.ViewObject.LineColor = line_rgb; obj.ViewObject.Transparency = tr
            layer_objects.setdefault((lid,dt), []).append(obj)

        try: doc.commitTransaction()
        except Exception: pass
        doc.recompute()
        self.update_properties(selected_layers, mymodule.parse_lyp(self.lyp_path)[1], layer_objects)
        FreeCADGui.activeDocument().activeView().viewIsometric(); FreeCADGui.SendMsgToActiveView("ViewFit")
