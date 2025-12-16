import FreeCAD as App
import Part

def build_qfn(params, export_path=None):
    """
    Build a simple QFN:
    - A plastic body (box)
    - An exposed pad under the body (thin box)
    """

    # 1) Get the document (create if none)
    doc = App.ActiveDocument
    if doc is None:
        doc = App.newDocument("QFN")

    # 2) Read parameters from the UI (millimeters)
    L = float(params["body_L_mm"])          # body length
    W = float(params["body_W_mm"])          # body width
    H = float(params["body_H_mm"])          # body height

    ep_L = float(params["exposed_pad_L_mm"])  # exposed pad length
    ep_W = float(params["exposed_pad_W_mm"])  # exposed pad width

    # 3) Create the QFN body as a box (centered on X/Y)
    body = Part.makeBox(L, W, H)
    body.translate(App.Vector(-L/2, -W/2, 0))

    body_obj = doc.addObject("Part::Feature", "QFN_Body")
    body_obj.Shape = body

    # 4) Create the exposed pad (thin box) and put it *below* the body
    ep_H = 0.10  # 0.10 mm thickness (simple default)
    pad = Part.makeBox(ep_L, ep_W, ep_H)
    pad.translate(App.Vector(-ep_L/2, -ep_W/2, -ep_H))

    pad_obj = doc.addObject("Part::Feature", "QFN_ExposedPad")
    pad_obj.Shape = pad

    # 5) Create simple side pads (one small box repeated)
    pins = int(params["pins"])
    if pins % 4 != 0:
        raise ValueError("QFN pins must be divisible by 4 (e.g., 16, 20, 24, 32...)")

    pitch = float(params["pitch_mm"])
    pad_L = float(params["pad_L_mm"])   # pad length (outward direction)
    pad_W = float(params["pad_W_mm"])   # pad width (along the side)

    pads_per_side = pins // 4

    # pad thickness (metal height)
    pad_H = 0.05

    pads = []

    # helper positions
    x_left  = -L/2 - pad_L
    x_right =  L/2
    y_bottom = -W/2 - pad_L
    y_top    =  W/2

    # Place pads on LEFT and RIGHT sides
    for i in range(pads_per_side):
        y = (-((pads_per_side - 1) * pitch) / 2) + i * pitch

        # Left pad
        p1 = Part.makeBox(pad_L, pad_W, pad_H)
        p1.translate(App.Vector(x_left, y - pad_W/2, 0))
        pads.append(p1)

        # Right pad
        p2 = Part.makeBox(pad_L, pad_W, pad_H)
        p2.translate(App.Vector(x_right, y - pad_W/2, 0))
        pads.append(p2)

    # Place pads on TOP and BOTTOM sides
    for i in range(pads_per_side):
        x = (-((pads_per_side - 1) * pitch) / 2) + i * pitch

        # Bottom pad
        p3 = Part.makeBox(pad_W, pad_L, pad_H)
        p3.translate(App.Vector(x - pad_W/2, y_bottom, 0))
        pads.append(p3)

        # Top pad
        p4 = Part.makeBox(pad_W, pad_L, pad_H)
        p4.translate(App.Vector(x - pad_W/2, y_top, 0))
        pads.append(p4)

    # Combine pads into one shape
    pads_shape = pads[0]
    for shp in pads[1:]:
        pads_shape = pads_shape.fuse(shp)

    pads_obj = doc.addObject("Part::Feature", "QFN_Pads")
    pads_obj.Shape = pads_shape

    # 6) Make ONE package object (Compound)
    qfn_compound = Part.Compound([body_obj.Shape, pad_obj.Shape, pads_obj.Shape])

    pkg_obj = doc.addObject("Part::Feature", "QFN")
    pkg_obj.Shape = qfn_compound

    # Hide helper objects (keep the scene clean)
    body_obj.ViewObject.Visibility = False
    pad_obj.ViewObject.Visibility = False
    pads_obj.ViewObject.Visibility = False

    # 7) Update the model in FreeCAD
    doc.recompute()

    # 8) Optional STEP export
    if export_path:
        import ImportGui
        ImportGui.export([pkg_obj], export_path)


    # Return objects (useful later)
    return pkg_obj

