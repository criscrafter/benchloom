"""Runs a project: opens its devices and executes the Lua test script.

What a script sees:

    <name>:<command>(...)   every device from patchbay.toml, by its entry name
    sleep(seconds)          wait
    log { key = value }     append a row to the run's data.csv
    time()                  seconds since the run started
    print(...)              write to the console (and the run's console.log)
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:
    import lupa.lua54 as lupa
except ImportError:  # pragma: no cover - platforms without the 5.4 build
    import lupa

from . import __version__
from .datalog import RunLog
from .plugin import Device, PluginRegistry
from .project import DeviceConfig, Project, ProjectError
from .transports import open_transport

_STOP = "__patchbay_stop__"

_BOOTSTRAP = """
function(emit, should_stop)
    python = nil  -- scripts talk to Python only through the functions below

    print = function(...)
        local parts = {}
        for i = 1, select("#", ...) do
            parts[i] = tostring((select(i, ...)))
        end
        emit(table.concat(parts, "\\t"))
    end

    debug.sethook(function()
        if should_stop() then error("%s", 0) end
    end, "", 10000)

    -- Builds the script-side object for one device.
    return function(device_name, names, call)
        local device = {}
        for _, name in ipairs(names) do
            device[name] = function(self, ...)
                if self ~= device then
                    error("use a colon to call device commands: " .. device_name
                          .. ":" .. name .. "(...)", 2)
                end
                local ok, result = call(name, ...)
                if not ok then error(result, 2) end
                return result
            end
        end
        return device
    end
end
""" % _STOP


def _no_attributes(obj, name, is_setting):
    """Scripts may call the Python functions they are given, not look inside them."""
    raise AttributeError("not accessible from scripts")


class ScriptError(Exception):
    """The Lua script failed; the message includes the file and line."""


class ScriptStopped(Exception):
    """The script was stopped before it finished."""


class LuaEngine:
    def __init__(
        self,
        *,
        on_print: Callable[[str], None],
        log_row: Callable[[dict[str, Any]], None],
        elapsed: Callable[[], float],
        stop_event: threading.Event | None = None,
    ):
        self.stop_event = stop_event or threading.Event()
        self.lua = lupa.LuaRuntime(
            unpack_returned_tuples=True, register_eval=False, register_builtins=False,
            attribute_filter=_no_attributes,
        )
        self._make_device = self.lua.eval(_BOOTSTRAP)(on_print, self.stop_event.is_set)
        g = self.lua.globals()
        g.sleep = self._sleep
        g.time = elapsed
        g.log = lambda table: log_row(self._to_python(table, as_dict=True))

    def _to_python(self, value: Any, as_dict: bool = False) -> Any:
        if lupa.lua_type(value) != "table":
            return value
        if as_dict:
            return {str(k): self._to_python(v) for k, v in value.items()}
        return [self._to_python(v) for v in value.values()]

    def _to_lua(self, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return self.lua.table_from([self._to_lua(v) for v in value])
        if isinstance(value, dict):
            return self.lua.table_from({k: self._to_lua(v) for k, v in value.items()})
        return value

    def _sleep(self, seconds: float = 0) -> None:
        deadline = time.monotonic() + float(seconds)
        while not self.stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 0.05))

    def add_device(self, name: str, device: Device) -> None:
        def call(command, *args):
            try:
                result = device.call(command, *[self._to_python(a) for a in args])
            except Exception as exc:
                return False, f"{name}:{command}: {exc}"
            return True, self._to_lua(result)

        names = self.lua.table_from(sorted(type(device).commands()))
        self.lua.globals()[name] = self._make_device(name, names, call)

    def run(self, code: str, chunk_name: str = "script") -> None:
        loaded = self.lua.globals().load(code, "@" + chunk_name)
        if isinstance(loaded, tuple):  # (nil, message): syntax error
            raise ScriptError(str(loaded[1]))
        try:
            loaded()
        except lupa.LuaError as exc:
            if _STOP in str(exc):
                raise ScriptStopped() from None
            raise ScriptError(str(exc)) from None
        except Exception as exc:  # raised inside sleep(), log() or the attribute filter
            raise ScriptError(f"{chunk_name}: {exc}") from exc
        if self.stop_event.is_set():
            raise ScriptStopped()


@dataclass
class RunResult:
    status: str  # "ok", "error" or "stopped"
    error: str | None
    path: Path
    rows: list[dict[str, Any]]


def open_device(registry: PluginRegistry, config: DeviceConfig) -> Device:
    spec = registry.device(config.device)
    transport = open_transport(config.resource, spec.load_simulator())
    try:
        device = spec.load_driver()(transport, config.settings)
        device.connect()
    except Exception:
        transport.close()
        raise
    return device


def run_project(
    project_dir: str | Path,
    *,
    on_print: Callable[[str], None] = print,
    stop_event: threading.Event | None = None,
) -> RunResult:
    project = Project.load(project_dir)
    if project.mode != "lua":
        raise ProjectError("visual projects cannot be run yet (the node editor is not built)")
    script = project.path / project.script
    if not script.is_file():
        raise ProjectError(f"script not found: {script}")
    registry = PluginRegistry.default(project.path)

    run = RunLog(project.path / "runs")
    shutil.copy2(script, run.path / script.name)

    def emit(text: str) -> None:
        run.console(text)
        on_print(text)

    devices: dict[str, Device] = {}
    status, error = "ok", None
    try:
        for name, config in project.devices.items():
            try:
                devices[name] = open_device(registry, config)
            except Exception as exc:
                raise ScriptError(f"device '{name}' ({config.resource}): {exc}") from exc
        engine = LuaEngine(
            on_print=emit, log_row=run.log, elapsed=run.elapsed, stop_event=stop_event
        )
        for name, device in devices.items():
            engine.add_device(name, device)
        engine.run(script.read_text(encoding="utf-8"), script.name)
    except (ScriptStopped, KeyboardInterrupt):
        status = "stopped"
        emit("Stopped.")
    except ScriptError as exc:
        status, error = "error", str(exc)
        emit(f"Error: {error}")
    finally:
        for device in devices.values():
            try:
                device.close()
            finally:
                device.transport.close()
        run.finish(status, error, {
            "patchbay": __version__,
            "project": project.name,
            "script": script.name,
            "devices": {n: {"device": c.device, "resource": c.resource}
                        for n, c in project.devices.items()},
        })
    return RunResult(status, error, run.path, run.rows)
