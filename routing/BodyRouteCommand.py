# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
3-D Route — a trace that crosses from one face of a body onto the next.

The other routers all work within ONE face. That covers flat, slanted and
curved faces alike (core.routing_frame maps any of them to a metric 2-D
space), but a trace can never leave the face it started on, because the
routable-surface boundary stops it at the edge by design.

This command is the separate tool for the case that needs more: connecting
two contact points that sit on DIFFERENT faces of the same body — a pad on
top of a package and one down its flank, say. It works out which faces touch
which, finds a sensible sequence of them, picks where to cross each shared
edge, and routes within every face along the way with the same walk-around
the single-face routers use. The result is baked as one trace solid that
follows the body around its edges.

The planning lives in core.body_routing, which is Qt-free and headlessly
tested; this module is the document and UI layer around it.
"""

import os
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

import core.body_routing as body_routing
import core.routing_frame as routing_frame
import core.trace_obstacles as trace_obstacles
import core.trace_walkaround as trace_walkaround
from Get_Path import get_icon
from routing.InteractiveRouterCommand import bake_body_trace

DEFAULT_WIDTH_MM     = 0.3
DEFAULT_THICKNESS_MM = 0.035
DEFAULT_CLEARANCE_MM = 0.2

# How far a contact point may sit from a face and still be taken as being on it.
_ON_FACE_TOL_MM = 0.5


def _parent_placement_of(obj):
    """Placement of *obj*'s containers only — a Part::Feature's Shape already
    carries its own Placement but not an App::Part container's."""
    pl = FreeCAD.Placement()
    current = obj
    while current.InList:
        parent = current.InList[0]
        if hasattr(parent, "Placement"):
            pl = parent.Placement.multiply(pl)
        current = parent
    return pl


def _selected_contact_points():
    pts = []
    try:
        sel = FreeCADGui.Selection.getSelection()
    except Exception:
        sel = []
    for o in sel:
        if getattr(o, "IsContactPoint", False):
            pts.append(o)
    return pts


def _point_of(marker):
    pos = getattr(marker, "ContactPoint", None)
    if pos is not None:
        return FreeCAD.Vector(pos)
    try:
        return FreeCAD.Vector(marker.Shape.Vertexes[0].Point)
    except Exception:
        return None


def _candidate_bodies(doc):
    """Physical bodies a trace could be routed over, largest first — the one
    carrying both pads is usually the package or board."""
    out = []
    for o in doc.Objects:
        if getattr(o, "IsContactPoint", False) or getattr(o, "IsRoutingTrace", False):
            continue
        try:
            if not o.isDerivedFrom("Part::Feature"):
                continue
        except Exception:
            continue
        shp = getattr(o, "Shape", None)
        if shp is None or shp.isNull() or not shp.Faces:
            continue
        try:
            out.append((shp.Area, o))
        except Exception:
            continue
    out.sort(key=lambda t: -t[0])
    return [o for _a, o in out]


def _face_carrying(obj, point, tol: float = _ON_FACE_TOL_MM):
    """(face_index, distance) of the face of *obj* nearest *point*, or
    (None, inf) when nothing is within *tol*."""
    import Part
    best_i, best_d = None, tol
    try:
        faces = obj.Shape.Faces
    except Exception:
        return None, float("inf")
    vertex = Part.Vertex(point)
    for i, f in enumerate(faces):
        try:
            d = f.distToShape(vertex)[0]
        except Exception:
            continue
        if d <= best_d:
            best_d, best_i = d, i
    return best_i, best_d


def _find_body_and_faces(doc, p1, p2):
    """The body carrying BOTH points, with the face each sits on."""
    for obj in _candidate_bodies(doc):
        i1, _d1 = _face_carrying(obj, p1)
        if i1 is None:
            continue
        i2, _d2 = _face_carrying(obj, p2)
        if i2 is None:
            continue
        return obj, i1, i2
    return None, None, None


