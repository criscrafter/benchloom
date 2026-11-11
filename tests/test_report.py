# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Pass/fail checks, saved waveforms, the run report and the SVG charts."""
import importlib.util
import json
import math

import pytest

from benchloom.datalog import evaluate
from benchloom.engine import run_project
from benchloom.panel import format_value
from benchloom.project import create_project
from benchloom.svgplot import Series, chart, describe, nice_ticks, si, waveform_svg


def run_lua(tmp_path, code, **kwargs):
    project = create_project(tmp_path / "proj")
    (project.path / "main.lua").write_text(code, encoding="utf-8")
    printed = []
    result = run_project(project.path, on_print=printed.append, **kwargs)
    return result, printed


def test_evaluate():
    assert evaluate(1.0, 0.5, 1.5) and evaluate(0.5, 0.5, 1.5) and evaluate(1.5, 0.5, 1.5)
    assert not evaluate(1.6, 0.5, 1.5) and not evaluate(0.4, 0.5, None)
    assert evaluate(9e9, 0, None) and evaluate(-9e9, None, 0)
    assert evaluate(True, None, None) and not evaluate(False, None, None)
    assert not evaluate(None, None, None) and not evaluate(math.nan, 0, 1)
    assert not evaluate("1.0", 0, 2) and not evaluate(True, 0, 2)


def test_checks_decide_the_verdict(tmp_path):
    seen = []
    result, printed = run_lua(tmp_path, '''
        assert(check("in range", 1.02, 0.95, 1.05, "V") == true)
        assert(check("too high", 3, nil, 2.5, "A") == false)
        check("flag", true)
    ''', on_check=seen.append)
    assert result.status == "ok" and result.verdict == "fail"
    assert printed[:3] == ["PASS  in range: 1.02 V (limits: 0.95 to 1.05 V)",
                           "FAIL  too high: 3 A (limits: at most 2.5 A)",
                           "PASS  flag: true (limits: true)"]
    assert [c["passed"] for c in seen] == [True, False, True]
    csv = (result.path / "checks.csv").read_text().splitlines()
    assert csv[0] == "time_s,name,value,low,high,unit,result"
    assert csv[2].endswith(",too high,3,,2.5,A,fail")
    meta = json.loads((result.path / "run.json").read_text())
    assert meta["result"] == "fail" and meta["checks"] == {"passed": 2, "failed": 1}


def test_all_checks_passing_and_no_checks(tmp_path):
    result, _ = run_lua(tmp_path, 'check("ok", 5, 0, 10)')
    assert result.verdict == "pass"
    result, _ = run_lua(tmp_path / "b", 'log { a = 1 }')
    assert result.verdict == "none"
    assert json.loads((result.path / "run.json").read_text())["result"] == "none"


def test_check_errors_point_at_the_line(tmp_path):
    result, _ = run_lua(tmp_path, '\ncheck("x", 1, "low")')
    assert result.status == "error" and "main.lua:2:" in result.error
    assert "low must be a number or nil" in result.error
    result, _ = run_lua(tmp_path / "b", 'check(nil, 1)')
    assert "name must be a text" in result.error


def test_save_waveform_writes_csv_svg_and_png(tmp_path):
    seen = []
    result, _ = run_lua(tmp_path, '''
        local t, a, b = {}, {}, {}
        for i = 1, 400 do
            t[i] = (i - 200) * 1e-5
            a[i] = 2 * math.sin(2 * math.pi * 1000 * t[i])
            b[i] = 0.5 + math.cos(2 * math.pi * 1000 * t[i])
        end
        save_waveform("two traces", { CH2 = { time = t, volts = b }, CH1 = { time = t, volts = a } })
        save_waveform("one/trace?", { time = t, volts = a })
        psu:set_output(true)  -- also a device result works as traces once it is a table
    ''', on_waveform=lambda name, traces: seen.append((name, list(traces))))
    assert result.status == "ok", result.error
    assert seen == [("two traces", ["CH1", "CH2"]), ("one/trace?", ["V"])]
    folder = result.path / "waveforms"
    rows = (folder / "001_two_traces.csv").read_text().splitlines()
    assert rows[0] == "time_s,CH1_V,CH2_V" and len(rows) == 401
    assert (folder / "002_one_trace.csv").read_text().startswith("time_s,V_V")
    svg = (folder / "001_two_traces.svg").read_text(encoding="utf-8")
    assert "<svg" in svg and "CH1: 4 Vpp" in svg and "1 kHz" in svg
    if importlib.util.find_spec("PySide6"):
        assert (folder / "001_two_traces.png").stat().st_size > 5000
    meta = json.loads((result.path / "run.json").read_text())
    assert meta["waveforms"] == ["waveforms/001_two_traces", "waveforms/002_one_trace"]


