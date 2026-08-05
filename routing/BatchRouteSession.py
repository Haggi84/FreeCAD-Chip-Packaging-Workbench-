# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Batch Auto-Route — persistent session controller.

There is no existing "these two pads should connect" data anywhere in this
codebase: NetName on Trace_NNN/BondWire_NNN is a bare auto-incrementing
label, and ContactPoint_NNN / contact_point_housing_NNN counters have no
guaranteed correspondence. So this session lets the user define pad-pairs
interactively by clicking two ContactPoint markers, QUEUES the pair instead
of acting on it immediately (unlike wirebond.ManualWireBonding, which places
a bond wire as soon as the second point of a pair is clicked), and routes
the whole queue in one pass when the user presses "Route All".

Modeled on wirebond.ManualWireBonding's click-pair state machine
(_State.AWAIT_FIRST/AWAIT_SECOND, a SelectionGate restricting clicks to
IsContactPoint markers) and routing.TraceRoutingSession's session-singleton
+ contextual-toolbar pattern, but built on the walk-around routing
primitive (core.trace_walkaround.route_head + core.trace_obstacles.
PolyField) rather than the grid + visibility-graph one, since interactive
and batch routing share that geometry model — see
routing/InteractiveRouterCommand.py, which this reuses bake_trace from.

