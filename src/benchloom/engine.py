# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Runs a project: opens its devices and executes the Lua test script.

What a script sees:

    <name>:<command>(...)   every device from benchloom.toml, by its entry name
    sleep(seconds)          wait
    log { key = value }     append a row to the run's data.csv
    time()                  seconds since the run started
    print(...)              write to the console (and the run's console.log)
    panel.<name>            read a front panel control, or set an indicator

Scripts run in a sandbox: Lua's math, string, table, utf8 and coroutine
libraries, os.time/clock/date/difftime, and load for text only. There is no io,
os.execute and the like, require, dofile, loadfile, package, debug or
string.dump, and a script may use at most MAX_SCRIPT_MEMORY_MB of memory.
    check(name, value, low, high, unit)
                            a pass/fail check: value within low..high (either may
                            be nil), or true when no limits are given; returns
                            whether it passed. The run's verdict is PASS only if
                            every check passed.
    save_waveform(name, traces)
                            save scope traces as CSV and images in the run's
                            waveforms/ folder: one {time = {...}, volts = {...}}
                            (as read_waveform returns it), or several by name,
                            for example {CH1 = w1, CH2 = w2}
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

try:
    import lupa.lua54 as lupa
except ImportError:  # pragma: no cover - platforms without the 5.4 build
    import lupa

from . import __version__
from .config import AppConfig
from .datalog import Check, RunLog, evaluate
from .graph import NODE_MARKER, Catalog, GraphError, compile_graph
from .panel import Panel, PanelError, PanelState
from .plugin import PROJECT_PLUGIN_HINT, Device, PluginRegistry
from .project import DeviceConfig, Project, ProjectError
from .transports import Transport, TransportError, open_transport, parse_resource

_STOP = "__benchloom_stop__"
MAX_SCRIPT_MEMORY_MB = 1024  # a runaway script fails instead of filling the computer's RAM
GRAPH_CHUNK = "graph.lua"  # name of the Lua generated from a visual test
# How long a device command in progress may take to finish before the devices are shut
# down around it, when the script does not stop (see run_project's stop_timeout).
STOP_COMMAND_WAIT_S = 8.0

_BOOTSTRAP = """
function(emit, should_stop, panel_get, panel_set, py_check, py_save_waveform)
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

    -- Functions that report errors as (false, message), raised at the caller's line.
    local function reporting(fn)
        return function(...)
            check_stop()
            local ok, result = fn(...)
            if not ok then error(result, 2) end
            check_stop()
            return result
        end
    end
    check = reporting(py_check)
    save_waveform = reporting(py_save_waveform)

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

    -- The sandbox: a script from someone else must not reach files, programs or
    -- other code on this computer. What a test needs stays: math, string, table,
    -- utf8, coroutine, time and dates, Benchloom's own functions and the devices.
    local raw_load = load
    load = function(chunk, chunkname, mode, ...)
        -- text only: crafted bytecode can crash Lua or escape it. An explicit env
        -- argument (even nil) is passed on only when the script gave one.
        if select("#", ...) > 0 then return raw_load(chunk, chunkname, "t", ...) end
        return raw_load(chunk, chunkname, "t")
    end
    os = { time = os.time, clock = os.clock, date = os.date, difftime = os.difftime }
    io, require, dofile, loadfile, package, debug, collectgarbage = nil
    string.dump = nil

    return make_device, guard
end
""" % _STOP


def _no_attributes(obj, name, is_setting):
    """Scripts may call the Python functions they are given, not look inside them."""
    raise AttributeError("not accessible from scripts")


def _lua_text(value: Any) -> str:
    """A value as a script writer would write it: true, nil, 3.14159."""
    if value is None or isinstance(value, bool):
        return {None: "nil", True: "true", False: "false"}[value]
    return f"{value:.6g}" if isinstance(value, float) else str(value)


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
        on_check: Callable[..., Check] | None = None,
        on_waveform: Callable[[str, dict], Any] | None = None,
        on_node: Callable[[str], None] | None = None,
    ):
        self.stop_event = stop_event or threading.Event()
        self.panel = panel or PanelState(Panel())
        self._emit = on_print
        self._on_check = on_check
        self._on_waveform = on_waveform
        self.lua = lupa.LuaRuntime(
            unpack_returned_tuples=True, register_eval=False, register_builtins=False,
            attribute_filter=_no_attributes, max_memory=MAX_SCRIPT_MEMORY_MB * 1024 * 1024,
        )
        self._make_device, guard = self.lua.eval(_BOOTSTRAP)(
            on_print, self.stop_event.is_set, self._panel_get, self._panel_set,
            self._check, self._save_waveform)
        g = self.lua.globals()
        self._load = g.load  # kept, so a device or script redefining `load` cannot break runs
        # Held while a device command runs, so whoever shuts the devices down for a
        # script that does not stop can wait for the command in progress to end.
        self.device_lock = threading.Lock()
        g.sleep = guard(self._sleep)
        g.time = elapsed
        g.log = guard(lambda table: log_row(self._to_python(table, as_dict=True)))
        g[NODE_MARKER] = on_node or (lambda node_id: None)  # see compile_graph(trace=True)

    def _to_python(self, value: Any, as_dict: bool = False) -> Any:
        if lupa.lua_type(value) != "table":
            return value
        if as_dict:
            return {str(k): self._to_python(v) for k, v in value.items()}
        return [self._to_python(v) for v in value.values()]

    def _plain(self, value: Any) -> Any:
        """A Lua value as Python: tables numbered 1..n become lists, others dicts."""
        if lupa.lua_type(value) != "table":
            return value
        items = list(value.items())
        keys = [k for k, _ in items]
        if all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
            return [self._plain(v) for _, v in sorted(items, key=lambda kv: kv[0])]
        return {str(k): self._plain(v) for k, v in items}

    def _check(self, name=None, value=None, low=None, high=None, unit=""):
        if not isinstance(name, str) or not name:
            return False, "check(name, value, low, high, unit): name must be a text"
        for which, limit in (("low", low), ("high", high)):
            if limit is not None and (isinstance(limit, bool)
                                      or not isinstance(limit, (int, float))):
                return False, f"check '{name}': {which} must be a number or nil"
        unit = "" if unit is None else str(unit)
        value = self._plain(value)
        if self._on_check is not None:
            result = self._on_check(name, value, low, high, unit)
        else:
            result = Check(name, value, low, high, unit, evaluate(value, low, high), 0.0)
        shown = _lua_text(value)
        unit_text = f" {unit}" if unit else ""
        limits = result.limits() + (unit_text if low is not None or high is not None else "")
        self._emit(f"{'PASS' if result.passed else 'FAIL'}  {name}: {shown}{unit_text} "
                   f"(limits: {limits})")
        return True, result.passed

    def _save_waveform(self, name=None, traces=None):
        if not isinstance(name, str) or not name:
            return False, "save_waveform(name, traces): name must be a text"
        data = self._plain(traces)
        if isinstance(data, dict) and "time" in data and "volts" in data:
            data = {"V": data}
        if not isinstance(data, dict) or not data or not all(
                isinstance(t, dict) and isinstance(t.get("time"), list)
                and isinstance(t.get("volts"), list) and len(t["time"]) == len(t["volts"])
                for t in data.values()):
            return False, ("save_waveform: traces must be {time = {...}, volts = {...}} as "
                           "read_waveform returns it, or several of them by name")
        # Lua tables have no order: sort by name (CH1 before CH2, input before output)
        traces = {k: (data[k]["time"], data[k]["volts"]) for k in sorted(data)}
        if self._on_waveform is not None:
            self._on_waveform(name, traces)
        return True, None

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
                with self.device_lock:
                    if self.stop_event.is_set():  # stopped while waiting for the lock
                        return False, _STOP
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
        except lupa.LuaMemoryError:
            raise ScriptError(f"{chunk_name}: the script used more than "
                              f"{MAX_SCRIPT_MEMORY_MB} MB of memory") from None
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
    verdict: str = "none"  # "pass", "fail" or "none" (no checks)
    checks: list[dict[str, Any]] | None = None


