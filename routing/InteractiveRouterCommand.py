# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Interactive trace router — KiCad-style live routing.

Unlike routing/TraceRoutingCommand.py (which samples a grid, builds a
visibility graph and searches it), this tool never searches: you click a
start point, and as you MOVE THE MOUSE a two-segment 45-degree "head"
follows the cursor, walking around copper in the way. Click to fix the head,
Esc or double-click to finish.

That is how KiCad's router behaves, and it removes the problems inherent to
the grid model — there are no grid points to hit, nothing depends on grid
spacing, and "no route" is a visibly blocked head you can steer out of
rather than a silent failure.

Built on FreeCAD's own Draft interactive infrastructure rather than raw
Coin3D:
  * draftguitools.gui_base_original.Creator — command lifecycle, task UI,
    and a finish() that tears down the snapper/working plane and commits
    document changes outside the event callback.
  * view.addEventCallback("SoEvent", ...) — mouse and keyboard.
  * gui_tool_utils.get_point() — the snapped cursor position, already
    projected onto the working plane, so pads and track ends snap for free.
  * gui_trackers.wireTracker / boxTracker — cheap per-move preview.

Two rules that matter and are easy to get wrong:
  * NEVER mutate the scene graph or the document inside the event callback —
    trackers defer their own inserts, and document changes go through
    self.commit(). Updating Coin FIELD values in the callback is the fast,
    safe path.
  * WorkingPlane.get_working_plane(update=False) inside move handlers;
    update=True is documented as too slow to call per mouse move.
