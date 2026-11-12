# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import json
import os
import time

import pytest

from benchloom.engine import run_project
from benchloom.graph import Catalog, compile_graph
from benchloom.panel import Panel, PanelError, PanelState, Widget
from benchloom.project import create_project


def make_project(tmp_path, lua=None, mode="lua"):
    project = create_project(tmp_path / "proj", mode)
    panel = Panel(project.path / "panel.json", [
        Widget("number", "target", value=3.0, unit="V", max=20),
        Widget("switch", "enable", value=True),
        Widget("button", "stop"),
        Widget("text", "note", value="hello"),
        Widget("display", "reading", unit="V"),
        Widget("led", "ok"),
        Widget("chart", "trend"),
        Widget("label", "title", label="My test"),
    ])
    panel.save()
    if lua is not None:
        (project.path / "main.lua").write_text(lua, encoding="utf-8")
    return project


def run(project, **kwargs):
    lines, changes = [], []
    result = run_project(project.path, on_print=lines.append,
                         on_panel=lambda name, value: changes.append((name, value)), **kwargs)
    return result, lines, changes


def test_panel_file_round_trip(tmp_path):
    project = make_project(tmp_path)
    panel = Panel.load(project.path)
    assert [w.name for w in panel.widgets][:3] == ["target", "enable", "stop"]
    assert panel.control_values() == {"target": 3.0, "enable": True, "stop": False,
                                      "note": "hello"}
    added = panel.add("number")
    assert added.name == "number1" and added.y > 0


def test_script_reads_controls_and_sets_indicators(tmp_path):
    project = make_project(tmp_path, """
        print(panel.target, panel.enable, panel.note)
        panel.reading = panel.target * 2
        panel.ok = panel.reading > 5
        panel.trend = 1
        panel.trend = 2
        print(panel.reading)
    """)
    result, lines, changes = run(project)
    assert result.status == "ok", result.error
    assert lines == ["3.0\ttrue\thello", "6.0"]
    assert changes == [("reading", 6.0), ("ok", True), ("trend", 1.0), ("trend", 2.0)]


def test_live_values_override_saved_ones(tmp_path):
    project = make_project(tmp_path, "print(panel.target)\nprint(panel.note)")
    result, lines, _ = run(project, panel_values={"target": 7.5})
    assert lines == ["7.5", "hello"]


def test_button_is_true_once_per_click():
    state = PanelState(Panel(widgets=[Widget("button", "go")]), {"go": True})
    assert state.get("go") is True
    assert state.get("go") is False


def test_errors_name_the_widget_and_line(tmp_path):
    project = make_project(tmp_path, "local x = 1\nprint(panel.nope)\n")
    result, _, _ = run(project)
    assert result.status == "error"
    assert "main.lua:2:" in result.error and "no front panel widget named 'nope'" in result.error

    # a value display shows text as well as numbers; a chart needs numbers
    (project.path / "main.lua").write_text("panel.reading = 'abc'\npanel.trend = 'abc'",
                                           encoding="utf-8")
    result, _, changes = run(project)
    assert changes == [("reading", "abc")]
    assert "main.lua:2:" in result.error and "needs a number" in result.error


def test_number_widgets_reject_text():
    state = PanelState(Panel(widgets=[Widget("bar", "level")]))
    with pytest.raises(PanelError):
        state.set("level", "high")
    with pytest.raises(PanelError):
        state.set("title", 1)


def test_panel_nodes_in_a_graph(tmp_path):
    project = make_project(tmp_path, mode="visual")
    graph = {"version": 1, "nodes": [
        {"id": "start", "type": "core.start"},
        {"id": "get", "type": "panel.get", "widget": "target"},
        {"id": "double", "type": "core.math", "values": {"op": "*", "b": 2}},
        {"id": "show", "type": "panel.set", "widget": "reading"},
    ], "wires": [
        {"kind": "exec", "from": ["start", "next"], "to": ["show", "in"]},
        {"kind": "data", "from": ["get", "value"], "to": ["double", "a"]},
        {"kind": "data", "from": ["double", "result"], "to": ["show", "value"]},
    ]}
    code, _ = compile_graph(graph, Catalog(project))
    assert 'panel["reading"] = (panel["target"] * 2)' in code
    (project.path / project.script).write_text(json.dumps(graph), encoding="utf-8")
    result, _, changes = run(project)
    assert result.status == "ok", result.error
    assert changes == [("reading", 6.0)]
    palette = dict(Catalog(project).palette())
    labels = [label for label, _ in palette["Front panel"]]
    assert "Read target" in labels and "Set reading" in labels and "Set stop" not in labels


