# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Export a package assembly for thermal simulation.

Every physical part with a material (see core.materials) is written out,
grouped by material, together with what a mesher and a solver need to turn
it into a model. Everything goes into one directory:

  <base>_<material>.step   every solid of that material
  <base>.geo               Gmsh script: imports the STEP files, glues shared
                           faces so the mesh is conformal across parts, and
                           names one physical volume per material
  <base>_materials.csv     the properties to enter into the solver
  <base>_manifest.json     which object went where, part volumes, overlaps,
                           and every part left out and why

Units: FreeCAD works in millimetres and so do the STEP files. The .geo
converts to metres on import, so the SI properties in the CSV apply as they
are.

Two things are fixed up on the way, because a model built straight from the
document would be wrong without saying so:

  * Overlaps with a filler. A leadframe body is a solid box drawn around its
    leads, so the same volume is both copper and mould compound. The filler
    is cut by the parts it overlaps. Two non-fillers overlapping (metal in
    silicon, say) has no safe automatic answer and is only reported.
  * Overlaps within one material, such as a bump over the end of its wire,
    are fused so the shared volume is meshed once.

Parts without a material are left out and listed, never guessed.

Qt-free, so it is testable headlessly.
"""

import csv
import datetime
import json
import os
import re

import FreeCAD
import Part

from core import materials

# Below this a boolean result is rounding, not an overlap.
_VOLUME_TOL_MM3 = 1e-9


def _slug(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", str(name)).strip("_").lower() or "model"


def _global_shape(obj):
    """The object's shape where it really is — including the placement of any
    App::Part it sits in, which obj.Shape alone does not carry."""
    shape = obj.Shape.copy()
    try:
        shape.Placement = obj.getGlobalPlacement()
    except Exception:
        pass
    return shape


def _bbox_overlap(a, b):
    return (a.XMin < b.XMax and b.XMin < a.XMax
            and a.YMin < b.YMax and b.YMin < a.YMax
            and a.ZMin < b.ZMax and b.ZMin < a.ZMax)


def _common_volume(a, b):
    if not _bbox_overlap(a.BoundBox, b.BoundBox):
        return 0.0
    try:
        return a.common(b).Volume
    except Exception:
        return 0.0


def _as_shape(solids):
    solids = [s for s in solids if s.Volume > _VOLUME_TOL_MM3]
    if not solids:
        return None
    return solids[0] if len(solids) == 1 else Part.makeCompound(solids)


# ── collecting ───────────────────────────────────────────────────────────────

def collect_parts(doc):
    """
    ([{"object", "material", "shape"}], [(object, reason)]) — every physical
    part with a material, and every one left out.
    """
    parts, skipped = [], []
    for obj in materials.physical_parts(doc):
        mat = materials.material_of(obj)
        if mat is None:
            skipped.append((obj.Name, "no material assigned"))
            continue
        shape = _as_shape(_global_shape(obj).Solids)
        if shape is None:
            skipped.append((obj.Name, "no solid volume"))
            continue
        parts.append({"object": obj.Name, "material": mat.name, "shape": shape})
    return parts, skipped


def _is_filler(part):
    return materials.LIBRARY[part["material"]].kind in materials.FILLER_KINDS


def resolve_overlaps(parts):
    """
    Cut every filler part by the non-filler parts it overlaps, in place, and
    report the overlaps that cannot be resolved automatically.

    A filler that is cut away entirely has its "shape" set to None.

    Returns (resolved, remaining):
        resolved  [{"filler", "cut_by": [objects], "removed_mm3"}]
        remaining [{"objects": [a, b], "materials": [ma, mb], "overlap_mm3"}]
    """
    fillers = [p for p in parts if _is_filler(p)]
    solids = [p for p in parts if not _is_filler(p)]
    resolved, remaining = [], []

    for filler in fillers:
        tools = [s for s in solids
                 if _common_volume(filler["shape"], s["shape"]) > _VOLUME_TOL_MM3]
        if not tools:
            continue
        before = filler["shape"].Volume
        try:
            cut = filler["shape"].cut([t["shape"] for t in tools])
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[ThermalExport] could not cut {filler['object']} by the parts "
                f"it overlaps: {exc}\n")
            continue
        filler["shape"] = _as_shape(cut.Solids)
        after = filler["shape"].Volume if filler["shape"] is not None else 0.0
        resolved.append({
            "filler": filler["object"],
            "cut_by": [t["object"] for t in tools],
            "removed_mm3": before - after,
        })

    live = [p for p in parts if p["shape"] is not None]
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if a["material"] == b["material"]:
                continue            # fused at export — see _merge_overlapping
            if _is_filler(a) != _is_filler(b):
                continue            # cut above
            overlap = _common_volume(a["shape"], b["shape"])
            if overlap > _VOLUME_TOL_MM3:
                remaining.append({
                    "objects": [a["object"], b["object"]],
                    "materials": [a["material"], b["material"]],
                    "overlap_mm3": overlap,
                })
    return resolved, remaining


def _merge_overlapping(shapes):
    """
    Fuse shapes of one material that overlap, so their shared volume is
    meshed once. Disjoint shapes are left alone — fusing hundreds of wires
    that never touch would only cost time.
    """
    parent = list(range(len(shapes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(shapes)):
        for j in range(i + 1, len(shapes)):
            if _common_volume(shapes[i], shapes[j]) > _VOLUME_TOL_MM3:
                parent[find(i)] = find(j)

    clusters = {}
    for i, shape in enumerate(shapes):
        clusters.setdefault(find(i), []).append(shape)

    out = []
    for members in clusters.values():
        if len(members) == 1:
            out.append(members[0])
            continue
        try:
            fused = members[0].fuse(members[1:]).removeSplitter()
            out.extend(s for s in fused.Solids if s.Volume > _VOLUME_TOL_MM3)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[ThermalExport] fusing overlapping parts failed, exporting "
                f"them separately: {exc}\n")
            out.extend(members)
    return out


# ── writing ──────────────────────────────────────────────────────────────────

def gmsh_script(base, entries):
    """
    The .geo text for *entries* [(material, step_file, physical_tag)].

    Physical volumes are defined after Coherence on purpose: the parts are
    disjoint by then, so gluing their shared faces leaves each volume whole
    and its tag unchanged.
    """
    lines = [
        f"// Thermal model '{base}' — written by the DI-PASSIONATE Chip-Packaging Workbench.",
        f"// Mesh it with:  gmsh {base}.geo -3",
        "//",
        "// The STEP files are in millimetres; OCCTargetUnit converts them to metres",
        f"// on import, so the SI properties in {base}_materials.csv apply unchanged.",
        'SetFactory("OpenCASCADE");',
        'Geometry.OCCTargetUnit = "M";',
        "",
    ]
    for name, step_file, tag in entries:
        lines.append(f'v_{tag}() = ShapeFromFile("{step_file}");   // {name}')
    lines += [
        "",
        "// Glue coincident faces so heat can cross from one part to the next.",
        "// Without it every part is meshed on its own and every interface is open.",
        "Coherence;",
        "",
    ]
    for name, _step_file, tag in entries:
        lines.append(f'Physical Volume("{name}", {tag}) = {{v_{tag}()}};')
    lines.append('Physical Surface("Exterior") = CombinedBoundary{ Volume{:}; };')
    return "\n".join(lines) + "\n"


def _workbench_version():
    try:
        from version import VERSION_STRING
        return VERSION_STRING
    except Exception:
        return "unknown"


def export_thermal_model(doc, out_dir, basename=None, fix_overlaps=True):
    """
    Write the thermal model of *doc* into *out_dir*. Returns a summary:
        {"directory", "files", "materials", "parts",
         "skipped", "overlaps_resolved", "overlaps_remaining"}

    Raises ValueError when there is nothing to export.
    """
    if doc is None:
        raise ValueError("No document to export.")
    base = _slug(basename or doc.Label or doc.Name)

    parts, skipped = collect_parts(doc)
    if not parts:
        raise ValueError(
            "No part in the document has a material. Run Assign Materials "
            "first, or set PackageMaterial on the parts by hand.")

    resolved, remaining = [], []
    if fix_overlaps:
        resolved, remaining = resolve_overlaps(parts)
        for p in parts:
            if p["shape"] is None:
                skipped.append((p["object"], "entirely inside other parts"))
        parts = [p for p in parts if p["shape"] is not None]

    os.makedirs(out_dir, exist_ok=True)

    by_material = {}
    for p in parts:
        by_material.setdefault(p["material"], []).append(p)
    ordered = [name for name in materials.LIBRARY if name in by_material]

    entries, records, files = [], [], []
    for tag, name in enumerate(ordered, start=1):
        group = by_material[name]
        step_file = f"{base}_{_slug(name)}.step"
        shapes = _merge_overlapping([p["shape"] for p in group])
        Part.makeCompound(shapes).exportStep(os.path.join(out_dir, step_file))
        files.append(step_file)
        entries.append((name, step_file, tag))

        mat = materials.LIBRARY[name]
        records.append({
            "name": mat.name,
            "kind": mat.kind,
            "physical_volume": tag,
            "step_file": step_file,
            "thermal_conductivity_W_per_mK": mat.thermal_conductivity,
            "density_kg_per_m3": mat.density,
            "specific_heat_J_per_kgK": mat.specific_heat,
            "cte_ppm_per_K": mat.cte_ppm,
            "note": mat.note,
            "parts": [{"object": p["object"],
                       "volume_mm3": round(p["shape"].Volume, 12)} for p in group],
        })

    geo_file = f"{base}.geo"
    with open(os.path.join(out_dir, geo_file), "w", encoding="utf-8") as fh:
        fh.write(gmsh_script(base, entries))
    files.append(geo_file)

    csv_file = f"{base}_materials.csv"
    with open(os.path.join(out_dir, csv_file), "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["material", "physical_volume", "step_file",
                         "thermal_conductivity_W_per_mK", "density_kg_per_m3",
                         "specific_heat_J_per_kgK", "cte_ppm_per_K", "parts"])
        for r in records:
            writer.writerow([r["name"], r["physical_volume"], r["step_file"],
                             r["thermal_conductivity_W_per_mK"], r["density_kg_per_m3"],
                             r["specific_heat_J_per_kgK"], r["cte_ppm_per_K"],
                             len(r["parts"])])
    files.append(csv_file)

    manifest_file = f"{base}_manifest.json"
    manifest = {
        "generator": f"DI-PASSIONATE Chip-Packaging Workbench {_workbench_version()}",
        "document": doc.Name,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "units": {"geometry": "mm (converted to m by the .geo)", "properties": "SI"},
        "property_note": ("Nominal bulk values at about 25 °C — a starting "
                          "point, not a datasheet."),
        "gmsh_script": geo_file,
        "materials": records,
        "overlaps_resolved": resolved,
        "overlaps_remaining": remaining,
        "skipped": [{"object": n, "reason": r} for n, r in skipped],
    }
    with open(os.path.join(out_dir, manifest_file), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    files.append(manifest_file)

    FreeCAD.Console.PrintMessage(
        f"[ThermalExport] {len(parts)} part(s) in {len(ordered)} material(s) "
        f"written to {out_dir}; {len(skipped)} left out, "
        f"{len(resolved)} overlap(s) resolved, {len(remaining)} remaining\n")

    return {
        "directory": out_dir,
        "files": files,
        "materials": ordered,
        "parts": len(parts),
        "skipped": skipped,
        "overlaps_resolved": resolved,
        "overlaps_remaining": remaining,
    }
