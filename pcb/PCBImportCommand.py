# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
PCBImportCommand.py
===================
Loads a PCB as a STEP file, places it freely in 3D space, and
automatically creates ContactPoints on the detected pad faces.

Workflow
--------
1. File dialog → select .step / .stp
2. Import STEP via Import.insert() — preserves hierarchy and per-body colours
3. Find root objects of the imported hierarchy
4. Placement dialog: X / Y / Z offset + rotation around Z axis
5. Apply placement to all root-level objects (children in App::Part follow automatically)
6. Pad detection: small flat top faces (copper pads) → ContactPoints
7. ContactPoints are marked with SourceObject="PCB_Pad_*"
   → ContactPointPanel recognises them as the new group "PCB"

Structure preservation
----------------------
Import.insert() is used instead of Part.read() so that the original
object hierarchy (board body, component bodies, sub-assemblies) and
their per-face colours are retained exactly as authored in the STEP file.

The root object(s) of the imported hierarchy are identified by checking
InList: any object whose InList contains no other newly imported object
is a top-level root.  All root names are stored in the PCBBodyObjects
property so PCBPlacementCommand can move them as a unit later.

Pad Detection
-------------
Heuristic for typical PCB STEP files (KiCad, Altium export):
  - Face must be horizontal (normal ≈ +Z)
  - Face area between 0.01 mm² and 150 mm² (no substrate, no housing)
  - Z position near the top of the board (top 4% of Z extent)
  - No ContactPoint already present at this position (deduplication)