def test_save_waveform_rejects_other_tables(tmp_path):
    result, _ = run_lua(tmp_path, 'save_waveform("x", { 1, 2, 3 })')
    assert result.status == "error" and "traces must be" in result.error
    result, _ = run_lua(tmp_path / "b", 'save_waveform("x", { time = {1, 2}, volts = {1} })')
    assert "traces must be" in result.error


def test_report_shows_verdict_checks_data_and_waveforms(tmp_path):
    project = create_project(tmp_path / "proj")
    project.plot = {"x": "set_V", "right": ["I"], "hide": ["time_s"]}
    project.save()
    (project.path / "main.lua").write_text('''
        for v = 1, 3 do log { set_V = v, V = v * 1.01, I = v / 10 } end
        check("V at 3", 3.03, 2.9, 3.1, "V")
        save_waveform("sine", { time = {0, 1e-3, 2e-3, 3e-3}, volts = {0, 1, 0, -1} })
        print("done <ok>")
    ''', encoding="utf-8")
    result = run_project(project.path, on_print=lambda _: None)
    page = (result.path / "report.html").read_text(encoding="utf-8")
    assert "<div class='verdict pass'>PASS</div>" in page
    assert "1 of 1 checks passed, 3 rows logged, 1 waveforms saved" in page
    assert "<td>V at 3</td>" in page and "2.9 to 3.1 V" in page
    assert page.count("<svg") == 2  # the data chart and the waveform
    assert "I (right)" in page and ">time_s<" not in page.split("<h2>Logged rows")[0]
    assert "done &lt;ok&gt;" in page  # the console, escaped
    assert "demo.psu" in page


def test_report_of_a_failed_run(tmp_path):
    result, _ = run_lua(tmp_path, 'error("boom")')
    page = (result.path / "report.html").read_text(encoding="utf-8")
    assert "<div class='verdict error'>ERROR</div>" in page and "boom" in page


def test_si_ticks_and_describe():
    assert si(1.5e-6, "s") == "1.5 µs" and si(2200, "Hz") == "2.2 kHz" and si(0, "V") == "0 V"
    assert nice_ticks(0, 9.3) == [0, 2, 4, 6, 8, 10]
    assert nice_ticks(-1.02, 0.97) == pytest.approx([-1.5, -1, -0.5, 0, 0.5, 1])
    times = [i * 1e-5 for i in range(1000)]
    text = describe(times, [3 * math.sin(2 * math.pi * 500 * t) + 1 for t in times])
    assert text.startswith("6 Vpp, mean 1 V") and "500 Hz" in text


def test_chart_skips_values_that_are_not_finite():
    svg = chart([Series("gain", [1, 10, 100, 1000], [0, -3, -math.inf, -40]),
                 Series("phase", [1, 10, 100, 1000], [0, -45, -84, -89], right=True)],
                log_x=True, title="Bode")
    assert svg.count("<polyline") == 2 and "phase (right)" in svg
    assert ">1 k<" in svg and ">100<" in svg  # decades on the log axis
    assert "No data" in chart([Series("x", [], [])])
    svg = waveform_svg({"CH1": ([0, 1e-6, 2e-6], [0, 1, 0])}, "fast")
    assert "Time (ns)" in svg or "Time (µs)" in svg


def test_format_value():
    assert [format_value(v) for v in (50.0, 50000.0, -89.2581, 140.5, 0.012345, 0.0)] == \
        ["50", "50000", "-89.26", "140.5", "0.01235", "0"]
    assert format_value(1.5e-6) == "1.5e-06" and format_value(12345678.0) == "1.235e+07"
    assert format_value(-0.0004, 2) == "0.00" and format_value(3, 2, ",") == "3,00"
    assert format_value(-89.2581, point=",") == "-89,26"


def test_the_packaged_app_starts_its_own_png_helper(tmp_path, monkeypatch):
    """A packaged app has no `python -m`: it calls its own program with an argument."""
    import subprocess
    import sys

    from benchloom import datalog

    calls = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "C:/Benchloom/Benchloom.exe")
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args))
    run = datalog.RunLog(tmp_path / "runs")
    run.save_waveform("w", {"CH1": ([0, 1], [0, 1])})
    run._render_pngs()
    assert calls[0][:2] == ["C:/Benchloom/Benchloom.exe", datalog.SVGPNG_ARG]
    assert calls[0][2].endswith("001_w.svg")
