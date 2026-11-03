# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Smoke test for the integration points added between the GDS performance
mechanisms (see ui/LODManager.py's module docstring for the full picture):

  - sync_new_layer_display() — a newly-loaded layer respects the document's
    CURRENT fast-mesh state instead of always forcing full B-rep Detail.
  - invalidate_layer_mesh() / invalidate_via_block() — a real bug caught
    during this audit: _bake_layer_mesh() and _build_via_block() both reuse
    whatever companion object already exists under a given name forever,
    with no check that the source Shape hasn't changed since baking.
    ui.DetailLayerPanel's bbox-simplify toggle mutates a layer's Shape in
    place, so without invalidation the mesh/block companion would silently
    keep showing geometry baked from the Shape's *previous* contents.
"""

import Part
from FreeCAD import Base

from _harness import TestCase, load_module_from_file, new_document


def run():
    tc = TestCase("perf_mode_sync")
    perf = load_module_from_file("gds_perf_sync_test", "gds/TogglePerformanceModeCommand.py")
    via  = load_module_from_file("gds_via_sync_test", "gds/ToggleViaDetailCommand.py")

    doc = new_document("TestPerfModeSync")
    grp = doc.addObject("App::DocumentObjectGroup", "GDS_Die")
    grp.Label = "GDS_Die"

    layer = doc.addObject("Part::Feature", "Layer_Test0")
    layer.Shape = Part.makeBox(1.0, 1.0, 0.1, Base.Vector(0, 0, 0))
    grp.addObject(layer)
    doc.recompute()

    via_layer = doc.addObject("Part::Feature", "Layer_TopVia1_drawing_125")
    via_layer.Shape = Part.makeBox(0.1, 0.1, 0.1, Base.Vector(0, 0, 0))
    grp.addObject(via_layer)
    doc.recompute()

    # ── sync_new_layer_display: detail mode (default) ──────────────────────
    tc.check("starts in detail mode", not perf.is_fast_mode())
    box = {}

    def _sync_detail():
        perf.sync_new_layer_display(doc, layer)
    tc.check_raises_nothing("sync_new_layer_display (detail mode): no exception", _sync_detail)
    tc.check("detail mode: no mesh companion created",
             doc.getObject("Layer_Test0_PerfMesh") is None)

    # ── sync_new_layer_display: fast mode ───────────────────────────────────
    perf._fast_mode = True
    try:
        def _sync_fast():
            perf.sync_new_layer_display(doc, layer)
        if tc.check_raises_nothing("sync_new_layer_display (fast mode): no exception", _sync_fast):
            mesh_obj = doc.getObject("Layer_Test0_PerfMesh")
            tc.check("fast mode: mesh companion created", mesh_obj is not None)
            if mesh_obj is not None:
                tc.check("fast mode: mesh has points", len(mesh_obj.Mesh.Points) > 0)
                # Note: ViewObject visibility can't be checked headlessly —
                # obj.ViewObject is always None under freecadcmd (no GUI),
                # which is exactly what the try/except in
                # sync_new_layer_display already handles gracefully.

        # A VIA layer should be dispatched to via-block simplification, not
        # generic mesh baking, even while fast mode is on.
        def _sync_via():
            perf.sync_new_layer_display(doc, via_layer)
        if tc.check_raises_nothing("sync_new_layer_display on a via layer: no exception", _sync_via):
            tc.check(
                "via layer dispatched to via-block, not generic mesh",
                doc.getObject("Layer_TopVia1_drawing_125_PerfMesh") is None,
            )
            tc.check(
                "via layer got a via-block companion instead",
                doc.getObject("Layer_TopVia1_drawing_125_ViaBlock") is not None,
            )
    finally:
        perf._fast_mode = False

    # ── invalidate_layer_mesh: the actual bug fix ───────────────────────────
    mesh_name = "Layer_Test0_PerfMesh"
    tc.check("mesh companion exists before invalidation",
             doc.getObject(mesh_name) is not None)
    perf.invalidate_layer_mesh(doc, "Layer_Test0")
    tc.check("mesh companion removed after invalidation",
             doc.getObject(mesh_name) is None)

    # Re-baking after invalidation must produce a fresh object, not silently
    # no-op because a stale one was still sitting in the document.
    grp2 = perf._perf_mesh_group(doc)
    rebaked = perf._bake_layer_mesh(doc, layer, grp2)
    tc.check("re-bake after invalidation succeeds", rebaked is not None)

    # ── invalidate_via_block ─────────────────────────────────────────────────
    block_name = "Layer_TopVia1_drawing_125_ViaBlock"
    tc.check("via block exists before invalidation",
             doc.getObject(block_name) is not None)
    via.invalidate_via_block(doc, "Layer_TopVia1_drawing_125")
    tc.check("via block removed after invalidation",
             doc.getObject(block_name) is None)

    import FreeCAD
    FreeCAD.closeDocument(doc.Name)
    return tc.results
