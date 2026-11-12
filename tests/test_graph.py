# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import json

import pytest

from benchloom.engine import run_project
from benchloom.graph import (GRAPH_VERSION, Catalog, GraphError, compile_graph, example_graph,
                             lua_literal)
from benchloom.project import create_project


def node(node_id, kind, **extra):
    return {"id": node_id, "type": kind, **extra}


def wire(kind, src, src_pin, dst, dst_pin):
    return {"kind": kind, "from": [src, src_pin], "to": [dst, dst_pin]}


def chain(*ids):
    return [wire("exec", a, "next", b, "in") for a, b in zip(ids, ids[1:])]


def run_graph(tmp_path, nodes, wires):
    project = create_project(tmp_path / "proj", "visual")
    graph = {"version": 1, "nodes": [node("start", "core.start"), *nodes], "wires": wires}
    (project.path / project.script).write_text(json.dumps(graph), encoding="utf-8")
    lines = []
    return run_project(project.path, on_print=lines.append), lines


def test_example_graph_runs_like_the_lua_example(tmp_path):
    project = create_project(tmp_path / "proj", "visual")
    lines = []
    result = run_project(project.path, on_print=lines.append)
    assert result.status == "ok", result.error
    assert lines == ["Sweep finished"]
    assert len(result.rows) == 10
    assert result.rows[4]["set_V"] == 5
    assert result.rows[4]["V"] == pytest.approx(5.0, abs=0.05)
    assert result.rows[4]["I"] == pytest.approx(0.5, abs=0.01)
    assert (result.path / "graph.lua").is_file()


def test_line_map_points_at_nodes(tmp_path):
    project = create_project(tmp_path / "proj", "visual")
    code, line_map = compile_graph(example_graph(), Catalog(project))
    lines = code.splitlines()
    assert len(lines) == len(line_map)
    assert line_map[lines.index("    psu:set_voltage(v.n4_index)")] == "n5"
    assert '["V"] = psu:measure_voltage()' in code  # value node is inlined where it is used


def test_if_while_variables_and_math(tmp_path):
    nodes = [
        node("set0", "core.set_var", values={"name": "count", "value": 0}),
        node("loop", "core.while"),
        node("get", "core.get_var", values={"name": "count"}),
        node("lt", "core.compare", values={"op": "<", "b": 3}),
        node("add", "core.math", values={"op": "+", "b": 1}),
        node("inc", "core.set_var", values={"name": "count"}),
        node("check", "core.if"),
        node("eq", "core.compare", values={"op": "==", "b": 3}),
        node("yes", "core.print", values={"text": "three"}),
        node("no", "core.print", values={"text": "not three"}),
        node("after", "core.print", values={"text": "after"}),
    ]
    wires = chain("start", "set0", "loop") + [
        wire("data", "get", "value", "lt", "a"),
        wire("data", "lt", "result", "loop", "condition"),
        wire("exec", "loop", "body", "inc", "in"),
        wire("data", "get", "value", "add", "a"),
        wire("data", "add", "result", "inc", "value"),
        wire("exec", "loop", "done", "check", "in"),
        wire("data", "get", "value", "eq", "a"),
        wire("data", "eq", "result", "check", "condition"),
        wire("exec", "check", "then", "yes", "in"),
        wire("exec", "check", "else", "no", "in"),
        wire("exec", "check", "done", "after", "in"),
    ]
    result, lines = run_graph(tmp_path, nodes, wires)
    assert result.status == "ok", result.error
    assert lines == ["three", "after"]


def test_sequenced_measurement_keeps_its_value(tmp_path):
    nodes = [
        node("on", "device", device="psu", command="set_output", values={"on": True}),
        node("set", "device", device="psu", command="set_voltage", values={"volts": 4}),
        node("meas", "device", device="psu", command="measure_voltage", sequenced=True),
        node("off", "device", device="psu", command="set_output", values={"on": False}),
        node("log", "core.log", columns=["V"]),
    ]
    wires = chain("start", "on", "set", "meas", "off", "log") + [
        wire("data", "meas", "result", "log", "V")]
    result, _ = run_graph(tmp_path, nodes, wires)
    # measured while the output was on, logged after it was switched off
    assert result.rows[0]["V"] == pytest.approx(4.0, abs=0.05)


def test_fail_node_and_runtime_error_line(tmp_path):
    result, _ = run_graph(tmp_path, [node("f", "core.fail", values={"message": "too hot"})],
                          chain("start", "f"))
    assert result.status == "error" and "too hot" in result.error

    result, _ = run_graph(
        tmp_path / "b",
        [node("set", "device", device="psu", command="set_voltage", values={"volts": 99})],
        chain("start", "set"))
    assert "graph.lua:2:" in result.error and "above the maximum" in result.error


