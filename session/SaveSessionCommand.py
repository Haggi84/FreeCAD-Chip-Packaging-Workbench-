# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Save Design Session command.

Writes the current in-memory session (all recorded actions and their
parameters) to a .dipas JSON file next to the active FreeCAD document.
"""

import os
import FreeCAD
import FreeCADGui
from compat import QtWidgets

from Get_Path import get_icon
from session.SessionManager import session_manager, SESSION_EXT


class SaveSessionCommand:
    def GetResources(self):
        return {
            "MenuText": "Save Design Session",
            "ToolTip":  (
                "Save current design settings and action history to a "
                ".dipas session file so the design can be replayed later."
            ),
            "Pixmap": get_icon("Save_Session.svg"),
        }

    def Activated(self):
        if not session_manager.has_actions:
            QtWidgets.QMessageBox.information(
                None,
                "No Session to Save",
                "No design actions have been recorded yet.\n\n"
                "Perform at least one action (e.g. Load GDSII, Leadframe "
                "Configurator, …) before saving a session.",
            )
            return

        # Suggest a filename next to the active document
        default_path = ""
        doc = FreeCAD.activeDocument()
        if doc and doc.FileName:
            session_manager.set_document_path(doc.FileName)
            default_path = os.path.splitext(doc.FileName)[0] + SESSION_EXT

        filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
            None,
            "Save Design Session",
            default_path,
            f"DI-PASSIONATE Session (*{SESSION_EXT});;All Files (*)",
        )
        if not filepath:
            return

        try:
            saved = session_manager.save(filepath)
            actions = session_manager.get_actions()
            QtWidgets.QMessageBox.information(
                None,
                "Session Saved",
                f"Design session saved to:\n{saved}\n\n"
                f"Actions recorded: {len(actions)}\n"
                + "\n".join(
                    f"  • {_action_label(a['type'])}" for a in actions
                ),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                None, "Save Failed", f"Could not save session:\n{exc}"
            )

    def IsActive(self):
        return session_manager.has_actions


def _action_label(action_type):
    return {
        "gds_import":         "GDS Import",
        "leadframe_config":   "Leadframe Configurator",
        "layer_on_leadframe": "Layer on Leadframe",
        "housing_config":     "Housing Configurator",
        "wirebond_config":    "Wire Bonding Config",
        "center_leadframe":   "Center Leadframe",
    }.get(action_type, action_type)


FreeCADGui.addCommand("SaveSessionCommand", SaveSessionCommand())
