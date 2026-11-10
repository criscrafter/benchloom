import csv
import json
import threading

import pytest

from patchbay.engine import run_project
from patchbay.plugin import PluginRegistry
from patchbay.project import create_project
from patchbay.transports import TransportError, parse_resource, prologix_escape


def make(tmp_path, lua=None):
    project = create_project(tmp_path / "proj")
    if lua is not None:
        (project.path / "main.lua").write_text(lua, encoding="utf-8")
    return project.path


def run(path, **kwargs):
    lines = []
    return run_project(path, on_print=lines.append, **kwargs), lines


def test_builtin_plugins_are_discovered():
    devices = PluginRegistry.default().devices
    assert {"demo.psu", "generic.scpi", "generic.serial"} <= set(devices)
    commands = devices["demo.psu"].load_driver().commands()
    volts = commands["set_voltage"].params[0]
    assert (volts.type, volts.unit, volts.min, volts.max) == ("number", "V", 0, 20)
    assert commands["measure_voltage"].unit == "V"
    assert "idn" in commands  # inherited from ScpiDevice


def test_example_project_runs_and_logs(tmp_path):
    result, lines = run(make(tmp_path))
    assert result.status == "ok", result.error
    assert lines[0].startswith("Connected to Patchbay,Demo PSU")
    with open(result.path / "data.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 10
    assert set(rows[0]) == {"time_s", "set_V", "V", "I"}
    assert float(rows[4]["V"]) == pytest.approx(5.0, abs=0.05)
    assert float(rows[4]["I"]) == pytest.approx(0.5, abs=0.01)
    meta = json.loads((result.path / "run.json").read_text())
    assert meta["status"] == "ok" and meta["rows"] == 10
    assert (result.path / "main.lua").is_file()


def test_out_of_range_argument_reports_script_line(tmp_path):
    result, _ = run(make(tmp_path, "local x = 1\npsu:set_voltage(50)\n"))
    assert result.status == "error"
    assert "main.lua:2:" in result.error
    assert "above the maximum of 20 V" in result.error


def test_dot_call_gets_a_helpful_error(tmp_path):
    result, _ = run(make(tmp_path, "psu.set_voltage(5)\n"))
    assert "use a colon" in result.error and "main.lua:1:" in result.error


def test_syntax_error(tmp_path):
    result, _ = run(make(tmp_path, "for for\n"))
    assert result.status == "error" and "main.lua:1:" in result.error


def test_new_columns_appear_mid_run(tmp_path):
    result, _ = run(make(tmp_path, "log{a=1}\nlog{a=2, b='x'}\n"))
    with open(result.path / "data.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["a"] for r in rows] == ["1", "2"]
    assert [r["b"] for r in rows] == ["", "x"]


def test_stop_interrupts_a_busy_loop(tmp_path):
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    result, lines = run(make(tmp_path, "while true do end\n"), stop_event=stop)
    assert result.status == "stopped"



def test_stop_ends_a_script_that_sleeps_and_calls_devices(tmp_path):
    # A normal test loop runs far fewer than 10000 Lua instructions between stop
    # checks of the count hook; Stop must still end it, and no device may get
    # another command afterwards (found with a real KP184 load).
    stop = threading.Event()
    lua = """
for step = 1, 50 do
    psu:set_voltage(step / 10)
    sleep(0.05)
    log { step = step, V = psu:measure_voltage() }
end
print("loop finished")
"""
    rows = []

    def on_row(row):
        rows.append(row)
        if len(rows) == 3:
            stop.set()

    result, lines = run(make(tmp_path, lua), stop_event=stop, on_row=on_row)
    assert result.status == "stopped"
    assert len(rows) == 3 and "loop finished" not in lines

def test_scripts_cannot_reach_python(tmp_path):
    result, lines = run(make(tmp_path, "print(python)\n"))
    assert lines == ["nil"]
    result, _ = run(make(tmp_path / "b", "print(sleep.__self__)\n"))
    assert result.status == "error"


def test_resource_parsing():
    assert parse_resource("serial://COM3?baud=9600") == ("serial", "COM3", {"baud": "9600"})
    assert parse_resource("serial:///dev/ttyUSB0")[1] == "/dev/ttyUSB0"
    assert parse_resource("visa://GPIB0::5::INSTR")[1] == "GPIB0::5::INSTR"
    with pytest.raises(TransportError):
        parse_resource("COM3")


def test_prologix_escaping():
    assert prologix_escape(b"A+B\n") == b"A\x1b+B\x1b\n"
