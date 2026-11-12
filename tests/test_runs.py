# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Past runs: reading them back, the Runs panel, showing and comparing them."""
import os
import shutil
import time

import pytest

from benchloom.engine import run_project
from benchloom.history import list_runs, read_rows, read_run
from benchloom.project import create_project


def run_script(project, code):
    (project.path / "main.lua").write_text(code, encoding="utf-8")
    result = run_project(project.path, on_print=lambda _: None)
    time.sleep(1.05)  # run folders are named by the second they started
    return result


def test_runs_are_read_back_newest_first(tmp_path):
    project = create_project(tmp_path / "proj")
    passed = run_script(project, 'log { V = 1, ok = true, note = "a" } check("c", 1, 0, 2)')
    failed = run_script(project, 'log { V = 2 } check("c", 5, 0, 2)')
    broken = run_script(project, 'error("boom")')
    (project.path / "runs" / "2026-01-01_000000").mkdir()  # killed before run.json
    runs = list_runs(project.path)
    assert [r.path for r in runs[:3]] == [broken.path, failed.path, passed.path]
    assert [(r.status, r.result) for r in runs[:3]] == [("error", "none"), ("ok", "fail"),
                                                         ("ok", "pass")]
    assert runs[0].error.endswith("boom") and runs[1].failed == 1 and runs[2].passed == 1
    assert runs[-1].status == "unknown" and runs[-1].started.year == 2026
    assert runs[2].has_report and runs[2].duration_s is not None
    assert read_rows(passed.path) == [{"time_s": read_rows(passed.path)[0]["time_s"],
                                       "V": 1, "note": "a", "ok": True}]
    assert read_run(tmp_path).status == "unknown" and list_runs(tmp_path / "none") == []


# --- the window ----------------------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


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


def test_runs_panel_shows_compares_and_deletes(app, tmp_path):
    from PySide6.QtCore import QSettings

    from benchloom.gui.main_window import MainWindow

    project = create_project(tmp_path / "proj")
    first = run_script(project, 'for v = 1, 5 do log { set_V = v, V = v * 2 } end print("old")')
    window = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    assert window.open_project(project.path)
    panel = window.runs
    assert panel.tree.topLevelItemCount() == 1
    assert panel.tree.topLevelItem(0).text(1) == "–"  # no checks

    window.editor.setPlainText('log { set_V = 1, V = 2.1 } check("V", 2.1, 2, 2.2, "V")')
    window.run()  # a new run appears at the top, selected
    status, error, path = wait_for_run(app, window)
    assert status == "ok", error
    assert panel.tree.topLevelItemCount() == 2
    assert panel.selected().path.name == os.path.basename(path)
    assert panel.tree.currentItem().text(1) == "PASS"

    panel.tree.setCurrentItem(panel.tree.topLevelItem(1))  # the older run
    panel.compare_button.click()
    plot = window.plot
    assert len(plot.reference) == 5 and plot.reference_label
    assert {"V", "set_V"} <= set(plot._ref_curves) | {plot.x_axis.currentText()}
    assert panel.uncompare_button.isEnabled()
    plot.clear()  # a new run keeps the comparison
    assert plot.reference and plot.x_axis.count() > 0

    panel.show_button.click()
    assert len(window.plot.rows) == 5 and window.table.model.rowCount() == 5
    assert "old" in window.console.toPlainText()
    assert window._last_run == first.path

    panel.uncompare_button.click()
    assert plot.reference == [] and plot._ref_curves == {}
    assert not panel.uncompare_button.isEnabled()

    trashed = []
    panel._trash = lambda path: trashed.append(path) or shutil.rmtree(path) is None
    assert panel.delete_selected(confirm=False)
    assert trashed == [first.path] and panel.tree.topLevelItemCount() == 1
    window.close()
