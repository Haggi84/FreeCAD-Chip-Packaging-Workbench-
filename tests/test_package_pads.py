# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless tests for core.package_pads — finding the bond fingers of a package
model that arrives without contact points.

The module deliberately does not decide which plane is the bond shelf; it
reports the planes and what is in them. So what is tested is that the
evidence is right: the leads land in one plane, their undersides in another,
the package body is filtered out by size, and a ring of leads is reported as
lying around the outline while a grid of bumps is not.
"""

import math

import FreeCAD
import Part

from _harness import TestCase

import core.package_pads as package_pads

V = FreeCAD.Vector


def _package_shape():
    """A 13 x 13 mm body with 16 leads around it: tops at z = 1.2, undersides
    at z = 1.0, plus a 3 x 3 mm die paddle in the same plane as the tops."""
    parts = [Part.makeBox(13.0, 13.0, 1.0, V(-6.5, -6.5, 0.0))]
    for side in range(4):
        for i in range(4):
            along = -2.25 + i * 1.5
            if side == 0:
                at = V(along - 0.25, 5.0, 1.0)
                size = (0.5, 1.0, 0.2)
            elif side == 1:
                at = V(along - 0.25, -6.0, 1.0)
                size = (0.5, 1.0, 0.2)
            elif side == 2:
                at = V(5.0, along - 0.25, 1.0)
                size = (1.0, 0.5, 0.2)
            else:
                at = V(-6.0, along - 0.25, 1.0)
                size = (1.0, 0.5, 0.2)
            parts.append(Part.makeBox(*size, at))
    parts.append(Part.makeBox(3.0, 3.0, 0.2, V(-1.5, -1.5, 1.0)))
    return Part.makeCompound(parts)


def run():
    tc = TestCase("package_pads")
    _check_levels(tc)
    _check_ring_fraction(tc)
    _check_selection_helpers(tc)
    return tc.results


def _check_levels(tc):
    shape = _package_shape()
    levels = package_pads.detect_levels(shape)
    tc.check("the planes are reported richest first",
              levels and levels[0]["count"] >= levels[-1]["count"],
              str([(round(lv["z_mm"], 3), lv["count"]) for lv in levels]))

    tops = [lv for lv in levels if abs(lv["z_mm"] - 1.2) < 1e-6]
    bottoms = [lv for lv in levels if abs(lv["z_mm"] - 1.0) < 1e-6]
    tc.check("the 16 lead tops and the die paddle land in one plane at z = 1.2",
              len(tops) == 1 and tops[0]["count"] == 17,
              str([(round(lv["z_mm"], 3), lv["count"]) for lv in levels]))
    tc.check("that plane's face sizes span the leads and the paddle",
              abs(tops[0]["area_min_mm2"] - 0.5) < 1e-6
              and abs(tops[0]["area_max_mm2"] - 9.0) < 1e-6,
              f"{tops[0]['area_min_mm2']} .. {tops[0]['area_max_mm2']}")
    tc.check("the lead undersides are a plane of their own, not mixed in",
              len(bottoms) == 1 and bottoms[0]["count"] == 17,
              str([(round(lv["z_mm"], 3), lv["count"]) for lv in levels]))
    tc.check("16 of the 17 faces lie around the outline — a lead ring, with the "
              "paddle in the middle",
              abs(tops[0]["ring_fraction"] - 16.0 / 17.0) < 1e-6,
              str(tops[0]["ring_fraction"]))

    tc.check("the 169 mm² package body is filtered out by size, so it cannot "
              "be mistaken for a pad",
              not any(lv["area_max_mm2"] > 150.0 for lv in levels),
              str([lv["area_max_mm2"] for lv in levels]))
    tc.check("vertical faces are not candidates at all",
              sum(lv["count"] for lv in levels) == 34,
              str(sum(lv["count"] for lv in levels)))

    narrow = package_pads.detect_levels(shape, max_area_mm2=1.0)
    top = [lv for lv in narrow if abs(lv["z_mm"] - 1.2) < 1e-6]
    tc.check("an area limit below the paddle leaves the 16 leads alone",
              len(top) == 1 and top[0]["count"] == 16 and top[0]["ring_fraction"] == 1.0,
              str([(round(lv["z_mm"], 3), lv["count"]) for lv in narrow]))


def _check_ring_fraction(tc):
    square = [V(x, y, 0.0) for x, y in
              ((-5, -5), (0, -5), (5, -5), (5, 0), (5, 5), (0, 5), (-5, 5), (-5, 0))]
    circle = [V(math.cos(a) * 5.0, math.sin(a) * 5.0, 0.0)
              for a in [i * math.pi / 8 for i in range(16)]]
    grid = [V(x, y, 0.0) for x in (-1.0, 0.0, 1.0) for y in (-1.0, 0.0, 1.0)]
    tc.check("a ring of leads around a rectangular package is all edge",
              package_pads.ring_fraction(square) == 1.0,
              str(package_pads.ring_fraction(square)))
    tc.check("an even ring on a circle scores 0.75, not 1.0: the distance is "
              "measured to the bounding rectangle, and a circle's diagonals sit "
              "farthest from that",
              abs(package_pads.ring_fraction(circle) - 0.75) < 1e-9,
              str(package_pads.ring_fraction(circle)))
    tc.check("a 3 x 3 grid is 8/9 edge — only its centre point is nowhere near",
              abs(package_pads.ring_fraction(grid) - 8.0 / 9.0) < 1e-9,
              str(package_pads.ring_fraction(grid)))
    tc.check("no points, no ring", package_pads.ring_fraction([]) == 0.0)


def _check_selection_helpers(tc):
    levels = package_pads.detect_levels(_package_shape())
    chosen = package_pads.points_of(levels, [0])
    tc.check("points_of returns the chosen plane's points",
              len(chosen) == levels[0]["count"], f"{len(chosen)}")
    tc.check("points_of ignores an index that is not there",
              package_pads.points_of(levels, [len(levels) + 5]) == [])

    duplicate = chosen[0]
    tc.check("dedup drops a point that is already there",
              len(package_pads.dedup(chosen + [duplicate])) == len(chosen))
    tc.check("without_existing skips the faces that already have a contact point",
              len(package_pads.without_existing(chosen, [chosen[0], chosen[1]]))
              == len(chosen) - 2)
    tc.check("without_existing keeps everything when there is nothing yet",
              len(package_pads.without_existing(chosen, [])) == len(chosen))
