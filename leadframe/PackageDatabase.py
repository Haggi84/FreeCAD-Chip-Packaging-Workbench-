# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
PackageDatabase
===============
Parametric IC package catalogue based on JEDEC / IPC standards.

All dimensions are in mm.  Each entry produces a config dict compatible
with ``core.leadframe.build_leadframe()``.

Package families
----------------
  QFN   JEDEC MO-220  —  Quad Flat No-lead, square and rectangular
  QFP   JEDEC MS-026  —  Quad Flat Package (gull-wing)
  DIP   JEDEC MS-001  —  Dual In-line Package (modelled as QFP with 2 sides)
  SOIC  JEDEC MS-012  —  Small Outline IC
  TSSOP JEDEC MO-153  —  Thin Shrink Small Outline Package
  BGA   JEDEC MS-034  —  Ball Grid Array

Usage
-----
    from leadframe.PackageDatabase import search_packages, get_config

    results = search_packages("QFN-24")        # → [PackageSpec, …]
    cfg     = results[0].to_leadframe_config() # → dict for build_leadframe()

    # or: iterate all
    from leadframe.PackageDatabase import ALL_PACKAGES
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Tuple


# ── data container ─────────────────────────────────────────────────────────────

@dataclass
class PackageSpec:
    """One standardised package variant."""

    name:         str            # canonical name, e.g. "QFN-24"
    family:       str            # "QFN" | "QFP" | "DIP" | "SOIC" | "TSSOP" | "BGA"
    standard:     str            # JEDEC / IPC reference
    description:  str            # human-readable

    # Body
    body_length_mm:    float     # X  (frame_length)
    body_width_mm:     float     # Y  (frame_width)
    body_height_mm:    float     # Z  (frame_thickness)

    # QFN / QFP / DIP / SOIC / TSSOP — lead geometry
    total_pins:        int       = 0
    pins_lr:           int       = 0      # pins per left / right side
    pins_tb:           int       = 0      # pins per top  / bottom side (0 for 2-sided)
    lead_pitch_mm:     float     = 0.5
    lead_width_mm:     float     = 0.25
    inner_lead_mm:     float     = 0.4    # bond-finger depth inside body
    outer_lead_mm:     float     = 0.6    # gull-wing length (QFP/SOIC only)
    has_die_paddle:    bool      = True
    paddle_length_mm:  float     = 0.0   # 0 → auto (55% of body)
    paddle_width_mm:   float     = 0.0

    # BGA — ball geometry
    bga_ball_dia_mm:   float     = 0.0
    bga_ball_pitch_mm: float     = 0.0

    # Display
    tags:  List[str]  = field(default_factory=list)  # e.g. ["RF", "open-cavity"]

    # ── derived ────────────────────────────────────────────────────────────

    @property
    def frame_type_str(self) -> str:
        if self.family in ("QFN",):
            return "QFN (Quad Flat No-lead)"
        if self.family in ("QFP", "DIP", "SOIC", "TSSOP"):
            return "QFP (Quad Flat Package)"
        if self.family == "BGA":
            return "BGA (Ball Grid Array)"
        return self.family

    def to_leadframe_config(self, material: str = "Copper") -> dict:
        """Return a dict ready for ``core.leadframe.build_leadframe()``."""
        cfg: dict = {
            "frame_type":      self.frame_type_str,
            "frame_length":    self.body_length_mm,
            "frame_width":     self.body_width_mm,
            "frame_thickness": self.body_height_mm,
            "material":        material,
        }
        if self.family in ("QFN", "QFP", "DIP", "SOIC", "TSSOP"):
            # For 2-sided packages (DIP, SOIC) pins_tb = 0 → no top/bottom leads
            cfg.update({
                "left_lead_count":   self.pins_lr,
                "right_lead_count":  self.pins_lr,
                "top_lead_count":    self.pins_tb,
                "bottom_lead_count": self.pins_tb,
                "lead_width":        self.lead_width_mm,
                "lead_pitch":        self.lead_pitch_mm,
                "inner_lead_length": self.inner_lead_mm,
                "lead_length":       self.outer_lead_mm,
                "qfn_pad_thickness": self.body_height_mm,
                "has_die_paddle":    self.has_die_paddle,
                "die_paddle_length": self.paddle_length_mm or round(self.body_length_mm * 0.55, 3),
                "die_paddle_width":  self.paddle_width_mm  or round(self.body_width_mm  * 0.55, 3),
            })
        elif self.family == "BGA":
            cfg.update({
                "bga_ball_diameter": self.bga_ball_dia_mm,
                "bga_ball_pitch":    self.bga_ball_pitch_mm,
            })
        return cfg

    def summary(self) -> str:
        """Single-line summary for display in the UI."""
        if self.family == "BGA":
            return (f"{self.name}  —  {self.body_length_mm}×{self.body_width_mm}×"
                    f"{self.body_height_mm} mm  |  ⌀{self.bga_ball_dia_mm} mm balls "
                    f"@ {self.bga_ball_pitch_mm} mm pitch  [{self.standard}]")
        two_sided = self.pins_tb == 0
        pin_desc  = (f"{self.pins_lr}×2={self.total_pins} pins  pitch={self.lead_pitch_mm} mm"
                     if two_sided else
                     f"{self.pins_lr}×L/R + {self.pins_tb}×T/B  pitch={self.lead_pitch_mm} mm")
        return (f"{self.name}  —  {self.body_length_mm}×{self.body_width_mm}×"
                f"{self.body_height_mm} mm  |  {pin_desc}  [{self.standard}]")


