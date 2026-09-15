# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Smoke test for session/WorkbenchState.py — the extensible document-level
state registry that replaced the old .dipas action-replay system.

WorkbenchState.py itself is imported normally (`import session.WorkbenchState`),
not via load_module_from_file — this matters: gds/ToggleViaDetailCommand.py
also does a normal `from session.WorkbenchState
import register_state_provider`, so a normal import here shares the SAME
module instance (and its module-level _PROVIDERS dict) that those real
provider registrations land in — a load_module_from_file copy would be a
separate module object with its own empty registry, unable to observe
whether the real subsystems actually self-register.

Headless caveat: FreeCADGui.addDocumentObserver's slotFinishRestoreDocument
(the Gui-level restore hook) can't be exercised under freecadcmd — this
test instead calls restore_from_document() directly, which is the entire
logic the Gui slot delegates to (see WorkbenchState.RestoreObserver).
"""

import json

import FreeCAD

from _harness import TestCase, load_module_from_file, new_document
import session.WorkbenchState as ws


def run():
    tc = TestCase("workbench_state")

    doc = new_document("TestWorkbenchState")

    # ── registry round-trip with a synthetic provider ───────────────────────
    captured = {}
    ws.register_state_provider(
        "dummy_test_provider",
        save_fn=lambda d: {"value": 42},
        restore_fn=lambda d, data: captured.update(data),
    )

    def _save():
        ws.collect_and_store(doc)

    if tc.check_raises_nothing("collect_and_store: no exception", _save):
        tc.check("hidden state property created on the document",
                 hasattr(doc, ws._PROP_NAME))
        blob = json.loads(getattr(doc, ws._PROP_NAME))
        tc.check("dummy provider's state captured",
                 blob.get("dummy_test_provider") == {"value": 42})

    def _restore():
        ws.restore_from_document(doc)

    if tc.check_raises_nothing("restore_from_document: no exception", _restore):
        tc.check("dummy provider's restore_fn invoked with the saved data",
                 captured.get("value") == 42)

    # ── a broken save provider must not block the others ────────────────────
    ws.register_state_provider(
        "broken_test_provider",
        save_fn=lambda d: 1 / 0,
        restore_fn=lambda d, s: None,
    )
    tc.check_raises_nothing("collect_and_store tolerates a broken provider", _save)
    blob = json.loads(getattr(doc, ws._PROP_NAME))
    tc.check("other providers still captured despite one broken provider",
             blob.get("dummy_test_provider") == {"value": 42})

    # ── real subsystem providers actually self-register at module import ────
    # The "gds_fast_mode" provider was registered here too, by the fast-mesh
    # module; that feature was removed. Documents saved while it existed
    # still carry the key, which is the case the tolerance check below pins.
    via = load_module_from_file("gds_via_wbstate_test", "gds/ToggleViaDetailCommand.py")
    tc.check("gds_via_detail provider registered", "gds_via_detail" in ws._PROVIDERS)
    tc.check("the removed fast-mesh provider no longer registers itself",
             "gds_fast_mode" not in ws._PROVIDERS)

    via._via_detailed = True
    ws.collect_and_store(doc)
    via._via_detailed = False
    ws.restore_from_document(doc)
    tc.check("via_detailed flag restored across a stash/restore cycle",
             via._via_detailed is True)

    # A document saved by an older version carries state for a provider that
    # no longer exists. Restoring must ignore it rather than raise — that is
    # what makes removing a feature safe for files already on disk.
    blob = json.loads(getattr(doc, ws._PROP_NAME))
    blob["gds_fast_mode"] = {"fast_mode": True}
    setattr(doc, ws._PROP_NAME, json.dumps(blob))

    def _restore_stale():
        ws.restore_from_document(doc)

    tc.check_raises_nothing(
        "restoring a document that stores state for a REMOVED provider is "
        "ignored, not an error", _restore_stale)
    tc.check("the surviving provider still restores alongside the stale key",
             via._via_detailed is True)

    FreeCAD.closeDocument(doc.Name)
    return tc.results
