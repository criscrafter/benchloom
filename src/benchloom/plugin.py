# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Plugin system: device drivers, command metadata and plugin discovery.

A plugin is a folder containing a ``plugin.toml`` manifest and Python files.
Each device in the manifest names a driver class (a :class:`Device` subclass)
whose methods are marked with :func:`command`. The metadata collected here is
what the Lua API, the node editor and the device panels are generated from.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import inspect
import math
import re
import sys
import tomllib
import typing
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .config import AppConfig, user_plugin_dir

BUILTIN_PLUGIN_DIR = Path(__file__).parent / "plugins"

# where a plugin was found, as shown in the plugin manager
SOURCES = {"built-in": "Built-in", "user": "Installed", "folder": "Plugin folder",
           "project": "This project"}

# kinds of device a plugin can give in a [[device]] entry's ``category``, with the
# label the Add device dialog shows; a plugin may use others, and devices without
# one count as "other"
DEVICE_KINDS = {
    "dmm": "Multimeter",
    "scope": "Oscilloscope",
    "psu": "Power supply",
    "load": "Electronic load",
    "generator": "Signal generator",
    "counter": "Counter",
    "ac_source": "AC source",
    "smu": "Source measure unit",
    "analyzer": "Analyzer",
    "lcr": "LCR meter",
    "other": "Other",
}


def kind_label(kind: str) -> str:
    """The label of a device kind, also for kinds not in :data:`DEVICE_KINDS`."""
    return DEVICE_KINDS.get(kind) or kind.replace("_", " ").strip().capitalize() or "Other"

_TYPE_NAMES = {
    float: "number",
    int: "integer",
    bool: "boolean",
    str: "string",
    list: "array",
    type(None): "nil",
}


class CommandError(Exception):
    """A device command was called with bad arguments."""


class PluginError(Exception):
    """A plugin could not be found or loaded."""


@dataclass(frozen=True)
class Param:
    """Extra metadata for one command parameter."""

    unit: str = ""
    min: float | None = None
    max: float | None = None
    choices: tuple | None = None
    doc: str = ""


@dataclass(frozen=True)
class ParamInfo:
    name: str
    type: str
    unit: str = ""
    min: float | None = None
    max: float | None = None
    choices: tuple | None = None
    doc: str = ""
    required: bool = True
    default: Any = None
    items: str = ""  # for an array: the type of its items ("number", ..., or "any")


@dataclass(frozen=True)
class CommandInfo:
    name: str
    doc: str
    params: tuple[ParamInfo, ...]
    returns: str
    unit: str


def command(doc: Any = "", *, unit: str = "", params: dict[str, Param] | None = None):
    """Mark a driver method as a command usable from scripts and nodes.

    ``unit`` is the unit of the return value. ``params`` maps parameter names
    to :class:`Param` for units, ranges and choices. Types come from the
    method's annotations.
    """

    def wrap(fn):
        fn.__benchloom_command__ = {"doc": doc, "unit": unit, "params": params or {}}
        return fn

    if callable(doc):  # used as a bare @command
        fn, doc = doc, ""
        return wrap(fn)
    return wrap


def _type_name(hint: Any) -> str:
    if typing.get_origin(hint) is list:
        return "array"
    try:
        return _TYPE_NAMES.get(hint, "any")
    except TypeError:
        return "any"


def _describe(name: str, fn) -> CommandInfo:
    meta = fn.__benchloom_command__
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    params = []
    for p in list(inspect.signature(fn).parameters.values())[1:]:
        extra = meta["params"].get(p.name, Param())
        required = p.default is inspect.Parameter.empty
        type_name = _type_name(hints[p.name]) if p.name in hints else "any"
        items = ""
        if type_name == "array":  # list[float] -> "number"; a bare list -> "any"
            args = typing.get_args(hints[p.name])
            items = _type_name(args[0]) if args else "any"
        params.append(
            ParamInfo(
                name=p.name,
                type=type_name,
                items=items,
                unit=extra.unit,
                min=extra.min,
                max=extra.max,
                choices=tuple(extra.choices) if extra.choices else None,
                doc=extra.doc,
                required=required,
                default=None if required else p.default,
            )
        )
    doc = meta["doc"] or inspect.getdoc(fn) or ""
    returns = _type_name(hints["return"]) if "return" in hints else "any"
    return CommandInfo(name, doc, tuple(params), returns, meta["unit"])