# ── JEDEC / IPC catalogue ──────────────────────────────────────────────────────
#
# Sources:
#   JEDEC MO-220   QFN (Quad Flat No-lead)
#   JEDEC MS-026   QFP (Quad Flat Package)
#   JEDEC MS-001   DIP (Dual In-line Package)
#   JEDEC MS-012   SOIC (Small Outline IC)
#   JEDEC MO-153   TSSOP (Thin Shrink Small Outline Package)
#   JEDEC MS-034   BGA (Ball Grid Array)
#   IPC-7351B      Land pattern guidelines
#
# Dimensions are nominal (not minimum/maximum).  "inner_lead_mm" is
# approximated as body_width/2 * 0.4 for QFN, or standard finger depth.

ALL_PACKAGES: List[PackageSpec] = [

    # ══════════════════════════════════════════════════════
    # QFN  (JEDEC MO-220)
    # ══════════════════════════════════════════════════════

    PackageSpec("QFN-8",  "QFN", "JEDEC MO-220",
                "8-lead 2×2 mm QFN, 0.5 mm pitch",
                body_length_mm=2.0, body_width_mm=2.0, body_height_mm=0.9,
                total_pins=8, pins_lr=2, pins_tb=2,
                lead_pitch_mm=0.50, lead_width_mm=0.25, inner_lead_mm=0.30,
                paddle_length_mm=1.40, paddle_width_mm=1.40),

    PackageSpec("QFN-12", "QFN", "JEDEC MO-220",
                "12-lead 3×3 mm QFN, 0.65 mm pitch",
                body_length_mm=3.0, body_width_mm=3.0, body_height_mm=0.9,
                total_pins=12, pins_lr=3, pins_tb=3,
                lead_pitch_mm=0.65, lead_width_mm=0.30, inner_lead_mm=0.40,
                paddle_length_mm=1.70, paddle_width_mm=1.70),

    PackageSpec("QFN-16", "QFN", "JEDEC MO-220",
                "16-lead 4×4 mm QFN, 0.65 mm pitch",
                body_length_mm=4.0, body_width_mm=4.0, body_height_mm=0.9,
                total_pins=16, pins_lr=4, pins_tb=4,
                lead_pitch_mm=0.65, lead_width_mm=0.30, inner_lead_mm=0.40,
                paddle_length_mm=2.60, paddle_width_mm=2.60),

    PackageSpec("QFN-20", "QFN", "JEDEC MO-220",
                "20-lead 4×4 mm QFN, 0.5 mm pitch",
                body_length_mm=4.0, body_width_mm=4.0, body_height_mm=0.9,
                total_pins=20, pins_lr=5, pins_tb=5,
                lead_pitch_mm=0.50, lead_width_mm=0.25, inner_lead_mm=0.40,
                paddle_length_mm=2.60, paddle_width_mm=2.60),

    PackageSpec("QFN-24", "QFN", "JEDEC MO-220",
                "24-lead 4×4 mm QFN, 0.5 mm pitch",
                body_length_mm=4.0, body_width_mm=4.0, body_height_mm=0.9,
                total_pins=24, pins_lr=6, pins_tb=6,
                lead_pitch_mm=0.50, lead_width_mm=0.25, inner_lead_mm=0.40,
                paddle_length_mm=2.50, paddle_width_mm=2.50),

    PackageSpec("QFN-28", "QFN", "JEDEC MO-220",
                "28-lead 5×5 mm QFN, 0.5 mm pitch",
                body_length_mm=5.0, body_width_mm=5.0, body_height_mm=0.9,
                total_pins=28, pins_lr=7, pins_tb=7,
                lead_pitch_mm=0.50, lead_width_mm=0.25, inner_lead_mm=0.45,
                paddle_length_mm=3.50, paddle_width_mm=3.50),

    PackageSpec("QFN-32", "QFN", "JEDEC MO-220",
                "32-lead 5×5 mm QFN, 0.5 mm pitch",
                body_length_mm=5.0, body_width_mm=5.0, body_height_mm=0.9,
                total_pins=32, pins_lr=8, pins_tb=8,
                lead_pitch_mm=0.50, lead_width_mm=0.25, inner_lead_mm=0.45,
                paddle_length_mm=3.40, paddle_width_mm=3.40),

    PackageSpec("QFN-40", "QFN", "JEDEC MO-220",
                "40-lead 6×6 mm QFN, 0.5 mm pitch",
                body_length_mm=6.0, body_width_mm=6.0, body_height_mm=0.9,
                total_pins=40, pins_lr=10, pins_tb=10,
                lead_pitch_mm=0.50, lead_width_mm=0.25, inner_lead_mm=0.45,
                paddle_length_mm=4.20, paddle_width_mm=4.20),

    PackageSpec("QFN-48", "QFN", "JEDEC MO-220",
                "48-lead 7×7 mm QFN, 0.5 mm pitch",
                body_length_mm=7.0, body_width_mm=7.0, body_height_mm=0.9,
                total_pins=48, pins_lr=12, pins_tb=12,
                lead_pitch_mm=0.50, lead_width_mm=0.25, inner_lead_mm=0.45,
                paddle_length_mm=5.10, paddle_width_mm=5.10),

    PackageSpec("QFN-64", "QFN", "JEDEC MO-220",
                "64-lead 9×9 mm QFN, 0.5 mm pitch",
                body_length_mm=9.0, body_width_mm=9.0, body_height_mm=0.9,
                total_pins=64, pins_lr=16, pins_tb=16,
                lead_pitch_mm=0.50, lead_width_mm=0.25, inner_lead_mm=0.45,
                paddle_length_mm=6.50, paddle_width_mm=6.50),

    # MirrorSemi open-cavity QFN series (air-cavity MEMS/RF)
    PackageSpec("M-QFN8W.65",  "QFN", "MirrorSemi",
                "8-lead open-cavity QFN 2×2 mm, 0.65 mm pitch",
                body_length_mm=2.0, body_width_mm=2.0, body_height_mm=0.85,
                total_pins=8, pins_lr=2, pins_tb=2,
                lead_pitch_mm=0.65, lead_width_mm=0.30, inner_lead_mm=0.30,
                paddle_length_mm=1.20, paddle_width_mm=1.20,
                has_die_paddle=True, tags=["open-cavity", "RF", "MEMS"]),

    PackageSpec("M-QFN16W.65", "QFN", "MirrorSemi",
                "16-lead open-cavity QFN 4×4 mm, 0.65 mm pitch",
                body_length_mm=4.0, body_width_mm=4.0, body_height_mm=0.85,
                total_pins=16, pins_lr=4, pins_tb=4,
                lead_pitch_mm=0.65, lead_width_mm=0.30, inner_lead_mm=0.40,
                paddle_length_mm=2.60, paddle_width_mm=2.60,
                has_die_paddle=True, tags=["open-cavity", "RF", "MEMS"]),

    PackageSpec("M-QFN24W.5",  "QFN", "MirrorSemi",
                "24-lead open-cavity QFN 4×4 mm, 0.5 mm pitch",
                body_length_mm=4.0, body_width_mm=4.0, body_height_mm=0.85,
                total_pins=24, pins_lr=6, pins_tb=6,
                lead_pitch_mm=0.50, lead_width_mm=0.25, inner_lead_mm=0.40,
                paddle_length_mm=2.50, paddle_width_mm=2.50,
                has_die_paddle=True, tags=["open-cavity", "RF", "MEMS"]),

    PackageSpec("M-QFN32W.5",  "QFN", "MirrorSemi",
                "32-lead open-cavity QFN 5×5 mm, 0.5 mm pitch",
                body_length_mm=5.0, body_width_mm=5.0, body_height_mm=0.85,
                total_pins=32, pins_lr=8, pins_tb=8,
                lead_pitch_mm=0.50, lead_width_mm=0.25, inner_lead_mm=0.45,
                paddle_length_mm=3.40, paddle_width_mm=3.40,
                has_die_paddle=True, tags=["open-cavity", "RF", "MEMS"]),

    # ══════════════════════════════════════════════════════
    # QFP  (JEDEC MS-026)
    # ══════════════════════════════════════════════════════

    PackageSpec("LQFP-32", "QFP", "JEDEC MS-026 / IPC-7351B",
                "32-lead LQFP 7×7×1.4 mm, 0.8 mm pitch",
                body_length_mm=7.0, body_width_mm=7.0, body_height_mm=1.4,
                total_pins=32, pins_lr=8, pins_tb=8,
                lead_pitch_mm=0.80, lead_width_mm=0.45, inner_lead_mm=1.0,
                outer_lead_mm=0.6,
                paddle_length_mm=3.0, paddle_width_mm=3.0),

    PackageSpec("LQFP-44", "QFP", "JEDEC MS-026",
                "44-lead LQFP 10×10×1.4 mm, 0.8 mm pitch",
                body_length_mm=10.0, body_width_mm=10.0, body_height_mm=1.4,
                total_pins=44, pins_lr=11, pins_tb=11,
                lead_pitch_mm=0.80, lead_width_mm=0.45, inner_lead_mm=1.0,
                outer_lead_mm=0.6,
                paddle_length_mm=5.0, paddle_width_mm=5.0),

    PackageSpec("LQFP-64", "QFP", "JEDEC MS-026",
                "64-lead LQFP 10×10×1.4 mm, 0.5 mm pitch",
                body_length_mm=10.0, body_width_mm=10.0, body_height_mm=1.4,
                total_pins=64, pins_lr=16, pins_tb=16,
                lead_pitch_mm=0.50, lead_width_mm=0.22, inner_lead_mm=1.0,
                outer_lead_mm=0.6,
                paddle_length_mm=5.0, paddle_width_mm=5.0),

    PackageSpec("LQFP-100", "QFP", "JEDEC MS-026",
                "100-lead LQFP 14×14×1.4 mm, 0.5 mm pitch",
                body_length_mm=14.0, body_width_mm=14.0, body_height_mm=1.4,
                total_pins=100, pins_lr=25, pins_tb=25,
                lead_pitch_mm=0.50, lead_width_mm=0.22, inner_lead_mm=1.0,
                outer_lead_mm=0.6,
                paddle_length_mm=8.0, paddle_width_mm=8.0),

    PackageSpec("LQFP-144", "QFP", "JEDEC MS-026",
                "144-lead LQFP 20×20×1.4 mm, 0.5 mm pitch",
                body_length_mm=20.0, body_width_mm=20.0, body_height_mm=1.4,
                total_pins=144, pins_lr=36, pins_tb=36,
                lead_pitch_mm=0.50, lead_width_mm=0.22, inner_lead_mm=1.0,
                outer_lead_mm=0.6,
                paddle_length_mm=12.0, paddle_width_mm=12.0),

    PackageSpec("TQFP-32", "QFP", "JEDEC MS-026",
                "32-lead TQFP 7×7×1.0 mm, 0.8 mm pitch",
                body_length_mm=7.0, body_width_mm=7.0, body_height_mm=1.0,
                total_pins=32, pins_lr=8, pins_tb=8,
                lead_pitch_mm=0.80, lead_width_mm=0.45, inner_lead_mm=1.0,
                outer_lead_mm=0.6,
                paddle_length_mm=3.0, paddle_width_mm=3.0),

    PackageSpec("PQFP-208", "QFP", "JEDEC MS-026",
                "208-lead PQFP 30.6×28.6×3.8 mm, 0.5 mm pitch",
                body_length_mm=30.6, body_width_mm=28.6, body_height_mm=3.8,
                total_pins=208, pins_lr=52, pins_tb=52,
                lead_pitch_mm=0.50, lead_width_mm=0.22, inner_lead_mm=1.5,
                outer_lead_mm=0.8,
                paddle_length_mm=18.0, paddle_width_mm=16.0),

    # ══════════════════════════════════════════════════════
    # DIP  (JEDEC MS-001)   modelled as 2-sided QFP
    # ══════════════════════════════════════════════════════

    PackageSpec("DIP-8",  "DIP", "JEDEC MS-001",
                "8-lead DIP 9.27×6.35×3.3 mm, 2.54 mm pitch",
                body_length_mm=9.27, body_width_mm=6.35, body_height_mm=3.3,
                total_pins=8, pins_lr=4, pins_tb=0,
                lead_pitch_mm=2.54, lead_width_mm=0.50, inner_lead_mm=2.5,
                outer_lead_mm=3.3, has_die_paddle=False),

    PackageSpec("DIP-14", "DIP", "JEDEC MS-001",
                "14-lead DIP 19.05×6.35×3.3 mm, 2.54 mm pitch",
                body_length_mm=19.05, body_width_mm=6.35, body_height_mm=3.3,
                total_pins=14, pins_lr=7, pins_tb=0,
                lead_pitch_mm=2.54, lead_width_mm=0.50, inner_lead_mm=2.5,
                outer_lead_mm=3.3, has_die_paddle=False),

    PackageSpec("DIP-16", "DIP", "JEDEC MS-001",
                "16-lead DIP 19.05×6.35×3.3 mm, 2.54 mm pitch",
                body_length_mm=19.05, body_width_mm=6.35, body_height_mm=3.3,
                total_pins=16, pins_lr=8, pins_tb=0,
                lead_pitch_mm=2.54, lead_width_mm=0.50, inner_lead_mm=2.5,
                outer_lead_mm=3.3, has_die_paddle=False),

    PackageSpec("DIP-28", "DIP", "JEDEC MS-001",
                "28-lead DIP 35.56×7.62×3.3 mm, 2.54 mm pitch",
                body_length_mm=35.56, body_width_mm=7.62, body_height_mm=3.3,
                total_pins=28, pins_lr=14, pins_tb=0,
                lead_pitch_mm=2.54, lead_width_mm=0.50, inner_lead_mm=3.0,
                outer_lead_mm=3.3, has_die_paddle=False),

    PackageSpec("DIP-40", "DIP", "JEDEC MS-001",
                "40-lead DIP 52.07×15.24×3.3 mm, 2.54 mm pitch",
                body_length_mm=52.07, body_width_mm=15.24, body_height_mm=3.3,
                total_pins=40, pins_lr=20, pins_tb=0,
                lead_pitch_mm=2.54, lead_width_mm=0.50, inner_lead_mm=6.0,
                outer_lead_mm=3.3, has_die_paddle=False),

    # ══════════════════════════════════════════════════════
    # SOIC  (JEDEC MS-012)
    # ══════════════════════════════════════════════════════

    PackageSpec("SOIC-8",  "SOIC", "JEDEC MS-012",
                "8-lead SOIC 4.9×3.9×1.75 mm, 1.27 mm pitch",
                body_length_mm=4.9, body_width_mm=3.9, body_height_mm=1.75,
                total_pins=8, pins_lr=4, pins_tb=0,
                lead_pitch_mm=1.27, lead_width_mm=0.42, inner_lead_mm=1.5,
                outer_lead_mm=0.8, has_die_paddle=False),

    PackageSpec("SOIC-14", "SOIC", "JEDEC MS-012",
                "14-lead SOIC 8.65×3.9×1.75 mm, 1.27 mm pitch",
                body_length_mm=8.65, body_width_mm=3.9, body_height_mm=1.75,
                total_pins=14, pins_lr=7, pins_tb=0,
                lead_pitch_mm=1.27, lead_width_mm=0.42, inner_lead_mm=1.5,
                outer_lead_mm=0.8, has_die_paddle=False),

    PackageSpec("SOIC-16", "SOIC", "JEDEC MS-012",
                "16-lead SOIC 9.9×3.9×1.75 mm, 1.27 mm pitch",
                body_length_mm=9.9, body_width_mm=3.9, body_height_mm=1.75,
                total_pins=16, pins_lr=8, pins_tb=0,
                lead_pitch_mm=1.27, lead_width_mm=0.42, inner_lead_mm=1.5,
                outer_lead_mm=0.8, has_die_paddle=False),

    PackageSpec("SOIC-20W", "SOIC", "JEDEC MS-013",
                "20-lead SOIC Wide 12.8×7.5×2.35 mm, 1.27 mm pitch",
                body_length_mm=12.8, body_width_mm=7.5, body_height_mm=2.35,
                total_pins=20, pins_lr=10, pins_tb=0,
                lead_pitch_mm=1.27, lead_width_mm=0.42, inner_lead_mm=2.5,
                outer_lead_mm=0.8, has_die_paddle=False),

    # ══════════════════════════════════════════════════════
    # TSSOP  (JEDEC MO-153)
    # ══════════════════════════════════════════════════════

    PackageSpec("TSSOP-8",  "TSSOP", "JEDEC MO-153",
                "8-lead TSSOP 3.0×4.4×1.2 mm, 0.65 mm pitch",
                body_length_mm=3.0, body_width_mm=4.4, body_height_mm=1.2,
                total_pins=8, pins_lr=4, pins_tb=0,
                lead_pitch_mm=0.65, lead_width_mm=0.30, inner_lead_mm=0.8,
                outer_lead_mm=0.5, has_die_paddle=False),

    PackageSpec("TSSOP-16", "TSSOP", "JEDEC MO-153",
                "16-lead TSSOP 5.0×4.4×1.2 mm, 0.65 mm pitch",
                body_length_mm=5.0, body_width_mm=4.4, body_height_mm=1.2,
                total_pins=16, pins_lr=8, pins_tb=0,
                lead_pitch_mm=0.65, lead_width_mm=0.30, inner_lead_mm=0.8,
                outer_lead_mm=0.5, has_die_paddle=False),

    PackageSpec("TSSOP-20", "TSSOP", "JEDEC MO-153",
                "20-lead TSSOP 6.5×4.4×1.2 mm, 0.65 mm pitch",
                body_length_mm=6.5, body_width_mm=4.4, body_height_mm=1.2,
                total_pins=20, pins_lr=10, pins_tb=0,
                lead_pitch_mm=0.65, lead_width_mm=0.30, inner_lead_mm=0.8,
                outer_lead_mm=0.5, has_die_paddle=False),

    PackageSpec("TSSOP-28", "TSSOP", "JEDEC MO-153",
                "28-lead TSSOP 9.7×4.4×1.2 mm, 0.65 mm pitch",
                body_length_mm=9.7, body_width_mm=4.4, body_height_mm=1.2,
                total_pins=28, pins_lr=14, pins_tb=0,
                lead_pitch_mm=0.65, lead_width_mm=0.30, inner_lead_mm=0.8,
                outer_lead_mm=0.5, has_die_paddle=False),

    # ══════════════════════════════════════════════════════
    # BGA  (JEDEC MS-034)
    # ══════════════════════════════════════════════════════

    PackageSpec("BGA-64",   "BGA", "JEDEC MS-034",
                "64-ball BGA 8×8 mm, 1.0 mm pitch, ⌀0.6 mm balls",
                body_length_mm=8.0,  body_width_mm=8.0,  body_height_mm=1.2,
                total_pins=64,
                bga_ball_dia_mm=0.60, bga_ball_pitch_mm=1.00),

    PackageSpec("BGA-100",  "BGA", "JEDEC MS-034",
                "100-ball BGA 10×10 mm, 1.0 mm pitch, ⌀0.6 mm balls",
                body_length_mm=10.0, body_width_mm=10.0, body_height_mm=1.2,
                total_pins=100,
                bga_ball_dia_mm=0.60, bga_ball_pitch_mm=1.00),

    PackageSpec("BGA-144",  "BGA", "JEDEC MS-034",
                "144-ball BGA 13×13 mm, 1.0 mm pitch, ⌀0.6 mm balls",
                body_length_mm=13.0, body_width_mm=13.0, body_height_mm=1.2,
                total_pins=144,
                bga_ball_dia_mm=0.60, bga_ball_pitch_mm=1.00),

    PackageSpec("BGA-256",  "BGA", "JEDEC MS-034",
                "256-ball BGA 17×17 mm, 1.0 mm pitch, ⌀0.6 mm balls",
                body_length_mm=17.0, body_width_mm=17.0, body_height_mm=1.2,
                total_pins=256,
                bga_ball_dia_mm=0.60, bga_ball_pitch_mm=1.00),

    PackageSpec("BGA-324",  "BGA", "JEDEC MS-034",
                "324-ball BGA 19×19 mm, 1.0 mm pitch, ⌀0.6 mm balls",
                body_length_mm=19.0, body_width_mm=19.0, body_height_mm=1.4,
                total_pins=324,
                bga_ball_dia_mm=0.60, bga_ball_pitch_mm=1.00),

    PackageSpec("FBGA-165", "BGA", "JEDEC MS-034",
                "165-ball Fine-pitch BGA 11×13 mm, 0.8 mm pitch, ⌀0.45 mm balls",
                body_length_mm=11.0, body_width_mm=13.0, body_height_mm=1.0,
                total_pins=165,
                bga_ball_dia_mm=0.45, bga_ball_pitch_mm=0.80),

    PackageSpec("WLCSP-25", "BGA", "JEDEC WLCSP",
                "25-ball Wafer-Level CSP 1.665×1.665 mm, 0.4 mm pitch, ⌀0.27 mm balls",
                body_length_mm=1.665, body_width_mm=1.665, body_height_mm=0.6,
                total_pins=25,
                bga_ball_dia_mm=0.27, bga_ball_pitch_mm=0.40,
                tags=["WLCSP"]),

    PackageSpec("WLCSP-49", "BGA", "JEDEC WLCSP",
                "49-ball WLCSP 2.315×2.315 mm, 0.4 mm pitch, ⌀0.27 mm balls",
                body_length_mm=2.315, body_width_mm=2.315, body_height_mm=0.6,
                total_pins=49,
                bga_ball_dia_mm=0.27, bga_ball_pitch_mm=0.40,
                tags=["WLCSP"]),
]