# --- desktop app -------------------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyqtgraph")


def test_charts_take_several_curves_and_tables_take_rows(tmp_path):
    project = make_project(tmp_path, """
        panel.trend = { V = 5, I = 0.25 }
        panel.results = { step = 1, V = 5, ok = true }
        panel.results = nil
        panel.dial = 3.5
    """)
    panel = Panel.load(project.path)
    panel.widgets += [Widget("table", "results"), Widget("gauge", "dial", max=10)]
    panel.save()
    result, _, changes = run(project)
    assert result.status == "ok", result.error
    assert changes == [("trend", {"V": 5.0, "I": 0.25}),
                       ("results", {"step": 1, "V": 5, "ok": True}),
                       ("results", None), ("dial", 3.5)]


@pytest.mark.parametrize("widget, code, message", [
    (Widget("chart", "c"), "panel.c = { V = 'high' }", "a number or a table of numbers"),
    (Widget("chart", "c"), "panel.c = {}", "a number or a table of numbers"),
    (Widget("chart", "c", x_value="f"), "panel.c = { gain = -3 }", "needs 'f' in the table"),
    (Widget("table", "t"), "panel.t = 5", "needs a row"),
    (Widget("table", "t"), "panel.t = { a = { 1 } }", "needs a row"),
    (Widget("gauge", "g"), "panel.g = 'x'", "needs a number"),
])
def test_new_widgets_reject_what_they_cannot_show(tmp_path, widget, code, message):
    project = make_project(tmp_path, code)
    panel = Panel.load(project.path)
    panel.widgets.append(widget)
    panel.save()
    result, _, _ = run(project)
    assert result.status == "error" and message in result.error
    assert "main.lua:1:" in result.error


def test_renaming_a_widget_in_graphs_and_scripts():
    from benchloom.panel import rename_in_graph, rename_in_lua

    graph = {"nodes": [{"id": "a", "type": "panel.get", "widget": "volts"},
                       {"id": "b", "type": "panel.set", "widget": "volts2"},
                       {"id": "c", "type": "device", "widget": "volts"}]}
    assert rename_in_graph(graph, "volts", "target") == 1
    assert [n["widget"] for n in graph["nodes"]] == ["target", "volts2", "volts"]
    code = ("x = panel.volts + panel . volts2\npanel['volts'] = 1\n"
            "local volts = panel.voltsx -- volts\nprint(panel[ \"volts\" ])")
    assert rename_in_lua(code, "volts", "target") == (
        "x = panel.target + panel . volts2\npanel['target'] = 1\n"
        "local volts = panel.voltsx -- volts\nprint(panel[ \"target\" ])", 3)

@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def open_window(tmp_path, project):
    from PySide6.QtCore import QSettings

    from benchloom.gui.main_window import MainWindow

    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    assert window.open_project(project.path)
    return window


