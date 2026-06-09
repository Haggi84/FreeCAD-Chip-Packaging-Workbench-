"""
PCBPlacementCommand.py
======================
Erlaubt das nachträgliche Verschieben und Rotieren einer bereits geladenen PCB.

Öffnet einen Dialog mit den aktuellen Placement-Werten vorausgefüllt.
Nach Bestätigung wird das PCB-Objekt und alle zugehörigen PCB-Pad-ContactPoints
gemeinsam transformiert (relative Verschiebung).
"""

from __future__ import annotations

import FreeCAD
import FreeCADGui
from FreeCAD import Base
from compat import QtWidgets

from Get_Path import get_icon


class _MovePCBDialog(QtWidgets.QDialog):
    def __init__(self, current_placement: Base.Placement, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Move / Rotate PCB")
        self.setMinimumWidth(320)

        p   = current_placement.Base
        rot = current_placement.Rotation

        lay = QtWidgets.QFormLayout(self)

        self._x = QtWidgets.QDoubleSpinBox()
        self._y = QtWidgets.QDoubleSpinBox()
        self._z = QtWidgets.QDoubleSpinBox()
        self._r = QtWidgets.QDoubleSpinBox()

        for sb, val in ((self._x, p.x), (self._y, p.y), (self._z, p.z)):
            sb.setRange(-10000, 10000)
            sb.setDecimals(3)
            sb.setSuffix(" mm")
            sb.setValue(val)

        try:
            yaw = rot.toEuler()[0]   # Z-Rotation
        except Exception:
            yaw = 0.0
        self._r.setRange(-180, 180)
        self._r.setDecimals(1)
        self._r.setSuffix(" °")
        self._r.setValue(yaw)

        lay.addRow("X:", self._x)
        lay.addRow("Y:", self._y)
        lay.addRow("Z:", self._z)
        lay.addRow("Rotation Z:", self._r)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addRow(btns)

    @property
    def placement(self) -> Base.Placement:
        pos = Base.Vector(self._x.value(), self._y.value(), self._z.value())
        rot = Base.Rotation(Base.Vector(0, 0, 1), self._r.value())
        return Base.Placement(pos, rot)


class PCBPlacementCommand:
    def GetResources(self):
        return {
            "MenuText": "Move PCB",
            "ToolTip":  "Reposition the PCB board and its ContactPoints.",
            "Pixmap":   get_icon("PCB_Move.svg"),
        }

    def Activated(self):
        doc = FreeCAD.activeDocument()
        if doc is None:
            return

        # PCB-Objekt finden
        pcb = next(
            (o for o in doc.Objects if getattr(o, "IsPCBBoard", False)),
            None
        )
        if pcb is None:
            QtWidgets.QMessageBox.warning(
                None, "No PCB",
                "No PCB board found in the document.\n"
                "Import a PCB first."
            )
            return

        dlg = _MovePCBDialog(pcb.Placement, FreeCADGui.getMainWindow())
        if not dlg.exec_():
            return

        new_pl  = dlg.placement
        old_pl  = pcb.Placement
        # Relative Transformation: delta = new * old^-1
        delta   = new_pl.multiply(old_pl.inverse())

        try:
            doc.openTransaction("Move PCB")
        except Exception:
            pass

        # PCB verschieben
        pcb.Placement = new_pl

        # Alle PCB-Pad-ContactPoints mitbewegen
        for obj in doc.Objects:
            src = getattr(obj, "SourceObject", "")
            if not src.startswith("PCB_") and not src == pcb.Name:
                continue
            if not getattr(obj, "IsContactPoint", False):
                continue
            try:
                old_cp  = getattr(obj, "ContactPoint", obj.Placement.Base)
                new_cp  = delta.multVec(old_cp)
                obj.ContactPoint = new_cp
                obj.Placement    = Base.Placement(new_cp, Base.Rotation())
                # Shape neu positionieren
                if hasattr(obj, "Shape") and obj.Shape:
                    obj.Shape = obj.Shape.copy()
                    obj.Shape.Placement = obj.Placement
            except Exception as e:
                FreeCAD.Console.PrintWarning(
                    f"[PCB] Could not move ContactPoint {obj.Name}: {e}\n"
                )

        try:
            doc.commitTransaction()
        except Exception:
            pass

        doc.recompute()
        FreeCADGui.updateGui()

        # ContactPointPanel aktualisieren
        try:
            mw = FreeCADGui.getMainWindow()
            panel = mw.findChild(__import__("compat").QtWidgets.QDockWidget,
                                 "ContactPointPanel")
            if panel and hasattr(panel, "refresh"):
                panel.refresh()
        except Exception:
            pass

    def IsActive(self):
        doc = FreeCAD.activeDocument()
        return doc is not None and any(
            getattr(o, "IsPCBBoard", False) for o in doc.Objects
        )


FreeCADGui.addCommand("PCBPlacementCommand", PCBPlacementCommand())
