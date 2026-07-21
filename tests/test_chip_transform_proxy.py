# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Smoke test for the chip-proxy-aware selection expansion added to
gds/ChipTransformCommand.py as part of multi-chip layout ("Phase 2"):
selecting EITHER a chip proxy's block alone OR just one of its pad markers
must resolve to the WHOLE proxy (block + every pad) so moving/rotating a
chip never desyncs its contact points from the die surface they sit on.

_expand_selection() is exercised directly (split out from
_selected_objects() specifically so this doesn't need a live
FreeCADGui.Selection, unavailable under freecadcmd — see that function's
docstring in ChipTransformCommand.py).
"""

import Part
from FreeCAD import Base

from _harness import TestCase, load_module_from_file, new_document

import core.chip_proxy as chip_proxy

_PROXY_DATA = {
    "source_gds": "fake.gds",
    "source_lyp": None,
    "source_map": None,
    "source_xml": None,
    "footprint_mm": (0.0, 0.0, 1.0, 1.0),
    "thickness_mm": 0.3,
    "z0_mm": 0.0,
    "thickness_source": "no_stackup_default",
    "pads": [
        {"name": "Pad", "x_mm": 0.2, "y_mm": 0.2},
        {"name": "Pad", "x_mm": 0.8, "y_mm": 0.8},
    ],
}


def run():
    tc = TestCase("chip_transform_proxy")
    ctc = load_module_from_file("chip_transform_proxy_test", "gds/ChipTransformCommand.py")

    doc = new_document("TestChipTransformProxy")
    block = chip_proxy.build_chip_proxy_object(doc, _PROXY_DATA, name="TestChip")
    pads  = [o for o in doc.Objects if getattr(o, "IsContactPoint", False)]
    tc.check("fixture: 2 pad markers created", len(pads) == 2, f"got {len(pads)}")

    # An unrelated, non-proxy object — must be a complete no-op for
    # _proxy_group_of and pass through _expand_selection unchanged.
    other = doc.addObject("Part::Feature", "UnrelatedThing")
    other.Shape = Part.makeBox(1, 1, 1, Base.Vector(5, 5, 0))

    # ── _proxy_group_of ──────────────────────────────────────────────────────
    grp_from_block = ctc._proxy_group_of(block)
    tc.check("_proxy_group_of(block) returns the containing Proxy group",
              grp_from_block is not None and grp_from_block.TypeId == "App::DocumentObjectGroup")

    if pads:
        grp_from_pad = ctc._proxy_group_of(pads[0])
        tc.check("_proxy_group_of(pad marker) returns the SAME containing group",
                  grp_from_pad is not None and grp_from_block is not None
                  and grp_from_pad.Name == grp_from_block.Name)

    tc.check("_proxy_group_of(unrelated object) returns None",
              ctc._proxy_group_of(other) is None)

    # ── _expand_selection: selecting just the block pulls in every pad ──────
    expanded_from_block = ctc._expand_selection([block], doc=doc)
    names = {o.Name for o in expanded_from_block}
    tc.check("expanding from the block alone includes the block itself",
              block.Name in names)
    tc.check("expanding from the block alone includes every pad marker (no desync)",
              all(p.Name in names for p in pads),
              f"got {names}, expected pads {[p.Name for p in pads]}")

    # ── _expand_selection: selecting just ONE pad also resolves atomically ──
    if pads:
        expanded_from_pad = ctc._expand_selection([pads[0]], doc=doc)
        names2 = {o.Name for o in expanded_from_pad}
        tc.check("expanding from a single pad also includes the block",
                  block.Name in names2)
        tc.check("expanding from a single pad also includes every other pad",
                  all(p.Name in names2 for p in pads),
                  f"got {names2}")

    # ── regression guard: unrelated, non-proxy objects still behave as before ──
    expanded_other = ctc._expand_selection([other], doc=doc)
    tc.check("expanding a plain, unrelated object returns just itself",
              [o.Name for o in expanded_other] == [other.Name],
              f"got {[o.Name for o in expanded_other]}")

    import FreeCAD
    FreeCAD.closeDocument(doc.Name)
    return tc.results
