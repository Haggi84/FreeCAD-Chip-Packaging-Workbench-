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