"""

import os
import sys

import FreeCAD
import FreeCADGui
import Part

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from Get_Path import get_icon

import core.trace_obstacles as trace_obstacles
import core.trace_walkaround as walkaround
import core.routing_frame as routing_frame

_TRACE_COLOR   = (0.85, 0.55, 0.20)   # copper
_BLOCKED_COLOR = (1.00, 0.20, 0.20)   # head has nowhere to go

# Defaults; the command reads any values already set by the grid router's
# setup panel so both tools behave consistently in one document.
DEFAULT_WIDTH_MM     = 0.3
DEFAULT_THICKNESS_MM = 0.035
DEFAULT_CLEARANCE_MM = 0.2


def _next_trace_index(doc) -> int:
    return len([o for o in doc.Objects if o.Name.startswith("Trace_")]) + 1


def _traces_group(doc):
    grp = next((o for o in doc.Objects if o.Name == "Traces"), None)
    if grp is None:
        grp = doc.addObject("App::DocumentObjectGroup", "Traces")
        grp.Label = "Traces"
    return grp


def _top_face_of(obj):
    """
    Fallback routing surface when a whole object is selected rather than one
    of its faces: its uppermost near-horizontal planar face.

    Only a fallback — ANY face can be routed on (side walls, slanted faces,
    curved flanks); pick it explicitly to use it. This just guesses the
    sensible default for a board or a package lid.
    """
    import Part
    best = None
    best_key = None
    for f in getattr(obj.Shape, "Faces", []):
        try:
            if not isinstance(f.Surface, Part.Plane):
                continue
            if abs(f.normalAt(0, 0).z) < 0.7:
                continue
        except Exception:
            continue
        key = (round(f.BoundBox.ZMax, 6), f.Area)
        if best_key is None or key > best_key:
            best_key = key
            best = f
    return best


class InteractiveRouteCommand:
    """FreeCAD command wrapper. The interactive work lives in the Draft
    Creator subclass built lazily in Activated(), so importing this module
    never requires Draft to be loaded."""

    def GetResources(self):
        return {
            "MenuText": "Interactive Route (KiCad style)",
            "ToolTip": (
                "Route traces the way KiCad does: click a start point, then\n"
                "move the mouse — the trace follows at 45 degrees and walks\n"
                "around copper in the way.\n\n"
                "Click to fix a segment,  /  flips the corner,\n"
                "Backspace undoes the last segment,  Esc finishes."
            ),
            "Pixmap": get_icon("Interactive_Route.svg"),
        }

    def Activated(self):
        tool = _make_tool()
        if tool is None:
            return
        tool.Activated()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


def _make_tool():
    """Build the Draft-based tool. Draft is imported here (not at module
    import) so this workbench still loads if Draft is unavailable."""
    try:
        # Draft's tool base class relies on state that only exists once the
        # Draft GUI has initialised — FreeCAD.activeDraftCommand,
        # FreeCADGui.draftToolBar and FreeCADGui.Snapper. Those are set up as
        # a side effect of importing DraftTools (DraftTools.py:211,
        # DraftGui.py:2110), which is the documented way to force-load them
        # from another workbench; without it Creator.Activated raises
        # "module 'FreeCAD' has no attribute 'activeDraftCommand'" unless the
        # user happens to have visited the Draft workbench first.
        import DraftTools  # noqa: F401  (imported for its side effects)
        from draftguitools import gui_base_original, gui_tool_utils
        from draftguitools import gui_trackers as trackers
    except Exception as exc:
        FreeCAD.Console.PrintError(
            "[InteractiveRoute] FreeCAD's Draft workbench is required for the "
            f"interactive router and could not be loaded: {exc}\n"
        )
        return None

    # Defence in depth: if a future Draft reshuffles those side effects, fail
    # with something actionable rather than an AttributeError from inside
    # Draft's own base class.
    if not hasattr(FreeCAD, "activeDraftCommand"):
        FreeCAD.activeDraftCommand = None
    if not hasattr(FreeCADGui, "draftToolBar"):
        FreeCAD.Console.PrintError(
            "[InteractiveRoute] Draft's tool bar is not available; open the "
            "Draft workbench once and try again.\n"
        )
        return None

    class _Tool(gui_base_original.Creator):

        def Activated(self):
            super().Activated(name="Trace")
            doc = FreeCAD.activeDocument()
            if doc is None:
                self.finish()
                return

            self.face_index = 0
            face, source_name = self._pick_surface()
            if face is None:
                FreeCAD.Console.PrintError(
                    "[InteractiveRoute] Select the face to route on (e.g. the PCB "
                    "top face) before starting the tool.\n"
                )
                self.finish()
                return

            self.width = DEFAULT_WIDTH_MM
            self.thickness = DEFAULT_THICKNESS_MM
            self.clearance = DEFAULT_CLEARANCE_MM
            self.step_deg = walkaround.STEP_45
            self.flip = False
            self.nodes = []            # fixed waypoints
            self.head = []             # live head from nodes[-1] to the cursor
            self.blocked = False

            # The 2-D space this trace is routed in: the face's own parameter
            # space scaled to mm. Flat OR curved, any orientation — see
            # core.routing_frame.
            self.face = face
            self.source_name = source_name
            self.frame = routing_frame.SurfaceFrame(face)
            self.plane_z = face.BoundBox.ZMax     # only used for flat previews
            try:
                self.wp.align_to_face(face)
            except Exception:
                pass

            band = self.thickness + self.clearance
            polys = trace_obstacles.collect_obstacle_polys_on_frame(
                doc, {source_name}, self.frame, band)
            polys += trace_obstacles.face_hole_polys_on_frame(face, self.frame)
            self.field = trace_obstacles.PolyField(
                polys, clearance=self.clearance + self.width / 2.0, cell_size=1.0,
                boundary=trace_obstacles.face_outer_poly_on_frame(face, self.frame))

            self.wire = trackers.wireTracker(_two_point_wire(self.plane_z))
            self.wire.setColor(_TRACE_COLOR)
            self.box = trackers.boxTracker(width=self.width,
                                            height=max(self.thickness, 1e-3),
                                            shaded=True)
            self.ui.wireUi(title="Interactive Route")
            self.call = self.view.addEventCallback("SoEvent", self.action)

            distortion = self.frame.distortion()
            kind = "flat" if self.frame.is_planar else "curved"
            if distortion > 0.05:
                FreeCAD.Console.PrintWarning(
                    "[InteractiveRoute] This surface is double-curved "
                    f"({distortion * 100:.0f}% scale variation), so trace widths, "
                    "clearances and 45-degree angles hold only approximately "
                    "away from the middle of the face.\n"
                )
            FreeCAD.Console.PrintMessage(
                f"[InteractiveRoute] Routing on '{source_name}' ({kind} surface) — "
                f"{len(self.field)} copper outline(s), width {self.width} mm, "
                f"clearance {self.clearance} mm.\n"
                "  Click a start point, then move the mouse. Click to fix a "
                "segment.  '/' flips the corner, Backspace undoes, Esc finishes.\n"
            )

        # ── surface selection ────────────────────────────────────────────

        def _pick_surface(self):
            """
            Routing surface from the current selection — any face of any body,
            flat or curved, at any orientation. Selecting a whole object falls
            back to its top face (see _top_face_of).
            """
            try:
                sel = FreeCADGui.Selection.getSelectionEx()
            except Exception:
                sel = []
            for s in sel:
                obj = s.Object
                if not hasattr(obj, "Shape"):
                    continue
                for sub in (s.SubElementNames or []):
                    if sub.startswith("Face"):
                        try:
                            idx = int(sub[4:]) - 1
                            self.face_index = idx
                            return obj.Shape.Faces[idx], obj.Name
                        except Exception:
                            pass
                f = _top_face_of(obj)
                if f is not None:
                    try:
                        self.face_index = list(obj.Shape.Faces).index(f)
                    except Exception:
                        self.face_index = 0
                    return f, obj.Name
            return None, None

        # ── event loop ───────────────────────────────────────────────────

        def action(self, arg):
            if arg["Type"] == "SoKeyboardEvent":
                key = arg.get("Key", "")
                if key == "ESCAPE":
                    self.finish()
                elif key == "/":
                    self.flip = not self.flip
                    self._recompute_head(self.point)
                elif key == "BACKSPACE":
                    self._undo_segment()
                return

            if arg["Type"] == "SoLocation2Event":
                self.point = self._cursor_2d(arg)
                if self.point is not None:
                    self._recompute_head(self.point)
                gui_tool_utils.redraw3DView()
                return

            if (arg["Type"] == "SoMouseButtonEvent"
                    and arg["State"] == "DOWN" and arg["Button"] == "BUTTON1"):
                if self.point is None:
                    return
                if not self.nodes:
                    self.nodes = [self.point]
                    return
                if self.blocked or len(self.head) < 2:
                    FreeCAD.Console.PrintWarning(
                        "[InteractiveRoute] No way through to the cursor — move it "
                        "somewhere else, or press '/' to flip the corner.\n"
                    )
                    return
                self.nodes = _append_head(self.nodes, self.head)
                self._recompute_head(self.point)

        # ── cursor ───────────────────────────────────────────────────────

        def _cursor_2d(self, arg):
            """
            Cursor position as 2-D routing-frame coordinates.

            Prefers the real 3-D point under the cursor (view.getObjectInfo),
            because on a curved surface Draft's snapped point lies on a FLAT
            working plane and would not correspond to any point on the face.
            Falls back to the snapped working-plane point, which is correct
            for a flat routing surface and keeps pad snapping working there.
            """
            near = self.nodes[-1] if self.nodes else None
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

        # ── head maintenance ─────────────────────────────────────────────

        def _recompute_head(self, cursor2d):
            """Route from the last fixed node to the cursor, entirely in the
            frame's 2-D space; the surface mapping happens only when the
            result is drawn."""
            if cursor2d is None or not self.nodes:
                return
            a = FreeCAD.Vector(self.nodes[-1][0], self.nodes[-1][1], 0.0)
            c = FreeCAD.Vector(cursor2d[0], cursor2d[1], 0.0)
            path, used_flip = walkaround.route_head(
                a, c, self.field, step_deg=self.step_deg, flip=self.flip)
            if path is None:
                # Show where the trace would go, in the "blocked" colour, so
                # the user can see what is refusing rather than nothing at all.
                self.blocked = True
                path = walkaround.posture_points(a, c, self.step_deg, self.flip)
            else:
                self.blocked = False
                self.flip = used_flip
            self.head = [(p.x, p.y) for p in path]
            self._update_trackers()

        def _update_trackers(self):
            pts2 = _append_head(self.nodes, self.head) if self.head else list(self.nodes)
            if len(pts2) < 2:
                self.wire.off()
                self.box.off()
                return
            pts3 = self.frame.path_to_3d(pts2, max_step_mm=self._preview_step())
            if len(pts3) < 2:
                self.wire.off()
                self.box.off()
                return
            self.wire.setColor(_BLOCKED_COLOR if self.blocked else _TRACE_COLOR)
            self.wire.line.numVertices.setValue(len(pts3))
            self.wire.updateFromPointlist(pts3)
            self.wire.on()
            if self.frame.is_planar:
                try:
                    self.box.update(line=[pts3[-2], pts3[-1]],
                                    normal=self.frame.normal_at(*pts2[-1]))
                    self.box.on()
                except Exception:
                    self.box.off()
            else:
                # A single straight box cannot represent a trace that follows
                # curvature; the width-accurate solid is built on commit.
                self.box.off()

        def _preview_step(self):
            """Densification step for the preview — fine enough to look like
            it follows the surface, coarse enough to stay responsive."""
            return 1.0 if not self.frame.is_planar else 1e9

        def _undo_segment(self):
            if len(self.nodes) > 1:
                self.nodes.pop()
            elif self.nodes:
                self.nodes = []
                self.head = []
            self._recompute_head(self.point)

        # ── finish ───────────────────────────────────────────────────────

        def finish(self, cont=False):
            # finish() also runs on the early-bail paths in Activated() (no
            # document, no routing surface), where the event callback was
            # never installed and there may be no 3-D view at all. Draft's
            # end_callbacks only guards RuntimeError, so calling it blindly
            # raises AttributeError on a None view and masks the real reason
            # the tool refused to start.
            if getattr(self, "call", None) is not None and getattr(self, "view", None) is not None:
                try:
                    self.end_callbacks(self.call)
                except Exception:
                    pass
            self.call = None
            for t in (getattr(self, "wire", None), getattr(self, "box", None)):
                if t is not None:
                    try:
                        t.finalize()
                    except Exception:
                        pass
            nodes = list(getattr(self, "nodes", []))
            if len(nodes) >= 2:
                # Document changes must run outside the Coin callback. The
                # face is referenced by (object, face index) so the commit can
                # rebuild the same routing frame it was drawn in.
                self.commit("Interactive Route",
                            _build_commands(self.source_name, self.face_index,
                                             nodes, self.width, self.thickness,
                                             self.clearance))
            super().finish()

    return _Tool()


def _two_point_wire(z):
    """A throwaway 2-point wire — wireTracker's constructor needs one."""
    import Part
    return Part.makePolygon([FreeCAD.Vector(0, 0, z), FreeCAD.Vector(1, 0, z)])


