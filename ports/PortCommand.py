# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Define Port — draw ports on the imported layout, one after another.

Three clicks make a port:

    1. click an edge, where the port starts
    2. click again along that edge, which sets its length
    3. move the mouse up or down and click, which sets its height and
       whether it reaches in +Z or -Z

Then it starts again on the next port, and keeps going until you finish. Esc
abandons the port being drawn; Esc again — or Finish — ends the session.

The height follows the cursor on the vertical plane through the two points,
so what you see while moving is the face that will be built. A height typed
into the panel is used instead, which is how a port drawn roughly by hand
gets an exact number without being drawn again.

The port itself, and what it remembers, is in core.ports.
"""

import FreeCAD
import FreeCADGui

from Get_Path import get_icon

_PREVIEW_NAME = "_PortPreview"

_PICK_EDGE, _PICK_END, _PICK_HEIGHT = "edge", "end", "height"

_HINTS = {
    _PICK_EDGE: "Click an edge where the port starts.",
    _PICK_END: "Click again along the edge to set the port's length.",
    _PICK_HEIGHT: "Move up or down to set the height, then click.",
}


class _PortPanel:
    """A small panel that stays open while ports are being drawn."""

    def __init__(self, session, QtWidgets, QtCore, parent):
        self.session = session
        self.dialog = QtWidgets.QDialog(parent)
        self.dialog.setWindowTitle("Define Port")
        self.dialog.setWindowFlags(self.dialog.windowFlags() | QtCore.Qt.Tool)
        self.dialog.setModal(False)
        layout = QtWidgets.QVBoxLayout(self.dialog)

        self.hint = QtWidgets.QLabel(_HINTS[_PICK_EDGE])
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.readout = QtWidgets.QLabel("")
        self.readout.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self.readout)

        form = QtWidgets.QFormLayout()
        self.height = QtWidgets.QDoubleSpinBox()
        self.height.setRange(0.0, 1000.0)
        self.height.setDecimals(4)
        self.height.setSingleStep(0.01)
        self.height.setSuffix(" mm")
        self.height.setSpecialValueText("follow the mouse")
        self.height.setToolTip("0 follows the cursor. Any other value is used "
                               "as the height, so the third click only sets "
                               "the direction.")
        form.addRow("Height:", self.height)

        self.impedance = QtWidgets.QDoubleSpinBox()
        self.impedance.setRange(0.1, 10000.0)
        self.impedance.setDecimals(1)
        self.impedance.setValue(50.0)
        self.impedance.setSuffix(" Ω")
        self.impedance.setToolTip("Recorded on the port for the solver. It "
                                  "does not change the geometry.")
        form.addRow("Impedance:", self.impedance)
        layout.addLayout(form)

        buttons = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("Cancel port")
        cancel.setToolTip("Abandon the port being drawn and start another (Esc)")
        cancel.clicked.connect(session.cancel_current)
        finish = QtWidgets.QPushButton("Finish")
        finish.setToolTip("Stop drawing ports")
        finish.clicked.connect(session.finish)
        buttons.addWidget(cancel)
        buttons.addStretch()
        buttons.addWidget(finish)
        layout.addLayout(buttons)

        self.dialog.finished.connect(lambda _result: session.finish())
        self.dialog.show()

    def update(self, state, length=None, height=None, direction=""):
        self.hint.setText(_HINTS.get(state, ""))
        parts = []
        if length is not None:
            parts.append(f"length {length:.4f} mm")
        if height:
            parts.append(f"height {abs(height):.4f} mm {direction}")
        self.readout.setText("   ".join(parts))

    def fixed_height(self):
        value = self.height.value()
        return value if value > 0.0 else None

    def close(self):
        try:
            self.dialog.finished.disconnect()
        except Exception:
            pass
        self.dialog.close()


class _PortSession:
    """The click-by-click state of drawing ports."""

    def __init__(self):
        self.doc = None
        self.view = None
        self.call = None
        self.panel = None
        self.active = False
        self._reset()

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self, doc, view, QtWidgets, QtCore, parent):
        self.doc = doc
        self.view = view
        self.active = True
        self._reset()
        self.call = view.addEventCallback("SoEvent", self.action)
        self.panel = _PortPanel(self, QtWidgets, QtCore, parent)
        FreeCAD.Console.PrintMessage(
            "[Port] Click an edge, click again along it to set the length, "
            "then move up or down and click to set the height. "
            "Esc abandons the port being drawn; Esc again finishes.\n")

    def finish(self):
        if not self.active:
            return
        self.active = False
        self._clear_preview()
        if self.call is not None and self.view is not None:
            try:
                self.view.removeEventCallback("SoEvent", self.call)
            except Exception:
                pass
        self.call = None
        if self.panel is not None:
            self.panel.close()
            self.panel = None
        from core import ports
        FreeCAD.Console.PrintMessage(
            f"[Port] Finished — the document has {len(ports.ports_of(self.doc))} "
            f"port(s).\n")

    def cancel_current(self):
        """Abandon the port being drawn, keep drawing others."""
        self._reset()
        self._clear_preview()
        if self.panel is not None:
            self.panel.update(_PICK_EDGE)

    # ── events ─────────────────────────────────────────────────────────

    def action(self, arg):
        if not self.active:
            return
        if arg["Type"] == "SoKeyboardEvent" and arg.get("Key", "") == "ESCAPE":
            if self.state == _PICK_EDGE:
                self.finish()
            else:
                self.cancel_current()
            return
        if arg["Type"] == "SoLocation2Event":
            self._on_move(arg.get("Position"))
            return
        if (arg["Type"] == "SoMouseButtonEvent" and arg["State"] == "DOWN"
                and arg["Button"] == "BUTTON1"):
            self._on_click(arg.get("Position"))

    def _on_move(self, position):
        if position is None:
            return
        if self.state == _PICK_END and self.start is not None:
            point = self._point_on_edge(position)
            if point is not None:
                self.end = point
                self._preview_line(self.start, self.end)
                self._report()
        elif self.state == _PICK_HEIGHT:
            self.height = self._height_at(position)
            self._preview_face()
            self._report()

    def _on_click(self, position):
        if position is None:
            return
        if self.state == _PICK_EDGE:
            picked = self._pick_edge(position)
            if picked is None:
                FreeCAD.Console.PrintWarning(
                    "[Port] That is not an edge — click an edge of the "
                    "geometry to start a port on it.\n")
                return
            self.owner, self.sub, self.edge, self.start = picked
            self.state = _PICK_END
        elif self.state == _PICK_END:
            point = self._point_on_edge(position)
            if point is None:
                return
            from core import ports
            if (point - self.start).Length < ports.MIN_EXTENT_MM:
                FreeCAD.Console.PrintWarning(
                    "[Port] The two points are the same — click further along "
                    "the edge to give the port a length.\n")
                return
            self.end = point
            self.state = _PICK_HEIGHT
        elif self.state == _PICK_HEIGHT:
            self.height = self._height_at(position)
            self._create()
        self._report()

    # ── geometry from the cursor ───────────────────────────────────────

    def _pick_edge(self, position):
        """(owner, sub-element, edge, point) under the cursor, or None."""
        try:
            info = self.view.getObjectInfo(tuple(position))
        except Exception:
            info = None
        if not info or not str(info.get("Component", "")).startswith("Edge"):
            return None
        owner = self.doc.getObject(info["Object"])
        if owner is None or not hasattr(owner, "Shape"):
            return None
        try:
            edge = owner.Shape.getElement(info["Component"])
        except Exception:
            return None
        from core import ports
        point = ports.snap_to_edge(
            edge, FreeCAD.Vector(info["x"], info["y"], info["z"]))
        return owner, info["Component"], edge, point

    def _point_on_edge(self, position):
        """The cursor, put back onto the edge the port started on — so the
        second click need not hit the edge exactly."""
        from core import ports
        try:
            info = self.view.getObjectInfo(tuple(position))
            raw = (FreeCAD.Vector(info["x"], info["y"], info["z"]) if info
                   else self.view.getPoint(tuple(position)))
        except Exception:
            return None
        return ports.snap_to_edge(self.edge, raw) if self.edge else raw

    def _height_at(self, position):
        """
        Signed height from the cursor: where the view ray crosses the vertical
        plane through the two points.

        Looking along that plane edge-on leaves nothing to intersect, so the
        cursor's own height is used instead — the drag still works when the
        camera happens to line up with the port.
        """
        fixed = self.panel.fixed_height() if self.panel else None
        try:
            cursor = FreeCAD.Vector(self.view.getPoint(tuple(position)))
            direction = FreeCAD.Vector(self.view.getViewDirection())
        except Exception:
            return self.height
        if fixed is not None:
            below = cursor.z < self.start.z
            return -fixed if below else fixed

        along = self.end - self.start
        normal = along.cross(FreeCAD.Vector(0, 0, 1))
        if normal.Length < 1e-9:                    # the edge itself is vertical
            normal = FreeCAD.Vector(0, 1, 0)
        normal.normalize()
        denominator = direction.dot(normal)
        if abs(denominator) < 1e-6:
            return cursor.z - self.start.z
        distance = (self.start - cursor).dot(normal) / denominator
        return (cursor + direction * distance).z - self.start.z

    # ── preview and creation ───────────────────────────────────────────

    def _preview_object(self):
        obj = self.doc.getObject(_PREVIEW_NAME)
        if obj is None:
            obj = self.doc.addObject("Part::Feature", _PREVIEW_NAME)
            obj.Label = "Port preview"
            if FreeCAD.GuiUp and obj.ViewObject is not None:
                obj.ViewObject.ShapeColor = (1.0, 0.35, 0.35)
                obj.ViewObject.LineColor = (1.0, 0.35, 0.35)
                obj.ViewObject.Transparency = 60
        return obj

    def _preview_line(self, start, end):
        import Part
        if (end - start).Length < 1e-9:
            return
        self._preview_object().Shape = Part.makeLine(start, end)
        FreeCADGui.updateGui()

    def _preview_face(self):
        from core import ports
        try:
            face = ports.port_face(self.start, self.end, abs(self.height),
                                   "+Z" if self.height >= 0 else "-Z")
        except ValueError:
            return
        self._preview_object().Shape = face
        FreeCADGui.updateGui()

    def _clear_preview(self):
        if self.doc is not None and self.doc.getObject(_PREVIEW_NAME) is not None:
            try:
                self.doc.removeObject(_PREVIEW_NAME)
            except Exception:
                pass
        FreeCADGui.updateGui()

    def _create(self):
        from core import ports
        impedance = self.panel.impedance.value() if self.panel else 50.0
        self.doc.openTransaction("Define Port")
        try:
            ports.make_port(self.doc, self.start, self.end, abs(self.height),
                            "+Z" if self.height >= 0 else "-Z",
                            source=(self.owner.Name if self.owner else "", self.sub),
                            impedance=impedance)
            self.doc.commitTransaction()
        except ValueError as exc:
            self.doc.abortTransaction()
            FreeCAD.Console.PrintWarning(f"[Port] {exc}\n")
            return
        except Exception as exc:
            self.doc.abortTransaction()
            FreeCAD.Console.PrintError(f"[Port] {exc}\n")
            return
        self.doc.recompute()
        self.cancel_current()

    # ── state ──────────────────────────────────────────────────────────

    def _reset(self):
        self.state = _PICK_EDGE
        self.owner = None
        self.sub = ""
        self.edge = None
        self.start = None
        self.end = None
        self.height = 0.0

    def _report(self):
        if self.panel is None:
            return
        length = ((self.end - self.start).Length
                  if self.start is not None and self.end is not None else None)
        self.panel.update(self.state, length, self.height,
                          "+Z" if self.height >= 0 else "-Z")


session = _PortSession()


class DefinePortCommand:
    """Draw ports on the layout until you stop."""

    def GetResources(self):
        return {
            "MenuText": "Define Port",
            "ToolTip":  "Draw simulation ports: click an edge, click again to "
                        "set the length, then move up or down to set the "
                        "height. Repeats until you finish.",
            "Pixmap":   get_icon("Define_Port.svg"),
        }

    def Activated(self):
        from compat import QtWidgets, QtCore

        doc = FreeCAD.activeDocument()
        if doc is None:
            return
        parent = FreeCADGui.getMainWindow()
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
        except Exception:
            view = None
        if view is None or not hasattr(view, "addEventCallback"):
            QtWidgets.QMessageBox.warning(
                parent, "Define Port",
                "This needs an open 3-D view to click in.")
            return
        if session.active:
            session.finish()
        session.start(doc, view, QtWidgets, QtCore, parent)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("DefinePortCommand", DefinePortCommand())
