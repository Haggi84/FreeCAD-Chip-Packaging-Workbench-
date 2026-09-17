# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Netlist import — which die pad is bonded to which package pin.

A CSV with a header row and one connection per row:

    net,from,to
    VDD,VDD,Lead_L01
    GND,GND,2

"die_pad" / "package_pin" are accepted in place of from / to, and "net" is
optional. Blank lines and lines starting with # are ignored.

Each end is looked up among the document's contact points by, in order:
object name, PadName (the layout's own label, see core.pad_names), label,
the PinNumber of the lead it sits on, and the name of that lead. The first
criterion that matches exactly one contact point decides. One that matches
several is reported as ambiguous — bonding the wrong one of two pads named
alike is exactly the mistake a netlist exists to prevent.

A connection that already has a wire only gets its net name updated, so
importing the same netlist twice does not double the wires.

Qt-free. The wire itself is built by a function the caller passes in
(wirebond.ManualWireBonding.place_bond_wire in the workbench), which keeps
this module testable without the bonding code's geometry.
"""

import csv
import math

import FreeCAD

_FROM_COLUMNS = ("from", "die_pad", "pad")
_TO_COLUMNS = ("to", "package_pin", "pin")
_NET_COLUMNS = ("net", "net_name", "signal")


def read_netlist_csv(path):
    """[{"net", "from", "to", "row"}]. Raises ValueError on a netlist that
    cannot be read unambiguously."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise ValueError("The netlist is empty.")

    reader = csv.DictReader(lines)
    columns = {(f or "").strip().lower(): f for f in reader.fieldnames or []}

    def column(options):
        return next((columns[o] for o in options if o in columns), None)

    from_col, to_col, net_col = column(_FROM_COLUMNS), column(_TO_COLUMNS), column(_NET_COLUMNS)
    if from_col is None or to_col is None:
        raise ValueError(
            "The netlist needs a 'from' and a 'to' column (or 'die_pad' and "
            f"'package_pin'); found: {', '.join(reader.fieldnames or [])}")

    rows = []
    for number, record in enumerate(reader, start=1):
        a = (record.get(from_col) or "").strip()
        b = (record.get(to_col) or "").strip()
        if not a or not b:
            raise ValueError(f"Connection {number}: both ends are needed, got "
                             f"'{a}' and '{b}'.")
        rows.append({
            "net": (record.get(net_col) or "").strip() if net_col else "",
            "from": a,
            "to": b,
            "row": number,
        })
    return rows


def _contact_points(doc):
    return [o for o in doc.Objects if getattr(o, "IsContactPoint", False)]


def _pin_number(doc, cp):
    source = doc.getObject(getattr(cp, "SourceObject", "") or "")
    number = getattr(source, "PinNumber", None) if source is not None else None
    return str(number) if number else None


def find_contact_point(doc, key):
    """(contact point, None) or (None, reason)."""
    wanted = str(key).strip().lower()
    cps = _contact_points(doc)
    criteria = (
        ("name", lambda cp: cp.Name.lower() == wanted),
        ("pad name", lambda cp: (getattr(cp, "PadName", "") or "").lower() == wanted),
        ("label", lambda cp: (cp.Label or "").lower() == wanted),
        ("pin number", lambda cp: _pin_number(doc, cp) == wanted),
        ("lead", lambda cp: (getattr(cp, "SourceObject", "") or "").lower() == wanted),
    )
    for what, matches in criteria:
        hits = [cp for cp in cps if matches(cp)]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            names = ", ".join(h.Name for h in hits[:5])
            return None, f"'{key}' matches {len(hits)} contact points by {what} ({names})"
    return None, f"no contact point matches '{key}'"


def _existing_wire(doc, a, b):
    ends = {a.Name, b.Name}
    for obj in doc.Objects:
        if (obj.Name.startswith("BondWire_")
                and {getattr(obj, "StartCP", ""), getattr(obj, "EndCP", "")} == ends):
            return obj
    return None


def _set_net(wire, net):
    if not net:
        return
    if not hasattr(wire, "NetName"):
        wire.addProperty("App::PropertyString", "NetName", "Wirebond", "Net identifier")
    wire.NetName = net


def apply_netlist(doc, rows, place_wire):
    """
    Bond every connection in *rows*. *place_wire(cp_from, cp_to)* builds one
    wire and returns it.

    Returns {"placed": [(row, wire)], "updated": [(row, wire)],
             "failed": [(row, reason)]} with wire object names.
    """
    report = {"placed": [], "updated": [], "failed": []}
    for row in rows:
        a, why_a = find_contact_point(doc, row["from"])
        b, why_b = find_contact_point(doc, row["to"])
        if a is None or b is None:
            report["failed"].append((row, why_a or why_b))
            continue
        if a is b:
            report["failed"].append((row, f"both ends are {a.Name}"))
            continue

        wire = _existing_wire(doc, a, b)
        if wire is not None:
            _set_net(wire, row["net"])
            report["updated"].append((row, wire.Name))
            continue

        try:
            wire = place_wire(a, b)
        except Exception as exc:
            report["failed"].append((row, f"wire could not be built: {exc}"))
            continue
        _set_net(wire, row["net"])
        report["placed"].append((row, wire.Name))
    return report


# ── proposing a netlist ──────────────────────────────────────────────────────
#
# With no pinout to start from, the usual first proposal is to bond each die
# pad to the package pin facing it, in ring order: the shortest wires that do
# not cross. That is a geometric question, so it can be answered here and
# handed over as an ordinary netlist CSV to edit.

def _position(cp):
    point = getattr(cp, "ContactPoint", None)
    return FreeCAD.Vector(point) if point is not None else None


def die_outlines(doc):
    """(xmin, ymin, xmax, ymax) for every chip proxy and die body in *doc*."""
    outlines = []
    for obj in doc.Objects:
        if not (getattr(obj, "IsChipProxy", False) or getattr(obj, "IsDieBody", False)):
            continue
        try:
            bb = obj.Shape.BoundBox
        except Exception:
            continue
        if bb.isValid() and bb.XLength > 0 and bb.YLength > 0:
            outlines.append((bb.XMin, bb.YMin, bb.XMax, bb.YMax))
    return outlines


def classify_contact_points(doc):
    """
    (die_side, package_side) contact points, split by whether each lies over a
    die — geometry, not naming, so a contact point placed by hand on a die is
    die-side too.

    With no die in the document nothing is die-side and no proposal is
    possible; the caller reports that rather than pairing arbitrarily.
    """
    outlines = die_outlines(doc)
    die, package = [], []
    for cp in _contact_points(doc):
        point = _position(cp)
        if point is None:
            continue
        over_die = any(x0 <= point.x <= x1 and y0 <= point.y <= y1
                       for x0, y0, x1, y1 in outlines)
        (die if over_die else package).append(cp)
    return die, package


def _centre(points):
    return FreeCAD.Vector(sum(p.x for p in points) / len(points),
                          sum(p.y for p in points) / len(points), 0.0)


def _by_angle(cps):
    """Contact points in ring order, anticlockwise about their own centre."""
    centre = _centre([_position(cp) for cp in cps])
    return sorted(cps, key=lambda cp: math.atan2(_position(cp).y - centre.y,
                                                 _position(cp).x - centre.x))


def _plan_distance(a, b):
    pa, pb = _position(a), _position(b)
    return math.hypot(pb.x - pa.x, pb.y - pa.y)


def _best_alignment(die, package):
    """
    Pair two rings by trying every starting offset and both directions, and
    keeping the shortest total. Without this the pairing depends on where
    atan2 happens to start, which is nowhere in particular.
    """
    best, best_total = None, None
    for direction in (1, -1):
        for offset in range(len(package)):
            pairs = [(die[i], package[(offset + direction * i) % len(package)])
                     for i in range(len(die))]
            total = sum(_plan_distance(a, b) for a, b in pairs)
            if best_total is None or total < best_total:
                best, best_total = pairs, total
    return best or []


def _greedy_pairs(die, package):
    """Shortest-first pairing for rings of different sizes: every pin goes to
    the nearest pad still free, and the rest are reported."""
    candidates = sorted((_plan_distance(a, b), i, j)
                        for i, a in enumerate(die) for j, b in enumerate(package))
    used_die, used_package, pairs = set(), set(), []
    for _distance, i, j in candidates:
        if i in used_die or j in used_package:
            continue
        used_die.add(i)
        used_package.add(j)
        pairs.append((die[i], package[j]))
    return pairs


def _pad_name_counts(cps):
    counts = {}
    for cp in cps:
        pad_name = (getattr(cp, "PadName", "") or "").strip().lower()
        if pad_name:
            counts[pad_name] = counts.get(pad_name, 0) + 1
    return counts


def _reference(cp, pad_name_counts):
    """What to write for a contact point: its pad name when that is
    unambiguous in this document, otherwise its object name."""
    pad_name = (getattr(cp, "PadName", "") or "").strip()
    if pad_name and pad_name_counts.get(pad_name.lower(), 0) == 1:
        return pad_name
    return cp.Name


def _crosses(a0, a1, b0, b1, tol=1e-12):
    def orient(p, q, r):
        return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)

    def opposite(u, v):
        return (u > tol and v < -tol) or (u < -tol and v > tol)

    return (opposite(orient(b0, b1, a0), orient(b0, b1, a1))
            and opposite(orient(a0, a1, b0), orient(a0, a1, b1)))


