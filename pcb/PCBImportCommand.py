"""
PCBImportCommand.py
===================
Lädt eine PCB als STEP-Datei, platziert sie frei im 3D-Raum und
erstellt automatisch ContactPoints auf den erkannten Pad-Flächen.

Ablauf
------
1. Dateidialog → .step / .stp wählen
2. STEP via Part.read() importieren → FreeCAD Part::Feature "PCB_Board"
3. Platzier-Dialog: X / Y / Z Offset + Rotation um Z-Achse
4. Pad-Erkennung: kleine flache Topflächen (copper pads) → ContactPoints
5. ContactPoints werden mit SourceObject="PCB_Pad_*" markiert
   → ContactPointPanel erkennt sie als neue Gruppe "PCB"

Pad-Erkennung
-------------
Heuristik für typische PCB-STEP-Dateien (KiCad, Altium-Export):
  - Fläche muss horizontal sein (Normale ≈ +Z)
  - Flächeninhalt zwischen 0.01 mm² und 100 mm² (kein Substrat, kein Gehäuse)
  - Z-Position nahe am Top der Platine (oberstes 5% der Z-Ausdehnung)
  - Noch kein ContactPoint an dieser Position vorhanden (Deduplizierung)
"""

from __future__ import annotations

import os
import FreeCAD
import FreeCADGui
import Part
from FreeCAD import Base
from compat import QtWidgets, QtCore

from Get_Path import get_icon


# ── Konstanten ────────────────────────────────────────────────────────────────

