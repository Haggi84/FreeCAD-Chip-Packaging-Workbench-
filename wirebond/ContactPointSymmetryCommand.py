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


def _picked_face():
    """(face_in_world, object_name) for the face currently selected in the
    3-D view, or (None, None)."""
    try:
        sel = FreeCADGui.Selection.getSelectionEx()
    except Exception:
        sel = []
    for s in sel:
        obj = s.Object
        if not hasattr(obj, "Shape"):
            continue
        for sub_name in (s.SubElementNames or []):
            if not sub_name.startswith("Face"):
                continue
            try:
                face = obj.Shape.getElement(sub_name)
            except Exception:
                continue
            parent = _parent_placement_of(obj)
            if not parent.isIdentity():
                face = face.copy()
                face.transformShape(parent.toMatrix())
            return face, obj.Name
    return None, None


def _contact_points_on(doc, frame, band_mm: float = _ON_FACE_BAND_MM):
    """[(marker, (u, v)), ...] for every contact point lying on the frame's
    face. Markers belonging to some other face are excluded, so a symmetry
    operation never drags in points that are not part of this pattern."""
    out = []
    near = None
    for o in doc.Objects:
        if not getattr(o, "IsContactPoint", False):
            continue
        pos = getattr(o, "ContactPoint", None)
        if pos is None:
            continue
        p = FreeCAD.Vector(pos)
        try:
            if frame.distance_to_surface(p) > band_mm:
                continue
        except Exception:
            continue
        xy = frame.to_2d(p, near=near)
        if xy is None:
            continue
        out.append((o, xy))
        near = xy
    return out