@pytest.mark.parametrize("nodes, wires, message, where", [
    ([node("s", "device", device="psu", command="set_voltage")],
     chain("start", "s"), "needs a value or a wire", "s"),
    ([node("a", "core.wait"), node("b", "core.wait")],
     chain("start", "a", "b", "a"), "form a circle", "a"),
    ([node("p", "core.print"), node("m", "core.math")],
     chain("start", "p") + [wire("data", "m", "result", "m", "a"),
                            wire("data", "m", "result", "p", "text")],
     "value wires form a circle", "m"),
    ([node("x", "device", device="nope", command="idn")], [], "no device named", "x"),
    ([node("x", "device", device="psu", command="nope")], [], "no command", "x"),
    # wires that end at pins the node does not have: never skipped without a word
    ([node("p", "core.print"), node("m", "core.math")],
     chain("start", "p") + [wire("data", "m", "nope", "p", "text")], "does not have", "m"),
    ([node("p", "core.print"), node("m", "core.math")],
     chain("start", "p") + [wire("data", "m", "result", "p", "nope")], "does not have", "p"),
    ([node("p", "core.print"), node("q", "core.print")],
     chain("start", "p") + [wire("exec", "p", "later", "q", "in")], "does not have", "p"),
    ([node("m", "core.math")], [wire("exec", "start", "next", "m", "in")], "does not have", "m"),
    ([node("p", "core.print")], [wire("power", "start", "next", "p", "in")],
     "unknown kind 'power'", "p"),
    ([node("p", "core.print"), node("m", "core.math"), node("n", "core.math")],
     chain("start", "p") + [wire("data", "m", "result", "p", "text"),
                            wire("data", "n", "result", "p", "text")], "Two wires lead into", "p"),
    ([node("a", "core.wait"), node("b", "core.wait")],
     chain("start", "a") + [wire("exec", "start", "next", "b", "in")],
     "Two run-order wires", "start"),
])
def test_compile_errors_name_the_node(tmp_path, nodes, wires, message, where):
    project = create_project(tmp_path / "proj", "visual")
    graph = {"nodes": [node("start", "core.start"), *nodes], "wires": wires}
    with pytest.raises(GraphError) as error:
        compile_graph(graph, Catalog(project))
    assert message in str(error.value)
    assert error.value.node_id == where


@pytest.mark.parametrize("version", [GRAPH_VERSION + 1, "2", None])
def test_a_graph_from_a_newer_benchloom_is_refused(tmp_path, version):
    project = create_project(tmp_path / "proj", "visual")
    graph = {"version": version, "nodes": [node("start", "core.start")], "wires": []}
    with pytest.raises(GraphError, match="saved by a newer Benchloom"):
        compile_graph(graph, Catalog(project))


def test_a_wire_of_a_deleted_node_is_ignored(tmp_path):
    result, lines = run_graph(tmp_path, [node("p", "core.print", values={"text": "typed"})],
                              chain("start", "p") + [wire("data", "gone", "result", "p", "text")])
    assert result.status == "ok", result.error
    assert lines == ["typed"]


def test_lua_literals():
    assert lua_literal(1.0) == "1" and lua_literal(0.1) == "0.1"
    assert lua_literal(True) == "true"
    assert lua_literal('a "b"\n\\') == '"a \\"b\\"\\n\\\\"'


def test_traced_code_reports_each_node_without_moving_lines(tmp_path):
    project = create_project(tmp_path / "proj", "visual")
    plain, line_map = compile_graph(example_graph(), Catalog(project))
    traced, traced_map = compile_graph(example_graph(), Catalog(project), trace=True)
    assert traced_map == line_map and len(traced.splitlines()) == len(plain.splitlines())
    assert "__node" not in plain
    assert '    __node("n5"); psu:set_voltage(v.n4_index)' in traced.splitlines()

    seen = []
    result = run_project(project.path, on_print=lambda _: None, on_node=seen.append)
    assert result.status == "ok", result.error
    assert seen[0] == "n2" and seen.count("n5") == 10  # once per pass through the loop
    assert '__node("n5")' in (result.path / "graph.lua").read_text(encoding="utf-8")


def test_a_script_reaches_the_node_report_only_with_text(tmp_path):
    project = create_project(tmp_path / "proj")
    (project.path / "main.lua").write_text('__node("x"); __node = nil; print("ok")',
                                           encoding="utf-8")
    seen = []
    result = run_project(project.path, on_print=lambda _: None, on_node=seen.append)
    assert result.status == "ok", result.error
    assert seen == ["x"]  # harmless: it only passes a text to the window