_PAD_AREA_MIN_MM2   =  0.01    # kleinste akzeptierte Pad-Fläche
_PAD_AREA_MAX_MM2   = 150.0    # größte akzeptierte Pad-Fläche (keine Kupferfläche)
_PAD_Z_TOP_FRAC     =  0.04    # Pad muss im obersten X% der Platinen-Z liegen
_PAD_NORMAL_Z_MIN   =  0.85    # cos(Winkel) — Fläche muss "fast horizontal" sein
_PAD_DEDUP_MM       =  0.05    # ContactPoints näher als X mm gelten als gleich


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _face_normal_z(face) -> float:
    """Gibt die Z-Komponente der Flächennormale zurück (0–1)."""
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
    Gibt eine Liste von Pad-Mittelpunkten zurück.

    Filtert horizontale Top-Flächen im richtigen Größenbereich.
    """
    bb      = shape.BoundBox
    z_top   = bb.ZMax
    z_range = bb.ZLength
    z_min_pad = z_top - z_range * z_threshold_frac

    pad_positions = []

    for face in shape.Faces:
        # Nur horizontale Flächen
        if _face_normal_z(face) < _PAD_NORMAL_Z_MIN:
            continue
        # Nur Topflächen
        c = _face_center(face)
        if c.z < z_min_pad:
            continue
        # Größenfilter
        area = _face_area(face)
        if not (_PAD_AREA_MIN_MM2 <= area <= _PAD_AREA_MAX_MM2):
            continue
        pad_positions.append(c)

    # Deduplizierung
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
    """Erstellt ContactPoint-Marker für alle erkannten Pads."""
    created = []
    cp_idx  = _next_cp_index(doc)

    for i, pos in enumerate(pad_positions):
        pad_name = f"PCB_Pad_{_next_pcb_pad_index(doc) + i - 1}"

        marker = doc.addObject("Part::Feature", pad_name)
        # Kleiner Quader als sichtbarer Marker
        try:
            sz  = 0.15   # 150 µm Markergröße
            box = Part.makeBox(sz, sz, 0.02,
                               FreeCAD.Vector(pos.x - sz/2, pos.y - sz/2, pos.z))
            marker.Shape = box
        except Exception:
            marker.Shape = Part.makeBox(0.1, 0.1, 0.01,
                                        FreeCAD.Vector(pos.x, pos.y, pos.z))

        marker.ViewObject.ShapeColor   = (0.20, 0.60, 1.00)   # blau = PCB-Pad
        marker.ViewObject.LineColor    = (0.10, 0.30, 0.60)
        marker.ViewObject.Transparency = 0

        # ContactPoint-Metadaten
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


# ── Platzier-Dialog ───────────────────────────────────────────────────────────

class _PlacementDialog(QtWidgets.QDialog):
    """Einfacher Dialog zur freien Platzierung der PCB im 3D-Raum."""

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


# ── FreeCAD-Befehl ────────────────────────────────────────────────────────────

class PCBImportCommand:
    def GetResources(self):
        return {
            "MenuText": "Import PCB (STEP)",
            "ToolTip":  ("Load a PCB as a STEP file, place it freely, "
                         "and auto-detect pad ContactPoints for wire bonding."),
            "Pixmap":   get_icon("PCB_Import.svg"),
        }

    def Activated(self):
        # ── 1. Datei wählen ───────────────────────────────────────────────
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Select PCB STEP file", "",
            "STEP Files (*.step *.stp *.STEP *.STP)"
        )
        if not path or not os.path.exists(path):
            return

        doc = FreeCAD.activeDocument()
        if doc is None:
            doc = FreeCAD.newDocument("PCB_Assembly")

        # ── 2. STEP importieren ──────────────────────────────────────────
        try:
            shape = Part.read(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(None, "Import Error",
                                           f"Could not read STEP file:\n{e}")
            return

        # ── 3. Platzier-Dialog ───────────────────────────────────────────
        dlg = _PlacementDialog(FreeCADGui.getMainWindow())
        if not dlg.exec_():
            return

        placement  = dlg.placement
        do_pads    = dlg.detect_pads

        # ── 4. PCB-Objekt erstellen ──────────────────────────────────────
        try:
            doc.openTransaction("Import PCB")
        except Exception:
            pass

        pcb_obj = doc.addObject("Part::Feature", "PCB_Board")
        pcb_obj.Shape = shape
        pcb_obj.Placement = placement

        pcb_obj.ViewObject.ShapeColor   = (0.15, 0.55, 0.20)   # PCB-grün
        pcb_obj.ViewObject.LineColor    = (0.05, 0.25, 0.10)
        pcb_obj.ViewObject.Transparency = 15

        # Metadaten
        try:
            pcb_obj.addProperty("App::PropertyString", "PCBFilePath", "PCB",
                                 "Source STEP file path")
            pcb_obj.PCBFilePath = path
            pcb_obj.addProperty("App::PropertyBool", "IsPCBBoard", "PCB",
                                 "Marks this object as a PCB board")
            pcb_obj.IsPCBBoard = True
        except Exception:
            pass

        # ── 5. Pad-Erkennung ─────────────────────────────────────────────
        n_pads = 0
        if do_pads:
            try:
                # Shape mit Placement transformieren
                placed_shape = shape.copy()
                placed_shape.Placement = placement
                pad_positions = detect_pads(placed_shape)

                if pad_positions:
                    cps = create_pad_contact_points(doc, pad_positions, pcb_obj.Name)
                    n_pads = len(cps)

                    # In PCB-Gruppe einordnen
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
                        f"[PCB] {n_pads} pad ContactPoints erstellt.\n"
                    )
                else:
                    FreeCAD.Console.PrintWarning(
                        "[PCB] Keine Pads erkannt. Bitte manuell ContactPoints setzen.\n"
                    )
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"[PCB] Pad-Erkennung fehlgeschlagen: {e}\n")

        try:
            doc.commitTransaction()
        except Exception:
            pass

        doc.recompute()
        FreeCADGui.updateGui()

        v = FreeCADGui.activeDocument().activeView()
        if v:
            v.fitAll()

        # ── 6. ContactPoint-Panel aktualisieren ──────────────────────────
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
    """Aktualisiert das ContactPointPanel falls es offen ist."""
    try:
        mw = FreeCADGui.getMainWindow()
        panel = mw.findChild(QtWidgets.QDockWidget, "ContactPointPanel")
        if panel and hasattr(panel, "refresh"):
            panel.refresh()
    except Exception:
        pass


FreeCADGui.addCommand("PCBImportCommand", PCBImportCommand())
