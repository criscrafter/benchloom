# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Project folders: a ``benchloom.toml`` plus the test itself (Lua or a graph)."""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_FILE = "benchloom.toml"
LEGACY_PROJECT_FILE = "patchbay.toml"  # projects made before the rename to Benchloom
MODES = ("lua", "visual")


# Names a device must not take: Lua keywords, Lua's standard globals and Benchloom's own.
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


def project_file(path: str | Path) -> Path | None:
    """The project file in a folder: benchloom.toml, or the older patchbay.toml."""
    for name in (PROJECT_FILE, LEGACY_PROJECT_FILE):
        file = Path(path) / name
        if file.is_file():
            return file
    return None


def is_project(path: str | Path) -> bool:
    return project_file(path) is not None


def device_name_problem(name: str) -> str | None:
    """Why ``name`` cannot name a device, or None if it can."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return "must start with a letter and contain only letters, digits and underscores"
    if name in RESERVED_NAMES or name.startswith("_"):
        return "is reserved by Lua or Benchloom; choose another name"
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
    # How the plot shows the logged rows: x (column), log_x (bool), right, hide and
    # columns (lists of columns; columns also orders data.csv, the table and the report).
    plot: dict[str, Any] = field(default_factory=dict)
    saved_with: str = ""  # the Benchloom version that saved the project last; "" if unknown

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        path = Path(path).resolve()
        file = project_file(path) or path / PROJECT_FILE
        if not file.is_file():
            raise ProjectError(f"{path} is not a Benchloom project (no {PROJECT_FILE})")
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
                   meta.get("script", default_script), devices, dict(plot),
                   str(meta.get("saved_with", "")))

    def save(self) -> None:
        """Rewrite benchloom.toml from this object (comments in the file are lost)."""
        self.saved_with = _version()
        lines = [
            "[project]",
            f"name = {_toml(self.name)}",
            f"mode = {_toml(self.mode)}",
            f"script = {_toml(self.script)}",
            f"saved_with = {_toml(self.saved_with)}",
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
        # an older project keeps its patchbay.toml
        file = project_file(self.path) or self.path / PROJECT_FILE
        file.write_text("\n".join(lines), encoding="utf-8")


def _version() -> str:
    from . import __version__

    return __version__


def _set_project_key(path: str | Path, key: str, value: str) -> None:
    """Set ``key`` in the [project] table of the project file, keeping the rest of the
    file (comments too) as it is. Writes nothing if the value is already there."""
    file = project_file(path)
    if file is None:
        raise ProjectError(f"{path} is not a Benchloom project (no {PROJECT_FILE})")
    text = file.read_text(encoding="utf-8")
    lines = text.splitlines()
    new = f"{key} = {_toml(value)}"
    section = None
    start = end = None  # the [project] table: header line, last non-empty line
    found = None
    for index, line in enumerate(lines):
        header = re.match(r"\s*\[([^\]]*)\]", line)
        if header:
            section = header.group(1).strip()
            if section == "project":
                start = end = index
            continue
        if section == "project":
            if re.match(rf"\s*{re.escape(key)}\s*=", line):
                found = index
            if line.strip():
                end = index
    if found is not None:
        if lines[found].split("#", 1)[0].strip() == new:
            return
        comment = lines[found].split("#", 1)[1] if "#" in lines[found] else ""
        lines[found] = new + (f"      #{comment}" if comment else "")
    elif start is None:
        lines[:0] = ["[project]", new, ""]
    else:
        lines.insert(end + 1, new)
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stamp_version(path: str | Path) -> None:
    """Note in the project file which Benchloom version saved the project last."""
    _set_project_key(path, "saved_with", _version())


def last_saved(path: str | Path) -> float | None:
    """When the project was saved last, as a timestamp: the newest of its project file,
    test and front panel. None if none of them can be read."""
    path = Path(path)
    files = [project_file(path), path / "panel.json"]
    try:
        files.append(path / Project.load(path).script)
    except ProjectError:
        pass
    times = []
    for file in files:
        try:
            if file is not None:
                times.append(file.stat().st_mtime)
        except OSError:
            pass
    return max(times, default=None)


def project_name_problem(name: str) -> str | None:
    """Why ``name`` cannot name a project folder, or None if it can."""
    if not name.strip() or name.strip() in (".", "..") or re.search(r'[\\/:*?"<>|]', name):
        return "Enter a project name without special characters."
    return None


def rename_project(path: str | Path, name: str) -> Path:
    """Give a project another name: its folder and the name in its project file.
    Returns the project's new place."""
    name = name.strip()
    problem = project_name_problem(name)
    if problem:
        raise ProjectError(problem)
    path = Path(path).resolve()
    if not is_project(path):
        raise ProjectError(f"{path} is not a Benchloom project")
    target = path.with_name(name)
    if target.name != path.name:
        if target.exists() and not _same_file(target, path):
            raise ProjectError(f"{target} already exists")
        try:
            # through a temporary name, so a change of case alone works on Windows too
            temp = path.with_name(path.name + ".renaming")
            path.rename(temp)
            temp.rename(target)
        except OSError as exc:
            raise ProjectError(
                f"Could not rename {path.name}: {exc.strerror or exc}. "
                "Is a file of the project open in another program?") from exc
    _set_project_key(target, "name", name)
    return target


