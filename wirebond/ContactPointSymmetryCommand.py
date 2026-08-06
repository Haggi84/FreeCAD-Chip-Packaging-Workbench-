# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Symmetric contact points on a face — mirror, symmetrize, or generate.

Three operations, all about the FACE'S OWN CENTRE (see core.face_symmetry
for why that is the outer-boundary midpoint rather than the area centroid
or the parameter-range middle):

  Mirror       Reflect the contact points already on the face across its U
               axis, its V axis, or both, creating only the markers that
               are actually missing. For "I placed one side by hand, now do
               the other."
  Symmetrize   Tidy a hand-placed set: pull nearly-symmetric pairs onto
               exactly symmetric positions, snap a lone near-axis point onto
               the axis, then add whatever images are still missing.
  Generate     Lay down a fresh symmetric pattern — a bond-pad ring (N per
               side) or a grid (N x M) — inset from the face edge, with an
               optional exact pitch.

Works on ANY face of any body at any orientation, because all of it happens
in core.routing_frame.SurfaceFrame's metric 2-D space rather than in world
XY. Markers are created with the same schema and colours the grid-based
placement tool uses, so they are interchangeable with contact points from
every other source.
"""

import os
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

import core.face_symmetry as face_symmetry
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

# How far off the face a contact point may sit and still count as "on" it.
_ON_FACE_BAND_MM = 1.0


def _parent_placement_of(obj):
    """Placement of *obj*'s containers only. A Part::Feature's Shape already
    carries its own Placement but not an App::Part container's, so this is
    what must be applied to geometry read out of obj.Shape (verified against
    FreeCAD 1.1)."""
    pl = FreeCAD.Placement()
    current = obj
    while current.InList:
        parent = current.InList[0]
        if hasattr(parent, "Placement"):
            pl = parent.Placement.multiply(pl)
        current = parent
    return pl


def _picked_faces():
    """
    [(face_in_world, object_name), ...] for EVERY face currently selected in
    the 3-D view.

    Several faces at once is the normal case, not the exception: a package
    carries contact points on all four flanks, and each of those wants its
    own symmetric pattern in one go.
    """
    try:
        sel = FreeCADGui.Selection.getSelectionEx()
    except Exception:
        sel = []
    out = []
    for s in sel:
        obj = s.Object
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
            out.append((face, obj.Name))
    return out


def _contact_points_on(doc, frame, band_mm: float = _ON_FACE_BAND_MM):
    """[(marker, (u, v)), ...] for every contact point lying on the frame's
    face. Markers belonging to some other face are excluded, so a symmetry
    operation never drags in points that are not part of this pattern."""
    return _assign_points_to_faces(doc, [frame], band_mm)[0]


def _assign_points_to_faces(doc, frames, band_mm: float = _ON_FACE_BAND_MM):
    """
    One bucket of [(marker, (u, v)), ...] per frame, assigning each contact
    point to the NEAREST of the given faces.

    Nearest-wins rather than "every face it is near to": faces that meet at
    an edge both have points sitting within the band of the other, and
    without this a shared-edge point would be mirrored once per face — and
    worse, MOVED twice by symmetrize, the second move undoing the first.
    """
    buckets = [[] for _ in frames]
    if doc is None or not frames:
        return buckets
    for o in doc.Objects:
        if not getattr(o, "IsContactPoint", False):
            continue
        pos = getattr(o, "ContactPoint", None)
        if pos is None:
            continue
        p = FreeCAD.Vector(pos)
        best_i, best_d = None, band_mm
        for i, frame in enumerate(frames):
            try:
                d = frame.distance_to_surface(p)
            except Exception:
                continue
            if d <= best_d:
                best_d, best_i = d, i
        if best_i is None:
            continue
        xy = frames[best_i].to_2d(p)
        if xy is None:
            continue
        buckets[best_i].append((o, xy))
    return buckets


class ContactPointSymmetryDialog(QtWidgets.QDialog):
    """Modeless dialog — stays open so several operations can be applied to
    the same face, matching gds.ChipTransformCommand's dialog idiom."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Contact Point Symmetry")
        self.setModal(False)
        self.setMinimumWidth(360)

        # [(face, object_name, frame), ...] — every selected face, each
        # carrying its own centre and axes.
        self._faces = []

        self._build_ui()
        self._read_selection()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)

        self._lbl_face = QtWidgets.QLabel()
        self._lbl_face.setWordWrap(True)
        root.addWidget(self._lbl_face)

        refresh = QtWidgets.QPushButton("↺  Read current FreeCAD selection")
        refresh.setToolTip("Click a FACE in the 3D view, then press this to "
                            "use it as the symmetry reference.")
        refresh.clicked.connect(self._read_selection)
        root.addWidget(refresh)

        tol_row = QtWidgets.QHBoxLayout()
        tol_row.addWidget(QtWidgets.QLabel("Tolerance:"))
        self._tol = QtWidgets.QDoubleSpinBox()
        self._tol.setRange(0.0, 50.0)
        self._tol.setDecimals(3)
        self._tol.setValue(0.05)
        self._tol.setSuffix(" mm")
        self._tol.setToolTip(
            "How far a point may be from exact symmetry and still count as\n"
            "'meant to be symmetric'. Clicked points are never perfect, so\n"
            "without this every one of them would merely gain a near-\n"
            "duplicate neighbour instead of being tidied up."
        )
        tol_row.addWidget(self._tol)
        tol_row.addStretch()
        root.addLayout(tol_row)

        # ── mirror ───────────────────────────────────────────────────────
        m_grp = QtWidgets.QGroupBox("Mirror existing points")
        m_lay = QtWidgets.QHBoxLayout(m_grp)
        for label, mode, tip in (
            ("Mirror U", face_symmetry.MIRROR_U,
             "Reflect left <-> right about the face's centre line."),
            ("Mirror V", face_symmetry.MIRROR_V,
             "Reflect near <-> far about the face's centre line."),
            ("Mirror both", face_symmetry.MIRROR_BOTH,
             "Reflect about both axes at once — full four-fold symmetry."),
        ):
            b = QtWidgets.QPushButton(label)
            b.setToolTip(tip + "\nOnly the missing markers are created.")
            b.clicked.connect(lambda _c=False, m=mode: self._do_mirror(m))
            m_lay.addWidget(b)
        root.addWidget(m_grp)

        # ── symmetrize ───────────────────────────────────────────────────
        s_grp = QtWidgets.QGroupBox("Symmetrize what is already placed")
        s_lay = QtWidgets.QHBoxLayout(s_grp)
        self._sym_mode = QtWidgets.QComboBox()
        self._sym_mode.addItems(["About U axis", "About V axis", "About both axes"])
        self._sym_mode.setCurrentIndex(2)
        s_lay.addWidget(self._sym_mode)
        sym_btn = QtWidgets.QPushButton("Symmetrize")
        sym_btn.setToolTip(
            "Snap nearly-symmetric pairs onto exactly symmetric positions,\n"
            "pull a lone near-axis point onto the axis, and add any missing\n"
            "mirror images. Existing markers are MOVED, not duplicated."
        )
        sym_btn.clicked.connect(self._do_symmetrize)
        s_lay.addWidget(sym_btn)
        root.addWidget(s_grp)

        # ── generate ─────────────────────────────────────────────────────
        g_grp = QtWidgets.QGroupBox("Generate a symmetric pattern")
        g_lay = QtWidgets.QFormLayout(g_grp)

        self._gen_mode = QtWidgets.QComboBox()
        self._gen_mode.addItems(["Ring (N per side)", "Grid (N x M)"])
        self._gen_mode.currentIndexChanged.connect(self._sync_gen_ui)
        g_lay.addRow("Pattern:", self._gen_mode)

        self._n_u = QtWidgets.QSpinBox()
        self._n_u.setRange(0, 200)
        self._n_u.setValue(5)
        g_lay.addRow("Count (per side / U):", self._n_u)

        self._n_v = QtWidgets.QSpinBox()
        self._n_v.setRange(0, 200)
        self._n_v.setValue(3)
        g_lay.addRow("Count (V):", self._n_v)
        self._lbl_n_v = g_lay.labelForField(self._n_v)

        self._margin = QtWidgets.QDoubleSpinBox()
        self._margin.setRange(0.0, 1000.0)
        self._margin.setDecimals(3)
        self._margin.setValue(1.0)
        self._margin.setSuffix(" mm")
        self._margin.setToolTip("Inset from the face's edge.")
        g_lay.addRow("Edge margin:", self._margin)

        self._pitch = QtWidgets.QDoubleSpinBox()
        self._pitch.setRange(0.0, 1000.0)
        self._pitch.setDecimals(3)
        self._pitch.setValue(0.0)
        self._pitch.setSuffix(" mm")
        self._pitch.setToolTip(
            "Exact spacing between neighbouring points, centred on the face.\n"
            "0 spreads them evenly across the available span instead."
        )
        g_lay.addRow("Pitch (0 = spread):", self._pitch)

        gen_btn = QtWidgets.QPushButton("Generate")
        gen_btn.setToolTip("Create the pattern as new contact points. "
                            "Existing points are left untouched.")
        gen_btn.clicked.connect(self._do_generate)
        g_lay.addRow("", gen_btn)
        root.addWidget(g_grp)
        self._sync_gen_ui()

        self._lbl_status = QtWidgets.QLabel("")
        self._lbl_status.setWordWrap(True)
        self._lbl_status.setStyleSheet("font-size: 11px; color: #888;")
        root.addWidget(self._lbl_status)

        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.close)
        root.addWidget(close)

    def _sync_gen_ui(self):
        is_grid = self._gen_mode.currentIndex() == 1
        self._n_v.setVisible(is_grid)
        if self._lbl_n_v is not None:
            self._lbl_n_v.setVisible(is_grid)

    # ── selection / context ───────────────────────────────────────────────

    def _read_selection(self):
        picked = _picked_faces()
        self._faces = []
        for face, obj_name in picked:
            try:
                self._faces.append((face, obj_name, routing_frame.SurfaceFrame(face)))
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"[CPSymmetry] skipping an unusable face on {obj_name}: {exc}\n")

        if not self._faces:
            self._lbl_face.setText(
                "<b>No face selected.</b> Click one or more faces in the 3D "
                "view (Ctrl+click to add), then press "
                "'↺ Read current FreeCAD selection'.")
            self._lbl_face.setStyleSheet("color: #cc8844;")
            return

        doc = FreeCAD.activeDocument()
        buckets = (_assign_points_to_faces(doc, [f[2] for f in self._faces])
                   if doc else [[] for _ in self._faces])
        n_pts = sum(len(b) for b in buckets)
        objs = sorted({name for _f, name, _fr in self._faces})
        where = objs[0] if len(objs) == 1 else f"{len(objs)} objects"
        if len(self._faces) == 1:
            face, _name, frame = self._faces[0]
            ext = face_symmetry.outer_extent_2d(face, frame)
            size = (f"{ext[2] - ext[0]:.2f} x {ext[3] - ext[1]:.2f} mm"
                    if ext else "unknown size")
            self._lbl_face.setText(
                f"<b>1 face on {where}</b> ({size}) — "
                f"{n_pts} contact point(s) on it.")
        else:
            self._lbl_face.setText(
                f"<b>{len(self._faces)} faces on {where}</b> — {n_pts} contact "
                "point(s) across them. Each face is treated about its OWN "
                "centre.")
        self._lbl_face.setStyleSheet("color: #88cc88;")

    def _context(self):
        """(doc, faces) or None, warning the user if not ready.
        *faces* is [(face, object_name, frame, centre), ...] — every selected
        face with its own centre already resolved."""
        doc = FreeCAD.activeDocument()
        if doc is None:
            return None
        if not self._faces:
            QtWidgets.QMessageBox.warning(
                self, "No face selected",
                "Click one or more faces in the 3D view (Ctrl+click to add) "
                "and press '↺ Read current FreeCAD selection' first.")
            return None
        usable = []
        for face, obj_name, frame in self._faces:
            center = face_symmetry.face_center_2d(face, frame)
            if center is None:
                FreeCAD.Console.PrintWarning(
                    f"[CPSymmetry] centre of a face on {obj_name} could not be "
                    "determined; skipping it.\n")
                continue
            usable.append((face, obj_name, frame, center))
        if not usable:
            QtWidgets.QMessageBox.warning(
                self, "Unusable faces",
                "The centre of the selected face(s) could not be determined.")
            return None
        return doc, usable

    def _mode_from_combo(self):
        return (face_symmetry.MIRROR_U, face_symmetry.MIRROR_V,
                face_symmetry.MIRROR_BOTH)[self._sym_mode.currentIndex()]

    # ── marker creation ───────────────────────────────────────────────────

    def _marker_kind(self, doc, existing, obj_name):
        """'gds' or 'package' — matched to the points already on THIS face so
        mirroring die pads yields die pads, falling back to the source
        object's own group when the face is still empty."""
        if existing:
            n_gds = sum(1 for m, _ in existing if m.Name.startswith("ContactPoint_"))
            return "gds" if n_gds * 2 >= len(existing) else "package"
        return _get_source_assembly(doc, obj_name or "")

    def _create(self, doc, pts2d, kind, face, obj_name, frame):
        """Create markers for frame-space points that lie within *face*.
        Returns (n_created, n_outside)."""
        created = 0
        outside = 0
        for p in pts2d:
            if not face_symmetry.is_inside_face(p, face, frame):
                outside += 1
                continue
            world = face_symmetry.to_world([p], frame)
            if not world:
                outside += 1
                continue
            try:
                if kind == "gds":
                    m = _create_gds_marker(doc, obj_name or "",
                                            world[0], _next_gds_index(doc))
                else:
                    m = _create_housing_marker(doc, obj_name or "",
                                                world[0], _next_housing_index(doc))
                _add_to_contact_points_group(doc, m)
                created += 1
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"[CPSymmetry] marker creation failed: {exc}\n")
        return created, outside

    def _summary(self, verb: str, created: int, outside: int,
                 n_faces: int, extra: str = "") -> str:
        msg = f"{verb}: {created} contact point(s){extra}"
        if n_faces > 1:
            msg += f" across {n_faces} faces"
        msg += "."
        if outside:
            msg += (f" {outside} image(s) fell outside their face and were "
                    "skipped.")
        return msg

    def _finish(self, doc, message: str):
        doc.recompute()
        _refresh_contact_panel()
        self._lbl_status.setText(message)
        FreeCAD.Console.PrintMessage(f"[CPSymmetry] {message}\n")
        self._read_selection()

    # ── operations ────────────────────────────────────────────────────────

    def _do_mirror(self, mode: str):
        ctx = self._context()
        if ctx is None:
            return
        doc, faces = ctx
        buckets = _assign_points_to_faces(doc, [f[2] for f in faces])
        if not any(buckets):
            QtWidgets.QMessageBox.information(
                self, "Nothing to mirror",
                "There are no contact points on the selected face(s) yet. "
                "Place some first, or use 'Generate a symmetric pattern'.")
            return

        tol = self._tol.value()
        created = outside = 0
        touched = 0
        doc.openTransaction("Mirror Contact Points")
        for (face, obj_name, frame, center), existing in zip(faces, buckets):
            if not existing:
                continue        # a face with no points of its own is left alone
            new_pts = face_symmetry.mirror_points_2d(
                [xy for _m, xy in existing], center, mode, tol=tol)
            if not new_pts:
                continue
            kind = self._marker_kind(doc, existing, obj_name)
            c, o = self._create(doc, new_pts, kind, face, obj_name, frame)
            created += c
            outside += o
            touched += 1
        doc.commitTransaction()

        if not created and not outside:
            self._lbl_status.setText("Already symmetric — nothing to add.")
            return
        self._finish(doc, self._summary("Mirrored", created, outside, touched,
                                         " added"))

    def _do_symmetrize(self):
        ctx = self._context()
        if ctx is None:
            return
        doc, faces = ctx
        mode = self._mode_from_combo()
        buckets = _assign_points_to_faces(doc, [f[2] for f in faces])
        if not any(buckets):
            QtWidgets.QMessageBox.information(
                self, "Nothing to symmetrize",
                "There are no contact points on the selected face(s) yet.")
            return

        import Part
        tol = self._tol.value()
        moved = created = outside = 0
        touched = 0
        doc.openTransaction("Symmetrize Contact Points")
        for (face, obj_name, frame, center), existing in zip(faces, buckets):
            if not existing:
                continue
            result, _n_snapped, _n_added = face_symmetry.symmetrize_points_2d(
                [xy for _m, xy in existing], center, mode, tol=tol)

            # The first len(existing) entries correspond 1:1 to the existing
            # markers (symmetrize preserves order), so those are MOVED rather
            # than duplicated — the whole point of tidying rather than adding.
            for (marker, old_xy), new_xy in zip(existing, result):
                if (abs(new_xy[0] - old_xy[0]) < 1e-9
                        and abs(new_xy[1] - old_xy[1]) < 1e-9):
                    continue
                world = face_symmetry.to_world([new_xy], frame)
                if not world:
                    continue
                try:
                    marker.Shape = Part.Vertex(world[0].x, world[0].y, world[0].z)
                    marker.ContactPoint = world[0]
                    moved += 1
                except Exception as exc:
                    FreeCAD.Console.PrintWarning(
                        f"[CPSymmetry] moving {marker.Name} failed: {exc}\n")

            kind = self._marker_kind(doc, existing, obj_name)
            c, o = self._create(doc, result[len(existing):], kind,
                                 face, obj_name, frame)
            created += c
            outside += o
            touched += 1
        doc.commitTransaction()

        if not moved and not created and not outside:
            self._lbl_status.setText("Already symmetric — nothing changed.")
            return
        self._finish(doc, self._summary(
            "Symmetrized", created, outside, touched,
            f" added, {moved} moved onto exact positions"))

    def _do_generate(self):
        ctx = self._context()
        if ctx is None:
            return
        doc, faces = ctx
        margin = self._margin.value()
        pitch = self._pitch.value() or None
        is_ring = self._gen_mode.currentIndex() == 0
        buckets = _assign_points_to_faces(doc, [f[2] for f in faces])

        created = outside = 0
        touched = 0
        any_pattern = False
        doc.openTransaction("Generate Symmetric Contact Points")
        # The SAME pattern is laid onto every selected face, each about its
        # own centre and sized to its own extent — a package's four flanks
        # get matching pad rings in one go.
        for (face, obj_name, frame, _center), existing in zip(faces, buckets):
            ext = face_symmetry.outer_extent_2d(face, frame)
            if ext is None:
                FreeCAD.Console.PrintWarning(
                    f"[CPSymmetry] extent of a face on {obj_name} could not be "
                    "measured; skipping it.\n")
                continue
            if is_ring:
                pts = face_symmetry.symmetric_ring_2d(
                    ext, self._n_u.value(), margin, pitch)
            else:
                pts = face_symmetry.symmetric_grid_2d(
                    ext, self._n_u.value(), self._n_v.value(), margin,
                    pitch, pitch)
            if not pts:
                continue
            any_pattern = True
            kind = self._marker_kind(doc, existing, obj_name)
            c, o = self._create(doc, pts, kind, face, obj_name, frame)
            created += c
            outside += o
            touched += 1
        doc.commitTransaction()

        if not any_pattern:
            QtWidgets.QMessageBox.information(
                self, "Nothing generated",
                "That pattern has no points — check the counts.")
            return
        self._finish(doc, self._summary("Generated", created, outside, touched))


_dialog = None


class ContactPointSymmetryCommand:
    """Toggle the Contact Point Symmetry dialog."""

    def GetResources(self):
        return {
            "MenuText": "Contact Point Symmetry",
            "ToolTip": (
                "Mirror, symmetrize or generate contact points symmetrically\n"
                "about the centre of a selected face.\n\n"
                "Select a face in the 3D view, then choose an operation."
            ),
            "Pixmap": get_icon("Contact_Point_Symmetry.svg"),
        }

    def Activated(self):
        global _dialog
        if _dialog is None:
            _dialog = ContactPointSymmetryDialog(FreeCADGui.getMainWindow())
        else:
            _dialog._read_selection()
        _dialog.show()
        _dialog.raise_()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ContactPointSymmetryCommand", ContactPointSymmetryCommand())
