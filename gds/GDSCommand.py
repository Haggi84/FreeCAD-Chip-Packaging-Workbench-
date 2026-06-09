"""
GDSCommand.py
=============
FreeCAD-Befehle für den GDS-Import.

Interaktiver Pfad  : load_gds_layers()       — zeigt Dialoge
Session-Replay     : load_gds_with_params()  — keine Dialoge

Beide Pfade nutzen core.lod_import.build_lod_import_params() für die
Parameterableitung, damit keine Logik doppelt existiert.

Standard-Importmodus ist LOD (Level of Detail):
  • Sofort sichtbar: PIN-/Bond-Layer (3D) + IC_Body_Solid (BBox-Quader)
  • Lazy:            Routing-Layer auf Anfrage über den LOD-Manager
"""

from compat import QtWidgets, QtCore
import os, sys, time
import FreeCAD, FreeCADGui

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from gds.PropertyPanel import PropertyPanel
from core import Core_Functionality
from core.Color import hex_to_rgb
from core.lod_import import build_lod_import_params
from Get_Path import get_icon
from session.SessionManager import session_manager


# ── Farbauflösung ─────────────────────────────────────────────────────────────

def _layer_colors(layer: dict, ihp_map: dict,
                  match_klayout: bool, highlight_bondable: bool):
    """Gibt (shape_rgb, line_rgb, transparency) für einen Layer zurück."""
    lid = layer.get("layer_id", 0)
    dt  = layer.get("datatype",  0)
    m   = ihp_map.get((lid, dt))
    types = m["edi_types"] if m else set()

    if match_klayout:
        sr = hex_to_rgb(layer.get("fill-color",  "#FFFFFF"))
        lr = hex_to_rgb(layer.get("frame-color", "#000000"))
        tr = 0
        if highlight_bondable and Core_Functionality.is_bondable(types):
            sr, lr, tr = (0.90, 0.75, 0.20), (0.25, 0.20, 0.10), 0
    else:
        edi = m["edi_name"] if m else ""
        _, sr, lr, tr = Core_Functionality.style_for_material(edi, types)
        if not highlight_bondable and Core_Functionality.is_bondable(types):
            sr = hex_to_rgb(layer.get("fill-color",  "#FFFFFF"))
            lr = hex_to_rgb(layer.get("frame-color", "#000000"))
            tr = 0
    return sr, lr, tr


# ── Dokument-Aufbau ───────────────────────────────────────────────────────────

def _populate_document(doc, shapes, filtered_layers, ihp_map,
                       match_klayout, highlight_bondable, mesh_3d):
    """
    Erstellt FreeCAD-Objekte für alle Shapes.
    Gibt (layer_objects, pending_colors) zurück.
    """
    layer_objects  = {}
    pending_colors = []

    # IC_Body_Solid wird nicht mehr erstellt — LODManager übernimmt die
    # optische Rolle mit Pro-Layer-Platzhaltern.

    for layer in filtered_layers:
        lid  = layer.get("layer_id", 0)
        dt   = layer.get("datatype",  0)
        name = layer.get("name", "Unknown")

        shp = next((s for s in shapes
                    if s["layer_id"] == lid and s["datatype"] == dt), None)
        if not shp:
            continue

        sr, lr, tr = _layer_colors(layer, ihp_map, match_klayout, highlight_bondable)

        if shp.get("is_mesh"):
            obj = doc.addObject("Mesh::Feature", f"Layer_{name}_{lid}")
            obj.Mesh = shp["mesh"]
        else:
            obj = doc.addObject("Part::Feature", f"Layer_{name}_{lid}")
            obj.Shape = shp["shape"]

        # LOD-Metadaten für den Manager speichern
        try:
            obj.addProperty("App::PropertyInteger", "GDSLayerID",  "LOD", "GDS Layer ID")
            obj.addProperty("App::PropertyInteger", "GDSDatatype", "LOD", "GDS Datatype")
            obj.GDSLayerID  = lid
            obj.GDSDatatype = dt
        except Exception:
            pass

        pending_colors.append((obj, sr, lr, tr))
        layer_objects.setdefault(lid, []).append(obj)

    return layer_objects, pending_colors


def _apply_colors(pending_colors):
    for obj, sr, lr, tr in pending_colors:
        try:
            obj.ViewObject.ShapeColor   = sr
            obj.ViewObject.LineColor    = lr
            obj.ViewObject.Transparency = tr
        except Exception:
            pass


# ── Property Panel Setup ──────────────────────────────────────────────────────

