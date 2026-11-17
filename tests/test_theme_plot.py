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
    assert is_dark(app.palette()) and isinstance(app.style(), theme.DarkStyle)
    assert app.style().baseStyle().name() == "fusion"
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


def test_dark_check_boxes_and_fields_have_a_visible_edge(app):
    from PySide6.QtWidgets import QCheckBox, QLineEdit

    apply_theme("dark", app)
    window_color = app.palette().window().color()
    for widget in (QCheckBox(), QLineEdit()):
        widget.resize(60, 24)
        image = widget.grab().toImage()
        lightest = max(image.pixelColor(x, y).lightness()
                       for x in range(image.width()) for y in range(image.height()))
        # Fusion alone draws the edge darker than the window: nothing to see
        assert lightest > window_color.lightness() + 40, type(widget).__name__
    apply_theme("light", app)


def _right_value_at(plot, view_y):
    """The value the right axis shows at this height of the plot."""
    axis = plot.plot.getPlotItem().getAxis("right")
    assert axis.linkedView() is plot.plot.getPlotItem().vb
    return view_y * axis.scale


@pytest.mark.parametrize("left, right", [
    ([0, 200], [-1, 8]),       # one scale only above 0
    ([-5, 10], [-2, 1]),
    ([-3, -1], [2, 4]),        # one only below, the other only above
    ([-3, -1], [-4, -2]),      # both only below
    ([0, 0], [1, 2]),          # only zeros
])
def test_aligned_ranges_hold_the_values_with_zero_at_one_height(left, right):
    from benchloom.gui.plot import aligned_ranges
    ranges = aligned_ranges(left, right)
    heights = [-lo / (hi - lo) for lo, hi in ranges]
    assert heights[0] == pytest.approx(heights[1])
    for (lo, hi), ys in zip(ranges, (left, right)):
        assert lo <= min(ys) and hi >= max(ys) and lo < 0 < hi


def test_align_zero_puts_zero_of_both_scales_at_one_height(app):
    from PySide6.QtCore import QPointF
    plot = PlotPanel()
    assert not plot.align_zero.isEnabled()  # nothing on the right yet
    for n in range(5):
        plot.add_row({"time_s": n, "V": 50.0 * n, "I": n - 3.0})
    plot.set_right_columns(["I"])
    assert plot.align_zero.isEnabled()
    seen = []
    plot.align_zero_changed.connect(seen.append)
    plot.align_zero.setChecked(True)
    assert seen == [True]
    main = plot.plot.getPlotItem().vb
    current = plot._curves["I"]
    assert current.getViewBox() is main and not plot.right_view.isVisible()

    def in_sync():
        # every current is drawn at the height where the right axis shows it,
        # and 0 A sits where the left axis shows 0 V
        for i in (-3.0, 0.0, 1.0):
            y = main.mapFromItemToView(current, QPointF(0, i)).y()
            assert _right_value_at(plot, y) == pytest.approx(i)
        assert main.mapFromItemToView(current, QPointF(0, 0)).y() == pytest.approx(0)

    in_sync()
    lo, hi = main.viewRange()[1]
    assert _right_value_at(plot, lo) <= -3 and _right_value_at(plot, hi) >= 1
    assert lo <= 0 and hi >= 200  # the voltages fit too
    # zoom, pan and a drag on the right axis move one view: both scales go along
    main.scaleBy((0.5, 0.3), center=QPointF(2, 40))
    in_sync()
    main.translateBy(y=-25)
    in_sync()
    main.scaleBy((1, 2.5))  # what dragging either axis does
    in_sync()
    ratio = plot._zero_ratio
    plot.add_row({"time_s": 5, "V": 400.0, "I": 4.0})
    plot._redraw()
    assert plot._zero_ratio == ratio  # a zoom by hand is left alone
    # the A button fits both again
    plot.plot.getPlotItem().autoBtn.clicked.emit(True)
    assert main.state["autoRange"][1]
    in_sync()
    lo, hi = main.viewRange()[1]
    assert hi >= 400 and _right_value_at(plot, hi) >= 4
    # off: each scale fits itself again
    plot.align_zero.setChecked(False)
    assert plot._curves["I"].getViewBox() is plot.right_view and plot.right_view.isVisible()
    assert plot.plot.getPlotItem().getAxis("right").scale == 1.0
    plot.right_view.autoRange(padding=0)  # room for the dots, in a tiny test plot
    assert plot.right_view.viewRange()[1] == pytest.approx([-3, 4], abs=0.3)


