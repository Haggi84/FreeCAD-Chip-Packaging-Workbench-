"""
TogglePerformanceModeCommand
============================
Toggles the GDS viewport between Detail and Performance (Wireframe) mode.

The switch sets ViewObject.Deviation / DisplayMode per layer.  Because
FreeCAD re-tessellates each shape synchronously on the main thread when
Deviation changes, this can take several seconds for chips with many layers.
A progress dialog with per-layer feedback keeps the UI from appearing frozen.
"""

import FreeCAD
import FreeCADGui
from Get_Path import get_icon
from compat import QtWidgets, QtCore

# ── tuneable constants ─────────────────────────────────────────────────────────

_DETAIL_DEVIATION  = 0.5
_FAST_DEVIATION    = 8.0
_DETAIL_ANGULAR    = 28.5
_FAST_ANGULAR      = 57.0
_DISPLAY_DETAIL    = "Flat Lines"
_DISPLAY_FAST      = "Wireframe"
_FILL_LAYER_HINTS  = ("fill", "filler", "dummy", "block")

# ── module-level state ─────────────────────────────────────────────────────────

_fast_mode: bool  = False
_saved_props: dict = {}


# ── helpers ────────────────────────────────────────────────────────────────────

def _is_fill_layer(obj) -> bool:
    name  = (obj.Name  or "").lower()
    label = (obj.Label or "").lower()
    return any(h in name or h in label for h in _FILL_LAYER_HINTS)


def _gds_layer_objects(doc):
    gds_group = next(
        (o for o in doc.Objects if o.Name == "GDS_Die" or o.Label == "GDS_Die"),
        None,
    )
    candidates = getattr(gds_group, "Group", []) if gds_group else [
        o for o in doc.Objects if o.Name.startswith("Layer_")
    ]
    for obj in candidates:
        vobj = getattr(obj, "ViewObject", None)
        if vobj is not None:
            yield obj, vobj


def _make_progress(title: str, n: int) -> QtWidgets.QProgressDialog:
    """Create and immediately show a non-cancelable progress dialog."""
    dlg = QtWidgets.QProgressDialog(
        title, None, 0, max(n, 1),
        FreeCADGui.getMainWindow(),
    )
    dlg.setWindowTitle("Rendering")
    dlg.setWindowModality(QtCore.Qt.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setMinimumWidth(360)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    return dlg


# ── mode transitions ───────────────────────────────────────────────────────────

def _enter_fast_mode(doc):
    global _saved_props
    _saved_props = {}

    pairs = list(_gds_layer_objects(doc))
    n     = len(pairs)
    if n == 0:
        return

    dlg = _make_progress(f"Switching to Wireframe mode…  (0 / {n})", n)

    for i, (obj, vobj) in enumerate(pairs):
        label = obj.Label or obj.Name
        dlg.setLabelText(f"Wireframe mode — processing layer {i + 1} / {n}\n{label}")
        dlg.setValue(i)
        QtWidgets.QApplication.processEvents()

        _saved_props[obj.Name] = (
            getattr(vobj, "Deviation",         _DETAIL_DEVIATION),
            getattr(vobj, "AngularDeflection", _DETAIL_ANGULAR),
            getattr(vobj, "DisplayMode",       _DISPLAY_DETAIL),
            getattr(vobj, "Visibility",        True),
        )
        try:
            vobj.Deviation         = _FAST_DEVIATION
            vobj.AngularDeflection = _FAST_ANGULAR
            vobj.DisplayMode       = _DISPLAY_FAST
            if _is_fill_layer(obj):
                vobj.Visibility = False
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[PerfMode] '{obj.Name}': {exc}\n")

    dlg.setValue(n)
    dlg.setLabelText(f"Wireframe mode — done ({n} layer(s))")
    QtWidgets.QApplication.processEvents()
    dlg.close()
    FreeCAD.Console.PrintMessage(f"[PerfMode] Wireframe mode ON — {n} layer(s)\n")


def _enter_detail_mode(doc):
    pairs = list(_gds_layer_objects(doc))
    n     = len(pairs)
    if n == 0:
        return

    dlg = _make_progress(f"Switching to Detail mode…  (0 / {n})", n)

    restored = 0
    for i, (obj, vobj) in enumerate(pairs):
        label = obj.Label or obj.Name
        dlg.setLabelText(f"Detail mode — processing layer {i + 1} / {n}\n{label}")
        dlg.setValue(i)
        QtWidgets.QApplication.processEvents()

        saved = _saved_props.get(obj.Name,
                (_DETAIL_DEVIATION, _DETAIL_ANGULAR, _DISPLAY_DETAIL, True))
        dev, ang, disp, vis = saved
        try:
            vobj.Deviation         = dev
            vobj.AngularDeflection = ang
            vobj.DisplayMode       = disp
            vobj.Visibility        = vis
            restored += 1
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[PerfMode] '{obj.Name}': {exc}\n")

    dlg.setValue(n)
    dlg.setLabelText(f"Detail mode — done ({restored} layer(s))")
    QtWidgets.QApplication.processEvents()
    dlg.close()
    FreeCAD.Console.PrintMessage(f"[PerfMode] Detail mode ON — {restored} layer(s)\n")


# ── public API ─────────────────────────────────────────────────────────────────

def apply_performance_mode(doc):
    global _fast_mode
    _enter_fast_mode(doc)
    _fast_mode = True


def apply_detail_mode(doc):
    global _fast_mode
    _enter_detail_mode(doc)
    _fast_mode = False


def set_layer_detail(obj, detail: bool):
    vobj = getattr(obj, "ViewObject", None)
    if vobj is None:
        return
    try:
        if detail:
            vobj.Deviation         = _DETAIL_DEVIATION
            vobj.AngularDeflection = _DETAIL_ANGULAR
            vobj.DisplayMode       = _DISPLAY_DETAIL
            vobj.Visibility        = True
        else:
            vobj.Deviation         = _FAST_DEVIATION
            vobj.AngularDeflection = _FAST_ANGULAR
            vobj.DisplayMode       = _DISPLAY_FAST
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[PerfMode] set_layer_detail '{obj.Name}': {exc}\n"
        )


# ── FreeCAD command ────────────────────────────────────────────────────────────

class TogglePerformanceModeCommand:

    def GetResources(self):
        mode = "Detail" if _fast_mode else "Wireframe"
        return {
            "MenuText": f"Toggle Render Mode  [{mode}]",
            "ToolTip": (
                "Switch GDS layers between Detail and Wireframe rendering.\n"
                "\n"
                "Wireframe  →  fast pan / zoom / rotate on large chips.\n"
                "Detail     →  full shading for wire bonding & measurements.\n"
                "\n"
                "A progress bar shows rendering status during the switch.\n"
                f"Current mode: {mode}"
            ),
            "Pixmap": get_icon("Performance_Mode.svg"),
        }

    def IsActive(self):
        doc = FreeCAD.activeDocument()
        if doc is None:
            return False
        return any(
            o.Name.startswith("Layer_") or o.Name == "GDS_Die"
            for o in doc.Objects
        )

    def Activated(self):
        global _fast_mode

        doc = FreeCAD.activeDocument()
        if doc is None:
            return

        _fast_mode = not _fast_mode

        if _fast_mode:
            _enter_fast_mode(doc)
        else:
            _enter_detail_mode(doc)

        FreeCADGui.updateGui()
        mode_str = "Wireframe" if _fast_mode else "Detail"
        FreeCAD.Console.PrintMessage(f"[PerfMode] Now in {mode_str} mode.\n")


FreeCADGui.addCommand("TogglePerformanceModeCommand", TogglePerformanceModeCommand())
