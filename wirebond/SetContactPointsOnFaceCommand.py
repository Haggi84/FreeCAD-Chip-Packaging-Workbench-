"""
Grid-based contact-point placement — two-phase workflow.

Phase 1  Ctrl+click one or more faces in the 3D view.
         Adjust the grid spacing, then press "Generate Grid".
         A regular grid of candidate points appears on every selected face.

Phase 2  Left-click individual grid points to toggle their selection
         (grey = candidate, orange = selected contact point).
         Press OK to keep the selected points as permanent contact points
         and delete the rest.  Press Cancel to discard everything.
"""

import os
import sys

import FreeCAD
import FreeCADGui
import Part
from compat import QtWidgets, QtCore, qenum_int

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from Get_Path import get_icon


# ── palette ────────────────────────────────────────────────────────────────────

_COLOR_CANDIDATE = (0.55, 0.55, 0.55)   # grey  — candidate
_COLOR_SELECTED  = (1.00, 1.00, 0.00)   # yellow — chosen housing/package contact point
_POINT_SIZE      = 10


# ── assembly classification ────────────────────────────────────────────────────

def _get_source_assembly(doc, obj_name: str) -> str:
    """Return 'gds', 'package', or 'unknown' based on which group the object is in."""
    obj = doc.getObject(obj_name)
    if obj is None:
        return "unknown"
    for group in doc.Objects:
        if not hasattr(group, "Group"):
            continue
        try:
            if obj not in group.Group:
                continue
        except Exception:
            continue
        lbl = group.Label.lower()
        if any(k in lbl for k in ("gds", "die")):
            return "gds"
        if any(k in lbl for k in ("package", "leadframe")):
            return "package"
    return "unknown"


# ── contact-point marker helpers ───────────────────────────────────────────────

def _next_housing_index(doc) -> int:
    existing = [o for o in doc.Objects
                if o.Name.startswith("contact_point_housing_")]
    return len(existing) + 1


def _next_gds_index(doc) -> int:
    existing = [o for o in doc.Objects
                if o.Name.startswith("ContactPoint_")]
    return len(existing) + 1


def _create_housing_marker(doc, source_name: str, point, index: int):
    """Create a yellow ContactPoint marker named contact_point_housing_NNN (package side)."""
    marker = doc.addObject("Part::Feature", f"contact_point_housing_{index:03d}")
    marker.Shape = Part.Vertex(point.x, point.y, point.z)

    marker.addProperty("App::PropertyVector", "ContactPoint", "Wirebond", "")
    marker.addProperty("App::PropertyString", "SourceObject",  "Wirebond", "")
    marker.addProperty("App::PropertyBool",   "IsContactPoint","Wirebond", "")

    marker.ContactPoint   = point
    marker.SourceObject   = source_name
    marker.IsContactPoint = True

    marker.ViewObject.PointSize   = 8
    marker.ViewObject.PointColor  = (1.0, 1.0, 0.0)   # yellow
    marker.ViewObject.DisplayMode = "Points"
    return marker


def _create_gds_marker(doc, source_name: str, point, index: int):
    """Create an orange ContactPoint marker named ContactPoint_NNN (die/GDS side)."""
    marker = doc.addObject("Part::Feature", f"ContactPoint_{index:03d}")
    marker.Shape = Part.Vertex(point.x, point.y, point.z)

    marker.addProperty("App::PropertyVector", "ContactPoint", "Wirebond", "")
    marker.addProperty("App::PropertyString", "SourceObject",  "Wirebond", "")
    marker.addProperty("App::PropertyBool",   "IsContactPoint","Wirebond", "")

    marker.ContactPoint   = point
    marker.SourceObject   = source_name
    marker.IsContactPoint = True

    marker.ViewObject.PointSize   = 8
    marker.ViewObject.PointColor  = (1.0, 0.50, 0.0)   # orange — die side
    marker.ViewObject.DisplayMode = "Points"
    return marker


# ── Contact Point Browser refresh ─────────────────────────────────────────────