def route_between(doc, marker_a, marker_b, params):
    """
    Plan and bake a trace between two contact points across a body's faces.
    Returns (trace_object, message).
    """
    p1, p2 = _point_of(marker_a), _point_of(marker_b)
    if p1 is None or p2 is None:
        return None, "Those contact points have no usable position."

    obj, face_a, face_b = _find_body_and_faces(doc, p1, p2)
    if obj is None:
        return None, ("No single body carries both contact points — 3-D Route "
                       "connects two pads on the SAME body, crossing its faces.")

    shape = obj.Shape
    parent = _parent_placement_of(obj)
    if not parent.isIdentity():
        shape = shape.copy()
        shape.transformShape(parent.toMatrix())

    width = params["width_mm"]
    thickness = params["thickness_mm"]
    clearance = params["clearance_mm"]
    band = thickness + clearance

    cache = {}

    def field_for_face(face_idx):
        if face_idx not in cache:
            face = shape.Faces[face_idx]
            frame = routing_frame.SurfaceFrame(face)
            polys = trace_obstacles.collect_obstacle_polys_on_frame(
                doc, {obj.Name}, frame, band)
            polys += trace_obstacles.face_hole_polys_on_frame(face, frame)
            field = trace_obstacles.PolyField(
                polys, clearance=clearance + width / 2.0, cell_size=1.0,
                boundary=trace_obstacles.face_outer_poly_on_frame(face, frame))
            cache[face_idx] = (frame, field)
        return cache[face_idx]

    segments = body_routing.route_across_faces(
        shape, face_a, p1, face_b, p2, field_for_face,
        step_deg=params.get("step_deg", trace_walkaround.STEP_45),
        max_faces=params.get("max_faces", body_routing.DEFAULT_MAX_FACES),
        # Square up to each shared edge over roughly a trace width, which is
        # enough for the two faces' segments to meet flush without forcing a
        # long detour on a small face.
        approach_mm=max(width, 0.05))

    if not segments:
        if face_a == face_b:
            return None, ("Blocked on that face — try a larger clearance, or "
                           "route it by hand with Interactive Route.")
        return None, ("No way across the body's faces at that clearance. The "
                       "pads may be separated by copper, or need more faces "
                       "than the limit allows.")

    trace = bake_body_trace(obj.Name, segments, width, thickness, clearance)
    if trace is None:
        return None, "The route was found but the trace solid could not be built."

    frames = {f: cache[f][0] for f, _p in segments if f in cache}
    length = body_routing.segments_length(segments, frames)
    faces_txt = (f"{len(segments)} faces" if len(segments) > 1 else "1 face")

    # Warn about double-curved faces, exactly as Interactive Route does. On
    # such a face the parameter-to-millimetre scale varies across the
    # surface, so the trace still lies ON it but its width and the clearances
    # it kept hold only approximately — worth saying out loud rather than
    # letting a lumpy-looking trace on an organic part look like a bug.
    worst = 0.0
    for face_idx in frames:
        try:
            worst = max(worst, frames[face_idx].distortion())
        except Exception:
            continue
    warning = ""
    if worst > 0.05:
        warning = (f"  Note: one face is double-curved ({worst * 100:.0f}% "
                    "scale variation), so trace width and clearance hold only "
                    "approximately there.")
        FreeCAD.Console.PrintWarning(f"[BodyRoute]{warning}\n")

    pieces = len(trace.Shape.Solids)
    if pieces > 1:
        warning += (f"  WARNING: the trace came out as {pieces} separate "
                     "pieces — it is broken where it crosses an edge.")

    # Plain ASCII: this string also goes to Console.PrintMessage, and the
    # Windows console codec cannot encode an arrow.
    return trace, (f"Routed {marker_a.Name} -> {marker_b.Name} across "
                    f"{faces_txt} on {obj.Label or obj.Name} "
                    f"({length:.2f} mm).{warning}")


