"""Project folders: a ``patchbay.toml`` plus the test itself (Lua or a graph)."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_FILE = "patchbay.toml"
MODES = ("lua", "visual")


# Names a device must not take: Lua keywords, Lua's standard globals and Patchbay's own.
RESERVED_NAMES = frozenset("""
    and break do else elseif end false for function goto if in local nil not or repeat
    return then true until while
    assert collectgarbage dofile error getmetatable ipairs load loadfile next pairs pcall
    print rawequal rawget rawlen rawset require select setmetatable tonumber tostring type
    xpcall coroutine debug io math os package string table utf8
    sleep log time panel python vars v
""".split())


class ProjectError(Exception):
    pass


def device_name_problem(name: str) -> str | None:
    """Why ``name`` cannot name a device, or None if it can."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return "must start with a letter and contain only letters, digits and underscores"
    if name in RESERVED_NAMES or name.startswith("_"):
        return "is reserved by Lua or Patchbay; choose another name"
    return None


@dataclass
class DeviceConfig:
    name: str
    device: str
    resource: str
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class Project:
    path: Path
    name: str
    mode: str
    script: str
    devices: dict[str, DeviceConfig]

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        path = Path(path).resolve()
        file = path / PROJECT_FILE
        if not file.is_file():
            raise ProjectError(f"{path} is not a Patchbay project (no {PROJECT_FILE})")
        try:
            data = tomllib.loads(file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ProjectError(f"{file}: {exc}") from exc
        meta = data.get("project", {})
        mode = meta.get("mode", "lua")
        if mode not in MODES:
            raise ProjectError(f"{file}: mode must be one of {', '.join(MODES)}")
        devices = {}
        for name, entry in data.get("devices", {}).items():
            problem = device_name_problem(name)
            if problem:
                raise ProjectError(f"{file}: device name '{name}' {problem}")
            entry = dict(entry)
            try:
                devices[name] = DeviceConfig(
                    name, entry.pop("device"), entry.pop("resource", "sim://"), entry
                )
            except KeyError:
                raise ProjectError(f"{file}: device '{name}' is missing 'device'") from None
        default_script = "main.lua" if mode == "lua" else "main.graph.json"
        return cls(path, meta.get("name", path.name), mode,
                   meta.get("script", default_script), devices)

    def save(self) -> None:
        """Rewrite patchbay.toml from this object (comments in the file are lost)."""
        lines = [
            "[project]",
            f"name = {_toml(self.name)}",
            f"mode = {_toml(self.mode)}",
            f"script = {_toml(self.script)}",
            "",
        ]
        for device in self.devices.values():
            lines += [
                f"[devices.{device.name}]",
                f"device = {_toml(device.device)}",
                f"resource = {_toml(device.resource)}",
            ]
            lines += [f"{key} = {_toml(value)}" for key, value in device.settings.items()
                      if not isinstance(value, dict)]
            lines.append("")
        (self.path / PROJECT_FILE).write_text("\n".join(lines), encoding="utf-8")


def _toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


_TOML = """\
[project]
name = "{name}"
mode = "{mode}"      # "lua" or "visual"
script = "{script}"

# One entry per instrument. The entry name is the name used in the test.
# Change `resource` to connect real hardware, for example:
#   serial://COM3?baud=9600      tcp://192.168.1.20:5025
#   prologix://COM4/5            visa://GPIB0::5::INSTR
[devices.psu]
device = "demo.psu"
resource = "sim://"
"""

_LUA = """\
-- Sweep the supply from 1 V to 10 V and record voltage and current.
print("Connected to " .. psu:idn())

psu:set_current_limit(2)
psu:set_output(true)

for volts = 1, 10 do
    psu:set_voltage(volts)
    sleep(0.1)
    log { set_V = volts, V = psu:measure_voltage(), I = psu:measure_current() }
end

psu:set_output(false)
print("Sweep finished")
"""



def create_project(path: str | Path, mode: str = "lua") -> Project:
    if mode not in MODES:
        raise ProjectError(f"mode must be one of {', '.join(MODES)}")
    path = Path(path).resolve()
    if (path / PROJECT_FILE).exists():
        raise ProjectError(f"{path} already contains a Patchbay project")
    path.mkdir(parents=True, exist_ok=True)
    script = "main.lua" if mode == "lua" else "main.graph.json"
    (path / PROJECT_FILE).write_text(
        _TOML.format(name=path.name, mode=mode, script=script), encoding="utf-8"
    )
    if mode == "lua":
        content = _LUA
    else:
        from .graph import example_graph

        content = json.dumps(example_graph(), indent=1)
    (path / script).write_text(content, encoding="utf-8")
    return Project.load(path)
