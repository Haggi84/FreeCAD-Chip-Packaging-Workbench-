# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Write a GDS3D process-definition file from the active PDK.

GDS3D (https://github.com/trilomix/GDS3D) is a separate C++/OpenGL viewer
that renders a GDSII layout as triangles and never builds CAD geometry. That
is exactly what makes it fast where FreeCAD is not, and exactly why it cannot
route, bond or run a DRC. The two tools are complementary rather than
competing: this workbench keeps a fast proxy plus the semantic layer
(contact points, footprint) that every tool actually depends on, and hands
the *looking* — and the Gmsh export — to the viewer.

It needs a process file describing every layer: where it sits in the stack,
how thick it is, what colour it is, and whether it is metal. We already hold
all of that (stackup XML, KLayout .lyp, the technology map), so generating
the file is a translation job rather than new information.

Licence note, and the reason nothing here links against GDS3D: GDS3D is
GPL-2 (forced by the Gmsh code inside it), which is incompatible with this
workbench's GPL-3-or-later for the purpose of combining them into one work.
Writing a file it can read, and launching it as a SEPARATE PROCESS, keeps
the two at arm's length and raises no licensing question at all.

Qt-free and FreeCAD-free: takes plain dictionaries, returns text.
"""

# GDS3D expresses the stack in nanometres.
_MM_TO_NM = 1.0e6

# Layers whose name marks them as a via rather than a conductor. GDS3D's
# "Metal:" flag drives net highlighting, and a via must be 0 for it to trace
# a net THROUGH the via instead of stopping at it.
_VIA_HINTS = ("via", "cont", "cnt")

# Names that mark an actual conductor. Checked against GDS3D's own shipped
# techfiles/example.txt, where Substrate and N-Well are both "Metal: 0" —
# the flag means "this is a conductor to trace nets along", not "this is
# not a via", so treating every non-via layer as metal would offer net
# highlighting on wells, implants and text layers.
_METAL_HINTS = ("metal", "alucap", "aluminium", "aluminum", "copper")

# Transparency. GDS3D's own example makes the substrate 0.5 and conductors
# opaque, which is what lets you see the metal stack through the bulk —
# the whole point of looking at a layout in 3-D.
_FILTER_CONDUCTOR = 0.0
_FILTER_OTHER = 0.5


def _hex_to_rgb01(colour, default=(0.5, 0.5, 0.5)):
    """'#rrggbb' -> (r, g, b) floats in 0..1, GDS3D's colour convention."""
    if not colour:
        return default
    s = str(colour).strip().lstrip("#")
    if len(s) == 8:          # some tools emit #aarrggbb
        s = s[2:]
    if len(s) != 6:
        return default
    try:
        return (int(s[0:2], 16) / 255.0,
                int(s[2:4], 16) / 255.0,
                int(s[4:6], 16) / 255.0)
    except ValueError:
        return default


def is_via_name(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in _VIA_HINTS)


def is_metal_name(name: str) -> bool:
    """True for a conductor layer — what GDS3D's Metal flag actually means."""
    n = (name or "").lower()
    if is_via_name(n):
        return False
    if any(h in n for h in _METAL_HINTS):
        return True
    # M1 / M2.drawing style shorthand.
    return len(n) >= 2 and n[0] == "m" and n[1].isdigit()


def layer_entries(selected_layers, lyp_layers=None, stack_mm=None,
                  default_thickness_mm: float = 0.001,
                  materials=None):
    """
    Build the per-layer records the writer needs.

    *selected_layers*  [{'layer_id': int, 'datatype': int}, ...]
    *lyp_layers*       parse_lyp()'s list, for names and colours
    *stack_mm*         {(layer, datatype): {'t_mm':…, 'z0_mm':…}} from the
                       stackup — the real heights, when a stackup is loaded
    *materials*        optional {(layer, datatype): (material, out_material)}
                       for Gmsh export, which is the only consumer of those

    A layer with no stackup entry still gets written, stacked in order at a
    nominal thickness: a viewer that shows the layout with approximate
    heights is far more useful than one that refuses to open.
    """
    by_key = {}
    for entry in (lyp_layers or []):
        try:
            by_key[(int(entry["layer_id"]), int(entry["datatype"]))] = entry
        except (KeyError, TypeError, ValueError):
            continue

    out = []
    fallback_z = 0.0
    for sel in selected_layers:
        try:
            layer = int(sel["layer_id"])
            datatype = int(sel.get("datatype", 0))
        except (KeyError, TypeError, ValueError):
            continue
        key = (layer, datatype)
        meta = by_key.get(key, {})
        name = (meta.get("name") or f"Layer{layer}_{datatype}").strip()

        stack = (stack_mm or {}).get(key)
        if stack:
            thickness_mm = float(stack.get("t_mm", default_thickness_mm))
            height_mm = float(stack.get("z0_mm", 0.0))
        else:
            thickness_mm = default_thickness_mm
            height_mm = fallback_z
            fallback_z += default_thickness_mm

        material, out_material = (materials or {}).get(key, (None, None))
        out.append({
            "name": name,
            "layer": layer,
            "datatype": datatype,
            "height_nm": height_mm * _MM_TO_NM,
            "thickness_nm": max(thickness_mm, 1e-9) * _MM_TO_NM,
            "rgb": _hex_to_rgb01(meta.get("fill-color") or meta.get("frame-color")),
            "metal": 1 if is_metal_name(name) else 0,
            "filter": _FILTER_CONDUCTOR if is_metal_name(name) else _FILTER_OTHER,
            "show": 1,
            "material": material,
            "out_material": out_material,
        })
    # Bottom-up, which is how the stack reads in the viewer's layer list.
    out.sort(key=lambda e: (e["height_nm"], e["layer"], e["datatype"]))
    return out


def render_process_file(entries, title: str = "DI-PASSIONATE export") -> str:
    """
    The process-definition text GDS3D reads with its -p option.

    Shortkey is assigned to the first ten layers only: GDS3D binds those to
    the number keys 0-9, and there is nothing to give the eleventh.
    """
    lines = [
        f"# {title}",
        "# Generated by the DI-PASSIONATE Chip-Packaging Workbench.",
        "# Heights and thicknesses are in nanometres, taken from the active",
        "# PDK stackup; colours come from the KLayout .lyp.",
        "",
    ]
    for i, e in enumerate(entries):
        lines.append(f"LayerStart: {e['name']}")
        lines.append(f"Layer: {e['layer']}")
        lines.append(f"Datatype: {e['datatype']}")
        lines.append(f"Height: {e['height_nm']:.0f}")
        lines.append(f"Thickness: {e['thickness_nm']:.0f}")
        r, g, b = e["rgb"]
        lines.append(f"Red: {r:.2f}")
        lines.append(f"Green: {g:.2f}")
        lines.append(f"Blue: {b:.2f}")
        lines.append(f"Filter: {e.get('filter', 0.0):.2f}")
        lines.append(f"Metal: {e['metal']}")
        if i < 10:
            lines.append(f"Shortkey: {i}")
        lines.append(f"Show: {e['show']}")
        if e.get("material"):
            lines.append(f"Material: {e['material']}")
        if e.get("out_material"):
            lines.append(f"OutMaterial: {e['out_material']}")
        lines.append("LayerEnd")
        lines.append("")
    return "\n".join(lines)


def write_process_file(path: str, entries, title: str = "DI-PASSIONATE export") -> str:
    import os
    text = render_process_file(entries, title)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def build_command(executable: str, process_file: str, gds_file: str,
                  topcell: str = None) -> list:
    """
    The argv for launching the viewer, per its documented interface:
        GDS3D -p <process> -i <gds> [-t <topcell>]
    Returned as a list so it is passed to the OS without a shell, which
    keeps paths containing spaces intact.
    """
    argv = [executable, "-p", process_file, "-i", gds_file]
    if topcell:
        argv += ["-t", topcell]
    return argv
