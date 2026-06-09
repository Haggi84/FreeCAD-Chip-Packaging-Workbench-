"""
Layer classification and visual-style helpers.
"""
from .stackup import stack_rank_for_edi

_BOND_MARKERS  = {"PIN", "LEFPIN", "BUMP", "PAD"}
_ROUTING_TYPES = {"NET", "SPNET", "VIA", "DRAWING"}


def is_bondable(types: set) -> bool:
    """Return True when *types* contains a bond-marker but no routing types."""
    if not types:
        return False
    T = {t.upper() for t in types}
    return bool(_BOND_MARKERS & T) and not bool(_ROUTING_TYPES & T)


def identify_contact_layers(selected_layers, ihp_map):
    """
    Return (top_keys, bottom_keys) as sets of (layer_id, datatype).

    top_keys    — bondable / PIN layers at the highest stack rank
    bottom_keys — lowest-rank non-via / non-fill layer

    Falls back to layer_id ordering when ihp_map is absent.
    """
    entries = []
    for L in selected_layers:
        lid  = L.get("layer_id", 0)
        dt   = L.get("datatype",  0)
        key  = (lid, dt)
        m    = (ihp_map or {}).get(key)
        edi   = m["edi_name"]  if m else ""
        types = m["edi_types"] if m else set()
        entries.append((key, stack_rank_for_edi(edi), is_bondable(types), edi, types))

    if not entries:
        return set(), set()

    bondable = [(k, r) for k, r, b, *_ in entries if b]
    if bondable:
        max_rank = max(r for _, r in bondable)
        top_keys = {k for k, r in bondable if r >= max_rank - 100}
    else:
        max_rank = max(r for _, r, *_ in entries)
        top_keys = {k for k, r, *_ in entries if r == max_rank}

    non_via_fill = [
        (k, r) for k, r, _, edi, types in entries
        if "VIA" not in edi.upper()
        and "FILL" not in {t.upper() for t in types}
    ]
    if non_via_fill:
        min_rank    = min(r for _, r in non_via_fill)
        bottom_keys = {k for k, r in non_via_fill if r == min_rank}
    else:
        min_rank    = min(r for _, r, *_ in entries)
        bottom_keys = {k for k, r, *_ in entries if r == min_rank}

    bottom_keys -= top_keys
    return top_keys, bottom_keys


def style_for_material(edi_name: str, edi_types: set):
    """
    Return (material_label, shape_rgb, line_rgb, transparency).
    Bondable layers get a gold highlight; fill layers are semi-transparent.
    """
    en = (edi_name or "").upper()
    et = {t.upper() for t in (edi_types or set())}

    if is_bondable(et):
        return "Bondable metal",          (0.90, 0.75, 0.20), (0.25, 0.20, 0.10),  0
    if "VIA" in et and "FILL" not in et:
        return "Via metal",               (0.35, 0.35, 0.35), (0.08, 0.08, 0.08),  0
    if "FILL" in et:
        return "Metal fill / dielectric", (0.70, 0.85, 1.00), (0.25, 0.35, 0.45), 70
    if en.startswith("TOPMETAL") or en.startswith("METAL") or "METAL" in et:
        return "Routing metal",           (0.60, 0.60, 0.60), (0.12, 0.12, 0.12),  0
    if en.startswith("COMP") or en.startswith("DIEAREA") or "DIE" in et:
        return "Component/Die",           (0.80, 0.90, 0.95), (0.25, 0.35, 0.45), 60
    return "Generic",                     (0.75, 0.75, 0.75), (0.10, 0.10, 0.10),  0
