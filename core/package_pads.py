# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Bond fingers on an imported package model.

A package from the Leadframe Library arrives as a STEP solid with no contact
points at all, so every bond finger has to be placed by hand before wire
bonding can start — dozens of clicks on a QFP.

PCB Import already detects pads automatically, but its rule does not carry
over: it takes near-horizontal faces in the top few percent of the board,
whereas a package's bond shelf is a ring of small faces INSIDE the body, well
below its top, and how far below depends on the model.

So this does not guess. It groups every near-horizontal face into the plane it
lies in and reports each plane with its face count, area range and how much of
it lies around the outline. The bond shelf is the plane holding a ring of
like-sized faces — obvious at a glance in that list, and a judgement best left
to the person who can see the model too.

Faces are taken as horizontal by the absolute Z of their normal, up or down:
STEP face orientation is not dependable, and a lead's underside lands in its
own plane anyway, where it is easy to tell apart from the shelf.

Qt-free, so it is testable headlessly.
"""

import FreeCAD

# How flat a face must be to be a candidate: |cos| of its normal against +Z.
DEFAULT_NORMAL_Z_MIN = 0.85
# Bond fingers are small. Same bounds as pcb.PCBImportCommand's pad detection.
DEFAULT_MIN_AREA_MM2 = 0.01
DEFAULT_MAX_AREA_MM2 = 150.0
# Faces within this of each other in Z lie in the same plane.
DEFAULT_LEVEL_TOL_MM = 0.05
# Two candidates closer than this are the same pad.
DEFAULT_DEDUP_MM = 0.05
# A point within this fraction of the half-extent from the outline counts as
# lying around the edge — how a lead ring differs from an array of bumps.
_RING_BAND_FRAC = 0.25


def flat_faces(shape, normal_z_min=DEFAULT_NORMAL_Z_MIN,
               min_area_mm2=DEFAULT_MIN_AREA_MM2,
               max_area_mm2=DEFAULT_MAX_AREA_MM2):
    """[{"z_mm", "area_mm2", "point"}] for every near-horizontal face of the
    right size. *shape* must already be in world coordinates."""
    out = []
    for face in getattr(shape, "Faces", []):
        try:
            if abs(face.normalAt(0, 0).z) < normal_z_min:
                continue
            area = float(face.Area)
            centre = FreeCAD.Vector(face.CenterOfMass)
        except Exception:
            continue
        if not (min_area_mm2 <= area <= max_area_mm2):
            continue
        out.append({"z_mm": centre.z, "area_mm2": area, "point": centre})
    return out


def dedup(points, tol_mm=DEFAULT_DEDUP_MM):
    """Points more than *tol_mm* apart, in the order given."""
    unique = []
    for p in points:
        if not any((p - q).Length < tol_mm for q in unique):
            unique.append(p)
    return unique


def ring_fraction(points, outline=None):
    """
    How much of *points* lies around the edge of the outline they span: 1.0
    for a ring of leads, well below that for an array or a scatter.

    Distance is measured to the bounding RECTANGLE, which is the shape a
    leadframe's fingers are arranged along. Points on a circle score lower
    (0.75 for an even ring), because the diagonals of a circle sit farthest
    from the rectangle around it.

    *outline* is (xmin, ymin, xmax, ymax); the points' own extent is used
    when it is not given.
    """
    if not points:
        return 0.0
    if outline is None:
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        outline = (min(xs), min(ys), max(xs), max(ys))
    x0, y0, x1, y1 = outline
    band = _RING_BAND_FRAC * min((x1 - x0) / 2.0, (y1 - y0) / 2.0)
    if band <= 0.0:
        return 1.0
    near = sum(1 for p in points
               if min(p.x - x0, x1 - p.x, p.y - y0, y1 - p.y) <= band)
    return near / float(len(points))


def group_levels(faces, tol_mm=DEFAULT_LEVEL_TOL_MM, outline=None,
                 dedup_mm=DEFAULT_DEDUP_MM):
    """
    The planes *faces* lie in, richest first:
        [{"z_mm", "count", "area_min_mm2", "area_max_mm2", "ring_fraction",
          "points"}]
    """
    clusters = []
    for face in sorted(faces, key=lambda f: f["z_mm"]):
        if clusters and abs(face["z_mm"] - clusters[-1]["last_z"]) <= tol_mm:
            cluster = clusters[-1]
        else:
            clusters.append({"points": [], "areas": [], "last_z": face["z_mm"]})
            cluster = clusters[-1]
        cluster["points"].append(face["point"])
        cluster["areas"].append(face["area_mm2"])
        cluster["last_z"] = face["z_mm"]

    levels = []
    for cluster in clusters:
        points = dedup(cluster["points"], dedup_mm)
        if not points:
            continue
        areas = sorted(cluster["areas"])
        levels.append({
            "z_mm": sum(p.z for p in points) / len(points),
            "count": len(points),
            "area_min_mm2": areas[0],
            "area_max_mm2": areas[-1],
            "ring_fraction": ring_fraction(points, outline),
            "points": points,
        })
    levels.sort(key=lambda lv: (-lv["count"], -lv["z_mm"]))
    return levels


def detect_levels(shape, normal_z_min=DEFAULT_NORMAL_Z_MIN,
                  min_area_mm2=DEFAULT_MIN_AREA_MM2,
                  max_area_mm2=DEFAULT_MAX_AREA_MM2,
                  tol_mm=DEFAULT_LEVEL_TOL_MM,
                  dedup_mm=DEFAULT_DEDUP_MM):
    """The candidate bond-finger planes of one package shape, richest first."""
    faces = flat_faces(shape, normal_z_min, min_area_mm2, max_area_mm2)
    try:
        bb = shape.BoundBox
        outline = (bb.XMin, bb.YMin, bb.XMax, bb.YMax)
    except Exception:
        outline = None
    return group_levels(faces, tol_mm, outline, dedup_mm)


def points_of(levels, indices, dedup_mm=DEFAULT_DEDUP_MM):
    """Every point of the chosen levels, with duplicates dropped."""
    chosen = []
    for i in indices:
        if 0 <= i < len(levels):
            chosen.extend(levels[i]["points"])
    return dedup(chosen, dedup_mm)


def without_existing(points, existing, tol_mm=DEFAULT_DEDUP_MM):
    """The points that have no contact point on them yet."""
    return [p for p in points
            if not any((p - q).Length < tol_mm for q in existing)]