SHARED_SCHEMES = ("serial", "tcp")  # buses: several devices on one port (RS-485, gateways)


class SharedPorts:
    """The ports of a run, so devices on one bus share one open connection.

    Several devices may name the same serial port (an RS-485 bus with one address
    per device) or network address (a serial-to-Ethernet gateway). They then share
    one transport; their settings must match.
    """

    def __init__(self):
        self._open: dict[tuple[str, str], tuple[dict, Transport, str]] = {}

    def get(self, resource: str) -> Transport | None:
        scheme, target, options = parse_resource(resource)
        if scheme not in SHARED_SCHEMES or (scheme, target) not in self._open:
            return None
        first_options, transport, first_user = self._open[(scheme, target)]
        if options != first_options:
            raise TransportError(
                f"{target} is already open for '{first_user}' with other settings "
                f"({resource} here); devices sharing a port need the same settings")
        return transport

    def add(self, resource: str, transport: Transport, user: str) -> None:
        scheme, target, options = parse_resource(resource)
        if scheme in SHARED_SCHEMES:
            self._open.setdefault((scheme, target), (options, transport, user))


def open_device(registry: PluginRegistry, config: DeviceConfig,
                ports: SharedPorts | None = None) -> Device:
    spec = registry.device(config.device)
    transport = ports.get(config.resource) if ports else None
    new = transport is None
    if new:
        transport = open_transport(config.resource, spec.load_simulator())
    try:
        device = spec.load_driver()(transport, config.settings)
        device.connect()
    except Exception:
        if new:
            transport.close()
        raise
    if new and ports:
        ports.add(config.resource, transport, config.name)
    return device