def _refresh_contact_panel():
    try:
        mw = FreeCADGui.getMainWindow()
        for w in mw.findChildren(QtWidgets.QDockWidget):
            if w.objectName() == "ContactPointPanel":
                inner = w.widget()
                target = inner if (inner and hasattr(inner, "populate")) else w
                if hasattr(target, "populate"):
                    target.populate()
                    return
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"SetContactPoints: browser refresh: {exc}\n")


# ── grid sampling ──────────────────────────────────────────────────────────────

def _sample_grid(face, spacing_mm: float):
    """
    Return FreeCAD.Vector points sampled on *face* at ~spacing_mm intervals.
    Uses UV parametrisation; points outside the trimmed boundary are dropped.
    Capped at 50 steps per axis.
    """
    pts = []
    try:
        u_min, u_max, v_min, v_max = face.ParameterRange

        if (u_max - u_min) < 1e-9 or (v_max - v_min) < 1e-9:
            return pts

        u_mid = (u_min + u_max) / 2.0
        v_mid = (v_min + v_max) / 2.0
        p0    = face.valueAt(u_mid, v_mid)

        du_p = (u_max - u_min) * 0.01
        dv_p = (v_max - v_min) * 0.01
        u_sc = (p0.distanceToPoint(face.valueAt(u_mid + du_p, v_mid)) / du_p
                if du_p > 1e-12 else 1.0)
        v_sc = (p0.distanceToPoint(face.valueAt(u_mid, v_mid + dv_p)) / dv_p
                if dv_p > 1e-12 else 1.0)

        du = (spacing_mm / u_sc) if u_sc > 1e-9 else (u_max - u_min) / 5.0
        dv = (spacing_mm / v_sc) if v_sc > 1e-9 else (v_max - v_min) / 5.0

        n_u = max(2, min(50, int((u_max - u_min) / du) + 1))
        n_v = max(2, min(50, int((v_max - v_min) / dv) + 1))

        for i in range(n_u):
            u = u_min + (u_max - u_min) * i / (n_u - 1)
            for j in range(n_v):
                v = v_min + (v_max - v_min) * j / (n_v - 1)
                try:
                    pt = face.valueAt(u, v)
                except Exception:
                    continue
                inside = True
                try:
                    inside = face.isPartOfDomain(u, v)
                except AttributeError:
                    try:
                        inside = face.isInside(pt, 1e-3, True)
                    except Exception:
                        inside = True
                if inside:
                    pts.append(FreeCAD.Vector(pt.x, pt.y, pt.z))
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"SetContactPoints: grid sampling: {exc}\n")
    return pts


# ── unified selection observer ─────────────────────────────────────────────────

class _PanelObserver:
    """
    Single SelectionObserver used throughout both phases.

    Phase 1 (_PHASE_FACES):  addSelection / removeSelection / clearSelection
                              → update the live face count label.

    Phase 2 (_PHASE_POINTS): addSelection
                              → if the selected object is a grid point, toggle
                                its state; then defer clearSelection so
                                FreeCAD's green highlight disappears.
    """

    def __init__(self, panel):
        self._panel = panel

    def addSelection(self, doc, obj, sub, pnt):
        p = self._panel
        if not p._active:
            return
        if p._phase == _PHASE_FACES:
            p._refresh_face_count()
        else:
            # Phase 2: toggle grid point if that's what was clicked
            if obj in p._grid_names:
                p._toggle_grid_point(obj)
                # Defer clear so FreeCAD's own picker finishes first
                QtCore.QTimer.singleShot(0, FreeCADGui.Selection.clearSelection)

    def removeSelection(self, doc, obj, sub):
        p = self._panel
        if p._active and p._phase == _PHASE_FACES:
            p._refresh_face_count()

    def setSelection(self, doc):
        pass

    def clearSelection(self, doc):
        p = self._panel
        if p._active and p._phase == _PHASE_FACES:
            p._refresh_face_count()

    def setPreselection(self, *_):    pass
    def removePreselection(self, *_): pass


# ── phases ─────────────────────────────────────────────────────────────────────

_PHASE_FACES  = "faces"
_PHASE_POINTS = "points"


# ── task panel ─────────────────────────────────────────────────────────────────

