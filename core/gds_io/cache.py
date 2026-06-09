"""
Disk-cache helpers for serialised GDS import results.

Keeps the expensive OCCT B-rep computation out of repeat imports for the
same GDS + layer combination.  Shapes are stored as BREP strings; Mesh
objects cannot be serialised and are silently skipped.
"""
import hashlib
import os
import pickle
import platform
from pathlib import Path

import FreeCAD
import Part


# ── Cache directory ──────────────────────────────────────────────────────────

def _gds_cache_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "FreeCAD" / "DI-PASSIONATE" / "gds_cache"


_GDS_CACHE_DIR = _gds_cache_dir()


# ── Public API ────────────────────────────────────────────────────────────────

def cache_key(gds_path: str, selected_layers, options: dict) -> str:
    """Compute a short hex digest that uniquely identifies an import configuration."""
    h = hashlib.sha256()
    try:
        h.update(str(Path(gds_path).stat().st_mtime_ns).encode())
    except OSError:
        h.update(gds_path.encode())
    h.update(repr([(l.get("layer_id"), l.get("datatype")) for l in selected_layers]).encode())
    h.update(repr(sorted(options.items())).encode())
    return h.hexdigest()[:24]


def load_cache(key: str):
    """Return deserialised results list or *None* on miss / error."""
    p = _GDS_CACHE_DIR / f"{key}.pkl"
    if not p.exists():
        return None
    try:
        with open(p, "rb") as fh:
            payload = pickle.load(fh)
        results = []
        for entry in payload:
            e = dict(entry)
            if "shape_brep" in e:
                shp = Part.Shape()
                shp.importBrepFromString(e.pop("shape_brep"))
                e["shape"] = shp
            results.append(e)
        return results
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"GDS cache load failed ({exc}), re-importing.\n")
        return None


def save_cache(key: str, results: list):
    """Serialise *results* to disk; silently skips un-serialisable shapes."""
    try:
        _GDS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = []
        for entry in results:
            e = dict(entry)
            if "shape" in e and not e.get("is_mesh"):
                try:
                    e["shape_brep"] = e.pop("shape").exportBrepToString()
                except Exception:
                    return   # non-serialisable — skip cache for this import
            payload.append(e)
        p = _GDS_CACHE_DIR / f"{key}.pkl"
        with open(p, "wb") as fh:
            pickle.dump(payload, fh, protocol=4)
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"GDS cache save failed: {exc}\n")
