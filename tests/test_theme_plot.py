# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from benchloom.gui.editor import is_dark  # noqa: E402
from benchloom.gui.main_window import MainWindow  # noqa: E402
from benchloom.gui.plot import PlotPanel  # noqa: E402
from benchloom.gui import theme  # noqa: E402
from benchloom.gui.theme import apply_theme  # noqa: E402
from benchloom.project import create_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    yield app
    apply_theme("light", app)  # leave other test modules a light app


def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


def sweep(plot, rows=5):
    for n in range(rows):
        plot.add_row({"time_s": n, "V": 50.0 * n, "P": 2.0 * n})
    plot._redraw()


def test_columns_can_go_on_a_right_axis(app):
    plot = PlotPanel()
    sweep(plot)
    vb = plot.plot.getPlotItem().vb
    assert plot._curves["V"] in vb.addedItems and plot._curves["P"] in vb.addedItems
    assert [a.text() for a in plot.right_menu.actions()] == ["V", "P"]  # not the X column

    seen = []
    plot.right_columns_changed.connect(seen.append)
    plot.right_menu.actions()[1].setChecked(True)  # P to the right
    assert seen == [["P"]]
    assert plot._curves["P"] in plot.right_view.addedItems
    assert plot._curves["P"] not in vb.addedItems
    item = plot.plot.getPlotItem()
    assert item.getAxis("right").labelText == "P" and item.getAxis("left").labelText == "V"
    labels = [label.text for _, label in plot.legend.items]
    assert labels == ["V", "P (right)"]
    # the right scale is its own: it fits 0-8 W, not 0-200 V
    plot.right_view.autoRange(padding=0)
    assert plot.right_view.viewRange()[1] == pytest.approx([0, 8])

    # the choice survives a new run
    plot.clear()
    sweep(plot)
    assert plot._curves["P"] in plot.right_view.addedItems
    assert [a.isChecked() for a in plot.right_menu.actions()] == [False, True]


def test_x_column_is_not_offered_for_the_right_axis(app):
    plot = PlotPanel()
    sweep(plot)
    plot.set_right_columns(["P"])
    plot.x_axis.setCurrentText("V")  # plot P against V
    assert [a.text() for a in plot.right_menu.actions()] == ["time_s", "P"]
    assert "V" not in plot._curves
    assert plot._curves["P"] in plot.right_view.addedItems


def test_theme_switches_live_and_is_remembered(app, tmp_path):
    project = create_project(tmp_path / "proj")
    window = MainWindow(settings(tmp_path))
    window.open_project(project.path)
    window.set_theme("light")
    sweep(window.plot)  # legend labels made in light mode must turn light in dark mode
    window.set_theme("dark")
    app.processEvents()
    assert is_dark(app.palette()) and app.style().name() == "fusion"
    background = window.plot.plot.backgroundBrush().color()
    assert background.lightness() < 60
    assert window.editor.highlighter.formats["keyword"].foreground().color().name() == "#c792ea"
    assert window.graph.canvas_color.lightness() < 60
    text = app.palette().text().color().name()[1:]  # e3e3e3
    labels = [label.item.toHtml() for _, label in window.plot.legend.items]
    assert labels and all(text in html for html in labels)

    window.set_theme("light")
    app.processEvents()
    assert not is_dark(app.palette()) and app.style().name() == theme._native_style
    assert window.plot.plot.backgroundBrush().color().lightness() > 200
    assert window.editor.highlighter.formats["keyword"].foreground().color().name() == "#8431c5"
    window.close()

    again = MainWindow(settings(tmp_path))
    assert again.theme() == "light" and again.theme_actions["light"].isChecked()
    again.close()


def test_right_axis_choice_is_kept_per_project(app, tmp_path):
    first, second = create_project(tmp_path / "a"), create_project(tmp_path / "b")
    window = MainWindow(settings(tmp_path))
    window.open_project(first.path)
    sweep(window.plot)
    window.plot.right_menu.actions()[1].setChecked(True)
    window.open_project(second.path)
    assert window.plot.right_columns == set()
    window.open_project(first.path)
    assert window.plot.right_columns == {"P"}
    window.close()
