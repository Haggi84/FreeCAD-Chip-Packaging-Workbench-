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
    tc.check("total thickness is a plausible die thickness (0.2 - 1.0 mm)",
              0.2 < thickness_mm < 1.0, f"got {thickness_mm}")

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
