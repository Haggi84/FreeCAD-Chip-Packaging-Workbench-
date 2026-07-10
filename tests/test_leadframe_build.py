# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Smoke test for core.leadframe.build_leadframe() — the parametric leadframe
generator used by the Leadframe Configurator and by Pin Numbering's Auto
mode.  Builds a small QFN headlessly and checks the expected objects, tags,
and geometry come out.
"""

import FreeCAD

from _harness import TestCase, new_document

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
_EXPECTED_LEADS = 16  # 4 per side x 4 sides


def run():
    tc = TestCase("leadframe_build")
    doc = new_document("TestLeadframe")

    def _build():
        leadframe.build_leadframe(_QFN_CONFIG, doc)

    if not tc.check_raises_nothing("builds without exception", _build):
        FreeCAD.closeDocument(doc.Name)
        return tc.results

    leads = [o for o in doc.Objects if getattr(o, "IsLeadFinger", False)]
    tc.check("lead count matches config", len(leads) == _EXPECTED_LEADS,
             f"got {len(leads)}, expected {_EXPECTED_LEADS}")

    sides = {o.LeadSide for o in leads}
    tc.check("all four sides present", sides == {"L", "R", "T", "B"}, f"got {sides}")

    paddle = doc.getObject("DiePaddle")
    tc.check("die paddle created", paddle is not None)
    if paddle is not None:
        tc.check("die paddle tagged IsDiePaddle", getattr(paddle, "IsDiePaddle", False))

    body = doc.getObject("LeadframeBody")
    tc.check("leadframe body created", body is not None)
    if body is not None:
        tc.check("leadframe body has valid shape",
                 body.Shape.isValid() and not body.Shape.isNull())

    contact_points = [o for o in doc.Objects if getattr(o, "IsContactPoint", False)]
    tc.check("one contact point per lead", len(contact_points) == _EXPECTED_LEADS,
             f"got {len(contact_points)}, expected {_EXPECTED_LEADS}")

    for lead in leads:
        ok_shape = (lead.Shape.isValid() and not lead.Shape.isNull()
                    and lead.Shape.Volume > 1e-9)
        tc.check(f"lead {lead.Name} has valid, non-degenerate shape", ok_shape)

    FreeCAD.closeDocument(doc.Name)
    return tc.results
