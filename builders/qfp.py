import FreeCAD as App
import Part

def build_qfp(params, export_path=None):
    doc = App.ActiveDocument or App.newDocument("QFP")

    L = params["body_L_mm"]
    W = params["body_W_mm"]
    H = params["body_H_mm"]

    body = Part.makeBox(L, W, H)
    body.translate(App.Vector(-L/2, -W/2, 0))

    body_obj = doc.addObject("Part::Feature", "QFP_Body")
    body_obj.Shape = body

    doc.recompute()

    if export_path:
        import ImportGui
        ImportGui.export([body_obj], export_path)

    return body_obj
