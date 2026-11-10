"""Runs a project: opens its devices and executes the Lua test script.

What a script sees:

    <name>:<command>(...)   every device from patchbay.toml, by its entry name
    sleep(seconds)          wait
    log { key = value }     append a row to the run's data.csv
    time()                  seconds since the run started
    print(...)              write to the console (and the run's console.log)
    panel.<name>            read a front panel control, or set an indicator
"""

from __future__ import annotations

import json
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
from .graph import Catalog, GraphError, compile_graph
from .panel import Panel, PanelError, PanelState
from .plugin import Device, PluginRegistry
from .project import DeviceConfig, Project, ProjectError
from .transports import open_transport

_STOP = "__patchbay_stop__"
GRAPH_CHUNK = "graph.lua"  # name of the Lua generated from a visual test

_BOOTSTRAP = """
function(emit, should_stop, panel_get, panel_set)
    python = nil  -- scripts talk to Python only through the functions below

    panel = setmetatable({}, {
        __index = function(_, name)
            local ok, result = panel_get(name)
            if not ok then error(result, 2) end
            return result
        end,
        __newindex = function(_, name, value)
            local ok, result = panel_set(name, value)
            if not ok then error(result, 2) end
        end,
    })

    print = function(...)
        local parts = {}
        for i = 1, select("#", ...) do
            parts[i] = tostring((select(i, ...)))
        end
        emit(table.concat(parts, "\\t"))
    end

    local function check_stop()
        if should_stop() then error("%s", 0) end
    end
    -- The count hook catches busy loops. Most scripts spend their time in sleep()
    -- and device calls instead, so those check too (see guard and the device
    -- wrapper below): after Stop, no device gets another command.
    debug.sethook(check_stop, "", 10000)

    local function guard(fn)
        return function(...)
            check_stop()
            local result = fn(...)
            check_stop()
            return result
        end
    end

    -- Builds the script-side object for one device.
    local function make_device(device_name, names, call)
        local device = {}
        for _, name in ipairs(names) do
            device[name] = function(self, ...)
                if self ~= device then
                    error("use a colon to call device commands: " .. device_name
                          .. ":" .. name .. "(...)", 2)
                end
                check_stop()
                local ok, result = call(name, ...)
                check_stop()
                if not ok then error(result, 2) end
                return result
            end
        end
        return device
    end

    return make_device, guard
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
        panel: PanelState | None = None,
    ):
        self.stop_event = stop_event or threading.Event()
        self.panel = panel or PanelState(Panel())
        self.lua = lupa.LuaRuntime(
            unpack_returned_tuples=True, register_eval=False, register_builtins=False,
            attribute_filter=_no_attributes,
        )
        self._make_device, guard = self.lua.eval(_BOOTSTRAP)(
            on_print, self.stop_event.is_set, self._panel_get, self._panel_set)
        g = self.lua.globals()
        self._load = g.load  # kept, so a device or script redefining `load` cannot break runs
        g.sleep = guard(self._sleep)
        g.time = elapsed
        g.log = guard(lambda table: log_row(self._to_python(table, as_dict=True)))

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

    def _panel_get(self, name):
        try:
            return True, self.panel.get(name)
        except PanelError as exc:
            return False, str(exc)

    def _panel_set(self, name, value):
        try:
            self.panel.set(name, self._to_python(value))
            return True, None
        except PanelError as exc:
            return False, str(exc)

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
        loaded = self._load(code, "@" + chunk_name)
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
    on_row: Callable[[dict[str, Any]], None] | None = None,
    stop_event: threading.Event | None = None,
    panel_values: dict[str, Any] | None = None,
    on_panel: Callable[[str, Any], None] | None = None,
) -> RunResult:
    """Run a project's test.

    ``panel_values`` holds the current values of the front panel controls; whoever
    shows the panel may keep updating that dict while the test runs. Without it the
    controls have their saved values. ``on_panel`` receives what the test sets.
    """
    project = Project.load(project_dir)
    script = project.path / project.script
    if not script.is_file():
        raise ProjectError(f"script not found: {script}")
    registry = PluginRegistry.default(project.path)

    run = RunLog(project.path / "runs")
    shutil.copy2(script, run.path / script.name)

    def emit(text: str) -> None:
        run.console(text)
        on_print(text)

    def log_row(values: dict[str, Any]) -> None:
        row = run.log(values)
        if on_row is not None:
            on_row(row)

    devices: dict[str, Device] = {}
    status, error = "ok", None
    try:
        for name, config in project.devices.items():
            try:
                devices[name] = open_device(registry, config)
            except Exception as exc:
                raise ScriptError(f"device '{name}' ({config.resource}): {exc}") from exc
        try:
            panel = PanelState(Panel.load(project.path), on_change=on_panel)
        except PanelError as exc:
            raise ScriptError(str(exc)) from exc
        if panel_values is not None:  # share the caller's dict so live changes arrive
            for name, value in panel.values.items():
                panel_values.setdefault(name, value)
            panel.values = panel_values
        engine = LuaEngine(
            on_print=emit, log_row=log_row, elapsed=run.elapsed, stop_event=stop_event,
            panel=panel,
        )
        for name, device in devices.items():
            engine.add_device(name, device)
        code, chunk_name = script.read_text(encoding="utf-8"), script.name
        if project.mode == "visual":
            # visual tests are compiled to Lua; keep the generated code with the run
            try:
                code, _ = compile_graph(json.loads(code), Catalog(project, registry))
            except (GraphError, ValueError, KeyError, TypeError) as exc:
                raise ScriptError(f"graph: {exc}") from exc
            chunk_name = GRAPH_CHUNK
            (run.path / GRAPH_CHUNK).write_text(code, encoding="utf-8")
        engine.run(code, chunk_name)
    except (ScriptStopped, KeyboardInterrupt):
        status = "stopped"
        emit("Stopped.")
    except ScriptError as exc:
        status, error = "error", str(exc)
        emit(f"Error: {error}")
    finally:
        for name, device in devices.items():
            try:
                device.close()  # sources and loads switch their output off here
            except Exception as exc:
                emit(f"Warning: '{name}' could not be shut down cleanly: {exc}")
            try:
                device.transport.close()
            except Exception:
                pass
        run.finish(status, error, {
            "patchbay": __version__,
            "project": project.name,
            "script": script.name,
            "devices": {n: {"device": c.device, "resource": c.resource}
                        for n, c in project.devices.items()},
        })
    return RunResult(status, error, run.path, run.rows)