def _coerce(cmd: str, p: ParamInfo, value: Any) -> Any:
    where = f"{cmd}(): argument '{p.name}'"
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if p.type in ("number", "integer") and is_number and not math.isfinite(value):
        # nan passes every min/max test and would reach the instrument (VOLT nan)
        raise CommandError(f"{where} must be a finite number, got {value!r}")
    if p.type == "number":
        if not is_number:
            raise CommandError(f"{where} must be a number, got {value!r}")
        value = float(value)
    elif p.type == "integer":
        if not is_number or float(value) != int(value):
            raise CommandError(f"{where} must be a whole number, got {value!r}")
        value = int(value)
    elif p.type == "array":
        if not isinstance(value, list):
            raise CommandError(f"{where} must be a list, got {value!r}")
        # each item is checked as a value of its own, with the parameter's limits
        return [_coerce(cmd, replace(p, name=f"{p.name}[{i}]", type=p.items or "any", items=""),
                        item) for i, item in enumerate(value, start=1)]
    elif p.type == "boolean":
        if not isinstance(value, bool):
            raise CommandError(f"{where} must be true or false, got {value!r}")
    elif p.type == "string":
        if not isinstance(value, str):
            raise CommandError(f"{where} must be a string, got {value!r}")
    unit = f" {p.unit}" if p.unit else ""
    if p.min is not None and is_number and value < p.min:
        raise CommandError(f"{where} is {value:g}{unit}, below the minimum of {p.min:g}{unit}")
    if p.max is not None and is_number and value > p.max:
        raise CommandError(f"{where} is {value:g}{unit}, above the maximum of {p.max:g}{unit}")
    if p.choices and value not in p.choices:
        options = ", ".join(repr(c) for c in p.choices)
        raise CommandError(f"{where} must be one of {options}, got {value!r}")
    return value


class Device:
    """Base class for device drivers.

    ``transport`` is an opened :class:`benchloom.transports.Transport`;
    ``settings`` holds the extra keys from the device's project entry.
    """

    def __init__(self, transport, settings: dict[str, Any] | None = None):
        self.transport = transport
        self.settings = settings or {}

    def connect(self) -> None:
        """Called once after the transport is open."""

    def close(self) -> None:
        """Called before the transport is closed, after a test ends.

        Drivers of sources and loads switch their output off here, unless the
        project's device entry has ``leave_output_on = true``.
        """

    def identify(self) -> str:
        """A short text proving the device answers; used by "Test connection"."""
        if "idn" in type(self).commands():
            return str(self.call("idn"))
        return ""

    def leave_output_on(self) -> bool:
        return bool(self.settings.get("leave_output_on", False))

    @classmethod
    def commands(cls) -> dict[str, CommandInfo]:
        cache = cls.__dict__.get("_benchloom_commands")
        if cache is None:
            cache = {
                name: _describe(name, fn)
                for name, fn in inspect.getmembers(cls, inspect.isfunction)
                if hasattr(fn, "__benchloom_command__")
            }
            cls._benchloom_commands = cache
        return cache

    def call(self, name: str, *args: Any) -> Any:
        """Validate ``args`` against the command metadata and run the command."""
        info = type(self).commands().get(name)
        if info is None:
            raise CommandError(f"unknown command '{name}'")
        if len(args) > len(info.params):
            raise CommandError(
                f"{name}() takes at most {len(info.params)} argument(s), got {len(args)}"
            )
        values = []
        for i, p in enumerate(info.params):
            if i >= len(args) or args[i] is None:
                if p.required:
                    raise CommandError(f"{name}(): missing argument '{p.name}'")
                values.append(p.default)
            else:
                values.append(_coerce(name, p, args[i]))
        return getattr(self, name)(*values)


