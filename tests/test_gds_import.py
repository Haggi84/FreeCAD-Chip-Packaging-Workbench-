# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Smoke test for core.Core_Functionality.load_gds() — the lowest-level entry
point of the GDS import pipeline (no FreeCADGui / dialog dependency), run
directly on a small sample file rather than through the full interactive
GDSCommand.load_gds_layers() flow (file dialogs, property panel, etc.).
"""

import os

from _harness import TestCase, REPO_ROOT

from core import Core_Functionality

_SAMPLE = os.path.join(REPO_ROOT, "samples", "Sample_JZ.GDS")


def run():
    tc = TestCase("gds_import")

    if not tc.check("sample GDS file exists", os.path.exists(_SAMPLE), _SAMPLE):
        return tc.results

    box = {}

    def _get_layers():
        box["layers"] = Core_Functionality.get_gds_layer(_SAMPLE)

    if not tc.check_raises_nothing("get_gds_layer() runs without exception", _get_layers):
        return tc.results

    layer_tuples = box.get("layers")
    if not tc.check("at least one layer found", bool(layer_tuples), f"got {layer_tuples}"):
        return tc.results

    # get_gds_layer() returns raw (layer_id, datatype) tuples (used elsewhere
    # only to cross-reference against LYP layer dicts) — load_gds()'s own
    # selected_layers parameter expects a list of dicts with layer_id/datatype
    # keys (see Core_Functionality.py:824), so build that shape directly.
    selected_layers = [{"layer_id": lid, "datatype": dt} for (lid, dt) in layer_tuples]

    def _load():
        box["shapes"] = Core_Functionality.load_gds(_SAMPLE, selected_layers)

    if not tc.check_raises_nothing("load_gds() runs without exception", _load):
        return tc.results

    shapes = box.get("shapes")
    tc.check("load_gds returns entries", bool(shapes), f"got {shapes!r}")

    for entry in (shapes or []):
        name = f"layer {entry.get('layer_id')}/{entry.get('datatype')}"
        if entry.get("is_mesh"):
            tc.check(f"{name}: mesh present", entry.get("mesh") is not None)
        else:
            shp = entry.get("shape")
            tc.check(f"{name}: shape present", shp is not None)
            if shp is not None:
                tc.check(f"{name}: shape valid", shp.isValid() and not shp.isNull())

    return tc.results
