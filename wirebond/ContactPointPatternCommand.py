# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Copy a contact point onto other faces, one guided click at a time.

The flow this implements:

  1. Place a contact point on a face with any of the existing tools.
  2. Select that point AND the faces to copy it onto, then start this tool.
     The point's own face counts as a target too — adding more points to it
     (a row starting from the first) is an ordinary thing to want.
  3. The view swings round to look straight at the first target face.
  4. Move the mouse: a dashed cross-hair shows exactly where the point would
     land, and the report view names the position ("u = middle").
  5. Lock the movement to one axis, or snap to an exact position, so the
     copies line up instead of merely looking aligned.
  6. Click to place. The marker appears immediately.
  7. Move on to the next face and repeat.
  8. Confirm keeps everything; Undo removes the last point; Abort removes
     every point this session created and restores the original state.

All the placement arithmetic lives in core.point_pattern, which is Qt-free
and headlessly tested — this module is the interaction shell around it.

Built on the same Draft infrastructure as routing/InteractiveRouterCommand.py
and routing/TraceDragCommand.py; see the former's docstring for the two
Coin3D rules that matter (never mutate the document inside the event
callback, and never ask for a working-plane update per mouse move).
"""

import os
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

import core.face_symmetry as face_symmetry
import core.point_pattern as point_pattern
import core.routing_frame as routing_frame
from Get_Path import get_icon
from wirebond.SetContactPointsOnFaceCommand import (
    _add_to_contact_points_group,
    _create_gds_marker,
    _create_housing_marker,
    _get_source_assembly,
    _next_gds_index,
    _next_housing_index,
    _refresh_contact_panel,
)

_GUIDE_COLOR   = (0.25, 0.66, 0.96)   # cross-hair guide lines
_PREVIEW_COLOR = (1.00, 1.00, 0.00)   # the point that would be placed
_TOOLBAR_TITLE = "Contact Point Pattern"

# Points placed in this session are real objects straight away (so they
# persist and can be undone), but they are shown in a distinct colour until
# Confirm settles them — that is what makes "placed but not yet confirmed"
# visible at a glance next to contact points that were already there.
_PENDING_COLOR = (0.20, 1.00, 0.55)
_PENDING_SIZE  = 11
_SETTLED_SIZE  = 8
_SETTLED_COLOR = {"gds": (1.00, 0.50, 0.00), "package": (1.00, 1.00, 0.00)}

# How far off a face a contact point may sit and still count as being on it.
_ON_FACE_BAND_MM = 1.0


def _set_session_toolbar_visible(visible: bool) -> None:
    """Show/hide the contextual Confirm / Undo / Abort toolbar — same
    mechanism as the wire-bonding and routing session toolbars."""
    try:
        from compat import QtWidgets as _QW
        mw = FreeCADGui.getMainWindow()
        for tb in mw.findChildren(_QW.QToolBar):
            if tb.windowTitle() == _TOOLBAR_TITLE:
                tb.setVisible(visible)
                break
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[CPPattern] toolbar visibility: {exc}\n")


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


def _read_selection():
    """(source_marker, [(face, object_name), ...]) from the current selection."""
    try:
        sel = FreeCADGui.Selection.getSelectionEx()
    except Exception:
        sel = []
    source = None
    faces = []
    for s in sel:
        obj = s.Object
        if getattr(obj, "IsContactPoint", False) and source is None:
            source = obj
            continue
        if not hasattr(obj, "Shape"):
            continue
        parent = _parent_placement_of(obj)
        for sub_name in (s.SubElementNames or []):
            if not sub_name.startswith("Face"):
                continue
            try:
                face = obj.Shape.getElement(sub_name)
            except Exception:
                continue
            if not parent.isIdentity():
                face = face.copy()
                face.transformShape(parent.toMatrix())
            faces.append((face, obj.Name))
    return source, faces


def _marker_kind_of(marker) -> str:
    return "gds" if marker.Name.startswith("ContactPoint_") else "package"


def _style_marker(marker, pending: bool) -> None:
    """Show a marker as pending (this session's, not yet confirmed) or as a
    settled contact point. A no-op without a GUI, where there is no
    ViewObject to style."""
    if not FreeCAD.GuiUp:
        return
    vo = getattr(marker, "ViewObject", None)
    if vo is None:
        return
    try:
        if pending:
            vo.PointColor = _PENDING_COLOR
            vo.PointSize = _PENDING_SIZE
        else:
            vo.PointColor = _SETTLED_COLOR[_marker_kind_of(marker)]
            vo.PointSize = _SETTLED_SIZE
    except Exception:
        pass


class _PatternSession:
    """
    Holds everything the tool and the Confirm/Undo/Abort commands share:
    the target faces, which one is current, and the markers created so far.

    The markers are created for real as they are placed (the user asked to
    see the point appear), so Undo and Abort work by DELETING them again
    rather than by replaying a pending list — the document is always what
    the user sees.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.is_active = False
        self.doc = None
        self.source = None       # optional template point; None = from scratch
        self.faces = []          # [(face, object_name, frame, extent), ...]
        self.index = 0
        self.placed = []         # marker names, in creation order
        self.source_rel = (0.5, 0.5)
        self.tool = None
        self.dialog = None
        # Placement mode lives HERE rather than on the tool, so the 3-D tool
        # and the dialog are two views of one state instead of two copies
        # that can disagree.
        self.move = point_pattern.MOVE_FREE
        self.snap = point_pattern.SNAP_NONE

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self, doc, source, picked_faces) -> bool:
        self.reset()
        self.doc = doc
        self.source = source

        # No template point is fine: the session then simply places new
        # points, and the reference falls back to each face's own centre.
        src_pos = None
        if source is not None:
            try:
                src_pos = FreeCAD.Vector(getattr(source, "ContactPoint", None)
                                          or source.Shape.Vertexes[0].Point)
            except Exception:
                src_pos = None

        prepared = []
        src_frame = None
        src_best = _ON_FACE_BAND_MM
        for face, obj_name in picked_faces:
            try:
                frame = routing_frame.SurfaceFrame(face)
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"[CPPattern] skipping an unusable face on {obj_name}: {exc}\n")
                continue
            ext = face_symmetry.outer_extent_2d(face, frame)
            if ext is None:
                continue
            # Note which face the source sits on — its relative position
            # there is what gets carried onto the others (nearest wins, so a
            # point on a shared edge picks the face it is really on). That
            # face stays a perfectly good TARGET as well: adding further
            # points to the face the first one is on is an ordinary thing to
            # want, e.g. extending it into a row.
            if src_pos is not None:
                try:
                    d = frame.distance_to_surface(src_pos)
                    if d <= src_best:
                        src_best = d
                        src_frame = (face, frame, ext)
                except Exception:
                    pass
            prepared.append((face, obj_name, frame, ext))

        if not prepared:
            FreeCAD.Console.PrintError(
                "[CPPattern] Select at least one target face.\n")
            return False

        # The source's relative position within its own face is what gets
        # carried across (see core.point_pattern.carry_reference). Without a
        # source, or when the source's face was not among the selection, the
        # reference stays the middle — the least surprising default.
        if src_frame is not None and src_pos is not None:
            face, frame, ext = src_frame
            xy = frame.to_2d(src_pos)
            if xy is not None:
                self.source_rel = point_pattern.relative_position(xy, ext)

        self.faces = prepared
        self.index = 0
        self.is_active = True
        _set_session_toolbar_visible(True)
        what = (f"Copying {source.Name} onto" if source is not None
                else "Placing new contact points on")
        FreeCAD.Console.PrintMessage(
            f"[CPPattern] {what} {len(prepared)} face(s).\n"
            "  Move the mouse to aim, or type exact values in the dialog; "
            "click to place.\n"
            "  X / Y lock the movement axis, F frees it, M cycles the middle "
            "snaps,\n"
            "  N goes to the next face, Backspace undoes, Enter confirms, "
            "Esc aborts.\n"
        )
        return True

    def end(self, keep: bool):
        if not self.is_active:
            return
        # Counted BEFORE undo_all empties the list, or an abort would always
        # claim it discarded nothing.
        n = len(self.placed)
        if keep:
            self.settle_placed()
        else:
            self.undo_all()
        dialog = self.dialog
        self.reset()
        if dialog is not None:
            try:
                dialog.close()
            except Exception:
                pass
        _set_session_toolbar_visible(False)
        FreeCAD.Console.PrintMessage(
            f"[CPPattern] Session ended — {n} contact point(s) "
            f"{'kept' if keep else 'discarded'}.\n")

    # ── shared placement mode ────────────────────────────────────────────

    def set_mode(self, move=None, snap=None):
        """Update the movement lock / snap and refresh both views of it."""
        if move is not None:
            self.move = move
        if snap is not None:
            self.snap = snap
        if self.tool is not None:
            try:
                self.tool.refresh()
            except Exception:
                pass
        if self.dialog is not None:
            try:
                self.dialog.sync_modes()
            except Exception:
                pass

    # ── current face ─────────────────────────────────────────────────────

    def current(self):
        if not self.faces:
            return None
        return self.faces[self.index % len(self.faces)]

    def reference(self):
        """Where the copied point wants to be on the CURRENT face."""
        cur = self.current()
        if cur is None:
            return (0.0, 0.0)
        return point_pattern.apply_relative(self.source_rel, cur[3])

    def next_face(self, step: int = 1):
        if not self.faces:
            return
        self.index = (self.index + step) % len(self.faces)
        if self.tool is not None:
            try:
                self.tool.enter_face()
            except Exception:
                pass
        if self.dialog is not None:
            try:
                self.dialog.sync_face()
            except Exception:
                pass

    def kind_for(self, obj_name: str) -> str:
        """'gds' or 'package'. Matched to the template point when there is
        one, otherwise inferred from the group the target object lives in —
        the same rule the grid placement tool uses."""
        if self.source is not None:
            return _marker_kind_of(self.source)
        return _get_source_assembly(self.doc, obj_name or "")

    # ── placement ────────────────────────────────────────────────────────

    def place(self, pt2d):
        """Create a marker at a frame-space point on the current face."""
        cur = self.current()
        if cur is None or self.doc is None:
            return None
        face, obj_name, frame, _ext = cur
        if not face_symmetry.is_inside_face(pt2d, face, frame):
            FreeCAD.Console.PrintWarning(
                "[CPPattern] That position is outside the face — not placed.\n")
            return None
        world = face_symmetry.to_world([pt2d], frame)
        if not world:
            return None
        try:
            if self.kind_for(obj_name) == "gds":
                m = _create_gds_marker(self.doc, obj_name, world[0],
                                        _next_gds_index(self.doc))
            else:
                m = _create_housing_marker(self.doc, obj_name, world[0],
                                            _next_housing_index(self.doc))
            _add_to_contact_points_group(self.doc, m)
            _style_marker(m, pending=True)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"[CPPattern] placing failed: {exc}\n")
            return None
        self.placed.append(m.Name)
        self.doc.recompute()
        _refresh_contact_panel()
        # The point stays on screen from here until Confirm keeps it or
        # Abort removes it — it is a real object, not a transient preview.
        # The cross-hair must come straight back so the NEXT point can be
        # aimed without moving the mouse first.
        if self.tool is not None:
            try:
                self.tool.refresh()
            except Exception:
                pass
        if self.dialog is not None:
            try:
                self.dialog.sync_face()
            except Exception:
                pass
        FreeCAD.Console.PrintMessage(
            f"[CPPattern] Placed {m.Name} on {obj_name} "
            f"({len(self.placed)} so far).\n")
        return m

    def undo_last(self) -> bool:
        if not self.placed or self.doc is None:
            return False
        name = self.placed.pop()
        try:
            self.doc.removeObject(name)
            self.doc.recompute()
            _refresh_contact_panel()
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[CPPattern] undo {name}: {exc}\n")
            return False
        FreeCAD.Console.PrintMessage(f"[CPPattern] Removed {name}.\n")
        return True

    def undo_all(self):
        while self.placed:
            if not self.undo_last():
                break

    def settle_placed(self):
        """Confirm: the pending points become ordinary contact points."""
        if self.doc is None:
            return
        for name in self.placed:
            o = self.doc.getObject(name)
            if o is not None:
                _style_marker(o, pending=False)
        _refresh_contact_panel()