@dataclass
class PluginInfo:
    id: str
    name: str
    version: str
    path: Path
    description: str = ""
    devices: list["DeviceSpec"] = field(default_factory=list)
    source: str = ""  # a key of SOURCES
    manufacturer: str = ""  # of its devices, unless a device names its own

    @property
    def official(self) -> bool:
        """Shipped with Benchloom, rather than installed or found in a folder."""
        return self.source == "built-in"

    @property
    def package(self) -> str:
        return "benchloom_plugin_" + re.sub(r"\W", "_", self.id)

    def _ensure_package(self) -> None:
        loaded = sys.modules.get(self.package)
        if loaded is not None and list(loaded.__path__) != [str(self.path)]:
            # code of a plugin with the same id from another folder (another project's
            # copy, say): drop it, or this plugin would run that one's drivers
            for name in [n for n in sys.modules
                         if n == self.package or n.startswith(self.package + ".")]:
                del sys.modules[name]
        if self.package not in sys.modules:
            spec = importlib.machinery.ModuleSpec(self.package, None, is_package=True)
            module = importlib.util.module_from_spec(spec)
            module.__path__ = [str(self.path)]
            sys.modules[self.package] = module

    def load(self, ref: str) -> type:
        """Load ``"module:Class"`` from this plugin's folder."""
        module_name, _, attr = ref.partition(":")
        self._ensure_package()
        try:
            module = importlib.import_module(f"{self.package}.{module_name}")
            return getattr(module, attr)
        except Exception as exc:
            raise PluginError(f"plugin '{self.id}': cannot load '{ref}': {exc}") from exc


SETTING_TYPES = ("int", "float", "bool", "text")


@dataclass
class SettingSpec:
    """A setting of a device entry that the app offers as a field.

    Declared in plugin.toml under ``[[device.setting]]``; the driver reads it from
    ``self.settings``. Settings a plugin does not declare can still be written into
    the device entry by hand.
    """

    name: str
    label: str
    type: str = "text"
    default: Any = None
    min: float | None = None
    max: float | None = None
    help: str = ""


@dataclass
class DeviceSpec:
    id: str
    name: str
    plugin: PluginInfo
    driver: str
    simulator: str | None = None
    transports: tuple[str, ...] = ()
    description: str = ""
    default_name: str = ""  # suggested name in a project, e.g. "psu"
    settings: tuple[SettingSpec, ...] = ()
    default_resource: str = ""  # offered when adding the device, e.g. "usb://049F:505A"
    category: str = ""  # a key of DEVICE_KINDS, or a kind of the plugin's own
    manufacturer: str = ""

    @property
    def kind(self) -> str:
        return self.category or "other"

    @property
    def maker(self) -> str:
        """The manufacturer the Add device dialog groups the device under."""
        return self.manufacturer or self.plugin.manufacturer or self.plugin.name

    def load_driver(self) -> type[Device]:
        return self.plugin.load(self.driver)

    def load_simulator(self) -> type | None:
        return self.plugin.load(self.simulator) if self.simulator else None


def _setting(entry: dict) -> SettingSpec:
    kind = entry.get("type", "text")
    if kind not in SETTING_TYPES:
        raise KeyError(f"setting type must be one of {', '.join(SETTING_TYPES)}, not {kind!r}")
    return SettingSpec(
        name=entry["name"], label=entry.get("label", entry["name"]), type=kind,
        default=entry.get("default"), min=entry.get("min"), max=entry.get("max"),
        help=entry.get("help", ""),
    )


def read_manifest(folder: Path) -> PluginInfo:
    manifest = folder / "plugin.toml"
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        meta = data["plugin"]
        info = PluginInfo(
            id=meta["id"],
            name=meta.get("name", meta["id"]),
            version=str(meta.get("version", "0")),
            path=folder,
            description=meta.get("description", ""),
            manufacturer=str(meta.get("manufacturer", "")).strip(),
        )
        for dev in data.get("device", []):
            info.devices.append(
                DeviceSpec(
                    id=f"{info.id}.{dev['id']}",
                    name=dev.get("name", dev["id"]),
                    plugin=info,
                    driver=dev["driver"],
                    simulator=dev.get("simulator"),
                    transports=tuple(dev.get("transports", ())),
                    description=dev.get("description", ""),
                    default_name=dev.get("default_name", ""),
                    settings=tuple(_setting(entry) for entry in dev.get("setting", ())),
                    default_resource=dev.get("default_resource", ""),
                    category=str(dev.get("category", "")).strip().lower(),
                    manufacturer=str(dev.get("manufacturer", "")).strip(),
                )
            )
    except KeyError as exc:
        raise PluginError(f"{manifest}: invalid manifest: '{exc.args[0]}' is missing") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PluginError(f"{manifest}: invalid manifest: {exc}") from exc
    return info