def _append_head(nodes, head):
    """Fixed nodes plus the live head, whose first point duplicates the last
    fixed node. Both are 2-D routing-frame coordinates."""
    if not nodes:
        return list(head)
    if not head:
        return list(nodes)
    return list(nodes) + list(head[1:])


def _build_commands(obj_name, face_index, nodes2d, width, thickness, clearance):
    """Deferred document work — a list of statements Draft's ToDo runs after
    the event callbacks are gone (see Creator.commit)."""
    pts = [(round(x, 6), round(y, 6)) for x, y in nodes2d]
    return [
        "import FreeCAD",
        "from routing.InteractiveRouterCommand import bake_trace",
        f"bake_trace({obj_name!r}, {face_index!r}, {pts!r}, "
        f"{width!r}, {thickness!r}, {clearance!r})",
    ]


def _clean_pts2d(pts2d):
    """Drop numerically-degenerate points: the walk-around's posture math can
    emit consecutive points separated only by floating-point noise (well
    below a nanometre), and OCCT's solid builders reject those outright —
    the loft with "insufficient separation", the per-segment box fallback
    with "length of box too small" (both observed in a randomized stress
    test). Shared by bake_trace and rebuild_trace so every caller
    (interactive, batch, drag-edit) is covered."""
    if not pts2d:
        return []
    cleaned = [tuple(pts2d[0])]
    for p in pts2d[1:]:
        q = tuple(p)
        if abs(q[0] - cleaned[-1][0]) > 1e-6 or abs(q[1] - cleaned[-1][1]) > 1e-6:
            cleaned.append(q)
    return cleaned


