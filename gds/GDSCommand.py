# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
GDSCommand.py
=============
FreeCAD commands for GDS import.

Interactive path: load_gds_layers() — shows dialogs, uses
core.lod_import.build_lod_import_params() for parameter derivation.

Default import mode is LOD (Level of Detail):
  • Immediately visible: PIN-/Bond-Layer (3D) + IC_Body_Solid (BBox cuboid)
  • Lazy:                Routing layers on demand via the LOD manager
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


# ── Colour resolution ─────────────────────────────────────────────────────────

def _layer_colors(layer: dict, ihp_map: dict,
                  match_klayout: bool, highlight_bondable: bool,
                  exact_geometry: bool = False):
    """Returns (shape_rgb, line_rgb, transparency) for a layer."""
    lid = layer.get("layer_id", 0)
    dt  = layer.get("datatype",  0)
    m   = ihp_map.get((lid, dt))
    types = m["edi_types"] if m else set()

    if exact_geometry:
        # Straight from the .lyp, with no workbench opinion applied. The
        # bondable highlight below repaints pad layers gold, which is useful
        # for wire bonding and is the one thing that makes an otherwise
        # KLayout-faithful view disagree with KLayout on colour.
        return (hex_to_rgb(layer.get("fill-color",  "#FFFFFF")),
                hex_to_rgb(layer.get("frame-color", "#000000")),
                0)

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


# ── Document construction ─────────────────────────────────────────────────────

def _populate_document(doc, shapes, filtered_layers, ihp_map,
                       match_klayout, highlight_bondable, mesh_3d,
                       exact_geometry=False):
    """
    Creates FreeCAD objects for all shapes.
    Returns (layer_objects, pending_colors).
    """
    layer_objects  = {}
    pending_colors = []

    # IC_Body_Solid is no longer created — LODManager takes over the
    # visual role with per-layer placeholders.

    for layer in filtered_layers:
        lid  = layer.get("layer_id", 0)
        dt   = layer.get("datatype",  0)
        name = layer.get("name", "Unknown")

        shp = next((s for s in shapes
                    if s["layer_id"] == lid and s["datatype"] == dt), None)
        if not shp:
            continue

        sr, lr, tr = _layer_colors(layer, ihp_map, match_klayout,
                                    highlight_bondable, exact_geometry)

        if shp.get("is_mesh"):
            obj = doc.addObject("Mesh::Feature", f"Layer_{name}_{lid}")
            obj.Mesh = shp["mesh"]
        else:
            obj = doc.addObject("Part::Feature", f"Layer_{name}_{lid}")
            obj.Shape = shp["shape"]

        # Store LOD metadata for the manager
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


# ── Resolve technology file ───────────────────────────────────────────────────

def _resolve_tech_file(tech_config, kind: str, title: str,
                        file_filter: str, optional: bool = False):
    """Returns path from tech_config or file dialog."""
    has_fn = getattr(tech_config, f"has_{kind}")
    get_fn = getattr(tech_config, f"get_{kind}")

    if has_fn():
        p = get_fn()
        FreeCAD.Console.PrintMessage(f"TechConfig: {kind.upper()}  {p}\n")
        return p

    p, _ = QtWidgets.QFileDialog.getOpenFileName(None, title, "", file_filter)
    if not p:
        if optional:
            FreeCAD.Console.PrintWarning(f"No {kind.upper()} selected — skipping.\n")
            return None
        QtWidgets.QMessageBox.critical(None, "Error",
                                       f"{kind.upper()} file not found.")
        return None

    kw = {"map_": p} if kind == "map" else {kind: p}
    tech_config.set_local(**kw)
    return p


# ── Import with progress display ──────────────────────────────────────────────

