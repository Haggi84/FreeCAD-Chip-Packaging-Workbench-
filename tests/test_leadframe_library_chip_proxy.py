# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Regression test: leadframe/LeadframeLibrary.py's STEP-package-import flow
must recognize a document containing only a core.chip_proxy lightweight
chip as a valid "GDS document" to merge into.

Before this fix, _find_gds_document() only matched a document literally
named "GDSII_Document" or containing "Layer_"-prefixed objects — neither
of which a chip-proxy document has (its objects are named
"<name>_Block"/"<name>_Pad_NNN" and live in a document the user named
themselves, e.g. "ChipLayout"). The practical symptom: importing a STEP
package via Leadframe Library while a chip-proxy document was open
silently disabled the "Merge into GDS document" option — there was no way
to combine an imported package with a chip proxy at all.

Also covers gds.ChipTransformCommand._gds_objects(), which had the exact
same name-prefix-only gap for its "GDS Chip Objects" selection scope.
"""

import FreeCAD

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
        {"name": "Pad", "x_mm": 0.2, "y_mm": 0.2, "width_mm": 0.05, "height_mm": 0.05},
        {"name": "Pad", "x_mm": 0.8, "y_mm": 0.8, "width_mm": 0.05, "height_mm": 0.05},
    ],
}


def run():
    tc = TestCase("leadframe_library_chip_proxy")

    lfl = load_module_from_file("leadframe_library_chip_proxy_test",
                                 "leadframe/LeadframeLibrary.py")
    ctc = load_module_from_file("chip_transform_gds_objects_test",
                                 "gds/ChipTransformCommand.py")

    # A document named nothing like "GDSII_Document", containing ONLY a
    # chip proxy — exactly the real-world "ChipLayout" scenario.
    doc = new_document("MyChipLayout")
    block = chip_proxy.build_chip_proxy_object(doc, _PROXY_DATA, name="TestChip")
    pads  = [o for o in doc.Objects if getattr(o, "IsContactPoint", False)]
    tc.check("fixture: 2 pad markers created", len(pads) == 2, f"got {len(pads)}")

    # ── _find_gds_document ──────────────────────────────────────────────────
    found_name = lfl._find_gds_document()
    tc.check("_find_gds_document finds a chip-proxy-only document",
              found_name == doc.Name, f"got {found_name!r}, expected {doc.Name!r}")

    # ── _gds_die_objects ─────────────────────────────────────────────────────
    die_objs = lfl._gds_die_objects(doc)
    die_names = {o.Name for o in die_objs}
    tc.check("_gds_die_objects includes the proxy block",
              block.Name in die_names, f"got {die_names}")
    tc.check("_gds_die_objects includes every pad marker",
              all(p.Name in die_names for p in pads), f"got {die_names}")

    # ── ChipTransformCommand._gds_objects (the parallel fix) ─────────────────
    gds_objs = ctc._gds_objects(doc)
    gds_names = {o.Name for o in gds_objs}
    tc.check("ChipTransformCommand._gds_objects includes the proxy block",
              block.Name in gds_names, f"got {gds_names}")
    tc.check("ChipTransformCommand._gds_objects includes every pad marker",
              all(p.Name in gds_names for p in pads), f"got {gds_names}")

    # ── regression guard: an unrelated plain document (no GDSII_Document
    # naming, no Layer_ objects, no chip proxy) must never be the one
    # _find_gds_document() picks, even while a real chip-proxy document is
    # also open ──────────────────────────────────────────────────────────────
    plain_doc = new_document("PlainDoc")
    import Part
    from FreeCAD import Base
    o = plain_doc.addObject("Part::Feature", "SomeBox")
    o.Shape = Part.makeBox(1, 1, 1, Base.Vector(0, 0, 0))
    plain_doc.recompute()

    tc.check("_find_gds_document does not match an unrelated plain document",
              lfl._find_gds_document() != plain_doc.Name)
    tc.check("_gds_die_objects on the plain document falls back to all its shapes "
              "(none tagged as die objects, so nothing to prefer over the whole doc)",
              o.Name in {ob.Name for ob in lfl._gds_die_objects(plain_doc)})

    FreeCAD.closeDocument(doc.Name)
    FreeCAD.closeDocument(plain_doc.Name)
    return tc.results