# ── search API ─────────────────────────────────────────────────────────────────

def search_packages(query: str,
                    family: Optional[str] = None) -> List[PackageSpec]:
    """
    Search ALL_PACKAGES by name, family, pin count, or description.

    Parameters
    ----------
    query  : free-text search string (case-insensitive)
    family : optional family filter: "QFN" | "QFP" | "DIP" | "SOIC" |
             "TSSOP" | "BGA"

    Returns list sorted by relevance (exact name match first).
    """
    q = query.strip().lower()
    results: List[Tuple[int, PackageSpec]] = []

    for pkg in ALL_PACKAGES:
        if family and pkg.family.upper() != family.upper():
            continue

        score = 0
        name_l = pkg.name.lower()
        desc_l = pkg.description.lower()
        tags_l = " ".join(pkg.tags).lower()

        if q == name_l:
            score = 100
        elif q in name_l:
            score = 80
        elif name_l.startswith(q):
            score = 70
        elif q in desc_l:
            score = 40
        elif q in tags_l:
            score = 30
        elif q in pkg.family.lower():
            score = 20
        elif q in str(pkg.total_pins):
            score = 10

        if score > 0:
            results.append((score, pkg))

    results.sort(key=lambda x: -x[0])
    return [pkg for _, pkg in results]


def get_package(name: str) -> Optional[PackageSpec]:
    """Return the first PackageSpec whose name matches exactly (case-insensitive)."""
    n = name.strip().lower()
    for pkg in ALL_PACKAGES:
        if pkg.name.lower() == n:
            return pkg
    return None


def families() -> List[str]:
    """Return sorted list of unique family names in the catalogue."""
    return sorted({p.family for p in ALL_PACKAGES})