class PluginRegistry:
    """All plugins found in a list of folders. Later folders override earlier ones.

    ``search_paths`` holds folders, or ``(folder, source)`` pairs where source is a
    key of :data:`SOURCES`. Plugins whose id is in ``disabled`` are found but not
    offered.
    """

    def __init__(self, search_paths: list, disabled=()):
        self.plugins: dict[str, PluginInfo] = {}  # the plugins in use, by id
        self.found: list[PluginInfo] = []  # every plugin found, in load order
        self.disabled = set(disabled)
        self.errors: list[str] = []
        self.broken: list[tuple[Path, str]] = []  # plugin folders that failed to load
        # the project's own plugins, when the user has not allowed them: read, never loaded
        self.blocked: list[PluginInfo] = []
        for entry in search_paths:
            root, source = entry if isinstance(entry, tuple) else (entry, "")
            root = Path(root)
            if not root.is_dir():
                continue
            for folder in sorted(root.iterdir()):
                if (folder / "plugin.toml").is_file():
                    try:
                        info = read_manifest(folder)
                    except PluginError as exc:
                        self.errors.append(str(exc))
                        self.broken.append((folder, str(exc)))
                        continue
                    info.source = source
                    self.found.append(info)
                    if info.id not in self.disabled:
                        self.plugins[info.id] = info

    @classmethod
    def default(cls, project_dir: Path | None = None, config: AppConfig | None = None,
                trust_project: bool | None = None) -> "PluginRegistry":
        """Built-in plugins, installed ones, extra folders from the app config and
        the project's own ``plugins`` folder, minus the plugins switched off.

        A project's plugins are Python code that came with the project, so they are
        used only when the user allowed it for that project (``AppConfig.trusts``;
        ``trust_project`` overrides that). Otherwise they are listed in ``blocked``.
        """
        config = config or AppConfig.load()
        paths = [(BUILTIN_PLUGIN_DIR, "built-in"), (user_plugin_dir(), "user")]
        paths += [(Path(p), "folder") for p in config.plugin_paths]
        blocked = []
        if project_dir is not None:
            if trust_project is None:
                trust_project = config.trusts(project_dir)
            if trust_project:
                paths.append((Path(project_dir) / "plugins", "project"))
            else:
                blocked = project_plugins(project_dir)
        registry = cls(paths, config.disabled_plugins)
        registry.blocked = blocked
        return registry

    def status(self, info: PluginInfo) -> str:
        """'enabled', 'disabled', or 'replaced' (a later plugin has the same id)."""
        if info.id in self.disabled:
            return "disabled"
        active = self.plugins.get(info.id)
        return "enabled" if active is not None and active.path == info.path else "replaced"

    @property
    def devices(self) -> dict[str, DeviceSpec]:
        return {d.id: d for p in self.plugins.values() for d in p.devices}

    def device(self, device_id: str) -> DeviceSpec:
        devices = self.devices
        if device_id not in devices:
            for info in self.found:
                if info.id in self.disabled and any(d.id == device_id for d in info.devices):
                    raise PluginError(f"device type '{device_id}' comes from the plugin "
                                      f"'{info.name}', which is switched off in the plugin "
                                      "manager")
            for info in self.blocked:
                if any(d.id == device_id for d in info.devices):
                    raise PluginError(f"device type '{device_id}' comes from this project's "
                                      f"own plugin '{info.name}', which was not allowed to "
                                      f"load. {PROJECT_PLUGIN_HINT}")
            known = ", ".join(sorted(devices)) or "none"
            raise PluginError(f"unknown device type '{device_id}' (available: {known})")
        return devices[device_id]


PROJECT_PLUGIN_HINT = ("Plugins are Python code and run with your permissions: allow them "
                       "when opening the project in the app, or with --trust-plugins "
                       "('benchloom run' or 'benchloom describe'), if you trust where the "
                       "project comes from.")


def project_plugins(project_dir: str | Path) -> list[PluginInfo]:
    """The plugins in a project's own ``plugins`` folder, from their manifests alone:
    nothing in them is run."""
    root = Path(project_dir) / "plugins"
    found = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        if (folder / "plugin.toml").is_file():
            try:
                info = read_manifest(folder)
            except PluginError:
                # unreadable, but still something the project would have loaded
                info = PluginInfo(folder.name, folder.name, "?", folder)
            info.source = "project"
            found.append(info)
    return found
