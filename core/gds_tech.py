# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Which technology a chip was imported with.

A package holds more than one die, and in a multi-die or stacked package
they are often not from the same process — a SKY130 die next to an SG13G2
die is an ordinary case, not an exotic one. The Technology Configuration
dialog holds one active profile for the session, which is the right thing
for "what am I about to import" and the wrong thing for "what is this die
made of": the session moves on to the next chip, while the die stays in the
document and still has to answer that question a week later.

So every import records its own technology on the objects it creates, and
everything downstream asks the object rather than the session:

    technology_of(block)   -> {"name", "lyp_path", "map_path", "xml_path"}
    stackup_of(block)      -> the parsed stackup XML for that die, or None
    technologies(doc)      -> what this document actually contains

An object with no technology of its own inherits one, in this order: the
chip it belongs to (its SourceObject — how a pad marker finds its die), then
the groups containing it (how one imported GDS layer finds its import). That
way a single tag on the die block or the import group covers everything that
came in with it, and an object can still override it by carrying its own.

Qt-free on purpose: the dialogs live in ui/, this is what the tests and the
headless paths use.
"""

import os

import FreeCAD

PROP_PROFILE = "TechProfile"
PROP_LYP = "SourceLYP"
PROP_MAP = "SourceMAP"
PROP_XML = "SourceXML"

GROUP = "ChipProxy"   # the property group these live in, for the editor

_PATH_PROPS = ((PROP_LYP, "lyp_path", "Layer properties (.lyp) this chip was imported with"),
               (PROP_MAP, "map_path", "Layer map (.map) this chip was imported with"),
               (PROP_XML, "xml_path", "Stackup (.xml) this chip was imported with"))

# How far to follow SourceObject/group links before giving up. A document
# with a cycle in it (possible by hand) must not hang the property editor.
_MAX_DEPTH = 8

_stackup_cache: dict = {}


def profile(name, lyp_path="", map_path="", xml_path="", description=""):
    """One technology, as a plain dict — the shape everything here passes around."""
    return {
        "name": str(name or ""),
        "description": str(description or ""),
        "lyp_path": str(lyp_path or ""),
        "map_path": str(map_path or ""),
        "xml_path": str(xml_path or ""),
    }


def from_config(name=None, config=None):
    """
    A profile out of the Technology Configuration, by name.

    *name* of None means the session's active profile, which is what an
    import starts from before the user picks anything else. Returns a
    profile whose paths may be empty — an unconfigured workbench is a normal
    state, not an error.
    """
    if config is None:
        from core.TechConfig import tech_config
        config = tech_config
    if name is None:
        name = config.get_active_name()
        data = config.get_local()
    else:
        data = config.get_profile(name)
    return profile(name, data.get("lyp_path", ""), data.get("map_path", ""),
                   data.get("xml_path", ""), data.get("description", ""))


def config_profiles(config=None):
    """Every named technology available to choose from, as profiles."""
    if config is None:
        from core.TechConfig import tech_config
        config = tech_config
    return [from_config(name, config) for name in config.profile_names()]


def _text(obj, prop):
    value = getattr(obj, prop, "")
    return str(value) if value else ""


def read(obj):
    """
    The technology recorded on *obj* itself, or None.

    Nothing is inherited here — technology_of() does that. The distinction
    matters when tagging: re-tagging a group should not be confused by a
    value that really lives on one of its members.
    """
    if obj is None:
        return None
    name = _text(obj, PROP_PROFILE)
    paths = {key: _text(obj, prop) for prop, key, _ in _PATH_PROPS}
    if not name and not any(paths.values()):
        return None
    return profile(name, paths["lyp_path"], paths["map_path"], paths["xml_path"])


def technology_of(obj, _depth=0):
    """
    The technology *obj* belongs to, following its chip and its groups.

    Returns None when nothing in the chain records one — a document built
    before technologies were recorded, or a part that is not from a GDS at
    all, and callers fall back to whatever they used before.
    """
    if obj is None or _depth > _MAX_DEPTH:
        return None
    own = read(obj)
    if own is not None:
        return own

    doc = getattr(obj, "Document", None)
    source = _text(obj, "SourceObject")
    if source and doc is not None:
        found = technology_of(doc.getObject(source), _depth + 1)
        if found is not None:
            return found

    for parent in getattr(obj, "InList", None) or []:
        if parent is obj:
            continue
        if parent.isDerivedFrom("App::DocumentObjectGroup"):
            found = technology_of(parent, _depth + 1)
            if found is not None:
                return found
    return None


def tag(obj, tech):
    """
    Record *tech* on *obj*, adding the properties if it does not have them.

    Safe to call on an object that already carries a technology: this
    overwrites it, which is what re-importing or correcting a chip means.
    """
    if obj is None or not tech:
        return None
    try:
        if not hasattr(obj, PROP_PROFILE):
            obj.addProperty("App::PropertyString", PROP_PROFILE, GROUP,
                            "Technology (PDK) profile this chip was imported with")
        obj.TechProfile = tech.get("name", "")
        for prop, key, doc_text in _PATH_PROPS:
            if not hasattr(obj, prop):
                obj.addProperty("App::PropertyString", prop, GROUP, doc_text)
            setattr(obj, prop, tech.get(key, "") or "")
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[Tech] could not record the technology on {obj.Name}: {exc}\n")
    return obj


def tag_all(objects, tech):
    """Record *tech* on several objects; returns how many were tagged."""
    return sum(1 for obj in objects or [] if tag(obj, tech) is not None)


def technologies(doc):
    """
    What technologies this document contains.

    Returns [{"name", "lyp_path", "map_path", "xml_path", "objects": [...]},
    ...], one entry per distinct technology, each listing the objects that
    carry it in their own right (not the ones that merely inherit it) —
    which is what a report should show: one line per die, not one per pad.
    """
    found = {}
    order = []
    for obj in getattr(doc, "Objects", None) or []:
        own = read(obj)
        if own is None:
            continue
        key = (own["name"], own["lyp_path"], own["map_path"], own["xml_path"])
        if key not in found:
            entry = dict(own)
            entry["objects"] = []
            found[key] = entry
            order.append(key)
        found[key]["objects"].append(obj.Name)
    return [found[key] for key in order]


def is_mixed(doc):
    """Whether more than one technology is present — worth saying out loud."""
    return len({entry["name"] for entry in technologies(doc)}) > 1


def stackup_for_path(xml_path):
    """
    parse_stackup_xml() for one file, cached on (path, mtime).

    Per-object lookups happen once per part in a materials pass, and a real
    stackup XML is parsed in milliseconds but thousands of times is not
    free. The mtime is in the key so editing the XML still takes effect.
    """
    if not xml_path or not os.path.isfile(xml_path):
        return None
    try:
        key = (os.path.abspath(xml_path), os.path.getmtime(xml_path))
    except OSError:
        return None
    if key in _stackup_cache:
        return _stackup_cache[key]
    try:
        from core.Core_Functionality import parse_stackup_xml
        data = parse_stackup_xml(xml_path) or None
    except Exception as exc:
        FreeCAD.Console.PrintWarning(
            f"[Tech] could not read the stackup '{xml_path}': {exc}\n")
        data = None
    _stackup_cache[key] = data
    return data


def stackup_of(obj):
    """The parsed stackup of the technology *obj* belongs to, or None."""
    tech = technology_of(obj)
    return stackup_for_path(tech["xml_path"]) if tech else None


def clear_cache():
    """Forget parsed stackups — for tests, and after editing a PDK by hand."""
    _stackup_cache.clear()


def describe(doc):
    """A short human summary of the technologies in *doc*."""
    entries = technologies(doc)
    if not entries:
        return "No technology recorded — imported before technologies were tracked."
    lines = []
    for entry in entries:
        name = entry["name"] or "(unnamed)"
        xml = os.path.basename(entry["xml_path"]) if entry["xml_path"] else "no stackup"
        lines.append(f"{name} ({xml}): {', '.join(entry['objects'])}")
    return "\n".join(lines)
