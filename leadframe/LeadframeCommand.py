from PySide2 import QtWidgets
import FreeCAD, FreeCADGui, os, sys

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)
from Get_Path import get_icon

from core.leadframe import build_leadframe

def create_leadframe(config, doc=None, gds_objects=None):
    return build_leadframe(config, doc=doc, gds_objects=gds_objects)

def configure_leadframe():
    from leadframe.LeadframeConfigurator import LeadframeConfigurator
    
    """
    Open the leadframe configuration dialog and create a leadframe based on user input.

    Returns: configuration
    """
    dialog = LeadframeConfigurator()
    if dialog.exec_():
        return dialog.get_config()
    return None


class LeadframeCommand:
    def GetResources(self):
        return {
            "MenuText": "Leadframe Configurator",
            "ToolTip": "Configure and generate a leadframe geometry",
            "Pixmap": get_icon("Leadframe_Configurator.png")
        }

    def Activated(self):
        config = configure_leadframe()
        if config:
            create_leadframe(config)
            QtWidgets.QMessageBox.information(None, "Success", f"Leadframe created:\n{config}")
        else:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Leadframe configuration cancelled.")

    def IsActive(self):
        return True

FreeCADGui.addCommand('LeadframeCommand', LeadframeCommand())


def center_leadframe_on_gds(doc=None):
    """
    Move all leadframe objects so their XY centre aligns with the combined
    bounding-box centre of all imported GDS layer objects.

    Returns (success: bool, message: str).
    """
    from FreeCAD import Base

    doc = doc or FreeCAD.activeDocument()
    if not doc:
        return False, "No active document."

    # ── gather objects ──────────────────────────────────────────────────────
    gds_objects = [o for o in doc.Objects if o.Name.startswith("Layer_")]
    if not gds_objects:
        return False, "No GDS layers found in the document. Import a GDSII file first."

    lf_prefixes = ("LeadframeBody", "DiePaddle", "Lead_", "BGA_Ball_", "ContactPoint_")
    lf_objects = [o for o in doc.Objects
                  if any(o.Name.startswith(p) for p in lf_prefixes)]
    if not lf_objects:
        return False, "No leadframe found in the document. Create a leadframe first."

    # ── GDS world bounding box centre (XY only) ─────────────────────────────
    x_min = x_max = y_min = y_max = None
    for o in gds_objects:
        shape = getattr(o, "Shape", None)
        if shape is None or shape.isNull():
            continue
        bb   = shape.BoundBox
        ox   = o.Placement.Base.x
        oy   = o.Placement.Base.y
        xlo, xhi = bb.XMin + ox, bb.XMax + ox
        ylo, yhi = bb.YMin + oy, bb.YMax + oy
        x_min = xlo if x_min is None else min(x_min, xlo)
        x_max = xhi if x_max is None else max(x_max, xhi)
        y_min = ylo if y_min is None else min(y_min, ylo)
        y_max = yhi if y_max is None else max(y_max, yhi)

    if x_min is None:
        return False, "GDS objects carry no geometry."

    gds_cx = (x_min + x_max) / 2.0
    gds_cy = (y_min + y_max) / 2.0

    # ── current leadframe XY centre (from LeadframeBody shape + placement) ──
    lf_body = next((o for o in lf_objects if o.Name.startswith("LeadframeBody")), None)
    if lf_body is not None and not lf_body.Shape.isNull():
        bb    = lf_body.Shape.BoundBox
        lf_cx = (bb.XMin + bb.XMax) / 2.0 + lf_body.Placement.Base.x
        lf_cy = (bb.YMin + bb.YMax) / 2.0 + lf_body.Placement.Base.y
    else:
        lf_cx, lf_cy = 0.0, 0.0

    dx = gds_cx - lf_cx
    dy = gds_cy - lf_cy

    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return True, "Leadframe is already centred on the GDS."

    # ── apply translation ───────────────────────────────────────────────────
    doc.openTransaction("Center Leadframe on GDS")
    try:
        offset = Base.Vector(dx, dy, 0.0)
        for o in lf_objects:
            pl      = o.Placement
            pl.Base = pl.Base + offset
            o.Placement = pl
            # Keep the stored ContactPoint snap vector in sync
            if getattr(o, "IsContactPoint", False):
                cp = o.ContactPoint
                o.ContactPoint = Base.Vector(cp.x + dx, cp.y + dy, cp.z)
        doc.commitTransaction()
    except Exception as e:
        doc.abortTransaction()
        return False, f"Translation failed: {e}"

    doc.recompute()
    return True, f"Leadframe centred on GDS (shifted {dx:+.3f} mm X, {dy:+.3f} mm Y)."


class CenterLeadframeCommand:
    def GetResources(self):
        return {
            "MenuText": "Center Leadframe on GDS",
            "ToolTip":  "Move the leadframe so its centre aligns with the imported GDS bounding box",
            "Pixmap":   get_icon("Center_Leadframe.svg"),
        }

    def Activated(self):
        ok, msg = center_leadframe_on_gds()
        if ok:
            QtWidgets.QMessageBox.information(None, "Center Leadframe", msg)
        else:
            QtWidgets.QMessageBox.warning(None, "Center Leadframe", msg)

    def IsActive(self):
        doc = FreeCAD.activeDocument()
        if not doc:
            return False
        has_gds = any(o.Name.startswith("Layer_")        for o in doc.Objects)
        has_lf  = any(o.Name.startswith("LeadframeBody") for o in doc.Objects)
        return has_gds and has_lf


FreeCADGui.addCommand("CenterLeadframeCommand", CenterLeadframeCommand())