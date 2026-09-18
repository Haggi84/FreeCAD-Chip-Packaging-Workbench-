# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Reading KiCad: the board, the netlist, and where each component's 3-D model
lives.

Both files are S-expressions, so one reader serves both:

  * the board (.kicad_pcb) carries what the geometry needs — where each
    component sits, where its pads are, which net each pad is on, and the
    path of its 3-D model;
  * the netlist export (.net) carries the design's own view — reference,
    value and footprint per component, and the nets by name. It is what the
    schematic says, so it is also the thing to check the board against.

Coordinates are converted here, once. KiCad's board Y axis points DOWN the
page; FreeCAD's points up. Every position is therefore mirrored (y -> -y) and
every rotation negated with it, so a component that looks rotated clockwise
on the KiCad canvas is rotated clockwise in the 3-D view too. Lengths are
already millimetres in KiCad 6 and later, which is what FreeCAD uses.

Standard library only — no KiCad installation, no kicad-cli, nothing to keep
in step with a KiCad release.
"""

import os
import re

# KiCad writes the model path with the environment variable of its own major
# version. Any of them may be set; the newest wins, and the import dialog can
# add a folder of its own.
_MODEL_DIR_VARS = ("KICAD9_3DMODEL_DIR", "KICAD8_3DMODEL_DIR",
                   "KICAD7_3DMODEL_DIR", "KICAD6_3DMODEL_DIR",
                   "KISYS3DMOD")

# A 3-D model is referenced as .wrl as often as .step; only the STEP family
# carries solid geometry FreeCAD can use.
_SOLID_SUFFIXES = (".step", ".stp", ".STEP", ".STP")

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()]+')


# ── S-expressions ────────────────────────────────────────────────────────────

def parse_sexp(text):
    """The S-expression in *text* as nested lists; strings keep their value,
    everything else stays a string."""
    stack, current = [], []
    for token in _TOKEN.findall(text):
        if token == "(":
            stack.append(current)
            current = []
        elif token == ")":
            if not stack:
                raise ValueError("Unbalanced ')' in the file.")
            done = current
            current = stack.pop()
            current.append(done)
        elif token.startswith('"'):
            current.append(token[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
        else:
            current.append(token)
    if stack:
        raise ValueError("Unbalanced '(' in the file.")
    return current[0] if len(current) == 1 else current


def _is_node(item, key=None):
    return isinstance(item, list) and item and isinstance(item[0], str) and \
        (key is None or item[0] == key)


def children(node, key):
    """Every direct child list of *node* whose head is *key*."""
    return [item for item in node if _is_node(item, key)]


def child(node, key):
    """The first direct child list whose head is *key*, or None."""
    return next(iter(children(node, key)), None)


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _at(node):
    """(x, y, rotation) of an (at …) child, already in FreeCAD's frame."""
    entry = child(node, "at")
    if entry is None:
        return 0.0, 0.0, 0.0
    x = _number(entry[1] if len(entry) > 1 else 0.0)
    y = _number(entry[2] if len(entry) > 2 else 0.0)
    rotation = _number(entry[3] if len(entry) > 3 else 0.0)
    return x, -y, -rotation


def _xyz(node, key, default=(0.0, 0.0, 0.0)):
    entry = child(node, key)
    if entry is None:
        return default
    xyz = child(entry, "xyz")
    if xyz is None:
        return default
    return tuple(_number(v) for v in xyz[1:4])


# ── the board ────────────────────────────────────────────────────────────────

def _rotate(x, y, degrees):
    if not degrees:
        return x, y
    import math
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    return x * cos - y * sin, x * sin + y * cos


