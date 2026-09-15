# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Pad names from a layout's text labels.

GDSII has no notion of a pad name. Layouts do label their pins, though — a
text on or inside the pad — and those labels are what a netlist refers to.
A pad takes the label that lies inside its outline; with several, the one
nearest its centre. A pad with no label inside stays unnamed rather than
borrowing a neighbour's.

Labels are read with the transformations of every cell reference applied,
so a label placed inside a pad cell ends up where that pad is instantiated.
"""

import gdstk


def read_labels_mm(lib, cells):
    """[{"text", "x_mm", "y_mm", "layer", "texttype"}] for every non-empty
    label in *cells* and everything they reference."""
    scale = (lib.unit * 1000.0) if getattr(lib, "unit", None) else 0.001
    out = []
    for cell in cells:
        for label in cell.get_labels(apply_repetitions=True, depth=None):
            text = (label.text or "").strip()
            if not text:
                continue
            x, y = label.origin
            out.append({
                "text": text,
                "x_mm": float(x) * scale,
                "y_mm": float(y) * scale,
                "layer": label.layer,
                "texttype": label.texttype,
            })
    return out


def read_labels_from_file(gds_path):
    lib = gdstk.read_gds(str(gds_path))
    return read_labels_mm(lib, lib.top_level() or lib.cells)


def label_in_box(labels, xmin, ymin, xmax, ymax, tol_mm=1e-6):
    """The text of the label inside the box nearest its centre, or ""."""
    cx, cy = (xmin + xmax) / 2.0, (ymin + ymax) / 2.0
    inside = [lb for lb in labels
              if xmin - tol_mm <= lb["x_mm"] <= xmax + tol_mm
              and ymin - tol_mm <= lb["y_mm"] <= ymax + tol_mm]
    if not inside:
        return ""
    return min(inside, key=lambda lb: (lb["x_mm"] - cx) ** 2 + (lb["y_mm"] - cy) ** 2)["text"]


def label_for_pad(labels, pad):
    """The label for a pad dict as core.chip_proxy describes pads."""
    w = float(pad.get("width_mm") or 0.0)
    h = float(pad.get("height_mm") or 0.0)
    return label_in_box(labels, pad["x_mm"] - w / 2.0, pad["y_mm"] - h / 2.0,
                        pad["x_mm"] + w / 2.0, pad["y_mm"] + h / 2.0)