class BodyRouteDialog(QtWidgets.QDialog):
    """Modeless: pick two contact points in the 3-D view, press Route,
    repeat — the same idiom as the Contact Point Symmetry dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("3-D Route")
        self.setModal(False)
        self.setMinimumWidth(320)
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)

        intro = QtWidgets.QLabel(
            "Connects two contact points on <b>different faces of the same "
            "body</b>. The trace follows the surface across the shared edges.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self._lbl_sel = QtWidgets.QLabel()
        self._lbl_sel.setWordWrap(True)
        root.addWidget(self._lbl_sel)

        refresh = QtWidgets.QPushButton("↺  Read current FreeCAD selection")
        refresh.clicked.connect(self._refresh)
        root.addWidget(refresh)

        form = QtWidgets.QFormLayout()
        self._width = QtWidgets.QDoubleSpinBox()
        self._width.setRange(0.001, 100.0)
        self._width.setDecimals(3)
        self._width.setValue(DEFAULT_WIDTH_MM)
        self._width.setSuffix(" mm")
        form.addRow("Trace width:", self._width)

        self._thickness = QtWidgets.QDoubleSpinBox()
        self._thickness.setRange(0.001, 100.0)
        self._thickness.setDecimals(3)
        self._thickness.setValue(DEFAULT_THICKNESS_MM)
        self._thickness.setSuffix(" mm")
        form.addRow("Trace thickness:", self._thickness)

        self._clearance = QtWidgets.QDoubleSpinBox()
        self._clearance.setRange(0.0, 100.0)
        self._clearance.setDecimals(3)
        self._clearance.setValue(DEFAULT_CLEARANCE_MM)
        self._clearance.setSuffix(" mm")
        form.addRow("Clearance:", self._clearance)

        self._max_faces = QtWidgets.QSpinBox()
        self._max_faces.setRange(2, 64)
        self._max_faces.setValue(body_routing.DEFAULT_MAX_FACES)
        self._max_faces.setToolTip(
            "How many faces one trace may cross. Raising it lets a trace take "
            "a longer way round a complicated body, at the cost of a slower "
            "search.")
        form.addRow("Max faces crossed:", self._max_faces)
        root.addLayout(form)

        self._btn = QtWidgets.QPushButton("Route selected pair")
        self._btn.clicked.connect(self._route)
        root.addWidget(self._btn)

        # ── queue: connect many pairs in one go ──────────────────────────
        q_grp = QtWidgets.QGroupBox("Queue (routed one after another)")
        q_lay = QtWidgets.QVBoxLayout(q_grp)
        q_lay.setSpacing(4)

        add = QtWidgets.QPushButton("Add selected pair to queue")
        add.setToolTip("Select two contact points and add them; repeat for "
                        "every connection, then press Route all.")
        add.clicked.connect(self._enqueue)
        q_lay.addWidget(add)

        self._queue_list = QtWidgets.QListWidget()
        self._queue_list.setMaximumHeight(110)
        q_lay.addWidget(self._queue_list)

        row = QtWidgets.QHBoxLayout()
        rm = QtWidgets.QPushButton("Remove selected")
        rm.clicked.connect(self._dequeue)
        clr = QtWidgets.QPushButton("Clear")
        clr.clicked.connect(self._clear_queue)
        row.addWidget(rm)
        row.addWidget(clr)
        q_lay.addLayout(row)

        run = QtWidgets.QPushButton("Route all queued")
        run.setToolTip(
            "Route every queued pair in order. Each trace becomes copper the\n"
            "later ones must keep clear of, so order matters — queue the\n"
            "hardest connections first, while there is still room.")
        run.clicked.connect(self._route_all)
        q_lay.addWidget(run)
        root.addWidget(q_grp)

        self._queue = []          # [(name_a, name_b), ...]

        self._lbl_status = QtWidgets.QLabel("")
        self._lbl_status.setWordWrap(True)
        self._lbl_status.setStyleSheet("font-size: 11px; color: #888;")
        root.addWidget(self._lbl_status)

        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.close)
        root.addWidget(close)

    def _refresh(self):
        pts = _selected_contact_points()
        if len(pts) == 2:
            self._lbl_sel.setText(
                f"<b>{pts[0].Name} → {pts[1].Name}</b>")
            self._lbl_sel.setStyleSheet("color: #88cc88;")
        else:
            self._lbl_sel.setText(
                f"<b>Select exactly two contact points</b> "
                f"({len(pts)} selected).")
            self._lbl_sel.setStyleSheet("color: #cc8844;")

    # ── queue ─────────────────────────────────────────────────────────────

    def _params(self):
        return {
            "width_mm":     self._width.value(),
            "thickness_mm": self._thickness.value(),
            "clearance_mm": self._clearance.value(),
            "max_faces":    self._max_faces.value(),
        }

    def _sync_queue(self):
        self._queue_list.clear()
        for a, b in self._queue:
            self._queue_list.addItem(f"{a}  ->  {b}")

    def _enqueue(self):
        pts = _selected_contact_points()
        if len(pts) != 2:
            QtWidgets.QMessageBox.warning(
                self, "Select two contact points",
                "Click one contact point, Ctrl+click a second, then add.")
            return
        pair = (pts[0].Name, pts[1].Name)
        if pair in self._queue or pair[::-1] in self._queue:
            self._lbl_status.setText("That pair is already queued.")
            return
        self._queue.append(pair)
        self._sync_queue()
        self._lbl_status.setText(f"{len(self._queue)} pair(s) queued.")

    def _dequeue(self):
        row = self._queue_list.currentRow()
        if 0 <= row < len(self._queue):
            del self._queue[row]
            self._sync_queue()

    def _clear_queue(self):
        self._queue = []
        self._sync_queue()

    def _route_all(self):
        doc = FreeCAD.activeDocument()
        if doc is None or not self._queue:
            QtWidgets.QMessageBox.information(
                self, "Nothing queued",
                "Add at least one pair to the queue first.")
            return
        params = self._params()
        routed, blocked = 0, []
        doc.openTransaction("3-D Route (queue)")
        try:
            for name_a, name_b in list(self._queue):
                a, b = doc.getObject(name_a), doc.getObject(name_b)
                if a is None or b is None:
                    blocked.append(f"{name_a} -> {name_b} (missing)")
                    continue
                try:
                    trace, message = route_between(doc, a, b, params)
                except Exception as exc:
                    FreeCAD.Console.PrintError(f"[BodyRoute] {exc}\n")
                    blocked.append(f"{name_a} -> {name_b}")
                    continue
                if trace is None:
                    blocked.append(f"{name_a} -> {name_b}")
                else:
                    routed += 1
                FreeCAD.Console.PrintMessage(f"[BodyRoute] {message}\n")
        finally:
            doc.commitTransaction()

        # Only the ones that got through leave the queue, so the rest stay
        # visible to retry with a different clearance or in another order.
        done = set()
        for name_a, name_b in self._queue:
            if f"{name_a} -> {name_b}" not in blocked and \
               f"{name_a} -> {name_b} (missing)" not in blocked:
                done.add((name_a, name_b))
        self._queue = [p for p in self._queue if p not in done]
        self._sync_queue()

        msg = f"Routed {routed} of {routed + len(blocked)} queued pair(s)."
        if blocked:
            msg += ("  Still blocked (left in the queue): "
                    + ", ".join(blocked) + ".  Try a smaller clearance, a "
                    "higher face limit, or queue these first next time.")
        self._lbl_status.setText(msg)
        FreeCAD.Console.PrintMessage(f"[BodyRoute] {msg}\n")

    def _route(self):
        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        pts = _selected_contact_points()
        if len(pts) != 2:
            QtWidgets.QMessageBox.warning(
                self, "Select two contact points",
                "Click one contact point, Ctrl+click a second, then press "
                "Route.")
            return
        params = self._params()
        doc.openTransaction("3-D Route")
        try:
            trace, message = route_between(doc, pts[0], pts[1], params)
        except Exception as exc:
            doc.abortTransaction()
            FreeCAD.Console.PrintError(f"[BodyRoute] {exc}\n")
            self._lbl_status.setText(f"Failed: {exc}")
            return
        if trace is None:
            doc.abortTransaction()
        else:
            doc.commitTransaction()
        self._lbl_status.setText(message)
        FreeCAD.Console.PrintMessage(f"[BodyRoute] {message}\n")
        self._refresh()


_dialog = None


class BodyRouteCommand:
    """Toggle the 3-D Route dialog."""

    def GetResources(self):
        return {
            "MenuText": "3-D Route",
            "ToolTip": (
                "Route a trace across the faces of a 3-D body.\n\n"
                "Select two contact points on different faces of the same\n"
                "body; the trace follows the surface over the shared edges.\n"
                "For two pads on ONE face, the other routers are simpler."
            ),
            "Pixmap": get_icon("Body_Route.svg"),
        }

    def Activated(self):
        global _dialog
        if _dialog is None:
            _dialog = BodyRouteDialog(FreeCADGui.getMainWindow())
        else:
            _dialog._refresh()
        _dialog.show()
        _dialog.raise_()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("BodyRouteCommand", BodyRouteCommand())
