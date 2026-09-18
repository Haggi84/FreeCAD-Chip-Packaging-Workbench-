# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
KiCad components as bodies you can place, with their pads as contact points.

One component becomes a group holding its body and one contact point per pad.
The body is the footprint's own 3-D model when that STEP file can be found,
and a block the size of its courtyard when it cannot — a component whose model
is missing still has to be placeable, and its pads are what the routing
actually needs.

Pads are contact points like any other in this workbench, so every tool that
already snaps to a contact point works on them unchanged. Each one carries:

    PadName    "R1.2" — the reference and pin, which a netlist refers to
    PadNumber  "2"
    ComponentRef "R1"
    NetName    the net from the board

They sit at the plane the component stands on, not on top of its body: that
is where its pads touch the substrate, and where a trace has to reach.

Qt-free, so it is testable headlessly.
"""

import os

import FreeCAD
import Part

IS_COMPONENT = "IsComponent"
COMPONENT_REF = "ComponentRef"
PAD_NUMBER = "PadNumber"

# A stand-in body is as tall as this unless the caller says otherwise: enough
# to see and to place, not a claim about the real part.
DEFAULT_PLACEHOLDER_HEIGHT_MM = 1.0
# How far outside the pads a stand-in body reaches when the footprint draws no
# courtyard.
_PAD_MARGIN_MM = 0.2


def _safe_name(text, fallback="Component"):
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(text))
    return cleaned.strip("_") or fallback


def _pad_extent(footprint):
    xs, ys = [], []
    for pad in footprint["pads"]:
        xs += [pad["x_mm"] - pad["width_mm"] / 2.0 - _PAD_MARGIN_MM,
               pad["x_mm"] + pad["width_mm"] / 2.0 + _PAD_MARGIN_MM]
        ys += [pad["y_mm"] - pad["height_mm"] / 2.0 - _PAD_MARGIN_MM,
               pad["y_mm"] + pad["height_mm"] / 2.0 + _PAD_MARGIN_MM]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def placeholder_outline(footprint):
    """
    (xmin, ymin, xmax, ymax) in board coordinates for a stand-in body: the
    courtyard where the footprint draws one, otherwise the pads plus a margin.
    """
    courtyard = footprint.get("courtyard")
    if courtyard:
        import math
        angle = math.radians(footprint["rotation_deg"])
        cos, sin = math.cos(angle), math.sin(angle)
        corners = [(x, y) for x in courtyard[0::2] for y in courtyard[1::2]]
        turned = [(x * cos - y * sin, x * sin + y * cos) for x, y in corners]
        xs = [footprint["x_mm"] + x for x, _y in turned]
        ys = [footprint["y_mm"] + y for _x, y in turned]
        return min(xs), min(ys), max(xs), max(ys)
    return _pad_extent(footprint)


def _build_placeholder(doc, footprint, base_z_mm, height_mm):
    outline = placeholder_outline(footprint)
    if outline is None:
        return None
    x0, y0, x1, y1 = outline
    obj = doc.addObject("Part::Feature", f"Comp_{_safe_name(footprint['ref'])}")
    obj.Shape = Part.makeBox(max(x1 - x0, 1e-3), max(y1 - y0, 1e-3), height_mm,
                             FreeCAD.Vector(x0, y0, base_z_mm))
    if FreeCAD.GuiUp and getattr(obj, "ViewObject", None) is not None:
        obj.ViewObject.ShapeColor = (0.35, 0.35, 0.38)
        obj.ViewObject.Transparency = 30
    return obj


def _import_step(doc, path, footprint, base_z_mm):
    """Import the footprint's STEP model and place it. Returns the object, or
    None when the import produced nothing usable."""
    import Import

    before = {o.Name for o in doc.Objects}
    Import.insert(path, doc.Name)
    fresh = [o for o in doc.Objects if o.Name not in before]
    solids = [o for o in fresh if getattr(o, "Shape", None) is not None
              and not o.Shape.isNull() and o.Shape.Solids]
    if not solids:
        for obj in fresh:
            try:
                doc.removeObject(obj.Name)
            except Exception:
                pass
        return None

    shape = (solids[0].Shape.copy() if len(solids) == 1
             else Part.makeCompound([o.Shape.copy() for o in solids]))
    for obj in fresh:
        try:
            doc.removeObject(obj.Name)
        except Exception:
            pass

    model = footprint.get("model") or {}
    scale = model.get("scale") or (1.0, 1.0, 1.0)
    if any(abs(s - 1.0) > 1e-9 for s in scale):
        matrix = FreeCAD.Matrix()
        matrix.scale(*scale)
        shape = shape.transformGeometry(matrix)

    placement = FreeCAD.Placement()
    rx, ry, rz = model.get("rotate_deg") or (0.0, 0.0, 0.0)
    rotation = (FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), rz)
                * FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), ry)
                * FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), rx))
    placement.Rotation = (FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1),
                                           footprint["rotation_deg"]) * rotation)
    offset = model.get("offset_mm") or (0.0, 0.0, 0.0)
    placement.Base = FreeCAD.Vector(footprint["x_mm"] + offset[0],
                                    footprint["y_mm"] - offset[1],
                                    base_z_mm + offset[2])

    obj = doc.addObject("Part::Feature", f"Comp_{_safe_name(footprint['ref'])}")
    obj.Shape = shape
    obj.Placement = placement
    return obj


def build_component(doc, footprint, base_z_mm=0.0, model_dirs=(),
                    placeholder_height_mm=DEFAULT_PLACEHOLDER_HEIGHT_MM,
                    use_models=True):
    """
    Build one component: its body and its pads.

    Returns {"ref", "body", "pads", "model": "step"|"placeholder"|"none"}.
    """
    from core import kicad

    model = footprint.get("model") or {}
    path = (kicad.resolve_model_path(model.get("path"), model_dirs)
            if use_models else None)

    body, source = None, "none"
    if path:
        try:
            body = _import_step(doc, path, footprint, base_z_mm)
            source = "step" if body is not None else "none"
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[Components] {footprint['ref']}: could not import "
                f"{os.path.basename(path)} ({exc}); using a stand-in body.\n")
            body = None
    if body is None:
        body = _build_placeholder(doc, footprint, base_z_mm, placeholder_height_mm)
        source = "placeholder" if body is not None else "none"

    group = doc.addObject("App::DocumentObjectGroup",
                          f"{_safe_name(footprint['ref'])}_Component")
    group.Label = (f"{footprint['ref']} ({footprint['value']})"
                   if footprint["value"] else footprint["ref"])

    if body is not None:
        body.Label = group.Label
        for prop, ptype, value in (
            (IS_COMPONENT, "App::PropertyBool", True),
            (COMPONENT_REF, "App::PropertyString", footprint["ref"]),
            ("ComponentValue", "App::PropertyString", footprint.get("value", "")),
            ("FootprintName", "App::PropertyString", footprint.get("library", "")),
        ):
            if not hasattr(body, prop):
                body.addProperty(ptype, prop, "Component", "")
            setattr(body, prop, value)
        group.addObject(body)

    pads = []
    for pad in footprint["pads"]:
        marker = doc.addObject(
            "Part::Feature",
            f"Pad_{_safe_name(footprint['ref'])}_{_safe_name(pad['number'], 'x')}")
        point = FreeCAD.Vector(pad["x_mm"], pad["y_mm"], base_z_mm)
        marker.Shape = Part.Vertex(point)
        marker.Label = f"{footprint['ref']}.{pad['number']}"
        for prop, ptype, value in (
            ("ContactPoint", "App::PropertyVector", point),
            ("IsContactPoint", "App::PropertyBool", True),
            ("SourceObject", "App::PropertyString", body.Name if body else ""),
            ("PadName", "App::PropertyString", f"{footprint['ref']}.{pad['number']}"),
            (PAD_NUMBER, "App::PropertyString", pad["number"]),
            (COMPONENT_REF, "App::PropertyString", footprint["ref"]),
            ("NetName", "App::PropertyString", pad.get("net", "")),
        ):
            if not hasattr(marker, prop):
                marker.addProperty(ptype, prop, "Wirebond", "")
            setattr(marker, prop, value)
        if FreeCAD.GuiUp and getattr(marker, "ViewObject", None) is not None:
            marker.ViewObject.PointSize = 6
            marker.ViewObject.PointColor = (0.20, 0.60, 1.00)
            marker.ViewObject.DisplayMode = "Points"
        group.addObject(marker)
        pads.append(marker)

    return {"ref": footprint["ref"], "body": body, "pads": pads, "model": source,
            "group": group}


def build_board(doc, board, base_z_mm=0.0, model_dirs=(),
                placeholder_height_mm=DEFAULT_PLACEHOLDER_HEIGHT_MM,
                use_models=True, refs=None):
    """
    Build every component of a parsed board (see core.kicad.read_board).

    Returns {"components": [...], "pads": n, "with_model": n,
             "placeholders": n, "skipped": [(ref, reason)]}.
    """
    report = {"components": [], "pads": 0, "with_model": 0, "placeholders": 0,
              "skipped": []}
    for footprint in board["footprints"]:
        if refs is not None and footprint["ref"] not in refs:
            continue
        if not footprint["pads"]:
            report["skipped"].append((footprint["ref"], "no pads"))
            continue
        built = build_component(doc, footprint, base_z_mm, model_dirs,
                                placeholder_height_mm, use_models)
        report["components"].append(built)
        report["pads"] += len(built["pads"])
        if built["model"] == "step":
            report["with_model"] += 1
        elif built["model"] == "placeholder":
            report["placeholders"] += 1
        else:
            report["skipped"].append((footprint["ref"], "no body could be built"))
    FreeCAD.Console.PrintMessage(
        f"[Components] {len(report['components'])} component(s), "
        f"{report['pads']} pad(s); {report['with_model']} with a 3-D model, "
        f"{report['placeholders']} as stand-in bodies\n")
    return report


def components_of(doc):
    """Every component body in the document."""
    return [o for o in doc.Objects if getattr(o, IS_COMPONENT, False)]


def pads_of(doc, ref=None):
    """Component pads, of one component when *ref* is given."""
    pads = []
    for obj in doc.Objects:
        if not getattr(obj, "IsContactPoint", False):
            continue
        if not getattr(obj, COMPONENT_REF, ""):
            continue
        if ref is None or obj.ComponentRef == ref:
            pads.append(obj)
    return pads
