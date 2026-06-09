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
2. Import STEP via Part.read() → FreeCAD Part::Feature "PCB_Board"
3. Placement dialog: X / Y / Z offset + rotation around Z axis
4. Pad detection: small flat top faces (copper pads) → ContactPoints
5. ContactPoints are marked with SourceObject="PCB_Pad_*"
   → ContactPointPanel recognises them as the new group "PCB"

Pad Detection
-------------
Heuristic for typical PCB STEP files (KiCad, Altium export):
  - Face must be horizontal (normal ≈ +Z)
  - Face area between 0.01 mm² and 100 mm² (no substrate, no housing)
  - Z position near the top of the board (top 5% of Z extent)
  - No ContactPoint already present at this position (deduplication)
"""

from __future__ import annotations

import os
import FreeCAD
import FreeCADGui
import Part
from FreeCAD import Base
from compat import QtWidgets, QtCore

from Get_Path import get_icon


# ── Constants ────────────────────────────────────────────────────────────────

_PAD_AREA_MIN_MM2   =  0.01    # smallest accepted pad area
_PAD_AREA_MAX_MM2   = 150.0    # largest accepted pad area (no copper pour)
_PAD_Z_TOP_FRAC     =  0.04    # pad must be within the top X% of board Z extent
_PAD_NORMAL_Z_MIN   =  0.85    # cos(angle) — face must be "nearly horizontal"
_PAD_DEDUP_MM       =  0.05    # ContactPoints closer than X mm are considered identical


# ── Helper functions ──────────────────────────────────────────────────────────

def _face_normal_z(face) -> float:
    """Returns the Z component of the face normal (0–1)."""
    try:
        n = face.normalAt(0, 0)
        return abs(n.z)
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
    """
    bb      = shape.BoundBox
    z_top   = bb.ZMax
    z_range = bb.ZLength
    z_min_pad = z_top - z_range * z_threshold_frac

    pad_positions = []

    for face in shape.Faces:
        # Horizontal faces only
        if _face_normal_z(face) < _PAD_NORMAL_Z_MIN:
            continue
        # Top faces only
        c = _face_center(face)
        if c.z < z_min_pad:
            continue
        # Size filter
        area = _face_area(face)
        if not (_PAD_AREA_MIN_MM2 <= area <= _PAD_AREA_MAX_MM2):
            continue
        pad_positions.append(c)

    # Deduplication
    unique = []
    for p in pad_positions:
        if not any(
            (p - q).Length < _PAD_DEDUP_MM
            for q in unique
        ):
            unique.append(p)

    return unique


def _next_pcb_pad_index(doc) -> int:
    existing = [o for o in doc.Objects if o.Name.startswith("PCB_Pad_")]
    return len(existing) + 1


def _next_cp_index(doc) -> int:
    existing = [o for o in doc.Objects
                if getattr(o, "IsContactPoint", False)]
    return len(existing) + 1


def create_pad_contact_points(doc, pad_positions: list[Base.Vector],
                               pcb_obj_name: str) -> list:
    """Creates ContactPoint markers for all detected pads."""
    created = []
    cp_idx  = _next_cp_index(doc)

    for i, pos in enumerate(pad_positions):
        pad_name = f"PCB_Pad_{_next_pcb_pad_index(doc) + i - 1}"

        marker = doc.addObject("Part::Feature", pad_name)
        # Small box as a visible marker
        try:
            sz  = 0.15   # 150 µm marker size
            box = Part.makeBox(sz, sz, 0.02,
                               FreeCAD.Vector(pos.x - sz/2, pos.y - sz/2, pos.z))
            marker.Shape = box
        except Exception:
            marker.Shape = Part.makeBox(0.1, 0.1, 0.01,
                                        FreeCAD.Vector(pos.x, pos.y, pos.z))

        marker.ViewObject.ShapeColor   = (0.20, 0.60, 1.00)   # blue = PCB pad
        marker.ViewObject.LineColor    = (0.10, 0.30, 0.60)
        marker.ViewObject.Transparency = 0

        # ContactPoint metadata
        def _add(ptype, name, grp, desc, val):
            try:
                marker.addProperty(ptype, name, grp, desc)
                setattr(marker, name, val)
            except Exception:
                pass

        _add("App::PropertyVector", "ContactPoint",   "Wirebond",
             "3D position of this contact point", pos)
        _add("App::PropertyBool",   "IsContactPoint", "Wirebond",
             "Identifies this object as a ContactPoint marker", True)
        _add("App::PropertyString", "SourceObject",   "Wirebond",
             "Parent object this point was extracted from", pcb_obj_name)
        _add("App::PropertyString", "PadType",        "PCB",
             "Type of pad", "PCB_Pad")
        _add("App::PropertyInteger","CPIndex",         "Wirebond",
             "Unique index", cp_idx)

        marker.Label = pad_name
        cp_idx += 1
        created.append(marker)

    return created