class _Wheel:
    """Enough of a wheel event over an axis for ZeroAxis."""
    def __init__(self, axis, delta):
        from PySide6.QtCore import QPointF
        view = axis.linkedView().sceneBoundingRect()  # an axis' own rect spans its grid
        x = view.left() - 10 if axis.orientation == "left" else view.right() + 10
        self._pos, self._delta, self.accepted = QPointF(x, view.center().y()), delta, False

    def delta(self):
        return self._delta

    def scenePos(self):
        return self._pos

    def pos(self):
        return self._pos

    def accept(self):
        self.accepted = True

    def ignore(self):
        pass


def test_with_zero_aligned_each_axis_scales_on_its_own(app):
    from PySide6.QtCore import QPointF
    plot = PlotPanel()
    plot.resize(600, 400)
    plot.show()
    for n in range(5):
        plot.add_row({"time_s": n, "V": 50.0 * n, "I": n - 3.0})
    plot.set_right_columns(["I"])
    plot.set_align_zero(True)
    app.processEvents()
    item = plot.plot.getPlotItem()
    main, left, right = item.vb, item.getAxis("left"), item.getAxis("right")

    def shown(axis):  # the values an axis shows from bottom to top
        lo, hi = main.viewRange()[1]
        return lo * axis.scale, hi * axis.scale

    def at_one_height():  # 0 V and 0 A on the same line, every current where its label is
        current = plot._curves["I"]
        for i in (-3.0, 0.0, 1.0):
            assert main.mapFromItemToView(current, QPointF(0, i)).y() * right.scale \
                == pytest.approx(i)

    volts, amps = shown(left), shown(right)
    event = _Wheel(right, -240)  # wheel on the right axis: zoom out the current
    right.wheelEvent(event)
    assert event.accepted
    assert shown(left) == pytest.approx(volts)  # the voltage scale did not move
    assert shown(right)[1] > amps[1] * 1.5 and shown(right)[0] < amps[0] * 1.5
    at_one_height()
    volts, amps = shown(left), shown(right)
    left.wheelEvent(_Wheel(left, 240))  # wheel on the left axis: zoom in the voltage
    assert shown(right) == pytest.approx(amps)  # the current scale did not move
    assert shown(left)[1] < volts[1] / 1.5
    at_one_height()
    plot.add_row({"time_s": 5, "V": 400.0, "I": 4.0})
    plot._redraw()
    assert shown(right) == pytest.approx(amps)  # scales set by hand stay
    item.autoBtn.clicked.emit(True)  # until the A button fits both again
    assert shown(left)[1] >= 400 and shown(right)[1] >= 4
    at_one_height()
    # without 0 aligned, the right axis zooms its own view as before
    plot.align_zero.setChecked(False)
    before = plot.right_view.viewRange()[1]
    right.wheelEvent(_Wheel(right, -240))
    assert plot.right_view.viewRange()[1] != pytest.approx(before)
    plot.close()


def test_align_zero_is_kept_per_project(app, tmp_path):
    first, second = create_project(tmp_path / "a"), create_project(tmp_path / "b")
    window = MainWindow(settings(tmp_path))
    window.open_project(first.path)
    window.plot.align_zero.setChecked(True)
    window.open_project(second.path)
    assert not window.plot.align_zero.isChecked()
    window.open_project(first.path)
    assert window.plot.align_zero.isChecked()
    window.close()