def move_project(path: str | Path, folder: str | Path) -> Path:
    """Move a project folder into ``folder``. Returns the project's new place."""
    path = Path(path).resolve()
    folder = Path(folder).resolve()
    if not is_project(path):
        raise ProjectError(f"{path} is not a Benchloom project")
    target = folder / path.name
    if target == path:
        return path
    if folder == path or path in folder.parents:
        raise ProjectError("A project cannot move into its own folder.")
    if target.exists():
        raise ProjectError(f"{target} already exists.")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
    except OSError as exc:
        raise ProjectError(
            f"Could not move {path.name}: {exc.strerror or exc}. "
            "Is a file of the project open in another program?") from exc
    return target


def duplicate_project(path: str | Path, name: str) -> Path:
    """Copy a project next to itself under another name, without its past runs.
    Returns the copy's place."""
    name = name.strip()
    problem = project_name_problem(name)
    if problem:
        raise ProjectError(problem)
    path = Path(path).resolve()
    if not is_project(path):
        raise ProjectError(f"{path} is not a Benchloom project")
    target = path.with_name(name)
    if target.exists():
        raise ProjectError(f"{target} already exists.")
    try:
        shutil.copytree(path, target, ignore=lambda folder, names: (
            ["runs"] if Path(folder) == path else []))
    except OSError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise ProjectError(f"Could not copy {path.name}: {exc}") from exc
    _set_project_key(target, "name", name)
    return target


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.samefile(b)
    except OSError:
        return False


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

_EMPTY_TOML = """\
[project]
name = "{name}"
mode = "{mode}"      # "lua" or "visual"
script = "{script}"

# One entry per instrument; the entry name is the name used in the test. For example:
#   [devices.psu]
#   device = "demo.psu"
#   resource = "sim://"     # or serial://COM3?baud=9600, tcp://192.168.1.20:5025, ...
"""

_EMPTY_LUA = """\
-- Add your devices in the Devices panel, then write the test here.
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
    project (``benchloom.toml`` with ``{name}`` for the project name, the test, maybe
    a ``panel.json``) plus ``template.toml`` with its title, description and mode.
    """

    id: str
    title: str
    description: str
    mode: str | None = None  # None: either mode
    folder: Path | None = None


EXAMPLE = Template("example", "Example", "A simulated power supply swept from 1 to 10 V, "
                   "so the project runs straight away.")
EMPTY = Template("empty", "Empty project", "No devices and an empty test, to start "
                 "from scratch.")


def templates() -> list[Template]:
    found = [EXAMPLE, EMPTY]
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
    if is_project(path):
        raise ProjectError(f"{path} already contains a Benchloom project")
    path.mkdir(parents=True, exist_ok=True)
    if chosen.folder is not None:
        for source in chosen.folder.iterdir():
            if source.is_file() and source.name != "template.toml":
                text = source.read_text(encoding="utf-8")
                if source.name == PROJECT_FILE:
                    text = text.replace("{name}", json.dumps(path.name)[1:-1])
                (path / source.name).write_text(text, encoding="utf-8")
        stamp_version(path)
        return Project.load(path)
    script = "main.lua" if mode == "lua" else "main.graph.json"
    empty = chosen is EMPTY
    (path / PROJECT_FILE).write_text(
        (_EMPTY_TOML if empty else _TOML).format(
            name=json.dumps(path.name)[1:-1], mode=mode, script=script),
        encoding="utf-8")
    stamp_version(path)
    if mode == "lua":
        content = _EMPTY_LUA if empty else _LUA
    else:
        from .graph import GRAPH_VERSION, example_graph

        graph = example_graph() if not empty else {
            "version": GRAPH_VERSION, "wires": [],
            "nodes": [{"id": "n1", "type": "core.start", "x": 40, "y": 40}]}
        content = json.dumps(graph, indent=1)
    (path / script).write_text(content, encoding="utf-8")
    return Project.load(path)
