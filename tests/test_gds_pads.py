# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.gds_pads — picking the top contact areas by hand.

The case this exists for is reproduced here rather than described: a
SKY130-style layout, drawn on datatype 20 with its pad opening typed LEFOBS,
where every automatic rule in the workbench finds no pads at all. The first
check below asserts that failure, because if a later change makes automatic
detection cover SKY130, this test should say so rather than quietly keep
passing on a file that no longer represents the problem.

The rest is about the picked pads being real pads: one per physical opening
even when it is drawn as overlapping rectangles, sized and positioned as the
layout draws them, named from the layout's own labels, and landing on top of
the die — including a die that has already been placed on a carrier, where
using the wrong coordinate frame would scatter the pads across the package.
"""

import os
import tempfile

import gdstk
import FreeCAD
import Part

from _harness import TestCase, new_document

import core.chip_proxy as chip_proxy
import core.gds_pads as gds_pads

V = FreeCAD.Vector

# The fixture, in microns: a 2 x 2 mm die with four 70 µm pad openings on
# 76/20 (SKY130's pad.drawing), a die-sized met5 field underneath on 72/20,
# and the die outline on 235/4 (SKY130's prBoundary).
_PAD_LD = (76, 20)
_MET5_LD = (72, 20)
_OUTLINE_LD = (235, 4)
_BUMP_LD = (81, 20)
_PAD_UM = 70.0
# A bump field placed as one array reference — 3 x 2 copies of one cell.
_BUMP_UM = 40.0
_BUMP_ORIGIN_UM = (600.0, 1000.0)
_BUMP_PITCH_UM = 100.0
_BUMP_COLS, _BUMP_ROWS = 3, 2
_PAD_CENTRES_UM = [(200.0, 200.0), (1800.0, 200.0),
                   (200.0, 1800.0), (1800.0, 1800.0)]


def _rect(x0, y0, x1, y1, layer, datatype):
    return gdstk.rectangle((x0, y0), (x1, y1), layer=layer, datatype=datatype)


def _write_fixture(path):
    """A small SKY130-flavoured GDS: pad openings on 76/20, nothing on DT 0/2."""
    lib = gdstk.Library(unit=1e-6, precision=1e-9)

    pad_cell = lib.new_cell("sky130_pad_70")
    half = _PAD_UM / 2.0
    # Drawn as two overlapping rectangles — one physical opening, two
    # polygons, exactly as a real pad cell built from a cross or an L is.
    pad_cell.add(_rect(-half, -half, half, 0.0, *_PAD_LD))
    pad_cell.add(_rect(-half, -half / 2.0, half, half, *_PAD_LD))

    bump_cell = lib.new_cell("bump_40")
    bump_cell.add(_rect(-_BUMP_UM / 2.0, -_BUMP_UM / 2.0,
                        _BUMP_UM / 2.0, _BUMP_UM / 2.0, *_BUMP_LD))

    top = lib.new_cell("chip_top")
    top.add(_rect(0.0, 0.0, 2000.0, 2000.0, *_OUTLINE_LD))
    top.add(_rect(100.0, 100.0, 1900.0, 1900.0, *_MET5_LD))   # a die-sized field
    for i, (x, y) in enumerate(_PAD_CENTRES_UM):
        top.add(gdstk.Reference(pad_cell, (x, y)))
        top.add(gdstk.Label(f"PAD{i + 1}", (x, y), layer=_PAD_LD[0], texttype=5))

    top.add(gdstk.Reference(bump_cell, _BUMP_ORIGIN_UM,
                            columns=_BUMP_COLS, rows=_BUMP_ROWS,
                            spacing=(_BUMP_PITCH_UM, _BUMP_PITCH_UM)))

    lib.write_gds(path)
    return path


def _bump_centres_mm():
    x0, y0 = _BUMP_ORIGIN_UM
    return sorted((round((x0 + i * _BUMP_PITCH_UM) / 1000.0, 6),
                   round((y0 + j * _BUMP_PITCH_UM) / 1000.0, 6))
                  for i in range(_BUMP_COLS) for j in range(_BUMP_ROWS))


def run():
    tc = TestCase("gds_pads")
    directory = tempfile.mkdtemp(prefix="dip_gds_pads_")
    gds = _write_fixture(os.path.join(directory, "sky130_like.gds"))

    _check_automatic_detection_misses_it(tc, gds)
    _check_layer_rows(tc, gds)
    _check_structure_tree(tc, gds)
    _check_pads_from_layers(tc, gds)
    _check_pads_from_cells(tc, gds)
    _check_dedupe_and_labels(tc, gds)
    _check_attach(tc, gds)
    _check_attach_to_full_import(tc, gds)
    _check_replacing_keeps_bonded_pads(tc, gds)
    _check_pads_land_in_the_chips_group(tc, gds)
    return tc.results


def _check_automatic_detection_misses_it(tc, gds):
    pads = chip_proxy.get_bond_pad_positions_mm(gds, {}, [], 3)
    tc.check("automatic detection finds no pads on a SKY130-style layout — "
             "the reason picking them by hand exists at all",
             not pads, f"found {len(pads)}")


def _check_layer_rows(tc, gds):
    rows = gds_pads.layer_rows(gds)
    by_key = {(r["layer"], r["datatype"]): r for r in rows}

    tc.check("every layer that carries geometry is offered",
             set(by_key) == {_PAD_LD, _MET5_LD, _OUTLINE_LD, _BUMP_LD},
             str(sorted(by_key)))

    bumps = _BUMP_COLS * _BUMP_ROWS
    tc.check("an array reference counts as every copy it places, not as the "
             "one reference it is written as",
             by_key[_BUMP_LD]["polygons"] == bumps, str(by_key[_BUMP_LD]))

    pad_row = by_key[_PAD_LD]
    tc.check("the pad layer's polygons are counted through the hierarchy — "
             "the pads are in a referenced cell, not the top one",
             pad_row["polygons"] == 8, str(pad_row["polygons"]))
    tc.check("...and every one of them is pad-sized",
             pad_row["pad_like"] == 8, str(pad_row))
    tc.check("the metal field is not pad-sized, so a glance at the list "
             "tells the pad layer from the routing layer",
             by_key[_MET5_LD]["pad_like"] == 0, str(by_key[_MET5_LD]))
    tc.check("a layer the PDK files do not name still identifies itself by "
             "its numbers",
             pad_row["name"] == "76/20", pad_row["name"])

    named = gds_pads.layer_rows(gds, ihp_map={_PAD_LD: {"edi_name": "pad"}})
    tc.check("...and takes the PDK's name for it when there is one",
             next(r["name"] for r in named
                  if (r["layer"], r["datatype"]) == _PAD_LD) == "pad")


def _check_structure_tree(tc, gds):
    tree = gds_pads.structure_tree(gds)
    tc.check("the tree starts at the real top cell",
             len(tree) == 1 and tree[0]["name"] == "chip_top", str(tree))

    children = {child["name"]: child for child in tree[0]["children"]}
    tc.check("both cells it places appear under it",
             set(children) == {"sky130_pad_70", "bump_40"}, str(list(children)))
    tc.check("an array is reported as the number of bumps it places",
             children["bump_40"]["instances"] == _BUMP_COLS * _BUMP_ROWS,
             str(children["bump_40"]))
    pad_cell = children["sky130_pad_70"]
    tc.check("...with how many times it is placed — which is what identifies "
             "a pad cell in a padframe",
             pad_cell["instances"] == len(_PAD_CENTRES_UM), str(pad_cell))
    tc.check("...and its own size, in mm",
             abs(pad_cell["width_mm"] - _PAD_UM / 1000.0) < 1e-9
             and abs(pad_cell["height_mm"] - _PAD_UM / 1000.0) < 1e-9,
             f"{pad_cell['width_mm']} x {pad_cell['height_mm']}")
    tc.check("...and the layers it draws on, so the pad layer can be found "
             "from the cell as well as the other way round",
             pad_cell["layers"] == [_PAD_LD], str(pad_cell["layers"]))


def _check_pads_from_layers(tc, gds):
    pads = gds_pads.pads_from_layers(gds, [_PAD_LD])
    tc.check("one pad per physical opening, although each is drawn as two "
             "overlapping rectangles",
             len(pads) == len(_PAD_CENTRES_UM), f"got {len(pads)}")

    centres = sorted((round(p["x_mm"], 6), round(p["y_mm"], 6)) for p in pads)
    expected = sorted((round(x / 1000.0, 6), round(y / 1000.0, 6))
                      for x, y in _PAD_CENTRES_UM)
    tc.check("...at the positions the layout draws them", centres == expected,
             f"{centres} vs {expected}")
    tc.check("...sized as the layout draws them",
             all(abs(p["width_mm"] - _PAD_UM / 1000.0) < 1e-9
                 and abs(p["height_mm"] - _PAD_UM / 1000.0) < 1e-9 for p in pads),
             str(pads[0]))
    tc.check("...and each records which layer it came from",
             all(p["source"] == "layer 76/20" for p in pads))

    unmerged = gds_pads.pads_from_layers(gds, [_PAD_LD], merge=False)
    tc.check("without merging, the same openings give two pads each — which "
             "is why merging is the default",
             len(unmerged) == 2 * len(_PAD_CENTRES_UM), str(len(unmerged)))

    field = gds_pads.pads_from_layers(gds, [_MET5_LD])
    tc.check("a die-sized metal field yields nothing: the size filter is what "
             "keeps a shared layer from contributing its seal ring",
             not field, str(field))
    tc.check("...unless the bounds are widened to include it",
             len(gds_pads.pads_from_layers(gds, [_MET5_LD], max_mm=2.0)) == 1)

    tc.check("asking for a layer that is not in the file is empty, not an error",
             gds_pads.pads_from_layers(gds, [(1, 0)]) == [])


def _check_pads_from_cells(tc, gds):
    pads = gds_pads.pads_from_cells(gds, ["sky130_pad_70"])
    tc.check("picking the pad cell finds one pad per instance",
             len(pads) == len(_PAD_CENTRES_UM), f"got {len(pads)}")
    centres = sorted((round(p["x_mm"], 6), round(p["y_mm"], 6)) for p in pads)
    expected = sorted((round(x / 1000.0, 6), round(y / 1000.0, 6))
                      for x, y in _PAD_CENTRES_UM)
    tc.check("...where they are placed", centres == expected, str(centres))

    tc.check("the name is matched exactly, so a cell whose name merely starts "
             "the same is not swept in",
             gds_pads.pads_from_cells(gds, ["sky130_pad"]) == [])
    tc.check("matching ignores case", len(gds_pads.pads_from_cells(
        gds, ["SKY130_PAD_70"])) == len(_PAD_CENTRES_UM))

    bumps = gds_pads.pads_from_cells(gds, ["bump_40"])
    tc.check("a bump field placed as one array reference gives a pad per "
             "bump — reporting one where there are six looks like it worked",
             len(bumps) == _BUMP_COLS * _BUMP_ROWS, f"got {len(bumps)}")
    tc.check("...at the lattice the array defines",
             sorted((round(p["x_mm"], 6), round(p["y_mm"], 6)) for p in bumps)
             == _bump_centres_mm(),
             str(sorted((round(p["x_mm"], 6), round(p["y_mm"], 6)) for p in bumps)))


def _check_dedupe_and_labels(tc, gds):
    both = gds_pads.pick_pads(gds, layer_keys=[_PAD_LD],
                              cell_names=["sky130_pad_70"])
    tc.check("picking both the pad cell and its opening layer still gives one "
             "pad per pad — they describe the same four",
             len(both) == len(_PAD_CENTRES_UM), f"got {len(both)}")
    tc.check("each pad takes its name from the layout's own label",
             sorted(p["label"] for p in both) == ["PAD1", "PAD2", "PAD3", "PAD4"],
             str([p.get("label") for p in both]))


def _check_attach(tc, gds):
    doc = new_document("PadPick")
    try:
        data = chip_proxy.extract_chip_proxy(gds)
        block = chip_proxy.build_chip_proxy_object(doc, data, name="Sky")
        tc.check("the proxy itself is built with no pads, as imported",
                 not gds_pads.existing_pads(doc, block))

        top_z = block.Shape.BoundBox.ZMax
        pads = gds_pads.pick_pads(gds, layer_keys=[_PAD_LD])
        markers = gds_pads.attach_pads(doc, block, pads)
        tc.check("a marker is created for every picked pad",
                 len(markers) == len(pads), f"{len(markers)} vs {len(pads)}")
        tc.check("they sit on top of the die, not inside it",
                 all(abs(m.Shape.BoundBox.ZMin - top_z) < 1e-9 for m in markers),
                 f"{[m.Shape.BoundBox.ZMin for m in markers]} vs {top_z}")
        tc.check("the contact point is on the marker's top face, where a wire "
                 "would land",
                 all(m.ContactPoint.z > top_z for m in markers))
        tc.check("every marker is a contact point belonging to this die, so "
                 "wire bonding treats it like any detected pad",
                 all(m.IsContactPoint and m.SourceObject == block.Name
                     for m in markers))
        tc.check("...and carries the name the layout gave the pad",
                 sorted(m.PadName for m in markers) == ["PAD1", "PAD2", "PAD3", "PAD4"],
                 str([m.PadName for m in markers]))
        tc.check("...and says which layer it was picked from",
                 all(getattr(m, gds_pads.PAD_SOURCE) == "layer 76/20"
                     for m in markers))
        group = next((g for g in doc.Objects
                      if g.isDerivedFrom("App::DocumentObjectGroup")), None)
        tc.check("the pads join the chip's own group in the tree",
                 all(m in (group.Group or []) for m in markers))

        again = gds_pads.attach_pads(doc, block, pads)
        tc.check("picking again adds pads rather than overwriting names — the "
                 "numbering continues instead of colliding",
                 len({m.Name for m in markers + again}) == len(markers) * 2)

        removed = gds_pads.remove_pads(doc, block)
        tc.check("removing this die's pads takes all of them",
                 removed == len(markers) * 2 and not gds_pads.existing_pads(doc, block),
                 str(removed))

        # A die that has already been placed: the pads are measured in the
        # GDS's coordinates, and it is the placement that has to carry them.
        block.Placement.Base = V(5.0, 7.0, 1.0)
        doc.recompute()
        moved = gds_pads.attach_pads(doc, block, pads)
        tc.check("on a die already placed on a carrier, the pads land on that "
                 "die and not back at the origin",
                 all(abs(m.Shape.BoundBox.ZMin - block.Shape.BoundBox.ZMax) < 1e-9
                     for m in moved),
                 f"{[m.Shape.BoundBox.ZMin for m in moved]} vs {block.Shape.BoundBox.ZMax}")
        tc.check("...and the contact point moves with them, rather than being "
                 "left in the die's own coordinates",
                 all(abs(m.ContactPoint.x - (p["x_mm"] + 5.0)) < 1e-9
                     for m, p in zip(moved, pads)),
                 str([(m.ContactPoint.x, p["x_mm"]) for m, p in zip(moved, pads)][:2]))
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_attach_to_full_import(tc, gds):
    """
    A full GDS import is a group of layers, not one block — and once the chip
    has been placed on a carrier, every layer carries that placement. Pads
    picked afterwards have to land on the chip where it now is.
    """
    doc = new_document("PadPickFull")
    try:
        group = doc.addObject("App::DocumentObjectGroup", "GDS_Die")
        metal = doc.addObject("Part::Feature", "Layer_met5_72")
        metal.Shape = Part.makeBox(2.0, 2.0, 0.003, V(0, 0, 0.0))
        via = doc.addObject("Part::Feature", "Layer_via4_71")
        via.Shape = Part.makeBox(2.0, 2.0, 0.001, V(0, 0, -0.001))
        for obj in (metal, via):
            group.addObject(obj)
        doc.recompute()

        pads = gds_pads.pick_pads(gds, layer_keys=[_PAD_LD])
        markers = gds_pads.attach_pads(doc, group, pads)
        tc.check("pads can be put on a full import, not only on a proxy",
                 len(markers) == len(pads) and all(m.IsContactPoint for m in markers),
                 str(len(markers)))
        tc.check("...on top of its highest layer",
                 all(abs(m.Shape.BoundBox.ZMin - 0.003) < 1e-9 for m in markers),
                 str([m.Shape.BoundBox.ZMin for m in markers]))
        tc.check("...and belonging to the import, so a second pick knows they "
                 "are there",
                 len(gds_pads.existing_pads(doc, group)) == len(pads))

        gds_pads.remove_pads(doc, group)
        for obj in (metal, via):
            obj.Placement.Base = V(3.0, 4.0, 2.0)
        doc.recompute()
        moved = gds_pads.attach_pads(doc, group, pads)
        tc.check("on a chip already placed on a carrier the pads follow it, "
                 "rather than staying at the layout's own origin",
                 all(abs(m.Shape.BoundBox.ZMin - 2.003) < 1e-9
                     and abs(m.ContactPoint.x - (p["x_mm"] + 3.0)) < 1e-9
                     for m, p in zip(moved, pads)),
                 str([(m.Shape.BoundBox.ZMin, m.ContactPoint.x) for m in moved][:2]))
    finally:
        FreeCAD.closeDocument(doc.Name)

def _check_replacing_keeps_bonded_pads(tc, gds):
    """
    Re-picking is a correction to the pads, not a decision to throw away
    bonding work. A wire whose pad has been deleted is much harder to notice
    than a pad that is still there, so a bonded pad survives a replace.
    """
    doc = new_document("PadPickReplace")
    try:
        data = chip_proxy.extract_chip_proxy(gds)
        block = chip_proxy.build_chip_proxy_object(doc, data, name="Sky")
        pads = gds_pads.pick_pads(gds, layer_keys=[_PAD_LD])
        markers = gds_pads.attach_pads(doc, block, pads)

        wire = doc.addObject("Part::Feature", "BondWire_001")
        wire.Shape = Part.makeBox(0.01, 0.01, 0.01)
        wire.addProperty("App::PropertyString", "StartCP", "Wirebond", "")
        wire.StartCP = markers[0].Name
        doc.recompute()

        again = gds_pads.attach_pads(doc, block, pads, replace=True)
        left = {m.Name for m in gds_pads.existing_pads(doc, block)}
        tc.check("the pad a wire lands on is still there after replacing",
                 markers[0].Name in left, str(sorted(left)))
        tc.check("...while the rest were replaced, so the count is the new "
                 "pick plus the one that was kept",
                 len(left) == len(again) + 1, f"{len(left)} vs {len(again)} + 1")
        tc.check("...and the wire still points at a pad that exists",
                 doc.getObject(wire.StartCP) is not None)
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check_pads_land_in_the_chips_group(tc, gds):
    """Where the pads appear in the tree: inside the chip, not beside it."""
    doc = new_document("PadPickTree")
    try:
        group = doc.addObject("App::DocumentObjectGroup", "GDS_Die")
        layer = doc.addObject("Part::Feature", "Layer_met5_72")
        layer.Shape = Part.makeBox(2.0, 2.0, 0.003)
        group.addObject(layer)
        doc.recompute()

        pads = gds_pads.pick_pads(gds, layer_keys=[_PAD_LD])
        markers = gds_pads.attach_pads(doc, group, pads)
        tc.check("pads picked for a full import go into that import's group",
                 all(marker in (group.Group or []) for marker in markers),
                 str([o.Name for o in group.Group or []]))
    finally:
        FreeCAD.closeDocument(doc.Name)
