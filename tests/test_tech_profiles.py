# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for the bundled PDK profiles — IHP SG13G2 and SkyWater SKY130.

A PDK profile is three files that have to agree with each other and with the
parsers that read them, and every way they can disagree fails quietly:

* A .lyp whose <source> the parser cannot read yields zero visible layers,
  which looks like "the import found no geometry" rather than a parse error.
  KLayout writes '68/20@1' and the official sky130A.lyp does so on every
  entry, so this is not hypothetical.
* SKY130 draws met1 on 68/20 and the via above it on 68/44. A stackup keyed
  by layer number alone silently gives one of them the other's Z position —
  geometry at the wrong height, no error anywhere.
* A profile pointing at a file that is not shipped configures cleanly and
  fails only at import time.

Adding SKY130 must also not disturb SG13G2, so the regression guards below
pin SG13G2's own numbers rather than only checking the new PDK.
"""

import os

from _harness import TestCase, REPO_ROOT

import core.chip_proxy as chip_proxy
from core.TechConfig import TechConfigManager, BUILTIN_PROFILES
from core.Core_Functionality import (
    parse_lyp, parse_map, parse_stackup_xml, _parse_lyp_source,
)
from core.tech.stackup import build_stack_mm_from_xml

_SKY = os.path.join(REPO_ROOT, "resources", "stack_info", "SkyWater-PDK_SKY130")
_IHP = os.path.join(REPO_ROOT, "resources", "stack_info", "IHP-PDK_SG13G2")


def run():
    tc = TestCase("tech_profiles")
    _check_lyp_source_syntax(tc)
    _check_bundled_files(tc)
    _check_sky130_stackup(tc)
    _check_sg13g2_unchanged(tc)
    _check_outline_layers(tc)
    return tc.results


def _check_lyp_source_syntax(tc):
    tc.check("_parse_lyp_source: plain 'L/D'",
              _parse_lyp_source("68/20") == (68, 20))
    tc.check("_parse_lyp_source: KLayout's 'L/D@n' — the form the official "
              "sky130A.lyp uses on every entry",
              _parse_lyp_source("68/20@1") == (68, 20))
    tc.check("_parse_lyp_source: 'L/D@*' wildcard",
              _parse_lyp_source("235/4@*") == (235, 4))
    tc.check("_parse_lyp_source: surrounding whitespace",
              _parse_lyp_source("  72/20@1 ") == (72, 20))
    for bad in ("", "68", "abc/20"):
        try:
            _parse_lyp_source(bad)
            raised = False
        except ValueError:
            raised = True
        tc.check(f"_parse_lyp_source: rejects {bad!r}", raised)


def _check_bundled_files(tc):
    mgr = TechConfigManager
    for name in BUILTIN_PROFILES:
        profile = mgr.builtin_profile(name)
        for key in ("lyp_path", "map_path", "xml_path"):
            path = profile[key]
            if not tc.check(f"{name}: {key} is shipped",
                             os.path.isfile(path), path):
                continue

        layers, _colours = parse_lyp(profile["lyp_path"])
        tc.check(f"{name}: the .lyp yields visible layers — zero would look "
                  f"like an empty layout rather than a parse failure",
                  len(layers) > 0, f"{len(layers)} layers")
        tc.check(f"{name}: every parsed layer carries a layer_id/datatype",
                  all("layer_id" in L and "datatype" in L for L in layers))

        mapping = parse_map(profile["map_path"])
        tc.check(f"{name}: the .map yields mappings",
                  len(mapping) > 0, f"{len(mapping)} mappings")

        stack = parse_stackup_xml(profile["xml_path"])
        tc.check(f"{name}: the stackup parses", len(stack) > 0)
        tc.check(f"{name}: the stackup states a substrate offset, so die "
                  f"thickness is not a flat guess",
                  "_substrate_offset_um" in stack)

        thick, z0, source = chip_proxy.get_die_thickness_mm(stack)
        # NOT "greater than the default substrate": SG13G2's die is 0.198 mm,
        # i.e. thinner than the 200 um fallback constant. What matters is
        # that the number came from the stackup rather than from the flat
        # last-resort guess.
        tc.check(f"{name}: thickness resolves from the PDK, not the default",
                  source == "xml_with_substrate"
                  and abs(thick - chip_proxy.DEFAULT_DIE_THICKNESS_MM) > 1e-9
                  and thick > 0.0,
                  f"{thick:.4f} mm via {source}")
        tc.check(f"{name}: the substrate underside sits below zero",
                  z0 < 0, f"z0={z0}")


def _check_sky130_stackup(tc):
    """SKY130 reuses one layer number for a metal and the via above it."""
    profile = TechConfigManager.builtin_profile("SkyWater SKY130")
    if not os.path.isfile(profile["xml_path"]):
        return
    stack = parse_stackup_xml(profile["xml_path"])

    pairs = ((68, "met1"), (69, "met2"), (70, "met3"), (71, "met4"))
    for number, metal in pairs:
        met = stack.get((number, 20))
        via = stack.get((number, 44))
        tc.check(f"SKY130: {metal} ({number}/20) and the via ({number}/44) "
                  f"are separate stackup entries",
                  met is not None and via is not None
                  and met["zmin_um"] != via["zmin_um"],
                  f"met={met}, via={via}")
        if met and via:
            tc.check(f"SKY130: the {number}/44 via starts where {metal} ends",
                      abs(via["zmin_um"] - met["zmax_um"]) < 1e-9,
                      f"{met['zmax_um']} vs {via['zmin_um']}")

    tc.check("SKY130: a bare layer number resolves to the CONDUCTOR, the "
              "better guess of the two when a caller knows no datatype",
              stack.get(68, {}).get("type") == "conductor",
              str(stack.get(68)))

    # The stack must come out monotonic: met1 below met2 below ... met5.
    order = [stack.get((n, 20), {}).get("zmin_um")
             for n in (68, 69, 70, 71, 72)]
    tc.check("SKY130: met1..met5 are stacked in ascending Z",
              all(a is not None and b is not None and a < b
                  for a, b in zip(order, order[1:])), str(order))

    layers, _ = parse_lyp(profile["lyp_path"])
    mapping = parse_map(profile["map_path"])
    built = build_stack_mm_from_xml(layers, mapping, stack)
    tc.check("build_stack_mm_from_xml: met1 and its via get different Z — "
              "the whole reason the stackup carries datatypes",
              built.get((68, 20)) is not None
              and built.get((68, 44)) is not None
              and built[(68, 20)]["z0_mm"] != built[(68, 44)]["z0_mm"],
              f"{built.get((68, 20))} vs {built.get((68, 44))}")


def _check_sg13g2_unchanged(tc):
    """Adding SKY130 must not move SG13G2's own numbers."""
    profile = TechConfigManager.builtin_profile("IHP-PDK SG13G2")
    if not os.path.isfile(profile["xml_path"]):
        return
    stack = parse_stackup_xml(profile["xml_path"])

    metal1 = stack.get(8)
    tc.check("SG13G2: Metal1 still resolves by bare layer number 8, since "
              "that stackup carries no datatypes",
              metal1 is not None
              and abs(metal1["zmin_um"] - 1.04) < 1e-9
              and abs(metal1["zmax_um"] - 1.46) < 1e-9,
              str(metal1))
    tc.check("SG13G2: no (layer, datatype) keys are invented for a stackup "
              "that does not declare them",
              stack.get((8, 0)) is None and stack.get((8, 20)) is None)
    tc.check("SG13G2: substrate offset unchanged at 183.75 um",
              abs(stack.get("_substrate_offset_um", 0) - 183.75) < 1e-9,
              str(stack.get("_substrate_offset_um")))