"""

from __future__ import annotations

import os
import FreeCAD
import FreeCADGui
import Part
from FreeCAD import Base
from compat import QtWidgets

from Get_Path import get_icon


# ── Constants ────────────────────────────────────────────────────────────────

_PAD_AREA_MIN_MM2  =   0.01   # smallest accepted pad area
_PAD_AREA_MAX_MM2  = 150.0    # largest accepted pad area (no copper pour)
_PAD_Z_TOP_FRAC    =   0.04   # pad must be within the top X% of board Z extent
_PAD_NORMAL_Z_MIN  =   0.85   # cos(angle) — face must be "nearly horizontal"
_PAD_DEDUP_MM      =   0.05   # ContactPoints closer than X mm are considered identical


# ── Pad detection helpers ─────────────────────────────────────────────────────

def _face_normal_z(face) -> float:
    """Returns the Z component of the face normal (0–1)."""
    try:
        return abs(face.normalAt(0, 0).z)
    except Exception:
        return 0.0


def _face_area(face) -> float:
    try:
        return face.Area
    except Exception:
        return 0.0


def _face_center(face) -> Base.Vector:
    try:
        return Base.Vector(face.CenterOfMass)
    except Exception:
        return Base.Vector(0, 0, 0)


def detect_pads(shape, z_threshold_frac: float = _PAD_Z_TOP_FRAC) -> list[Base.Vector]:
    """
    Returns a list of pad centre points.

    Filters horizontal top faces within the correct area range.
    The shape must already be in world coordinates (placement applied).
    """
    bb        = shape.BoundBox
    z_min_pad = bb.ZMax - bb.ZLength * z_threshold_frac

    pad_positions = []
    for face in shape.Faces:
        if _face_normal_z(face) < _PAD_NORMAL_Z_MIN:
            continue
        c = _face_center(face)
        if c.z < z_min_pad:
            continue
        area = _face_area(face)
        if not (_PAD_AREA_MIN_MM2 <= area <= _PAD_AREA_MAX_MM2):
            continue
        pad_positions.append(c)

    # Deduplication
    unique: list[Base.Vector] = []
    for p in pad_positions:
        if not any((p - q).Length < _PAD_DEDUP_MM for q in unique):
            unique.append(p)

    return unique


def _world_compound(objs) -> Part.Shape | None:
    """
    Builds a compound of all Part::Feature shapes in *objs* with each
    object's placement applied, so face centres are in world coordinates.
    """
    placed = []
    for obj in objs:
        try:
            if not (hasattr(obj, "Shape") and obj.Shape.Faces):
                continue
            s = obj.Shape.copy()
            s.Placement = obj.Placement
            placed.append(s)
        except Exception:
            pass
    if not placed:
        return None
    return Part.makeCompound(placed) if len(placed) > 1 else placed[0]


# ── Root-hierarchy helpers ────────────────────────────────────────────────────

def _find_roots(new_objs: list) -> list:
    """
    Returns the subset of *new_objs* that have no parent within the same set.
    These are the top-level objects of the imported hierarchy.
    """
    new_names = {o.Name for o in new_objs}
    roots = [
        o for o in new_objs
        if not any(p.Name in new_names for p in o.InList)
    ]
    return roots or new_objs  # fallback: treat everything as root


# ── ContactPoint helpers ──────────────────────────────────────────────────────

def _next_pcb_pad_index(doc) -> int:
    return len([o for o in doc.Objects if o.Name.startswith("PCB_Pad_")]) + 1


def _next_cp_index(doc) -> int:
    return len([o for o in doc.Objects if getattr(o, "IsContactPoint", False)]) + 1


def create_pad_contact_points(doc, pad_positions: list[Base.Vector],
                               source_name: str) -> list:
    """Creates ContactPoint markers for all detected pads."""
    created = []
    cp_idx  = _next_cp_index(doc)

    for i, pos in enumerate(pad_positions):
        pad_name = f"PCB_Pad_{_next_pcb_pad_index(doc) + i - 1}"
        marker   = doc.addObject("Part::Feature", pad_name)

        sz = 0.15   # 150 µm marker size
        try:
            marker.Shape = Part.makeBox(
                sz, sz, 0.02,
                FreeCAD.Vector(pos.x - sz / 2, pos.y - sz / 2, pos.z),
            )
        except Exception:
            marker.Shape = Part.makeBox(0.1, 0.1, 0.01,
                                        FreeCAD.Vector(pos.x, pos.y, pos.z))

        marker.ViewObject.ShapeColor   = (0.20, 0.60, 1.00)   # blue = PCB pad
        marker.ViewObject.LineColor    = (0.10, 0.30, 0.60)
        marker.ViewObject.Transparency = 0

        def _add(ptype, name, grp, desc, val, m=marker):
            try:
                m.addProperty(ptype, name, grp, desc)
                setattr(m, name, val)
            except Exception:
                pass

        _add("App::PropertyVector", "ContactPoint",   "Wirebond",
             "3D position of this contact point", pos)
        _add("App::PropertyBool",   "IsContactPoint", "Wirebond",
             "Identifies this object as a ContactPoint marker", True)
        _add("App::PropertyString", "SourceObject",   "Wirebond",
             "Parent object this point was extracted from", source_name)
        _add("App::PropertyString", "PadType",        "PCB",
             "Type of pad", "PCB_Pad")
        _add("App::PropertyInteger","CPIndex",         "Wirebond",
             "Unique index", cp_idx)

        marker.Label = pad_name
        cp_idx += 1
        created.append(marker)

    return created


# ── Placement dialog ──────────────────────────────────────────────────────────

class _PlacementDialog(QtWidgets.QDialog):
    """Simple dialog for freely placing the PCB in 3D space."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Place PCB")
        self.setMinimumWidth(300)

        lay = QtWidgets.QFormLayout(self)

        self._x   = QtWidgets.QDoubleSpinBox()
        self._y   = QtWidgets.QDoubleSpinBox()
        self._z   = QtWidgets.QDoubleSpinBox()
        self._rot = QtWidgets.QDoubleSpinBox()

        for sb in (self._x, self._y, self._z):
            sb.setRange(-10000, 10000)
            sb.setDecimals(3)
            sb.setSuffix(" mm")

        self._rot.setRange(-180, 180)
        self._rot.setDecimals(1)
        self._rot.setSuffix(" °")

        lay.addRow("X offset:",    self._x)
        lay.addRow("Y offset:",    self._y)
        lay.addRow("Z offset:",    self._z)
        lay.addRow("Rotation Z:", self._rot)

        self._detect_pads = QtWidgets.QCheckBox(
            "Auto-detect pads and create ContactPoints"
        )
        self._detect_pads.setChecked(True)
        lay.addRow(self._detect_pads)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addRow(btns)

    @property
    def placement(self) -> Base.Placement:
        pos = Base.Vector(self._x.value(), self._y.value(), self._z.value())
        rot = Base.Rotation(Base.Vector(0, 0, 1), self._rot.value())
        return Base.Placement(pos, rot)

    @property
    def detect_pads(self) -> bool:
        return self._detect_pads.isChecked()


# ── FreeCAD command ───────────────────────────────────────────────────────────

