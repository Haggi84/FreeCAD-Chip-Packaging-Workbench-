"""
Realistic leadframe geometry builder.

Object naming convention
------------------------
LeadframeBody      : semi-transparent package body (mold compound)
DiePaddle          : central die-attach copper pad  — IsDiePaddle = True
Lead_L01 … L/R/T/B: individual lead fingers        — IsLeadFinger = True
BGA_Ball_00_00 …   : individual BGA solder balls   — IsLeadFinger = True
ContactPoint_001 … : auto-generated snap markers   — IsContactPoint = True
                     placed at the top-face centre of every lead / ball
"""

from FreeCAD import Base
import FreeCAD
import FreeCADGui
import Part


# ── material / visual colours  (R, G, B)  0–1 ─────────────────────────────────
_MAT_COLOR = {
    "Copper":   (0.72, 0.45, 0.20),
    "Alloy 42": (0.60, 0.60, 0.65),
    "Silver":   (0.75, 0.75, 0.80),
}
_MOLD_COLOR    = (0.25, 0.25, 0.28)   # dark-grey mold compound
_CONTACT_COLOR = (0.10, 0.60, 0.90)   # blue contact-point markers


# ── geometry helpers ───────────────────────────────────────────────────────────

def _box(x0, y0, z0, dx, dy, dz) -> Part.Shape:
    return Part.makeBox(dx, dy, dz, Base.Vector(x0, y0, z0))


def _feature(doc, name: str, shape: Part.Shape, color, transparency: int = 0):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    obj.ViewObject.ShapeColor = color
    obj.ViewObject.Transparency = transparency
    return obj


def _tag_lead(obj, side: str, index: int):
    """Mark an object as a wire-bonding lead target."""
    obj.addProperty("App::PropertyBool",    "IsLeadFinger", "Leadframe",
                    "Wire-bond target lead finger or BGA ball")
    obj.addProperty("App::PropertyString",  "LeadSide",     "Leadframe",
                    "Package side: L / R / T / B  or  BGA")
    obj.addProperty("App::PropertyInteger", "LeadIndex",    "Leadframe",
                    "Lead number on this side (1-based)")
    obj.IsLeadFinger = True
    obj.LeadSide     = side
    obj.LeadIndex    = index


# ── contact-point marker creation ─────────────────────────────────────────────

def _create_contact_markers(doc, pairs: list):
    """
    For each (lead_obj, snap_point) in *pairs* create a ContactPoint marker
    at *snap_point* (top face centre of the lead).

    The markers are coloured blue and carry the same properties as those
    created by ContactPointTool so the wire-bonding session can use them.
    """
    # count existing markers to avoid name clashes
    existing = sum(1 for o in doc.Objects if o.Name.startswith("ContactPoint_"))

    for i, (lead_obj, pt) in enumerate(pairs, 1):
        idx    = existing + i
        name   = f"ContactPoint_{idx:03d}"
        marker = doc.addObject("Part::Feature", name)
        marker.Shape = Part.Vertex(pt.x, pt.y, pt.z)

        marker.addProperty("App::PropertyVector", "ContactPoint", "Wirebond",
                            "Snap point for wire bonding")
        marker.addProperty("App::PropertyString", "SourceObject", "Wirebond",
                            "Lead object this point belongs to")
        marker.addProperty("App::PropertyBool",   "IsContactPoint", "Wirebond",
                            "Wire-bond contact point marker")

        marker.ContactPoint  = pt
        marker.SourceObject  = lead_obj.Name
        marker.IsContactPoint = True

        marker.ViewObject.PointSize  = 8
        marker.ViewObject.PointColor = _CONTACT_COLOR
        marker.ViewObject.DisplayMode = "Points"


# ── public entry point ─────────────────────────────────────────────────────────

