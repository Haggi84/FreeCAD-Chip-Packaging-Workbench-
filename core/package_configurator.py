# SPDX-License-Identifier: GPL-3.0-or-later
"""Requirement and recommendation model for package selection.

This module intentionally has no FreeCAD or Qt dependency so recommendations
can also be used by scripted/package-generation workflows.
"""

from dataclasses import dataclass, field
from typing import Optional

from leadframe.PackageDatabase import PackageSpec, ALL_PACKAGES


@dataclass
class ChipInfo:
    length_mm: float = 0.0
    width_mm: float = 0.0
    thickness_mm: float = 0.0
    pad_count: int = 0
    source: str = "User supplied"
    notes: str = ""


@dataclass
class PackageRequirements:
    max_length_mm: Optional[float] = None
    max_width_mm: Optional[float] = None
    max_height_mm: Optional[float] = None
    min_supported_pitch_mm: Optional[float] = None
    thermal_performance: str = ""
    electrical_performance: str = ""
    rework: str = ""
    cost: str = ""
    preferred_family: str = ""
    mounting: str = ""


@dataclass
class PackageRecommendation:
    package: PackageSpec
    score: float
    reasons: list[str] = field(default_factory=list)


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


_THERMAL_RANK = {
    "Low-Moderate": 1,
    "Moderate": 2,
    "Good": 3,
    "Excellent": 4,
}
_ELECTRICAL_RANK = {"Low": 1, "Good": 2, "Excellent": 3}
_REWORK_RANK = {"Easy": 4, "Medium": 3, "Hard": 2, "Very Hard": 1}
_COST_RANK = {"Low": 3, "Medium": 2, "High": 1}


def extract_chip_info(doc) -> ChipInfo:
    """Derive package-relevant facts from an imported GDS/proxy document."""
    if doc is None:
        return ChipInfo()

    candidates = []
    for obj in getattr(doc, "Objects", []):
        if getattr(obj, "IsChipProxy", False):
            candidates.append(obj)
    if not candidates:
        candidates = [
            obj for obj in getattr(doc, "Objects", [])
            if getattr(obj, "Name", "").startswith(("Layer_", "IC_Body_Solid"))
            and getattr(obj, "Shape", None) is not None
        ]

    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    zmin = float("inf")
    source = "GDS geometry"
    for obj in candidates:
        shape = getattr(obj, "Shape", None)
        if shape is None or getattr(shape, "isNull", lambda: True)():
            continue
        bb = shape.BoundBox
        pl = getattr(obj, "Placement", None)
        base = getattr(pl, "Base", None)
        dx, dy, dz = (getattr(base, "x", 0.0), getattr(base, "y", 0.0),
                      getattr(base, "z", 0.0))
        xmin, xmax = min(xmin, bb.XMin + dx), max(xmax, bb.XMax + dx)
        ymin, ymax = min(ymin, bb.YMin + dy), max(ymax, bb.YMax + dy)
        zmin = min(zmin, bb.ZMin + dz)
        if getattr(obj, "IsChipProxy", False):
            source = "GDS chip proxy"

    pads = [
        obj for obj in getattr(doc, "Objects", [])
        if getattr(obj, "IsContactPoint", False)
        and str(getattr(obj, "SourceObject", "")).startswith(("GDS_",))
    ]
    if xmin == float("inf"):
        return ChipInfo(pad_count=len(pads), source=source)

    thickness = 0.0
    for obj in candidates:
        if getattr(obj, "IsChipProxy", False):
            shape = getattr(obj, "Shape", None)
            if shape is not None:
                thickness = max(thickness, float(shape.BoundBox.ZLength))
            break
    return ChipInfo(
        length_mm=max(0.0, xmax - xmin),
        width_mm=max(0.0, ymax - ymin),
        thickness_mm=thickness,
        pad_count=len(pads),
        source=source,
    )


def recommend_packages(chip: ChipInfo, requirements: PackageRequirements,
                        packages=None) -> list[PackageRecommendation]:
    """Return compatible catalogue entries ordered from best to worst."""
    packages = ALL_PACKAGES if packages is None else packages
    result = []
    for pkg in packages:
        reasons = []
        if requirements.preferred_family and pkg.family != requirements.preferred_family:
            continue
        if chip.length_mm and pkg.body_length_mm < chip.length_mm:
            continue
        if chip.width_mm and pkg.body_width_mm < chip.width_mm:
            continue
        if requirements.max_length_mm and pkg.body_length_mm > requirements.max_length_mm:
            continue
        if requirements.max_width_mm and pkg.body_width_mm > requirements.max_width_mm:
            continue
        if requirements.max_height_mm and pkg.body_height_mm > requirements.max_height_mm:
            continue
        if chip.pad_count and pkg.total_pins < chip.pad_count:
            continue
        pitch = pkg.bga_ball_pitch_mm if pkg.family == "BGA" else pkg.lead_pitch_mm
        if (requirements.min_supported_pitch_mm is not None
                and pitch < requirements.min_supported_pitch_mm):
            continue
        if requirements.mounting and pkg.mounting != requirements.mounting:
            continue

        score = 100.0
        score -= max(0.0, pkg.body_length_mm - chip.length_mm) * 2
        score -= max(0.0, pkg.body_width_mm - chip.width_mm) * 2
        score -= max(0, pkg.total_pins - chip.pad_count) * 0.15
        if requirements.preferred_family:
            score += 20
            reasons.append("preferred family")
        if chip.pad_count and pkg.total_pins == chip.pad_count:
            score += 12
            reasons.append("pin count matches detected pads")
        if requirements.min_supported_pitch_mm is not None:
            reasons.append("meets PCB pitch capability")
        if chip.length_mm and chip.width_mm:
            reasons.append("fits detected die footprint")
        if requirements.thermal_performance:
            required = _THERMAL_RANK.get(requirements.thermal_performance, 0)
            actual = _THERMAL_RANK.get(pkg.thermal_performance, 0)
            if actual >= required:
                score += 10
                reasons.append(f"{pkg.thermal_performance.lower()} thermal capability")
            else:
                score -= (required - actual) * 10
                reasons.append(
                    f"below {requirements.thermal_performance.lower()} thermal target"
                )
        if requirements.electrical_performance:
            required = _ELECTRICAL_RANK.get(requirements.electrical_performance, 0)
            actual = _ELECTRICAL_RANK.get(pkg.electrical_performance, 0)
            if actual >= required:
                score += 10
                reasons.append(
                    f"{pkg.electrical_performance.lower()} electrical capability"
                )
            else:
                score -= (required - actual) * 10
                reasons.append(
                    f"below {requirements.electrical_performance.lower()} "
                    "electrical target"
                )
        if requirements.rework:
            requested = _REWORK_RANK.get(requirements.rework, 0)
            actual = _REWORK_RANK.get(pkg.rework, 0)
            if actual >= requested:
                score += 8
                reasons.append(f"{pkg.rework.lower()} rework")
            else:
                score -= (actual - requested) * 8
                reasons.append(f"{pkg.rework.lower()} rework")
        if requirements.cost:
            requested = _COST_RANK.get(requirements.cost, 0)
            actual = _COST_RANK.get(pkg.cost, 0)
            if actual >= requested:
                score += 8
                reasons.append(f"{pkg.cost.lower()} cost")
            else:
                score -= (actual - requested) * 8
                reasons.append(f"{pkg.cost.lower()} cost")
        if requirements.mounting:
            score += 10
            reasons.append(f"{pkg.mounting} mounting")
        result.append(PackageRecommendation(pkg, score, reasons))
    return sorted(result, key=lambda item: (-item.score, item.package.name))
