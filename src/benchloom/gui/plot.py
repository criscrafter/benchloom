# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Live plot of the rows a test logs.

Columns are drawn against the column chosen as X axis, on the left value scale
or, when ticked under "Right axis", on a second scale on the right. That keeps
columns of very different size readable together, such as 230 V and 10 W.
"Columns" hides curves, and "Log X" puts the X axis on a log scale, for
frequency sweeps. "Align 0" puts 0 of both scales at the same height, so a
reading on the left and one on the right cross zero at the same place.
Another run can be drawn next to the current one, dashed, for comparison
(see :meth:`PlotPanel.set_reference`).
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QTransform
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget,
)

from .theme import PALETTE_EVENTS

# Categorical colours that stay distinguishable on light and dark backgrounds.
COLORS = ["#3b82f6", "#f97316", "#10b981", "#e11d48", "#8b5cf6", "#eab308", "#06b6d4", "#ec4899"]


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def aligned_ranges(left: list[float], right: list[float],
                   pad: float = 0.02) -> tuple[tuple[float, float], tuple[float, float]]:
    """Y ranges for the left and right scale that hold their values and 0, with 0
    at the same height on both."""
    spans = []
    for ys in (left, right):
        lo, hi = min(min(ys), 0.0), max(max(ys), 0.0)
        if hi == lo:
            hi = 1.0  # only zeros
        spans.append((lo, hi))
    below = [-lo / (hi - lo) for lo, hi in spans]  # share of each scale below 0
    if all(f == 1.0 for f in below):
        share = 1.0
    elif all(f == 0.0 for f in below):
        share = 0.0
    else:
        share = max(f for f in below if f < 1.0)
        if share == 0.0:  # one scale only below 0, the other only above
            share = 0.5
    out = []
    for lo, hi in spans:
        size = max(-lo / share if share > 0 else 0.0, hi / (1 - share) if share < 1 else 0.0)
        out.append((-(share + pad) * size, (1 - share + pad) * size))
    return out[0], out[1]


class ZeroAxis(pg.AxisItem):
    """A value axis that, while 0 is aligned, scales its own scale around 0 on the
    wheel or a right-button drag, instead of the view shared by both scales."""

    def __init__(self, orientation: str):
        super().__init__(orientation)
        self.scale_around_zero = None  # set by the plot: (orientation, factor) -> handled

    def _scaled(self, factor: float) -> bool:
        return bool(self.scale_around_zero and self.scale_around_zero(self.orientation, factor))

    def wheelEvent(self, event):
        lv = self.linkedView()
        if lv is not None and not lv.sceneBoundingRect().contains(event.scenePos()) \
                and self._scaled(1.02 ** (event.delta() * lv.state["wheelScaleFactor"])):
            event.accept()
            return
        super().wheelEvent(event)

    def mouseDragEvent(self, event):
        lv = self.linkedView()
        if lv is not None and event.button() == Qt.MouseButton.RightButton \
                and not lv.sceneBoundingRect().contains(event.buttonDownScenePos()):
            if self._scaled(1.02 ** (event.screenPos().y() - event.lastScreenPos().y())):
                event.accept()
                return
        super().mouseDragEvent(event)


def style_plot(plot: pg.PlotWidget, palette, axes=("left", "bottom")) -> None:
    """Colour a plot's background, axes and grid from a Qt palette."""
    text = palette.text().color()
    plot.setBackground(palette.base().color())
    for name in axes:
        axis = plot.getAxis(name)
        axis.setPen(text)
        axis.setTextPen(text)


