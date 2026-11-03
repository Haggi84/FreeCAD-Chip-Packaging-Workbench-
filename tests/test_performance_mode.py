# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Smoke test for the fast-mesh baking engine in
gds/TogglePerformanceModeCommand.py.

Scope note: this tests _bake_layer_mesh() directly against synthetic
"Layer_*" box objects, not the full apply_performance_mode() /
_enter_mesh_mode() flow.  That flow also creates a QProgressDialog and
filters candidate layers via obj.ViewObject, both of which assume a live
Qt/GUI session — reworking those for headless use is out of scope for this
first pass of smoke tests.  _bake_layer_mesh() is the part that actually
does the tessellation work, so it's the highest-value target here.
"""

import FreeCAD
import Part
from FreeCAD import Base

from _harness import TestCase, load_module_from_file, new_document


def run():
    tc = TestCase("performance_mode")
    perf = load_module_from_file("gds_perf_test", "gds/TogglePerformanceModeCommand.py")

    doc = new_document("TestPerfMode")
    grp = doc.addObject("App::DocumentObjectGroup", "GDS_Die")
    grp.Label = "GDS_Die"

    layer_names = []
    for i in range(2):
        obj = doc.addObject("Part::Feature", f"Layer_Test{i}")
        obj.Shape = Part.makeBox(1.0, 1.0, 0.1, Base.Vector(i * 2.0, 0, 0))
        grp.addObject(obj)
        layer_names.append(obj.Name)
    doc.recompute()

    perf_grp = perf._perf_mesh_group(doc)
    tc.check("perf mesh group created", perf_grp is not None)

    baked_names = []
    for name in layer_names:
        obj = doc.getObject(name)
        box = {}

        def _bake(o=obj, box=box):
            box["mesh_obj"] = perf._bake_layer_mesh(doc, o, perf_grp)

        if tc.check_raises_nothing(f"bake {name}: no exception", _bake):
            mesh_obj = box.get("mesh_obj")
            tc.check(f"bake {name}: mesh object returned", mesh_obj is not None)
            if mesh_obj is not None:
                tc.check(f"bake {name}: mesh has points", len(mesh_obj.Mesh.Points) > 0)
                baked_names.append(mesh_obj.Name)

    if baked_names:
        obj0 = doc.getObject(layer_names[0])
        mesh_again = perf._bake_layer_mesh(doc, obj0, perf_grp)
        tc.check(
            "re-bake reuses existing mesh object (idempotent)",
            mesh_again is not None and mesh_again.Name == baked_names[0],
            f"got {getattr(mesh_again, 'Name', None)}, expected {baked_names[0]}",
        )

    FreeCAD.closeDocument(doc.Name)
    return tc.results