def count_crossings(pairs):
    """How many wires of the proposal would cross in plan view."""
    segments = [(_position(a), _position(b)) for a, b in pairs]
    return sum(1 for i, (a0, a1) in enumerate(segments)
               for b0, b1 in segments[i + 1:]
               if _crosses(a0, a1, b0, b1))


def propose_connections(doc):
    """
    A first pinout for a die and a package that have none: every die pad
    bonded to the pin facing it, in ring order.

    Returns (rows, report). The rows are as read_netlist_csv returns them, so
    they can be written out and imported unchanged. The report carries the
    counts, anything left unpaired, and how many wires cross — 0 for two rings
    of the same size, which is the point of pairing by angle rather than by
    nearest neighbour.
    """
    die, package = classify_contact_points(doc)
    report = {"die": len(die), "package": len(package), "crossings": 0,
              "unpaired_die": [], "unpaired_package": [], "problem": None}
    if not die or not package:
        report["problem"] = (
            "A proposal needs contact points on a die and on a package: found "
            f"{len(die)} over a die and {len(package)} elsewhere. Import the "
            "chip and place the package bond fingers first.")
        return [], report

    die_ring, package_ring = _by_angle(die), _by_angle(package)
    if len(die_ring) <= len(package_ring):
        pairs = _best_alignment(die_ring, package_ring)
    else:
        pairs = _greedy_pairs(die_ring, package_ring)

    paired_die = {a.Name for a, _b in pairs}
    paired_package = {b.Name for _a, b in pairs}
    report["unpaired_die"] = [cp.Name for cp in die if cp.Name not in paired_die]
    report["unpaired_package"] = [cp.Name for cp in package
                                  if cp.Name not in paired_package]
    report["crossings"] = count_crossings(pairs)

    counts = _pad_name_counts(die + package)
    rows = []
    for number, (a, b) in enumerate(pairs, start=1):
        pad_name = (getattr(a, "PadName", "") or "").strip()
        rows.append({
            "net": pad_name or f"Net_{number:03d}",
            "from": _reference(a, counts),
            "to": _reference(b, counts),
            "row": number,
            "distance_mm": _plan_distance(a, b),
        })
    return rows, report


def write_netlist_csv(rows, path, comments=()):
    """Write *rows* as a netlist this module can read back."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        for line in comments:
            fh.write(f"# {line}\n")
        writer = csv.writer(fh)
        writer.writerow(["net", "from", "to"])
        for row in rows:
            writer.writerow([row["net"], row["from"], row["to"]])
    return path


def export_netlist_csv(doc, path):
    """Write the document's bond wires as a netlist. Returns how many
    connections were written."""
    counts = _pad_name_counts(_contact_points(doc))
    rows = []
    for obj in sorted(doc.Objects, key=lambda o: o.Name):
        if not obj.Name.startswith("BondWire_"):
            continue
        start = doc.getObject(getattr(obj, "StartCP", "") or "")
        end = doc.getObject(getattr(obj, "EndCP", "") or "")
        if start is None or end is None:
            continue
        rows.append({
            "net": getattr(obj, "NetName", "") or obj.Name,
            "from": _reference(start, counts),
            "to": _reference(end, counts),
        })
    write_netlist_csv(rows, path,
                      comments=[f"{len(rows)} bond wire(s) from {doc.Label}"])
    return len(rows)
