# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Trace Routing — persistent session controller.

Modeled on wirebond.ManualWireBonding's session-singleton pattern
(FreeCADGui.Selection.addObserver + a SelectionGate restricting clicks to
the session's own marker objects, a contextual toolbar shown only while
active), with one structural addition wire bonding didn't need: per-TRACE
rollback, borrowed from
wirebond.SetContactPointsOnFaceCommand._GridContactPanel's provisional-
object-list pattern (track objects created since the last confirm, delete
them all on abort) — applied at trace granularity instead of whole-session
granularity, since "Abort" here must discard only the in-progress trace,
not traces already confirmed earlier in the same session.

Two-level state:
  * SESSION  (is_active / doc / params / a core.trace_routing.VisibilityGraph
              built once at session start over the sampled grid + initial
              obstacles, growing by one obstacle rect per confirmed trace)
  * TRACE    (_TraceState.AWAIT_START / ROUTING, the accumulating waypoint
              list, and the provisional preview-leg object names)

Each click on a grid point either sets the trace's start point (AWAIT_START)
or auto-routes a leg from the last waypoint to the clicked point (ROUTING),
building a provisional preview solid immediately so the user sees the trace
take shape leg by leg. confirm_trace() bakes ONE final solid from the full
accumulated waypoint list, keeps the session active, and folds the new
trace into the obstacle set. abort_trace() discards only the provisional
legs of the current trace. end_session() tears the whole session down,
auto-aborting any unconfirmed trace first.
"""

import FreeCAD
import FreeCADGui
import Part

import core.trace_routing as trace_routing


# ── selection gate ──────────────────────────────────────────────────────────

def _resolve_doc(doc):
    return FreeCAD.getDocument(doc) if isinstance(doc, str) else doc


def _resolve_obj(doc, obj_name):
    d = _resolve_doc(doc)
    return d.getObject(obj_name) if d is not None else None


class _RoutePointGate:
    """
    FreeCAD SelectionGate allowing this session's grid-point cloud
    (IsRoutingGridPoint=True) AND the routing surface object(s) — mirrors
    wirebond.ManualWireBonding._ContactPointGate, but deliberately wider:
    hitting one dot out of a couple of thousand is fiddly, so clicking the
    surface anywhere is accepted and snapped to the nearest grid point (see
    TraceRoutingSession._resolve_clicked_point).
    """

    def __init__(self, surface_names=()):
        self._surface_names = set(surface_names or ())

    def allow(self, doc, obj, sub) -> bool:
        try:
            fc_obj = obj if not isinstance(obj, str) else _resolve_obj(doc, obj)
            if fc_obj is None:
                return False
            return (bool(getattr(fc_obj, "IsRoutingGridPoint", False))
                    or fc_obj.Name in self._surface_names)
        except Exception:
            return False


# ── contextual toolbar visibility ──────────────────────────────────────────────

def _set_session_toolbar_visible(visible: bool) -> None:
    try:
        from compat import QtWidgets as _QW
        mw = FreeCADGui.getMainWindow()
        for tb in mw.findChildren(_QW.QToolBar):
            if tb.windowTitle() == "Trace Routing Session":
                tb.setVisible(visible)
                break
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[TraceRouting] toolbar visibility: {exc}\n")


# ── trace-level state ───────────────────────────────────────────────────────

class _TraceState:
    AWAIT_START = "await_start"
    ROUTING     = "routing"


# ── document object helpers ─────────────────────────────────────────────────

_GRID_COLOR    = (0.55, 0.55, 0.55)   # grey  — candidate grid point
_PREVIEW_COLOR = (1.00, 0.65, 0.00)   # orange — in-progress leg preview
_TRACE_COLOR   = (0.85, 0.55, 0.20)   # copper — confirmed trace


def _next_trace_index(doc) -> int:
    existing = [o for o in doc.Objects if o.Name.startswith("Trace_")]
    return len(existing) + 1


def _add_to_traces_group(doc, obj) -> None:
    """Add *obj* to the document-level Traces group, creating it if absent
    — mirrors wirebond.SetContactPointsOnFaceCommand._add_to_contact_points_group."""
    try:
        grp = next((o for o in doc.Objects if o.Name == "Traces"), None)
        if grp is None:
            grp = doc.addObject("App::DocumentObjectGroup", "Traces")
            grp.Label = "Traces"
        grp.addObject(obj)
    except Exception:
        pass


# ── session controller ──────────────────────────────────────────────────────

class TraceRoutingSession:

    def __init__(self):
        self.is_active = False
        self.doc = None
        self.params = {}
        self._graph = None
        self._trace_state = _TraceState.AWAIT_START
        self._waypoints = []
        self._leg_preview_names = []
        self._grid_names = []
        self._surface_names = set()
        self._status = ""

    # ── session lifecycle ────────────────────────────────────────────────

    def start_routing_session(self, doc, grid_points, surface_names, params,
                              surface_faces=None) -> bool:
        """
        Sample-grid markers + obstacle collection + visibility graph are all
        built HERE (not by the setup panel) so this class owns the full
        lifecycle of everything it creates, including cleanup on abort/end.

        *surface_faces* are the Part.Face objects the grid was sampled from;
        their HOLES become keep-outs too (see trace_routing.face_hole_rects)
        — grid points are never placed inside a cutout, but without this a
        straight edge between two points on opposite sides of one would run
        right across it.
        """
        if self.is_active:
            self.end_session()

        if not grid_points:
            FreeCAD.Console.PrintWarning("[TraceRouting] No grid points to route on.\n")
            return False

        self.doc = doc
        self.params = dict(params)
        self._trace_state = _TraceState.AWAIT_START
        self._waypoints = []
        self._leg_preview_names = []
        self._grid_names = []
        self._surface_names = set(surface_names or ())

        spacing_mm   = self.params["spacing_mm"]
        width_mm     = self.params["width_mm"]
        thickness_mm = self.params["thickness_mm"]
        clearance_mm = self.params["clearance_mm"]

        zs = [p.z for p in grid_points]
        z_band_min = min(zs) - thickness_mm - clearance_mm
        z_band_max = max(zs) + thickness_mm + clearance_mm
        expand_mm  = clearance_mm + width_mm / 2.0

        obstacles = trace_routing.collect_obstacles(
            doc, exclude_names=set(surface_names),
            z_min=z_band_min, z_max=z_band_max, expand_mm=expand_mm,
        )
        n_body = len(obstacles)
        for face in (surface_faces or ()):
            obstacles.extend(trace_routing.face_hole_rects(face, expand_mm))
        n_holes = len(obstacles) - n_body

        neighbor_radius = trace_routing.DEFAULT_NEIGHBOR_RADIUS_MULTIPLIER * spacing_mm
        self._graph = trace_routing.build_visibility_graph(
            list(grid_points), obstacles, neighbor_radius, spacing_mm
        )

        doc.openTransaction("Trace Routing: Generate Grid")
        try:
            grid_obj = self._make_grid_cloud(doc, grid_points)
            self._grid_names = [grid_obj.Name]
        finally:
            doc.commitTransaction()
        doc.recompute()

        self.is_active = True
        FreeCADGui.Selection.addObserver(self)
        FreeCADGui.Selection.addSelectionGate(_RoutePointGate(self._surface_names))
        _set_session_toolbar_visible(True)
        self._set_status("Trace routing — click a grid point to start a trace")
        step = self.params.get("heading_step_deg", 0.0)
        pref_txt = f"{step:g}° grid" if step else "any angle"
        FreeCAD.Console.PrintMessage(
            "Trace routing session started.\n"
            f"  {len(grid_points)} grid point(s) — spacing={spacing_mm}mm, "
            f"width={width_mm}mm, thickness={thickness_mm}mm, "
            f"clearance={clearance_mm}mm, max bend={self.params['max_bend_deg']} deg.\n"
            f"  {n_body} body keep-out(s) + {n_holes} surface-cutout keep-out(s).\n"
            f"  preferred trace angles: {pref_txt}"
            f" | corner radius: {self.params.get('corner_radius_mm', 0.0):g} mm\n"
            "  Click a grid point (or anywhere on the routing surface — the "
            "nearest grid point is used) to set the trace start, click again "
            "to route the next leg.\n"
            "  Use 'Confirm Trace' / 'Abort Trace' / 'End Routing Session' from "
            "the contextual toolbar as needed.\n"
        )
        return True

    def confirm_trace(self) -> None:
        if not self.is_active:
            return
        if self._trace_state != _TraceState.ROUTING or len(self._waypoints) < 2:
            FreeCAD.Console.PrintWarning("[TraceRouting] No in-progress trace to confirm.\n")
            return

        doc = self.doc
        waypoints = list(self._waypoints)
        doc.openTransaction("Trace Routing: Confirm Trace")
        try:
            solid = trace_routing.build_trace_solid(
                waypoints, self.params["width_mm"], self.params["thickness_mm"],
                corner_radius_mm=self.params.get("corner_radius_mm", 0.0),
            )
            trace_obj = self._bake_trace(doc, solid)
            self._remove_objects(self._leg_preview_names)
        finally:
            doc.commitTransaction()
        doc.recompute()

        self._leg_preview_names = []
        # One keep-out per SEGMENT, not one for the whole trace: a trace's
        # overall bounding box covers the entire rectangle it spans, so an
        # L-shaped or diagonal trace would wrongly block a large empty area
        # (and, being so coarse, would also mis-describe where the copper
        # actually is) for every later trace in this session.
        expand = self.params["clearance_mm"] + self.params["width_mm"] / 2.0
        for a, b in zip(waypoints, waypoints[1:]):
            self._graph.add_obstacle(trace_routing.Rect(
                min(a.x, b.x) - expand, min(a.y, b.y) - expand,
                max(a.x, b.x) + expand, max(a.y, b.y) + expand,
            ))
        self._waypoints = []
        self._trace_state = _TraceState.AWAIT_START
        self._set_status("Trace confirmed. Click a grid point to start the next trace.")
        FreeCAD.Console.PrintMessage(f"[TraceRouting] Confirmed {trace_obj.Name}.\n")

    def abort_trace(self) -> None:
        if not self.is_active:
            return
        if not self._leg_preview_names and not self._waypoints:
            FreeCAD.Console.PrintMessage("[TraceRouting] Nothing to abort.\n")
            return

        doc = self.doc
        if doc is not None and self._leg_preview_names:
            doc.openTransaction("Trace Routing: Abort Trace")
            self._remove_objects(self._leg_preview_names)
            doc.commitTransaction()
            doc.recompute()

        self._leg_preview_names = []
        self._waypoints = []
        self._trace_state = _TraceState.AWAIT_START
        self._set_status("Trace aborted. Click a grid point to start a new trace.")

    def end_session(self) -> None:
        if not self.is_active:
            return
        if self._trace_state == _TraceState.ROUTING and self._waypoints:
            FreeCAD.Console.PrintWarning(
                "[TraceRouting] Ending session with an unconfirmed trace in "
                "progress — discarding it.\n"
            )
            self.abort_trace()
        self._teardown()
        FreeCAD.Console.PrintMessage("[TraceRouting] Routing session ended.\n")

    def _teardown(self) -> None:
        self.is_active = False
        self._trace_state = _TraceState.AWAIT_START
        try:
            FreeCADGui.Selection.removeSelectionGate()
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[TraceRouting] removeSelectionGate: {exc}\n")
        try:
            FreeCADGui.Selection.removeObserver(self)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[TraceRouting] removeObserver: {exc}\n")

        doc = self.doc
        if doc is not None:
            self._remove_objects(self._grid_names)
        self._grid_names = []
        self._waypoints = []
        self._leg_preview_names = []
        self._graph = None
        self._set_status("")
        _set_session_toolbar_visible(False)
        if doc is not None:
            try:
                doc.recompute()
            except Exception:
                pass

    # ── FreeCAD Selection observer callbacks ───────────────────────────────

    def addSelection(self, doc, obj_name, sub, pos):
        if not self.is_active:
            return
        obj = _resolve_obj(doc, obj_name)
        if obj is None:
            FreeCADGui.Selection.clearSelection()
            return
        is_grid    = bool(getattr(obj, "IsRoutingGridPoint", False))
        is_surface = obj.Name in self._surface_names
        if not (is_grid or is_surface):
            FreeCADGui.Selection.clearSelection()
            self._set_status(
                "Trace routing — click a grid point or the routing surface"
            )
            return

        pt = self._resolve_clicked_point(obj, sub, pos, is_grid)
        if pt is None:
            FreeCADGui.Selection.clearSelection()
            return

        if self._trace_state == _TraceState.AWAIT_START:
            self._waypoints = [pt]
            self._trace_state = _TraceState.ROUTING
            self._set_status(
                f"Trace start set at ({pt.x:.3f}, {pt.y:.3f}, {pt.z:.3f}) — "
                "click the next waypoint"
            )
        else:
            self._route_next_leg(pt)

        FreeCADGui.Selection.clearSelection()

    def _resolve_clicked_point(self, obj, sub, pos, is_grid: bool):
        """
        Resolve the grid point the user meant.

        Clicking an individual dot in a 2000+ point cloud is fiddly, so a
        click ANYWHERE on the routing surface is accepted too and snapped to
        the nearest grid point (_nearest_grid_point). When a grid vertex was
        hit directly, *sub* (e.g. "Vertex42") identifies which one —
        resolved via Shape.getElement(sub), the same convention
        gds.ChipTransformCommand._target_xy_pick uses for vertex
        sub-elements, rather than assuming index 0 as a one-point-per-object
        design would.
        """
        if is_grid:
            try:
                if sub:
                    elem = obj.Shape.getElement(sub)
                    if hasattr(elem, "Point"):
                        p = elem.Point
                        return FreeCAD.Vector(p.x, p.y, p.z)
            except Exception:
                pass
        if pos:
            try:
                raw = FreeCAD.Vector(pos[0], pos[1], pos[2])
            except Exception:
                return None
            snapped = self._nearest_grid_point(raw)
            return snapped if snapped is not None else (None if not is_grid else raw)
        return None

    def _nearest_grid_point(self, pt):
        """Closest sampled grid point to *pt* in XY, or None if the click
        landed too far from the grid to be meaningful. Uses the graph's
        existing spatial hash, widening the search ring until something is
        found, so this stays cheap on a 2000+ point grid."""
        g = self._graph
        if g is None or not g.points:
            return None
        cell = g.cell_size
        cx, cy = trace_routing._cell_of(pt.x, pt.y, cell)
        best = None
        best_d2 = None
        for ring in range(0, 6):
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    if ring > 0 and abs(dx) != ring and abs(dy) != ring:
                        continue          # only the newly-added ring
                    for i in g._point_index.get((cx + dx, cy + dy), ()):
                        p = g.points[i]
                        d2 = (p.x - pt.x) ** 2 + (p.y - pt.y) ** 2
                        if best_d2 is None or d2 < best_d2:
                            best_d2 = d2
                            best = p
            if best is not None:
                break
        return best

    def _escape_mm(self) -> float:
        """How far a trace may run inside the copper/pad it starts or ends on
        (see trace_routing.VisibilityGraph.clear_with_escape). User-set when
        provided; otherwise a board-scaled default big enough to step off a
        pad but far too small to traverse a copper pour."""
        v = self.params.get("escape_mm")
        if v:
            return float(v)
        return max(2.0 * self.params.get("spacing_mm", 0.5),
                   self.params.get("width_mm", 0.3) + 2.0 * self.params.get("clearance_mm", 0.2))

    def _route_next_leg(self, pt) -> None:
        last = self._waypoints[-1]
        if (pt - last).Length < 1e-6:
            self._set_status("Trace routing — click a different point to continue")
            return

        leg = trace_routing.route_leg(
            self._graph, last, pt, self.params["max_bend_deg"],
            heading_step_deg=self.params.get("heading_step_deg", 0.0),
            heading_penalty_mm_per_deg=self.params.get("heading_penalty_mm_per_deg", 0.0),
            escape_mm=self._escape_mm(),
        )
        if leg is None:
            self._set_status(
                "No path found for that leg — try a smaller clearance, a looser "
                "max bend angle, or a start/end point clear of existing copper"
            )
            FreeCAD.Console.PrintWarning("[TraceRouting] No path found for this leg.\n")
            return

        new_pts = leg[1:]   # leg[0] duplicates the already-accumulated `last`
        candidate = self._waypoints + new_pts

        # Rebuild the preview as ONE solid over the WHOLE trace so far, not
        # one solid per leg. Per-leg solids meet at sharp butt joints, which
        # leaves visible notches at every corner, and — because a single leg
        # is usually just two points — no corner ever existed for the corner
        # radius to round, so that option looked like it did nothing until
        # the trace was confirmed. One solid means the preview is exactly
        # what Confirm will bake.
        try:
            solid = trace_routing.build_trace_solid(
                candidate, self.params["width_mm"], self.params["thickness_mm"],
                corner_radius_mm=self.params.get("corner_radius_mm", 0.0),
            )
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[TraceRouting] leg solid build failed: {exc}\n")
            return

        self.doc.openTransaction("Trace Routing: Add Leg")
        self._remove_objects(self._leg_preview_names)
        preview = self._bake_preview(solid)
        self.doc.commitTransaction()
        FreeCADGui.updateGui()

        self._leg_preview_names = [preview.Name]
        self._waypoints = candidate
        self._set_status(
            f"{len(self._waypoints)} waypoint(s) so far — click to continue, "
            "Confirm Trace to finish, or Abort Trace to discard"
        )

    def removeSelection(self, doc, obj_name, sub):
        pass

    def setSelection(self, doc):
        pass

    def clearSelection(self, doc):
        pass

    def setPreselection(self, doc, obj_name, sub):
        pass

    def removePreselection(self, doc, obj_name, sub):
        pass

    # ── document object helpers ─────────────────────────────────────────────

    def _make_grid_cloud(self, doc, points):
        """
        ONE Part::Feature holding every sampled grid point as a compound of
        vertices — NOT one object per point. The original design (modeled
        directly on wirebond.SetContactPointsOnFaceCommand's per-point
        GridPt objects) created one Part::Feature + ViewObject per sampled
        point, which is fine for that tool's typical use (a handful to a
        few hundred points on a single bond pad) but made gridding a real
        PCB face unusably slow — FreeCAD's per-object document/undo/
        ViewObject overhead dominates at thousands of objects, even though
        the underlying OCCT vertex sampling itself is fast. A single
        compound shape still supports per-vertex 3D-view picking
        (SubElementNames like "Vertex42", resolved in
        _resolve_clicked_point the same way
        gds.ChipTransformCommand._target_xy_pick resolves vertex
        sub-elements), so the click-to-route interaction is unchanged —
        only the document object COUNT drops from thousands to one.
        """
        obj = doc.addObject("Part::Feature", "RouteGrid")
        obj.Shape = Part.Compound([Part.Vertex(p.x, p.y, p.z) for p in points])
        obj.addProperty("App::PropertyBool", "IsRoutingGridPoint", "Routing",
                         "Trace-routing candidate grid (one compound object, many points)")
        obj.IsRoutingGridPoint = True
        if FreeCAD.GuiUp:
            # Deliberately large: these are click targets on a dense grid,
            # and clicking anywhere on the surface snaps to the nearest one
            # anyway (see _resolve_clicked_point).
            obj.ViewObject.PointSize   = 6
            obj.ViewObject.PointColor  = _GRID_COLOR
            obj.ViewObject.DisplayMode = "Points"
        return obj

    def _bake_preview(self, solid):
        # There is exactly one preview object at a time (rebuilt per leg over
        # the whole trace), but the counter keeps successive names distinct so
        # a stale object can never be silently reused.
        self._preview_seq = getattr(self, "_preview_seq", 0) + 1
        obj = self.doc.addObject("Part::Feature", f"TracePreview_{self._preview_seq:04d}")
        obj.Shape = solid
        if FreeCAD.GuiUp:
            obj.ViewObject.ShapeColor   = _PREVIEW_COLOR
            obj.ViewObject.Transparency = 20
        return obj

    def _bake_trace(self, doc, solid):
        idx = _next_trace_index(doc)
        obj = doc.addObject("Part::Feature", f"Trace_{idx:03d}")
        obj.Shape = solid

        obj.addProperty("App::PropertyBool", "IsRoutingTrace", "Routing",
                         "Marks this object as a routed conductor trace")
        obj.addProperty("App::PropertyLength", "TraceWidth", "Routing", "Trace width")
        obj.addProperty("App::PropertyLength", "TraceThickness", "Routing", "Trace thickness")
        obj.addProperty("App::PropertyLength", "Clearance", "Routing",
                         "Obstacle clearance used when routing this trace")
        obj.addProperty("App::PropertyAngle", "MaxBendAngle", "Routing",
                         "Max bend angle used when routing this trace")
        obj.addProperty("App::PropertyVectorList", "Waypoints", "Routing",
                         "Trace polyline waypoints")
        obj.addProperty("App::PropertyString", "NetName", "Routing", "Net identifier")

        obj.IsRoutingTrace  = True
        obj.TraceWidth      = self.params["width_mm"]
        obj.TraceThickness  = self.params["thickness_mm"]
        obj.Clearance       = self.params["clearance_mm"]
        obj.MaxBendAngle    = self.params["max_bend_deg"]
        obj.Waypoints       = list(self._waypoints)
        obj.NetName         = f"Net_{idx:03d}"

        if FreeCAD.GuiUp:
            obj.ViewObject.ShapeColor = _TRACE_COLOR
            obj.ViewObject.LineColor  = _TRACE_COLOR

        _add_to_traces_group(doc, obj)
        # A rubber line for this connection has just become unnecessary.
        try:
            from core import ratsnest
            ratsnest.refresh_if_present(doc)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[TraceRouting] the ratsnest was not updated: {exc}\n")
        return obj

    def _remove_objects(self, names) -> None:
        doc = self.doc
        if doc is None:
            return
        for n in names:
            try:
                doc.removeObject(n)
            except Exception:
                pass

    def _set_status(self, msg: str) -> None:
        self._status = msg
        try:
            FreeCADGui.getMainWindow().statusBar().showMessage(msg, 8000)
        except Exception:
            pass
        if msg:
            FreeCAD.Console.PrintMessage(f"[TraceRouting] {msg}\n")


# Singleton instance — persisted across command activations, mirrors
# wirebond.ManualWireBonding's module-level `manual_bonder`.
trace_router = TraceRoutingSession()