def _update_ratsnest(doc):
    """Redraw the rubber lines, if this document has any: the connection just
    routed is one the ratsnest no longer has to show (see core.ratsnest)."""
    try:
        from core import ratsnest
        ratsnest.refresh_if_present(doc)
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[InteractiveRoute] the ratsnest was not updated: {exc}\n")


def bake_trace(obj_name, face_index, pts2d, width_mm, thickness_mm, clearance_mm):
    """
    Create the real Trace_NNN solid. Called from the deferred commit, so it
    runs outside the Coin event callback.

    *pts2d* are routing-frame coordinates on face *face_index* of *obj_name*;
    the frame is rebuilt here so the baked solid follows the same surface the
    trace was drawn on — flat or curved. Property schema matches the grid
    router's, so both tools produce interchangeable objects.
    """
    doc = FreeCAD.activeDocument()
    if doc is None or len(pts2d) < 2:
        return None
    pts2d = _clean_pts2d(pts2d)
    if len(pts2d) < 2:
        return None
    src = doc.getObject(obj_name)
    if src is None:
        FreeCAD.Console.PrintError(
            f"[InteractiveRoute] '{obj_name}' is gone; cannot build the trace.\n")
        return None
    try:
        face = src.Shape.Faces[face_index]
    except Exception:
        FreeCAD.Console.PrintError(
            f"[InteractiveRoute] Face {face_index} of '{obj_name}' is gone; "
            "cannot build the trace.\n")
        return None

    frame = routing_frame.SurfaceFrame(face)
    shape = routing_frame.build_surface_trace_solid(
        frame, [tuple(p) for p in pts2d], width_mm, thickness_mm)
    waypoints = frame.path_to_3d([tuple(p) for p in pts2d])

    idx = _next_trace_index(doc)
    obj = doc.addObject("Part::Feature", f"Trace_{idx:03d}")
    obj.Shape = shape

    obj.addProperty("App::PropertyBool", "IsRoutingTrace", "Routing",
                     "Marks this object as a routed conductor trace")
    obj.addProperty("App::PropertyLength", "TraceWidth", "Routing", "Trace width")
    obj.addProperty("App::PropertyLength", "TraceThickness", "Routing", "Trace thickness")
    obj.addProperty("App::PropertyLength", "Clearance", "Routing",
                     "Obstacle clearance used when routing this trace")
    obj.addProperty("App::PropertyVectorList", "Waypoints", "Routing",
                     "Trace polyline waypoints")
    obj.addProperty("App::PropertyString", "NetName", "Routing", "Net identifier")
    # Which surface this trace was routed on — needed to REBUILD the trace
    # later (drag-edit, rip-up-and-reroute): the SurfaceFrame is transient,
    # so without these two properties there is no way back from a baked
    # trace to the face its 2-D geometry lives on.
    obj.addProperty("App::PropertyString", "SourceObject", "Routing",
                     "Object whose face this trace was routed on")
    obj.addProperty("App::PropertyInteger", "SourceFaceIndex", "Routing",
                     "Index of the face this trace was routed on")

    obj.IsRoutingTrace   = True
    obj.TraceWidth       = width_mm
    obj.TraceThickness   = thickness_mm
    obj.Clearance        = clearance_mm
    obj.Waypoints        = waypoints
    obj.NetName          = f"Net_{idx:03d}"
    obj.SourceObject     = obj_name
    obj.SourceFaceIndex  = int(face_index)

    if FreeCAD.GuiUp:
        obj.ViewObject.ShapeColor = _TRACE_COLOR
        obj.ViewObject.LineColor  = _TRACE_COLOR

    _traces_group(doc).addObject(obj)
    doc.recompute()
    _update_ratsnest(doc)
    FreeCAD.Console.PrintMessage(
        f"[InteractiveRoute] Created {obj.Name} with {len(waypoints)} waypoint(s).\n"
    )
    return obj