Each pair in route_all() is: resolve both ContactPoint positions -> project
onto the routing frame -> route_head -> bake_trace -> fold the newly baked
trace's own outline into the live PolyField
(core.trace_obstacles.outline_polys_of_object_on_frame + PolyField.add) —
exactly the obstacle-growth step TraceRoutingSession.confirm_trace does for
its own Rect-based graph, so pair 2 in the same batch correctly routes
around pair 1's now-baked trace. A pair that cannot be routed is recorded as
blocked and the rest of the queue still runs — the point of reporting it is
so the user can route that one connection by hand afterward, not to abort
the whole batch.
"""

import FreeCAD
import FreeCADGui

import core.trace_walkaround as wa
import core.trace_obstacles as tob
import core.routing_frame as rf
from routing.InteractiveRouterCommand import bake_trace, rebuild_trace


# ── selection gate ──────────────────────────────────────────────────────────

def _resolve_doc(doc):
    return FreeCAD.getDocument(doc) if isinstance(doc, str) else doc


def _resolve_obj(doc, obj_name):
    d = _resolve_doc(doc)
    return d.getObject(obj_name) if d is not None else None


class _ContactPointGate:
    """FreeCAD SelectionGate allowing only IsContactPoint markers — the same
    restriction wirebond.ManualWireBonding._ContactPointGate applies,
    redefined locally rather than imported so this session does not take a
    dependency on the wirebond package."""

    def allow(self, doc, obj, sub) -> bool:
        try:
            fc_obj = obj if not isinstance(obj, str) else _resolve_obj(doc, obj)
            return fc_obj is not None and bool(getattr(fc_obj, "IsContactPoint", False))
        except Exception:
            return False


# ── contextual toolbar visibility ────────────────────────────────────────────

def _set_session_toolbar_visible(visible: bool) -> None:
    try:
        from compat import QtWidgets as _QW
        mw = FreeCADGui.getMainWindow()
        for tb in mw.findChildren(_QW.QToolBar):
            if tb.windowTitle() == "Batch Route Session":
                tb.setVisible(visible)
                break
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[BatchRoute] toolbar visibility: {exc}\n")


class _State:
    AWAIT_FIRST  = "await_first"
    AWAIT_SECOND = "await_second"


class BatchRouteSession:

    def __init__(self):
        self.is_active     = False
        self.doc            = None
        self.obj_name       = None
        self.face_index     = None
        self.frame           = None
        self.field           = None
        self.params          = {}
        self.queue           = []   # [(first_cp_name, second_cp_name), ...]
        self.last_results    = []   # [(first, second, "baked"/"blocked", trace_name_or_None), ...]
        self._state           = _State.AWAIT_FIRST
        self._first_cp        = None
        self._base_polys      = []   # non-trace copper + surface holes
        self._trace_polys     = {}   # trace name -> [Poly, ...] (spacing-inflated)
        self._boundary        = None
        # Optional callable(), invoked whenever the queue or status changes —
        # the setup panel hooks this to keep its queue list live while a
        # session is running, mirroring TraceRoutingSetupPanel's use of a
        # FreeCADGui.Selection observer to keep its own face count live.
        self.on_change = None

    # ── session lifecycle ────────────────────────────────────────────────

    def start_session(self, doc, obj_name, face_index, face, params) -> bool:
        if self.is_active:
            self.end_session()

        self.doc          = doc
        self.obj_name      = obj_name
        self.face_index    = face_index
        self.params        = dict(params)
        self.queue          = []
        self.last_results   = []
        self._state          = _State.AWAIT_FIRST
        self._first_cp       = None

        width_mm     = self.params["width_mm"]
        thickness_mm = self.params["thickness_mm"]
        clearance_mm = self.params["clearance_mm"]
        band = thickness_mm + clearance_mm

        self.frame = rf.SurfaceFrame(face)
        self._boundary = tob.face_outer_poly_on_frame(face, self.frame)

        # Existing traces are tracked SEPARATELY from other copper, each
        # under its own name — that is what makes rip-up-and-reroute
        # possible (rebuild the field without exactly one trace) and what
        # the trace-spacing option inflates (see _trace_obstacle_polys).
        trace_names = {o.Name for o in doc.Objects if getattr(o, "IsRoutingTrace", False)}
        self._base_polys = tob.collect_obstacle_polys_on_frame(
            doc, {obj_name} | trace_names, self.frame, band)
        self._base_polys += tob.face_hole_polys_on_frame(face, self.frame)

        self._trace_polys = {}
        for o in doc.Objects:
            if not getattr(o, "IsRoutingTrace", False):
                continue
            polys = self._trace_obstacle_polys(o)
            if polys:
                self._trace_polys[o.Name] = polys

        self._rebuild_field()

        self.is_active = True
        FreeCADGui.Selection.addObserver(self)
        FreeCADGui.Selection.addSelectionGate(_ContactPointGate())
        _set_session_toolbar_visible(True)
        self._notify()
        FreeCAD.Console.PrintMessage(
            "Batch route session started.\n"
            f"  routing surface: {obj_name} (face index {face_index})\n"
            f"  width={width_mm}mm thickness={thickness_mm}mm clearance={clearance_mm}mm\n"
            "  Click two ContactPoint markers to queue a pair, repeat as "
            "needed, then use Route All.\n"
        )
        return True

    def end_session(self) -> None:
        if not self.is_active:
            return
        self.is_active = False
        self._state     = _State.AWAIT_FIRST
        self._first_cp  = None
        try:
            FreeCADGui.Selection.removeSelectionGate()
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[BatchRoute] removeSelectionGate: {exc}\n")
        try:
            FreeCADGui.Selection.removeObserver(self)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[BatchRoute] removeObserver: {exc}\n")
        self.frame = None
        self.field = None
        self.queue = []
        self._base_polys = []
        self._trace_polys = {}
        self._boundary = None
        _set_session_toolbar_visible(False)
        FreeCAD.Console.PrintMessage("[BatchRoute] Session ended.\n")

    # ── pair queue ────────────────────────────────────────────────────────

    def remove_pair(self, index: int) -> None:
        if 0 <= index < len(self.queue):
            del self.queue[index]
            self._notify()

    # ── obstacle-field bookkeeping ────────────────────────────────────────

    def _extra_trace_margin(self) -> float:
        """How much wider than the plain clearance the space around TRACES
        must be — the user-facing "minimum trace spacing" option. 0 when the
        requested spacing does not exceed the clearance (which already
        guarantees clearance mm edge-to-edge)."""
        spacing = float(self.params.get("trace_spacing_mm", 0.0) or 0.0)
        return max(0.0, spacing - float(self.params["clearance_mm"]))

    def _trace_obstacle_polys(self, trace_obj) -> list:
        """The obstacle polygons one existing trace contributes: its per-leg
        footprint, inflated by the extra trace-spacing margin, and only when
        the trace actually lies near THIS routing surface (a trace on the
        board's other side projects onto the frame at the same 2-D spot, and
        must not count)."""
        band = float(self.params["thickness_mm"]) + float(self.params["clearance_mm"])
        try:
            dmin = min(self.frame.distance_to_surface(v.Point)
                       for v in trace_obj.Shape.Vertexes)
        except Exception:
            return []
        if dmin > band:
            return []
        extra = self._extra_trace_margin()
        polys = tob.outline_polys_of_object_on_frame(trace_obj, self.frame)
        if extra > 0:
            polys = [tob.inflate_poly(p, extra) for p in polys]
        return polys

    def _rebuild_field(self, exclude_trace: str = None) -> None:
        polys = list(self._base_polys)
        for name, tpolys in self._trace_polys.items():
            if name == exclude_trace:
                continue
            polys.extend(tpolys)
        self.field = tob.PolyField(
            polys,
            clearance=float(self.params["clearance_mm"]) + float(self.params["width_mm"]) / 2.0,
            cell_size=1.0, boundary=self._boundary)

    # ── routing ───────────────────────────────────────────────────────────

    def _route_pts(self, a2, c2, field):
        # route_head_with_via: the plain walk-around first, then the slower
        # via-point fallback — batch routing is offline, so it can afford
        # searches the live interactive router cannot.
        path, _flip = wa.route_head_with_via(
            FreeCAD.Vector(a2[0], a2[1], 0), FreeCAD.Vector(c2[0], c2[1], 0),
            field, step_deg=self.params.get("step_deg", wa.STEP_45))
        return path

    def _bake(self, path):
        try:
            return bake_trace(
                self.obj_name, self.face_index, [(p.x, p.y) for p in path],
                self.params["width_mm"], self.params["thickness_mm"],
                self.params["clearance_mm"])
        except Exception as exc:
            # An OCCT failure on ONE pair's solid must not abort the whole
            # batch — the caller records it blocked and keeps going.
            FreeCAD.Console.PrintError(f"[BatchRoute] baking failed: {exc}\n")
            return None

    def _endpoints_2d(self, trace_obj):
        wp = list(getattr(trace_obj, "Waypoints", []) or [])
        if len(wp) < 2:
            return None, None
        return self.frame.to_2d(wp[0]), self.frame.to_2d(wp[-1])

    def _try_ripup(self, a2, c2):
        """
        Single-trace rip-up-and-reroute: when a pair cannot be routed, try —
        for each existing trace in turn — whether removing THAT trace from
        the field lets the pair route, and whether the removed trace can
        then itself be rerouted around the pair's new copper. Only commits
        when BOTH routes exist; a half-successful attempt is rolled back
        (the freshly baked pair trace deleted again), so the document never
        ends up with a connection lost that existed before.

        Returns (new_trace_obj, rerouted_trace_name) or (None, None).
        """
        doc = self.doc
        for tname in list(self._trace_polys.keys()):
            tobj = doc.getObject(tname)
            if tobj is None:
                continue
            e0, e1 = self._endpoints_2d(tobj)
            if e0 is None or e1 is None:
                continue        # not reroutable — don't rip what we can't restore

            self._rebuild_field(exclude_trace=tname)
            path_new = self._route_pts(a2, c2, self.field)
            if path_new is None:
                continue
            new_obj = self._bake(path_new)
            if new_obj is None:
                continue

            # The ripped trace must now route around the new pair's copper.
            self._trace_polys[new_obj.Name] = self._trace_obstacle_polys(new_obj)
            self._rebuild_field(exclude_trace=tname)
            path_r = self._route_pts(e0, e1, self.field)
            if path_r is not None and rebuild_trace(tname, [(p.x, p.y) for p in path_r]) is not None:
                self._trace_polys[tname] = self._trace_obstacle_polys(tobj)
                self._rebuild_field()
                FreeCAD.Console.PrintMessage(
                    f"[BatchRoute] Rerouted existing {tname} to make room.\n")
                return new_obj, tname

            # Roll back: remove the new trace, restore the old field.
            del self._trace_polys[new_obj.Name]
            try:
                doc.removeObject(new_obj.Name)
            except Exception:
                pass
        self._rebuild_field()
        return None, None

    def route_all(self) -> list:
        """
        Route and bake every queued pair in order, folding each newly baked
        trace's own footprint into the live PolyField before routing the
        next pair (see module docstring). Returns a list of
        (first_name, second_name, "baked"/"blocked", trace_name_or_None).

        A pair that cannot be routed directly triggers single-trace
        rip-up-and-reroute (see _try_ripup) unless the session was started
        with allow_reroute=False. A blocked pair does not stop the batch —
        the rest of the queue still runs.
        """
        results = []
        if not self.is_active or self.doc is None:
            return results

        doc = self.doc
        for first_name, second_name in self.queue:
            first  = doc.getObject(first_name)
            second = doc.getObject(second_name)
            p1 = getattr(first, "ContactPoint", None) if first is not None else None
            p2 = getattr(second, "ContactPoint", None) if second is not None else None
            if p1 is None or p2 is None:
                results.append((first_name, second_name, "blocked", None))
                continue

            a2 = self.frame.to_2d(FreeCAD.Vector(p1))
            c2 = self.frame.to_2d(FreeCAD.Vector(p2))
            if a2 is None or c2 is None:
                results.append((first_name, second_name, "blocked", None))
                continue

            path = self._route_pts(a2, c2, self.field)
            obj = self._bake(path) if path is not None else None

            if obj is None and self.params.get("allow_reroute", True):
                obj, rerouted = self._try_ripup(a2, c2)

            if obj is None:
                results.append((first_name, second_name, "blocked", None))
                continue

            if obj.Name not in self._trace_polys:
                self._trace_polys[obj.Name] = self._trace_obstacle_polys(obj)
                self._rebuild_field()
            results.append((first_name, second_name, "baked", obj.Name))

        self.last_results = results
        self.queue = []
        self._notify()
        n_baked   = sum(1 for r in results if r[2] == "baked")
        n_blocked = len(results) - n_baked
        FreeCAD.Console.PrintMessage(
            f"[BatchRoute] Route All: {n_baked} baked, {n_blocked} blocked.\n"
        )
        return results

    # ── FreeCAD Selection observer callbacks ───────────────────────────────

    def addSelection(self, doc, obj_name, sub, pos):
        if not self.is_active:
            return
        obj = _resolve_obj(doc, obj_name)
        if obj is None or not getattr(obj, "IsContactPoint", False):
            FreeCADGui.Selection.clearSelection()
            return

        if self._state == _State.AWAIT_FIRST:
            self._first_cp = obj
            self._state    = _State.AWAIT_SECOND
            FreeCAD.Console.PrintMessage(
                f"[BatchRoute] First: {obj.Name} — click the second point\n")
        else:
            if obj is self._first_cp:
                FreeCAD.Console.PrintWarning(
                    "[BatchRoute] Same contact point selected twice — pick a "
                    "different one.\n")
                FreeCADGui.Selection.clearSelection()
                return
            self.queue.append((self._first_cp.Name, obj.Name))
            FreeCAD.Console.PrintMessage(
                f"[BatchRoute] Queued {self._first_cp.Name} -> {obj.Name} "
                f"({len(self.queue)} pair(s) queued)\n"
            )
            self._first_cp = None
            self._state    = _State.AWAIT_FIRST

        FreeCADGui.Selection.clearSelection()
        self._notify()

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

    # ── helpers ───────────────────────────────────────────────────────────

    def _notify(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass


# Singleton instance — persisted across command activations, mirrors
# wirebond.ManualWireBonding's module-level `manual_bonder`.
batch_router = BatchRouteSession()
