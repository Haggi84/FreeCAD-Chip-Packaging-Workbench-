# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Trace Routing — setup panel (Step 1 of the semi-automated routing tool).

A FreeCADGui.Control task panel, modeled on
wirebond.SetContactPointsOnFaceCommand._GridContactPanel: a plain QWidget
form with an Ok/Cancel button pair, two sub-phases.

  Phase FACES   Ctrl+click one or more faces in the 3D view — any planar
                surface (a PCB top face, a leadframe/package surface, ...),
                not hardcoded to any one object type.
  Phase PARAMS  Set grid spacing, trace width/thickness, clearance, and
                max bend angle.

Pressing OK in the PARAMS phase samples the grid on every selected face
and hands everything off to routing.TraceRoutingSession.trace_router,
which owns all further document mutation (grid markers, leg previews,
confirmed traces) for the rest of the interactive click-to-route session —
this panel's job ends once the session starts, matching the reasoning in
_GridContactPanel for why its own FACES phase is a task panel (needs live
3D-view selection) while parameter-only dialogs elsewhere in this codebase
(e.g. wirebond.WirebondConfigurator) are plain modal QDialogs instead.
"""

import os
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore, qenum_int

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

import core.trace_routing as trace_routing
from routing.TraceRoutingSession import trace_router

_PHASE_FACES  = "faces"
_PHASE_PARAMS = "params"


class _FacePickObserver:
    """Live face-selection-count feedback during the FACES phase — mirrors
    wirebond.SetContactPointsOnFaceCommand._PanelObserver's FACES handling."""

    def __init__(self, panel):
        self._panel = panel

    def addSelection(self, doc, obj, sub, pnt):
        if self._panel._phase == _PHASE_FACES:
            self._panel._refresh_face_count()

    def removeSelection(self, doc, obj, sub):
        if self._panel._phase == _PHASE_FACES:
            self._panel._refresh_face_count()

    def setSelection(self, doc):
        pass

    def clearSelection(self, doc):
        if self._panel._phase == _PHASE_FACES:
            self._panel._refresh_face_count()

    def setPreselection(self, *_):
        pass

    def removePreselection(self, *_):
        pass


