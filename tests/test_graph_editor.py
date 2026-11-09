import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from patchbay.graph import Catalog, example_graph  # noqa: E402
from patchbay.gui.graph_editor import GraphEditor, WireItem, parse_value  # noqa: E402
from patchbay.gui.main_window import MainWindow  # noqa: E402
from patchbay.project import Project, create_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    project = create_project(tmp_path / "proj", "visual")
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    assert window.open_project(project.path)
    yield window
    window.graph.modified = False
    window.close()


def wait_for_run(app, window, timeout=30):
    done = []
    window.runner.finished.connect(lambda *args: done.append(args))
    deadline = time.monotonic() + timeout
    while not done and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert done, "run did not finish"
    app.processEvents()
    return done[0]


def wire_count(editor):
    return sum(isinstance(item, WireItem) for item in editor.scene().items())


def normalized(graph):
    nodes = sorted((json.dumps(n, sort_keys=True) for n in graph["nodes"]))
    wires = sorted((json.dumps(w, sort_keys=True) for w in graph["wires"]))
    return nodes, wires


def test_load_and_save_keep_the_graph(app, tmp_path):
    project = create_project(tmp_path / "proj", "visual")
    editor = GraphEditor()
    editor.load(example_graph(), Catalog(project))
    assert len(editor.nodes) == 11 and wire_count(editor) == 12
    assert not editor.modified
    saved = editor.to_graph()
    # booleans and choices shown on a node are written out, so compare after one round trip
    editor.load(saved, Catalog(project))
    assert normalized(editor.to_graph()) == normalized(saved)
    assert {n["id"] for n in saved["nodes"]} == {n["id"] for n in example_graph()["nodes"]}
    assert len(saved["wires"]) == 12


def test_wired_inputs_hide_their_editor(app, tmp_path):
    project = create_project(tmp_path / "proj", "visual")
    editor = GraphEditor()
    editor.load(example_graph(), Catalog(project))
    set_voltage = editor.nodes["n5"]
    assert not set_voltage.proxies["volts"].isVisible()
    editor.remove_wire(set_voltage.pins[("data", "volts", False)].wires[0])
    assert set_voltage.proxies["volts"].isVisible()
    assert editor.modified


def test_run_visual_project(app, window):
    window.run()
    status, error, _ = wait_for_run(app, window)
    assert status == "ok", error
    assert "Sweep finished" in window.console.toPlainText()
    assert len(window.plot.rows) == 10


def test_build_a_test_by_hand(app, window):
    graph = window.graph
    for item in list(graph.nodes.values()):
        graph.remove_node(item)  # Start stays
    assert list(graph.nodes) == ["n1"] and wire_count(graph) == 0

    idn = graph.add_node({"type": "device", "device": "psu", "command": "idn"})
    show = graph.add_node({"type": "core.print"})
    graph.connect_pins(graph.nodes["n1"].pins[("exec", "next", True)],
                       show.pins[("exec", "in", False)])
    graph.connect_pins(idn.pins[("data", "result", True)], show.pins[("data", "text", False)])
    window.run()
    status, error, _ = wait_for_run(app, window)
    assert status == "ok", error
    assert "Patchbay,Demo PSU" in window.console.toPlainText()
    saved = json.loads((window.project.path / "main.graph.json").read_text())
    assert len(saved["nodes"]) == 3 and len(saved["wires"]) == 2


def test_missing_value_marks_the_node_and_does_not_run(app, window):
    node = window.graph.nodes["n5"]
    window.graph.remove_wire(node.pins[("data", "volts", False)].wires[0])
    window.run()
    assert not window.runner.running
    assert window.graph.nodes["n5"].error
    assert "needs a value or a wire" in window.console.toPlainText()


def test_runtime_error_marks_the_node(app, window):
    window.graph.nodes["n4"].data["values"]["to"] = 30  # sweeps past the 20 V limit
    window.run()
    status, error, _ = wait_for_run(app, window)
    assert status == "error" and "above the maximum" in error
    assert window.graph.nodes["n5"].error
    assert not window.graph.nodes["n4"].error


def test_run_in_sequence_toggle_keeps_wires(app, window):
    graph = window.graph
    node = graph._set_sequenced(graph.nodes["n8"], True) or graph.nodes["n8"]
    assert node.spec.exec_in and ("exec", "in", False) in node.pins
    assert len(node.pins[("data", "result", True)].wires) == 1
    assert wire_count(graph) == 12


def test_log_columns(app, window):
    graph = window.graph
    graph.add_column(graph.nodes["n7"], "note")
    node = graph.nodes["n7"]
    assert [row[0] for row in node.left] == ["set_V", "V", "I", "note"]
    assert wire_count(graph) == 12
    graph._remove_column(node, "V")
    assert wire_count(graph) == 11


def test_removed_device_flags_nodes_but_keeps_them(app, window):
    project = window.project
    project.devices["supply"] = project.devices.pop("psu")
    project.devices["supply"].name = "supply"
    project.save()
    window.devices.set_project(Project.load(project.path))
    window.project = window.devices.project
    window._devices_changed()
    graph = window.graph
    assert graph.nodes["n5"].broken and graph.nodes["n5"].error
    saved = graph.to_graph()
    assert len(saved["nodes"]) == 11 and len(saved["wires"]) == 12


def test_parse_value():
    assert parse_value("2,5", "number") == 2.5
    assert parse_value("3", "integer") == 3
    assert parse_value("", "number") is None
    assert parse_value("true", "any") is True
    assert parse_value("hello", "any") == "hello"
    with pytest.raises(ValueError):
        parse_value("abc", "number")
