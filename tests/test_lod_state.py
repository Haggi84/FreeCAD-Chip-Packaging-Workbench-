# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Regression test for the LODManager._scan_existing_objects reconciliation fix
made as part of the session-save/load redesign (see session/WorkbenchState.py).

Before the fix: a routing layer already promoted to DETAIL in a previous
session (real geometry object present, IsLayerPlaceholder not set) was
still reported as SOLID by a freshly constructed LODManager, because
self._states was only ever seeded from `categories` before the object scan
ran. Left unfixed, a subsequent "Load All" click would needlessly
re-tessellate an already-correct layer using today's code — a miniature
recurrence of the "replay produces different geometry than what was saved"
bug the whole save/load redesign exists to eliminate.

This only exercises the reconciliation logic directly (synthetic document +
manually-built aux dict) — a full save/restore round trip through
_save_lod_state/_restore_lod_state needs real GDS/LYP fixture files this
suite doesn't ship, and is covered instead by the manual GUI verification
steps in the workbench-state redesign plan.
"""

import Part
from FreeCAD import Base

from _harness import TestCase, load_module_from_file, new_document


def run():
    tc = TestCase("lod_state")
    lod = load_module_from_file("lod_manager_test", "ui/LODManager.py")

    doc = new_document("TestLodState")
    grp = doc.addObject("App::DocumentObjectGroup", "GDS_Die")
    grp.Label = "GDS_Die"

    # Simulate a routing layer that was promoted to DETAIL in a *previous*
    # session: real geometry, GDSLayerID/GDSDatatype set, no
    # IsLayerPlaceholder flag (placeholders are markers for NOT-yet-loaded
    # layers, and this one is meant to represent an already-loaded one).
    detail_obj = doc.addObject("Part::Feature", "Layer_Metal1_5")
    detail_obj.Shape = Part.makeBox(1, 1, 0.1, Base.Vector(0, 0, 0))
    detail_obj.addProperty("App::PropertyInteger", "GDSLayerID",  "LOD", "")
    detail_obj.addProperty("App::PropertyInteger", "GDSDatatype", "LOD", "")
    detail_obj.GDSLayerID  = 5
    detail_obj.GDSDatatype = 0
    grp.addObject(detail_obj)
    doc.recompute()

    all_layers = [{"layer_id": 5, "datatype": 0, "name": "Metal1", "fill-color": "#888888"}]
    aux = dict(
        all_layers=all_layers,
        categories={(5, 0): "routing"},   # routing -> starts SOLID in __init__
        contact_keys=set(),
        fill_layer_keys=set(),
        flat_layer_keys=set(),
        stack_mm={},
        ihp_map={},
    )

    box = {}

    def _construct():
        box["mgr"] = lod.LODManager(doc, "dummy.gds", aux)

    if not tc.check_raises_nothing("LODManager construction: no exception", _construct):
        import FreeCAD
        FreeCAD.closeDocument(doc.Name)
        return tc.results

    mgr = box["mgr"]
    tc.check(
        "restored DETAIL layer is reconciled, not left reported as SOLID",
        mgr.state((5, 0)) == lod.LODState.DETAIL,
    )
    tc.check(
        "no duplicate placeholder created for an already-detailed layer",
        doc.getObject("Layer_Metal1_5") is detail_obj,
    )

    import FreeCAD
    FreeCAD.closeDocument(doc.Name)
    return tc.results
