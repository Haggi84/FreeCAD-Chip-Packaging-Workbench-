# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.thermal_export.

A thermal model can be wrong without looking wrong, so the checks here are
on the numbers that decide the physics: each material's exported VOLUME
(re-read from the STEP file, not from the shapes that were written), that an
overlap is resolved in favour of the part the filler surrounds, that nothing
without a material slips in, and the boundary conditions.

When FreeCAD's bundled Gmsh is available the generated .geo is also meshed,
and the mesh is checked for what the script promises: one physical volume
per material, coordinates in metres, parts that share nodes across their
interfaces — without that, heat could not cross from one part to the next —
and a heat-sink surface that is exactly the underside.
"""

import csv
import json
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict

import FreeCAD
import Part

from _harness import TestCase, new_document

import core.materials as materials
import core.thermal_export as thermal_export

V = FreeCAD.Vector

_BOUNDARY = {"die_power_W": 1.0, "heat_sink_temperature_C": 30.0,
             "convection_W_per_m2K": 15.0, "ambient_temperature_C": 20.0}


def run():
    tc = TestCase("thermal_export")
    _check_export(tc)
    _check_unresolvable_overlap(tc)
    _check_refusal(tc)
    return tc.results


def _box(doc, name, size, at, **props):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = Part.makeBox(*size, at)
    for prop, value in props.items():
        ptype = "App::PropertyBool" if isinstance(value, bool) else "App::PropertyString"
        obj.addProperty(ptype, prop, "Test", "")
        setattr(obj, prop, value)
    return obj


def _build_package(doc):
    """
    A die on a paddle inside a mould body, with a wire and a bump:
      paddle  3 x 3 x 0.2 copper, fully inside the mould body's slab
      mould   4 x 4 x 0.2 — overlaps the paddle by 1.8 mm³
      die     1 x 1 x 0.2 on top of the paddle
      wire    0.5 x 0.05 x 0.05 on the paddle, bump overlapping its end
    """
    _box(doc, "DiePaddle", (3, 3, 0.2), V(-1.5, -1.5, 0),
         IsDiePaddle=True, LeadframeMaterial="Copper")
    _box(doc, "LeadframeBody", (4, 4, 0.2), V(-2, -2, 0))
    _box(doc, "Chip_Block", (1, 1, 0.2), V(-0.5, -0.5, 0.2), IsChipProxy=True)
    _box(doc, "BondWire_001", (0.5, 0.05, 0.05), V(0.6, 0, 0.2))
    # Wire spans x 0.6..1.1; the bump spans x 1.05..1.15, so they share
    # 0.05 x 0.05 x 0.05 = 0.000125 mm³ at the wire's end.
    _box(doc, "WireBump_Bal_001", (0.1, 0.05, 0.05), V(1.05, 0, 0.2), IsWireBump=True)
    _box(doc, "Mystery", (1, 1, 1), V(20, 0, 0))
    doc.recompute()


def _step_volume(path):
    shape = Part.read(path)
    return sum(s.Volume for s in shape.Solids), len(shape.Solids)


def _check_export(tc):
    doc = new_document("ThermalPkg")
    out_dir = tempfile.mkdtemp(prefix="thermal_export_test_")
    try:
        _build_package(doc)
        materials.assign_materials(doc)

        summary = thermal_export.export_thermal_model(
            doc, out_dir, basename="pkg", boundary=_BOUNDARY)
        tc.check("exports the four materials present, in library order",
                  summary["materials"] == ["Silicon", "Copper", "Gold",
                                           "Epoxy mould compound"],
                  str(summary["materials"]))

        expected_files = ["pkg_silicon.step", "pkg_copper.step", "pkg_gold.step",
                          "pkg_epoxy_mould_compound.step", "pkg.geo",
                          "pkg_materials.csv", "pkg_manifest.json"]
        tc.check("writes one STEP per material plus the .geo, CSV and manifest",
                  all(os.path.isfile(os.path.join(out_dir, f)) for f in expected_files)
                  and summary["files"] == expected_files,
                  str(summary["files"]))

        volumes = {}
        for name in ("silicon", "copper", "gold", "epoxy_mould_compound"):
            volumes[name] = _step_volume(os.path.join(out_dir, f"pkg_{name}.step"))
        tc.check("silicon: the die, 0.2 mm³",
                  abs(volumes["silicon"][0] - 0.2) < 1e-6, str(volumes["silicon"]))
        tc.check("copper: the paddle, untouched by the mould body, 1.8 mm³",
                  abs(volumes["copper"][0] - 1.8) < 1e-6, str(volumes["copper"]))
        tc.check("mould compound: cut by the paddle it surrounds — 3.2 - 1.8 = 1.4 mm³, "
                  "not counted twice",
                  abs(volumes["epoxy_mould_compound"][0] - 1.4) < 1e-6,
                  str(volumes["epoxy_mould_compound"]))
        # 0.00125 (wire) + 0.00025 (bump) - 0.000125 (shared) = 0.001375 mm³
        tc.check("gold: wire and overlapping bump fused into one solid, the shared "
                  "volume counted once — 0.001375 mm³",
                  abs(volumes["gold"][0] - 0.001375) < 1e-9 and volumes["gold"][1] == 1,
                  str(volumes["gold"]))

        with open(os.path.join(out_dir, "pkg_manifest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        tc.check("manifest: the part without a material is listed as left out",
                  {"object": "Mystery", "reason": "no material assigned"} in manifest["skipped"],
                  str(manifest["skipped"]))
        resolved = manifest["overlaps_resolved"]
        tc.check("manifest: the resolved overlap names the filler, what cut it, "
                  "and how much went",
                  len(resolved) == 1 and resolved[0]["filler"] == "LeadframeBody"
                  and resolved[0]["cut_by"] == ["DiePaddle"]
                  and abs(resolved[0]["removed_mm3"] - 1.8) < 1e-6,
                  str(resolved))
        tc.check("manifest: nothing is left overlapping",
                  manifest["overlaps_remaining"] == [], str(manifest["overlaps_remaining"]))
        tc.check("manifest: carries each material's thermal properties",
                  next(m for m in manifest["materials"] if m["name"] == "Copper")
                  ["thermal_conductivity_W_per_mK"]
                  == materials.LIBRARY["Copper"].thermal_conductivity)

        bc = manifest["boundary_conditions"]
        tc.check("boundary: the heat sink carries the requested temperature",
                  bc["HeatSink"]["temperature_C"] == 30.0
                  and bc["HeatSink"]["physical_surface"] == "HeatSink", str(bc["HeatSink"]))
        tc.check("boundary: convection carries the coefficient and ambient temperature",
                  bc["Convection"]["heat_transfer_coefficient_W_per_m2K"] == 15.0
                  and bc["Convection"]["ambient_temperature_C"] == 20.0, str(bc["Convection"]))
        # 1 W in 0.2 mm³ = 0.2e-9 m³ of silicon.
        tc.check("boundary: the die power becomes a power density over the silicon — "
                  "1 W / 0.2e-9 m³ = 5e9 W/m³",
                  bc["HeatSource"]["physical_volume"] == "Silicon"
                  and abs(bc["HeatSource"]["power_density_W_per_m3"] / 5e9 - 1.0) < 1e-9,
                  str(bc["HeatSource"]))

        with open(os.path.join(out_dir, "pkg_materials.csv"), encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        tc.check("CSV: one row per material, with its physical volume tag",
                  [(r["material"], r["physical_volume"]) for r in rows]
                  == [("Silicon", "1"), ("Copper", "2"), ("Gold", "3"),
                      ("Epoxy mould compound", "4")],
                  str(rows))

        with open(os.path.join(out_dir, "pkg.geo"), encoding="utf-8") as fh:
            geo = fh.read()
        tc.check(".geo: converts millimetres to metres on import",
                  'Geometry.OCCTargetUnit = "M";' in geo)
        tc.check(".geo: glues the parts before naming them",
                  "Coherence;" in geo
                  and geo.index("Coherence;") < geo.index("Physical Volume"))
        tc.check(".geo: one physical volume per material",
                  all(f'Physical Volume("{name}", {tag})' in geo
                      for name, tag in (("Silicon", 1), ("Copper", 2), ("Gold", 3),
                                        ("Epoxy mould compound", 4))))
        tc.check(".geo: names the heat-sink and convection surfaces",
                  'Physical Surface("HeatSink", 1)' in geo
                  and 'Physical Surface("Convection", 2)' in geo)

        _check_gmsh(tc, out_dir)
    finally:
        FreeCAD.closeDocument(doc.Name)
        shutil.rmtree(out_dir, ignore_errors=True)


def _gmsh_executable():
    home = FreeCAD.getHomePath()
    for name in ("gmsh.exe", "gmsh"):
        candidate = os.path.join(home, "bin", name)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("gmsh")


def _read_msh2(path):
    """Physical names by (dim, tag), and per physical tag the element count
    and node ids of tetrahedra and triangles, plus every node's coordinates,
    from a Gmsh 2.2 ASCII mesh."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    names = {}
    tets, tris = defaultdict(int), defaultdict(int)
    tet_nodes, tri_nodes = defaultdict(set), defaultdict(set)
    coords = {}
    i = 0
    while i < len(lines):
        section = lines[i].strip()
        if section in ("$PhysicalNames", "$Nodes", "$Elements"):
            count = int(lines[i + 1])
            for row in lines[i + 2:i + 2 + count]:
                fields = row.split()
                if section == "$PhysicalNames":
                    names[(int(fields[0]), int(fields[1]))] = row.split('"')[1]
                elif section == "$Nodes":
                    coords[fields[0]] = tuple(float(v) for v in fields[1:4])
                else:
                    element_type, n_tags = int(fields[1]), int(fields[2])
                    tag = int(fields[3])
                    element_nodes = fields[3 + n_tags:]
                    if element_type == 4:          # 4-node tetrahedron
                        tets[tag] += 1
                        tet_nodes[tag].update(element_nodes)
                    elif element_type == 2:        # 3-node triangle
                        tris[tag] += 1
                        tri_nodes[tag].update(element_nodes)
            i += count + 2
        else:
            i += 1
    return names, tets, tet_nodes, tris, tri_nodes, coords