def _run_import(gds_path, selected_layers, load_kwargs):
    """
    Runs Core_Functionality.load_gds() with a progress dialog.
    Returns (shapes, cancelled).
    """
    dlg = QtWidgets.QProgressDialog(
        "Importing GDS layers…", "Cancel", 0, 0,
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
            dlg.setLabelText(f"{msg or 'Importing…'}\n{eta} remaining")
        else:
            dlg.setLabelText(msg or "Importing…")

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
    """Calls doc.recompute() with a progress dialog."""
    n = sum(len(v) for v in layer_objects.values())
    dlg = QtWidgets.QProgressDialog(
        f"FreeCAD is tessellating {n} shape(s)…\nPlease wait.",
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
        f"[GDS] Tessellation: {time.time()-t0:.1f}s\n"
    )


# ── Post-import: PIN instances, group ────────────────────────────────────────

def _add_die_body(doc, gds_path, stackup_data, options, stack_mm=None):
    """
    Build the epi + silicon slabs under the lowest drawn layer.

    A die is mostly the body below the interconnect — on SG13G2 the drawn
    stack is 14.23 um and the silicon under it is 183.75 um — and none of it
    was being modelled, so the imported object was under 8% of the real part.

    The footprint comes from the die outline (seal ring / prBoundary) rather
    than the bounding box of whatever layers happened to be loaded: in LOD
    mode that is two contact layers, which would put a substrate under only
    part of the die.
    """
    if not bool(options.get("add_die_body", True)):
        return []
    try:
        import core.substrate as substrate
        from core.chip_proxy import describe_die_footprint

        if not substrate.substrate_layers_mm(stackup_data):
            return []          # stackup declares no body; nothing to build
        outline = describe_die_footprint(gds_path)
        made = substrate.build_substrate_objects(
            doc, outline["footprint_mm"], stackup_data)

        # Fill the space between the die surface and the lowest layer this
        # layout actually uses. A PDK defines more levels than any one design
        # draws on, and that volume is oxide in the real part, not air.
        if bool(options.get("fill_dielectric_gap", True)):
            from core.tech.stackup import lowest_real_layer_z0
            lowest = lowest_real_layer_z0(stack_mm or {})
            fill = substrate.build_dielectric_fill(
                doc, outline["footprint_mm"], stackup_data, lowest)
            if fill is not None:
                made.append(fill)
        return made
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[Substrate] not built: {exc}\n")
        return []


def _post_import(doc, gds_path, ihp_map, selected_layers,
                 auto_pin_contacts, before_objs,
                 stackup_data=None, options=None, stack_mm=None):
    """
    After the actual import:
    - Display GDS cells named "pin" as flat 2D shapes
    - Auto-PIN detection (optional)
    - Build the die body (epi + substrate) below the layout
    - Create GDS_Die group
    Returns cp_count (0 if auto_pin_contacts is disabled).
    """
    # GDS cells "pin"
    pin_shp = Core_Functionality.load_pin_cell_shapes(gds_path)
    if pin_shp:
        po = doc.addObject("Part::Feature", "GDS_Pin_Instances")
        po.Shape = pin_shp
        po.ViewObject.ShapeColor   = (0.20, 0.80, 0.20)
        po.ViewObject.LineColor    = (0.10, 0.50, 0.10)
        po.ViewObject.Transparency = 30
        FreeCAD.Console.PrintMessage("GDS pin cells imported.\n")

    cp_count = 0
    if auto_pin_contacts:
        cp_count = Core_Functionality.import_pin_pads_as_contacts(
            gds_path, ihp_map, doc,
            selected_layers=selected_layers, top_n=3,
            stack_mm=stack_mm,
        )

    # Die body — built before the group is formed, so the slabs are swept
    # into GDS_Die with everything else the import produced.
    _add_die_body(doc, gds_path, stackup_data, options or {}, stack_mm)

    # GDS_Die group
    grp = doc.addObject("App::DocumentObjectGroup", "GDS_Die")
    grp.Label = "GDS_Die"
    for o in list(doc.Objects):
        if o.Name not in before_objs and o.Name != grp.Name:
            grp.addObject(o)

    return cp_count


def _apply_display_mode(doc, pending_colors, keep_via_detail=True,
                            exact_geometry=False):
    """
    Post-import display setup.

    Used to bake every layer into a fast triangulated mesh first; that
    feature was removed, so the only remaining step is whether via layers
    start as clustered blocks.
    """
    if exact_geometry:
        try:
            from gds.ToggleViaDetailCommand import set_via_detailed
            set_via_detailed(True)
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"[GDS] Via state: {e}\n")
        FreeCAD.Console.PrintMessage(
            "[GDS] Exact KLayout geometry — via blocks skipped. Still "
            "available from the Render toolbar.\n")
        return
    # VIA layers → simple outlined blocks, unless the user asked to keep via
    # detail. This step runs AFTER the geometry is built and swaps the real
    # via arrays for clustered blocks, so on its own it undoes every
    # protection applied during the build — which is exactly how an import
    # could report "VIA detail protected" and still show blocks.
    if keep_via_detail:
        # Tell the toggle where the document actually stands, or its first
        # press would compute the wrong direction and appear to do nothing.
        try:
            from gds.ToggleViaDetailCommand import set_via_detailed
            set_via_detailed(True)
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"[GDS] Via state: {e}\n")
        FreeCAD.Console.PrintMessage(
            "[GDS] Via simplify skipped — 'Keep VIA layers in full detail' is "
            "on. Press Toggle VIA Detail (Render toolbar) to cluster them "
            "into blocks; the blocks are built on demand.\n")
        return
    try:
        from gds.ToggleViaDetailCommand import apply_via_simplified
        apply_via_simplified(doc)
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"[GDS] Via simplify: {e}\n")
    _apply_colors(pending_colors)   # reapply after re-tessellation
    FreeCADGui.updateGui()
    v = FreeCADGui.activeDocument().activeView()
    if v:
        v.viewIsometric()
        v.fitAll()


