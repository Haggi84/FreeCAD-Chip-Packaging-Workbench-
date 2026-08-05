# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Drag-edit an existing trace — KiCad-style trace dragging.

Select a Trace_NNN and activate the tool: as the mouse moves, the point
under the cursor becomes an intermediate waypoint and BOTH halves of the
trace (start -> cursor, cursor -> end) are re-routed live with the same
walk-around the interactive router uses, around all OTHER copper (the
dragged trace itself is excluded from the obstacle set — it is being
replaced). Click to drop the trace in its new shape, Esc to leave it
unchanged, '/' flips the corner posture.

The endpoints never move — dragging re-SHAPES the connection, it does not
re-CONNECT it. The commit rebuilds the existing object in place
(routing/InteractiveRouterCommand.rebuild_trace), so the trace keeps its
name, net, width and colours.

Built on the same Draft interactive infrastructure as
routing/InteractiveRouterCommand.py — see that module's docstring for the
two Coin3D rules that matter (never mutate the document inside the event
callback; working-plane access with update=False).
"""

import FreeCAD
import FreeCADGui

import core.trace_obstacles as trace_obstacles
import core.trace_walkaround as walkaround
from routing.InteractiveRouterCommand import resolve_trace_frame
from Get_Path import get_icon

_TRACE_COLOR   = (0.85, 0.55, 0.20)
_BLOCKED_COLOR = (0.90, 0.15, 0.15)


def _selected_trace(doc):
    try:
        sel = FreeCADGui.Selection.getSelection()
    except Exception:
        sel = []
    for obj in sel:
        if getattr(obj, "IsRoutingTrace", False):
            return obj
    return None


class DragTraceCommand:
    """FreeCAD command wrapper — the interactive work lives in the Draft
    Creator subclass built lazily in Activated(), same structure as
    InteractiveRouteCommand."""

    def GetResources(self):
        return {
            "MenuText": "Drag Trace",
            "ToolTip": (
                "Re-shape an existing trace by dragging: select a trace,\n"
                "activate, move the mouse — the trace follows the cursor,\n"
                "walking around other copper. Click to drop it, '/' flips\n"
                "the corner, Esc leaves the trace unchanged."
            ),
            "Pixmap": get_icon("Drag_Trace.svg"),
        }

    def Activated(self):
        tool = _make_drag_tool()
        if tool is None:
            return
        tool.Activated()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


def _make_drag_tool():
    """Build the Draft-based tool — Draft imported here, not at module
    import, for the same reasons as InteractiveRouterCommand._make_tool."""
    try:
        import DraftTools  # noqa: F401  (imported for its side effects)
        from draftguitools import gui_base_original, gui_tool_utils
        from draftguitools import gui_trackers as trackers
    except Exception as exc:
        FreeCAD.Console.PrintError(
            "[DragTrace] FreeCAD's Draft workbench is required for trace "
            f"dragging and could not be loaded: {exc}\n"
        )
        return None

    if not hasattr(FreeCAD, "activeDraftCommand"):
        FreeCAD.activeDraftCommand = None
    if not hasattr(FreeCADGui, "draftToolBar"):
        FreeCAD.Console.PrintError(
            "[DragTrace] Draft's tool bar is not available; open the Draft "
            "workbench once and try again.\n"
        )
        return None

    class _DragTool(gui_base_original.Creator):

        def Activated(self):
            super().Activated(name="DragTrace")
            doc = FreeCAD.activeDocument()
            if doc is None:
                self.finish()
                return

            trace = _selected_trace(doc)
            if trace is None:
                FreeCAD.Console.PrintError(
                    "[DragTrace] Select the trace to drag (a Trace_NNN "
                    "object) before starting the tool.\n"
                )
                self.finish()
                return

            frame, src_name, _idx = resolve_trace_frame(doc, trace)
            if frame is None:
                FreeCAD.Console.PrintError(
                    f"[DragTrace] Cannot resolve which surface {trace.Name} "
                    "was routed on.\n"
                )
                self.finish()
                return

            wp = list(trace.Waypoints or [])
            if len(wp) < 2:
                FreeCAD.Console.PrintError(
                    f"[DragTrace] {trace.Name} has no editable waypoints.\n")
                self.finish()
                return
            start2 = frame.to_2d(wp[0])
            end2 = frame.to_2d(wp[-1])
            if start2 is None or end2 is None:
                FreeCAD.Console.PrintError(
                    f"[DragTrace] {trace.Name}'s endpoints do not lie on its "
                    "routing surface.\n")
                self.finish()
                return

            self.trace_name = trace.Name
            self.frame = frame
            self.start2 = start2
            self.end2 = end2
            self.flip = False
            self.blocked = False
            self.path2d = None      # committed on click
            self.point = None

            width = float(getattr(trace, "TraceWidth", 0.3))
            thickness = float(getattr(trace, "TraceThickness", 0.035))
            clearance = float(getattr(trace, "Clearance", 0.2))
            band = thickness + clearance

            # The dragged trace itself is EXCLUDED — it is being replaced;
            # everything else (other traces included) stays an obstacle.
            face = frame.face
            polys = trace_obstacles.collect_obstacle_polys_on_frame(
                doc, {src_name, trace.Name}, self.frame, band)
            polys += trace_obstacles.face_hole_polys_on_frame(face, self.frame)
            self.field = trace_obstacles.PolyField(
                polys, clearance=clearance + width / 2.0, cell_size=1.0,
                boundary=trace_obstacles.face_outer_poly_on_frame(face, self.frame))

            try:
                self.wp.align_to_face(face)
            except Exception:
                pass

            import Part
            z = face.BoundBox.ZMax
            self.wire = trackers.wireTracker(Part.makePolygon(
                [FreeCAD.Vector(0, 0, z), FreeCAD.Vector(1, 0, z)]))
            self.wire.setColor(_TRACE_COLOR)
            self.ui.wireUi(title="Drag Trace")
            self.call = self.view.addEventCallback("SoEvent", self.action)

            FreeCAD.Console.PrintMessage(
                f"[DragTrace] Dragging {trace.Name} — move the mouse, click "
                "to drop, '/' flips the corner, Esc cancels.\n"
            )

        # ── event loop ───────────────────────────────────────────────────

        def action(self, arg):
            if arg["Type"] == "SoKeyboardEvent":
                key = arg.get("Key", "")
                if key == "ESCAPE":
                    self.path2d = None      # leave the trace unchanged
                    self.finish()
                elif key == "/":
                    self.flip = not self.flip
                    self._recompute(self.point)
                return

            if arg["Type"] == "SoLocation2Event":
                self.point = self._cursor_2d(arg)
                if self.point is not None:
                    self._recompute(self.point)
                gui_tool_utils.redraw3DView()
                return

            if (arg["Type"] == "SoMouseButtonEvent"
                    and arg["State"] == "DOWN" and arg["Button"] == "BUTTON1"):
                if self.blocked or self.path2d is None:
                    FreeCAD.Console.PrintWarning(
                        "[DragTrace] No clear route through the cursor — move "
                        "it somewhere else, or press '/' to flip the corner.\n"
                    )
                    return
                self.finish()       # commits self.path2d

        # ── cursor / preview ─────────────────────────────────────────────

        def _cursor_2d(self, arg):
            near = self.point
            pos = arg.get("Position")
            if pos:
                try:
                    info = self.view.getObjectInfo((pos[0], pos[1]))
                except Exception:
                    info = None
                if info and all(k in info for k in ("x", "y", "z")):
                    hit = FreeCAD.Vector(info["x"], info["y"], info["z"])
                    xy = self.frame.to_2d(hit, near=near)
                    if xy is not None:
                        return xy
            try:
                pt, _ctrl, _i = gui_tool_utils.get_point(self, arg)
            except Exception:
                pt = None
            if pt is None:
                return None
            return self.frame.to_2d(pt, near=near)

        def _recompute(self, cursor2d):
            """Route start -> cursor -> end entirely in the frame's 2-D
            space; the grabbed point is an intermediate waypoint both halves
            walk around obstacles to reach."""
            if cursor2d is None:
                return
            a = FreeCAD.Vector(self.start2[0], self.start2[1], 0.0)
            m = FreeCAD.Vector(cursor2d[0], cursor2d[1], 0.0)
            c = FreeCAD.Vector(self.end2[0], self.end2[1], 0.0)

            first, used_flip = walkaround.route_head(
                a, m, self.field, step_deg=walkaround.STEP_45, flip=self.flip)
            second = None
            if first is not None:
                second, _f2 = walkaround.route_head(
                    m, c, self.field, step_deg=walkaround.STEP_45, flip=self.flip)

            if first is None or second is None:
                self.blocked = True
                pts = (walkaround.posture_points(a, m, walkaround.STEP_45, self.flip)
                       + walkaround.posture_points(m, c, walkaround.STEP_45, self.flip)[1:])
                self.path2d = None
            else:
                self.blocked = False
                self.flip = used_flip
                pts = list(first) + list(second)[1:]
                self.path2d = [(p.x, p.y) for p in pts]
            self._update_tracker([(p.x, p.y) for p in pts])

        def _update_tracker(self, pts2):
            if len(pts2) < 2:
                self.wire.off()
                return
            step = 1.0 if not self.frame.is_planar else 1e9
            pts3 = self.frame.path_to_3d(pts2, max_step_mm=step)
            if len(pts3) < 2:
                self.wire.off()
                return
            self.wire.setColor(_BLOCKED_COLOR if self.blocked else _TRACE_COLOR)
            self.wire.line.numVertices.setValue(len(pts3))
            self.wire.updateFromPointlist(pts3)
            self.wire.on()

        # ── finish ───────────────────────────────────────────────────────

        def finish(self, cont=False):
            if getattr(self, "call", None) is not None and getattr(self, "view", None) is not None:
                try:
                    self.end_callbacks(self.call)
                except Exception:
                    pass
            self.call = None
            if getattr(self, "wire", None) is not None:
                try:
                    self.wire.finalize()
                except Exception:
                    pass
            path = getattr(self, "path2d", None)
            if path and len(path) >= 2:
                pts = [(round(x, 6), round(y, 6)) for x, y in path]
                self.commit("Drag Trace", [
                    "import FreeCAD",
                    "from routing.InteractiveRouterCommand import rebuild_trace",
                    f"rebuild_trace({self.trace_name!r}, {pts!r})",
                ])
            super().finish()

    return _DragTool()


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("DragTraceCommand", DragTraceCommand())
