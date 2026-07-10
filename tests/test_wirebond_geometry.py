# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Smoke tests for wirebond/ManualWireBonding.py's create_bond_wire_3d().

This is the most fragile, most-iterated geometry in the plugin — the same
cut/trim logic went through roughly eight revisions in one session (z-plane
cuts, exact solid cuts, penetration depth, vertical stubs, the JEDEC
trapezoid profile) before it produced correctly connected, non-penetrating
wire bonds.  create_bond_wire_3d() is pure Part/FreeCAD.Base geometry with
no FreeCADGui dependency, so it can be tested directly with no patching.
"""

from _harness import TestCase, load_module_from_file

from FreeCAD import Base

_MIN_ZMIN_TOLERANCE = 0.05  # mm — catches real under-pad protrusion, not FP noise


def _check_solid(tc: TestCase, label: str, shape, min_zmin: float):
    if shape is None:
        tc.check(f"{label}: shape returned", False, "create_bond_wire_3d returned None")
        return
    tc.check(f"{label}: not null", not shape.isNull())
    tc.check(f"{label}: valid", shape.isValid())
    solids = shape.Solids
    tc.check(f"{label}: has >=1 solid", len(solids) >= 1, f"got {len(solids)}")
    if not solids:
        return
    vol = sum(s.Volume for s in solids)
    tc.check(f"{label}: positive volume", vol > 1e-6, f"volume={vol}")
    zmin = min(s.BoundBox.ZMin for s in solids)
    tc.check(
        f"{label}: no under-pad protrusion",
        zmin > min_zmin - _MIN_ZMIN_TOLERANCE,
        f"zmin={zmin:.4f}, expected > {min_zmin - _MIN_ZMIN_TOLERANCE:.4f}",
    )


def run():
    mwb = load_module_from_file("wirebond_mwb_test", "wirebond/ManualWireBonding.py")
    tc = TestCase("wirebond_geometry")

    A = Base.Vector(0.0, 0.0, 0.30)   # die pad
    B = Base.Vector(2.0, 0.5, 0.29)   # leadframe pad, slightly different height
    min_zmin = min(A.z, B.z)

    configs = [
        ("Ball-Wedge / cut / spline", dict(
            bond_type="Ball-Wedge", wedge_style="cut", wire_profile="spline",
            loop_height=0.3, diameter=0.025)),
        ("Ball-Wedge / solid / spline", dict(
            bond_type="Ball-Wedge", wedge_style="solid", wire_profile="spline",
            loop_height=0.3, diameter=0.025)),
        ("Wedge-Wedge / cut / spline", dict(
            bond_type="Wedge-Wedge", wedge_style="cut", wire_profile="spline",
            loop_height=0.3, diameter=0.025)),
        ("Wedge-Wedge / cut / jedec", dict(
            bond_type="Wedge-Wedge", wedge_style="cut", wire_profile="jedec",
            loop_height=0.3, diameter=0.025)),
        ("Ball-Wedge / cut / jedec", dict(
            bond_type="Ball-Wedge", wedge_style="cut", wire_profile="jedec",
            loop_height=0.3, diameter=0.025)),
    ]

    for label, config in configs:
        box = {}

        def _build(cfg=config, box=box):
            box["shape"] = mwb.create_bond_wire_3d(A, B, cfg)

        if tc.check_raises_nothing(f"{label}: builds without exception", _build):
            _check_solid(tc, label, box.get("shape"), min_zmin)

    # Degenerate case: contacts stacked in XY (start.x == end.x, start.y == end.y)
    # — this hits the L<1e-6 guard branch in create_bond_wire_3d.
    C = Base.Vector(0.0, 0.0, 0.30)
    D = Base.Vector(0.0, 0.0, 0.60)
    box = {}

    def _degenerate(box=box):
        box["shape"] = mwb.create_bond_wire_3d(
            C, D, dict(bond_type="Ball-Wedge", wedge_style="cut",
                       wire_profile="spline", loop_height=0.3, diameter=0.025)
        )

    if tc.check_raises_nothing("degenerate stacked contacts: builds without exception", _degenerate):
        shape = box.get("shape")
        tc.check("degenerate: shape returned", shape is not None)
        if shape is not None:
            tc.check("degenerate: not null", not shape.isNull())

    return tc.results