def _setup_property_panel(doc, ihp_map, map_path, gds_path,
                           lyp_path, filtered_layers, unique_colors):
    pp = PropertyPanel(FreeCADGui.getMainWindow())
    pp.attach_to_document(doc)
    pp.set_map(ihp_map, map_path)
    FreeCADGui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, pp)
    pp.gds_path        = gds_path
    pp.lyp_path        = lyp_path
    pp.filtered_layers = filtered_layers
    pp.update_properties([], unique_colors, {})
    return pp


# ── Technologie-Datei auflösen ────────────────────────────────────────────────

def _resolve_tech_file(tech_config, kind: str, title: str,
                        file_filter: str, optional: bool = False):
    """Gibt Pfad aus tech_config oder Dateidialog zurück."""
    has_fn = getattr(tech_config, f"has_{kind}")
    get_fn = getattr(tech_config, f"get_{kind}")

    if has_fn():
        p = get_fn()
        FreeCAD.Console.PrintMessage(f"TechConfig: {kind.upper()}  {p}\n")
        return p

    p, _ = QtWidgets.QFileDialog.getOpenFileName(None, title, "", file_filter)
    if not p:
        if optional:
            FreeCAD.Console.PrintWarning(f"Kein {kind.upper()} gewählt — wird übersprungen.\n")
            return None
        QtWidgets.QMessageBox.critical(None, "Fehler",
                                       f"{kind.upper()}-Datei nicht gefunden.")
        return None

    kw = {"map_": p} if kind == "map" else {kind: p}
    tech_config.set_local(**kw)
    return p


# ── Import mit Fortschrittsanzeige ────────────────────────────────────────────

