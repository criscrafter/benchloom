# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The Lua editor: autocomplete, and the line a running test is at."""
import os
import time

import pytest

from benchloom.engine import run_project
from benchloom.project import create_project

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from benchloom.gui.editor import CodeEditor, Completion, in_string_or_comment  # noqa: E402
from benchloom.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_the_engine_reports_the_script_line(tmp_path):
    project = create_project(tmp_path / "proj")
    (project.path / "main.lua").write_text(
        "local x = 0\nfor i = 1, 2 do\n  sleep(0.01)\n  log { i = i }\nend\n"
        "for i = 1, 300000 do x = x + i end\nprint(x)\n", encoding="utf-8")
    seen = []
    result = run_project(project.path, on_print=lambda _: None, on_line=seen.append)
    assert result.status == "ok", result.error
    assert seen[:4] == [3, 4, 3, 4]  # each sleep and log, only when the line changed
    assert 6 in seen  # a busy loop is seen too, between instruments calls


def test_what_autocomplete_offers(app):
    editor = CodeEditor()
    editor.set_names({"psu": [Completion("set_voltage(", "set_voltage(volts [V])"),
                              Completion("idn()", "idn()  → string")]}, ["target", "stop"])
    labels = lambda line: [c.label for c in editor.completions(line)[0]]
    assert labels("  psu:") == ["set_voltage(volts [V])", "idn()  → string"]
    assert editor.completions("x = psu:set_v")[1] == "set_v"
    assert labels("print(panel.") == ["target", "stop"]
    assert "psu" in labels("ps") and "sleep(seconds)" in labels("sl")
    assert labels('print("psu:') == [] and labels("-- psu:") == []
    assert labels("other:") == [] and labels("t.x") == []
    assert in_string_or_comment('a = "x\\"') and not in_string_or_comment('a = "x" ')


def test_typing_a_device_name_and_colon_completes_commands(app):
    editor = CodeEditor()
    editor.set_names({"psu": [Completion("set_voltage(", "set_voltage(volts [V])"),
                              Completion("set_output(", "set_output(on)")]}, [])
    editor.show()
    QTest.keyClicks(editor, "psu:set_o")
    popup = editor.completer.popup()
    assert popup.isVisible() and editor.completer.completionCount() == 1
    QTest.keyClick(popup, Qt.Key.Key_Return)
    assert editor.toPlainText() == "psu:set_output("
    editor.close()


def test_the_running_line_is_marked_in_the_app(app, tmp_path):
    project = create_project(tmp_path / "proj")
    window = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    assert window.open_project(project.path)
    assert any(c.label.startswith("set_voltage(volts [V])") for c in window.editor.devices["psu"])
    window.editor.setPlainText("for i = 1, 5 do\n  sleep(0.1)\n  log { i = i }\nend\n")
    marked = []
    window.runner.line.connect(lambda _: marked.append(window.editor._running_line))
    done = []
    window.runner.finished.connect(lambda *args: done.append(args))
    window.run()
    deadline = time.monotonic() + 30
    while not done and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert done and done[0][0] == "ok", done
    assert 2 in marked  # it spends its time in sleep, on line 2
    assert window.editor._running_line is None  # cleared at the end
    window.editor.document().setModified(False)
    window.close()