class PCBImportCommand:
    def GetResources(self):
        return {
            "MenuText": "Import PCB (STEP)",
            "ToolTip":  (
                "Load a PCB as a STEP file, place it freely, "
                "and auto-detect pad ContactPoints for wire bonding.\n"
                "The original object hierarchy and colours are preserved."
            ),
            "Pixmap":   get_icon("PCB_Import.svg"),
        }

    def Activated(self):
        # ── 1. Select file ────────────────────────────────────────────────
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Select PCB STEP file", "",
            "STEP Files (*.step *.stp *.STEP *.STP)",
        )
        if not path or not os.path.exists(path):
            return

        doc = FreeCAD.activeDocument()
        if doc is None:
            doc = FreeCAD.newDocument("PCB_Assembly")

        # ── 2. Import STEP — preserves hierarchy and per-body colours ─────
        before_names = {o.Name for o in doc.Objects}
        try:
            import Import
            Import.insert(path, doc.Name)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                None, "Import Error", f"Could not read STEP file:\n{e}"
            )
            return

        new_objs = [o for o in doc.Objects if o.Name not in before_names]
        if not new_objs:
            QtWidgets.QMessageBox.critical(
                None, "Import Error", "STEP import produced no objects."
            )
            return

        # Save per-face colours assigned by Import.insert() so they survive
        # the recompute() calls below (recompute re-tessellates and resets
        # DiffuseColor to the default single-colour representation).
        _saved_diffuse = {}
        for o in new_objs:
            try:
                dc = o.ViewObject.DiffuseColor
                if dc:
                    _saved_diffuse[o.Name] = list(dc)
            except Exception:
                pass

        # ── 3. Placement dialog ───────────────────────────────────────────
        dlg = _PlacementDialog(FreeCADGui.getMainWindow())
        if not dlg.exec_():
            for o in new_objs:
                try:
                    doc.removeObject(o.Name)
                except Exception:
                    pass
            return

        placement = dlg.placement
        do_pads   = dlg.detect_pads

        try:
            doc.openTransaction("Import PCB")
        except Exception:
            pass

        # ── 4. Find root objects and apply placement ──────────────────────
        # Root objects have no parent within the newly imported set.
        # For App::Part hierarchies only the container is moved; children
        # follow automatically via FreeCAD's assembly model.
        # For flat imports every root object is moved individually.
        root_objs = _find_roots(new_objs)

        for obj in root_objs:
            try:
                # Compose user offset on top of the STEP-file placement
                obj.Placement = placement.multiply(obj.Placement)
            except Exception:
                pass

        # ── 5. Label and tag the first root as the PCB anchor ─────────────
        pcb_root = root_objs[0]
        pcb_root.Label = "PCB_Board"

        def _tag(ptype, name, grp, desc, val, obj=pcb_root):
            try:
                obj.addProperty(ptype, name, grp, desc)
                setattr(obj, name, val)
            except Exception:
                pass

        _tag("App::PropertyBool",   "IsPCBBoard",     "PCB",
             "Marks this object as the PCB board root", True)
        _tag("App::PropertyString", "PCBFilePath",    "PCB",
             "Source STEP file path", path)
        _tag("App::PropertyString", "PCBBodyObjects", "PCB",
             "Comma-separated names of all root-level PCB body objects",
             ",".join(o.Name for o in root_objs))

        # ── 6. Pad detection ──────────────────────────────────────────────
        n_pads = 0
        if do_pads:
            try:
                # Build world-space compound from all imported Part::Features
                combined = _world_compound(new_objs)
                if combined is None:
                    raise ValueError("No geometry found in imported objects")

                pad_positions = detect_pads(combined)

                if pad_positions:
                    cps   = create_pad_contact_points(doc, pad_positions, pcb_root.Name)
                    n_pads = len(cps)

                    # Add pad ContactPoints to the shared ContactPoints group
                    cp_grp = next(
                        (o for o in doc.Objects if o.Name == "ContactPoints"),
                        None,
                    )
                    if cp_grp is None:
                        cp_grp = doc.addObject(
                            "App::DocumentObjectGroup", "ContactPoints"
                        )
                        cp_grp.Label = "ContactPoints"
                    for cp in cps:
                        cp_grp.addObject(cp)

                    FreeCAD.Console.PrintMessage(
                        f"[PCB] {n_pads} pad ContactPoints created.\n"
                    )
                else:
                    FreeCAD.Console.PrintWarning(
                        "[PCB] No pads detected. Please set ContactPoints manually.\n"
                    )
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"[PCB] Pad detection failed: {exc}\n"
                )

        try:
            doc.commitTransaction()
        except Exception:
            pass

        doc.recompute()

        # Restore per-face colours — recompute resets DiffuseColor to default
        for o in new_objs:
            if o.Name in _saved_diffuse:
                try:
                    o.ViewObject.DiffuseColor = _saved_diffuse[o.Name]
                except Exception:
                    pass

        FreeCADGui.updateGui()

        view = FreeCADGui.activeDocument().activeView()
        if view:
            view.fitAll()

        # ── 7. Refresh ContactPoint panel ─────────────────────────────────
        _refresh_contact_panel()

        n_bodies = len(new_objs)
        msg = f"PCB imported: {os.path.basename(path)}\n{n_bodies} object(s) in hierarchy."
        if n_pads:
            msg += f"\n{n_pads} pad ContactPoints created and ready for wire bonding."
        else:
            msg += "\nNo pads auto-detected — set ContactPoints manually on PCB pad faces."
        QtWidgets.QMessageBox.information(None, "PCB Import", msg)

    def IsActive(self):
        return True


def _refresh_contact_panel():
    """Refreshes the ContactPointPanel if it is open."""
    try:
        mw    = FreeCADGui.getMainWindow()
        panel = mw.findChild(QtWidgets.QDockWidget, "ContactPointPanel")
        if panel and hasattr(panel, "refresh"):
            panel.refresh()
    except Exception:
        pass


FreeCADGui.addCommand("PCBImportCommand", PCBImportCommand())
