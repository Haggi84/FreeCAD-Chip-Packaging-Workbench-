from PySide2 import QtWidgets
import os
import FreeCAD
import FreeCADGui

import LeadframeCommand
import mymodule
from GDSCommand import style_for_material, load_gds_layers


def hex_to_rgb(hex_color):
    hex_color = (hex_color or "#000000").lstrip('#')
    return tuple(int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def is_bondable(types) -> bool:
    if not types:
        return False
    T = {t.upper() for t in types}
    return any(t in T for t in ("PIN", "LEFPIN", "BUMP", "PAD"))


class TransformDialog(QtWidgets.QDialog):
    """Transform options for placing the die relative to the leadframe."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transform Options")
        self.setMinimumWidth(320)

        form = QtWidgets.QFormLayout()

        self.auto_fit = QtWidgets.QCheckBox("Auto-fit die into frame opening (keep aspect)")
        self.auto_fit.setChecked(True)
        form.addRow(self.auto_fit)

        self.margin_pct = QtWidgets.QDoubleSpinBox()
        self.margin_pct.setRange(0.0, 40.0)
        self.margin_pct.setSingleStep(1.0)
        self.margin_pct.setSuffix(" %")
        self.margin_pct.setValue(10.0)
        form.addRow("Fit Margin:", self.margin_pct)

        self.rot_deg = QtWidgets.QDoubleSpinBox()
        self.rot_deg.setRange(-360.0, 360.0)
        self.rot_deg.setSingleStep(1.0)
        self.rot_deg.setSuffix(" °")
        self.rot_deg.setValue(0.0)
        form.addRow("Rotation:", self.rot_deg)

        self.mirror_y = QtWidgets.QCheckBox("Mirror in Y (flip top/bottom)")
        self.mirror_y.setChecked(False)
        form.addRow(self.mirror_y)

        self.tx = QtWidgets.QDoubleSpinBox()
        self.tx.setRange(-10000.0, 10000.0)
        self.tx.setDecimals(4)
        self.tx.setSingleStep(0.1)
        self.tx.setSuffix(" mm")
        self.tx.setValue(0.0)
        form.addRow("Offset X:", self.tx)

        self.ty = QtWidgets.QDoubleSpinBox()
        self.ty.setRange(-10000.0, 10000.0)
        self.ty.setDecimals(4)
        self.ty.setSingleStep(0.1)
        self.ty.setSuffix(" mm")
        self.ty.setValue(0.0)
        form.addRow("Offset Y:", self.ty)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def get_opts(self):
        return {
            "auto_fit": self.auto_fit.isChecked(),
            "margin_pct": self.margin_pct.value(),
            "rot_deg": self.rot_deg.value(),
            "mirror_y": self.mirror_y.isChecked(),
            "tx": self.tx.value(),
            "ty": self.ty.value()
        }


def _bbox_from_entries(entries):
    if not entries:
        return None
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for entry in entries:
        bb = entry["shape"].BoundBox
        xmin = min(xmin, bb.XMin)
        ymin = min(ymin, bb.YMin)
        xmax = max(xmax, bb.XMax)
        ymax = max(ymax, bb.YMax)
    return xmin, ymin, xmax, ymax


class LayeronLeadframe:
    def GetResources(self):
        return {
            "MenuText": "Layer on Leadframe",
            "ToolTip": "Place GDS layers on a leadframe with correct 3D stacking; KLayout-view option supported",
            "Pixmap": ""
        }

    def Activated(self):
        try:
            # Pick + preview (fast 2D) -> returns options as 7th item
            result = load_gds_layers()
            if not result or result[0] is None:
                FreeCAD.Console.PrintError("❌ Failed to load GDS layers.\n")
                return

            # backward compatible unpacking
            if len(result) >= 7:
                preview_doc, _, selected_layers, _, gds_path, _, options = result
            else:
                preview_doc, _, selected_layers, _, gds_path, _ = result
                options = {"match_klayout": False, "highlight_bondable": True}

            if not selected_layers or not gds_path:
                FreeCAD.Console.PrintError("❌ Missing selected layers or GDS path.\n")
                return

            # IHP mapping (try default next to this module)
            default_map = os.path.join(os.path.dirname(__file__), "sg13g2.map")
            ihp_map = mymodule.parse_ihp_map(default_map) if os.path.exists(default_map) else {}

            # Leadframe configuration
            config = LeadframeCommand.configure_leadframe()
            if not config:
                FreeCAD.Console.PrintError("❌ Leadframe configuration cancelled.\n")
                return

            frame_length = config["frame_length"]
            frame_width  = config["frame_width"]

            # Transform options
            tdlg = TransformDialog()
            if tdlg.exec_() != QtWidgets.QDialog.Accepted:
                FreeCAD.Console.PrintMessage("ℹ Transform dialog cancelled by user.\n")
                return
            opts = tdlg.get_opts()

            # First pass — measure bbox at base scale
            first_transform = {
                "scale": None,
                "rot_deg": opts["rot_deg"],
                "mirror_y": opts["mirror_y"],
                "tx": 0.0,
                "ty": 0.0,
                "z_thickness": 0.03
            }
            tmp_entries = mymodule.load_gds_fast(
                gds_path, selected_layers, first_transform,
                preview_2d=False, compound_per_layer=True,
                min_area_mm2=0.0004, decimate_tol_mm=0.002, skip_fill_datatype=True
            )
            if not tmp_entries:
                QtWidgets.QMessageBox.warning(None, "Warning", "No shapes produced during measurement pass.")
                return

            bb = _bbox_from_entries(tmp_entries)
            if not bb:
                QtWidgets.QMessageBox.warning(None, "Warning", "Failed to compute bounding box for GDS shapes.")
                return
            xmin, ymin, xmax, ymax = bb
            die_w = max(0.0, xmax - xmin)
            die_h = max(0.0, ymax - ymin)

            # Auto-fit scale
            base_scale = mymodule.derive_base_scale_mm(gds_path)
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
            stack = mymodule.build_stack_mm(selected_layers, ihp_map, ild_um=mymodule.ILD_SPACING_UM)

            # 3D Import parameters derived from options
            match_klayout = bool(options.get("match_klayout", False))
            highlight_bondable = bool(options.get("highlight_bondable", True))
            skip_fill = not match_klayout
            min_area = 0.0 if match_klayout else 0.0004
            decimate = 0.0 if match_klayout else 0.002

            # Final import with material styles / LYP colors and correct Z stacking
            doc = FreeCAD.newDocument("Leadframe_Assembly")
            try:
                doc.openTransaction("Final Import (stacked)")
            except Exception:
                pass

            entries = mymodule.load_gds_fast(
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
            if not entries:
                QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found for the selected layers (final pass).")
                return

            entry_map = {(e["layer_id"], e["datatype"]): e for e in entries}

            layer_objects = {}
            for layer in selected_layers:
                lid = layer.get("layer_id", 0)
                dt  = layer.get("datatype", 0)
                lname = layer.get("name", "Unnamed")

                map_entry = ihp_map.get((lid, dt))
                edi_name  = map_entry["edi_name"] if map_entry else ""
                types     = map_entry["edi_types"] if map_entry else set()

                # color decision
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
                        # neutralize highlight
                        shape_rgb = hex_to_rgb(layer.get("fill-color", "#FFFFFF"))
                        line_rgb  = hex_to_rgb(layer.get("frame-color", "#000000"))
                        tr = 0

                entry = entry_map.get((lid, dt))
                if not entry:
                    continue

                obj = doc.addObject("Part::Feature", f"Layer_{lname}_{lid}_{dt}")
                obj.Shape = entry["shape"]
                obj.ViewObject.ShapeColor = shape_rgb
                obj.ViewObject.LineColor  = line_rgb
                obj.ViewObject.Transparency = tr

                if not hasattr(obj, "Bondable"):
                    obj.addProperty("App::PropertyBool", "Bondable", "Technology", "Can be connected to the leadframe")
                obj.Bondable = is_bondable(types) if highlight_bondable else False

                layer_objects.setdefault(lid, []).append(obj)

            # Create the leadframe geometry in the same doc
            LeadframeCommand.create_leadframe(config, doc, layer_objects)

            try:
                doc.commitTransaction()
            except Exception:
                pass

            doc.recompute()
            FreeCADGui.activeDocument().activeView().viewIsometric()
            FreeCADGui.SendMsgToActiveView("ViewFit")
            QtWidgets.QMessageBox.information(None, "Success", "GDS stacked with correct heights; imported with chosen color mode.")

        except Exception as e:
            FreeCAD.Console.PrintError(f"❌ An error occurred: {str(e)}\n")
            QtWidgets.QMessageBox.critical(None, "Error", f"An error occurred while creating the leadframe: {str(e)}")

    def IsActive(self):
        return True


FreeCADGui.addCommand("LayeronLeadframe", LayeronLeadframe())
