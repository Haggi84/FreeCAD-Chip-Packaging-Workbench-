# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.gds3d_process — translating the active PDK into a
GDS3D process-definition file.

Launching the external viewer cannot be tested here (and has not been run
against a real GDS3D binary), so the split is deliberate: everything that
can be wrong in the FILE is checked here — unit conversion, the metal/via
flag that drives net highlighting, colours, and the ordering — leaving only
the process launch itself unverified.
"""

from _harness import TestCase

import core.gds3d_process as g3


def _field(text, layer_name, field):
    """The value of *field* inside the block for *layer_name*."""
    block = None
    for chunk in text.split("LayerStart: "):
        if chunk.startswith(layer_name):
            block = chunk
            break
    if block is None:
        return None
    for line in block.splitlines():
        if line.startswith(field + ":"):
            return line.split(":", 1)[1].strip()
    return None


def run():
    tc = TestCase("gds3d_process")

    # ── colour conversion ───────────────────────────────────────────────────
    tc.check("hex -> 0..1 RGB", g3._hex_to_rgb01("#ff0000") == (1.0, 0.0, 0.0))
    tc.check("hex: mid grey round-trips sensibly",
              abs(g3._hex_to_rgb01("#808080")[0] - 0.502) < 0.01)
    tc.check("hex: an #aarrggbb value drops the alpha rather than failing",
              g3._hex_to_rgb01("#ffff0000") == (1.0, 0.0, 0.0))
    tc.check("hex: a missing or malformed colour falls back",
              g3._hex_to_rgb01(None) == (0.5, 0.5, 0.5)
              and g3._hex_to_rgb01("nope") == (0.5, 0.5, 0.5))

    # ── via detection drives the Metal flag ─────────────────────────────────
    tc.check("via names are recognised (Metal: 0 lets a net trace THROUGH)",
              g3.is_via_name("Via1.drawing") and g3.is_via_name("TopVia2")
              and g3.is_via_name("Cont.drawing"))
    tc.check("metal names are not mistaken for vias",
              not g3.is_via_name("Metal5.drawing")
              and not g3.is_via_name("Activ"))

    # The Metal flag means "a conductor to trace nets along", not merely
    # "not a via" — checked against GDS3D's own shipped example techfile,
    # where Substrate and N-Well are both Metal: 0.
    tc.check("is_metal_name: conductors are metal",
              g3.is_metal_name("Metal5.drawing") and g3.is_metal_name("M3")
              and g3.is_metal_name("TopMetal2"))
    tc.check("is_metal_name: wells, implants and substrate are NOT metal",
              not g3.is_metal_name("Substrate") and not g3.is_metal_name("N-Well")
              and not g3.is_metal_name("Activ") and not g3.is_metal_name("TEXT"))
    tc.check("is_metal_name: a via is not a conductor for this purpose",
              not g3.is_metal_name("Via1.drawing"))

    # ── entries from PDK data ───────────────────────────────────────────────
    sel = [{"layer_id": 8, "datatype": 0},
           {"layer_id": 19, "datatype": 0},
           {"layer_id": 67, "datatype": 0}]
    lyp = [{"layer_id": 8, "datatype": 0, "name": "Metal1.drawing",
            "fill-color": "#0000ff"},
           {"layer_id": 19, "datatype": 0, "name": "Via1.drawing",
            "fill-color": "#00ff00"},
           {"layer_id": 67, "datatype": 0, "name": "Metal5.drawing",
            "fill-color": "#ff0000"}]
    stack = {(8, 0):  {"t_mm": 0.00042, "z0_mm": 0.00062},
             (19, 0): {"t_mm": 0.00054, "z0_mm": 0.00104},
             (67, 0): {"t_mm": 0.00200, "z0_mm": 0.00300}}

    entries = g3.layer_entries(sel, lyp, stack)
    tc.check("layer_entries: one record per selected layer",
              len(entries) == 3, f"got {len(entries)}")
    tc.check("layer_entries: ordered bottom-up through the stack",
              [e["layer"] for e in entries] == [8, 19, 67],
              f"got {[e['layer'] for e in entries]}")

    m1 = next(e for e in entries if e["layer"] == 8)
    tc.check("layer_entries: millimetres are converted to nanometres "
              "(0.00042 mm = 420 nm)",
              abs(m1["thickness_nm"] - 420.0) < 1e-6, f"got {m1['thickness_nm']}")
    tc.check("layer_entries: height comes from the stackup's z0",
              abs(m1["height_nm"] - 620.0) < 1e-6, f"got {m1['height_nm']}")
    tc.check("layer_entries: a metal layer is flagged Metal: 1",
              m1["metal"] == 1)
    via = next(e for e in entries if e["layer"] == 19)
    tc.check("layer_entries: a via layer is flagged Metal: 0",
              via["metal"] == 0)
    tc.check("layer_entries: colour taken from the .lyp",
              m1["rgb"] == (0.0, 0.0, 1.0), f"got {m1['rgb']}")

    # A layer the stackup does not describe must still appear, stacked in
    # order — a viewer that opens with approximate heights beats one that
    # refuses to open at all.
    partial = g3.layer_entries(sel, lyp, {(8, 0): stack[(8, 0)]})
    tc.check("layer_entries: layers missing from the stackup are still "
              "written, at a nominal thickness",
              len(partial) == 3
              and all(e["thickness_nm"] > 0 for e in partial),
              f"got {[(e['layer'], e['thickness_nm']) for e in partial]}")

    tc.check("layer_entries: a layer with no .lyp entry still gets a name",
              g3.layer_entries([{"layer_id": 99, "datatype": 3}], [], {})[0]["name"]
              == "Layer99_3")

    # ── the rendered file ───────────────────────────────────────────────────
    text = g3.render_process_file(entries)
    tc.check("render: one block per layer",
              text.count("LayerStart:") == 3 and text.count("LayerEnd") == 3)
    tc.check("render: Metal1 block carries its layer number",
              _field(text, "Metal1", "Layer") == "8")
    tc.check("render: thickness written in whole nanometres",
              _field(text, "Metal1", "Thickness") == "420")
    tc.check("render: height written in whole nanometres",
              _field(text, "Metal1", "Height") == "620")
    tc.check("render: the via block is Metal: 0",
              _field(text, "Via1", "Metal") == "0")
    tc.check("render: the metal block is Metal: 1",
              _field(text, "Metal5", "Metal") == "1")
    tc.check("render: conductors are opaque, other layers see-through, so "
              "the metal stack is visible through the bulk",
              _field(text, "Metal5", "Filter") == "0.00", 
              f"got {_field(text, 'Metal5', 'Filter')}")
    tc.check("render: colour written as 0..1 floats",
              _field(text, "Metal5", "Red") == "1.00"
              and _field(text, "Metal5", "Blue") == "0.00")

    # Shortkey binds to the number keys, of which there are only ten.
    many = g3.layer_entries(
        [{"layer_id": i, "datatype": 0} for i in range(14)], [], {})
    tc.check("render: at most ten layers get a Shortkey (0-9 is all there is)",
              g3.render_process_file(many).count("Shortkey:") == 10,
              f"got {g3.render_process_file(many).count('Shortkey:')}")

    # Material only appears when supplied — it exists for the Gmsh export.
    tc.check("render: Material is omitted when the PDK does not supply one",
              "Material:" not in text)
    with_mat = g3.render_process_file(
        g3.layer_entries(sel, lyp, stack, materials={(8, 0): ("Al", "SiO2")}))
    tc.check("render: Material / OutMaterial are written when supplied "
              "(Gmsh export is their only consumer)",
              "Material: Al" in with_mat and "OutMaterial: SiO2" in with_mat)

    # ── the launch command ──────────────────────────────────────────────────
    argv = g3.build_command("/opt/GDS3D", "/tmp/p.txt", "/tmp/chip.gds")
    tc.check("build_command: matches the documented -p / -i interface",
              argv == ["/opt/GDS3D", "-p", "/tmp/p.txt", "-i", "/tmp/chip.gds"],
              f"got {argv}")
    argv_t = g3.build_command("G", "p", "g", topcell="TopCell")
    tc.check("build_command: -t selects a cell when asked",
              argv_t[-2:] == ["-t", "TopCell"], f"got {argv_t}")
    tc.check("build_command: returns a LIST, so paths with spaces survive "
              "without a shell",
              isinstance(argv, list))

    return tc.results
