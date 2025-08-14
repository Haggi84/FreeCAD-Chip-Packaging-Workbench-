from PySide2 import QtWidgets, QtCore
import FreeCAD
import FreeCADGui
import LeadframeCommand
import GDSCommand
import mymodule

class TransformDialog(QtWidgets.QDialog):
    """
    Lets the user control orientation and placement of the die relative to the leadframe.
    """
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


def _bbox_from_shapes(shapes):
    """
    Compute (xmin, ymin, xmax, ymax) from a list of (shape, frame_hex, fill_hex).
    All units are mm.
    """
    if not shapes:
        return None
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for shape, _, _ in shapes:
        bb = shape.BoundBox
        xmin = min(xmin, bb.XMin)
        ymin = min(ymin, bb.YMin)
        xmax = max(xmax, bb.XMax)
        ymax = max(ymax, bb.YMax)
    return xmin, ymin, xmax, ymax


class LayeronLeadframe:
    def GetResources(self):
        return {
            "MenuText": "Layer on Leadframe",
            "ToolTip": "Configure layers on leadframe",
            "Pixmap": ""
        }

    def Activated(self):
        try:
            # Step 1: Let the user pick GDS/LYP and layers (preview shown)
            result = GDSCommand.load_gds_layers()
            if not result or result[0] is None:
                FreeCAD.Console.PrintError("❌ Failed to load GDS layers.\n")
                return

            preview_doc, _layer_objects, selected_layers, _unique_colors, gds_path, _lyp_path = result
            if not selected_layers or not gds_path:
                FreeCAD.Console.PrintError("❌ Missing selected layers or GDS path.\n")
                return

            # Step 2: Leadframe configuration
            config = LeadframeCommand.configure_leadframe()
            if not config:
                FreeCAD.Console.PrintError("❌ Leadframe configuration cancelled.\n")
                return

            frame_length = config["frame_length"]  # mm (X span in our model)
            frame_width = config["frame_width"]    # mm (Y span)

            # Step 3: Optional transform options (rotation/mirror/offset/auto-fit)
            tdlg = TransformDialog()
            if tdlg.exec_() != QtWidgets.QDialog.Accepted:
                FreeCAD.Console.PrintMessage("ℹ Transform dialog cancelled by user.\n")
                return
            opts = tdlg.get_opts()

            # Step 4: First pass — import with base scale only to measure bbox
            first_transform = {
                "scale": None,        # derive from GDS (mm per user unit)
                "rot_deg": opts["rot_deg"],
                "mirror_y": opts["mirror_y"],
                "tx": 0.0,
                "ty": 0.0,
                "z_thickness": 0.03
            }
            tmp_shapes = mymodule.load_gds(gds_path, selected_layers, first_transform)
            if not tmp_shapes:
                QtWidgets.QMessageBox.warning(None, "Warning", "No shapes produced during measurement pass.")
                return

            bb = _bbox_from_shapes(tmp_shapes)
            if not bb:
                QtWidgets.QMessageBox.warning(None, "Warning", "Failed to compute bounding box for GDS shapes.")
                return
            xmin, ymin, xmax, ymax = bb
            die_w = max(0.0, xmax - xmin)
            die_h = max(0.0, ymax - ymin)

            # Step 5: Compute final transform
            base_scale = mymodule.derive_base_scale_mm(gds_path)  # mm per user unit
            final_scale = base_scale

            if opts["auto_fit"] and die_w > 0 and die_h > 0:
                # Fit into frame window with a margin
                margin = max(0.0, opts["margin_pct"]) / 100.0
                fit_w = frame_length * (1.0 - margin)
                fit_h = frame_width * (1.0 - margin)
                fit_factor = min(fit_w / die_w, fit_h / die_h)
                if fit_factor < 1.0:
                    final_scale = base_scale * fit_factor

            # Center to origin (leadframe is centered around 0,0)
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

            # Step 6: Final import with transform, into a fresh document
            doc = FreeCAD.newDocument("Leadframe_Assembly")

            final_shapes = mymodule.load_gds(gds_path, selected_layers, final_transform)
            if not final_shapes:
                QtWidgets.QMessageBox.warning(None, "Warning", "No shapes found for the selected layers (final pass).")
                return

            # Build FC objects grouped by layer (using color match as before)
            layer_objects = {}
            for layer in selected_layers:
                layer_id = layer.get("layer_id", 0)
                datatype = layer.get("datatype", 0)
                layer_name = layer.get("name", "Unnamed")
                frame_hex = layer.get("frame-color", "#000000")
                fill_hex = layer.get("fill-color", "#FFFFFF")
                layer_objects[layer_id] = []
                idx = 0
                for shape, s_frame, s_fill in final_shapes:
                    if (s_frame, s_fill) == (frame_hex, fill_hex):
                        obj = doc.addObject("Part::Feature", f"Layer_{layer_name}_{layer_id}_{datatype}_{idx}")
                        obj.Shape = shape
                        obj.ViewObject.ShapeColor = _hex_to_rgb(fill_hex)
                        obj.ViewObject.LineColor = _hex_to_rgb(frame_hex)
                        layer_objects[layer_id].append(obj)
                        idx += 1

            # Step 7: Create the leadframe into the same document and place die above it
            LeadframeCommand.create_leadframe(config, doc, layer_objects)

            doc.recompute()
            FreeCADGui.activeDocument().activeView().viewIsometric()
            FreeCADGui.SendMsgToActiveView("ViewFit")

            QtWidgets.QMessageBox.information(None, "Success", "Leadframe created and GDS aligned successfully.")

        except Exception as e:
            FreeCAD.Console.PrintError(f"❌ An error occurred: {str(e)}\n")
            QtWidgets.QMessageBox.critical(None, "Error", f"An error occurred while creating the leadframe: {str(e)}")

    def IsActive(self):
        return True


def _hex_to_rgb(hex_color):
    """Utility for this module (0..1 floats)."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


import FreeCADGui
FreeCADGui.addCommand("LayeronLeadframe", LayeronLeadframe())
