# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Smoke test for core.chip_proxy — the fast, tessellation-free chip-layout
extraction added as "Phase 1" of multi-chip layout support (footprint via
gdstk's native Cell.bounding_box(), thickness via the stackup XML incl.
substrate offset, bond-pad positions via named pad-cell detection first,
falling back to the DT=2/DT=0 PIN-detection heuristic
Core_Functionality.import_pin_pads_as_contacts() uses).

Uses the real ALL_LNA.gds sample (4375 cells / ~1M polygons — the exact
"incredibly slow" full-chip case this feature exists to sidestep) plus the
bundled IHP SG13G2 lyp/map/stackup-XML, so this also serves as a wall-clock
sanity check that extraction stays fast on the worst-case sample this repo
ships.

Also uses resources/gds/6_final.gds — a real, synthesized, padframe-based
full-chip GDS (an I2C GPIO expander) — as the regression guard for a real
bug: the DT=2/DT=0 heuristic alone found 1109 "pads" scattered across the
whole die on this file (every internal net endpoint is DT=2-marked here,
not just real bond pads), when the actual pad ring — found correctly via
named "bondpad_70x70" cell instances — has exactly 20.
"""

import os
import time

import FreeCAD

from _harness import TestCase, REPO_ROOT, new_document

import core.chip_proxy as chip_proxy
from core.Core_Functionality import parse_stackup_xml, parse_map

_GDS = os.path.join(REPO_ROOT, "samples", "ALL_LNA.gds")
_STACK_DIR = os.path.join(REPO_ROOT, "resources", "stack_info", "IHP-PDK_SG13G2")
_LYP = os.path.join(_STACK_DIR, "sg13g2.lyp")
_MAP = os.path.join(_STACK_DIR, "sg13g2.map")
_XML = os.path.join(_STACK_DIR, "SG13G2_200um.xml")
_GDS_PADFRAME = os.path.join(REPO_ROOT, "resources", "gds", "6_final.gds")


def run():
    tc = TestCase("chip_proxy")

    for label, path in (("GDS", _GDS), ("LYP", _LYP), ("MAP", _MAP), ("XML", _XML)):
        if not tc.check(f"{label} sample file exists", os.path.exists(path), path):
            return tc.results

    # ── footprint: fast, tessellation-free, via gdstk's native bbox ────────
    box = {}

    def _footprint():
        t0 = time.time()
        box["bbox"] = chip_proxy.get_die_footprint_mm(_GDS)
        box["elapsed"] = time.time() - t0

    if tc.check_raises_nothing("get_die_footprint_mm: no exception", _footprint):
        xmin, ymin, xmax, ymax = box["bbox"]
        tc.check("footprint matches known ALL_LNA extent (0.78 x 0.98 mm)",
                  abs((xmax - xmin) - 0.78) < 1e-6 and abs((ymax - ymin) - 0.98) < 1e-6,
                  f"got {box['bbox']}")
        tc.check("footprint extraction is fast (<5 s) even on the full-chip sample",
                  box["elapsed"] < 5.0, f"took {box['elapsed']:.3f} s")

    _check_outline_provenance(tc)
    _check_dimension_overrides(tc)

    # ── thickness: stackup XML including <Substrate Offset> ────────────────
    stackup_data = parse_stackup_xml(_XML)
    tc.check("parse_stackup_xml exposes _substrate_offset_um",
              stackup_data.get("_substrate_offset_um") == 183.75,
              f"got {stackup_data.get('_substrate_offset_um')}")

    thickness_mm, z0_mm, source = chip_proxy.get_die_thickness_mm(stackup_data)
    tc.check("thickness source is xml_with_substrate", source == "xml_with_substrate")
    tc.check("z0_mm sits at the (negative) substrate underside",
              abs(z0_mm - (-0.18375)) < 1e-6, f"got {z0_mm}")
    tc.check("total thickness > substrate alone (interconnect stack included)",
              thickness_mm > 0.18375, f"got {thickness_mm}")
    tc.check("total thickness is a plausible die thickness (0.15 - 1.0 mm)",
              0.15 < thickness_mm < 1.0, f"got {thickness_mm}")
    # Regression guard: BACKSIDEGND/LBE (Zmin down to -190) must NOT be folded
    # into interconnect_um alongside _substrate_offset_um, or the substrate
    # gets counted twice (~0.388mm instead of the correct ~0.198mm on this XML).
    tc.check("substrate is not double-counted (expect ~0.198mm, not ~0.388mm)",
              abs(thickness_mm - 0.1979803) < 1e-4, f"got {thickness_mm}")

    # no stackup at all -> flat default, clearly labelled
    d_mm, d_z0, d_src = chip_proxy.get_die_thickness_mm({})
    tc.check("no-stackup fallback uses the documented default thickness",
              d_mm == chip_proxy.DEFAULT_DIE_THICKNESS_MM)
    tc.check("no-stackup fallback is clearly labelled", d_src == "no_stackup_default")

    # ── pads: raw DT=2/DT=0 heuristic (still directly tested, still used
    # as the last-resort fallback inside get_bond_pad_positions_mm) ────────
    ihp_map = parse_map(_MAP)

    def _pads():
        box["pads"] = chip_proxy.get_pad_positions_mm(_GDS, ihp_map)

    if tc.check_raises_nothing("get_pad_positions_mm: no exception", _pads):
        pads = box["pads"]
        tc.check("pads found on ALL_LNA via IHP map strategy", len(pads) > 0,
                  f"got {len(pads)}")
        xmin, ymin, xmax, ymax = box.get("bbox", (0, 0, 0.78, 0.98))
        margin = 0.05   # mm slack for pads right at the die edge
        out_of_bounds = [
            p for p in pads
            if not (xmin - margin <= p["x_mm"] <= xmax + margin
                    and ymin - margin <= p["y_mm"] <= ymax + margin)
        ]
        tc.check("all pad positions fall within (or very near) the die footprint",
                  not out_of_bounds, f"{len(out_of_bounds)} outliers, e.g. {out_of_bounds[:3]}")

    # ── pads: get_bond_pad_positions_mm prefers named pad-cell instances ───
    # ALL_LNA.gds turns out to have real "bondpad_*" cell instances too (21
    # of them) — fewer and more accurate than the raw DT=2 heuristic's 168,
    # confirming the named-cell strategy is a strict accuracy improvement
    # here as well, not just on the padframe regression case below.
    def _bond_pads():
        box["bond_pads"] = chip_proxy.get_bond_pad_positions_mm(_GDS, ihp_map)

    if tc.check_raises_nothing("get_bond_pad_positions_mm: no exception", _bond_pads):
        bond_pads = box["bond_pads"]
        tc.check("get_bond_pad_positions_mm finds named bondpad_* instances on ALL_LNA",
                  len(bond_pads) == 21, f"got {len(bond_pads)}")
        tc.check("named-cell strategy finds fewer, more accurate pads than the raw heuristic",
                  len(bond_pads) < len(box.get("pads", [])),
                  f"named={len(bond_pads)} raw={len(box.get('pads', []))}")

    # ── regression guard: a real synthesized full-chip GDS with a padframe ──
    # (the bug this whole named-cell strategy was added to fix — see module
    # docstring). Skipped gracefully if the file isn't present rather than
    # failing the whole suite, since it's a large, specific fixture.
    if os.path.exists(_GDS_PADFRAME):
        def _padframe_pads():
            box["padframe_pads"] = chip_proxy.get_bond_pad_positions_mm(_GDS_PADFRAME, ihp_map)

        if tc.check_raises_nothing("get_bond_pad_positions_mm on 6_final.gds: no exception",
                                     _padframe_pads):
            pf_pads = box["padframe_pads"]
            tc.check("finds exactly the real 20-pad ring, not 1000+ false positives",
                      len(pf_pads) == 20, f"got {len(pf_pads)}")
            tc.check("all padframe pads came from the named bondpad_* strategy",
                      all(p["name"] == "bondpad_70x70" for p in pf_pads),
                      f"names: {set(p['name'] for p in pf_pads)}")
    else:
        tc.skip("6_final.gds finds exactly the real 20-pad ring",
                f"sample not present: {_GDS_PADFRAME}")

    # ── one-shot extraction ──────────────────────────────────────────────────
    def _extract():
        box["proxy"] = chip_proxy.extract_chip_proxy(_GDS, _LYP, _MAP, _XML)

    if not tc.check_raises_nothing("extract_chip_proxy: no exception", _extract):
        return tc.results

    proxy = box["proxy"]
    tc.check("extract_chip_proxy: footprint present", "footprint_mm" in proxy)
    tc.check("extract_chip_proxy: thickness matches get_die_thickness_mm",
              abs(proxy["thickness_mm"] - thickness_mm) < 1e-9)
    tc.check("extract_chip_proxy: pads present", len(proxy.get("pads", [])) > 0)
    tc.check("extract_chip_proxy uses get_bond_pad_positions_mm (named-cell strategy)",
              len(proxy.get("pads", [])) == len(box.get("bond_pads", [])),
              f"extract_chip_proxy={len(proxy.get('pads', []))} "
              f"get_bond_pad_positions_mm={len(box.get('bond_pads', []))}")

    # ── FreeCAD object construction ──────────────────────────────────────────
    doc = new_document("TestChipProxy")

    def _build():
        box["block"] = chip_proxy.build_chip_proxy_object(doc, proxy, name="ALL_LNA")

    if tc.check_raises_nothing("build_chip_proxy_object: no exception", _build):
        block = box["block"]
        tc.check("proxy block created", block is not None and doc.getObject(block.Name) is block)
        tc.check("proxy block tagged IsChipProxy", getattr(block, "IsChipProxy", False) is True)
        shp = getattr(block, "Shape", None)
        tc.check("proxy block has a valid shape", shp is not None and shp.isValid() and not shp.isNull())
        if shp is not None:
            bb = shp.BoundBox
            xmin, ymin, xmax, ymax = proxy["footprint_mm"]
            tc.check("proxy block footprint matches extracted data",
                      abs(bb.XLength - (xmax - xmin)) < 1e-6 and abs(bb.YLength - (ymax - ymin)) < 1e-6,
                      f"got {bb.XLength:.4f} x {bb.YLength:.4f}")
            tc.check("proxy block thickness matches extracted data",
                      abs(bb.ZLength - proxy["thickness_mm"]) < 1e-6,
                      f"got {bb.ZLength:.4f}")

        pad_markers = [o for o in doc.Objects if getattr(o, "IsContactPoint", False)]
        tc.check("one ContactPoint marker created per detected pad",
                  len(pad_markers) == len(proxy["pads"]),
                  f"got {len(pad_markers)}, expected {len(proxy['pads'])}")
        if pad_markers:
            m = pad_markers[0]
            tc.check("pad marker SourceObject points at the proxy block",
                      m.SourceObject == block.Name)
            m_shp = getattr(m, "Shape", None)
            tc.check("pad marker is a real box (not an abstract point), matching its pad size",
                      m_shp is not None and m_shp.Faces and m_shp.Volume > 0,
                      f"shape={m_shp}")
            if m_shp is not None and m_shp.Faces:
                tc.check("pad marker footprint matches its detected pad size",
                          abs(m_shp.BoundBox.XLength - proxy["pads"][0].get(
                              "width_mm", chip_proxy._DEFAULT_PAD_SIZE_MM)) < 1e-6,
                          f"got {m_shp.BoundBox.XLength:.4f}")
            tc.check("pad marker ContactPoint sits on top of the marker itself "
                      "(which sits on top of the proxy block)",
                      m.ContactPoint.z > proxy["z0_mm"] + proxy["thickness_mm"] - 1e-9,
                      f"got {m.ContactPoint.z}, die top at "
                      f"{proxy['z0_mm'] + proxy['thickness_mm']}")

        # Every detected pad should carry a real footprint size now, not
        # just a bare position — this is what lets the marker be an
        # accurately-sized rectangle instead of an abstract dot.
        tc.check("detected pads carry width_mm/height_mm",
                  all("width_mm" in p and "height_mm" in p for p in proxy["pads"]),
                  "at least one pad dict is missing width_mm/height_mm")

    # ── degenerate footprint is rejected, not silently built ────────────────
    bad_proxy = dict(proxy)
    bad_proxy["footprint_mm"] = (0.0, 0.0, 0.0, 0.5)   # zero width

    def _build_degenerate():
        chip_proxy.build_chip_proxy_object(doc, bad_proxy, name="Degenerate")

    ok = False
    try:
        _build_degenerate()
    except ValueError:
        ok = True
    except Exception:
        ok = False
    tc.check("build_chip_proxy_object rejects a degenerate (zero-width) footprint", ok)

    # ── importing the same chip name twice must not collide ─────────────────
    # (a real scenario once multiple, possibly identical, dice are placed
    # in one package) — the second import must get distinctly-named
    # objects and a distinguishable label, not silently clash with the
    # first one's "{name}_Proxy"/"{name}_Block" names.
    box2 = {}

    def _build_twice():
        box2["first"]  = chip_proxy.build_chip_proxy_object(doc, proxy, name="DupChip")
        box2["second"] = chip_proxy.build_chip_proxy_object(doc, proxy, name="DupChip")

    if tc.check_raises_nothing("building two proxies with the same name: no exception", _build_twice):
        first, second = box2["first"], box2["second"]
        tc.check("second import gets a distinct internal Name",
                  first.Name != second.Name, f"both were {first.Name!r}")
        tc.check("second import gets a distinguishable Label",
                  first.Label != second.Label,
                  f"both were {first.Label!r}")
        tc.check("both proxy blocks coexist validly in the document",
                  doc.getObject(first.Name) is first and doc.getObject(second.Name) is second)

    FreeCAD.closeDocument(doc.Name)
    return tc.results


def _check_outline_provenance(tc):
    """
    The die outline must come from the layout's own outline layer when it
    draws one, and must not include tooling artefacts.

    Both matter for the same reason: a proxy discards the layout and keeps
    only the box, so the box is the entirety of what it claims. A stray
    marker overhanging the seal ring, or a Cadence '$$$CONTEXT_INFO$$$' cell
    folded into the union, silently makes that claim wrong.
    """
    d = chip_proxy.describe_die_footprint(_GDS)
    x0, y0, x1, y1 = d["footprint_mm"]
    tc.check("describe_die_footprint: ALL_LNA measures 0.78 x 0.98 mm",
              abs((x1 - x0) - 0.78) < 1e-6 and abs((y1 - y0) - 0.98) < 1e-6,
              f"got {(x1 - x0):.4f} x {(y1 - y0):.4f}")
    tc.check("describe_die_footprint: reports where the outline came from",
              "EdgeSeal" in d["source"], d["source"])
    tc.check("describe_die_footprint: names the die cell",
              d["cell"] == "ALL_LNA", d["cell"])

    # 6_final carries BOTH outline layers plus a Cadence context cell, so it
    # exercises the choice as well as the filtering.
    if os.path.exists(_GDS_PADFRAME):
        d2 = chip_proxy.describe_die_footprint(_GDS_PADFRAME)
        w = d2["footprint_mm"][2] - d2["footprint_mm"][0]
        h = d2["footprint_mm"][3] - d2["footprint_mm"][1]
        tc.check("6_final: the seal ring is preferred over prBoundary, which "
                  "overhangs it by a fraction of a micron",
                  abs(w - 1.05) < 1e-6 and abs(h - 1.05) < 1e-6,
                  f"got {w:.4f} x {h:.4f}")
        names = {c["name"] for c in d2["candidates"]}
        tc.check("6_final: both outline layers are reported as candidates, "
                  "so the choice is visible and not silent",
                  {"EdgeSeal.boundary", "prBoundary.boundary"} <= names,
                  str(sorted(names)))
        tc.check("6_final: the Cadence context cell is not chosen as the die",
                  not chip_proxy.is_artifact_cell(d2["cell"]), d2["cell"])

    tc.check("is_artifact_cell: recognises the Cadence context cell",
              chip_proxy.is_artifact_cell("$$$CONTEXT_INFO$$$"))
    tc.check("is_artifact_cell: leaves ordinary cell names alone",
              not chip_proxy.is_artifact_cell("ALL_LNA")
              and not chip_proxy.is_artifact_cell("I2cGpioExpanderTop"))


def _check_dimension_overrides(tc):
    """Typed-in dimensions must win, and must not move the pads."""
    base = {
        "footprint_mm": (1.0, 2.0, 3.0, 5.0),      # 2.0 x 3.0 mm at (1, 2)
        "footprint_source": "bounding box of all geometry",
        "thickness_mm": 0.3,
        "thickness_source": "no_stackup_default",
    }

    untouched = chip_proxy.apply_dimension_overrides(dict(base))
    tc.check("apply_dimension_overrides: all-None changes nothing",
              untouched["footprint_mm"] == base["footprint_mm"]
              and untouched["thickness_mm"] == base["thickness_mm"])
    tc.check("apply_dimension_overrides: provenance is untouched when nothing "
              "was entered — it must not claim a measurement was typed in",
              untouched["footprint_source"] == base["footprint_source"]
              and untouched["thickness_source"] == base["thickness_source"])

    d = chip_proxy.apply_dimension_overrides(dict(base), width_mm=4.0)
    tc.check("apply_dimension_overrides: width is applied",
              abs((d["footprint_mm"][2] - d["footprint_mm"][0]) - 4.0) < 1e-9,
              str(d["footprint_mm"]))
    tc.check("apply_dimension_overrides: the origin stays put, because the "
              "pads were measured in these coordinates and re-centring would "
              "slide every one of them off the die",
              d["footprint_mm"][0] == 1.0 and d["footprint_mm"][1] == 2.0,
              str(d["footprint_mm"]))
    tc.check("apply_dimension_overrides: length is left as measured when "
              "only width was given",
              abs((d["footprint_mm"][3] - d["footprint_mm"][1]) - 3.0) < 1e-9)
    tc.check("apply_dimension_overrides: says the outline was entered by hand",
              "hand" in d["footprint_source"], d["footprint_source"])

    d = chip_proxy.apply_dimension_overrides(dict(base), thickness_mm=0.2142)
    tc.check("apply_dimension_overrides: thickness is applied",
              abs(d["thickness_mm"] - 0.2142) < 1e-9)
    tc.check("apply_dimension_overrides: thickness provenance records the "
              "override, so a hand value is never mistaken for a PDK one",
              d["thickness_source"] == "entered_by_hand")
    tc.check("apply_dimension_overrides: a thickness override leaves the "
              "footprint alone",
              d["footprint_mm"] == base["footprint_mm"])

    for bad in (0.0, -1.0):
        d = chip_proxy.apply_dimension_overrides(
            dict(base), width_mm=bad, thickness_mm=bad)
        tc.check(f"apply_dimension_overrides: ignores a non-positive {bad} "
                  f"rather than building a degenerate block",
                  d["footprint_mm"] == base["footprint_mm"]
                  and d["thickness_mm"] == base["thickness_mm"])