SESSION = _PatternSession()


# ── the numeric dialog ───────────────────────────────────────────────────────

class _PatternDialog(QtWidgets.QDialog):
    """
    Numeric entry alongside the 3-D tool: type exact U/V values and watch the
    cross-hair follow in the view, instead of hunting for a position with the
    mouse.

    The dialog and the 3-D tool are two views of ONE state (SESSION.move /
    SESSION.snap and the tool's current preview point), not two copies —
    moving the mouse updates the spin boxes and typing updates the preview,
    without either fighting the other.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Contact Point Pattern")
        self.setModal(False)
        self.setMinimumWidth(300)
        # Set while pushing values INTO the widgets, so their change signals
        # do not bounce straight back out as a fresh edit.
        self._syncing = False
        self._build_ui()
        self.sync_face()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)

        self._lbl_face = QtWidgets.QLabel()
        self._lbl_face.setWordWrap(True)
        root.addWidget(self._lbl_face)

        nav = QtWidgets.QHBoxLayout()
        prev_btn = QtWidgets.QPushButton("◀ Previous face")
        next_btn = QtWidgets.QPushButton("Next face ▶")
        prev_btn.clicked.connect(lambda: SESSION.next_face(-1))
        next_btn.clicked.connect(lambda: SESSION.next_face(1))
        nav.addWidget(prev_btn)
        nav.addWidget(next_btn)
        root.addLayout(nav)

        form = QtWidgets.QFormLayout()
        self._u = QtWidgets.QDoubleSpinBox()
        self._v = QtWidgets.QDoubleSpinBox()
        for box, label in ((self._u, "X on face:"), (self._v, "Y on face:")):
            box.setDecimals(3)
            box.setSingleStep(0.1)
            box.setSuffix(" mm")
            box.setRange(-1e6, 1e6)
            box.valueChanged.connect(self._on_edit)
            form.addRow(label, box)
        root.addLayout(form)

        hint = QtWidgets.QLabel(
            "Measured along the face's own axes, from its lower-left corner "
            "as seen in the aligned view.")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 10px; color: #888;")
        root.addWidget(hint)

        move_grp = QtWidgets.QGroupBox("Movement")
        move_lay = QtWidgets.QHBoxLayout(move_grp)
        self._move_btns = {}
        for label, mode in (("Free (F)", point_pattern.MOVE_FREE),
                             ("Lock X (X)", point_pattern.MOVE_U),
                             ("Lock Y (Y)", point_pattern.MOVE_V)):
            b = QtWidgets.QRadioButton(label)
            b.toggled.connect(
                lambda checked, m=mode: checked and not self._syncing
                and SESSION.set_mode(move=m))
            move_lay.addWidget(b)
            self._move_btns[mode] = b
        move_grp.setToolTip(
            "A locked axis is pinned to the reference — the template point's\n"
            "position, or the face centre when there is no template — so a\n"
            "row of copies lines up exactly.")
        root.addWidget(move_grp)

        snap_row = QtWidgets.QHBoxLayout()
        snap_row.addWidget(QtWidgets.QLabel("Snap (M):"))
        self._snap = QtWidgets.QComboBox()
        self._snap_modes = (point_pattern.SNAP_NONE, point_pattern.SNAP_MID_U,
                             point_pattern.SNAP_MID_V, point_pattern.SNAP_MID_BOTH)
        self._snap.addItems(["None", "Middle of X", "Middle of Y",
                              "Centre of face"])
        self._snap.setToolTip(
            "An exact position. A snap deliberately overrides a movement "
            "lock on the axis it names.")
        self._snap.currentIndexChanged.connect(
            lambda i: (not self._syncing)
            and SESSION.set_mode(snap=self._snap_modes[i]))
        snap_row.addWidget(self._snap)
        root.addLayout(snap_row)

        self._lbl_pos = QtWidgets.QLabel("")
        self._lbl_pos.setWordWrap(True)
        self._lbl_pos.setStyleSheet("font-size: 11px; color: #888;")
        root.addWidget(self._lbl_pos)

        place = QtWidgets.QPushButton("Place point here")
        place.setToolTip("Create a contact point at the previewed position.")
        place.clicked.connect(self._on_place)
        root.addWidget(place)

        btns = QtWidgets.QHBoxLayout()
        undo = QtWidgets.QPushButton("Undo last")
        confirm = QtWidgets.QPushButton("Confirm")
        abort = QtWidgets.QPushButton("Abort")
        undo.clicked.connect(lambda: SESSION.undo_last())
        confirm.clicked.connect(lambda: _stop_tool(abort=False))
        abort.clicked.connect(lambda: _stop_tool(abort=True))
        for b in (undo, confirm, abort):
            btns.addWidget(b)
        root.addLayout(btns)

    # ── syncing ──────────────────────────────────────────────────────────

    def sync_modes(self):
        self._syncing = True
        try:
            btn = self._move_btns.get(SESSION.move)
            if btn is not None:
                btn.setChecked(True)
            if SESSION.snap in self._snap_modes:
                self._snap.setCurrentIndex(self._snap_modes.index(SESSION.snap))
        finally:
            self._syncing = False

    def sync_face(self):
        cur = SESSION.current()
        if cur is None:
            self._lbl_face.setText("<b>No target face.</b>")
            return
        _face, obj_name, _frame, ext = cur
        self._syncing = True
        try:
            self._u.setRange(ext[0], ext[2])
            self._v.setRange(ext[1], ext[3])
        finally:
            self._syncing = False
        self._lbl_face.setText(
            f"<b>Face {SESSION.index + 1} of {len(SESSION.faces)}</b> "
            f"on {obj_name} — {ext[2] - ext[0]:.2f} x {ext[3] - ext[1]:.2f} mm."
            + (f"  Placed so far: {len(SESSION.placed)}." if SESSION.placed else ""))
        self.sync_modes()

    def sync_point(self, pt2d):
        """Called by the 3-D tool whenever the previewed point moves."""
        if pt2d is None:
            return
        self._syncing = True
        try:
            self._u.setValue(pt2d[0])
            self._v.setValue(pt2d[1])
        finally:
            self._syncing = False
        cur = SESSION.current()
        if cur is not None:
            self._lbl_pos.setText(
                point_pattern.describe(pt2d, cur[3], SESSION.reference()))

    # ── actions ──────────────────────────────────────────────────────────

    def _on_edit(self):
        if self._syncing or SESSION.tool is None:
            return
        SESSION.tool.set_preview((self._u.value(), self._v.value()))

    def _on_place(self):
        tool = SESSION.tool
        pt = getattr(tool, "preview", None) if tool is not None else None
        if pt is None:
            pt = (self._u.value(), self._v.value())
        SESSION.place(pt)
        self.sync_face()

    def closeEvent(self, event):
        # Closing the dialog is not the same as ending the session — the 3-D
        # tool may still be running, so just detach.
        if SESSION.dialog is self:
            SESSION.dialog = None
        super().closeEvent(event)


# ── the interactive tool ─────────────────────────────────────────────────────

def _make_tool():
    try:
        import DraftTools  # noqa: F401  (imported for its side effects)
        from draftguitools import gui_base_original, gui_tool_utils
        from draftguitools import gui_trackers as trackers
        from pivy import coin
    except Exception as exc:
        FreeCAD.Console.PrintError(
            "[CPPattern] FreeCAD's Draft workbench is required and could not "
            f"be loaded: {exc}\n")
        return None
    if not hasattr(FreeCAD, "activeDraftCommand"):
        FreeCAD.activeDraftCommand = None
    if not hasattr(FreeCADGui, "draftToolBar"):
        FreeCAD.Console.PrintError(
            "[CPPattern] Draft's tool bar is not available; open the Draft "
            "workbench once and try again.\n")
        return None

    import Part

    class _Tool(gui_base_original.Creator):

        def Activated(self):
            super().Activated(name="ContactPointPattern")
            if not SESSION.is_active:
                self.finish()
                return

            self.point = None
            self.preview = None

            z = 0.0
            self.guide_u = trackers.wireTracker(Part.makePolygon(
                [FreeCAD.Vector(0, 0, z), FreeCAD.Vector(1, 0, z)]))
            self.guide_v = trackers.wireTracker(Part.makePolygon(
                [FreeCAD.Vector(0, 0, z), FreeCAD.Vector(1, 0, z)]))
            for g in (self.guide_u, self.guide_v):
                g.setColor(_GUIDE_COLOR)

            # The point marker is a Coin SoMarkerSet, which draws at a fixed
            # PIXEL size. A marker built from model-space geometry cannot be
            # right: scaled to the face it was invisible on a 2.00 x 0.50 mm
            # pad and far too large on a 1.20 x 1.20 mm one, and either way it
            # changed size as you zoomed. Built once here, outside the event
            # callback; the callback only updates its coordinate FIELD, which
            # is the documented safe path (see InteractiveRouterCommand).
            self._marker_sep = coin.SoSeparator()
            colour = coin.SoBaseColor()
            colour.rgb = _PREVIEW_COLOR
            self._marker_coords = coin.SoCoordinate3()
            self._marker_coords.point.setValue(0.0, 0.0, 0.0)
            self._marker = coin.SoMarkerSet()
            try:
                self._marker.markerIndex = FreeCADGui.getMarkerIndex("CIRCLE_FILLED", 9)
            except Exception:
                # getMarkerIndex honours the user's marker-size preference but
                # is GUI-only; Coin's own constant is the fallback, since the
                # default index is a hard-to-see small cross.
                try:
                    self._marker.markerIndex = coin.SoMarkerSet.CIRCLE_FILLED_9_9
                except Exception:
                    pass
            self._marker.numPoints = 0
            self._marker_sep.addChild(colour)
            self._marker_sep.addChild(self._marker_coords)
            self._marker_sep.addChild(self._marker)
            try:
                self.view.getSceneGraph().addChild(self._marker_sep)
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"[CPPattern] preview marker unavailable: {exc}\n")
                self._marker = None

            # Draft's own wire panel is deliberately NOT shown: this tool has
            # its own dialog, and raising both put two different panels with
            # the same title on screen, the Draft one offering point entry
            # that does not drive this tool at all.
            self.call = self.view.addEventCallback("SoEvent", self.action)
            self.enter_face()

        # ── external hooks (the dialog drives these) ─────────────────────

        def set_preview(self, pt2d):
            """Place the preview at an explicitly typed position."""
            cur = SESSION.current()
            if cur is None or pt2d is None:
                return
            _face, _obj, frame, ext = cur
            self.point = pt2d
            self.preview = point_pattern.clamp_to_extent(pt2d, ext)
            self._draw(frame, ext)
            gui_tool_utils.redraw3DView()

        def refresh(self):
            """Re-apply the current movement/snap mode to the last cursor.
            Falls back to the face's reference so that a preview is always
            showing — after placing a point, in particular, the cross-hair
            must come straight back rather than waiting for the next mouse
            move."""
            self._recompute(self.point if self.point is not None
                             else SESSION.reference())
            gui_tool_utils.redraw3DView()

        # ── per-face setup ───────────────────────────────────────────────

        def enter_face(self):
            cur = SESSION.current()
            if cur is None:
                return
            face, obj_name, frame, ext = cur
            self._look_at(face, frame)
            try:
                self.wp.align_to_face(face)
            except Exception:
                pass
            FreeCAD.Console.PrintMessage(
                f"[CPPattern] Face {SESSION.index + 1}/{len(SESSION.faces)} "
                f"on {obj_name} — {ext[2] - ext[0]:.2f} x {ext[3] - ext[1]:.2f} mm.\n")
            # A new face means new extents, so start the preview from its
            # reference rather than from a cursor position that belonged to
            # the previous face's coordinate system.
            self.point = SESSION.reference()
            self._recompute(self.point)

        def _look_at(self, face, frame):
            """Swing the camera round to look straight at the face."""
            try:
                c = face_symmetry.face_center_2d(face, frame)
                n = frame.normal_at(*c) if c else face.normalAt(0, 0)
                view = FreeCADGui.ActiveDocument.ActiveView
                # Look ALONG the inward normal, i.e. from outside the part.
                view.setViewDirection(FreeCAD.Vector(-n.x, -n.y, -n.z))
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"[CPPattern] could not align the view: {exc}\n")

        # ── event loop ───────────────────────────────────────────────────

        def action(self, arg):
            if arg["Type"] == "SoKeyboardEvent":
                key = (arg.get("Key", "") or "").upper()
                if key == "ESCAPE":
                    self.finish(abort=True)
                elif key == "RETURN" or key == "ENTER":
                    self.finish(abort=False)
                elif key == "X":
                    SESSION.set_mode(move=point_pattern.MOVE_U)
                    self._announce_mode()
                elif key == "Y":
                    SESSION.set_mode(move=point_pattern.MOVE_V)
                    self._announce_mode()
                elif key == "F":
                    SESSION.set_mode(move=point_pattern.MOVE_FREE)
                    self._announce_mode()
                elif key == "M":
                    SESSION.set_mode(snap=point_pattern.next_snap(SESSION.snap))
                    self._announce_mode()
                elif key == "N":
                    SESSION.next_face()      # calls back into enter_face()
                elif key == "BACKSPACE":
                    SESSION.undo_last()
                return

            if arg["Type"] == "SoLocation2Event":
                self.point = self._cursor_2d(arg)
                self._recompute(self.point)
                gui_tool_utils.redraw3DView()
                return

            if (arg["Type"] == "SoMouseButtonEvent"
                    and arg["State"] == "DOWN" and arg["Button"] == "BUTTON1"):
                if self.preview is None:
                    return
                # Document changes must not happen inside the Coin callback,
                # so the placement is deferred exactly like the routers' bake.
                pt = (round(self.preview[0], 6), round(self.preview[1], 6))
                self.commit("Place Contact Point", [
                    "from wirebond.ContactPointPatternCommand import SESSION",
                    f"SESSION.place({pt!r})",
                ])

        def _announce_mode(self):
            names = {point_pattern.MOVE_FREE: "free",
                     point_pattern.MOVE_U: "locked to X",
                     point_pattern.MOVE_V: "locked to Y"}
            snaps = {point_pattern.SNAP_NONE: "no snap",
                     point_pattern.SNAP_MID_U: "snap X to middle",
                     point_pattern.SNAP_MID_V: "snap Y to middle",
                     point_pattern.SNAP_MID_BOTH: "snap to centre"}
            FreeCAD.Console.PrintMessage(
                f"[CPPattern] Movement {names.get(SESSION.move, '?')}, "
                f"{snaps.get(SESSION.snap, '?')}.\n")
            self._recompute(self.point)

        # ── cursor / preview ─────────────────────────────────────────────

        def _cursor_2d(self, arg):
            cur = SESSION.current()
            if cur is None:
                return None
            frame = cur[2]
            pos = arg.get("Position")
            if pos:
                try:
                    info = self.view.getObjectInfo((pos[0], pos[1]))
                except Exception:
                    info = None
                if info and all(k in info for k in ("x", "y", "z")):
                    hit = FreeCAD.Vector(info["x"], info["y"], info["z"])
                    # getObjectInfo reports whatever is under the cursor —
                    # including a contact point placed a moment ago, or an
                    # unrelated body in front of the face. Taking that
                    # blindly makes the preview jump somewhere the user is
                    # not pointing, so only a hit that really lies on THIS
                    # face is trusted; anything else falls through to the
                    # working-plane projection below.
                    on_face = True
                    try:
                        on_face = frame.distance_to_surface(hit) <= _ON_FACE_BAND_MM
                    except Exception:
                        on_face = True
                    if on_face:
                        xy = frame.to_2d(hit, near=self.point)
                        if xy is not None:
                            return xy
            try:
                pt, _ctrl, _i = gui_tool_utils.get_point(self, arg)
            except Exception:
                pt = None
            if pt is None:
                return None
            return frame.to_2d(pt, near=self.point)

        def _recompute(self, cursor2d):
            cur = SESSION.current()
            if cur is None or cursor2d is None:
                self.preview = None
                for t in (self.guide_u, self.guide_v):
                    t.off()
                if self._marker is not None:
                    self._marker.numPoints = 0
                return
            face, _obj_name, frame, ext = cur
            ref = SESSION.reference()
            self.preview = point_pattern.constrain(
                cursor2d, ref, ext, SESSION.move, SESSION.snap)
            self._draw(frame, ext)
            if SESSION.dialog is not None:
                try:
                    SESSION.dialog.sync_point(self.preview)
                except Exception:
                    pass

        def _draw(self, frame, ext):
            segs = point_pattern.crosshair_segments(self.preview, ext)
            for tracker, seg in zip((self.guide_u, self.guide_v), segs):
                pts3 = face_symmetry.to_world(list(seg), frame)
                if len(pts3) < 2:
                    tracker.off()
                    continue
                tracker.line.numVertices.setValue(len(pts3))
                tracker.updateFromPointlist(pts3)
                tracker.on()

            # The point marker itself — one screen-space dot, so it looks the
            # same on any face at any zoom.
            if self._marker is not None:
                world = face_symmetry.to_world([self.preview], frame)
                if world:
                    self._marker_coords.point.setValue(
                        world[0].x, world[0].y, world[0].z)
                    self._marker.numPoints = 1
                else:
                    self._marker.numPoints = 0

        # ── finish ───────────────────────────────────────────────────────

        def finish(self, cont=False, abort=False):
            if getattr(self, "call", None) is not None and getattr(self, "view", None) is not None:
                try:
                    self.end_callbacks(self.call)
                except Exception:
                    pass
            self.call = None
            for t in (getattr(self, "guide_u", None), getattr(self, "guide_v", None)):
                if t is not None:
                    try:
                        t.finalize()
                    except Exception:
                        pass
            sep = getattr(self, "_marker_sep", None)
            if sep is not None:
                try:
                    self.view.getSceneGraph().removeChild(sep)
                except Exception:
                    pass
                self._marker_sep = None
                self._marker = None
            if SESSION.is_active:
                # Ending the session touches the document (Abort deletes the
                # markers), so it is deferred out of the callback too.
                self.commit("Contact Point Pattern", [
                    "from wirebond.ContactPointPatternCommand import SESSION",
                    f"SESSION.end(keep={not abort!r})",
                ])
            SESSION.tool = None
            super().finish()

    return _Tool()


# ── commands ─────────────────────────────────────────────────────────────────

class ContactPointPatternCommand:
    """Start the guided copy-onto-other-faces session."""

    def GetResources(self):
        return {
            "MenuText": "Contact Point Pattern",
            "ToolTip": (
                "Copy a contact point onto other faces, one guided click at\n"
                "a time.\n\n"
                "Select the contact point AND the target faces, then start.\n"
                "The view aligns to each face; a dashed cross-hair previews\n"
                "the position. X / Y lock the movement axis, F frees it,\n"
                "M cycles the middle snaps, N moves to the next face,\n"
                "Backspace undoes, Enter confirms, Esc aborts."
            ),
            "Pixmap": get_icon("Contact_Point_Pattern.svg"),
        }

    def Activated(self):
        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        if SESSION.is_active:
            FreeCAD.Console.PrintWarning(
                "[CPPattern] A pattern session is already running — confirm or "
                "abort it first.\n")
            return
        source, faces = _read_selection()
        if not faces:
            FreeCAD.Console.PrintError(
                "[CPPattern] Select at least one target FACE — Ctrl+click the "
                "faces to place points on. Selecting an existing contact "
                "point as well copies its position; without one, points start "
                "from each face's centre.\n")
            return
        if not SESSION.start(doc, source, faces):
            return
        tool = _make_tool()
        if tool is None:
            SESSION.end(keep=False)
            return
        SESSION.tool = tool
        tool.Activated()
        # Opened after the tool so the dialog can show its first preview.
        try:
            SESSION.dialog = _PatternDialog(FreeCADGui.getMainWindow())
            SESSION.dialog.show()
            if tool.preview is not None:
                SESSION.dialog.sync_point(tool.preview)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[CPPattern] could not open the dialog: {exc}\n")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


def _stop_tool(abort: bool):
    """Finish the running Draft tool, if any, then end the session."""
    tool = SESSION.tool
    if tool is not None:
        try:
            tool.finish(abort=abort)
            return
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[CPPattern] stopping the tool: {exc}\n")
    SESSION.end(keep=not abort)


class ConfirmPatternCommand:
    def GetResources(self):
        return {
            "MenuText": "Confirm Pattern",
            "ToolTip": "Keep every contact point placed in this session and end it.",
            "Pixmap": get_icon("Confirm_Trace.svg"),
        }

    def Activated(self):
        _stop_tool(abort=False)

    def IsActive(self):
        return SESSION.is_active


class UndoPatternCommand:
    def GetResources(self):
        return {
            "MenuText": "Undo Last Point",
            "ToolTip": "Remove the contact point placed most recently in this session.",
            "Pixmap": get_icon("Undo_Point.svg"),
        }

    def Activated(self):
        SESSION.undo_last()

    def IsActive(self):
        return SESSION.is_active and bool(SESSION.placed)


class AbortPatternCommand:
    def GetResources(self):
        return {
            "MenuText": "Abort Pattern",
            "ToolTip": ("Remove every contact point placed in this session and "
                         "restore the original state."),
            "Pixmap": get_icon("Abort_Trace.svg"),
        }

    def Activated(self):
        _stop_tool(abort=True)

    def IsActive(self):
        return SESSION.is_active


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ContactPointPatternCommand", ContactPointPatternCommand())
    FreeCADGui.addCommand("ConfirmPatternCommand", ConfirmPatternCommand())
    FreeCADGui.addCommand("UndoPatternCommand", UndoPatternCommand())
    FreeCADGui.addCommand("AbortPatternCommand", AbortPatternCommand())
