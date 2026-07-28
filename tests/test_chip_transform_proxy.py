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

    # ── multi-chip scoping: the "Chip Proxy (pick one)" scope must move
    # ONLY the chosen chip, never a second proxy imported alongside it —
    # this is the actual bug report this scope was added to fix (importing
    # 2+ chips as proxies and needing to move just one, together with the
    # package/leadframe and/or PCB). ──────────────────────────────────────
    block2 = chip_proxy.build_chip_proxy_object(doc, _PROXY_DATA, name="TestChip2")
    pads2  = [o for o in doc.Objects
              if getattr(o, "IsContactPoint", False) and getattr(o, "SourceObject", "") == block2.Name]
    tc.check("fixture: second proxy's 2 pad markers created", len(pads2) == 2, f"got {len(pads2)}")

    groups = ctc._chip_proxy_groups(doc)
    tc.check("_chip_proxy_groups finds both imported proxies",
              len(groups) == 2, f"got {[g.Name for g in groups]}")

    grp1 = ctc._proxy_group_of(block)
    grp2 = ctc._proxy_group_of(block2)
    tc.check("the two proxies have distinct containing groups",
              grp1 is not None and grp2 is not None and grp1.Name != grp2.Name)

    expanded_grp1 = {o.Name for o in ctc._expand_selection([grp1], doc=doc)}
    tc.check("selecting proxy 1's group moves proxy 1's block",
              block.Name in expanded_grp1)
    tc.check("selecting proxy 1's group does NOT pull in proxy 2's block",
              block2.Name not in expanded_grp1,
              f"got {expanded_grp1}")
    tc.check("selecting proxy 1's group does NOT pull in proxy 2's pads",
              all(p.Name not in expanded_grp1 for p in pads2),
              f"got {expanded_grp1}")

    # ── Package / leadframe and PCB auto-inclusion ───────────────────────────
    tc.check("_package_group: none built yet -> None", ctc._package_group(doc) is None)
    tc.check("_pcb_root_objects: none built yet -> []", ctc._pcb_root_objects(doc) == [])

    pkg_grp = doc.addObject("App::DocumentObjectGroup", "Package")
    pkg_part = doc.addObject("Part::Feature", "LeadframeBody")
    pkg_part.Shape = Part.makeBox(2, 2, 0.2, Base.Vector(-3, -3, -1))
    pkg_grp.addObject(pkg_part)

    tc.check("_package_group finds the 'Package' group",
              ctc._package_group(doc) is not None
              and ctc._package_group(doc).Name == pkg_grp.Name)

    pcb_a = doc.addObject("Part::Feature", "PCBBodyA")
    pcb_a.Shape = Part.makeBox(5, 5, 1, Base.Vector(-10, -10, -2))
    pcb_b = doc.addObject("Part::Feature", "PCBBodyB")
    pcb_b.Shape = Part.makeBox(1, 1, 1, Base.Vector(-10, -10, -3))
    pcb_a.addProperty("App::PropertyBool", "IsPCBBoard", "PCB", "")
    pcb_a.addProperty("App::PropertyString", "PCBBodyObjects", "PCB", "")
    pcb_a.IsPCBBoard = True
    pcb_a.PCBBodyObjects = f"{pcb_a.Name},{pcb_b.Name}"

    pcb_roots = ctc._pcb_root_objects(doc)
    tc.check("_pcb_root_objects resolves every root listed in PCBBodyObjects, "
              "not just the tagged anchor itself",
              {o.Name for o in pcb_roots} == {pcb_a.Name, pcb_b.Name},
              f"got {[o.Name for o in pcb_roots]}")

    # Simulate the dialog's "Chip Proxy (pick one)" scope with both
    # companion checkboxes ticked: proxy 1 + Package + PCB, still excluding
    # proxy 2 entirely.
    combined = {o.Name for o in ctc._expand_selection([grp1, pkg_grp] + pcb_roots, doc=doc)}
    tc.check("combined scope includes the chosen proxy's block",
              block.Name in combined)
    tc.check("combined scope includes the Package body",
              pkg_part.Name in combined)
    tc.check("combined scope includes both PCB root bodies",
              pcb_a.Name in combined and pcb_b.Name in combined)
    tc.check("combined scope still excludes the OTHER chip proxy",
              block2.Name not in combined and all(p.Name not in combined for p in pads2),
              f"got {combined}")

    import FreeCAD
    FreeCAD.closeDocument(doc.Name)
    return tc.results
