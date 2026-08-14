# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for VIA-layer protection during GDS import.

Three independent mechanisms in the loader replace a layer with one bounding
box — the per-layer polygon threshold, the total polygon budget, and the
micro-area pre-scan. For a via array all three produce the same falsehood: an
array of separate pillars between two metals becomes a single solid slab
shorting them together, with no error anywhere.

The micro-area scan is the one that catches every chip. It was written to
kill sub-micron dummy fill and trips below 2 um^2 — but a via is sub-micron
by definition; the measured median for SG13G2's TopVia1 is 0.1764 um^2. So
via layers were being flattened on every import regardless of size.

What is tested here is the DECISION, not the geometry: building 67,938 via
solids takes a minute, while both real bugs found in this code were in
deciding which layers are vias at all.
"""

import os

from _harness import TestCase, REPO_ROOT

from core.lod_import import via_layer_keys, _name_says_via
from core.Core_Functionality import parse_lyp, parse_map, parse_stackup_xml

_IHP = os.path.join(REPO_ROOT, "resources", "stack_info", "IHP-PDK_SG13G2")
_SKY = os.path.join(REPO_ROOT, "resources", "stack_info", "SkyWater-PDK_SKY130")


def run():
    tc = TestCase("via_detail")
    _check_names(tc)
    _check_sg13g2(tc)
    _check_sky130(tc)
    _check_threading(tc)
    return tc.results


def _check_names(tc):
    # TopVia1 and TopVia2 are the two layers in the report that started this.
    # A prefix/suffix match missed both — they neither start nor end with
    # "via" — so they are pinned explicitly.
    for name in ("Via1", "Via4", "TopVia1", "TopVia2", "TopVia1.drawing",
                 "Cont", "licon1.drawing", "mcon.drawing", "Vmim", "via.drawing"):
        tc.check(f"_name_says_via: {name!r} is a via", _name_says_via(name))

    # "Activ" is the trap: a careless reversed or fuzzy match catches it, and
    # protecting it would keep a huge diffusion layer at full detail.
    for name in ("Activ", "Activ.drawing", "Metal1", "Metal5", "TopMetal1",
                 "TopMetal2.drawing", "EdgeSeal.boundary", "nwell.drawing",
                 "diff.drawing", "poly.drawing", "met1.drawing", "psdm.drawing",
                 "prBoundary.boundary", ""):
        tc.check(f"_name_says_via: {name!r} is NOT a via",
                  not _name_says_via(name))


def _check_sg13g2(tc):
    lyp = os.path.join(_IHP, "sg13g2.lyp")
    if not os.path.isfile(lyp):
        return
    layers, _ = parse_lyp(lyp)
    mapping = parse_map(os.path.join(_IHP, "sg13g2.map"))
    stack = parse_stackup_xml(os.path.join(_IHP, "SG13G2_200um.xml"))
    keys = via_layer_keys(layers, mapping, stack)

    for lid, name in ((125, "TopVia1"), (133, "TopVia2"), (19, "Via1"),
                      (29, "Via2"), (49, "Via3"), (66, "Via4"), (6, "Cont"),
                      (129, "Vmim")):
        tc.check(f"SG13G2: {name} ({lid}/0) is protected",
                  (lid, 0) in keys, f"got {sorted(keys)[:12]}")

    # sg13g2.map lists VIA among the types for every routing metal
    # ("Metal1 NET,SPNET,PIN,LEFPIN,VIA"), because a metal can carry via
    # shapes in the EDI stream. Believing that marked all seven metals as
    # vias, which would keep the heaviest layers on the chip at full detail.
    for lid, name in ((8, "Metal1"), (10, "Metal2"), (30, "Metal3"),
                      (50, "Metal4"), (67, "Metal5"), (126, "TopMetal1"),
                      (134, "TopMetal2"), (1, "Activ")):
        tc.check(f"SG13G2: {name} ({lid}/0) is NOT treated as a via, despite "
                  f"the stream map listing VIA among its types",
                  (lid, 0) not in keys)


def _check_sky130(tc):
    lyp = os.path.join(_SKY, "sky130.lyp")
    if not os.path.isfile(lyp):
        return
    layers, _ = parse_lyp(lyp)
    mapping = parse_map(os.path.join(_SKY, "sky130.map"))
    stack = parse_stackup_xml(os.path.join(_SKY, "SKY130A_300um.xml"))
    keys = via_layer_keys(layers, mapping, stack)

    # SKY130 puts the metal and the via on the SAME layer number. A lookup by
    # number alone reports the metal, and every sky130 via goes unprotected.
    for number, name in ((68, "via"), (69, "via2"), (70, "via3"),
                         (71, "via4"), (67, "mcon"), (66, "licon1")):
        tc.check(f"SKY130: {name} ({number}/44) is protected",
                  (number, 44) in keys, f"got {sorted(keys)}")
        tc.check(f"SKY130: the metal on the SAME layer number ({number}/20) "
                  f"is not swept up with it",
                  (number, 20) not in keys)


def _check_threading(tc):
    """The option must actually reach the loader."""
    import inspect
    from core import Core_Functionality, lod_import

    sig = inspect.signature(Core_Functionality.load_gds)
    tc.check("load_gds accepts protect_via_keys",
              "protect_via_keys" in sig.parameters)

    src = inspect.getsource(lod_import.build_lod_import_params)
    tc.check("build_lod_import_params passes protect_via_keys to the loader",
              "protect_via_keys" in src)
    tc.check("build_lod_import_params reads the keep_via_detail option",
              "keep_via_detail" in src)

    # An explicit per-layer bbox request must still win over the blanket
    # protection, or the user loses the ability to simplify a via layer.
    tc.check("an explicit force-bbox request outranks via protection",
              "- user_bbox_keys" in src)

    body = inspect.getsource(Core_Functionality.load_gds)
    for mechanism in ("Auto-bbox skipped", "Micro-area bbox skipped"):
        tc.check(f"the loader reports '{mechanism}' for protected vias, so a "
                  f"kept layer is visible in the Report view",
                  mechanism in body)
    tc.check("all four collapse mechanisms consult _protect_via",
              body.count("_protect_via") >= 4,
              f"found {body.count('_protect_via')} references")
    _check_cache_key(tc)
    _check_real_geometry(tc)


def _check_real_geometry(tc):
    """
    End-to-end on a real layout, in the mode the GUI actually imports with.

    contacts_only_3d is the mechanism that survived every earlier fix. It does
    not collapse a via layer to its own bounding box — it merges the layer
    into the single combined BODY solid, so the via array comes out as one
    slab spanning the die and visibly bridging pads that share nothing. It
    fires on layers the user explicitly ticked for immediate load, because LOD
    mode categorises vias as "routing" and only contact layers escape.

    GSGPad_simple is used because it is 0.1 MB: small enough to build in the
    suite, and it still carries 884 real vias on TopVia1.
    """
    gds = os.path.join(REPO_ROOT, "samples", "IC_Pad_EdgeSeal.boundary.gds")
    lyp = os.path.join(_IHP, "sg13g2.lyp")
    if not (os.path.isfile(gds) and os.path.isfile(lyp)):
        return

    from core import Core_Functionality as CF
    from core.lod_import import categorize_layers
    import gdstk

    all_layers, _ = parse_lyp(lyp)
    mapping = parse_map(os.path.join(_IHP, "sg13g2.map"))
    stack = parse_stackup_xml(os.path.join(_IHP, "SG13G2_200um.xml"))
    vias = via_layer_keys(all_layers, mapping, stack)
    cats = categorize_layers(all_layers, mapping)

    lib = gdstk.read_gds(gds)
    used = set()
    for cell in lib.top_level():
        for poly in cell.get_polygons(depth=None):
            used.add((poly.layer, poly.datatype))
    present = [L for L in all_layers
               if (L["layer_id"], L["datatype"]) in used]
    keys = {(L["layer_id"], L["datatype"]) for L in present}

    def solids_by_key(protect):
        shapes = CF.load_gds(
            gds, present, transform=None, preview_2d=False,
            compound_per_layer=True, min_area_mm2=0.0, decimate_tol_mm=0.0,
            skip_fill_datatype=False, fill_as_bbox=True, fill_layer_keys=set(),
            flat_layer_keys=set(), force_bbox_keys=set(),
            exclude_auto_bbox_keys=keys, protect_via_keys=protect,
            ihp_map=mapping,
            stack_mm=CF.build_stack_mm_from_xml(all_layers, mapping, stack),
            contacts_only_3d=True,
            contact_keys={k for k, c in cats.items() if c == "contact"},
            mesh_3d=False, use_cache=False, auto_bbox_threshold=5_000)
        out = {}
        for e in shapes:
            shp = e.get("shape")
            out[(e.get("layer_id"), e.get("datatype"))] = (
                len(shp.Solids) if shp is not None else 0)
        return out

    unprotected = solids_by_key(set())
    protected = solids_by_key(vias)

    for key, expected in (((125, 0), 884), ((133, 0), 154)):
        tc.check(f"LOD import: via layer {key[0]}/{key[1]} builds as a real "
                  f"array, not one merged body slab",
                  protected.get(key, 0) == expected,
                  f"got {protected.get(key)} solids, expected {expected}")
        tc.check(f"LOD import: {key[0]}/{key[1]} WAS being swallowed by the "
                  f"body box without protection — pins the regression",
                  unprotected.get(key, 0) in (0, 1),
                  f"got {unprotected.get(key)} solids unprotected")


def _check_cache_key(tc):
    """
    The disk cache key must cover every argument that changes the geometry.

    This is how via protection failed in practice even once the loader
    honoured it: the key did not include protect_via_keys, so turning the
    option on produced a byte-identical key and the cache returned the
    flattened via layers built before the option existed. The import printed
    "VIA detail protected" and "cache hit" on consecutive lines and then
    handed back the old geometry.

    An omitted argument does not merely degrade the cache — it silently
    serves geometry built under different rules, which is indistinguishable
    from the setting being ignored.
    """
    import inspect
    from core import Core_Functionality as CF

    src = inspect.getsource(CF.load_gds)
    block = src[src.index("_cache_options = {"):src.index("_ck = _cache_key")]

    # Arguments that genuinely cannot change the produced geometry, or that
    # are folded into the key under a different name.
    accounted = {
        "gds_path", "selected_layers", "use_cache", "progress_callback",
        "parallel_workers",                      # threading only
        "auto_bbox_threshold", "poly_budget",    # keyed as bbox_thresh/budget
        "fill_as_bbox", "min_area_mm2", "decimate_tol_mm", "preview_2d",
        "mesh_3d", "force_bbox_keys", "exclude_auto_bbox_keys",
        "ihp_map", "stack_mm",                   # keyed as fill_map/stack
    }
    missing = [n for n in inspect.signature(CF.load_gds).parameters
               if n not in accounted and n not in block]
    tc.check("every geometry-affecting argument of load_gds appears in the "
              "cache key — an omitted one serves geometry built under "
              "different rules",
              not missing, f"missing: {missing}")

    layers = [{"layer_id": 125, "datatype": 0, "name": "TopVia1.drawing"}]
    base = dict(preview_2d=False, min_area=0.0, decimate=0.0, fill_bbox=True,
                mesh_3d=False, bbox_thresh=5000, budget=25000,
                force_bbox=(), excl_bbox=(), protect_via=(), fill_keys=(),
                flat_keys=(), contacts_only=False, contact_keys=(),
                max_polys=None, skip_fill_dt=False, gdstk_union=False,
                compound=True, stack=(), fill_map=(), transform=None)
    gds = os.path.join(REPO_ROOT, "samples", "ALL_LNA.gds")
    k0 = CF._cache_key(gds, layers, base)

    for label, changed in (
        ("protect_via_keys", dict(base, protect_via=((125, 0),))),
        ("stack_mm (switching PDK profile changes every Z)",
         dict(base, stack=(((125, 0), 0.0009, 0.0056),))),
        ("contacts_only_3d", dict(base, contacts_only=True)),
        ("max_polys_per_layer", dict(base, max_polys=1000)),
        ("flat_layer_keys", dict(base, flat_keys=((125, 0),))),
    ):
        tc.check(f"cache key changes when {label} changes",
                  CF._cache_key(gds, layers, changed) != k0)
