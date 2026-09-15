# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
What each solid in a package assembly is made of.

A thermal model needs one thing the geometry cannot supply: a material per
volume. The stackup XML names the die body's materials but only gives their
ELECTRICAL properties (permittivity, conductivity), and nothing else in the
document records a material at all — so this module adds both halves:

  * a small library of the materials this workbench actually builds, with
    nominal room-temperature thermal properties, and
  * assign_materials(), which tags every physical solid with one of them
    from what the document already knows about it — the stackup material a
    die-body slab records, the material chosen in the Leadframe or Housing
    Configurator, or the fixed kind of object it is (a bond wire, a trace).

What it will NOT do is guess. A GDS interconnect layer is "Type=Conductor"
in the stackup, which does not say which metal; a STEP package or PCB from
elsewhere carries no material at all. Those are reported as unassigned with
the reason, and left for a person to set — a wrong conductivity would look
exactly as plausible as a right one in the finished simulation.

The property values are nominal bulk figures at about 25 °C. Real parts vary
(thin films especially, and mould compounds by grade); they are a sensible
starting point, not a datasheet.

Qt-free, so it is testable headlessly.
"""

from collections import namedtuple

import FreeCAD

UNASSIGNED = "Unassigned"

PROPERTY = "PackageMaterial"
SOURCE_PROPERTY = "PackageMaterialSource"
_GROUP = "Material"

# Where the choice came from when a person set it in the property editor
# rather than assign_materials(). Anything already set is kept unless the
# caller asks to overwrite.
SOURCE_USER = "user"

Material = namedtuple("Material", [
    "name",
    "kind",                   # semiconductor, dielectric, metal, solder, encapsulant, polymer, laminate
    "thermal_conductivity",   # W/(m·K)
    "density",                # kg/m³
    "specific_heat",          # J/(kg·K)
    "cte_ppm",                # 1e-6/K, linear, below Tg for polymers
    "note",
])

LIBRARY = {m.name: m for m in (
    Material("Silicon",              "semiconductor", 149.0, 2329.0,  705.0,  2.6,
             "Bulk silicon; also used for the epitaxial layer"),
    Material("Silicon dioxide",      "dielectric",      1.4, 2200.0,  730.0,  0.5,
             "Inter-metal dielectric"),
    Material("Aluminium",            "metal",         237.0, 2700.0,  897.0, 23.1, ""),
    Material("Copper",               "metal",         398.0, 8960.0,  385.0, 16.5, ""),
    Material("Gold",                 "metal",         318.0, 19300.0, 129.0, 14.2, ""),
    Material("Silver",               "metal",         429.0, 10490.0, 235.0, 18.9, ""),
    Material("Alloy 42",             "metal",          12.0, 8110.0,  502.0,  4.5,
             "Fe-42Ni leadframe alloy"),
    Material("SAC305 solder",        "solder",         58.0, 7400.0,  230.0, 21.7,
             "Sn-3.0Ag-0.5Cu"),
    Material("Epoxy mould compound", "encapsulant",     0.9, 1900.0,  900.0, 10.0,
             "Typical filled EMC; varies strongly by grade"),
    Material("Polycarbonate",        "polymer",         0.20, 1200.0, 1200.0, 67.0, ""),
    Material("Acrylic (PMMA)",       "polymer",         0.19, 1180.0, 1450.0, 70.0, ""),
    Material("ABS",                  "polymer",         0.17, 1080.0, 1400.0, 90.0,
             "Transparent (methyl methacrylate) ABS"),
    Material("FR-4",                 "laminate",        0.30, 1850.0, 1100.0, 16.0,
             "Through-plane conductivity; CTE in-plane"),
)}

# Materials that fill the space around everything else. Where one of these
# overlaps a metal or the die, the export gives that volume to the part it
# surrounds rather than counting it twice.
FILLER_KINDS = frozenset({"encapsulant", "polymer"})

# Names the rest of the workbench already uses for these materials — the
# stackup's <Material Name=…>, the configurator combo boxes.
_ALIASES = {
    "SUBSTRATE": "Silicon",
    "EPI": "Silicon",
    "SI": "Silicon",
    "SIO2": "Silicon dioxide",
    "ACRYLIC": "Acrylic (PMMA)",
    "PMMA": "Acrylic (PMMA)",
    "TRANSPARENT ABS": "ABS",
    "SAC305": "SAC305 solder",
    "EMC": "Epoxy mould compound",
    "MOLD COMPOUND": "Epoxy mould compound",
    "MOULD COMPOUND": "Epoxy mould compound",
}


def names():
    """Library names in a stable order, UNASSIGNED first — the choices the
    property editor offers."""
    return [UNASSIGNED] + list(LIBRARY)


def resolve(name):
    """The library Material for *name* (a library name or a known alias,
    case-insensitive), or None."""
    key = str(name or "").strip()
    if not key:
        return None
    if key in LIBRARY:
        return LIBRARY[key]
    upper = key.upper()
    for lib_name, mat in LIBRARY.items():
        if lib_name.upper() == upper:
            return mat
    alias = _ALIASES.get(upper)
    return LIBRARY.get(alias) if alias else None


# ── which objects are physical parts ─────────────────────────────────────────

def _solids(obj):
    shp = getattr(obj, "Shape", None)
    try:
        if shp is None or shp.isNull():
            return []
        return list(shp.Solids)
    except Exception:
        return []


def _is_consumed(obj):
    """True when another shape feature uses this one as input — the housing's
    outer extrusion inside its Cut, say. Exporting both would count the same
    volume twice."""
    for parent in getattr(obj, "InList", []) or []:
        try:
            if parent.isDerivedFrom("Part::Feature"):
                return True
        except Exception:
            continue
    return False


def is_physical_part(obj):
    """A solid that belongs in a physical model: real volume, not a helper
    marker, not an intermediate another feature is built from."""
    try:
        if not obj.isDerivedFrom("Part::Feature"):
            return False
    except Exception:
        return False
    for flag in ("IsContactPoint", "IsGridPoint", "IsRoutingGridPoint",
                 "IsLayerPlaceholder"):
        if getattr(obj, flag, False):
            return False
    if obj.Name.startswith(("TracePreview_", "_Snap")):
        return False
    try:
        grp = obj.getParentGeoFeatureGroup()
        if grp is not None and grp.isDerivedFrom("PartDesign::Body"):
            return False            # a history state of the Body's one solid
    except Exception:
        pass
    if _is_consumed(obj):
        return False
    return any(s.Volume > 1e-12 for s in _solids(obj))


def physical_parts(doc):
    return [o for o in (doc.Objects if doc else []) if is_physical_part(o)]


# ── classification ───────────────────────────────────────────────────────────

def classify(obj):
    """
    (material_name, source) for an object this module knows how to assign,
    or (None, reason) when it does not.
    """
    if getattr(obj, "IsDieBody", False):
        stack = getattr(obj, "StackMaterial", "")
        mat = resolve(stack)
        if mat:
            return mat.name, f"stackup: {stack}"
        return None, f"stackup material '{stack}' is not in the library"

    if getattr(obj, "IsChipProxy", False):
        return "Silicon", "chip proxy: modelled as bulk silicon"

    if getattr(obj, "IsRoutingTrace", False):
        return "Copper", "default: routed trace"

    if obj.Name.startswith("BondWire_"):
        return "Gold", "default: bond wire"

    if getattr(obj, "IsWireBump", False):
        return "Gold", "default: wire bump"

    if getattr(obj, "IsLeadFinger", False) and getattr(obj, "LeadSide", "") == "BGA":
        return "SAC305 solder", "default: BGA ball"

    if getattr(obj, "IsLeadFinger", False) or getattr(obj, "IsDiePaddle", False):
        chosen = getattr(obj, "LeadframeMaterial", "")
        mat = resolve(chosen)
        if mat:
            return mat.name, f"leadframe: {chosen}"
        if chosen:
            return None, f"leadframe material '{chosen}' is not in the library"
        return None, "leadframe built before materials were recorded — set by hand"

    if obj.Name == "LeadframeBody":
        return "Epoxy mould compound", "default: leadframe body"

    chosen = getattr(obj, "HousingMaterial", "")
    if chosen:
        mat = resolve(chosen)
        if mat:
            return mat.name, f"housing: {chosen}"
        return None, f"housing material '{chosen}' is not in the library"

    if obj.Name.startswith("Layer_"):
        return None, ("interconnect layer: the stackup says 'conductor', not "
                      "which metal — set by hand")
    return None, "no material known for this object — set by hand"


# ── reading and writing the property ─────────────────────────────────────────

def material_of(obj):
    """The Material assigned to *obj*, or None."""
    return resolve(getattr(obj, PROPERTY, None))


def _ensure_properties(obj):
    if not hasattr(obj, PROPERTY):
        obj.addProperty("App::PropertyEnumeration", PROPERTY, _GROUP,
                        "Material this solid is made of, for simulation export")
    if not hasattr(obj, SOURCE_PROPERTY):
        obj.addProperty("App::PropertyString", SOURCE_PROPERTY, _GROUP,
                        "Where the material came from — 'user' when set by hand")
    current = getattr(obj, PROPERTY)
    # Re-listing keeps the current value, and picks up library additions made
    # since the property was first created.
    setattr(obj, PROPERTY, names())
    if current in LIBRARY:
        setattr(obj, PROPERTY, current)


def set_material(obj, name, source=SOURCE_USER):
    mat = resolve(name)
    if mat is None:
        raise ValueError(f"'{name}' is not a known material")
    _ensure_properties(obj)
    setattr(obj, PROPERTY, mat.name)
    setattr(obj, SOURCE_PROPERTY, source)


def assign_materials(doc, overwrite=False):
    """
    Tag every physical part in *doc* with a material.

    A material that is already set is kept unless *overwrite* — that is what
    makes a choice made by hand survive re-running this. Parts it cannot
    classify still get the property, set to UNASSIGNED, so the property
    editor offers the list.

    Returns {"assigned": [(name, material, source)],
             "kept":     [(name, material, source)],
             "unassigned": [(name, reason)]}.
    """
    report = {"assigned": [], "kept": [], "unassigned": []}
    for obj in physical_parts(doc):
        existing = material_of(obj)
        if existing is not None and not overwrite:
            _ensure_properties(obj)
            source = getattr(obj, SOURCE_PROPERTY, "") or SOURCE_USER
            report["kept"].append((obj.Name, existing.name, source))
            continue

        name, detail = classify(obj)
        if name is None:
            _ensure_properties(obj)
            setattr(obj, PROPERTY, UNASSIGNED)
            setattr(obj, SOURCE_PROPERTY, "")
            report["unassigned"].append((obj.Name, detail))
            continue

        set_material(obj, name, detail)
        report["assigned"].append((obj.Name, name, detail))

    FreeCAD.Console.PrintMessage(
        f"[Materials] {len(report['assigned'])} assigned, "
        f"{len(report['kept'])} kept, {len(report['unassigned'])} unassigned\n")
    return report