def bake_body_trace(obj_name, segments, width_mm, thickness_mm, clearance_mm):
    """
    Create ONE Trace_NNN solid from a route that crosses several faces of a
    body — see core.body_routing.

    *segments* is [(face_index, [(u, v), ...]), ...] as that planner returns
    it: one 2-D path per face, each in its own face's frame. Each is built
    into a solid on its own surface and the pieces are fused, so the result
    is a single trace object that follows the body around its edges rather
    than a pile of disconnected per-face fragments.

    Falls back to a compound if the fuse fails — a trace that is visibly
    correct but not a single solid is far more useful than no trace at all,
    and the geometry is identical either way.
    """
    doc = FreeCAD.activeDocument()
    if doc is None or not segments:
        return None
    src = doc.getObject(obj_name)
    if src is None:
        FreeCAD.Console.PrintError(
            f"[BodyRoute] '{obj_name}' is gone; cannot build the trace.\n")
        return None

    solids = []
    waypoints = []
    legs = []
    skipped = 0
    for face_index, pts2d in segments:
        pts = _clean_pts2d(pts2d)
        if len(pts) < 2:
            # Never silent: a dropped segment is a VISIBLE GAP in the trace,
            # and "no extrusion appeared at that edge" is impossible to
            # diagnose from the model alone.
            FreeCAD.Console.PrintWarning(
                f"[BodyRoute] the hop across face {face_index} collapsed to a "
                "single point and produced no geometry — the trace will have "
                "a gap there.\n")
            skipped += 1
            continue
        try:
            face = src.Shape.Faces[face_index]
        except Exception:
            FreeCAD.Console.PrintError(
                f"[BodyRoute] Face {face_index} of '{obj_name}' is gone.\n")
            return None
        frame = routing_frame.SurfaceFrame(face)
        seg_pts = frame.path_to_3d(pts)
        seg_len = sum((b - a).Length for a, b in zip(seg_pts, seg_pts[1:]))
        # Short hops across sliver faces are KEPT: the whole route is lofted
        # as one solid (see below), where a short step is merely a short step
        # rather than a degenerate piece the fuse would choke on.
        legs.append((frame, pts))
        try:
            solids.append(routing_frame.build_surface_trace_solid(
                frame, pts, width_mm, thickness_mm))
        except Exception as exc:
            # Only the FALLBACK path needs this; a failure here is not fatal.
            FreeCAD.Console.PrintWarning(
                f"[BodyRoute] per-face fallback geometry for face "
                f"{face_index} could not be built: {exc}\n")
        # The hand-over point is shared by two consecutive segments; keeping
        # it once leaves a clean polyline in Waypoints.
        if waypoints and seg_pts and (seg_pts[0] - waypoints[-1]).Length < 1e-6:
            seg_pts = seg_pts[1:]
        waypoints.extend(seg_pts)

    if not legs:
        return None

    # One loft for the whole route — a single connected solid by
    # construction, which fusing per-face pieces demonstrably is not.
    shape = None
    try:
        candidate = routing_frame.build_multiface_trace_solid(
            legs, width_mm, thickness_mm)
        if candidate is not None and not candidate.isNull() and candidate.isValid():
            shape = candidate
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[BodyRoute] single-piece loft failed ({exc}); falling back to "
            "fusing the per-face segments.\n")

    if shape is None:
        if not solids:
            return None
        shape = solids[0]
        if len(solids) > 1:
            try:
                shape = shape.multiFuse(solids[1:])
                # removeSplitter RETURNS the cleaned shape rather than
                # modifying in place; take the result, and only when it is
                # still valid.
                try:
                    cleaned = shape.removeSplitter()
                    if cleaned is not None and cleaned.isValid() and cleaned.Solids:
                        shape = cleaned
                except Exception:
                    pass
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"[BodyRoute] could not fuse the segments ({exc}); "
                    "keeping them as a compound.\n")
                shape = Part.makeCompound(solids)
        if not shape.isValid():
            shape = Part.makeCompound(solids)

    idx = _next_trace_index(doc)
    obj = doc.addObject("Part::Feature", f"Trace_{idx:03d}")
    obj.Shape = shape

    obj.addProperty("App::PropertyBool", "IsRoutingTrace", "Routing",
                     "Marks this object as a routed conductor trace")
    obj.addProperty("App::PropertyLength", "TraceWidth", "Routing", "Trace width")
    obj.addProperty("App::PropertyLength", "TraceThickness", "Routing", "Trace thickness")
    obj.addProperty("App::PropertyLength", "Clearance", "Routing",
                     "Obstacle clearance used when routing this trace")
    obj.addProperty("App::PropertyVectorList", "Waypoints", "Routing",
                     "Trace polyline waypoints")
    obj.addProperty("App::PropertyString", "NetName", "Routing", "Net identifier")
    obj.addProperty("App::PropertyString", "SourceObject", "Routing",
                     "Object whose face(s) this trace was routed on")
    obj.addProperty("App::PropertyInteger", "SourceFaceIndex", "Routing",
                     "Index of the face this trace starts on")
    obj.addProperty("App::PropertyIntegerList", "SourceFaceIndices", "Routing",
                     "Every face this trace crosses, in order")

    obj.IsRoutingTrace     = True
    obj.TraceWidth         = width_mm
    obj.TraceThickness     = thickness_mm
    obj.Clearance          = clearance_mm
    obj.Waypoints          = waypoints
    obj.NetName            = f"Net_{idx:03d}"
    obj.SourceObject       = obj_name
    obj.SourceFaceIndex    = int(segments[0][0])
    obj.SourceFaceIndices  = [int(f) for f, _p in segments]

    if FreeCAD.GuiUp and obj.ViewObject is not None:
        obj.ViewObject.ShapeColor = _TRACE_COLOR
        obj.ViewObject.LineColor  = _TRACE_COLOR

    _traces_group(doc).addObject(obj)
    doc.recompute()
    _update_ratsnest(doc)

    # Say plainly whether the trace came out in one piece. A trace that is
    # broken at an edge looks almost right in the 3-D view — the gap is a
    # fraction of a millimetre — so without this it is easy to believe the
    # route succeeded when part of it is missing.
    pieces = len(obj.Shape.Solids)
    if pieces > 1:
        FreeCAD.Console.PrintWarning(
            f"[BodyRoute] {obj.Name} came out as {pieces} separate pieces "
            "rather than one connected trace — it is broken at one or more "
            "of the edges it crosses.\n")
    note = f", {skipped} segment(s) left out" if skipped else ""
    FreeCAD.Console.PrintMessage(
        f"[BodyRoute] Created {obj.Name} across {len(segments)} face(s) "
        f"with {len(waypoints)} waypoint(s){note}"
        f"{'' if pieces == 1 else f' in {pieces} pieces'}.\n")
    return obj


