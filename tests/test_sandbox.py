# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Lua scripts run in a sandbox: no files, programs or other code on the computer."""
import pytest

from benchloom import engine
from benchloom.engine import run_project
from benchloom.project import create_project


def run_lua(tmp_path, code):
    project = create_project(tmp_path / "proj")
    (project.path / "main.lua").write_text(code, encoding="utf-8")
    printed = []
    result = run_project(project.path, on_print=printed.append)
    return result, printed


@pytest.mark.parametrize("name", [
    "io", "require", "dofile", "loadfile", "package", "debug", "collectgarbage",
    "os.execute", "os.remove", "os.rename", "os.exit", "os.getenv", "os.tmpname",
    "string.dump", "python",
])
def test_dangerous_functions_are_gone(tmp_path, name):
    result, printed = run_lua(tmp_path, f"print(type({name}))")
    assert result.status == "ok", result.error
    assert printed == ["nil"]


def test_what_tests_need_still_works(tmp_path):
    result, printed = run_lua(tmp_path, '''
        print(type(os.time()), type(os.clock()), os.date("%Y", 0) ~= nil, os.difftime(5, 2))
        print(math.floor(2.7), string.format("%.2f", 1/3), ("abc"):upper(), #table.pack(1, 2))
        print(utf8.char(181), coroutine.wrap(function() coroutine.yield(7) end)())
        local f = load("return 1 + 2")
        print(f(), load("return x", "chunk", "t", { x = 5 })())
    ''')
    assert result.status == "ok", result.error
    assert printed == ["number\tnumber\ttrue\t3.0", "2\t0.33\tABC\t2", "µ\t7", "3\t5"]


def test_load_refuses_bytecode_and_keeps_an_explicit_nil_env(tmp_path):
    result, printed = run_lua(tmp_path, '''
        local f, err = load("\\27Lua garbage", "x", "b")
        print(f, err ~= nil and err:find("binary") ~= nil)
        local g = load("return print", "y", "t", nil)
        local ok = pcall(g)
        print(ok)
    ''')
    assert result.status == "ok", result.error
    # bytecode is refused even when asked for; with env nil, the chunk sees no globals
    assert printed == ["nil\ttrue", "false"]


def test_a_script_cannot_get_the_dangerous_parts_back(tmp_path):
    result, printed = run_lua(tmp_path, '''
        print(getmetatable("").__index.dump, rawget(_G, "io"), _ENV.require)
    ''')
    assert result.status == "ok", result.error
    assert printed == ["nil\tnil\tnil"]


def test_the_stop_button_still_works_without_debug(tmp_path):
    import threading

    project = create_project(tmp_path / "proj")
    (project.path / "main.lua").write_text("while true do end", encoding="utf-8")
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    result = run_project(project.path, on_print=lambda _: None, stop_event=stop)
    assert result.status == "stopped"


def test_a_runaway_script_hits_the_memory_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "MAX_SCRIPT_MEMORY_MB", 20)
    result, _ = run_lua(tmp_path, "local t = {} for i = 1, 1e8 do t[i] = i end")
    assert result.status == "error"
    assert "used more than 20 MB of memory" in result.error
