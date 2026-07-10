# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Smoke test for the perimeter-walk auto-numbering logic in
leadframe/PinNumberingCommand.py.  This feature was built but never actually
exercised in a live FreeCAD session before this test — it checks that
_auto_number_from_pin1() produces a valid, gap-free 1..N assignment in both
directions, starting from an arbitrary chosen "pin 1" lead.
"""

import FreeCAD

from _harness import TestCase, load_module_from_file, new_document

import core.leadframe as leadframe

_QFN_CONFIG = {
    "frame_type": "QFN (Quad Flat No-lead)",
    "frame_length": 4.0,
    "frame_width": 4.0,
    "frame_thickness": 0.9,
    "material": "Copper",
    "left_lead_count": 4,
    "right_lead_count": 4,
    "top_lead_count": 4,
    "bottom_lead_count": 4,
    "lead_width": 0.3,
    "lead_pitch": 0.65,
    "inner_lead_length": 0.4,
    "has_die_paddle": True,
}
_EXPECTED_LEADS = 16


def run():
    tc = TestCase("pin_numbering")
    pn = load_module_from_file("lf_pinnum_test", "leadframe/PinNumberingCommand.py")

    doc = new_document("TestPinNumbering")
    leadframe.build_leadframe(_QFN_CONFIG, doc)

    leads = pn._lead_objects(doc)
    if not tc.check("leads found for numbering", len(leads) == _EXPECTED_LEADS,
                     f"got {len(leads)}"):
        FreeCAD.closeDocument(doc.Name)
        return tc.results

    pin1 = leads[0]
    maps = {}

    for clockwise in (False, True):
        label = "clockwise" if clockwise else "counter-clockwise"
        mapping = pn._auto_number_from_pin1(doc, leads, pin1, clockwise)
        maps[clockwise] = mapping

        tc.check(f"{label}: assigns a number to every lead",
                 set(mapping.keys()) == {l.Name for l in leads})
        values = sorted(mapping.values())
        tc.check(f"{label}: pin numbers are exactly 1..N, no gaps/dupes",
                 values == list(range(1, len(leads) + 1)), f"got {values}")
        tc.check(f"{label}: pin 1 assigned to the clicked lead",
                 mapping.get(pin1.Name) == 1)

    tc.check(
        "clockwise and counter-clockwise produce different orderings",
        maps[False] != maps[True],
    )

    FreeCAD.closeDocument(doc.Name)
    return tc.results