def _courtyard(footprint):
    """(xmin, ymin, xmax, ymax) of the courtyard in the footprint's own frame,
    or None. It is the outline a component is allowed to occupy, which makes
    it the right size for a stand-in body."""
    xs, ys = [], []
    for line in children(footprint, "fp_line") + children(footprint, "fp_rect"):
        layer = child(line, "layer")
        if layer is None or "CrtYd" not in str(layer[1]):
            continue
        for key in ("start", "end"):
            point = child(line, key)
            if point is not None:
                xs.append(_number(point[1]))
                ys.append(-_number(point[2]))
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _pads(footprint, origin, rotation):
    """Every pad with its position in board coordinates."""
    pads = []
    for pad in children(footprint, "pad"):
        number = str(pad[1]) if len(pad) > 1 else ""
        local_x, local_y, _rot = _at(pad)
        x, y = _rotate(local_x, local_y, rotation)
        size = child(pad, "size")
        net = child(pad, "net")
        pads.append({
            "number": number,
            "x_mm": origin[0] + x,
            "y_mm": origin[1] + y,
            "width_mm": _number(size[1]) if size else 0.0,
            "height_mm": _number(size[2]) if size and len(size) > 2 else 0.0,
            "net_code": str(net[1]) if net and len(net) > 1 else "",
            "net": str(net[2]) if net and len(net) > 2 else "",
            "type": str(pad[2]) if len(pad) > 2 else "",
        })
    return pads


def _model(footprint):
    entry = child(footprint, "model")
    if entry is None:
        return None
    return {
        "path": str(entry[1]) if len(entry) > 1 else "",
        "offset_mm": _xyz(entry, "offset"),
        "scale": _xyz(entry, "scale", (1.0, 1.0, 1.0)),
        "rotate_deg": _xyz(entry, "rotate"),
    }


def _property(footprint, name, default=""):
    for entry in children(footprint, "property"):
        if len(entry) > 2 and str(entry[1]) == name:
            return str(entry[2])
    # KiCad 6 wrote the reference as (fp_text reference "R1" …)
    for entry in children(footprint, "fp_text"):
        if len(entry) > 2 and str(entry[1]) == name.lower():
            return str(entry[2])
    return default


def read_board(path):
    """
    {"footprints": [...], "nets": {code: name}} from a .kicad_pcb.

    Each footprint carries its reference, value, library name, layer, position
    and rotation in FreeCAD's frame, its pads with their net, its courtyard,
    and its 3-D model reference.
    """
    with open(path, encoding="utf-8", errors="replace") as fh:
        board = parse_sexp(fh.read())
    if not _is_node(board, "kicad_pcb"):
        raise ValueError(f"{os.path.basename(path)} is not a KiCad board file.")

    nets = {}
    for entry in children(board, "net"):
        if len(entry) > 2:
            nets[str(entry[1])] = str(entry[2])

    footprints = []
    for entry in children(board, "footprint") + children(board, "module"):
        x, y, rotation = _at(entry)
        layer = child(entry, "layer")
        footprints.append({
            "ref": _property(entry, "Reference", "?"),
            "value": _property(entry, "Value", ""),
            "library": str(entry[1]) if len(entry) > 1 else "",
            "layer": str(layer[1]) if layer else "F.Cu",
            "x_mm": x,
            "y_mm": y,
            "rotation_deg": rotation,
            "courtyard": _courtyard(entry),
            "pads": _pads(entry, (x, y), rotation),
            "model": _model(entry),
        })
    return {"footprints": footprints, "nets": nets}


# ── the netlist export ───────────────────────────────────────────────────────