def _run_import(gds_path, selected_layers, load_kwargs):
    """
    Führt Core_Functionality.load_gds() mit Fortschrittsdialog aus.
    Gibt (shapes, cancelled) zurück.
    """
    dlg = QtWidgets.QProgressDialog(
        "GDS-Layer werden importiert…", "Abbrechen", 0, 0,
        FreeCADGui.getMainWindow()
    )
    dlg.setWindowModality(QtCore.Qt.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setWindowTitle("GDS Import")
    dlg.show()
    QtWidgets.QApplication.processEvents()

    cancelled = False
    t0 = time.time()

    def _cb(current, total, msg=""):
        nonlocal cancelled
        total   = max(int(total), 1)
        current = int(current)
        dlg.setMaximum(total)
        dlg.setValue(current)

        elapsed = time.time() - t0
        if current > 0 and elapsed > 0.5:
            rem = elapsed / current * (total - current)
            eta = (f"~{int(rem)}s" if rem < 60
                   else f"~{int(rem/60)}m {int(rem%60)}s")
            dlg.setLabelText(f"{msg or 'Importiere…'}\n{eta} verbleibend")
        else:
            dlg.setLabelText(msg or "Importiere…")

        QtWidgets.QApplication.processEvents()
        if dlg.wasCanceled():
            cancelled = True
            return False
        return True

    n_workers = max(1, (os.cpu_count() or 2) - 1)
    try:
        shapes = Core_Functionality.load_gds(
            gds_path, selected_layers,
            progress_callback=_cb,
            parallel_workers=n_workers,
            **load_kwargs,
        )
    finally:
        dlg.close()

    return shapes, cancelled


def _run_render(doc, layer_objects, pending_colors):
    """Ruft doc.recompute() mit Fortschrittsdialog auf."""
    n = sum(len(v) for v in layer_objects.values())
    dlg = QtWidgets.QProgressDialog(
        f"FreeCAD tesselliert {n} Shape(s)…\nBitte warten.",
        None, 0, 0, FreeCADGui.getMainWindow()
    )
    dlg.setWindowTitle("Rendering")
    dlg.setWindowModality(QtCore.Qt.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.show()
    QtWidgets.QApplication.processEvents()

    t0 = time.time()
    doc.recompute()
    _apply_colors(pending_colors)
    dlg.close()
    FreeCAD.Console.PrintMessage(
        f"[GDS] Tessellierung: {time.time()-t0:.1f}s\n"
    )


# ── Post-Import: PIN-Instances, Gruppe ────────────────────────────────────────

def _post_import(doc, gds_path, ihp_map, selected_layers,
                 auto_pin_contacts, before_objs):
    """
    Nach dem eigentlichen Import:
    - GDS-Zellen namens "pin" als flache 2D-Shapes einblenden
    - Auto-PIN-Erkennung (optional)
    - GDS_Die-Gruppe erstellen
    Gibt cp_count zurück (0 wenn auto_pin_contacts deaktiviert).
    """
    # GDS-Zellen "pin"
    pin_shp = Core_Functionality.load_pin_cell_shapes(gds_path)
    if pin_shp:
        po = doc.addObject("Part::Feature", "GDS_Pin_Instances")
        po.Shape = pin_shp
        po.ViewObject.ShapeColor   = (0.20, 0.80, 0.20)
        po.ViewObject.LineColor    = (0.10, 0.50, 0.10)
        po.ViewObject.Transparency = 30
        FreeCAD.Console.PrintMessage("GDS pin-Zellen importiert.\n")

    cp_count = 0
    if auto_pin_contacts:
        cp_count = Core_Functionality.import_pin_pads_as_contacts(
            gds_path, ihp_map, doc,
            selected_layers=selected_layers, top_n=3,
        )

    # GDS_Die Gruppe
    grp = doc.addObject("App::DocumentObjectGroup", "GDS_Die")
    grp.Label = "GDS_Die"
    for o in list(doc.Objects):
        if o.Name not in before_objs and o.Name != grp.Name:
            grp.addObject(o)

    return cp_count


def _apply_performance_mode(doc, pending_colors):
    """Performance-Modus nach Import; Farben danach neu setzen."""
    try:
        from gds.TogglePerformanceModeCommand import apply_performance_mode
        apply_performance_mode(doc)
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"[GDS] Performance-Modus: {e}\n")
    _apply_colors(pending_colors)   # nach Retessellierung erneut setzen
    FreeCADGui.updateGui()
    v = FreeCADGui.activeDocument().activeView()
    if v:
        v.viewIsometric()
        v.fitAll()


# ── Interaktiver Import ───────────────────────────────────────────────────────

def load_gds_layers():
    """
    Vollständiger interaktiver Import mit Dateidialogen und Fortschrittsanzeige.
    Gibt 8-Tupel zurück: (doc, layer_objects, selected_layers, unique_colors,
                          gds_path, lyp_path, options, map_path)
    """
    from ui.LayerSelector import LayerSelector

    try:
        gds_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "GDS-Datei wählen", "", "GDS Files (*.gds *.GDS)"
        )
        if not gds_path or not os.path.exists(gds_path):
            QtWidgets.QMessageBox.critical(None, "Fehler", "GDS-Datei nicht gefunden.")
            return (None,) * 8

        # ── Technologie-Konfiguration ──────────────────────────────────────
        from core.TechConfig import tech_config

        if not tech_config.is_configured():
            msg = QtWidgets.QMessageBox(None)
            msg.setWindowTitle("Technologie-Konfiguration")
            msg.setText("Kein Technologie-Profil konfiguriert.")
            msg.setInformativeText(
                "Eingebaute IHP SG13G2-Konfiguration verwenden "
                "oder Dateien manuell wählen?"
            )
            btn_std = msg.addButton("Standard (IHP SG13G2)",
                                    QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            msg.addButton("Manuell wählen",
                          QtWidgets.QMessageBox.ButtonRole.ActionRole)
            msg.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
            msg.setDefaultButton(btn_std)
            msg.exec_()
            clicked = msg.clickedButton()
            if clicked is None or clicked == msg.button(
                    QtWidgets.QMessageBox.StandardButton.Cancel):
                return (None,) * 8
            if clicked == btn_std:
                tech_config.apply_builtin_to_local()

        lyp_path = _resolve_tech_file(tech_config, "lyp", "LYP-Datei wählen",
                                      "LYP Files (*.lyp *.LYP)")
        if lyp_path is None:
            return (None,) * 8

        map_path = _resolve_tech_file(tech_config, "map", "IHP MAP wählen (optional)",
                                      "MAP Files (*.map *.MAP)", optional=True)
        ihp_map  = Core_Functionality.parse_map(map_path) if map_path else {}

        xml_path = _resolve_tech_file(tech_config, "xml", "Stackup XML wählen (optional)",
                                      "XML Files (*.xml *.XML)", optional=True)
        stackup_data = Core_Functionality.parse_stackup_xml(xml_path) if xml_path else {}

        layers_with_colors = Core_Functionality.parse_lyp(lyp_path)
        if not layers_with_colors:
            QtWidgets.QMessageBox.critical(None, "Fehler", "Keine Layer in LYP gefunden.")
            return (None,) * 8

        layers, unique_colors = layers_with_colors
        gds_layers  = Core_Functionality.get_gds_layer(gds_path)
        poly_counts = Core_Functionality.estimate_polygon_counts(gds_path)

        filtered_layers = [l for l in layers
                           if (l.get("layer_id", 0), l.get("datatype", 0)) in gds_layers]
        if not filtered_layers:
            QtWidgets.QMessageBox.warning(None, "Warnung",
                                          "Keine übereinstimmenden Layer in GDS und LYP.")
            return (None,) * 8

        doc = FreeCAD.newDocument("GDSII_Document")
        pp  = _setup_property_panel(doc, ihp_map, map_path, gds_path,
                                     lyp_path, filtered_layers, unique_colors)

        # ── Layer-Auswahl-Dialog ───────────────────────────────────────────
        dialog = LayerSelector(filtered_layers, options=pp.options,
                               ihp_map=ihp_map, poly_counts=poly_counts)
        if not dialog.exec_():
            QtWidgets.QMessageBox.information(None, "Abgebrochen",
                                              "Layer-Auswahl abgebrochen.")
            return (None,) * 8

        # dialog.layers      = alle verfügbaren Layer (LYP ∩ GDS)
        # dialog.selected_layers = die vom Nutzer angehakten (sofort laden)
        # Wenn nichts angehakt: LOD-Manager lädt alles lazy → kein Fehler
        all_avail_layers = dialog.layers          # vollständige Liste
        immediate_layers = dialog.selected_layers  # sofort-Import-Teilmenge
        options          = dialog.options

        pp.options             = dict(options)
        pp.options["xml_path"] = xml_path

        # ── Import-Parameter ableiten ──────────────────────────────────────
        # all_avail_layers → LODManager (kennt alle)
        # layers_to_load   → load_gds() (nur was sofort gebraucht wird)
        load_kwargs, aux = build_lod_import_params(
            all_avail_layers, ihp_map, stackup_data, options
        )
        # Überschreibe die automatisch berechnete Sofort-Liste mit der
        # Nutzerauswahl — er kann gezielt mehr oder weniger sofort laden
        if immediate_layers:
            layers_to_load = immediate_layers
        else:
            layers_to_load = aux["layers_to_load"]  # Fallback: Kontakt-Layer

        before_objs = {o.Name for o in doc.Objects}

        try:
            doc.openTransaction("LOD GDS Import")
        except Exception:
            pass

        shapes, cancelled = _run_import(gds_path, layers_to_load, load_kwargs)

        if cancelled:
            QtWidgets.QMessageBox.information(None, "Abgebrochen", "Import abgebrochen.")
            return (None,) * 8
        if not shapes:
            QtWidgets.QMessageBox.warning(None, "Warnung", "Keine Shapes gefunden.")
            return (None,) * 8

        # XY-Ausdehnung aus dem (nicht dargestellten) Body-Solid für LODManager
        _body = next((s for s in shapes if s.get("is_body_solid")), None)
        if _body:
            try:
                bb = _body["shape"].BoundBox
                aux["chip_xy"] = (bb.XMin, bb.YMin, bb.XMax, bb.YMax)
            except Exception:
                pass

        layer_objects, pending_colors = _populate_document(
            doc, shapes, layers_to_load, ihp_map,
            aux["match_klayout"], aux["highlight_bondable"], aux["mesh_3d"],
        )

        try:
            doc.commitTransaction()
        except Exception:
            pass

        _run_render(doc, layer_objects, pending_colors)
        pp.update_properties(layers_to_load, unique_colors, layer_objects)

        cp_count = _post_import(doc, gds_path, ihp_map, layers_to_load,
                                aux["auto_pin_contacts"], before_objs)

        if aux["auto_pin_contacts"]:
            if cp_count:
                QtWidgets.QMessageBox.information(
                    None, "Auto-PIN",
                    f"{cp_count} Kontaktpunkte auf PIN-Layern erstellt.\n"
                    "Bereit für Wire-Bonding.",
                )
            else:
                QtWidgets.QMessageBox.warning(
                    None, "Auto-PIN",
                    "Keine PIN-Pads gefunden.\n"
                    "Tipp: IHP .map-Datei laden für beste Ergebnisse.",
                )

        # LOD-Manager starten — registriert sich am Dokument und wartet auf
        # Promote-Anfragen aus dem DetailLayerPanel.
        _start_lod_manager(doc, gds_path, aux)

        _apply_performance_mode(doc, pending_colors)

        return (doc, layer_objects, all_avail_layers, unique_colors,
                gds_path, lyp_path, options, map_path)

    except Exception as e:
        import traceback
        FreeCAD.Console.PrintError(
            f"[GDSCommand] Fehler: {e}\n{traceback.format_exc()}\n"
        )
        QtWidgets.QMessageBox.critical(None, "Fehler", str(e))
        return (None,) * 8


def _start_lod_manager(doc, gds_path, aux):
    """Erstellt den LOD-Manager und registriert ihn in der globalen Registry."""
    try:
        from ui.LODManager import LODManager, register_lod_manager
        mgr = LODManager(doc, gds_path, aux)
        register_lod_manager(doc, mgr)
        FreeCAD.Console.PrintMessage("[LOD] Manager gestartet.\n")
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"[LOD] Manager-Fehler: {e}\n")