def build_leadframe(config: dict, doc=None, gds_objects=None):
    """
    Create a realistic leadframe in the active FreeCAD document.

    Required config keys (all types)
    ---------------------------------
    frame_type        : "QFN (Quad Flat No-lead)" | "QFP (Quad Flat Package)"
                        | "BGA (Ball Grid Array)"
    frame_length      : package length   [mm]
    frame_width       : package width    [mm]
    frame_thickness   : body thickness   [mm]
    material          : "Copper" | "Alloy 42" | "Silver"

    QFN / QFP additional keys
    -------------------------
    left_lead_count, right_lead_count,
    top_lead_count,  bottom_lead_count  : leads per side  (int)
    lead_width        : lead width                         [mm]
    lead_pitch        : centre-to-centre lead spacing      [mm]  > lead_width
    inner_lead_length : bond-finger depth inside package   [mm]
    lead_length       : outer gull-wing length (QFP only)  [mm]
    has_die_paddle    : include central die paddle?        (bool, default True)
    die_paddle_length : die paddle X size                  [mm]
    die_paddle_width  : die paddle Y size                  [mm]

    BGA additional keys
    -------------------
    bga_ball_diameter : solder ball diameter               [mm]
    bga_ball_pitch    : ball centre spacing                [mm]  > diameter

    GDS objects
    -----------
    gds_objects : {layer_key: [Part::Feature, …]}  — lifted to z = frame_thickness + 0.01
                  and centred at XY origin (GDS shapes already carry XY transform).
    """
    doc = FreeCAD.activeDocument() or FreeCAD.newDocument("Leadframe")

    frame_type      = config["frame_type"]
    frame_length    = config["frame_length"]
    frame_width     = config["frame_width"]
    frame_thickness = config["frame_thickness"]
    material        = config["material"]
    metal_color     = _MAT_COLOR.get(material, _MAT_COLOR["Copper"])

    half_l = frame_length / 2
    half_w = frame_width  / 2

    # ── Package body ──────────────────────────────────────────────────────
    body_shape = _box(-half_l, -half_w, 0, frame_length, frame_width, frame_thickness)
    _feature(doc, "LeadframeBody", body_shape, _MOLD_COLOR, transparency=65)

    # ── Type-specific geometry → returns (lead_obj, snap_point) pairs ────
    pairs = []
    if frame_type in ("QFN (Quad Flat No-lead)", "QFP (Quad Flat Package)"):
        pairs = _build_qfn_qfp(doc, config, frame_type, metal_color, half_l, half_w)
    elif frame_type == "BGA (Ball Grid Array)":
        pairs = _build_bga(doc, config, metal_color)

    # ── Auto contact points on every lead / ball top face ─────────────────
    if pairs:
        _create_contact_markers(doc, pairs)

    # ── Position GDS objects on top of leadframe, centred at XY origin ────
    if gds_objects:
        z = frame_thickness + 0.01
        for objs in gds_objects.values():
            for obj in objs:
                # Shape already carries XY centering from GDS transform;
                # Placement only needs to lift the object in Z.
                obj.Placement = Base.Placement(
                    Base.Vector(0, 0, z), Base.Rotation(0, 0, 0, 1)
                )

    doc.recompute()
    FreeCADGui.activeDocument().activeView().viewIsometric()
    FreeCADGui.SendMsgToActiveView("ViewFit")
    return doc


# ── QFN / QFP ─────────────────────────────────────────────────────────────────