# ── Interactive import ────────────────────────────────────────────────────────

def load_gds_layers():
    """
    Full interactive import with file dialogs and progress display.
    Returns an 8-tuple: (doc, layer_objects, selected_layers, unique_colors,
                         gds_path, lyp_path, options, map_path)
    """
    from ui.LayerSelector import LayerSelector

    try:
        gds_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Select GDS File", "", "GDS Files (*.gds *.GDS)"
        )
        if not gds_path or not os.path.exists(gds_path):
            QtWidgets.QMessageBox.critical(None, "Error", "GDS file not found.")
            return (None,) * 8

        # ── Technology configuration ───────────────────────────────────────
        from core.TechConfig import tech_config

        if not tech_config.is_configured():
            msg = QtWidgets.QMessageBox(None)
            msg.setWindowTitle("Technology Configuration")
            msg.setText("No technology profile configured.")
            msg.setInformativeText(
                "Use the built-in IHP SG13G2 configuration, "
                "or select files manually?"
            )
            btn_std = msg.addButton("Default (IHP SG13G2)",
                                    QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            msg.addButton("Select Manually",
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

        lyp_path = _resolve_tech_file(tech_config, "lyp", "Select LYP File",
                                      "LYP Files (*.lyp *.LYP)")
        if lyp_path is None:
            return (None,) * 8

        map_path = _resolve_tech_file(tech_config, "map", "Select IHP MAP (optional)",
                                      "MAP Files (*.map *.MAP)", optional=True)
        ihp_map  = Core_Functionality.parse_map(map_path) if map_path else {}

        xml_path = _resolve_tech_file(tech_config, "xml", "Select Stackup XML (optional)",
                                      "XML Files (*.xml *.XML)", optional=True)
        stackup_data = Core_Functionality.parse_stackup_xml(xml_path) if xml_path else {}

        layers_with_colors = Core_Functionality.parse_lyp(lyp_path)
        if not layers_with_colors:
            QtWidgets.QMessageBox.critical(None, "Error", "No layers found in LYP.")
            return (None,) * 8

        layers, unique_colors = layers_with_colors
        gds_layers  = Core_Functionality.get_gds_layer(gds_path)
        poly_counts = Core_Functionality.estimate_polygon_counts(gds_path)

        filtered_layers = [l for l in layers
                           if (l.get("layer_id", 0), l.get("datatype", 0)) in gds_layers]
        if not filtered_layers:
            QtWidgets.QMessageBox.warning(None, "Warning",
                                          "No matching layers between GDS and LYP.")
            return (None,) * 8

        doc = FreeCAD.newDocument("GDSII_Document")
        pp  = _setup_property_panel(doc, ihp_map, map_path, gds_path,
                                     lyp_path, filtered_layers, unique_colors)

        # ── Layer selection dialog ────────────────────────────────────────
        dialog = LayerSelector(filtered_layers, options=pp.options,
                               ihp_map=ihp_map, poly_counts=poly_counts)
        if not dialog.exec_():
            QtWidgets.QMessageBox.information(None, "Cancelled",
                                              "Layer selection cancelled.")
            return (None,) * 8

        # dialog.layers      = all available layers (LYP ∩ GDS)
        # dialog.selected_layers = those ticked by the user (load immediately)
        # If nothing ticked: LOD manager loads everything lazily → no error
        all_avail_layers = dialog.layers          # complete list
        immediate_layers = dialog.selected_layers  # immediate-import subset
        options          = dialog.options

        pp.options             = dict(options)
        pp.options["xml_path"] = xml_path

        # ── Derive import parameters ──────────────────────────────────────
        # all_avail_layers → LODManager (knows all layers)
        # layers_to_load   → load_gds() (only what is needed immediately)
        load_kwargs, aux = build_lod_import_params(
            all_avail_layers, ihp_map, stackup_data, options
        )
        # Override the automatically computed immediate-load list with
        # the user's selection — they may choose to load more or less right away
        if immediate_layers:
            layers_to_load = immediate_layers
        else:
            layers_to_load = aux["layers_to_load"]  # Fallback: contact layers

        before_objs = {o.Name for o in doc.Objects}

        try:
            doc.openTransaction("LOD GDS Import")
        except Exception:
            pass

        shapes, cancelled = _run_import(gds_path, layers_to_load, load_kwargs)

        if cancelled:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Import cancelled.")
            return (None,) * 8
        if not shapes:
            QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found.")
            return (None,) * 8

        # XY extent from the (not displayed) body solid for the LODManager
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
            aux.get("exact_geometry", False),
        )

        try:
            doc.commitTransaction()
        except Exception:
            pass

        _run_render(doc, layer_objects, pending_colors)
        pp.update_properties(layers_to_load, unique_colors, layer_objects)

        cp_count = _post_import(doc, gds_path, ihp_map, layers_to_load,
                                aux["auto_pin_contacts"], before_objs,
                                stackup_data=stackup_data, options=options,
                                stack_mm=aux.get("stack_mm"))

        if aux["auto_pin_contacts"]:
            if cp_count:
                QtWidgets.QMessageBox.information(
                    None, "Auto-PIN",
                    f"{cp_count} contact point(s) created on PIN layers.\n"
                    "Ready for wire bonding.",
                )
            else:
                QtWidgets.QMessageBox.warning(
                    None, "Auto-PIN",
                    "No PIN pads found.\n"
                    "Tip: load an IHP .map file for best results.",
                )

        # Stash the raw source-file inputs on aux so the LOD manager's
        # workbench-state save-provider can persist and reconstruct it
        # after a document save/reopen (see ui/LODManager.py).
        aux["lyp_path"] = lyp_path
        aux["map_path"] = map_path
        aux["options"]  = dict(options)

        # Start LOD manager — registers itself on the document and waits for
        # promote requests from the DetailLayerPanel.
        _start_lod_manager(doc, gds_path, aux)

        _apply_display_mode(
            doc, pending_colors,
            keep_via_detail=bool(options.get("keep_via_detail", True)),
            exact_geometry=aux.get("exact_geometry", False))

        return (doc, layer_objects, all_avail_layers, unique_colors,
                gds_path, lyp_path, options, map_path)

    except Exception as e:
        import traceback
        FreeCAD.Console.PrintError(
            f"[GDSCommand] Error: {e}\n{traceback.format_exc()}\n"
        )
        QtWidgets.QMessageBox.critical(None, "Error", str(e))
        return (None,) * 8


def _start_lod_manager(doc, gds_path, aux):
    """Creates the LOD manager and registers it in the global registry."""
    try:
        from ui.LODManager import LODManager, register_lod_manager
        mgr = LODManager(doc, gds_path, aux)
        register_lod_manager(doc, mgr)
        FreeCAD.Console.PrintMessage("[LOD] Manager started.\n")
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"[LOD] Manager error: {e}\n")


# ── FreeCAD command ───────────────────────────────────────────────────────────

class GDSCommand:
    def GetResources(self):
        return {
            "MenuText": "Load GDSII",
            "ToolTip":  "Import GDS file (LOD: bonding layers immediate, rest lazy)",
            "Pixmap":   get_icon("Load GDS.png"),
        }

    def Activated(self):
        result = load_gds_layers()
        if result and result[0]:
            QtWidgets.QMessageBox.information(
                None, "Done",
                "GDS imported — routing layers can be loaded on demand\n"
                "via the Detail Layer Panel.",
                QtWidgets.QMessageBox.Ok,
            )

    def IsActive(self):
        return True


import FreeCADGui
if FreeCAD.GuiUp:
    FreeCADGui.addCommand("GDSCommand", GDSCommand())
