# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Extensible document-level state registry.

FreeCAD's native .FCStd save/reopen already persists every DocumentObject's
Shape/Mesh/Placement/custom App::Property* fields automatically — so most
workbench state (contact points, lead tags, pin numbers, mesh companions,
via blocks, LOD placeholders) survives a normal save/reopen with zero extra
code.

The remaining gap: a few subsystems keep state in Python module-level
globals that are NOT tied to any DocumentObject (fast-mesh mode, VIA detail
mode, the LOD manager registry) — this is lost when the FreeCAD process
restarts even though the document itself reopens fine.

This module stores that residual state as ONE JSON blob in a hidden
App::Document-level property (confirmed to round-trip through
saveAs/openDocument), and lets each subsystem register its own
save/restore hook — so adding persisted state for a NEW feature later is
"write two functions and call register_state_provider() once," no changes
needed here or in the Save/Load commands.
"""

import json

import FreeCAD

_PROP_NAME  = "DIPassionateWorkbenchState"
_PROP_GROUP = "DI-PASSIONATE"

_PROVIDERS: dict = {}   # key(str) -> (save_fn, restore_fn)


def register_state_provider(key, save_fn, restore_fn):
    """
    Register (or replace) the save/restore pair for *key*.

    save_fn(doc) -> dict | None   Return a JSON-safe dict, or None if there
                                   is nothing worth persisting right now.
    restore_fn(doc, state: dict)  Re-apply previously saved state to *doc*.
    """
    _PROVIDERS[key] = (save_fn, restore_fn)


def collect_and_store(doc):
    """
    Gather state from every registered provider and stash it on *doc*.

    Must run BEFORE the document is written to disk (see
    SaveObserver.slotStartSaveDocument) — gathering after the write would
    mean the just-completed save doesn't contain the freshly captured
    state, reproducing the "saved state != actual state" bug this module
    exists to eliminate.
    """
    if doc is None:
        return
    state = {}
    for key, (save_fn, _restore_fn) in _PROVIDERS.items():
        try:
            data = save_fn(doc)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[WorkbenchState] save provider '{key}' failed: {exc}\n"
            )
            continue
        if data is not None:
            state[key] = data

    try:
        if not hasattr(doc, _PROP_NAME):
            doc.addProperty(
                "App::PropertyString", _PROP_NAME, _PROP_GROUP,
                "DI-PASSIONATE internal workbench state (JSON). "
                "Managed automatically — do not edit.",
                0, False, True,   # attr, read_only, hidden
            )
        setattr(doc, _PROP_NAME, json.dumps(state))
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[WorkbenchState] could not stash state on document: {exc}\n"
        )


def restore_from_document(doc):
    """
    Re-apply stashed state to *doc*. Safe no-op if nothing was stashed
    (fresh documents, documents saved before this feature existed, etc.).
    """
    if doc is None or not hasattr(doc, _PROP_NAME):
        return
    raw = getattr(doc, _PROP_NAME, "") or ""
    if not raw:
        return
    try:
        state = json.loads(raw)
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[WorkbenchState] corrupt state blob on '{doc.Name}': {exc}\n"
        )
        return
    for key, data in state.items():
        provider = _PROVIDERS.get(key)
        if provider is None:
            FreeCAD.Console.PrintWarning(
                f"[WorkbenchState] no provider registered for '{key}' "
                "— skipping (feature module not imported yet?)\n"
            )
            continue
        _save_fn, restore_fn = provider
        try:
            restore_fn(doc, data)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[WorkbenchState] restore provider '{key}' failed: {exc}\n"
            )


# ── Document observers ──────────────────────────────────────────────────

class SaveObserver:
    """App-level; works headless too. Register once via
    FreeCAD.addDocumentObserver()."""

    def slotStartSaveDocument(self, doc, filename):
        # MUST be Start, not Finish: slotFinishSaveDocument fires after the
        # file is already on disk, one save too late.
        collect_and_store(doc)


class RestoreObserver:
    """Gui-level ONLY — there is no App-level 'restore complete' event.
    Register once via FreeCADGui.addDocumentObserver(), gated behind
    FreeCAD.GuiUp."""

    def slotFinishRestoreDocument(self, doc):
        restore_from_document(doc)
