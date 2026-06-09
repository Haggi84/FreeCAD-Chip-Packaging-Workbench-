"""
Stackup helpers — layer thickness lookup, vertical ordering, and Z-position
dictionaries used by load_gds() for 3D extrusion.
"""

_NORM = lambda s: (s or "").upper()

# Default metal/via thicknesses [µm] for IHP SG13G2.
# Override by loading an ELayers stackup XML (parse_stackup_xml + build_stack_mm_from_xml).
THICKNESS_UM = {
    "METAL1": 0.9,  "METAL2": 0.9, "METAL3": 0.9, "METAL4": 1.2, "METAL5": 2.0,
    "TOPMETAL1": 2.0, "TOPMETAL2": 3.0,
    "VIA1": 0.5, "VIA2": 0.5, "VIA3": 0.5, "VIA4": 0.5,
    "TOPVIA1": 1.0, "TOPVIA2": 1.0,
    "COMP": 0.2,
}

ILD_SPACING_UM = 0.8   # default inter-layer dielectric gap [µm]


def thickness_um_for_edi(edi_name: str) -> float:
    """Best-effort thickness in µm for an EDI layer name."""
    n = _NORM(edi_name).replace("/", "_")
    if n in THICKNESS_UM:
        return THICKNESS_UM[n]
    for key in THICKNESS_UM:
        if n.startswith(key):
            return THICKNESS_UM[key]
    if "METAL" in n:
        return 1.0
    if "VIA" in n:
        return 0.5
    return 0.2


def stack_rank_for_edi(edi_name: str) -> int:
    """
    Vertical sort key — higher rank = closer to the top of the stack.
    TopMetal2 > TopMetal1 > Metal5 > … > Metal1 > COMP > Vias.
    """
    n = _NORM(edi_name)
    _digits = lambda s: int("".join(c for c in s if c.isdigit()) or "0")

    if n.startswith("TOPMETAL"):
        return 600 + 100 * _digits(n)
    if n.startswith("METAL"):
        return 100 * max(_digits(n), 1)
    if n.startswith("COMP"):
        return 50
    if n.startswith("TOPVIA"):
        return 650
    if n.startswith("VIA"):
        return 100 * max(_digits(n), 1) + 10
    return 0


def build_stack_mm(selected_layers, ihp_map, ild_um: float = ILD_SPACING_UM) -> dict:
    """
    Build a per-layer stacking dictionary using heuristic thickness + rank ordering.

    Returns: {(layer_id, datatype): {'t_mm': float, 'z0_mm': float}}
    """
    entries = []
    for L in selected_layers:
        lid = L.get("layer_id", 0)
        dt  = L.get("datatype",  0)
        m   = (ihp_map or {}).get((lid, dt))
        edi = m["edi_name"] if m else L.get("name", "Metal1")
        entries.append(((lid, dt), edi, stack_rank_for_edi(edi), thickness_um_for_edi(edi)))

    entries.sort(key=lambda e: e[2])  # ascending rank = bottom to top

    z_um = 0.0
    out  = {}
    for idx, (key, edi, rank, t_um) in enumerate(entries):
        out[key] = {"t_mm": t_um / 1000.0, "z0_mm": z_um / 1000.0}
        z_um += t_um
        if idx < len(entries) - 1 and "METAL" in _NORM(entries[idx + 1][1]):
            z_um += ild_um

    return out


def build_stack_mm_from_xml(selected_layers, ihp_map, stackup_data) -> dict:
    """
    Build the stacking dict using absolute Z positions from a parsed ELayers XML.

    Falls back to build_stack_mm() for any layer not found in the XML.
    """
    if not stackup_data:
        return build_stack_mm(selected_layers, ihp_map)

    out              = {}
    fallback_layers  = []

    for L in selected_layers:
        lid = L.get("layer_id", 0)
        dt  = L.get("datatype",  0)
        key = (lid, dt)

        entry = stackup_data.get(lid)
        if entry is None:
            m = (ihp_map or {}).get(key)
            if m:
                entry = stackup_data.get(m["edi_name"].upper())

        if entry is not None:
            out[key] = {
                "t_mm":  entry["thickness_um"] / 1000.0,
                "z0_mm": entry["zmin_um"]       / 1000.0,
            }
        else:
            fallback_layers.append(L)

    if fallback_layers:
        out.update(build_stack_mm(fallback_layers, ihp_map))

    return out