def _check_gmsh(tc, out_dir):
    exe = _gmsh_executable()
    if exe is None:
        tc.skip("gmsh: meshes the generated .geo", "Gmsh is not installed here")
        return
    msh = os.path.join(out_dir, "pkg.msh")
    try:
        proc = subprocess.run(
            [exe, "pkg.geo", "-3", "-clmax", "0.001", "-format", "msh2",
             "-o", msh, "-v", "2"],
            cwd=out_dir, capture_output=True, text=True, timeout=300)
    except Exception as exc:
        tc.check("gmsh: runs on the generated .geo", False, str(exc))
        return
    if not tc.check("gmsh: meshes the generated .geo without error",
                     proc.returncode == 0 and os.path.isfile(msh),
                     (proc.stdout or "")[-2000:] + (proc.stderr or "")[-2000:]):
        return

    names, tets, tet_nodes, tris, tri_nodes, coords = _read_msh2(msh)
    volumes = {tag: name for (dim, tag), name in names.items() if dim == 3}
    surfaces = {tag: name for (dim, tag), name in names.items() if dim == 2}
    tc.check("gmsh: the physical volumes are the four materials",
              volumes == {1: "Silicon", 2: "Copper", 3: "Gold", 4: "Epoxy mould compound"},
              str(volumes))
    tc.check("gmsh: every material volume has elements",
              all(tets.get(tag, 0) > 0 for tag in (1, 2, 3, 4)), str(dict(tets)))
    max_coord = max(abs(c) for xyz in coords.values() for c in xyz)
    tc.check("gmsh: coordinates are in metres (the package is 4 mm = 0.004 m wide)",
              0.0015 < max_coord < 0.0025, str(max_coord))
    for a, b, what in ((1, 2, "die and paddle"), (2, 4, "paddle and mould compound"),
                       (3, 2, "wire and paddle")):
        tc.check(f"gmsh: {what} share nodes across their interface, so heat can cross it",
                  bool(tet_nodes[a] & tet_nodes[b]),
                  f"{volumes.get(a)} / {volumes.get(b)}")

    tc.check("gmsh: the boundary surfaces are HeatSink and Convection",
              surfaces == {1: "HeatSink", 2: "Convection"}, str(surfaces))
    sink_z = [coords[n][2] for n in tri_nodes[1]]
    tc.check("gmsh: the heat sink is meshed, and is exactly the underside (z = 0)",
              tris.get(1, 0) > 0 and all(abs(z) < 1e-9 for z in sink_z),
              f"{tris.get(1, 0)} triangles, z range {min(sink_z, default=None)}..{max(sink_z, default=None)}")
    conv_z = [coords[n][2] for n in tri_nodes[2]]
    tc.check("gmsh: convection covers the rest of the outside, including the top",
              tris.get(2, 0) > 0 and max(conv_z, default=0.0) > 0.00039,
              f"{tris.get(2, 0)} triangles, top at {max(conv_z, default=None)}")


