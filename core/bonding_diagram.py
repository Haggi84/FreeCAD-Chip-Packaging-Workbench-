# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Bonding diagram and wire table — the two things an assembly house asks for
before it will bond a die.

  <name>_bonding_diagram.svg   plan view: die, leadframe, pads, every wire
                               numbered
  <name>_wire_table.csv        one row per wire, numbered to match: net,
                               from/to pad, span, wire length, loop height,
                               diameter

Pads are shown by their PadName when the layout labels them, falling back to
the contact point's label. Lengths are the ones the wire records: WireLength
is the length along the loop, SpanLength the straight plan-view distance.

Qt-free, so it is testable headlessly.
"""

import csv
import math
import os
from xml.sax.saxutils import escape

_WIDTH_PX = 1000.0
_MARGIN_PX = 40.0


def _mm(value):
    """A float from a FreeCAD Quantity, a float, or None."""
    if value is None:
        return None
    try:
        return float(getattr(value, "Value", value))
    except (TypeError, ValueError):
        return None


def _is_wire(obj):
    return obj.Name.startswith("BondWire_") and hasattr(obj, "Shape")


def _cp_name(doc, name):
    cp = doc.getObject(name) if name else None
    if cp is None:
        return name or ""
    return getattr(cp, "PadName", "") or cp.Label or cp.Name


def _endpoints(doc, wire):
    points = []
    for prop, cp_prop in (("StartPoint", "StartCP"), ("EndPoint", "EndCP")):
        point = getattr(wire, prop, None)
        if point is None:
            cp = doc.getObject(getattr(wire, cp_prop, "") or "")
            point = getattr(cp, "ContactPoint", None) if cp is not None else None
        points.append(point)
    return points


def _die_of_wire(doc, wire):
    """(die name, tier) of the die a wire starts or ends on, or ("", "")."""
    for prop in ("StartCP", "EndCP"):
        cp = doc.getObject(getattr(wire, prop, "") or "")
        name = (getattr(cp, "DieName", "") or "") if cp is not None else ""
        if name:
            for obj in doc.Objects:
                if getattr(obj, "DieName", "") == name and hasattr(obj, "DieTier"):
                    return name, obj.DieTier
            return name, ""
    return "", ""


def wire_table(doc):
    """One dict per bond wire, sorted by name, numbered from 1."""
    wires = sorted((o for o in doc.Objects if _is_wire(o)), key=lambda o: o.Name)
    rows = []
    for number, wire in enumerate(wires, start=1):
        start, end = _endpoints(doc, wire)
        span = _mm(getattr(wire, "SpanLength", None))
        if span is None and start is not None and end is not None:
            span = math.hypot(end.x - start.x, end.y - start.y)
        die_name, tier = _die_of_wire(doc, wire)
        rows.append({
            "no": number,
            "wire": wire.Name,
            "net": getattr(wire, "NetName", "") or "",
            "die": die_name,
            "tier": tier,
            "from": _cp_name(doc, getattr(wire, "StartCP", "")),
            "to": _cp_name(doc, getattr(wire, "EndCP", "")),
            "span_mm": span,
            "length_mm": _mm(getattr(wire, "WireLength", None)),
            "loop_height_mm": _mm(getattr(wire, "LoopHeight", None)),
            "diameter_mm": _mm(getattr(wire, "WireDiameter", None)),
            "start": start,
            "end": end,
        })
    return rows


def _fmt(value):
    return "" if value is None else f"{value:.4f}"


def write_wire_table_csv(rows, path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["no", "wire", "net", "die", "tier", "from", "to",
                         "span_mm", "length_mm", "loop_height_mm", "diameter_mm"])
        for r in rows:
            writer.writerow([r["no"], r["wire"], r["net"], r.get("die", ""),
                             r.get("tier", ""), r["from"], r["to"],
                             _fmt(r["span_mm"]), _fmt(r["length_mm"]),
                             _fmt(r["loop_height_mm"]), _fmt(r["diameter_mm"])])


# ── plan view ────────────────────────────────────────────────────────────────

def _boxes(doc, predicate):
    seen, out = set(), []
    for obj in doc.Objects:
        if not predicate(obj):
            continue
        shape = getattr(obj, "Shape", None)
        try:
            bb = shape.BoundBox
        except Exception:
            continue
        if not bb.isValid() or bb.XLength <= 0 or bb.YLength <= 0:
            continue
        key = tuple(round(v, 6) for v in (bb.XMin, bb.YMin, bb.XMax, bb.YMax))
        if key in seen:
            continue
        seen.add(key)
        out.append((obj, key))
    return out


def bonding_diagram_svg(doc, rows=None, title=None):
    """The plan-view SVG as a string. Raises ValueError without bond wires."""
    rows = wire_table(doc) if rows is None else rows
    wires = [r for r in rows if r["start"] is not None and r["end"] is not None]
    if not wires:
        raise ValueError("The document has no bond wires to draw.")

    dies = _boxes(doc, lambda o: getattr(o, "IsChipProxy", False)
                  or getattr(o, "IsDieBody", False))
    package = _boxes(doc, lambda o: o.Name == "LeadframeBody")
    leads = _boxes(doc, lambda o: getattr(o, "IsLeadFinger", False)
                   or getattr(o, "IsDiePaddle", False))
    pads = [o for o in doc.Objects
            if getattr(o, "IsContactPoint", False)
            and getattr(o, "ContactPoint", None) is not None]

    xs, ys = [], []
    for _obj, (x0, y0, x1, y1) in dies + package + leads:
        xs += [x0, x1]
        ys += [y0, y1]
    for r in wires:
        xs += [r["start"].x, r["end"].x]
        ys += [r["start"].y, r["end"].y]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    extent = max(xmax - xmin, ymax - ymin, 1e-6)
    scale = (_WIDTH_PX - 2 * _MARGIN_PX) / extent
    height = (ymax - ymin) * scale + 2 * _MARGIN_PX + 30.0

    def px(x, y):
        return (_MARGIN_PX + (x - xmin) * scale,
                30.0 + _MARGIN_PX + (ymax - y) * scale)

    def rect(key, css):
        x0, y0, x1, y1 = key
        left, top = px(x0, y1)
        return (f'<rect class="{css}" x="{left:.2f}" y="{top:.2f}" '
                f'width="{(x1 - x0) * scale:.2f}" height="{(y1 - y0) * scale:.2f}"/>')

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH_PX:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {_WIDTH_PX:.0f} {height:.0f}">',
        "<style>"
        ".package{fill:#eeeeee;stroke:#999;stroke-width:1}"
        ".die{fill:#c9d6e6;stroke:#2c4460;stroke-width:1.5}"
        ".lead{fill:#e8c19a;stroke:#7a4d18;stroke-width:1}"
        ".pad{fill:#d9442b}"
        ".wire{stroke:#b8860b;stroke-width:1.5}"
        ".number{font:10px sans-serif;fill:#222}"
        ".title{font:bold 14px sans-serif;fill:#222}"
        "</style>",
        f'<text class="title" x="{_MARGIN_PX:.0f}" y="24">'
        f'{escape(title or doc.Label)} — bonding diagram, {len(wires)} wire(s), '
        f'{extent:.3f} mm across</text>',
    ]
    out += [rect(key, "package") for _o, key in package]
    out += [rect(key, "die") for _o, key in dies]
    out += [rect(key, "lead") for _o, key in leads]
    for pad in pads:
        cx, cy = px(pad.ContactPoint.x, pad.ContactPoint.y)
        out.append(f'<circle class="pad" cx="{cx:.2f}" cy="{cy:.2f}" r="2"/>')
    for r in wires:
        x0, y0 = px(r["start"].x, r["start"].y)
        x1, y1 = px(r["end"].x, r["end"].y)
        out.append(f'<line class="wire" data-wire="{escape(r["wire"])}" '
                   f'x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}"/>')
        out.append(f'<text class="number" x="{(x0 + x1) / 2 + 3:.2f}" '
                   f'y="{(y0 + y1) / 2 - 3:.2f}">{r["no"]}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ── elevation ────────────────────────────────────────────────────────────────

def _carrier_boxes(doc):
    def is_carrier(o):
        return (o.Name in ("LeadframeBody", "PCB_Board")
                or getattr(o, "IsDiePaddle", False)
                or getattr(o, "IsLeadFinger", False))
    return _boxes(doc, is_carrier)


def elevation_svg(doc, rows=None, title=None):
    """
    The stack seen from the side: carrier, dies with the adhesive between
    them, and every wire's loop.

    A stack is a fraction of a millimetre tall over several millimetres of
    width, so drawn true to scale it is a line. The vertical scale is
    therefore exaggerated — by how much is stated in the title, because a
    drawing whose two axes differ silently is worse than no drawing.
    """
    from core import dies as die_model

    rows = wire_table(doc) if rows is None else rows
    wires = [r for r in rows if r["start"] is not None and r["end"] is not None]
    dies = die_model.collect(doc)
    if not dies:
        return None

    attaches = [(o, (o.Shape.BoundBox.XMin, o.Shape.BoundBox.XMax,
                     o.Shape.BoundBox.ZMin, o.Shape.BoundBox.ZMax))
                for o in doc.Objects if getattr(o, die_model.IS_DIE_ATTACH, False)
                and getattr(o, "Shape", None) is not None]
    carriers = [(obj, key) for obj, key in _carrier_boxes(doc)]

    xs, zs = [], []
    for die in dies:
        xs += [die.outline[0], die.outline[2]]
        zs += [die.z_bottom, die.z_top]
    for _obj, (x0, x1, z0, z1) in attaches:
        xs += [x0, x1]
        zs += [z0, z1]
    for obj, (x0, _y0, x1, _y1) in carriers:
        bb = obj.Shape.BoundBox
        xs += [x0, x1]
        zs += [bb.ZMin, bb.ZMax]
    for r in wires:
        xs += [r["start"].x, r["end"].x]
        zs += [r["start"].z, r["end"].z]
        if r["loop_height_mm"]:
            zs.append(max(r["start"].z, r["end"].z) + r["loop_height_mm"])

    xmin, xmax, zmin, zmax = min(xs), max(xs), min(zs), max(zs)
    x_range = max(xmax - xmin, 1e-6)
    z_range = max(zmax - zmin, 1e-6)
    inner = _WIDTH_PX - 2 * _MARGIN_PX
    x_scale = inner / x_range
    z_scale = x_scale
    if z_range * z_scale < 0.25 * inner:          # too flat to read
        z_scale = min((0.25 * inner) / z_range, x_scale * 50.0)
    exaggeration = z_scale / x_scale
    height = z_range * z_scale + 2 * _MARGIN_PX + 30.0

    def px(x, z):
        return (_MARGIN_PX + (x - xmin) * x_scale,
                30.0 + _MARGIN_PX + (zmax - z) * z_scale)

    def rect(x0, x1, z0, z1, css, label=None):
        left, top = px(x0, z1)
        width = max((x1 - x0) * x_scale, 0.5)
        tall = max((z1 - z0) * z_scale, 0.5)
        out = (f'<rect class="{css}" x="{left:.2f}" y="{top:.2f}" '
               f'width="{width:.2f}" height="{tall:.2f}"/>')
        if label:
            out += (f'<text class="label" x="{left + width / 2:.2f}" '
                    f'y="{top + tall / 2 + 3:.2f}" text-anchor="middle">'
                    f'{escape(label)}</text>')
        return out

    scale_note = (f"vertical scale ×{exaggeration:.0f}" if exaggeration > 1.05
                  else "true scale")
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH_PX:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {_WIDTH_PX:.0f} {height:.0f}">',
        "<style>"
        ".carrier{fill:#cc8833;stroke:#7a4d18;stroke-width:1}"
        ".die{fill:#c9d6e6;stroke:#2c4460;stroke-width:1.5}"
        ".attach{fill:#5a4a3a;stroke:#3a2c1c;stroke-width:0.5}"
        ".wire{fill:none;stroke:#b8860b;stroke-width:1.5}"
        ".label{font:10px sans-serif;fill:#222}"
        ".title{font:bold 14px sans-serif;fill:#222}"
        "</style>",
        f'<text class="title" x="{_MARGIN_PX:.0f}" y="24">'
        f'{escape(title or doc.Label)} — stack elevation, {len(dies)} die(s), '
        f'{len(wires)} wire(s), {scale_note}</text>',
    ]
    for obj, (x0, _y0, x1, _y1) in carriers:
        bb = obj.Shape.BoundBox
        out.append(rect(x0, x1, bb.ZMin, bb.ZMax, "carrier"))
    for _obj, (x0, x1, z0, z1) in attaches:
        out.append(rect(x0, x1, z0, z1, "attach"))
    for die in dies:
        out.append(rect(die.outline[0], die.outline[2], die.z_bottom, die.z_top,
                        "die", f"{die.name} (tier {die.tier})"))
    for r in wires:
        x0, y0 = px(r["start"].x, r["start"].z)
        x1, y1 = px(r["end"].x, r["end"].z)
        apex = max(r["start"].z, r["end"].z) + (r["loop_height_mm"] or 0.0)
        _cx, cy = px((r["start"].x + r["end"].x) / 2.0, apex)
        # The quadratic reaches half way to its control point, so the control
        # goes twice as high for the curve to touch the real apex.
        control_y = 2 * cy - (y0 + y1) / 2.0
        out.append(f'<path class="wire" d="M {x0:.2f} {y0:.2f} '
                   f'Q {(x0 + x1) / 2:.2f} {control_y:.2f} {x1:.2f} {y1:.2f}"/>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def write_bonding_diagram(doc, out_dir, basename=None):
    """
    Write the plan view, the wire table, and — when the document has dies —
    the stack elevation.

    Returns (svg_path, csv_path, elevation_path); the elevation is None when
    there is no die to draw.
    """
    rows = wire_table(doc)
    base = (basename or doc.Label or doc.Name).replace(" ", "_")
    svg = bonding_diagram_svg(doc, rows)          # raises before anything is written
    elevation = elevation_svg(doc, rows)
    os.makedirs(out_dir, exist_ok=True)
    svg_path = os.path.join(out_dir, f"{base}_bonding_diagram.svg")
    csv_path = os.path.join(out_dir, f"{base}_wire_table.csv")
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    write_wire_table_csv(rows, csv_path)
    elevation_path = None
    if elevation:
        elevation_path = os.path.join(out_dir, f"{base}_stack_elevation.svg")
        with open(elevation_path, "w", encoding="utf-8") as fh:
            fh.write(elevation)
    return svg_path, csv_path, elevation_path
