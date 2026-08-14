# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The die body below the lowest metal — the epitaxial layer and the silicon
substrate it sits on.

A real die is not the interconnect stack. Under the lowest drawn layer there
is an epi layer and then a couple of hundred microns of silicon, and that is
most of the die's physical thickness: on IHP SG13G2 the interconnect is
14.23 um and the body under it is 183.75 um, so what was being modelled was
under 8% of the object. Everything downstream that cares about the die as a
physical part — where a bond wire has to clear, how tall the package cavity
must be, what a thermal export contains — was working from the wrong solid.

The stackup XML already declares both, as <Dielectric> entries whose
thicknesses sum exactly to the <Substrate Offset>; parse_stackup_xml simply
discarded them. This module turns them into geometry.

Qt-free, so the derivation is testable headlessly. It uses Part only to build
the solids, in the same way the rest of core/ does.
"""

import FreeCAD
import Part

# The offset and the dielectric thicknesses come from the same file and
# should agree exactly; allow a rounding-level mismatch before distrusting it.
_SUM_TOLERANCE_UM = 0.01


def _materials(stackup_data):
    return (stackup_data or {}).get("_materials") or {}


def material_colour(stackup_data, material_name, default=(0.45, 0.45, 0.50)):
    """(r, g, b) floats for a material named in the stackup, or *default*."""
    entry = _materials(stackup_data).get(str(material_name or "").upper())
    raw = (entry or {}).get("color") or ""
    raw = raw.lstrip("#")
    if len(raw) != 6:
        return default
    try:
        return tuple(int(raw[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return default


def substrate_layers_mm(stackup_data):
    """
    The slabs below z=0, bottom-first:
        [{"name", "material", "t_mm", "z0_mm"}, ...]

    Derivation, and why it is not simply "the last two dielectrics": the
    <Dielectrics> list runs top-of-stack downwards and also contains AIR,
    the passivation and the inter-metal oxide, none of which belong below the
    die. What identifies the body is the <Substrate Offset> — the depth the
    interconnect's z=0 plane sits above the die underside. Accumulating
    dielectrics from the BOTTOM until they reach that offset picks out
    exactly the entries the offset is made of, and the sum is a check on the
    result rather than an assumption about ordering or naming.

    Returns [] when the stackup carries no offset or the thicknesses do not
    account for it — better to model nothing than to invent a body of the
    wrong depth.
    """
    data = stackup_data or {}
    offset_um = data.get("_substrate_offset_um")
    dielectrics = data.get("_dielectrics") or []
    if not offset_um or offset_um <= 0.0 or not dielectrics:
        return []

    picked, total = [], 0.0
    for entry in reversed(dielectrics):          # bottom of the stack upwards
        if total >= offset_um - _SUM_TOLERANCE_UM:
            break
        picked.append(entry)
        total += float(entry["thickness_um"])

    if abs(total - float(offset_um)) > _SUM_TOLERANCE_UM:
        FreeCAD.Console.PrintWarning(
            f"[Substrate] Dielectric thicknesses below the interconnect sum to "
            f"{total:.4f} um but the stackup declares a substrate offset of "
            f"{offset_um:.4f} um. Not modelling a die body from data that does "
            f"not agree with itself.\n"
        )
        return []

    # picked is bottom-first already, since we walked the list in reverse.
    out, z_um = [], -float(offset_um)
    for entry in picked:
        t_um = float(entry["thickness_um"])
        out.append({
            "name": entry["name"],
            "material": entry.get("material") or entry["name"],
            "t_mm": t_um / 1000.0,
            "z0_mm": z_um / 1000.0,
        })
        z_um += t_um
    return out


def total_thickness_mm(stackup_data) -> float:
    return sum(e["t_mm"] for e in substrate_layers_mm(stackup_data))


def build_substrate_objects(doc, footprint_mm, stackup_data, group=None,
                             name_prefix="GDS"):
    """
    Create one solid per slab under the die, spanning *footprint_mm*
    (xmin, ymin, xmax, ymax). Returns the created objects, bottom-first.

    The slabs are made transparent by default: they are the largest objects
    in the document by volume and would otherwise hide the entire layout
    behind a block of silicon.
    """
    entries = substrate_layers_mm(stackup_data)
    if not entries:
        return []

    xmin, ymin, xmax, ymax = (float(v) for v in footprint_mm)
    width, length = xmax - xmin, ymax - ymin
    if width <= 0.0 or length <= 0.0:
        raise ValueError(
            f"Degenerate die footprint ({width:.4f} x {length:.4f} mm) — "
            f"cannot build a substrate under it.")

    created = []
    for entry in entries:
        obj = doc.addObject("Part::Feature",
                            f"{name_prefix}_{entry['name']}")
        obj.Shape = Part.makeBox(
            width, length, entry["t_mm"],
            FreeCAD.Vector(xmin, ymin, entry["z0_mm"]))
        obj.Label = f"{entry['name']} ({entry['t_mm'] * 1000.0:.2f} µm)"

        obj.addProperty("App::PropertyBool", "IsDieBody", "Substrate",
                        "Part of the die's physical body below the lowest "
                        "drawn layer — not a routing layer")
        obj.addProperty("App::PropertyString", "StackMaterial", "Substrate",
                        "Material named for this slab in the stackup XML")
        obj.IsDieBody = True
        obj.StackMaterial = entry["material"]

        if FreeCAD.GuiUp and getattr(obj, "ViewObject", None) is not None:
            obj.ViewObject.ShapeColor = material_colour(
                stackup_data, entry["material"])
            # Silicon is the biggest thing in the document; opaque, it hides
            # every layer above it.
            obj.ViewObject.Transparency = 60
        if group is not None:
            group.addObject(obj)
        created.append(obj)

    FreeCAD.Console.PrintMessage(
        "[Substrate] " + ", ".join(
            f"{e['name']} {e['t_mm'] * 1000.0:.2f} µm" for e in entries)
        + f" under {width:.4f} x {length:.4f} mm\n")
    return created
