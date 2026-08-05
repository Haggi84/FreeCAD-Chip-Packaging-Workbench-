# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Batch Auto-Route — setup panel.

A FreeCADGui.Control task panel, modeled on
routing.TraceRoutingSetupPanel, extended with a third phase that panel
doesn't need:

  Phase FACE   Ctrl+click exactly ONE routing surface face. Unlike Trace
               Routing's grid router, batch route does not support pairs
               spanning different faces in one run (known v1 limitation),
               so this phase enforces a single face rather than collecting
               several.
  Phase PARAMS Trace width / thickness / clearance.
  Phase QUEUE  Click pairs of ContactPoint markers in the 3D view to queue
               connections (handled by routing.BatchRouteSession, which
               owns the SelectionGate + click state machine for this
               phase); this panel just displays the live queue and offers
               Remove / Route All. Route All executes the whole queue and
               closes the panel; Cancel discards everything queued so far.
"""

import os
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore, qenum_int

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from routing.BatchRouteSession import batch_router

_PHASE_FACE   = "face"
_PHASE_PARAMS = "params"
_PHASE_QUEUE  = "queue"


class _FacePickObserver:
    """Live face-selection-count feedback during the FACE phase — mirrors
    routing.TraceRoutingSetupPanel._FacePickObserver."""

    def __init__(self, panel):
        self._panel = panel

    def addSelection(self, doc, obj, sub, pnt):
        if self._panel._phase == _PHASE_FACE:
            self._panel._refresh_face_count()

    def removeSelection(self, doc, obj, sub):
        if self._panel._phase == _PHASE_FACE:
            self._panel._refresh_face_count()

    def setSelection(self, doc):
        pass

    def clearSelection(self, doc):
        if self._panel._phase == _PHASE_FACE:
            self._panel._refresh_face_count()

    def setPreselection(self, *_):
        pass

    def removePreselection(self, *_):
        pass


class BatchRouteSetupPanel:

    def __init__(self):
        self._phase = _PHASE_FACE
        self._active = True
        self._face_data = None   # (obj_name, face_index, Part.Face) once picked

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

        # ── params group ─────────────────────────────────────────────────
        params_grp = QtWidgets.QGroupBox("Routing Parameters")
        p_lay = QtWidgets.QFormLayout(params_grp)

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

        self._trace_spacing = QtWidgets.QDoubleSpinBox()
        self._trace_spacing.setRange(0.0, 10.0)
        self._trace_spacing.setDecimals(3)
        self._trace_spacing.setValue(0.0)
        self._trace_spacing.setSuffix(" mm")
        self._trace_spacing.setToolTip(
            "Minimum edge-to-edge space between TRACES, when it should be\n"
            "larger than the general clearance. Values up to the clearance\n"
            "have no extra effect (the clearance already guarantees that\n"
            "much space); 0 means traces just use the clearance."
        )
        p_lay.addRow("Trace spacing:", self._trace_spacing)

        self._allow_reroute = QtWidgets.QCheckBox(
            "Reroute existing traces when a pair is blocked")
        self._allow_reroute.setChecked(True)
        self._allow_reroute.setToolTip(
            "When a queued pair cannot be routed, try moving ONE existing\n"
            "trace out of the way: the pair is routed with that trace\n"
            "removed, then the trace itself is rerouted around the new\n"
            "copper. Only applied when both routes exist — a connection\n"
            "that exists is never sacrificed."
        )
        p_lay.addRow("", self._allow_reroute)

        root.addWidget(params_grp)
        self._params_grp = params_grp

        self._start_queue_btn = QtWidgets.QPushButton("Next: Queue Pairs →")
        self._start_queue_btn.clicked.connect(self._go_to_queue)
        root.addWidget(self._start_queue_btn)

        self._back_to_face_btn = QtWidgets.QPushButton("← Back to face selection")
        self._back_to_face_btn.clicked.connect(self._back_to_face)
        root.addWidget(self._back_to_face_btn)

        # ── queue group ──────────────────────────────────────────────────
        queue_grp = QtWidgets.QGroupBox("Queued Pairs")
        q_lay = QtWidgets.QVBoxLayout(queue_grp)

        self._queue_list = QtWidgets.QListWidget()
        q_lay.addWidget(self._queue_list)

        q_btn_row = QtWidgets.QHBoxLayout()
        self._remove_btn = QtWidgets.QPushButton("Remove Selected")
        self._remove_btn.clicked.connect(self._remove_selected_pair)
        q_btn_row.addWidget(self._remove_btn)
        q_btn_row.addStretch()
        q_lay.addLayout(q_btn_row)

        self._route_all_btn = QtWidgets.QPushButton("Route All")
        self._route_all_btn.setStyleSheet("font-weight: bold;")
        self._route_all_btn.clicked.connect(self._route_all_and_close)
        q_lay.addWidget(self._route_all_btn)

        root.addWidget(queue_grp)
        self._queue_grp = queue_grp

        root.addStretch()
        self._set_phase_ui()

    def _set_phase_ui(self):
        self._next_btn.setVisible(self._phase == _PHASE_FACE)
        self._params_grp.setVisible(self._phase == _PHASE_PARAMS)
        self._start_queue_btn.setVisible(self._phase == _PHASE_PARAMS)
        self._back_to_face_btn.setVisible(self._phase == _PHASE_PARAMS)
        self._queue_grp.setVisible(self._phase == _PHASE_QUEUE)

        if self._phase == _PHASE_FACE:
            self._hint.setText(
                "<b>Step 1 — Select the routing surface</b><br>"
                "Hold <b>Ctrl</b> and left-click exactly ONE face (e.g. the "
                "PCB top face). All queued pairs in this run must share this "
                "one face. Press Next when done."
            )
            self._refresh_face_count()
        elif self._phase == _PHASE_PARAMS:
            self._hint.setText(
                "<b>Step 2 — Set routing parameters</b><br>"
                "Press Next to start the batch-route session."
            )
            self._status.setText("1 face selected.")
        else:
            self._hint.setText(
                "<b>Step 3 — Queue pairs</b><br>"
                "Click two ContactPoint markers in the 3D view to queue a "
                "connection, repeat as needed, then press <b>Route All</b>. "
                "Closing this panel any other way discards everything "
                "queued so far without baking anything."
            )
            self._refresh_queue_ui()

    # ── phase FACE ────────────────────────────────────────────────────────

    def _refresh_face_count(self):
        n = sum(
            1
            for sel in FreeCADGui.Selection.getSelectionEx()
            for sub in (sel.SubElementNames or [])
            if sub.startswith("Face")
        )
        self._status.setText(f"{n} face(s) selected.")

    def _collect_one_face(self):
        """Returns (obj_name, face_index, Part.Face), or None with a
        message box already shown for 0 or >1 faces selected."""
        faces = []
        for sel in FreeCADGui.Selection.getSelectionEx():
            obj = sel.Object
            if not hasattr(obj, "Shape"):
                continue
            for sub in (sel.SubElementNames or []):
                if not sub.startswith("Face"):
                    continue
                try:
                    idx = int(sub[4:]) - 1
                    faces.append((obj.Name, idx, obj.Shape.Faces[idx]))
                except Exception:
                    pass

        if not faces:
            QtWidgets.QMessageBox.information(
                None, "No face selected",
                "Please Ctrl+click one face in the 3D view first."
            )
            return None
        if len(faces) > 1:
            QtWidgets.QMessageBox.information(
                None, "Select only one face",
                "Batch routing works on a single routing surface per run — "
                "all queued pairs must share it. Please select just one face."
            )
            return None
        return faces[0]

    def _go_to_params(self):
        picked = self._collect_one_face()
        if picked is None:
            return
        self._face_data = picked
        FreeCADGui.Selection.clearSelection()
        self._phase = _PHASE_PARAMS
        self._set_phase_ui()

    def _back_to_face(self):
        self._phase = _PHASE_FACE
        self._set_phase_ui()

    # ── phase PARAMS -> QUEUE ────────────────────────────────────────────

    def _go_to_queue(self):
        doc = FreeCAD.activeDocument()
        if doc is None or self._face_data is None:
            self._cleanup()
            QtCore.QTimer.singleShot(0, FreeCADGui.Control.closeDialog)
            return

        obj_name, face_index, face = self._face_data
        params = {
            "width_mm":         self._width.value(),
            "thickness_mm":     self._thickness.value(),
            "clearance_mm":     self._clearance.value(),
            "trace_spacing_mm": self._trace_spacing.value(),
            "allow_reroute":    self._allow_reroute.isChecked(),
        }
        ok = batch_router.start_session(doc, obj_name, face_index, face, params)
        if not ok:
            QtWidgets.QMessageBox.warning(
                None, "Could not start batch routing",
                "The batch route session could not be started. Check the "
                "Report View for details."
            )
            return

        batch_router.on_change = self._refresh_queue_ui
        self._phase = _PHASE_QUEUE
        self._set_phase_ui()

    # ── phase QUEUE ───────────────────────────────────────────────────────

    def _refresh_queue_ui(self):
        doc = FreeCAD.activeDocument()
        self._queue_list.clear()
        for first_name, second_name in batch_router.queue:
            first_lbl = self._label_of(doc, first_name)
            second_lbl = self._label_of(doc, second_name)
            self._queue_list.addItem(f"{first_lbl}  →  {second_lbl}")
        self._route_all_btn.setEnabled(len(batch_router.queue) > 0)

    @staticmethod
    def _label_of(doc, name: str) -> str:
        obj = doc.getObject(name) if doc is not None else None
        return obj.Label if obj is not None else name

    def _remove_selected_pair(self):
        row = self._queue_list.currentRow()
        if row < 0:
            return
        batch_router.remove_pair(row)

    def _route_all_and_close(self):
        if not batch_router.queue:
            QtWidgets.QMessageBox.information(
                None, "Nothing queued",
                "Click two ContactPoint markers in the 3D view to queue a "
                "pair before running Route All."
            )
            return

        results = batch_router.route_all()
        n_baked = sum(1 for r in results if r[2] == "baked")
        blocked = [r for r in results if r[2] == "blocked"]
        msg = f"{n_baked} trace(s) routed and baked."
        if blocked:
            doc = FreeCAD.activeDocument()
            names = ", ".join(
                f"{self._label_of(doc, a)} → {self._label_of(doc, b)}"
                for a, b, _status, _t in blocked
            )
            msg += (f"\n\n{len(blocked)} pair(s) could not be routed and "
                    f"were skipped — route these by hand:\n{names}")
        QtWidgets.QMessageBox.information(None, "Route All", msg)

        self._finish_session()
        QtCore.QTimer.singleShot(0, FreeCADGui.Control.closeDialog)

    # ── Ok / Cancel ───────────────────────────────────────────────────────

    def getStandardButtons(self):
        return qenum_int(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)

    def accept(self):
        if self._phase == _PHASE_FACE:
            self._go_to_params()
            return
        if self._phase == _PHASE_PARAMS:
            self._go_to_queue()
            return
        # QUEUE phase: OK behaves the same as the in-panel Route All button.
        self._route_all_and_close()

    def reject(self):
        self._finish_session()
        QtCore.QTimer.singleShot(0, FreeCADGui.Control.closeDialog)

    # ── helpers ───────────────────────────────────────────────────────────

    def _finish_session(self):
        if batch_router.is_active:
            batch_router.on_change = None
            batch_router.end_session()
        self._cleanup()

    def _cleanup(self):
        if not self._active:
            return
        self._active = False
        try:
            FreeCADGui.Selection.removeObserver(self._observer)
        except Exception:
            pass
