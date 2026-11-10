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
import re
import sys
import tomllib
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BUILTIN_PLUGIN_DIR = Path(__file__).parent / "plugins"
USER_PLUGIN_DIR = Path.home() / ".patchbay" / "plugins"

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
        fn.__patchbay_command__ = {"doc": doc, "unit": unit, "params": params or {}}
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
    meta = fn.__patchbay_command__
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    params = []
    for p in list(inspect.signature(fn).parameters.values())[1:]:
        extra = meta["params"].get(p.name, Param())
        required = p.default is inspect.Parameter.empty
        params.append(
            ParamInfo(
                name=p.name,
                type=_type_name(hints[p.name]) if p.name in hints else "any",
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
    if p.type == "number":
        if not is_number:
            raise CommandError(f"{where} must be a number, got {value!r}")
        value = float(value)
    elif p.type == "integer":
        if not is_number or float(value) != int(value):
            raise CommandError(f"{where} must be a whole number, got {value!r}")
        value = int(value)
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

    ``transport`` is an opened :class:`patchbay.transports.Transport`;
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
        cache = cls.__dict__.get("_patchbay_commands")
        if cache is None:
            cache = {
                name: _describe(name, fn)
                for name, fn in inspect.getmembers(cls, inspect.isfunction)
                if hasattr(fn, "__patchbay_command__")
            }
            cls._patchbay_commands = cache
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

    @property
    def package(self) -> str:
        return "patchbay_plugin_" + re.sub(r"\W", "_", self.id)

    def _ensure_package(self) -> None:
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

    def load_driver(self) -> type[Device]:
        return self.plugin.load(self.driver)

    def load_simulator(self) -> type | None:
        return self.plugin.load(self.simulator) if self.simulator else None


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
                )
            )
    except (KeyError, tomllib.TOMLDecodeError) as exc:
        raise PluginError(f"{manifest}: invalid manifest: {exc}") from exc
    return info


class PluginRegistry:
    """All plugins found in a list of folders. Later folders override earlier ones."""

    def __init__(self, search_paths: list[Path]):
        self.plugins: dict[str, PluginInfo] = {}
        self.errors: list[str] = []
        for root in search_paths:
            if not root.is_dir():
                continue
            for folder in sorted(root.iterdir()):
                if (folder / "plugin.toml").is_file():
                    try:
                        info = read_manifest(folder)
                        self.plugins[info.id] = info
                    except PluginError as exc:
                        self.errors.append(str(exc))

    @classmethod
    def default(cls, project_dir: Path | None = None) -> "PluginRegistry":
        paths = [BUILTIN_PLUGIN_DIR, USER_PLUGIN_DIR]
        if project_dir is not None:
            paths.append(Path(project_dir) / "plugins")
        return cls(paths)

    @property
    def devices(self) -> dict[str, DeviceSpec]:
        return {d.id: d for p in self.plugins.values() for d in p.devices}

    def device(self, device_id: str) -> DeviceSpec:
        devices = self.devices
        if device_id not in devices:
            known = ", ".join(sorted(devices)) or "none"
            raise PluginError(f"unknown device type '{device_id}' (available: {known})")
        return devices[device_id]
