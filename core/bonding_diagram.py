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


def wire_table(doc):
    """One dict per bond wire, sorted by name, numbered from 1."""
    wires = sorted((o for o in doc.Objects if _is_wire(o)), key=lambda o: o.Name)
    rows = []
    for number, wire in enumerate(wires, start=1):
        start, end = _endpoints(doc, wire)
        span = _mm(getattr(wire, "SpanLength", None))
        if span is None and start is not None and end is not None:
            span = math.hypot(end.x - start.x, end.y - start.y)
        rows.append({
            "no": number,
            "wire": wire.Name,
            "net": getattr(wire, "NetName", "") or "",
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
        writer.writerow(["no", "wire", "net", "from", "to", "span_mm",
                         "length_mm", "loop_height_mm", "diameter_mm"])
        for r in rows:
            writer.writerow([r["no"], r["wire"], r["net"], r["from"], r["to"],
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


def write_bonding_diagram(doc, out_dir, basename=None):
    """Write the SVG and the wire table. Returns (svg_path, csv_path)."""
    rows = wire_table(doc)
    base = (basename or doc.Label or doc.Name).replace(" ", "_")
    svg = bonding_diagram_svg(doc, rows)          # raises before anything is written
    os.makedirs(out_dir, exist_ok=True)
    svg_path = os.path.join(out_dir, f"{base}_bonding_diagram.svg")
    csv_path = os.path.join(out_dir, f"{base}_wire_table.csv")
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    write_wire_table_csv(rows, csv_path)
    return svg_path, csv_path