class _GridContactPanel:

    def __init__(self):
        self._phase   = _PHASE_FACES
        self._active  = True

        self._grid_names  = []    # internal names of all grid-point objects
        self._selected    = set() # subset toggled orange
        self._source_map  = {}    # grid name → source object name

        self._view     = FreeCADGui.activeDocument().activeView()
        self._observer = _PanelObserver(self)
        FreeCADGui.Selection.addObserver(self._observer)

        self._build_form()

    # ── form ──────────────────────────────────────────────────────────────────

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

        # Spacing row (phase 1 only)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Grid spacing (mm):"))
        self._spacing = QtWidgets.QDoubleSpinBox()
        self._spacing.setRange(0.001, 10.0)
        self._spacing.setValue(0.05)
        self._spacing.setDecimals(3)
        self._spacing.setSingleStep(0.01)
        row.addWidget(self._spacing)
        self._spacing_row = QtWidgets.QWidget()
        self._spacing_row.setLayout(row)
        root.addWidget(self._spacing_row)

        self._gen_btn = QtWidgets.QPushButton("Generate Grid")
        self._gen_btn.clicked.connect(self._generate_grid)
        root.addWidget(self._gen_btn)

        self._back_btn = QtWidgets.QPushButton("← Back to face selection")
        self._back_btn.clicked.connect(self._back_to_faces)
        root.addWidget(self._back_btn)

        root.addStretch()
        self._set_phase_ui()

    def _set_phase_ui(self):
        if self._phase == _PHASE_FACES:
            self._hint.setText(
                "<b>Step 1 — Select faces</b><br>"
                "Hold <b>Ctrl</b> and left-click one or more faces.<br>"
                "Set the grid spacing, then press <b>Generate Grid</b>."
            )
            self._spacing_row.setVisible(True)
            self._gen_btn.setVisible(True)
            self._back_btn.setVisible(False)
            self._refresh_face_count()
        else:
            self._spacing_row.setVisible(False)
            self._gen_btn.setVisible(False)
            self._back_btn.setVisible(True)
            self._hint.setText(
                "<b>Step 2 — Pick contact points</b><br>"
                "Left-click grid dots to select them "
                "<span style='color:#e64d1a'>(turns orange)</span>.<br>"
                "Click again to deselect.<br>"
                "Press <b>OK</b> to confirm, <b>Cancel</b> to discard all."
            )
            self._update_point_status()

    def _update_point_status(self):
        self._status.setText(
            f"{len(self._selected)} of {len(self._grid_names)} "
            "grid point(s) selected."
        )

    # ── phase 1 ───────────────────────────────────────────────────────────────

    def _refresh_face_count(self):
        n = sum(
            1
            for sel in FreeCADGui.Selection.getSelectionEx()
            for sub in (sel.SubElementNames or [])
            if sub.startswith("Face")
        )
        self._status.setText(f"{n} face(s) selected.")

    def _generate_grid(self):
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

        if not faces_data:
            QtWidgets.QMessageBox.information(
                None, "No faces selected",
                "Please Ctrl+click at least one face in the 3D view first."
            )
            return

        spacing = self._spacing.value()
        doc = FreeCAD.activeDocument()
        if not doc:
            return

        self._remove_grid_points(doc)

        for src_name, face in faces_data:
            for pt in _sample_grid(face, spacing):
                gp = doc.addObject("Part::Feature", "GridPt")
                gp.Shape = Part.Vertex(pt.x, pt.y, pt.z)
                gp.addProperty("App::PropertyBool", "IsGridPoint", "Grid", "")
                gp.IsGridPoint = True
                gp.ViewObject.PointSize   = _POINT_SIZE
                gp.ViewObject.PointColor  = _COLOR_CANDIDATE
                gp.ViewObject.DisplayMode = "Points"
                self._grid_names.append(gp.Name)
                self._source_map[gp.Name] = src_name

        doc.recompute()

        if not self._grid_names:
            QtWidgets.QMessageBox.information(
                None, "No grid points",
                "No valid points could be sampled on the selected faces.\n"
                "Try a larger face or a smaller grid spacing."
            )
            return

        FreeCADGui.Selection.clearSelection()
        self._phase = _PHASE_POINTS
        self._set_phase_ui()

    # ── phase 2 ───────────────────────────────────────────────────────────────

    def _toggle_grid_point(self, obj_name: str):
        """Toggle a grid point between candidate (grey) and selected (orange)."""
        doc = FreeCAD.activeDocument()
        if not doc:
            return
        obj = doc.getObject(obj_name)
        if obj is None:
            return
        if obj_name in self._selected:
            self._selected.discard(obj_name)
            obj.ViewObject.PointColor = _COLOR_CANDIDATE
        else:
            self._selected.add(obj_name)
            obj.ViewObject.PointColor = _COLOR_SELECTED
        self._update_point_status()

    def _back_to_faces(self):
        doc = FreeCAD.activeDocument()
        if doc:
            self._remove_grid_points(doc)
            doc.recompute()
        self._phase = _PHASE_FACES
        self._set_phase_ui()

    # ── ok / cancel ───────────────────────────────────────────────────────────

    def getStandardButtons(self):
        return qenum_int(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)

    def accept(self):
        if self._phase == _PHASE_FACES:
            self._cleanup()
            QtCore.QTimer.singleShot(0, FreeCADGui.Control.closeDialog)
            return

        doc = FreeCAD.activeDocument()
        placed = []
        if doc:
            for name in list(self._selected):
                gp = doc.getObject(name)
                if gp is None:
                    continue
                try:
                    vx  = gp.Shape.Vertexes[0]
                    pt  = FreeCAD.Vector(vx.X, vx.Y, vx.Z)
                    src = self._source_map.get(name, "")
                    asm = _get_source_assembly(doc, src)
                    if asm == "gds":
                        m = _create_gds_marker(doc, src, pt, _next_gds_index(doc))
                    else:
                        m = _create_housing_marker(doc, src, pt, _next_housing_index(doc))
                    placed.append(m.Name)
                except Exception as exc:
                    FreeCAD.Console.PrintWarning(
                        f"SetContactPoints: marker creation failed: {exc}\n"
                    )
            self._remove_grid_points(doc)
            doc.recompute()
            _refresh_contact_panel()

        FreeCAD.Console.PrintMessage(
            f"[SetContactPoints] {len(placed)} contact point(s) placed.\n"
        )
        self._cleanup()
        QtCore.QTimer.singleShot(0, FreeCADGui.Control.closeDialog)
        if placed:
            QtCore.QTimer.singleShot(200, lambda: FreeCADGui.runCommand("WirebondCommand"))

    def reject(self):
        doc = FreeCAD.activeDocument()
        if doc:
            self._remove_grid_points(doc)
            doc.recompute()
        self._cleanup()
        QtCore.QTimer.singleShot(0, FreeCADGui.Control.closeDialog)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _remove_grid_points(self, doc):
        for name in self._grid_names:
            try:
                doc.removeObject(name)
            except Exception:
                pass
        self._grid_names.clear()
        self._selected.clear()
        self._source_map.clear()

    def _cleanup(self):
        if not self._active:
            return
        self._active = False
        try:
            FreeCADGui.Selection.removeObserver(self._observer)
        except Exception:
            pass


# ── FreeCAD command ────────────────────────────────────────────────────────────

class SetContactPointsOnFaceCommand:
    def GetResources(self):
        return {
            "MenuText": "Set Contact Points on Face",
            "ToolTip": (
                "Phase 1: Ctrl+click faces and press Generate Grid.  "
                "Phase 2: click grid points to select contact points, then OK."
            ),
            "Pixmap": get_icon("Set_Contact_Points.svg"),
        }

    def Activated(self):
        if FreeCADGui.Control.activeDialog():
            QtWidgets.QMessageBox.information(
                None,
                "Task panel already open",
                "Please close the current task panel before placing contact points.",
            )
            return
        FreeCADGui.Control.showDialog(_GridContactPanel())

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand("SetContactPointsOnFaceCommand", SetContactPointsOnFaceCommand())
