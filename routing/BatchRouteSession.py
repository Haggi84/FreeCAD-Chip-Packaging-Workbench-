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
from routing.InteractiveRouterCommand import bake_trace


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
        polys = tob.collect_obstacle_polys_on_frame(doc, {obj_name}, self.frame, band)
        polys += tob.face_hole_polys_on_frame(face, self.frame)
        self.field = tob.PolyField(
            polys, clearance=clearance_mm + width_mm / 2.0, cell_size=1.0,
            boundary=tob.face_outer_poly_on_frame(face, self.frame),
        )

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
        _set_session_toolbar_visible(False)
        FreeCAD.Console.PrintMessage("[BatchRoute] Session ended.\n")

    # ── pair queue ────────────────────────────────────────────────────────

    def remove_pair(self, index: int) -> None:
        if 0 <= index < len(self.queue):
            del self.queue[index]
            self._notify()

    # ── routing ───────────────────────────────────────────────────────────

    def route_all(self) -> list:
        """
        Route and bake every queued pair in order, folding each newly baked
        trace's own footprint into the live PolyField before routing the
        next pair (see module docstring). Returns a list of
        (first_name, second_name, "baked"/"blocked", trace_name_or_None).

        A blocked pair does not stop the batch — the rest of the queue
        still runs.
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

            path, _flip = wa.route_head(
                FreeCAD.Vector(a2[0], a2[1], 0), FreeCAD.Vector(c2[0], c2[1], 0),
                self.field, step_deg=self.params.get("step_deg", wa.STEP_45),
            )
            if path is None:
                results.append((first_name, second_name, "blocked", None))
                continue

            pts2d = [(p.x, p.y) for p in path]
            obj = bake_trace(
                self.obj_name, self.face_index, pts2d,
                self.params["width_mm"], self.params["thickness_mm"],
                self.params["clearance_mm"],
            )
            if obj is None:
                results.append((first_name, second_name, "blocked", None))
                continue

            for poly in tob.outline_polys_of_object_on_frame(obj, self.frame):
                self.field.add(poly)
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
