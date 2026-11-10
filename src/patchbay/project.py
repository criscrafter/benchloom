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
    sleep log time panel python vars v check save_waveform
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
    # How the plot shows the logged rows: x (column), log_x (bool), right (columns).
    plot: dict[str, Any] = field(default_factory=dict)

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
        plot = data.get("plot", {})
        if not isinstance(plot, dict):
            raise ProjectError(f"{file}: [plot] must be a table")
        return cls(path, meta.get("name", path.name), mode,
                   meta.get("script", default_script), devices, dict(plot))

    def save(self) -> None:
        """Rewrite patchbay.toml from this object (comments in the file are lost)."""
        lines = [
            "[project]",
            f"name = {_toml(self.name)}",
            f"mode = {_toml(self.mode)}",
            f"script = {_toml(self.script)}",
            "",
        ]
        if self.plot:
            lines += ["[plot]"] + [f"{key} = {_toml(value)}" for key, value in self.plot.items()]
            lines.append("")
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

-- A pass/fail check: the run passes only if every check does (see report.html).
check("Current at 10 V", psu:measure_current(), 0.9, 1.1, "A")

psu:set_output(false)
print("Sweep finished")
"""



TEMPLATES_DIR = Path(__file__).with_name("templates")


@dataclass
class Template:
    """A starting point for a new project.

    The built-in example works in either mode; a template folder holds a finished
    project (``patchbay.toml`` with ``{name}`` for the project name, the test, maybe
    a ``panel.json``) plus ``template.toml`` with its title, description and mode.
    """

    id: str
    title: str
    description: str
    mode: str | None = None  # None: either mode
    folder: Path | None = None


EXAMPLE = Template("example", "Example", "A simulated power supply swept from 1 to 10 V, "
                   "so the project runs straight away.")


def templates() -> list[Template]:
    found = [EXAMPLE]
    for folder in sorted(TEMPLATES_DIR.iterdir()) if TEMPLATES_DIR.is_dir() else []:
        meta_file = folder / "template.toml"
        if not meta_file.is_file():
            continue
        meta = tomllib.loads(meta_file.read_text(encoding="utf-8"))
        found.append(Template(folder.name, meta.get("title", folder.name),
                              meta.get("description", ""), meta.get("mode"), folder))
    return found


def create_project(path: str | Path, mode: str = "lua", template: str = "example") -> Project:
    chosen = next((t for t in templates() if t.id == template), None)
    if chosen is None:
        raise ProjectError(f"there is no project template '{template}'")
    if chosen.mode:
        mode = chosen.mode
    if mode not in MODES:
        raise ProjectError(f"mode must be one of {', '.join(MODES)}")
    path = Path(path).resolve()
    if (path / PROJECT_FILE).exists():
        raise ProjectError(f"{path} already contains a Patchbay project")
    path.mkdir(parents=True, exist_ok=True)
    if chosen.folder is not None:
        for source in chosen.folder.iterdir():
            if source.is_file() and source.name != "template.toml":
                text = source.read_text(encoding="utf-8")
                if source.name == PROJECT_FILE:
                    text = text.replace("{name}", json.dumps(path.name)[1:-1])
                (path / source.name).write_text(text, encoding="utf-8")
        return Project.load(path)
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