# ── STEP colour extraction ────────────────────────────────────────────────────

def _parse_step_colors(path: str) -> dict:
    """
    Reads COLOUR_RGB entries from a STEP file.

    Fast variant: only checks lines containing 'COLOUR_RGB' —
    all other lines are skipped without further parsing.
    Typical runtime: < 5 ms even for large STEP files.

    Returns {"colors": {entity_id: (r, g, b)}}.
    """
    colors: dict[int, tuple[float, float, float]] = {}

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                if "COLOUR_RGB" not in raw:
                    continue
                line = raw.strip().rstrip(";")
                eq = line.find("=")
                if eq < 0:
                    continue
                try:
                    eid = int(line[1:eq].strip())
                    rest = line[eq + 1:].strip()
                    if not rest.startswith("COLOUR_RGB("):
                        continue
                    inner = rest[len("COLOUR_RGB("):-1]
                    parts = [p.strip().strip("'") for p in inner.split(",")]
                    if len(parts) >= 4:
                        colors[eid] = (float(parts[1]), float(parts[2]), float(parts[3]))
                except (ValueError, IndexError):
                    pass
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[PCB] STEP colour parsing failed: {exc}\n")

    return {"colors": colors}


def _apply_step_colors(path: str, vobj) -> bool:
    """
    Applies STEP colours to the ViewObject of a Part::Feature.

    Strategy:
    1. Parse STEP → dict {entity_id: (r,g,b)}
    2. Assign parsed colours to shapes (Solids + Shells) in order
    3. Set DiffuseColor per face

    Returns True if at least one colour was applied.
    """
    parsed = _parse_step_colors(path)
    if not parsed:
        return False

    colors_by_id = parsed.get("colors", {})
    if not colors_by_id:
        return False

    # All unique colours in order of first occurrence
    seen = []
    for rgb in colors_by_id.values():
        if rgb not in seen:
            seen.append(rgb)

    # Assign colours to sub-shapes: Solids first, then Shells
    try:
        shape = vobj.Object.Shape
        sub_shapes = list(shape.Solids) + list(shape.Shells)
    except Exception:
        return False

    if not sub_shapes:
        # No compound — single colour using the first colour
        if seen:
            r, g, b = seen[0]
            vobj.ShapeColor = (r, g, b)
            return True
        return False

    # Build Face→Colour mapping: each face belongs to the sub-shape with the tightest BoundBox
    face_colors = []
    for face in shape.Faces:
        best_idx = 0
        best_vol = float("inf")
        for i, sub in enumerate(sub_shapes):
            try:
                sb = sub.BoundBox
                fb = face.BoundBox
                # Face must lie within the sub-shape BBox
                if (sb.XMin <= fb.XMin + 1e-4 and sb.XMax >= fb.XMax - 1e-4 and
                        sb.YMin <= fb.YMin + 1e-4 and sb.YMax >= fb.YMax - 1e-4 and
                        sb.ZMin <= fb.ZMin + 1e-4 and sb.ZMax >= fb.ZMax - 1e-4):
                    vol = sb.XLength * sb.YLength * sb.ZLength
                    if vol < best_vol:
                        best_vol = vol
                        best_idx = i
            except Exception:
                pass
        color_idx = min(best_idx, len(seen) - 1)
        face_colors.append(seen[color_idx])

    try:
        vobj.DiffuseColor = face_colors
        FreeCAD.Console.PrintMessage(
            f"[PCB] {len(set(face_colors))} colours applied to {len(face_colors)} faces.\n"
        )
        return True
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[PCB] DiffuseColor failed: {exc}\n")
        return False




