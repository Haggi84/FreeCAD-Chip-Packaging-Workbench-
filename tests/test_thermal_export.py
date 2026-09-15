# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.thermal_export.

A thermal model can be wrong without looking wrong, so the checks here are
on the numbers that decide the physics: each material's exported VOLUME
(re-read from the STEP file, not from the shapes that were written), that an
overlap is resolved in favour of the part the filler surrounds, and that
nothing without a material slips in.

When FreeCAD's bundled Gmsh is available the generated .geo is also meshed,
and the mesh is checked for what the script promises: one physical volume
per material, coordinates in metres, and parts that share nodes across their
interfaces — without that, heat could not cross from one part to the next.
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

        summary = thermal_export.export_thermal_model(doc, out_dir, basename="pkg")
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
    """(physical volume names by tag, tet count by tag, node ids by tag,
    largest absolute coordinate) from a Gmsh 2.2 ASCII mesh."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    names, tets, nodes = {}, defaultdict(int), defaultdict(set)
    max_coord = 0.0
    i = 0
    while i < len(lines):
        section = lines[i].strip()
        if section in ("$PhysicalNames", "$Nodes", "$Elements"):
            count = int(lines[i + 1])
            for row in lines[i + 2:i + 2 + count]:
                fields = row.split()
                if section == "$PhysicalNames":
                    if fields[0] == "3":
                        names[int(fields[1])] = row.split('"')[1]
                elif section == "$Nodes":
                    max_coord = max(max_coord, *(abs(float(v)) for v in fields[1:4]))
                elif int(fields[1]) == 4:                   # 4-node tetrahedron
                    n_tags = int(fields[2])
                    tag = int(fields[3])
                    tets[tag] += 1
                    nodes[tag].update(fields[3 + n_tags:])
            i += count + 2
        else:
            i += 1
    return names, tets, nodes, max_coord


def _check_gmsh(tc, out_dir):
    exe = _gmsh_executable()
    if exe is None:
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

    names, tets, nodes, max_coord = _read_msh2(msh)
    tc.check("gmsh: the physical volumes are the four materials",
              names == {1: "Silicon", 2: "Copper", 3: "Gold", 4: "Epoxy mould compound"},
              str(names))
    tc.check("gmsh: every material volume has elements",
              all(tets.get(tag, 0) > 0 for tag in (1, 2, 3, 4)), str(dict(tets)))
    tc.check("gmsh: coordinates are in metres (the package is 4 mm = 0.004 m wide)",
              0.0015 < max_coord < 0.0025, str(max_coord))
    for a, b, what in ((1, 2, "die and paddle"), (2, 4, "paddle and mould compound"),
                       (3, 2, "wire and paddle")):
        tc.check(f"gmsh: {what} share nodes across their interface, so heat can cross it",
                  bool(nodes[a] & nodes[b]),
                  f"{names.get(a)} / {names.get(b)}")


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
