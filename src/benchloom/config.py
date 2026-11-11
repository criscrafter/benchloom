# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""App-wide settings shared by the desktop app, the command line and test runs.

Stored as JSON in ``~/.benchloom/config.json`` (or ``$BENCHLOOM_HOME/config.json``).
Before the rename to Benchloom this was ``~/.patchbay``; that folder is still
used while there is no ``~/.benchloom``.
Only settings that a test run needs live here, such as which plugins are
switched off; window layout and other desktop-only choices stay in Qt's own
settings store.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "config.json"


def home_dir() -> Path:
    """Benchloom's own folder: user plugins and config.json."""
    override = os.environ.get("BENCHLOOM_HOME") or os.environ.get("PATCHBAY_HOME")
    if override:
        return Path(override)
    home = Path.home() / ".benchloom"
    legacy = Path.home() / ".patchbay"
    return legacy if legacy.is_dir() and not home.exists() else home


def user_plugin_dir() -> Path:
    return home_dir() / "plugins"


def _project_key(project_dir: str | Path) -> str:
    """A project folder as it is compared and stored: absolute, in the system's spelling."""
    return os.path.normcase(str(Path(project_dir).resolve()))


@dataclass
class AppConfig:
    disabled_plugins: list[str] = field(default_factory=list)
    plugin_paths: list[str] = field(default_factory=list)  # extra folders to load plugins from
    # project folders whose own plugins/ folder may be loaded (the user said yes)
    trusted_projects: list[str] = field(default_factory=list)
    _other: dict = field(default_factory=dict, repr=False)  # keys from newer versions, kept

    @classmethod
    def load(cls) -> "AppConfig":
        try:
            data = json.loads((home_dir() / CONFIG_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}

        def strings(key):
            value = data.pop(key, [])
            return [str(v) for v in value] if isinstance(value, list) else []

        return cls(strings("disabled_plugins"), strings("plugin_paths"),
                   strings("trusted_projects"), data)

    def save(self) -> None:
        path = home_dir() / CONFIG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(self._other, disabled_plugins=sorted(set(self.disabled_plugins)),
                    plugin_paths=self.plugin_paths,
                    trusted_projects=sorted(set(self.trusted_projects)))
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def trusts(self, project_dir: str | Path) -> bool:
        """Whether the user allowed this project's own plugins to be loaded."""
        return _project_key(project_dir) in {_project_key(p) for p in self.trusted_projects}

    def set_trusted(self, project_dir: str | Path, trusted: bool = True) -> None:
        key = _project_key(project_dir)
        kept = [p for p in self.trusted_projects if _project_key(p) != key]
        self.trusted_projects = sorted(kept + [key] if trusted else kept)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        disabled = set(self.disabled_plugins)
        (disabled.discard if enabled else disabled.add)(plugin_id)
        self.disabled_plugins = sorted(disabled)