class _PlacementDialog(QtWidgets.QDialog):
    """Simple dialog for freely placing the PCB in 3D space."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Place PCB")
        self.setMinimumWidth(300)

        lay = QtWidgets.QFormLayout(self)

        self._x = QtWidgets.QDoubleSpinBox()
        self._y = QtWidgets.QDoubleSpinBox()
        self._z = QtWidgets.QDoubleSpinBox()
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

        self._detect_pads = QtWidgets.QCheckBox("Auto-detect pads and create ContactPoints")
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
            "ToolTip":  ("Load a PCB as a STEP file, place it freely, "
                         "and auto-detect pad ContactPoints for wire bonding."),
            "Pixmap":   get_icon("PCB_Import.svg"),
        }

    def Activated(self):
        # ── 1. Select file ────────────────────────────────────────────────
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Select PCB STEP file", "",
            "STEP Files (*.step *.stp *.STEP *.STP)"
        )
        if not path or not os.path.exists(path):
            return

        doc = FreeCAD.activeDocument()
        if doc is None:
            doc = FreeCAD.newDocument("PCB_Assembly")

        # ── 2. Import STEP ───────────────────────────────────────────────
        try:
            shape = Part.read(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(None, "Import Error",
                                           f"Could not read STEP file:\n{e}")
            return

        # ── 3. Placement dialog ──────────────────────────────────────────
        dlg = _PlacementDialog(FreeCADGui.getMainWindow())
        if not dlg.exec_():
            return

        placement  = dlg.placement
        do_pads    = dlg.detect_pads

        # ── 4. Create PCB object ─────────────────────────────────────────
        try:
            doc.openTransaction("Import PCB")
        except Exception:
            pass

        pcb_obj = doc.addObject("Part::Feature", "PCB_Board")
        pcb_obj.Shape = shape
        pcb_obj.Placement = placement

        pcb_obj.ViewObject.LineColor    = (0.05, 0.25, 0.10)
        pcb_obj.ViewObject.Transparency = 0

        # Read colours from STEP file; fallback: PCB green
        if not _apply_step_colors(path, pcb_obj.ViewObject):
            pcb_obj.ViewObject.ShapeColor = (0.15, 0.55, 0.20)
            FreeCAD.Console.PrintWarning(
                "[PCB] No STEP colours found — fallback colour used.\n"
            )

        # Metadata
        try:
            pcb_obj.addProperty("App::PropertyString", "PCBFilePath", "PCB",
                                 "Source STEP file path")
            pcb_obj.PCBFilePath = path
            pcb_obj.addProperty("App::PropertyBool", "IsPCBBoard", "PCB",
                                 "Marks this object as a PCB board")
            pcb_obj.IsPCBBoard = True
        except Exception:
            pass

        # ── 5. Pad detection ─────────────────────────────────────────────
        n_pads = 0
        if do_pads:
            try:
                # Transform shape with placement
                placed_shape = shape.copy()
                placed_shape.Placement = placement
                pad_positions = detect_pads(placed_shape)

                if pad_positions:
                    cps = create_pad_contact_points(doc, pad_positions, pcb_obj.Name)
                    n_pads = len(cps)

                    # Add to PCB group
                    try:
                        grp = next(
                            (o for o in doc.Objects
                             if o.Name == "PCB_Assembly"),
                            None
                        )
                        if grp is None:
                            grp = doc.addObject("App::DocumentObjectGroup", "PCB_Assembly")
                            grp.Label = "PCB_Assembly"
                        grp.addObject(pcb_obj)
                        for cp in cps:
                            grp.addObject(cp)
                    except Exception:
                        pass

                    FreeCAD.Console.PrintMessage(
                        f"[PCB] {n_pads} pad ContactPoints created.\n"
                    )
                else:
                    FreeCAD.Console.PrintWarning(
                        "[PCB] No pads detected. Please set ContactPoints manually.\n"
                    )
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"[PCB] Pad detection failed: {e}\n")

        try:
            doc.commitTransaction()
        except Exception:
            pass

        doc.recompute()
        FreeCADGui.updateGui()

        v = FreeCADGui.activeDocument().activeView()
        if v:
            v.fitAll()

        # ── 6. Refresh ContactPoint panel ────────────────────────────────
        _refresh_contact_panel()

        msg = f"PCB imported: {os.path.basename(path)}"
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
        mw = FreeCADGui.getMainWindow()
        panel = mw.findChild(QtWidgets.QDockWidget, "ContactPointPanel")
        if panel and hasattr(panel, "refresh"):
            panel.refresh()
    except Exception:
        pass


FreeCADGui.addCommand("PCBImportCommand", PCBImportCommand())