def read_netlist(path):
    """
    {"components": {ref: {...}}, "nets": {name: [(ref, pin), …]}} from a .net.
    """
    with open(path, encoding="utf-8", errors="replace") as fh:
        export = parse_sexp(fh.read())
    if not _is_node(export, "export"):
        raise ValueError(f"{os.path.basename(path)} is not a KiCad netlist export.")

    components = {}
    block = child(export, "components")
    for comp in children(block, "comp") if block else []:
        ref_node = child(comp, "ref")
        if ref_node is None:
            continue
        ref = str(ref_node[1])
        value = child(comp, "value")
        footprint = child(comp, "footprint")
        components[ref] = {
            "ref": ref,
            "value": str(value[1]) if value and len(value) > 1 else "",
            "footprint": str(footprint[1]) if footprint and len(footprint) > 1 else "",
        }

    nets = {}
    block = child(export, "nets")
    for net in children(block, "net") if block else []:
        name_node = child(net, "name")
        code_node = child(net, "code")
        name = (str(name_node[1]) if name_node and len(name_node) > 1
                else str(code_node[1]) if code_node and len(code_node) > 1 else "")
        nodes = []
        for node in children(net, "node"):
            ref_node, pin_node = child(node, "ref"), child(node, "pin")
            if ref_node is not None and pin_node is not None:
                nodes.append((str(ref_node[1]), str(pin_node[1])))
        if name:
            nets[name] = nodes
    return {"components": components, "nets": nets}


def compare(board, netlist):
    """
    What the board and the netlist disagree about:
    {"missing_from_board": [ref], "missing_from_netlist": [ref],
     "net_mismatch": [(ref, pin, board net, netlist net)]}

    Worth looking at before anything is built: a board saved before the last
    schematic change has components the netlist does not know, or pads on the
    wrong net, and every rubber line drawn from it would be wrong.
    """
    board_refs = {fp["ref"] for fp in board["footprints"]}
    netlist_refs = set(netlist["components"])

    expected = {}
    for name, nodes in netlist["nets"].items():
        for ref, pin in nodes:
            expected[(ref, str(pin))] = name

    mismatch = []
    for fp in board["footprints"]:
        for pad in fp["pads"]:
            key = (fp["ref"], pad["number"])
            if key not in expected:
                continue
            if pad["net"] != expected[key]:
                mismatch.append((fp["ref"], pad["number"], pad["net"], expected[key]))
    return {
        "missing_from_board": sorted(netlist_refs - board_refs),
        "missing_from_netlist": sorted(board_refs - netlist_refs),
        "net_mismatch": sorted(mismatch),
    }


# ── 3-D models ───────────────────────────────────────────────────────────────

def model_search_dirs(extra=()):
    """Folders to look for a 3-D model in: whatever KiCad's own environment
    variables point at, plus any the caller adds."""
    dirs = [path for path in (os.environ.get(var) for var in _MODEL_DIR_VARS) if path]
    return [d for d in list(extra) + dirs if d and os.path.isdir(d)]


def resolve_model_path(raw, search_dirs=()):
    """
    The STEP file for a model reference, or None.

    KiCad writes the path with its own environment variable —
    "${KICAD8_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0805.wrl" — and often names
    the .wrl, which is a rendering mesh. The .step beside it is the solid, so
    that is what is looked for, under each search folder in turn.
    """
    if not raw:
        return None
    text = str(raw).replace("\\", "/")
    variable = re.match(r"\$\{([^}]+)\}/?(.*)", text) or re.match(r"\$\(([^)]+)\)/?(.*)", text)
    tail = variable.group(2) if variable else text
    from_env = os.environ.get(variable.group(1)) if variable else None

    candidates = []
    roots = ([from_env] if from_env else []) + list(search_dirs)
    if os.path.isabs(text) and not variable:
        candidates.append(text)
    for root in roots:
        if root:
            candidates.append(os.path.join(root, tail))
            # A folder given by hand may already be the .3dshapes one.
            candidates.append(os.path.join(root, os.path.basename(tail)))

    for candidate in candidates:
        stem, _suffix = os.path.splitext(candidate)
        for suffix in _SOLID_SUFFIXES:
            if os.path.isfile(stem + suffix):
                # Normalised: the reference is written with forward slashes and
                # joined onto a Windows folder, so it comes back mixed.
                return os.path.normpath(stem + suffix)
        if os.path.isfile(candidate) and candidate.lower().endswith((".step", ".stp")):
            return os.path.normpath(candidate)
    return None