def _check_outline_layers(tc):
    """SKY130 layouts have no seal ring; SG13G2 layouts must keep theirs."""
    known = {(l, d) for l, d, _n in chip_proxy.DIE_OUTLINE_LAYERS}
    tc.check("DIE_OUTLINE_LAYERS: SKY130's prBoundary 235/4 is recognised",
              (235, 4) in known)
    tc.check("DIE_OUTLINE_LAYERS: SG13G2's EdgeSeal 39/4 comes first, so a "
              "layout carrying both keeps using its seal ring",
              chip_proxy.DIE_OUTLINE_LAYERS[0][:2] == (39, 4))
    order = [(l, d) for l, d, _n in chip_proxy.DIE_OUTLINE_LAYERS]
    tc.check("DIE_OUTLINE_LAYERS: 235/4 is tried last — it is Cadence's "
              "default prBoundary and turns up in non-SKY130 exports too",
              order[-1] == (235, 4), str(order))

    sky = os.path.join(REPO_ROOT, "samples", "PassionateSocRing.gds")
    if os.path.isfile(sky):
        d = chip_proxy.describe_die_footprint(sky)
        w = d["footprint_mm"][2] - d["footprint_mm"][0]
        h = d["footprint_mm"][3] - d["footprint_mm"][1]
        tc.check("a real SKY130 layout measures 2.15 x 2.15 mm",
                  abs(w - 2.15) < 1e-4 and abs(h - 2.15) < 1e-4,
                  f"{w:.4f} x {h:.4f}")
        tc.check("a real SKY130 layout takes its outline from prBoundary, "
                  "not from the raw bounding box",
                  "235/4" in d["source"], d["source"])