def _check_unresolvable_overlap(tc):
    doc = new_document("ThermalOverlap")
    out_dir = tempfile.mkdtemp(prefix="thermal_overlap_test_")
    try:
        _box(doc, "Chip_Block", (1, 1, 0.2), V(0, 0, 0), IsChipProxy=True)
        _box(doc, "Trace_001", (0.4, 0.4, 0.4), V(0.3, 0.3, 0.1), IsRoutingTrace=True)
        doc.recompute()
        materials.assign_materials(doc)
        summary = thermal_export.export_thermal_model(doc, out_dir, basename="overlap")
        remaining = summary["overlaps_remaining"]
        tc.check("copper inside silicon is reported, not silently resolved — there "
                  "is no safe automatic answer",
                  len(remaining) == 1
                  and set(remaining[0]["objects"]) == {"Chip_Block", "Trace_001"}
                  and abs(remaining[0]["overlap_mm3"] - 0.4 * 0.4 * 0.1) < 1e-9,
                  str(remaining))
        tc.check("...and neither part is changed",
                  abs(_step_volume(os.path.join(out_dir, "overlap_silicon.step"))[0] - 0.2) < 1e-9)
        tc.check("without explicit boundary conditions the defaults are written",
                  summary["boundary_conditions"]["HeatSource"]["power_W"]
                  == thermal_export.DEFAULT_BOUNDARY["die_power_W"])
    finally:
        FreeCAD.closeDocument(doc.Name)
        shutil.rmtree(out_dir, ignore_errors=True)


def _check_refusal(tc):
    doc = new_document("ThermalNothing")
    out_dir = tempfile.mkdtemp(prefix="thermal_nothing_test_")
    try:
        _box(doc, "Mystery", (1, 1, 1), V(0, 0, 0))
        doc.recompute()
        materials.assign_materials(doc)
        try:
            thermal_export.export_thermal_model(doc, out_dir)
            raised = False
        except ValueError:
            raised = True
        tc.check("a document with no materials is refused rather than exported empty",
                  raised)
        tc.check("...and nothing is written", os.listdir(out_dir) == [])
    finally:
        FreeCAD.closeDocument(doc.Name)
        shutil.rmtree(out_dir, ignore_errors=True)
