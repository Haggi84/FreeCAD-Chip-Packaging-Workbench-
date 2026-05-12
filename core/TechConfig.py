"""
Technology Configuration Manager
==================================
Global config  – JSON file in the FreeCAD user-data folder.
                 Persists across restarts.  Contains named profiles.
Local config   – In-memory overlay for the current session.
                 Initialised from the active global profile on workbench load.
                 Changes do NOT affect the global config unless explicitly saved.

Usage
-----
    from core.TechConfig import tech_config   # module-level singleton

    # Read effective paths (local overrides global)
    tech_config.get_lyp()   # -> str path or ""
    tech_config.get_map()
    tech_config.get_xml()

    # Is a valid file already configured?
    tech_config.has_lyp()   # -> bool

    # Override for this session only
    tech_config.set_local(lyp="C:/new.lyp")

    # Reset session back to active global profile
    tech_config.reset_local()
"""

import json
import os
import platform
from pathlib import Path


# ── Defaults ───────────────────────────────────────────────────────────────────

_EMPTY_PROFILE = {
    "description": "",
    "lyp_path":    "",
    "map_path":    "",
    "xml_path":    "",
}

_DEFAULT_NAME = "Default"


# ── Manager ────────────────────────────────────────────────────────────────────

class TechConfigManager:
    """Singleton that manages global + local technology configuration."""

    def __init__(self):
        self._global: dict = {"active": _DEFAULT_NAME, "profiles": {}}
        self._local:  dict = dict(_EMPTY_PROFILE)
        self._config_file: Path = self._resolve_config_path()
        self._load_global()
        self._ensure_ihp_sg13g2_profile()
        self._init_local()

    # ── Config-file location ───────────────────────────────────────────────

    @staticmethod
    def _resolve_config_path() -> Path:
        system = platform.system()
        if system == "Windows":
            base = Path(os.environ.get("APPDATA",
                        Path.home() / "AppData" / "Roaming"))
        elif system == "Darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_DATA_HOME",
                        Path.home() / ".local" / "share"))
        return base / "FreeCAD" / "DI-PASSIONATE" / "tech_config.json"

    # ── Global persistence ─────────────────────────────────────────────────

    def _load_global(self):
        try:
            if self._config_file.exists():
                with open(self._config_file, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    self._global = data
        except Exception as exc:
            try:
                import FreeCAD
                FreeCAD.Console.PrintWarning(
                    f"TechConfig: could not load config file: {exc}\n"
                )
            except Exception:
                pass

    def _ensure_ihp_sg13g2_profile(self):
        """Seed the built-in IHP-PDK SG13G2 profile on first run.

        Only runs when no user config file exists yet, so existing user
        settings are never overwritten.
        """
        profile_name = "IHP-PDK SG13G2"
        if self._config_file.exists():
            return
        stack_dir = Path(__file__).parent.parent / "resources" / "stack_info" / "IHP-PDK_SG13G2"
        self.set_profile(profile_name, {
            "description": "IHP SG13G2 BiCMOS 130 nm PDK — 200 µm stack",
            "lyp_path":    str(stack_dir / "sg13g2.lyp"),
            "map_path":    str(stack_dir / "sg13g2.map"),
            "xml_path":    str(stack_dir / "SG13G2_200um.xml"),
        })
        self.set_active_name(profile_name)
        self.save_global()

    def save_global(self):
        """Write the current global config to disk."""
        try:
            self._config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._config_file, "w", encoding="utf-8") as fh:
                json.dump(self._global, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            try:
                import FreeCAD
                FreeCAD.Console.PrintWarning(
                    f"TechConfig: could not save config file: {exc}\n"
                )
            except Exception:
                pass

    @property
    def config_file_path(self) -> Path:
        return self._config_file

    # ── Profile management ─────────────────────────────────────────────────

    def profile_names(self) -> list:
        return list(self._global.get("profiles", {}).keys())

    def get_profile(self, name: str) -> dict:
        return dict(self._global.get("profiles", {}).get(name, _EMPTY_PROFILE))

    def set_profile(self, name: str, data: dict):
        """Create or overwrite a global profile (does not auto-save)."""
        if "profiles" not in self._global:
            self._global["profiles"] = {}
        self._global["profiles"][name] = {
            "description": data.get("description", ""),
            "lyp_path":    data.get("lyp_path",    ""),
            "map_path":    data.get("map_path",     ""),
            "xml_path":    data.get("xml_path",     ""),
        }

    def delete_profile(self, name: str):
        self._global.get("profiles", {}).pop(name, None)
        if self.get_active_name() == name:
            names = self.profile_names()
            self._global["active"] = names[0] if names else ""

    def get_active_name(self) -> str:
        return self._global.get("active", "")

    def set_active_name(self, name: str):
        self._global["active"] = name

    def get_active_profile(self) -> dict:
        return self.get_profile(self.get_active_name())

    # ── Local (session) config ─────────────────────────────────────────────

    def _init_local(self):
        active = self.get_active_profile()
        self._local = dict(active) if active else dict(_EMPTY_PROFILE)

    def reset_local(self):
        """Revert session overrides to the active global profile."""
        self._init_local()

    def apply_profile_to_local(self, name: str):
        """Copy a specific global profile into the session config."""
        self._local = dict(self.get_profile(name))

    def set_local(self, lyp: str = None, map_: str = None, xml: str = None):
        """Override individual paths for the current session only."""
        if lyp  is not None:
            self._local["lyp_path"] = lyp
        if map_ is not None:
            self._local["map_path"] = map_
        if xml  is not None:
            self._local["xml_path"] = xml

    def get_local(self) -> dict:
        return dict(self._local)

    # ── Effective path accessors ───────────────────────────────────────────

    def get_lyp(self) -> str:
        return self._local.get("lyp_path", "") or ""

    def get_map(self) -> str:
        return self._local.get("map_path", "") or ""

    def get_xml(self) -> str:
        return self._local.get("xml_path", "") or ""

    def has_lyp(self) -> bool:
        p = self.get_lyp()
        return bool(p) and Path(p).is_file()

    def has_map(self) -> bool:
        p = self.get_map()
        return bool(p) and Path(p).is_file()

    def has_xml(self) -> bool:
        p = self.get_xml()
        return bool(p) and Path(p).is_file()

    def is_configured(self) -> bool:
        """True if at least LYP is configured — minimum needed for GDS import."""
        return self.has_lyp()

    def status_summary(self) -> str:
        """One-line description of the active configuration for status bars."""
        name = self.get_active_name()
        parts = []
        if self.has_lyp(): parts.append("LYP")
        if self.has_map(): parts.append("MAP")
        if self.has_xml(): parts.append("XML")
        configured = ", ".join(parts) if parts else "none"
        return f"Profile: {name or '(none)'}  —  configured: {configured}"


# ── Module-level singleton ────────────────────────────────────────────────────

tech_config = TechConfigManager()
