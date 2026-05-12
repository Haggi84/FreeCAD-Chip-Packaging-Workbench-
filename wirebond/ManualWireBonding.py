"""
Manual wire bonding — contact-point filter and 3-D swept-tube geometry.

Session flow
------------
1. User runs "Manual Wire Bonding" → WirebondConfigurator dialog.
2. Session starts; only ContactPoint markers are selectable (everything
   else is rejected and immediately deselected).
3. Click 1 — first ContactPoint  (die-side or leadframe-side, order free).
   A green snap-marker appears at the selected point.
4. Click 2 — second ContactPoint (must be a different object).
   A 3-D bond wire is created between the two ContactPoint positions.
5. Repeat from step 3 for the next bond.
6. "Finish Wire Bonding" ends the session and prints a report.

Wire geometry
-------------
Parabolic BSpline arc (5 control points) swept with a circular cross-section
of diameter = config['diameter'].  Falls back to a pipe shell, then a line.
"""

import FreeCAD
import FreeCADGui
import Part
from FreeCAD import Base

import os, sys
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)
from session.SessionManager import session_manager


# ── colour constants ───────────────────────────────────────────────────────────
_COLOR_WIRE       = (0.90, 0.75, 0.20)   # gold
_COLOR_SNAP_VALID = (0.10, 0.90, 0.10)   # green  — valid hover / first pick
_COLOR_SNAP_WAIT  = (0.10, 0.50, 0.90)   # blue   — first pick locked in


# ── snap-point resolution ──────────────────────────────────────────────────────

def resolve_snap_point(obj) -> Base.Vector:
    """
    Return the stored ContactPoint position of *obj*.
    Falls back to top-face centre, then BoundBox centre.
    """
    if getattr(obj, "IsContactPoint", False):
        cp = getattr(obj, "ContactPoint", None)
        if cp is not None:
            return Base.Vector(cp)

    shape = getattr(obj, "Shape", None)
    if shape is None:
        return Base.Vector(0, 0, 0)
    if shape.Faces:
        top_face = max(shape.Faces, key=lambda f: f.CenterOfMass.z)
        return top_face.CenterOfMass
    return Base.Vector(shape.BoundBox.Center)


# ── 3-D bond-wire geometry ─────────────────────────────────────────────────────

def create_bond_wire_3d(start: Base.Vector, end: Base.Vector, config: dict) -> Part.Shape:
    """
    Return a 3-D solid bond wire between *start* and *end*.

    Path  : parabolic BSpline through 5 points (start, q1, peak, q2, end).
    Profile: circle of radius = config['diameter'] / 2.
    Fallback chain: solid → open pipe shell → plain line.
    """
    loop_height = float(config.get("loop_height", 0.3))
    radius      = float(config.get("diameter",    0.025)) / 2.0

    z_top = max(start.z, end.z) + loop_height
    peak  = Base.Vector((start.x + end.x) / 2.0,
                        (start.y + end.y) / 2.0,
                        z_top)
    q1    = Base.Vector((start.x + peak.x) / 2.0,
                        (start.y + peak.y) / 2.0,
                        (start.z + peak.z) / 2.0)
    q2    = Base.Vector((peak.x + end.x) / 2.0,
                        (peak.y + end.y) / 2.0,
                        (peak.z + end.z) / 2.0)
    try:
        bspline = Part.BSplineCurve()
        bspline.interpolate([start, q1, peak, q2, end])
        spine_edge = bspline.toShape()
        spine_wire = Part.Wire([spine_edge])

        t_start = spine_edge.tangentAt(spine_edge.FirstParameter).normalize()
        circle_s = Part.makeCircle(radius, start, t_start)
        profile  = Part.Wire([Part.Edge(circle_s)])
        pipe     = spine_wire.makePipe(profile)

        # Close the open-ended pipe with flat end caps to form a true solid.
        try:
            cap_start = Part.Face(profile)

            t_end    = spine_edge.tangentAt(spine_edge.LastParameter).normalize()
            circle_e = Part.makeCircle(radius, end, t_end)
            cap_end  = Part.Face(Part.Wire([Part.Edge(circle_e)]))

            shell = Part.makeShell(list(pipe.Faces) + [cap_start, cap_end])
            solid = Part.makeSolid(shell)
            if solid.isValid():
                return solid
        except Exception as cap_err:
            FreeCAD.Console.PrintWarning(f"Wire end-cap failed ({cap_err}); using shell.\n")

        # Fallback: return the open pipe shell
        return pipe
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"3-D wire sweep failed ({e}); using line.\n")
        return Part.makeLine(start, end)