class ContactPointSymmetryDialog(QtWidgets.QDialog):
    """Modeless dialog — stays open so several operations can be applied to
    the same face, matching gds.ChipTransformCommand's dialog idiom."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Contact Point Symmetry")
        self.setModal(False)
        self.setMinimumWidth(360)

        self._face = None
        self._face_obj = None
        self._frame = None

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
        face, obj_name = _picked_face()
        if face is None:
            self._face = self._face_obj = self._frame = None
            self._lbl_face.setText(
                "<b>No face selected.</b> Click a face in the 3D view, then "
                "press '↺ Read current FreeCAD selection'.")
            self._lbl_face.setStyleSheet("color: #cc8844;")
            return
        try:
            frame = routing_frame.SurfaceFrame(face)
        except Exception as exc:
            self._lbl_face.setText(f"<b>Unusable face:</b> {exc}")
            return
        self._face, self._face_obj, self._frame = face, obj_name, frame

        doc = FreeCAD.activeDocument()
        n = len(_contact_points_on(doc, frame)) if doc else 0
        ext = face_symmetry.outer_extent_2d(face, frame)
        size = (f"{ext[2] - ext[0]:.2f} x {ext[3] - ext[1]:.2f} mm"
                if ext else "unknown size")
        self._lbl_face.setText(
            f"<b>Face on {obj_name}</b> ({size}) — "
            f"{n} contact point(s) currently on it.")
        self._lbl_face.setStyleSheet("color: #88cc88;")

    def _context(self):
        """(doc, face, frame, centre) or None, warning the user if not ready."""
        doc = FreeCAD.activeDocument()
        if doc is None:
            return None
        if self._frame is None or self._face is None:
            QtWidgets.QMessageBox.warning(
                self, "No face selected",
                "Click a face in the 3D view and press "
                "'↺ Read current FreeCAD selection' first.")
            return None
        center = face_symmetry.face_center_2d(self._face, self._frame)
        if center is None:
            QtWidgets.QMessageBox.warning(
                self, "Unusable face",
                "The centre of that face could not be determined.")
            return None
        return doc, self._face, self._frame, center

    def _mode_from_combo(self):
        return (face_symmetry.MIRROR_U, face_symmetry.MIRROR_V,
                face_symmetry.MIRROR_BOTH)[self._sym_mode.currentIndex()]

    # ── marker creation ───────────────────────────────────────────────────

    def _marker_kind(self, doc, existing):
        """'gds' or 'package' — matched to the points already on the face so
        mirroring die pads yields die pads, falling back to the source
        object's own group when the face is still empty."""
        if existing:
            n_gds = sum(1 for m, _ in existing if m.Name.startswith("ContactPoint_"))
            return "gds" if n_gds * 2 >= len(existing) else "package"
        return _get_source_assembly(doc, self._face_obj or "")

    def _create(self, doc, pts2d, kind, frame):
        """Create markers for frame-space points that lie within the face.
        Returns (n_created, n_outside)."""
        created = 0
        outside = 0
        for p in pts2d:
            if not face_symmetry.is_inside_face(p, self._face, frame):
                outside += 1
                continue
            world = face_symmetry.to_world([p], frame)
            if not world:
                outside += 1
                continue
            try:
                if kind == "gds":
                    m = _create_gds_marker(doc, self._face_obj or "",
                                            world[0], _next_gds_index(doc))
                else:
                    m = _create_housing_marker(doc, self._face_obj or "",
                                                world[0], _next_housing_index(doc))
                _add_to_contact_points_group(doc, m)
                created += 1
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"[CPSymmetry] marker creation failed: {exc}\n")
        return created, outside

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
        doc, _face, frame, center = ctx
        existing = _contact_points_on(doc, frame)
        if not existing:
            QtWidgets.QMessageBox.information(
                self, "Nothing to mirror",
                "There are no contact points on that face yet. Place some "
                "first, or use 'Generate a symmetric pattern'.")
            return

        pts = [xy for _m, xy in existing]
        new_pts = face_symmetry.mirror_points_2d(pts, center, mode,
                                                  tol=self._tol.value())
        if not new_pts:
            self._lbl_status.setText(
                "Already symmetric — nothing to add.")
            return

        kind = self._marker_kind(doc, existing)
        doc.openTransaction("Mirror Contact Points")
        created, outside = self._create(doc, new_pts, kind, frame)
        doc.commitTransaction()
        msg = f"Mirrored: {created} contact point(s) added."
        if outside:
            msg += (f" {outside} image(s) fell outside the face and were "
                    "skipped.")
        self._finish(doc, msg)

    def _do_symmetrize(self):
        ctx = self._context()
        if ctx is None:
            return
        doc, _face, frame, center = ctx
        mode = self._mode_from_combo()
        existing = _contact_points_on(doc, frame)
        if not existing:
            QtWidgets.QMessageBox.information(
                self, "Nothing to symmetrize",
                "There are no contact points on that face yet.")
            return

        pts = [xy for _m, xy in existing]
        result, n_snapped, n_added = face_symmetry.symmetrize_points_2d(
            pts, center, mode, tol=self._tol.value())

        doc.openTransaction("Symmetrize Contact Points")
        moved = 0
        # The first len(existing) entries correspond 1:1 to the existing
        # markers (symmetrize preserves order), so those are MOVED rather
        # than duplicated — the whole point of tidying rather than adding.
        for (marker, old_xy), new_xy in zip(existing, result):
            if abs(new_xy[0] - old_xy[0]) < 1e-9 and abs(new_xy[1] - old_xy[1]) < 1e-9:
                continue
            world = face_symmetry.to_world([new_xy], frame)
            if not world:
                continue
            try:
                import Part
                marker.Shape = Part.Vertex(world[0].x, world[0].y, world[0].z)
                marker.ContactPoint = world[0]
                moved += 1
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"[CPSymmetry] moving {marker.Name} failed: {exc}\n")

        kind = self._marker_kind(doc, existing)
        created, outside = self._create(doc, result[len(existing):], kind, frame)
        doc.commitTransaction()

        msg = (f"Symmetrized: {moved} point(s) moved onto exact positions, "
               f"{created} added.")
        if outside:
            msg += f" {outside} image(s) fell outside the face and were skipped."
        if not moved and not created:
            msg = "Already symmetric — nothing changed."
        self._finish(doc, msg)

    def _do_generate(self):
        ctx = self._context()
        if ctx is None:
            return
        doc, face, frame, _center = ctx
        ext = face_symmetry.outer_extent_2d(face, frame)
        if ext is None:
            QtWidgets.QMessageBox.warning(
                self, "Unusable face", "That face's extent could not be measured.")
            return

        margin = self._margin.value()
        pitch = self._pitch.value() or None
        if self._gen_mode.currentIndex() == 0:
            pts = face_symmetry.symmetric_ring_2d(
                ext, self._n_u.value(), margin, pitch)
        else:
            pts = face_symmetry.symmetric_grid_2d(
                ext, self._n_u.value(), self._n_v.value(), margin, pitch, pitch)
        if not pts:
            QtWidgets.QMessageBox.information(
                self, "Nothing generated",
                "That pattern has no points — check the counts.")
            return

        existing = _contact_points_on(doc, frame)
        kind = self._marker_kind(doc, existing)
        doc.openTransaction("Generate Symmetric Contact Points")
        created, outside = self._create(doc, pts, kind, frame)
        doc.commitTransaction()

        msg = f"Generated: {created} contact point(s)."
        if outside:
            msg += (f" {outside} fell outside the face (or beyond it at that "
                    "pitch) and were skipped.")
        self._finish(doc, msg)


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
