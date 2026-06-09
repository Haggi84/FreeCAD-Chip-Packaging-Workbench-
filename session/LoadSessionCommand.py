# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Load Design Session command.

Opens a .dipas session file, shows a summary dialog, and optionally
replays every recorded action non-interactively to rebuild the design
from scratch using the saved parameters.
"""

import os
import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtCore

from Get_Path import get_icon
from session.SessionManager import session_manager, SESSION_EXT


# ---------------------------------------------------------------------------
# Replay helpers  (one per action type)
# ---------------------------------------------------------------------------

def _replay_gds_import(params):
    """Replay a standalone GDS import without opening any dialogs."""
    from gds.GDSCommand import load_gds_with_params

    load_gds_with_params(
        gds_path=params["gds_path"],
        lyp_path=params["lyp_path"],
        map_path=params.get("map_path"),
        selected_layers=params["selected_layers"],
        options=params["options"],
    )


def _replay_leadframe_config(params):
    """Replay a standalone leadframe creation."""
    from leadframe.LeadframeCommand import create_leadframe
    create_leadframe(params)


def _replay_layer_on_leadframe(params):
    """Replay the combined Layer-on-Leadframe workflow."""
    import FreeCAD as _FC
    import FreeCADGui as _FCGUI
    from compat import QtCore as _QC, QtWidgets as _QW
    from core import Core_Functionality
    from leadframe.LayeronLeadframe import configuration
    from ui.ExtendedPropertyPanel import ExtendedPropertyPanel

    gds_path        = params["gds_path"]
    lyp_path        = params["lyp_path"]
    map_path        = params.get("map_path")
    selected_layers = params["selected_layers"]
    options         = params["options"]
    config          = params["leadframe_config"]
    opts            = params["transform_opts"]

    ihp_map = Core_Functionality.parse_map(map_path) if map_path else {}

    doc = _FC.newDocument("Leadframe_Assembly")
    try:
        doc.openTransaction("Session Replay: Layer on Leadframe")
    except Exception:
        pass

    # Re-parse LYP so the panel gets fresh color data
    lyp_result = Core_Functionality.parse_lyp(lyp_path)
    unique_colors = lyp_result[1] if lyp_result else {}

    property_panel = ExtendedPropertyPanel(_FCGUI.getMainWindow())
    property_panel.set_map(ihp_map, map_path)
    property_panel.gds_path        = gds_path
    property_panel.lyp_path        = lyp_path
    property_panel.selected_layers = selected_layers
    property_panel.options         = dict(options)
    property_panel.leadframe_config = config
    property_panel.transform_opts  = opts
    _FCGUI.getMainWindow().addDockWidget(_QC.Qt.RightDockWidgetArea, property_panel)

    result = configuration(doc, gds_path, selected_layers, options, ihp_map, config, opts)
    if result:
        doc_out, layer_objects = result
        property_panel.update_properties(selected_layers, unique_colors, layer_objects)
        try:
            doc.commitTransaction()
        except Exception:
            pass
        doc.recompute()
        _FCGUI.activeDocument().activeView().viewIsometric()
        _FCGUI.SendMsgToActiveView("ViewFit")
    else:
        try:
            doc.abortTransaction()
        except Exception:
            pass
        raise RuntimeError("configuration() returned no result — check the GDS / LYP paths.")


def _replay_housing_config(params):
    """Replay housing creation."""
    from housing.HousingCommand import create_housing
    create_housing(params)


def _replay_center_leadframe():
    """Replay center-leadframe-on-GDS."""
    from leadframe.LeadframeCommand import center_leadframe_on_gds
    center_leadframe_on_gds()


def _replay_wirebond_config(params):
    """Restore wire bonding configuration (no bonds placed yet)."""
    from wirebond.ManualWireBonding import manual_bonder
    try:
        from wirebond.Wirebon_Confi_Support import check_wirebond_prerequisites
        ok, _ = check_wirebond_prerequisites()
        if ok:
            manual_bonder.start_bonding_session(params)
    except Exception:
        pass


def _replay_wirebond_placements(params):
    """Recreate all saved bond wires non-interactively."""
    from wirebond.ManualWireBonding import create_bond_wire_3d
    from FreeCAD import Base

    config = params.get("config", {})
    bonds  = params.get("bonds", [])
    if not bonds:
        return

    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument("WireBonding")

    _COLOR_WIRE = (0.90, 0.75, 0.20)

    for i, bond in enumerate(bonds):
        start = Base.Vector(bond["start"][0], bond["start"][1], bond["start"][2])
        end   = Base.Vector(bond["end"][0],   bond["end"][1],   bond["end"][2])

        doc.openTransaction(f"Replay Bond Wire {i + 1}")
        try:
            shape    = create_bond_wire_3d(start, end, config)
            idx      = i + 1
            wire_obj = doc.addObject("Part::Feature", f"BondWire_{idx:03d}")
            wire_obj.Shape = shape
            wire_obj.ViewObject.ShapeColor = _COLOR_WIRE
            wire_obj.ViewObject.LineColor  = _COLOR_WIRE
            wire_obj.ViewObject.LineWidth  = 2

            def _prop(ptype, name, grp, desc):
                if not hasattr(wire_obj, name):
                    wire_obj.addProperty(ptype, name, grp, desc)

            _prop("App::PropertyVector", "StartPoint", "Wirebond", "First contact point position")
            _prop("App::PropertyVector", "EndPoint",   "Wirebond", "Second contact point position")
            _prop("App::PropertyString", "StartCP",    "Wirebond", "First ContactPoint object name")
            _prop("App::PropertyString", "EndCP",      "Wirebond", "Second ContactPoint object name")
            _prop("App::PropertyString", "NetName",    "Wirebond", "Net identifier")
            _prop("App::PropertyLength", "WireLength", "Wirebond", "Wire arc length (mm)")

            wire_obj.StartPoint = start
            wire_obj.EndPoint   = end
            wire_obj.StartCP    = bond.get("start_cp", "")
            wire_obj.EndCP      = bond.get("end_cp",   "")
            wire_obj.NetName    = bond.get("net_name", f"Net_{idx:03d}")
            wire_obj.WireLength = (start - end).Length

            doc.commitTransaction()
            # NOTE: recompute() intentionally NOT called here.
            # A single recompute after all wires are created is far faster
            # than one per wire (was causing N full OCCT rebuilds for N bonds).

        except Exception as e:
            doc.abortTransaction()
            FreeCAD.Console.PrintError(f"Bond wire replay failed (bond {i + 1}): {e}\n")

    # One recompute for all wires together — replaces the per-wire recompute
    # that was previously inside the loop (N wires → N OCCT rebuilds → slow).
    if bonds:
        doc.recompute()
        FreeCAD.Console.PrintMessage(
            f"[Session Replay] {len(bonds)} bond wire(s) rebuilt in single recompute.\n"
        )


# ---------------------------------------------------------------------------
# Replay orchestrator
# ---------------------------------------------------------------------------

_ACTION_LABEL = {
    "gds_import":            "GDS Import",
    "leadframe_config":      "Leadframe Configurator",
    "layer_on_leadframe":    "Layer on Leadframe",
    "housing_config":        "Housing Configurator",
    "wirebond_config":       "Wire Bonding Config",
    "wirebond_placements":   "Wire Bond Placements",
    "center_leadframe":      "Center Leadframe",
}


def replay_session(session_data, parent=None):
    """Execute every recorded action in order using the saved parameters.

    Actions that are superseded by *layer_on_leadframe* (namely
    *gds_import* and *leadframe_config*) are skipped automatically,
    because that workflow already contains both steps.
    Wire bonding actions are noted but cannot be replayed automatically.
    """
    actions = sorted(session_data.get("actions", []), key=lambda a: a["id"])
    if not actions:
        QtWidgets.QMessageBox.information(parent, "Replay", "No actions to replay.")
        return

    has_lol = any(a["type"] == "layer_on_leadframe" for a in actions)

    errors           = []
    wb_config_action = None
    wb_place_action  = None

    for action in actions:
        atype  = action["type"]
        params = action["params"]

        # layer_on_leadframe subsumes standalone gds_import + leadframe_config
        if has_lol and atype in ("gds_import", "leadframe_config"):
            FreeCAD.Console.PrintMessage(
                f"Session replay: skipping '{atype}' "
                "(superseded by layer_on_leadframe)\n"
            )
            continue

        # Collect wire-bond actions for deferred execution (geometry must exist first)
        if atype == "wirebond_config":
            wb_config_action = action
            continue
        if atype == "wirebond_placements":
            wb_place_action = action
            continue

        FreeCAD.Console.PrintMessage(
            f"Session replay: executing '{_ACTION_LABEL.get(atype, atype)}' …\n"
        )
        try:
            if atype == "gds_import":
                _replay_gds_import(params)
            elif atype == "leadframe_config":
                _replay_leadframe_config(params)
            elif atype == "layer_on_leadframe":
                _replay_layer_on_leadframe(params)
            elif atype == "housing_config":
                _replay_housing_config(params)
            elif atype == "center_leadframe":
                _replay_center_leadframe()
        except Exception as exc:
            FreeCAD.Console.PrintError(
                f"Session replay error in '{atype}': {exc}\n"
            )
            errors.append(f"{_ACTION_LABEL.get(atype, atype)}: {exc}")

    # Wire bonds — replay placed bonds if available, otherwise just restore config
    if wb_place_action:
        FreeCAD.Console.PrintMessage("Session replay: recreating wire bonds …\n")
        try:
            _replay_wirebond_placements(wb_place_action["params"])
        except Exception as exc:
            FreeCAD.Console.PrintError(f"Session replay error in wire bonds: {exc}\n")
            errors.append(f"Wire Bond Placements: {exc}")
    elif wb_config_action:
        try:
            _replay_wirebond_config(wb_config_action["params"])
        except Exception:
            pass

    # Summary message
    lines = ["Session replayed successfully."]
    if wb_config_action and not wb_place_action:
        lines.append(
            "\nNote: Wire bonding configuration was restored but no bonds "
            "were saved — place bonds manually using the Wire Bonding tool."
        )
    if errors:
        lines.append("\nErrors encountered during replay:")
        lines.extend(f"  • {e}" for e in errors)

    QtWidgets.QMessageBox.information(parent, "Session Replay", "\n".join(lines))


# ---------------------------------------------------------------------------
# Summary dialog
# ---------------------------------------------------------------------------

class SessionSummaryDialog(QtWidgets.QDialog):
    """Shows the contents of a .dipas session file and offers replay."""

    def __init__(self, session_data, session_path, parent=None):
        super().__init__(parent)
        self._data = session_data
        self._path = session_path
        self._want_replay = False
        self._build_ui()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("Design Session")
        self.setMinimumWidth(540)
        self.setMinimumHeight(440)

        root = QtWidgets.QVBoxLayout(self)

        # ── Header info ──────────────────────────────────────────────────
        info_grp  = QtWidgets.QGroupBox("Session Info")
        info_form = QtWidgets.QFormLayout(info_grp)
        info_form.addRow("File:",     QtWidgets.QLabel(os.path.basename(self._path)))
        info_form.addRow("Created:",  QtWidgets.QLabel(self._data.get("created",  "–")[:19]))
        info_form.addRow("Modified:", QtWidgets.QLabel(self._data.get("modified", "–")[:19]))
        fcstd = self._data.get("freecad_document") or "(not associated)"
        info_form.addRow("Document:", QtWidgets.QLabel(fcstd))
        root.addWidget(info_grp)

        # ── Actions list ─────────────────────────────────────────────────
        act_grp = QtWidgets.QGroupBox("Recorded Actions")
        act_vbox = QtWidgets.QVBoxLayout(act_grp)
        text = QtWidgets.QTextEdit()
        text.setReadOnly(True)
        text.setFontFamily("Consolas, Courier New, monospace")
        text.setPlainText(self._format_actions())
        act_vbox.addWidget(text)
        root.addWidget(act_grp, stretch=1)

        # ── Buttons ──────────────────────────────────────────────────────
        btn_row = QtWidgets.QHBoxLayout()

        fcstd_path = self._data.get("freecad_document")
        self.btn_open = QtWidgets.QPushButton("Open Document")
        self.btn_open.setEnabled(bool(fcstd_path and os.path.exists(fcstd_path)))
        self.btn_open.setToolTip("Open the associated .FCStd file in FreeCAD")
        self.btn_open.clicked.connect(self._open_document)

        btn_replay = QtWidgets.QPushButton("Replay Session")
        btn_replay.setDefault(True)
        btn_replay.setToolTip(
            "Rebuild the design from scratch using the saved parameters.\n"
            "This creates a new FreeCAD document."
        )
        btn_replay.clicked.connect(self._on_replay)

        btn_close = QtWidgets.QPushButton("Close")
        btn_close.setToolTip("Close this dialog without replaying")
        btn_close.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_open)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        btn_row.addWidget(btn_replay)
        root.addLayout(btn_row)

    # ── Formatting helpers ─────────────────────────────────────────────────

    def _format_actions(self):
        actions = self._data.get("actions", [])
        if not actions:
            return "No actions recorded."
        lines = []
        for a in actions:
            ts = a.get("timestamp", "")[:19]
            lines.append(f"[{a['id']}] {_ACTION_LABEL.get(a['type'], a['type'])}  ({ts})")
            lines.append(_describe_params(a["type"], a.get("params", {})))
            lines.append("")
        return "\n".join(lines)

    # ── Button handlers ────────────────────────────────────────────────────

    def _open_document(self):
        fcstd = self._data.get("freecad_document")
        if fcstd and os.path.exists(fcstd):
            FreeCAD.openDocument(fcstd)
            self.reject()

    def _on_replay(self):
        self._want_replay = True
        self.accept()

    @property
    def want_replay(self):
        return self._want_replay


def _describe_params(action_type, params):
    """Return a multi-line description string for a single action."""
    lines = []
    if action_type == "gds_import":
        lines.append(f"  GDS:    {os.path.basename(params.get('gds_path', ''))}")
        lines.append(f"  LYP:    {os.path.basename(params.get('lyp_path', ''))}")
        mp = params.get("map_path")
        if mp:
            lines.append(f"  MAP:    {os.path.basename(mp)}")
        n = len(params.get("selected_layers", []))
        lines.append(f"  Layers: {n} selected")
        opts  = params.get("options", {})
        flags = []
        if opts.get("extrude_3d"):        flags.append("3D extrude")
        if opts.get("match_klayout"):     flags.append("KLayout colours")
        if opts.get("highlight_bondable"):flags.append("highlight bondable")
        if opts.get("contacts_only_3d"):  flags.append("contacts-only 3D")
        if opts.get("auto_pin_contacts"): flags.append("auto PIN")
        if flags:
            lines.append(f"  Flags:  {', '.join(flags)}")

    elif action_type == "leadframe_config":
        ft = params.get("frame_type", "?")
        l  = params.get("frame_length", "?")
        w  = params.get("frame_width",  "?")
        lines.append(f"  Type:   {ft}")
        lines.append(f"  Size:   {l} × {w} mm,  t={params.get('frame_thickness','?')} mm")
        n_leads = sum(
            params.get(f"{s}_lead_count", 0)
            for s in ("left", "right", "top", "bottom")
        )
        if n_leads:
            lines.append(
                f"  Leads:  {n_leads} total  "
                f"(L={params.get('left_lead_count',0)} "
                f"R={params.get('right_lead_count',0)} "
                f"T={params.get('top_lead_count',0)} "
                f"B={params.get('bottom_lead_count',0)})"
            )
        lines.append(f"  Material: {params.get('material','?')}")

    elif action_type == "layer_on_leadframe":
        lines.append(f"  GDS:    {os.path.basename(params.get('gds_path', ''))}")
        mp = params.get("map_path")
        if mp:
            lines.append(f"  MAP:    {os.path.basename(mp)}")
        n = len(params.get("selected_layers", []))
        lines.append(f"  Layers: {n} selected")
        lfc = params.get("leadframe_config", {})
        if lfc:
            lines.append(
                f"  Frame:  {lfc.get('frame_type','?')}  "
                f"{lfc.get('frame_length','?')} × {lfc.get('frame_width','?')} mm"
            )
        tr = params.get("transform_opts", {})
        if tr:
            lines.append(
                f"  Transform: rot={tr.get('rot_deg',0)}°  "
                f"mirror_y={tr.get('mirror_y',False)}  "
                f"auto_fit={tr.get('auto_fit',True)}"
            )

    elif action_type == "housing_config":
        lines.append(f"  Type:     {params.get('frame_type','?')}")
        lines.append(
            f"  Size:     {params.get('frame_length','?')} × "
            f"{params.get('frame_width','?')} mm"
        )
        lines.append(
            f"  Height:   {params.get('housing_height','?')} mm  "
            f"Wall: {params.get('wall_thickness','?')} mm"
        )
        lines.append(f"  Material: {params.get('material','?')}")
        if params.get("include_lid"):
            lines.append(f"  Lid:      {params.get('lid_thickness','?')} mm")

    elif action_type == "wirebond_config":
        lines.append(f"  Wire Ø:      {params.get('diameter','?')} mm")
        lines.append(f"  Loop height: {params.get('loop_height','?')} mm")
        lines.append(
            f"  Length:      {params.get('min_wire_length','?')} – "
            f"{params.get('max_wire_length','?')} mm"
        )
        lines.append(f"  Spacing:     {params.get('min_wire_spacing','?')} mm")

    elif action_type == "wirebond_placements":
        cfg   = params.get("config", {})
        bonds = params.get("bonds", [])
        lines.append(f"  Bonds placed: {len(bonds)}")
        lines.append(f"  Wire Ø:       {cfg.get('diameter','?')} mm")
        lines.append(f"  Loop height:  {cfg.get('loop_height','?')} mm")
        for i, b in enumerate(bonds):
            s = b.get("start", [0, 0, 0])
            e = b.get("end",   [0, 0, 0])
            lines.append(
                f"  Bond {i+1:03d}: "
                f"{b.get('start_cp','?')} → {b.get('end_cp','?')}  "
                f"({s[0]:.3f},{s[1]:.3f},{s[2]:.3f}) → "
                f"({e[0]:.3f},{e[1]:.3f},{e[2]:.3f})"
            )

    elif action_type == "center_leadframe":
        lines.append("  (no parameters)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FreeCAD command
# ---------------------------------------------------------------------------

class LoadSessionCommand:
    def GetResources(self):
        return {
            "MenuText": "Load Design Session",
            "ToolTip":  (
                "Load a .dipas session file to review or replay a previous design."
            ),
            "Pixmap": get_icon("Load_Session.svg"),
        }

    def Activated(self):
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            None,
            "Load Design Session",
            "",
            f"DI-PASSIONATE Session (*{SESSION_EXT});;All Files (*)",
        )
        if not filepath or not os.path.exists(filepath):
            return

        try:
            data = session_manager.load(filepath)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                None, "Load Failed", f"Could not read session file:\n{exc}"
            )
            return

        dlg = SessionSummaryDialog(data, filepath, FreeCADGui.getMainWindow())
        dlg.exec_()

        if dlg.want_replay:
            replay_session(data, parent=FreeCADGui.getMainWindow())

    def IsActive(self):
        return True


FreeCADGui.addCommand("LoadSessionCommand", LoadSessionCommand())