# ── contact-point filter ───────────────────────────────────────────────────────

def _is_contact_point(obj) -> bool:
    return getattr(obj, "IsContactPoint", False)


class _ContactPointGate:
    """
    FreeCAD SelectionGate that allows only ContactPoint objects.
    Installed when a wire bonding session starts so that non-CP objects
    receive no hover highlight and cannot be clicked at all.

    FreeCAD 1.x passes the Document / DocumentObject directly; older builds
    pass name strings.  Both cases are handled below.
    """
    def allow(self, doc, obj, sub) -> bool:
        try:
            fc_obj = obj if not isinstance(obj, str) else (
                (FreeCAD.getDocument(doc) if isinstance(doc, str) else doc).getObject(obj)
            )
            return fc_obj is not None and getattr(fc_obj, "IsContactPoint", False)
        except Exception:
            return False


# ── state constants ────────────────────────────────────────────────────────────

class _State:
    IDLE         = "idle"
    AWAIT_FIRST  = "await_first"
    AWAIT_SECOND = "await_second"


# ── main class ─────────────────────────────────────────────────────────────────

class ManualWireBonding:
    """
    Wire bonding session controller with strict ContactPoint-only filter.

    Only objects with IsContactPoint = True are accepted.  Clicking any
    other object clears the selection immediately so the user gets clear
    visual feedback that the click was rejected.
    """

    def __init__(self):
        self.bonds             = []          # list of completed bond dicts
        self.first_cp          = None        # first ContactPoint object
        self.first_pt          = None        # its resolved snap point
        self.doc               = None
        self.config            = None
        self.is_active         = False
        self.state             = _State.IDLE
        self._highlighted      = None
        self._highlighted_orig = None

    # ── session lifecycle ──────────────────────────────────────────────────

    def start_bonding_session(self, config: dict):
        if self.is_active:
            self.cancel_session()

        self.config    = config
        self.bonds     = []
        self.first_cp  = None
        self.first_pt  = None
        self.is_active = True
        self.state     = _State.AWAIT_FIRST
        self.doc       = FreeCAD.activeDocument() or FreeCAD.newDocument("WireBonding")

        FreeCADGui.Selection.addObserver(self)
        FreeCADGui.Selection.addSelectionGate(_ContactPointGate())
        self._set_status("Wire bonding — click the first contact point (die pad)")
        FreeCAD.Console.PrintMessage(
            "Wire bonding started.\n"
            "  Step 1: click a ContactPoint on the die.\n"
            "  Step 2: click a ContactPoint on the leadframe.\n"
            "  Repeat. Use 'Finish Wire Bonding' when done.\n"
        )

    def finish_session(self) -> int:
        if not self.is_active:
            return 0
        self._teardown()
        count = len(self.bonds)
        FreeCAD.Console.PrintMessage(f"Wire bonding finished — {count} bond(s).\n")
        self._report()
        return count

    def cancel_session(self):
        self._teardown()
        self.bonds    = []
        self.first_cp = None
        self.first_pt = None
        FreeCAD.Console.PrintMessage("Wire bonding cancelled.\n")

    def _teardown(self):
        self.is_active = False
        self.state     = _State.IDLE
        self._clear_highlight()
        try:
            FreeCADGui.Selection.removeSelectionGate()
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"removeSelectionGate: {e}\n")
        try:
            FreeCADGui.Selection.removeObserver(self)
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"removeObserver: {e}\n")
        self._set_status("")

    # ── FreeCAD Selection observer callbacks ───────────────────────────────

    def setPreselection(self, doc, obj_name, sub):
        """Highlight ContactPoint objects green on hover; ignore everything else."""
        if not self.is_active:
            return
        try:
            obj = FreeCAD.getDocument(doc).getObject(obj_name)
            if obj is None or obj is self._highlighted:
                return
            if _is_contact_point(obj):
                self._clear_highlight()
                self._highlighted      = obj
                self._highlighted_orig = obj.ViewObject.LineColor
                obj.ViewObject.LineColor = _COLOR_SNAP_VALID
        except Exception:
            pass

    def removePreselection(self, doc, obj_name, sub):
        if not self.is_active:
            return
        self._clear_highlight()

    def addSelection(self, doc, obj_name, sub, pos):
        """
        Accept only ContactPoint objects.
        Any other click is immediately cleared from the FreeCAD selection
        so the 3D view gives clear visual feedback of the rejection.
        """
        if not self.is_active or not self.config:
            return
        try:
            obj = FreeCAD.getDocument(doc).getObject(obj_name)
            if obj is None:
                return

            # ── Reject non-ContactPoint objects ────────────────────────────
            if not _is_contact_point(obj):
                FreeCADGui.Selection.clearSelection()
                self._set_status(
                    "Wire bonding — only ContactPoint markers can be selected"
                )
                FreeCAD.Console.PrintWarning(
                    f"'{obj.Name}' is not a ContactPoint — skipped.\n"
                    "Select a ContactPoint marker (blue dot on leadframe or orange dot on die).\n"
                )
                return

            # ── First pick ─────────────────────────────────────────────────
            if self.state == _State.AWAIT_FIRST:
                self.first_cp = obj
                self.first_pt = resolve_snap_point(obj)
                self._create_temp_marker(self.first_pt)
                self.state = _State.AWAIT_SECOND
                self._set_status(
                    f"First point: {obj.Label} — now click the second contact point"
                )
                FreeCAD.Console.PrintMessage(
                    f"  First CP: {obj.Name}  "
                    f"pos=({self.first_pt.x:.3f}, {self.first_pt.y:.3f}, {self.first_pt.z:.3f})\n"
                )

            # ── Second pick ────────────────────────────────────────────────
            elif self.state == _State.AWAIT_SECOND:
                if obj is self.first_cp:
                    FreeCAD.Console.PrintWarning(
                        "Same contact point selected — pick a different one.\n"
                    )
                    return
                second_pt = resolve_snap_point(obj)
                self._place_wire(self.first_pt, second_pt, self.first_cp, obj)
                # Reset for the next bond
                self.first_cp = None
                self.first_pt = None
                self.state    = _State.AWAIT_FIRST
                self._set_status("Wire bonding — click the first contact point (die pad)")

        except Exception as e:
            FreeCAD.Console.PrintError(f"Wire bonding error: {e}\n")
            import traceback
            FreeCAD.Console.PrintError(traceback.format_exc())

    # ── wire placement ─────────────────────────────────────────────────────

    def _place_wire(self, start: Base.Vector, end: Base.Vector, cp1, cp2):
        """Create one bond wire in its own undo transaction."""
        doc = self.doc
        doc.openTransaction("Place Bond Wire")
        try:
            shape    = create_bond_wire_3d(start, end, self.config)
            idx      = len(self.bonds) + 1
            wire_obj = doc.addObject("Part::Feature", f"BondWire_{idx:03d}")
            wire_obj.Shape = shape

            wire_obj.ViewObject.ShapeColor = _COLOR_WIRE
            wire_obj.ViewObject.LineColor  = _COLOR_WIRE
            wire_obj.ViewObject.LineWidth  = 2

            def _prop(ptype, name, grp, desc):
                if not hasattr(wire_obj, name):
                    wire_obj.addProperty(ptype, name, grp, desc)

            _prop("App::PropertyVector", "StartPoint",  "Wirebond", "First contact point position")
            _prop("App::PropertyVector", "EndPoint",    "Wirebond", "Second contact point position")
            _prop("App::PropertyString", "StartCP",     "Wirebond", "First ContactPoint object name")
            _prop("App::PropertyString", "EndCP",       "Wirebond", "Second ContactPoint object name")
            _prop("App::PropertyString", "NetName",     "Wirebond", "Net identifier")
            _prop("App::PropertyLength", "WireLength",  "Wirebond", "Wire arc length (mm)")

            wire_obj.StartPoint = start
            wire_obj.EndPoint   = end
            wire_obj.StartCP    = cp1.Name
            wire_obj.EndCP      = cp2.Name
            wire_obj.NetName    = f"Net_{idx:03d}"
            # Use straight-line distance as a meaningful arc-length approximation.
            # shape.Length on a swept solid returns total edge length (all profile
            # circles included), which is not the wire arc length.
            wire_obj.WireLength = (start - end).Length

            doc.commitTransaction()
            doc.recompute()

            self.bonds.append({
                "cp1": cp1, "cp2": cp2,
                "start": start, "end": end,
                "wire": wire_obj,
            })

            # Update session record with the full cumulative bond list
            session_manager.record_action("wirebond_placements", {
                "config": self.config,
                "bonds": [
                    {
                        "start":    [b["start"].x, b["start"].y, b["start"].z],
                        "end":      [b["end"].x,   b["end"].y,   b["end"].z],
                        "start_cp": b["cp1"].Name,
                        "end_cp":   b["cp2"].Name,
                        "net_name": getattr(b["wire"], "NetName", f"Net_{j+1:03d}"),
                    }
                    for j, b in enumerate(self.bonds)
                ],
            })

            FreeCAD.Console.PrintMessage(
                f"  Bond {idx:03d}: {cp1.Name} -> {cp2.Name}  "
                f"length={shape.Length:.3f} mm\n"
            )

        except Exception as e:
            doc.abortTransaction()
            FreeCAD.Console.PrintError(f"Wire placement failed: {e}\n")

    # ── helpers ────────────────────────────────────────────────────────────

    def _create_temp_marker(self, pos: Base.Vector):
        """Green sphere at the first pick; auto-removed after 3 s."""
        try:
            from compat import QtCore
            m = self.doc.addObject("Part::Sphere", "_SnapMarker")
            m.Radius = 0.06
            # Must assign a new Placement object — modifying .Base in-place
            # only changes a Python copy and has no effect on the actual object.
            m.Placement = FreeCAD.Placement(pos, FreeCAD.Rotation(0, 0, 0, 1))
            m.ViewObject.ShapeColor = _COLOR_SNAP_VALID
            m.ViewObject.Transparency = 20
            self.doc.recompute()
            QtCore.QTimer.singleShot(3000, lambda: self._remove_marker(m))
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"Snap marker: {e}\n")

    def _remove_marker(self, marker):
        try:
            if self.doc and marker in self.doc.Objects:
                self.doc.removeObject(marker.Name)
                self.doc.recompute()
        except Exception:
            pass

    def _clear_highlight(self):
        if self._highlighted is not None:
            try:
                self._highlighted.ViewObject.LineColor = self._highlighted_orig
            except Exception:
                pass
            self._highlighted      = None
            self._highlighted_orig = None

    @staticmethod
    def _set_status(msg: str):
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            if hasattr(view, "setStatusBarMessage"):
                view.setStatusBarMessage(msg)
        except Exception:
            pass

    def _report(self):
        if not self.bonds:
            FreeCAD.Console.PrintMessage("No bonds were created.\n")
            return
        total = sum((b["start"] - b["end"]).Length for b in self.bonds)
        lines = [
            f"  Bond {i+1:03d}: {b['cp1'].Name} -> {b['cp2'].Name}"
            f"  {(b['start'] - b['end']).Length:.3f} mm"
            for i, b in enumerate(self.bonds)
        ]
        FreeCAD.Console.PrintMessage(
            "=== Wire Bonding Report ===\n"
            + "\n".join(lines)
            + f"\n  Total: {len(self.bonds)} bonds, {total:.3f} mm wire\n"
        )


# Module-level singleton shared across all WirebondCommand instances.
manual_bonder = ManualWireBonding()