def resolve_trace_frame(doc, trace_obj):
    """
    (frame, obj_name, face_index) for an existing Trace_NNN, or
    (None, None, None).

    Uses the SourceObject/SourceFaceIndex properties bake_trace persists;
    traces baked before those properties existed are resolved by scanning
    the document for the face whose surface actually contains the trace's
    first waypoint (nearest planar-or-not face within half the trace
    thickness plus a small tolerance).
    """
    src_name = getattr(trace_obj, "SourceObject", "") or ""
    src_idx = getattr(trace_obj, "SourceFaceIndex", None)
    if src_name and src_idx is not None:
        src = doc.getObject(src_name)
        if src is not None:
            try:
                face = src.Shape.Faces[int(src_idx)]
                return routing_frame.SurfaceFrame(face), src_name, int(src_idx)
            except Exception:
                pass

    wp = list(getattr(trace_obj, "Waypoints", []) or [])
    if not wp:
        return None, None, None
    p0 = wp[0]
    tol = float(getattr(trace_obj, "TraceThickness", 0.1)) + 0.1
    best = (None, None, None)
    best_d = tol
    for o in doc.Objects:
        if o is trace_obj or not hasattr(o, "Shape"):
            continue
        if getattr(o, "IsRoutingTrace", False) or getattr(o, "IsContactPoint", False):
            continue
        shp = o.Shape
        if shp.isNull():
            continue
        try:
            for i, f in enumerate(shp.Faces):
                d = f.distToShape(Part.Vertex(p0))[0]
                if d < best_d:
                    best_d = d
                    best = (routing_frame.SurfaceFrame(f), o.Name, i)
        except Exception:
            continue
    return best