def pump(app, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


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


def test_live_panel_in_the_app(app, tmp_path):
    project = make_project(tmp_path, """
        while not panel.stop do
            panel.reading = panel.target * 2
            panel.ok = panel.enable
            panel.trend = panel.target
            sleep(0.02)
        end
        print("stopped by the button")
    """)
    window = open_window(tmp_path, project)
    view = window.panel_view
    assert set(view.items) == {"target", "enable", "stop", "note", "reading", "ok", "trend",
                               "title"}
    window.run()
    pump(app, 1.0)
    assert window.runner.running
    assert view.items["reading"].control.text() == "6 V"
    assert view.items["ok"].control.on is True

    view.items["target"].control.setValue(4.5)  # the user changes controls mid-run
    view.items["enable"].control.setChecked(False)
    pump(app, 0.6)
    assert view.items["reading"].control.text() == "9 V"
    assert view.items["ok"].control.on is False
    assert len(view.items["trend"]._series[""][0]) > 5

    view.items["stop"].control.click()
    status, error, _ = wait_for_run(app, window)
    assert status == "ok", error
    assert "stopped by the button" in window.console.toPlainText()
    # control values at the time of the run are remembered
    assert Panel.load(project.path).get("target").value == 3.0
    window.close()


def test_edit_the_panel(app, tmp_path):
    project = create_project(tmp_path / "proj", "visual")
    window = open_window(tmp_path, project)
    view = window.panel_view
    assert view.items == {}
    view.edit_box.setChecked(True)
    item = view.add("number")
    second = view.add("display")
    assert second.model.y >= item.model.y + item.model.h
    assert item.overlay.isVisibleTo(view)

    item.model.name, item.model.unit, item.model.max = "setpoint", "V", 20.0
    item = view.rebuild(item)
    assert "setpoint" in view.items and "number1" not in view.items
    assert item.control.maximum() == 20.0 and item.control.suffix() == " V"

    item.move(200, 40)
    view.geometry_edited(item)
    saved = Panel.load(project.path)
    assert [(w.name, w.x, w.y) for w in saved.widgets][0] == ("setpoint", 200, 40)

    # the new widgets are offered as nodes straight away
    labels = [label for label, _ in dict(window.graph.catalog.palette())["Front panel"]]
    assert labels == ["Read setpoint", "Set setpoint", "Set display1"]

    view.remove(second)
    assert [w.name for w in Panel.load(project.path).widgets] == ["setpoint"]
    window.graph.modified = False
    window.close()


def test_new_widgets_in_the_app(app, tmp_path):
    project = make_project(tmp_path, "")
    panel = Panel.load(project.path)
    panel.widgets += [Widget("table", "results", columns=["V", "step"]),
                      Widget("gauge", "dial", max=10, unit="V"),
                      Widget("chart", "bode", x_value="f", log_x=True)]
    panel.save()
    window = open_window(tmp_path, project)
    view = window.panel_view
    view.start_run()
    view.show_value("trend", {"V": 5.0, "I": 0.25})
    view.show_value("trend", {"V": 6.0, "I": 0.3})
    view.show_value("trend", 7.0)  # a plain number is one more curve
    trend = view.items["trend"]
    assert sorted(trend._series) == ["", "I", "V"] and len(trend._series["V"][1]) == 2
    view.show_value("bode", {"f": 100.0, "gain": -3.0})
    view.show_value("bode", {"f": 0.0, "gain": 0.0})  # no place on a log axis
    assert view.items["bode"]._series["gain"][:2] == ([100.0], [-3.0])
    for i in range(3):
        view.show_value("results", {"step": i, "V": i * 1.5})
    rows = view.items["results"]._rows
    assert rows.columns == ["V", "step"] and len(rows.rows) == 3  # the order set for it
    view.show_value("results", None)
    assert rows.rows == []
    view.show_value("dial", 2.5)
    assert view.items["dial"].control.fraction() == 0.25
    view.start_run()  # a new run starts empty
    assert view.items["trend"]._series == {}
    window.close()


def test_renaming_a_widget_keeps_the_nodes(app, tmp_path, monkeypatch):
    from benchloom.gui import panel_view as panel_view_module

    project = make_project(tmp_path, mode="visual")
    window = open_window(tmp_path, project)
    graph = window.graph.to_graph()
    graph["nodes"].append({"id": "p1", "type": "panel.get", "widget": "target", "x": 0, "y": 0})
    window.graph.load(graph, window._catalog())

    def rename(dialog):  # what the settings dialog does when the user types a new name
        dialog.name_edit.setText("setpoint")
        dialog.accept()
        return True

    monkeypatch.setattr(panel_view_module.WidgetDialog, "exec", rename)
    window.panel_view.edit_properties(window.panel_view.items["target"])
    nodes = {n["id"]: n for n in window.graph.to_graph()["nodes"]}
    assert nodes["p1"]["widget"] == "setpoint" and not window.graph.nodes["p1"].broken
    assert window.graph.modified
    window.graph.modified = False
    window.close()


def test_renaming_a_widget_offers_to_change_the_script(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from benchloom.gui import main_window as main_window_module
    from benchloom.gui import panel_view as panel_view_module

    project = make_project(tmp_path, "print(panel.target)\npanel.reading = panel.target\n")
    window = open_window(tmp_path, project)
    asked = []
    monkeypatch.setattr(main_window_module.QMessageBox, "question",
                        lambda *args: asked.append(args[2]) or QMessageBox.StandardButton.Yes)

    def rename(dialog):
        dialog.name_edit.setText("setpoint")
        dialog.accept()
        return True

    monkeypatch.setattr(panel_view_module.WidgetDialog, "exec", rename)
    window.panel_view.edit_properties(window.panel_view.items["target"])
    assert "in 2 place(s)" in asked[0]
    assert window.editor.toPlainText() == "print(panel.setpoint)\npanel.reading = panel.setpoint\n"
    window.editor.undo()  # one step undoes it
    assert "panel.target" in window.editor.toPlainText()
    window.editor.document().setModified(False)
    window.close()