# ── FreeCAD-Befehl ────────────────────────────────────────────────────────────

class GDSCommand:
    def GetResources(self):
        return {
            "MenuText": "GDSII laden",
            "ToolTip":  "GDS-Datei importieren (LOD: Bonding-Layer sofort, Rest lazy)",
            "Pixmap":   get_icon("Load GDS.png"),
        }

    def Activated(self):
        result = load_gds_layers()
        if result and result[0]:
            doc, layer_objects, all_avail_layers, unique_colors, \
                gds_path, lyp_path, options, map_path = result
            session_manager.record_action("gds_import", {
                "gds_path":        gds_path,
                "lyp_path":        lyp_path,
                "map_path":        map_path,
                "selected_layers": all_avail_layers,
                "options":         options,
            })
            QtWidgets.QMessageBox.information(
                None, "Fertig",
                "GDS importiert — Routing-Layer können im Detail Layer Panel\n"
                "per Klick nachgeladen werden.",
                QtWidgets.QMessageBox.Ok,
            )

    def IsActive(self):
        return True


import FreeCADGui
FreeCADGui.addCommand("GDSCommand", GDSCommand())


# ── Session-Replay (kein Dialog) ──────────────────────────────────────────────

def load_gds_with_params(gds_path, lyp_path, map_path, selected_layers, options):
    """
    Importiert GDS ohne Dialoge (Session-Replay).
    Gleicher 8-Tupel-Rückgabewert wie load_gds_layers().
    """
    ihp_map = Core_Functionality.parse_map(map_path) if map_path else {}

    layers_with_colors = Core_Functionality.parse_lyp(lyp_path)
    if not layers_with_colors:
        FreeCAD.Console.PrintError("load_gds_with_params: LYP-Fehler.\n")
        return (None,) * 8
    layers, unique_colors = layers_with_colors

    saved_keys = {(l["layer_id"], l["datatype"]) for l in selected_layers}
    filtered   = [l for l in layers
                  if (l.get("layer_id", 0), l.get("datatype", 0)) in saved_keys]
    if not filtered:
        filtered = list(selected_layers)

    xml_path     = options.get("xml_path")
    stackup_data = Core_Functionality.parse_stackup_xml(xml_path) if xml_path else {}

    doc = FreeCAD.newDocument("GDSII_Document")
    pp  = _setup_property_panel(doc, ihp_map, map_path, gds_path,
                                 lyp_path, filtered, unique_colors)
    pp.options = dict(options)

    load_kwargs, aux = build_lod_import_params(
        filtered, ihp_map, stackup_data, options
    )
    layers_to_load = aux["layers_to_load"]

    try:
        doc.openTransaction("Session Replay: GDS Import")
    except Exception:
        pass

    n_workers = max(1, (os.cpu_count() or 2) - 1)
    shapes = Core_Functionality.load_gds(
        gds_path, layers_to_load,
        parallel_workers=n_workers,
        **load_kwargs,
    )

    if not shapes:
        FreeCAD.Console.PrintWarning("load_gds_with_params: keine Shapes.\n")
        return (None,) * 8

    layer_objects, pending_colors = _populate_document(
        doc, shapes, layers_to_load, ihp_map,
        aux["match_klayout"], aux["highlight_bondable"], aux["mesh_3d"],
    )

    try:
        doc.commitTransaction()
    except Exception:
        pass

    doc.recompute()
    _apply_colors(pending_colors)
    pp.update_properties(layers_to_load, unique_colors, layer_objects)

    before_objs = set()
    _post_import(doc, gds_path, ihp_map, layers_to_load,
                 aux["auto_pin_contacts"], before_objs)

    _start_lod_manager(doc, gds_path, aux)

    FreeCADGui.setActiveDocument(doc.Name)
    FreeCADGui.updateGui()
    v = FreeCADGui.activeDocument().activeView()
    if v:
        v.viewIsometric()
        v.fitAll()

    return doc, layer_objects, filtered, unique_colors, gds_path, lyp_path, options, map_path