def _build_qfn_qfp(doc, config, frame_type, color, half_l, half_w):
    """
    Build die paddle + individual lead fingers.
    Returns list of (lead_obj, snap_point) where snap_point is the
    top-face centre of the lead (at z = frame_thickness).
    """
    frame_thickness = config["frame_thickness"]
    lead_width      = config["lead_width"]
    lead_pitch      = config["lead_pitch"]
    inner_lead_len  = config["inner_lead_length"]
    is_qfp          = (frame_type == "QFP (Quad Flat Package)")
    outer_lead_len  = config.get("lead_length", 1.0) if is_qfp else 0.0

    # ── Die paddle ────────────────────────────────────────────────────────
    if config.get("has_die_paddle", True):
        dp_l = config.get("die_paddle_length", half_l * 2 * 0.55)
        dp_w = config.get("die_paddle_width",  half_w * 2 * 0.55)
        dp_shape = _box(-dp_l / 2, -dp_w / 2, 0,
                        dp_l, dp_w, frame_thickness + 0.005)
        dp_obj = _feature(doc, "DiePaddle", dp_shape, color)
        dp_obj.addProperty("App::PropertyBool", "IsDiePaddle", "Leadframe",
                            "Central die-attach paddle")
        dp_obj.IsDiePaddle = True

    # ── Individual lead fingers ───────────────────────────────────────────
    sides = [
        ("L", config.get("left_lead_count",   0)),
        ("R", config.get("right_lead_count",  0)),
        ("T", config.get("top_lead_count",    0)),
        ("B", config.get("bottom_lead_count", 0)),
    ]

    pairs = []
    for side, n in sides:
        if n <= 0:
            continue
        span = (n - 1) * lead_pitch
        for i in range(n):
            along = -span / 2 + i * lead_pitch   # position along the side

            if side in ("L", "R"):
                y0 = along - lead_width / 2
                y1 = along + lead_width / 2
                if side == "L":
                    x0 = -half_l - outer_lead_len
                    x1 = -half_l + inner_lead_len
                else:
                    x0 =  half_l - inner_lead_len
                    x1 =  half_l + outer_lead_len
                dx, dy = x1 - x0, y1 - y0
                shape  = _box(x0, y0, 0, dx, dy, frame_thickness)
                # Snap point: top-face centre (inner-finger X midpoint × full Y midpoint)
                # For QFP we snap to the inner-finger half only
                if is_qfp:
                    snap_x = (-half_l + inner_lead_len / 2) if side == "L" else (half_l - inner_lead_len / 2)
                else:
                    snap_x = x0 + dx / 2
                snap_pt = Base.Vector(snap_x, along, frame_thickness)

            else:  # T or B
                x0 = along - lead_width / 2
                x1 = along + lead_width / 2
                if side == "B":
                    y0 = -half_w - outer_lead_len
                    y1 = -half_w + inner_lead_len
                else:
                    y0 =  half_w - inner_lead_len
                    y1 =  half_w + outer_lead_len
                dx, dy = x1 - x0, y1 - y0
                shape  = _box(x0, y0, 0, dx, dy, frame_thickness)
                if is_qfp:
                    snap_y = (-half_w + inner_lead_len / 2) if side == "B" else (half_w - inner_lead_len / 2)
                else:
                    snap_y = y0 + dy / 2
                snap_pt = Base.Vector(along, snap_y, frame_thickness)

            name     = f"Lead_{side}{i + 1:02d}"
            lead_obj = _feature(doc, name, shape, color)
            _tag_lead(lead_obj, side, i + 1)
            pairs.append((lead_obj, snap_pt))

    return pairs


# ── BGA ───────────────────────────────────────────────────────────────────────

def _build_bga(doc, config, color):
    """
    Build individual BGA solder balls.
    Returns list of (ball_obj, snap_point) where snap_point is the top of
    each ball (z = 0, the PCB contact surface).
    """
    diameter = config["bga_ball_diameter"]
    pitch    = config["bga_ball_pitch"]
    radius   = diameter / 2

    frame_l = config["frame_length"]
    frame_w = config["frame_width"]
    nx      = max(1, round(frame_l / pitch))
    ny      = max(1, round(frame_w / pitch))
    x_start = -(nx - 1) * pitch / 2
    y_start = -(ny - 1) * pitch / 2

    pairs = []
    for i in range(nx):
        for j in range(ny):
            bx   = x_start + i * pitch
            by   = y_start + j * pitch
            name = f"BGA_Ball_{i:02d}_{j:02d}"
            ball = doc.addObject("Part::Sphere", name)
            ball.Radius    = radius
            ball.Placement = Base.Placement(
                Base.Vector(bx, by, -radius), Base.Rotation(0, 0, 0, 1)
            )
            ball.ViewObject.ShapeColor = color
            _tag_lead(ball, "BGA", i * ny + j + 1)
            # Top of ball is at z = placement.z + radius = 0
            snap_pt = Base.Vector(bx, by, 0.0)
            pairs.append((ball, snap_pt))

    return pairs
