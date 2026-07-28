# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Trace Routing — command classes.

TraceRoutingCommand shows the setup panel (pick routing surface + params,
see TraceRoutingSetupPanel). ConfirmTraceCommand / AbortTraceCommand /
EndRoutingSessionCommand live in the contextual "Trace Routing Session"
toolbar (see InitGui.py / TraceRoutingSession._set_session_toolbar_visible),
hidden until a session is actually active — mirrors
wirebond.WirebondCommand's FinishWireBondingCommand / CancelWireBondingCommand,
with a third command this feature needs that wire bonding doesn't: ending
the whole session is a separate action from confirming/aborting a single
in-progress trace.
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
from routing.TraceRoutingSetupPanel import TraceRoutingSetupPanel
from routing.TraceRoutingSession import trace_router


class TraceRoutingCommand:
    def GetResources(self):
        return {
            "MenuText": "Trace Routing",
            "ToolTip": (
                "Semi-automated point-to-point conductor-trace routing.\n"
                "Pick a routing surface and parameters, then click grid "
                "points in the 3D view to route traces."
            ),
            "Pixmap": get_icon("Trace_Routing.svg"),
        }

    def Activated(self):
        if FreeCADGui.Control.activeDialog():
            QtWidgets.QMessageBox.information(
                None, "Task panel already open",
                "Please close the current task panel before starting trace routing."
            )
            return
        FreeCADGui.Control.showDialog(TraceRoutingSetupPanel())

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


class ConfirmTraceCommand:
    """
    Confirms the in-progress trace: bakes it as a permanent object, keeps
    the routing session active for the next trace. See TraceRoutingCommand's
    docstring for why IsActive() must track session state, not just return
    True — this command has no useful effect while no session is active.
    """

    def GetResources(self):
        return {
            "MenuText": "Confirm Trace",
            "ToolTip": (
                "Bake the in-progress trace as a permanent object.\n"
                "The routing session stays active for the next trace."
            ),
            "Pixmap": get_icon("Confirm_Trace.svg"),
        }

    def Activated(self):
        trace_router.confirm_trace()

    def IsActive(self):
        return trace_router.is_active


class AbortTraceCommand:
    """Discards only the in-progress trace's provisional leg previews —
    previously confirmed traces are untouched. See ConfirmTraceCommand's
    docstring for why IsActive() tracks session state."""

    def GetResources(self):
        return {
            "MenuText": "Abort Trace",
            "ToolTip": (
                "Discard the in-progress trace only.\n"
                "Previously confirmed traces are not affected."
            ),
            "Pixmap": get_icon("Abort_Trace.svg"),
        }

    def Activated(self):
        trace_router.abort_trace()

    def IsActive(self):
        return trace_router.is_active


class EndRoutingSessionCommand:
    """Ends the whole routing session (grid markers removed, click-to-route
    mode turned off). Any unconfirmed in-progress trace is discarded first
    — already-confirmed traces stay in the document."""

    def GetResources(self):
        return {
            "MenuText": "End Routing Session",
            "ToolTip": (
                "End the trace routing session.\n"
                "An unconfirmed in-progress trace is discarded first; "
                "already-confirmed traces stay in the document."
            ),
            "Pixmap": get_icon("End_Routing.svg"),
        }

    def Activated(self):
        trace_router.end_session()

    def IsActive(self):
        return trace_router.is_active


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("TraceRoutingCommand", TraceRoutingCommand())
    FreeCADGui.addCommand("ConfirmTraceCommand", ConfirmTraceCommand())
    FreeCADGui.addCommand("AbortTraceCommand", AbortTraceCommand())
    FreeCADGui.addCommand("EndRoutingSessionCommand", EndRoutingSessionCommand())