def run_project(
    project_dir: str | Path,
    *,
    on_print: Callable[[str], None] = print,
    on_row: Callable[[dict[str, Any]], None] | None = None,
    stop_event: threading.Event | None = None,
    panel_values: dict[str, Any] | None = None,
    on_panel: Callable[[str, Any], None] | None = None,
    on_check: Callable[[dict[str, Any]], None] | None = None,
    on_waveform: Callable[[str, dict], None] | None = None,
    trust_plugins: bool = False,
    stop_timeout: float | None = None,
    on_shutdown: Callable[[], None] | None = None,
    on_abandoned: Callable[[RunResult], None] | None = None,
    on_node: Callable[[str], None] | None = None,
) -> RunResult:
    """Run a project's test.

    ``panel_values`` holds the current values of the front panel controls; whoever
    shows the panel may keep updating that dict while the test runs. Without it the
    controls have their saved values. ``on_panel`` receives what the test sets.

    ``trust_plugins`` allows the project's own plugins folder to load, and remembers
    that for the project.

    ``stop_timeout``: when the script has not ended this many seconds after
    ``stop_event`` was set (a loop that never checks, a hung driver), the devices are
    shut down anyway, from another thread, so that outputs do not stay on; the run is
    finished as "stopped" and ``on_abandoned`` gets its result. The script itself may
    still be running then: the caller has to end the process. ``on_shutdown`` is
    called when the devices start to be shut down, on either way.

    ``on_node`` gets the id of each node of a visual test as it starts to run. It is
    called very often; keep it short.
    """
    project = Project.load(project_dir)
    script = project.path / project.script
    if not script.is_file():
        raise ProjectError(f"script not found: {script}")
    if trust_plugins:
        config = AppConfig.load()
        if not config.trusts(project.path):
            config.set_trusted(project.path)
            config.save()
    registry = PluginRegistry.default(project.path)

    run = RunLog(project.path / "runs")
    shutil.copy2(script, run.path / script.name)
    closing = threading.Lock()  # shutting the devices down and finishing the run, once
    closed = False

    def emit(text: str) -> None:
        try:
            run.console(text)
        except ValueError:  # the run was finished without the script (see stop_timeout)
            return
        on_print(text)

    def log_row(values: dict[str, Any]) -> None:
        row = run.log(values)
        if on_row is not None:
            on_row(row)

    def check(name, value, low, high, unit) -> Check:
        result = run.check(name, value, low, high, unit)
        if on_check is not None:
            on_check(asdict(result))
        return result

    def save_waveform(name: str, traces: dict) -> None:
        waveform = run.save_waveform(name, traces)
        if on_waveform is not None:
            on_waveform(waveform.name, waveform.traces)

    devices: dict[str, Device] = {}
    ports = SharedPorts()
    status, error = "ok", None

    def shut_down(status: str, error: str | None) -> bool:
        """Switch the devices off and write the run's files. False: already done."""
        nonlocal closed
        with closing:
            if closed:
                return False
            closed = True
            if on_shutdown is not None:
                on_shutdown()
            for name, device in list(devices.items()):
                try:
                    device.close()  # sources and loads switch their output off here
                except Exception as exc:
                    emit(f"Warning: '{name}' could not be shut down cleanly: {exc}")
            for transport in {id(d.transport): d.transport for d in devices.values()}.values():
                try:
                    transport.close()  # once, also when devices share it
                except Exception:
                    pass
            run.finish(status, error, {
                "benchloom": __version__,
                "project": project.name,
                "script": script.name,
                "devices": {n: {"device": c.device, "resource": c.resource}
                            for n, c in project.devices.items()},
            }, project.plot)
            return True

    def result() -> RunResult:
        meta = json.loads((run.path / "run.json").read_text(encoding="utf-8"))
        return RunResult(meta["status"], meta["error"], run.path, run.rows, run.verdict(),
                         run.check_records())

    script_ended = threading.Event()

    def watch_stop(engine: LuaEngine) -> None:
        """Shut the devices down for a script that does not stop when told to."""
        while not stop_event.wait(0.2):
            if script_ended.is_set():
                return
        if script_ended.wait(stop_timeout):
            return
        emit(f"The test did not stop within {stop_timeout:g} s; shutting the devices down.")
        # A command in progress may finish first; a hung one must not keep outputs on.
        waited = engine.device_lock.acquire(timeout=STOP_COMMAND_WAIT_S)
        try:
            if script_ended.is_set() or not shut_down("stopped", None):
                return
        finally:
            if waited:
                engine.device_lock.release()
        if on_abandoned is not None:
            on_abandoned(result())

    try:
        for blocked in registry.blocked:
            emit(f"Note: this project's own plugin '{blocked.name}' was not loaded. "
                 f"{PROJECT_PLUGIN_HINT}")
        for name, config in project.devices.items():
            try:
                devices[name] = open_device(registry, config, ports)
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
            panel=panel, on_check=check, on_waveform=save_waveform, on_node=on_node,
        )
        for name, device in devices.items():
            engine.add_device(name, device)
        code, chunk_name = script.read_text(encoding="utf-8"), script.name
        if project.mode == "visual":
            # visual tests are compiled to Lua; keep the generated code with the run
            try:
                code, _ = compile_graph(json.loads(code), Catalog(project, registry), trace=True)
            except (GraphError, ValueError, KeyError, TypeError) as exc:
                raise ScriptError(f"graph: {exc}") from exc
            chunk_name = GRAPH_CHUNK
            (run.path / GRAPH_CHUNK).write_text(code, encoding="utf-8")
        if stop_event is not None and stop_timeout is not None:
            threading.Thread(target=watch_stop, args=(engine,), daemon=True).start()
        engine.run(code, chunk_name)
    except (ScriptStopped, KeyboardInterrupt):
        status = "stopped"
        emit("Stopped.")
    except ScriptError as exc:
        status, error = "error", str(exc)
        emit(f"Error: {error}")
    finally:
        script_ended.set()
        shut_down(status, error)  # waits, if the other thread is already doing it
    return result()
