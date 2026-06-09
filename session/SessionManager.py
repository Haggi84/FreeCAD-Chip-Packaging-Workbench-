# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Session Manager for DI-PASSIONATE FreeCAD workbench.

Records every design action (GDS import, leadframe config, housing config, …)
and its parameters in memory.  Call save() to persist them to a .dipas JSON
file and load() to restore a previous session from such a file.
"""

import json
from datetime import datetime

SESSION_VERSION = "1.0"
SESSION_EXT = ".dipas"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize(obj):
    """Recursively convert values to JSON-safe primitives."""
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, set):
        return sorted(_sanitize(v) for v in obj)
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, str):
        return obj
    if obj is None:
        return None
    return str(obj)


# ---------------------------------------------------------------------------
# SessionManager  (module-level singleton)
# ---------------------------------------------------------------------------

class SessionManager:
    """Singleton that accumulates design actions and manages persistence."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._actions = []          # list of action dicts (one per action_type)
        self._created = None        # ISO timestamp of first recorded action
        self._session_path = None   # path of the last saved/loaded .dipas file
        self._freecad_document = None  # path of the associated .FCStd file

    # ── Recording ──────────────────────────────────────────────────────────

    def record_action(self, action_type, params):
        """Record an action, replacing any previous entry of the same type.

        Args:
            action_type (str): One of: gds_import, leadframe_config,
                layer_on_leadframe, housing_config, wirebond_config,
                center_leadframe.
            params (dict): Serialisable configuration dict for the action.
        """
        if self._created is None:
            self._created = datetime.now().isoformat()

        # Keep only the latest occurrence of each action type
        self._actions = [a for a in self._actions if a["type"] != action_type]
        self._actions.append({
            "id": len(self._actions) + 1,
            "type": action_type,
            "timestamp": datetime.now().isoformat(),
            "params": _sanitize(params),
        })
        # Re-sequence ids
        for i, a in enumerate(self._actions):
            a["id"] = i + 1

    # ── Querying ───────────────────────────────────────────────────────────

    def get_last_params(self, action_type):
        """Return the params dict for the most recent action of *action_type*,
        or None if no such action has been recorded."""
        for a in reversed(self._actions):
            if a["type"] == action_type:
                return a["params"]
        return None

    def get_actions(self):
        """Return a copy of the recorded action list."""
        return list(self._actions)

    @property
    def has_actions(self):
        return bool(self._actions)

    # ── Document association ───────────────────────────────────────────────

    def set_document_path(self, path):
        """Associate this session with a FreeCAD document path (.FCStd)."""
        self._freecad_document = path

    @property
    def freecad_document(self):
        return self._freecad_document

    # ── Persistence ────────────────────────────────────────────────────────

    def save(self, filepath):
        """Serialise the session to *filepath* (adds .dipas extension if absent).

        Returns the final file path used.
        """
        if not filepath.endswith(SESSION_EXT):
            filepath += SESSION_EXT

        data = {
            "version": SESSION_VERSION,
            "created": self._created or datetime.now().isoformat(),
            "modified": datetime.now().isoformat(),
            "freecad_document": self._freecad_document,
            "actions": self._actions,
        }
        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)

        self._session_path = filepath
        return filepath

    def load(self, filepath):
        """Load a session from *filepath*.

        Replaces in-memory state.  Returns the raw session data dict.
        """
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        self._actions = data.get("actions", [])
        self._created = data.get("created")
        self._freecad_document = data.get("freecad_document")
        self._session_path = filepath
        return data

    def clear(self):
        """Reset the session (e.g. when starting a fresh design)."""
        self._init()

    @property
    def session_path(self):
        return self._session_path


# Module-level singleton — import this everywhere
session_manager = SessionManager()