class TraceRoutingSetupPanel:

    def __init__(self):
        self._phase = _PHASE_FACES
        self._active = True
        self._faces_data = []   # [(source_object_name, Part.Face), ...]

        self._observer = _FacePickObserver(self)
        FreeCADGui.Selection.addObserver(self._observer)

        self._build_form()

    # ── form ──────────────────────────────────────────────────────────────

    def _build_form(self):
        self.form = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(self.form)
        root.setSpacing(6)

        self._hint = QtWidgets.QLabel()
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        root.addWidget(sep)

        self._status = QtWidgets.QLabel()
        root.addWidget(self._status)

        self._next_btn = QtWidgets.QPushButton("Next: Set Parameters →")
        self._next_btn.clicked.connect(self._go_to_params)
        root.addWidget(self._next_btn)

        params_grp = QtWidgets.QGroupBox("Routing Parameters")
        p_lay = QtWidgets.QFormLayout(params_grp)

        self._spacing = QtWidgets.QDoubleSpinBox()
        self._spacing.setRange(0.01, 10.0)
        self._spacing.setDecimals(3)
        self._spacing.setValue(0.5)
        self._spacing.setSuffix(" mm")
        p_lay.addRow("Grid spacing:", self._spacing)

        self._width = QtWidgets.QDoubleSpinBox()
        self._width.setRange(0.01, 10.0)
        self._width.setDecimals(3)
        self._width.setValue(0.3)
        self._width.setSuffix(" mm")
        p_lay.addRow("Trace width:", self._width)

        self._thickness = QtWidgets.QDoubleSpinBox()
        self._thickness.setRange(0.001, 2.0)
        self._thickness.setDecimals(3)
        self._thickness.setValue(0.035)
        self._thickness.setSuffix(" mm")
        p_lay.addRow("Trace thickness:", self._thickness)

        self._clearance = QtWidgets.QDoubleSpinBox()
        self._clearance.setRange(0.0, 10.0)
        self._clearance.setDecimals(3)
        self._clearance.setValue(0.2)
        self._clearance.setSuffix(" mm")
        p_lay.addRow("Clearance:", self._clearance)

        self._max_bend = QtWidgets.QDoubleSpinBox()
        self._max_bend.setRange(1.0, 179.0)
        self._max_bend.setDecimals(1)
        self._max_bend.setValue(45.0)
        self._max_bend.setSuffix(" °")
        p_lay.addRow("Max bend angle:", self._max_bend)

        # Preferred trace HEADINGS — segments are biased to run along
        # multiples of the chosen step (the classic 45-degree PCB routing
        # grid, Manhattan, ...). A SOFT bias, so a congested board still
        # routes rather than failing when nothing on-grid fits.
        self._angle_pref = QtWidgets.QComboBox()
        self._angle_pref.addItems([
            "Any angle (no preference)",
            "45° grid (0/45/90/135)",
            "90° grid (Manhattan)",
            "30° grid (0/30/60/90/120/150)",
        ])
        self._angle_pref.setCurrentIndex(1)
        self._angle_pref.setToolTip(
            "Headings the copper should run along. Segments off this grid\n"
            "are penalised, so the router prefers routes that stay on it."
        )
        p_lay.addRow("Preferred trace angles:", self._angle_pref)

        self._angle_strength = QtWidgets.QDoubleSpinBox()
        self._angle_strength.setRange(0.0, 20.0)
        self._angle_strength.setDecimals(2)
        self._angle_strength.setValue(1.00)
        self._angle_strength.setSuffix(" mm/°")
        self._angle_strength.setToolTip(
            "How strongly to favour the preferred headings: extra routing cost\n"
            "per degree off the nearest allowed heading.\n"
            "Higher = straighter adherence but longer detours. 0 disables it."
        )
        p_lay.addRow("Angle preference strength:", self._angle_strength)

        self._corner_radius = QtWidgets.QDoubleSpinBox()
        self._corner_radius.setRange(0.0, 20.0)
        self._corner_radius.setDecimals(3)
        self._corner_radius.setValue(0.0)
        self._corner_radius.setSuffix(" mm")
        self._corner_radius.setToolTip(
            "Radius of the arc that replaces each sharp corner.\n"
            "0 keeps mitred (sharp) corners."
        )
        p_lay.addRow("Corner radius (rounding):", self._corner_radius)

        self._escape = QtWidgets.QDoubleSpinBox()
        self._escape.setRange(0.0, 50.0)
        self._escape.setDecimals(2)
        self._escape.setValue(0.0)
        self._escape.setSuffix(" mm")
        self._escape.setToolTip(
            "How far a trace may run inside the pad/copper it starts or ends on,\n"
            "so it can step off that pad without being allowed to travel along\n"
            "a large copper pour.\n"
            "0 = derive automatically from grid spacing, width and clearance."
        )
        p_lay.addRow("Max escape into start/end pad:", self._escape)

        root.addWidget(params_grp)
        self._params_grp = params_grp

        self._back_btn = QtWidgets.QPushButton("← Back to face selection")
        self._back_btn.clicked.connect(self._back_to_faces)
        root.addWidget(self._back_btn)

        root.addStretch()
        self._set_phase_ui()

    def _set_phase_ui(self):
        if self._phase == _PHASE_FACES:
            self._hint.setText(
                "<b>Step 1 — Select the routing surface</b><br>"
                "Hold <b>Ctrl</b> and left-click one or more faces "
                "(e.g. the PCB top face). Press Next when done."
            )
            self._next_btn.setVisible(True)
            self._params_grp.setVisible(False)
            self._back_btn.setVisible(False)
            self._refresh_face_count()
        else:
            self._hint.setText(
                "<b>Step 2 — Set routing parameters</b><br>"
                "Press <b>OK</b> to sample the grid and start the routing "
                "session — click a grid point in the 3D view to start a "
                "trace, click further points to route each next leg."
            )
            self._next_btn.setVisible(False)
            self._params_grp.setVisible(True)
            self._back_btn.setVisible(True)
            self._status.setText(f"{len(self._faces_data)} face(s) selected.")

    # ── phase FACES ───────────────────────────────────────────────────────

    def _refresh_face_count(self):
        n = sum(
            1
            for sel in FreeCADGui.Selection.getSelectionEx()
            for sub in (sel.SubElementNames or [])
            if sub.startswith("Face")
        )
        self._status.setText(f"{n} face(s) selected.")

    def _collect_faces(self):
        faces_data = []
        for sel in FreeCADGui.Selection.getSelectionEx():
            obj = sel.Object
            if not hasattr(obj, "Shape"):
                continue
            for sub in (sel.SubElementNames or []):
                if not sub.startswith("Face"):
                    continue
                try:
                    idx  = int(sub[4:]) - 1
                    face = obj.Shape.Faces[idx]
                    faces_data.append((obj.Name, face))
                except Exception:
                    pass
        return faces_data

    def _go_to_params(self):
        self._faces_data = self._collect_faces()
        if not self._faces_data:
            QtWidgets.QMessageBox.information(
                None, "No faces selected",
                "Please Ctrl+click at least one face in the 3D view first."
            )
            return
        FreeCADGui.Selection.clearSelection()
        self._phase = _PHASE_PARAMS
        self._set_phase_ui()

    def _back_to_faces(self):
        self._phase = _PHASE_FACES
        self._set_phase_ui()

    # Heading grid step (degrees) per dropdown entry — segments are biased
    # to run along multiples of this. 0 = no preference. See
    # core.trace_routing.heading_deviation_deg.
    _HEADING_STEPS = (0.0, 45.0, 90.0, 30.0)

    def _heading_step(self) -> float:
        idx = self._angle_pref.currentIndex()
        if 0 <= idx < len(self._HEADING_STEPS):
            return self._HEADING_STEPS[idx]
        return 0.0

    # ── Ok / Cancel ───────────────────────────────────────────────────────

    def getStandardButtons(self):
        return qenum_int(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)

    def accept(self):
        if self._phase == _PHASE_FACES:
            # OK pressed while still picking faces — same as Next.
            self._go_to_params()
            return

        doc = FreeCAD.activeDocument()
        if doc is None:
            self._cleanup()
            QtCore.QTimer.singleShot(0, FreeCADGui.Control.closeDialog)
            return

        spacing = self._spacing.value()
        grid_points = []
        surface_names = set()
        effective_spacing = spacing
        for obj_name, face in self._faces_data:
            face_pts, face_eff = trace_routing.sample_face_grid_ex(face, spacing)
            grid_points.extend(face_pts)
            effective_spacing = max(effective_spacing, face_eff)
            surface_names.add(obj_name)

        if not grid_points:
            QtWidgets.QMessageBox.information(
                None, "No grid points",
                "No valid points could be sampled on the selected face(s).\n"
                "Try a smaller grid spacing."
            )
            return

        if effective_spacing > spacing * 1.01:
            QtWidgets.QMessageBox.information(
                None, "Grid spacing adjusted",
                f"A spacing of {spacing:g} mm would need far more grid points "
                f"than this surface can carry, so {effective_spacing:.3f} mm "
                "was used instead.\n\n"
                f"{len(grid_points)} grid point(s) were generated.\n\n"
                "Pick a smaller face (or fewer faces) if you need a finer grid."
            )

        params = {
            # The spacing that ACTUALLY resulted — everything downstream
            # (neighbour radius, spatial-hash cells, escape distance) must be
            # sized to the grid that exists, not the one that was requested.
            "spacing_mm":   effective_spacing,
            "requested_spacing_mm": spacing,
            "width_mm":     self._width.value(),
            "thickness_mm": self._thickness.value(),
            "clearance_mm": self._clearance.value(),
            "max_bend_deg": self._max_bend.value(),
            "heading_step_deg":           self._heading_step(),
            "heading_penalty_mm_per_deg": self._angle_strength.value(),
            "corner_radius_mm":         self._corner_radius.value(),
            "escape_mm":                self._escape.value(),
        }
        trace_router.start_routing_session(
            doc, grid_points, surface_names, params,
            surface_faces=[f for _n, f in self._faces_data],
        )

        self._cleanup()
        QtCore.QTimer.singleShot(0, FreeCADGui.Control.closeDialog)

    def reject(self):
        self._cleanup()
        QtCore.QTimer.singleShot(0, FreeCADGui.Control.closeDialog)

    # ── helpers ───────────────────────────────────────────────────────────

    def _cleanup(self):
        if not self._active:
            return
        self._active = False
        try:
            FreeCADGui.Selection.removeObserver(self._observer)
        except Exception:
            pass