class PlotPanel(QWidget):
    right_columns_changed = Signal(list)  # the columns on the right scale
    log_x_changed = Signal(bool)
    align_zero_changed = Signal(bool)
    hidden_columns_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.right_columns: set[str] = set()
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._curve_side: dict[str, str] = {}
        self.preferred_x: str | None = None  # chosen as X axis as soon as it is logged
        self.hidden: set[str] = set()  # columns not drawn (the project's [plot] hide)
        self.reference: list[dict] = []  # rows of another run, drawn dashed
        self.reference_label = ""
        self._ref_curves: dict[str, tuple[pg.PlotDataItem, str]] = {}
        self._zero_ratio = 1.0  # right scale / left scale while 0 is aligned

        self.x_axis = QComboBox()
        self.x_axis.setMinimumWidth(120)
        self.x_axis.currentTextChanged.connect(self._redraw)
        self.right_menu = QMenu(self)
        self.right_button = QToolButton(text="Right axis")
        self.right_button.setToolTip("Columns shown on a second scale on the right")
        self.right_button.setMenu(self.right_menu)
        self.right_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.columns_menu = QMenu(self)
        self.columns_button = QToolButton(text="Columns")
        self.columns_button.setToolTip("The columns drawn")
        self.columns_button.setMenu(self.columns_menu)
        self.columns_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.log_x = QCheckBox("Log X")
        self.log_x.setToolTip("Show the X axis on a log scale")
        self.log_x.toggled.connect(self._log_x_toggled)
        self.align_zero = QCheckBox("Align 0")
        self.align_zero.setToolTip("Put 0 of the left and the right scale at the same height")
        self.align_zero.toggled.connect(self._align_zero_toggled)
        top = QHBoxLayout()
        top.addWidget(QLabel("X axis"))
        top.addWidget(self.x_axis)
        top.addSpacing(12)
        top.addWidget(self.right_button)
        top.addSpacing(12)
        top.addWidget(self.columns_button)
        top.addSpacing(12)
        top.addWidget(self.log_x)
        top.addSpacing(12)
        top.addWidget(self.align_zero)
        top.addStretch()

        self.plot = pg.PlotWidget(axisItems={side: ZeroAxis(side) for side in ("left", "right")})
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        item = self.plot.getPlotItem()
        self.right_view = pg.ViewBox()
        item.showAxis("right")
        item.scene().addItem(self.right_view)
        item.getAxis("right").linkToView(self.right_view)
        self.right_view.setXLink(item)
        item.vb.sigResized.connect(self._sync_views)
        for side in ("left", "right"):
            item.getAxis(side).scale_around_zero = self._scale_around_zero
        item.autoBtn.clicked.connect(lambda *_: self._fit_zero())  # after the plot's own
        self.legend = self.plot.addLegend(offset=(10, 10))
        self._apply_palette()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(top)
        layout.addWidget(self.plot)

        self._timer = QTimer(self, singleShot=True, interval=100)
        self._timer.timeout.connect(self._redraw)
        self._update_right_axis()

    # --- appearance -------------------------------------------------------
    def changeEvent(self, event) -> None:
        if event.type() in PALETTE_EVENTS:
            self._apply_palette()
        super().changeEvent(event)

    def _apply_palette(self) -> None:
        style_plot(self.plot, self.palette(), ("left", "bottom", "right"))
        self.legend.setLabelTextColor(self.palette().text().color())
        for _, label in self.legend.items:  # existing labels only redraw on setText
            label.setText(label.text)

    def _sync_views(self) -> None:
        item = self.plot.getPlotItem()
        self.right_view.setGeometry(item.vb.sceneBoundingRect())
        self.right_view.linkedViewChanged(item.vb, self.right_view.XAxis)

    # --- log X ------------------------------------------------------------
    def set_log_x(self, on: bool) -> None:
        self.log_x.blockSignals(True)
        self.log_x.setChecked(on)
        self.log_x.blockSignals(False)
        self._apply_log_x()

    def _log_x_toggled(self, on: bool) -> None:
        self._apply_log_x()
        self.log_x_changed.emit(on)

    def _apply_log_x(self) -> None:
        on = self.log_x.isChecked()
        # Curves live in our own view boxes, so each is told, not only the axis.
        self.plot.getPlotItem().setLogMode(x=on, y=False)
        for curve in [*self._curves.values(), *(c for c, _ in self._ref_curves.values())]:
            curve.setLogMode(on, False)
        self.plot.getPlotItem().vb.enableAutoRange()
        self.right_view.enableAutoRange()

    # --- 0 aligned on both scales ----------------------------------------
    def set_align_zero(self, on: bool) -> None:
        self.align_zero.blockSignals(True)
        self.align_zero.setChecked(on)
        self.align_zero.blockSignals(False)
        self._apply_align_zero()

    def _align_zero_toggled(self, on: bool) -> None:
        self._apply_align_zero()
        self.align_zero_changed.emit(on)

    def _apply_align_zero(self) -> None:
        """With 0 aligned, the right curves are drawn in the left view, squeezed by
        the ratio of the scales, and the right axis labels that view times the
        ratio. One view means zoom and pan can never pull the scales apart.
        The curves are taken out here and added again by _redraw where they belong."""
        for key in list(self._curves):
            self._remove_curve(key)
        for key in list(self._ref_curves):
            self._remove_ref_curve(key)
        on = self.align_zero.isChecked()
        item = self.plot.getPlotItem()
        self.right_view.setVisible(not on)  # else it would take the mouse
        self._sync_views()
        item.getAxis("right").linkToView(item.vb if on else self.right_view)
        self._set_zero_ratio(1.0)
        self._redraw()
        item.vb.enableAutoRange()
        self.right_view.enableAutoRange()
        self._fit_zero()

    def _side_values(self, side: str) -> list[float]:
        curves = [c for k, c in self._curves.items() if self._curve_side[k] == side]
        curves += [c for c, s in self._ref_curves.values() if s == side]
        values = []  # the lowest and highest of each curve are enough
        for curve in curves:
            _, ys = curve.getData()
            if ys is not None and len(ys) and not np.isnan(ys).all():
                values += [float(np.nanmin(ys)), float(np.nanmax(ys))]
        return values

    def _fit_zero(self) -> None:
        """Choose the ratio of the scales so that both sides fill the plot. Only
        while the plot fits itself to the data: never under a zoom set by hand."""
        main = self.plot.getPlotItem().vb
        if not self.align_zero.isChecked() or not main.state["autoRange"][1]:
            return
        left, right = self._side_values("left"), self._side_values("right")
        if left and right:
            (l_lo, l_hi), (r_lo, r_hi) = aligned_ranges(left, right)
            self._set_zero_ratio((r_hi - r_lo) / (l_hi - l_lo))
            main.updateAutoRange()

    def _scale_around_zero(self, side: str, factor: float) -> bool:
        """Scale one scale by this factor around 0, leaving the other as it is.
        Only while 0 is aligned; otherwise the axis zooms as usual."""
        if not self.align_zero.isChecked():
            return False
        main = self.plot.getPlotItem().vb
        main.enableAutoRange(y=False)  # a scale set by hand stays until the A button
        if side == "right":
            self._set_zero_ratio(self._zero_ratio * factor)
        else:  # the view grows, the right scale shrinks by as much: it stays put
            self._set_zero_ratio(self._zero_ratio / factor)
            main.scaleBy(y=factor, center=QPointF(0, 0))
        return True

    def _set_zero_ratio(self, ratio: float) -> None:
        on = self.align_zero.isChecked()
        self._zero_ratio = ratio if on else 1.0
        squeeze = QTransform.fromScale(1.0, 1.0 / self._zero_ratio)
        for key, curve in self._curves.items():
            if self._curve_side[key] == "right":
                curve.setTransform(squeeze)
        for curve, side in self._ref_curves.values():
            if side == "right":
                curve.setTransform(squeeze)
        self.plot.getPlotItem().getAxis("right").setScale(self._zero_ratio)

    # --- right axis -------------------------------------------------------
    def set_right_columns(self, columns) -> None:
        self.right_columns = set(columns)
        self._rebuild_right_menu()
        self._redraw()

    def _columns(self) -> list[str]:
        return [self.x_axis.itemText(i) for i in range(self.x_axis.count())]

    def _rebuild_right_menu(self) -> None:
        """Rebuild the Right axis and Columns menus from the logged columns."""
        self.right_menu.clear()
        self.columns_menu.clear()
        x_key = self.x_axis.currentText()
        for key in self._columns():
            if key == x_key:
                continue
            shown = QAction(key, self.columns_menu, checkable=True)
            shown.setChecked(key not in self.hidden)
            shown.toggled.connect(lambda on, key=key: self._toggle_shown(key, on))
            self.columns_menu.addAction(shown)
            if key in self.hidden:
                continue
            action = QAction(key, self.right_menu, checkable=True)
            action.setChecked(key in self.right_columns)
            action.toggled.connect(lambda on, key=key: self._toggle_right(key, on))
            self.right_menu.addAction(action)
        self.right_button.setEnabled(bool(self.right_menu.actions()))
        self.columns_button.setEnabled(bool(self.columns_menu.actions()))

    def set_hidden_columns(self, columns) -> None:
        self.hidden = set(columns)
        self._rebuild_right_menu()
        self._redraw()

    def _toggle_shown(self, key: str, on: bool) -> None:
        if on:
            self.hidden.discard(key)
        else:
            self.hidden.add(key)
        self._rebuild_right_menu()
        self._redraw()
        self.hidden_columns_changed.emit(sorted(self.hidden))

    def _toggle_right(self, key: str, on: bool) -> None:
        if on:
            self.right_columns.add(key)
        else:
            self.right_columns.discard(key)
        self._redraw()
        self.right_columns_changed.emit(sorted(self.right_columns))

    def _update_right_axis(self) -> None:
        item = self.plot.getPlotItem()
        right = [k for k, side in self._curve_side.items() if side == "right"]
        left = [k for k, side in self._curve_side.items() if side == "left"]
        item.getAxis("right").setStyle(showValues=bool(right))
        item.getAxis("right").setLabel(", ".join(right) if right else None)
        item.getAxis("left").setLabel(", ".join(left) if right and left else None)
        self.align_zero.setEnabled(bool(right and left) or self.align_zero.isChecked())
        self._fit_zero()

    # --- data -------------------------------------------------------------
    def clear(self) -> None:
        """Forget the rows; the right-axis choice and a run to compare with stay, for
        the next run."""
        self.rows.clear()
        for key in list(self._curves):
            self._remove_curve(key)
        self.x_axis.blockSignals(True)
        self.x_axis.clear()
        self.x_axis.blockSignals(False)
        self._add_columns(self.reference[:1])
        self._rebuild_right_menu()
        self._update_right_axis()
        self._redraw()

    def set_reference(self, rows: list[dict], label: str = "") -> None:
        """Draw another run's rows dashed, in the colours of the current curves.
        Empty rows remove it."""
        for key in list(self._ref_curves):
            self._remove_ref_curve(key)
        self.reference, self.reference_label = list(rows), label
        if self._add_columns(self.reference[:1]):
            self._rebuild_right_menu()
        self._redraw()

    def _add_columns(self, rows: list[dict]) -> bool:
        """Offer the numeric columns of these rows as X axis. True if any were new."""
        added = False
        for row in rows:
            for key, value in row.items():
                if _is_number(value) and self.x_axis.findText(key) < 0:
                    self.x_axis.blockSignals(True)
                    self.x_axis.addItem(key)
                    self.x_axis.blockSignals(False)
                    added = True
        if added and self.preferred_x and self.x_axis.findText(self.preferred_x) >= 0 \
                and self.x_axis.currentText() != self.preferred_x:
            self.x_axis.blockSignals(True)
            self.x_axis.setCurrentText(self.preferred_x)
            self.x_axis.blockSignals(False)
        return added

    def _remove_ref_curve(self, key: str) -> None:
        curve, side = self._ref_curves.pop(key)
        self.legend.removeItem(curve)
        curve.getViewBox().removeItem(curve)

    def add_row(self, row: dict) -> None:
        self.rows.append(row)
        if self._add_columns([row]):
            self._rebuild_right_menu()
        if not self._timer.isActive():
            self._timer.start()

    def _ref_legend_last(self) -> None:
        """Keep the dashed curves of the other run below the current ones in the legend."""
        for curve, _ in self._ref_curves.values():
            label = curve.opts.get("name") or ""
            self.legend.removeItem(curve)
            self.legend.addItem(curve, label)

    def _draw_reference(self, key: str, x_key: str, side: str, color: str) -> None:
        points = [(r[x_key], r[key]) for r in self.reference
                  if _is_number(r.get(x_key)) and _is_number(r.get(key))]
        if not points:
            if key in self._ref_curves:
                self._remove_ref_curve(key)
            return
        if key not in self._ref_curves:
            label = f"{key} ({self.reference_label or 'other run'})"
            curve = pg.PlotDataItem(name=label,
                                    pen=pg.mkPen(color, width=1.5, style=Qt.PenStyle.DashLine))
            curve.setLogMode(self.log_x.isChecked(), False)
            self._squeeze(curve, side)
            self._view(side).addItem(curve)
            self.legend.addItem(curve, label)
            self._ref_curves[key] = (curve, side)
        self._ref_curves[key][0].setData([p[0] for p in points], [p[1] for p in points])

    def _view(self, side: str) -> pg.ViewBox:
        if side == "right" and not self.align_zero.isChecked():
            return self.right_view
        return self.plot.getPlotItem().vb

    def _squeeze(self, curve: pg.PlotDataItem, side: str) -> None:
        if side == "right" and self.align_zero.isChecked():
            curve.setTransform(QTransform.fromScale(1.0, 1.0 / self._zero_ratio))

    def _remove_curve(self, key: str) -> None:
        curve = self._curves.pop(key)
        self.legend.removeItem(curve)
        self._curve_side.pop(key)
        curve.getViewBox().removeItem(curve)

    def _redraw(self) -> None:
        x_key = self.x_axis.currentText()
        self.plot.setLabel("bottom", x_key)
        columns = self._columns()
        if set(a.text() for a in self.columns_menu.actions()) != set(columns) - {x_key}:
            self._rebuild_right_menu()
        for key in columns:
            side = "right" if key in self.right_columns else "left"
            if key in self._curves and (key == x_key or self._curve_side[key] != side):
                self._remove_curve(key)
            if key in self._ref_curves and (key == x_key or key in self.hidden
                                            or self._ref_curves[key][1] != side):
                self._remove_ref_curve(key)
            if key == x_key or key in self.hidden:
                if key in self._curves:
                    self._remove_curve(key)
                continue
            color = COLORS[columns.index(key) % len(COLORS)]
            self._draw_reference(key, x_key, side, color)
            points = [(r[x_key], r[key]) for r in self.rows
                      if _is_number(r.get(x_key)) and _is_number(r.get(key))]
            if not points and key not in self._curves:
                continue  # only the run compared with has this column
            if key not in self._curves:
                curve = pg.PlotDataItem(
                    name=key, pen=pg.mkPen(color, width=2),
                    symbol="o", symbolSize=5, symbolBrush=color, symbolPen=color,
                )
                curve.setLogMode(self.log_x.isChecked(), False)
                self._squeeze(curve, side)
                self._view(side).addItem(curve)  # not PlotItem.addItem: legend is ours
                self.legend.addItem(curve, f"{key} (right)" if side == "right" else key)
                self._curves[key], self._curve_side[key] = curve, side
                self._ref_legend_last()
            self._curves[key].setData([p[0] for p in points], [p[1] for p in points])
        self._update_right_axis()
