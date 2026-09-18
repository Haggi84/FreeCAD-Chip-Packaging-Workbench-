# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The ratsnest: a straight line for every connection a net still needs.

A net is a set of pads that must end up electrically one. The rubber lines
are not the wiring — they are what is LEFT to wire. So a line is drawn
between two groups of pads that are not connected yet, and the moment a trace
joins them the line is gone, because the groups have become one.

That is why this is rebuilt from the document rather than kept as state:
pads move with their components, traces come and go, and a stale rubber line
is worse than none. Rebuilding is cheap — a net has a handful of pads.

Which pads a trace connects is not recorded by the routers; they store the
polyline. So a trace's two ends are matched to the nearest pad within a
tolerance, the same way the design rule check finds the pad a trace lands on.
Bond wires name their contact points outright, and those are used directly.

Qt-free, so it is testable headlessly.
"""

import itertools

import FreeCAD
import Part

IS_RATSNEST = "IsRatsnest"
GROUP_NAME = "Ratsnest"

# How close a trace's end must be to a pad to count as landing on it. A
# routed trace ends on the pad centre; this allows for the pad being wider
# than the trace and for a hand-drawn end.
DEFAULT_SNAP_MM = 0.25


def _contact_points(doc):
    return [o for o in doc.Objects if getattr(o, "IsContactPoint", False)]


def _position(pad):
    point = getattr(pad, "ContactPoint", None)
    return FreeCAD.Vector(point) if point is not None else None


def pads_by_net(doc):
    """{net name: [pads]} for every pad that carries a net."""
    nets = {}
    for pad in _contact_points(doc):
        net = (getattr(pad, "NetName", "") or "").strip()
        if net and _position(pad) is not None:
            nets.setdefault(net, []).append(pad)
    return nets


def _nearest_pad(pads, point, snap_mm):
    best, best_distance = None, snap_mm
    for pad in pads:
        distance = (_position(pad) - point).Length
        if distance <= best_distance:
            best, best_distance = pad, distance
    return best


def connected_pairs(doc, snap_mm=DEFAULT_SNAP_MM):
    """
    {(pad name, pad name)} already joined by something in the document.

    A trace counts when both of its ends land on a pad; a bond wire names its
    contact points, so it counts outright.
    """
    pads = _contact_points(doc)
    pairs = set()
    for obj in doc.Objects:
        ends = []
        if getattr(obj, "IsRoutingTrace", False):
            waypoints = list(getattr(obj, "Waypoints", []) or [])
            if len(waypoints) >= 2:
                ends = [_nearest_pad(pads, FreeCAD.Vector(waypoints[0]), snap_mm),
                        _nearest_pad(pads, FreeCAD.Vector(waypoints[-1]), snap_mm)]
        if obj.Name.startswith("BondWire_") or getattr(obj, "IsRoutingTrace", False):
            named = [doc.getObject(getattr(obj, prop, "") or "")
                     for prop in ("StartCP", "EndCP")]
            if all(cp is not None for cp in named):
                ends = named
        if len(ends) == 2 and all(ends) and ends[0] is not ends[1]:
            pairs.add(tuple(sorted((ends[0].Name, ends[1].Name))))
    return pairs


class _Groups:
    """Union-find over pad names: which pads are already one net in copper."""

    def __init__(self, names):
        self._parent = {name: name for name in names}

    def find(self, name):
        while self._parent[name] != name:
            self._parent[name] = self._parent[self._parent[name]]
            name = self._parent[name]
        return name

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def groups(self):
        out = {}
        for name in self._parent:
            out.setdefault(self.find(name), []).append(name)
        return list(out.values())


def open_links(doc, snap_mm=DEFAULT_SNAP_MM):
    """
    [(net, pad, pad, distance)] — the connections still missing, shortest
    first within each net.

    Pads already joined form one group, and the groups of a net are then
    linked by the shortest pad-to-pad hop between them, as a ratsnest does:
    n groups need n - 1 lines, not a line between every pair.
    """
    pairs = connected_pairs(doc, snap_mm)
    links = []
    for net, pads in pads_by_net(doc).items():
        if len(pads) < 2:
            continue
        by_name = {pad.Name: pad for pad in pads}
        groups = _Groups(list(by_name))
        for a, b in pairs:
            if a in by_name and b in by_name:
                groups.union(a, b)

        clusters = groups.groups()
        while len(clusters) > 1:
            best = None
            for i, j in itertools.combinations(range(len(clusters)), 2):
                for a in clusters[i]:
                    for b in clusters[j]:
                        distance = (_position(by_name[a]) - _position(by_name[b])).Length
                        if best is None or distance < best[0]:
                            best = (distance, i, j, a, b)
            if best is None:
                break
            distance, i, j, a, b = best
            links.append((net, by_name[a], by_name[b], distance))
            clusters[i] = clusters[i] + clusters[j]
            clusters.pop(j)
    return links


def clear(doc):
    """Remove every rubber line. Returns how many went."""
    gone = 0
    for obj in list(doc.Objects):
        if getattr(obj, IS_RATSNEST, False):
            try:
                doc.removeObject(obj.Name)
                gone += 1
            except Exception:
                pass
    group = doc.getObject(GROUP_NAME)
    if group is not None and not group.Group:
        try:
            doc.removeObject(group.Name)
        except Exception:
            pass
    return gone


def rebuild(doc, snap_mm=DEFAULT_SNAP_MM):
    """
    Draw the connections a net still needs, replacing whatever was there.

    Returns {"links", "nets", "connected_nets", "removed"}.
    """
    removed = clear(doc)
    links = open_links(doc, snap_mm)
    if links:
        group = doc.getObject(GROUP_NAME) or doc.addObject(
            "App::DocumentObjectGroup", GROUP_NAME)
        group.Label = "Ratsnest"
    for index, (net, a, b, _distance) in enumerate(links, start=1):
        line = doc.addObject("Part::Feature", f"Rats_{index:04d}")
        line.Shape = Part.makeLine(_position(a), _position(b))
        line.Label = f"{net}: {a.Label} — {b.Label}"
        for prop, ptype, value in (
            (IS_RATSNEST, "App::PropertyBool", True),
            ("NetName", "App::PropertyString", net),
            ("StartCP", "App::PropertyString", a.Name),
            ("EndCP", "App::PropertyString", b.Name),
        ):
            if not hasattr(line, prop):
                line.addProperty(ptype, prop, "Ratsnest", "")
            setattr(line, prop, value)
        if FreeCAD.GuiUp and getattr(line, "ViewObject", None) is not None:
            line.ViewObject.LineColor = (1.0, 0.85, 0.10)
            line.ViewObject.LineWidth = 1
            line.ViewObject.DrawStyle = "Dashed"
        doc.getObject(GROUP_NAME).addObject(line)

    nets = pads_by_net(doc)
    open_nets = {net for net, _a, _b, _d in links}
    report = {
        "links": len(links),
        "nets": len(nets),
        "connected_nets": len([net for net, pads in nets.items()
                               if len(pads) > 1 and net not in open_nets]),
        "removed": removed,
    }
    FreeCAD.Console.PrintMessage(
        f"[Ratsnest] {report['links']} connection(s) still open across "
        f"{report['nets']} net(s)\n")
    return report


def refresh_if_present(doc, snap_mm=DEFAULT_SNAP_MM):
    """
    Rebuild the ratsnest, but only in a document that already has one.

    Called after a trace is baked, so a rubber line disappears the moment its
    connection is made rather than when someone remembers to ask. A document
    with no rubber lines is left alone: there is nothing to update, and
    routing should not pay for a feature that is not in use.
    """
    if not any(getattr(obj, IS_RATSNEST, False) for obj in (doc.Objects if doc else [])):
        return None
    return rebuild(doc, snap_mm)


def net_status(doc, snap_mm=DEFAULT_SNAP_MM):
    """
    [{"net", "pads", "open", "done"}] — one row per net, most open first,
    for a panel to show.
    """
    links = open_links(doc, snap_mm)
    open_by_net = {}
    for net, _a, _b, _distance in links:
        open_by_net[net] = open_by_net.get(net, 0) + 1

    rows = []
    for net, pads in pads_by_net(doc).items():
        needed = max(len(pads) - 1, 0)
        still_open = open_by_net.get(net, 0)
        rows.append({"net": net, "pads": len(pads), "open": still_open,
                     "done": needed - still_open})
    rows.sort(key=lambda row: (-row["open"], row["net"]))
    return rows
