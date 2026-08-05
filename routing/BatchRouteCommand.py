# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Batch Auto-Route — command classes.

StartBatchRouteCommand shows the setup panel (pick face + params, then
queue pairs — see BatchRouteSetupPanel). RouteAllCommand / CancelBatchRouteCommand
live in the contextual "Batch Route Session" toolbar (see InitGui.py /
BatchRouteSession._set_session_toolbar_visible), hidden until a session is
actually active — mirrors routing.TraceRoutingCommand's
ConfirmTraceCommand / AbortTraceCommand / EndRoutingSessionCommand, collapsed
to two commands here since batch route has no per-item confirm/abort step,
only "run everything queued" or "discard the session."
"""

import os
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from Get_Path import get_icon
from routing.BatchRouteSetupPanel import BatchRouteSetupPanel
from routing.BatchRouteSession import batch_router


class StartBatchRouteCommand:
    def GetResources(self):
        return {
            "MenuText": "Batch Auto-Route",
            "ToolTip": (
                "Queue several pad-to-pad connections, then route and bake\n"
                "them all in one pass. Pick a routing surface and parameters,\n"
                "then click pairs of ContactPoint markers in the 3D view."
            ),
            "Pixmap": get_icon("Batch_Route.svg"),
        }

    def Activated(self):
        if FreeCADGui.Control.activeDialog():
            QtWidgets.QMessageBox.information(
                None, "Task panel already open",
                "Please close the current task panel before starting batch routing."
            )
            return
        FreeCADGui.Control.showDialog(BatchRouteSetupPanel())

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


class RouteAllCommand:
    """Routes and bakes every currently queued pair, then ends the session
    — the toolbar equivalent of the setup panel's own Route All button, for
    when the panel has been closed or moved out of the way."""

    def GetResources(self):
        return {
            "MenuText": "Route All",
            "ToolTip": (
                "Route and bake every queued pair, then end the batch route "
                "session.\nAny pair that cannot be routed is reported, not "
                "silently dropped."
            ),
            "Pixmap": get_icon("Confirm_Trace.svg"),
        }

    def Activated(self):
        batch_router.route_all()
        batch_router.end_session()
        if FreeCADGui.Control.activeDialog():
            FreeCADGui.Control.closeDialog()

    def IsActive(self):
        return batch_router.is_active


class CancelBatchRouteCommand:
    """Ends the batch route session without routing anything still queued —
    already-baked traces from an earlier Route All in the same session are
    not affected."""

    def GetResources(self):
        return {
            "MenuText": "Cancel Batch Route",
            "ToolTip": (
                "End the batch route session. Any pairs still queued (not "
                "yet routed) are discarded; nothing is baked for them."
            ),
            "Pixmap": get_icon("Abort_Trace.svg"),
        }

    def Activated(self):
        batch_router.end_session()
        if FreeCADGui.Control.activeDialog():
            FreeCADGui.Control.closeDialog()

    def IsActive(self):
        return batch_router.is_active


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("StartBatchRouteCommand", StartBatchRouteCommand())
    FreeCADGui.addCommand("RouteAllCommand", RouteAllCommand())
    FreeCADGui.addCommand("CancelBatchRouteCommand", CancelBatchRouteCommand())