def rebuild_trace(trace_name, pts2d):
    """
    Re-shape an existing Trace_NNN in place along a new 2-D path on ITS OWN
    routing surface — the commit step of drag-editing and of
    rip-up-and-reroute. Keeps the object's identity (name, net, width,
    colours); only Shape and Waypoints change. Returns the object or None.
    """
    doc = FreeCAD.activeDocument()
    if doc is None:
        return None
    obj = doc.getObject(trace_name)
    if obj is None or not getattr(obj, "IsRoutingTrace", False):
        return None
    pts2d = _clean_pts2d(pts2d)
    if len(pts2d) < 2:
        return None
    frame, _src, _idx = resolve_trace_frame(doc, obj)
    if frame is None:
        FreeCAD.Console.PrintError(
            f"[InteractiveRoute] Cannot resolve the routing surface of "
            f"{trace_name}; not rebuilding it.\n")
        return None
    width = float(getattr(obj, "TraceWidth", 0.3))
    thickness = float(getattr(obj, "TraceThickness", 0.035))
    try:
        shape = routing_frame.build_surface_trace_solid(frame, pts2d, width, thickness)
    except Exception as exc:
        FreeCAD.Console.PrintError(
            f"[InteractiveRoute] Rebuilding {trace_name} failed: {exc}\n")
        return None
    obj.Shape = shape
    obj.Waypoints = frame.path_to_3d(pts2d)
    doc.recompute()
    return obj


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("InteractiveRouteCommand", InteractiveRouteCommand())
