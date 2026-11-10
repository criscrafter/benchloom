"""Live plot of the rows a test logs.

Columns are drawn against the column chosen as X axis, on the left value scale
or, when ticked under "Right axis", on a second scale on the right. That keeps
columns of very different size readable together, such as 230 V and 10 W.
"Log X" puts the X axis on a log scale, for frequency sweeps.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget,
)

from .theme import PALETTE_EVENTS

# Categorical colours that stay distinguishable on light and dark backgrounds.
COLORS = ["#3b82f6", "#f97316", "#10b981", "#e11d48", "#8b5cf6", "#eab308", "#06b6d4", "#ec4899"]


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.right_columns: set[str] = set()
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._curve_side: dict[str, str] = {}
        self.preferred_x: str | None = None  # chosen as X axis as soon as it is logged
        self.hidden: set[str] = set()  # columns not drawn (the project's [plot] hide)

        self.x_axis = QComboBox()
        self.x_axis.setMinimumWidth(120)
        self.x_axis.currentTextChanged.connect(self._redraw)
        self.right_menu = QMenu(self)
        self.right_button = QToolButton(text="Right axis")
        self.right_button.setToolTip("Columns shown on a second scale on the right")
        self.right_button.setMenu(self.right_menu)
        self.right_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.log_x = QCheckBox("Log X")
        self.log_x.setToolTip("Show the X axis on a log scale")
        self.log_x.toggled.connect(self._log_x_toggled)
        top = QHBoxLayout()
        top.addWidget(QLabel("X axis"))
        top.addWidget(self.x_axis)
        top.addSpacing(12)
        top.addWidget(self.right_button)
        top.addSpacing(12)
        top.addWidget(self.log_x)
        top.addStretch()

        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        item = self.plot.getPlotItem()
        self.right_view = pg.ViewBox()
        item.showAxis("right")
        item.scene().addItem(self.right_view)
        item.getAxis("right").linkToView(self.right_view)
        self.right_view.setXLink(item)
        item.vb.sigResized.connect(self._sync_views)
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
        for curve in self._curves.values():
            curve.setLogMode(on, False)
        self.plot.getPlotItem().vb.enableAutoRange()
        self.right_view.enableAutoRange()

    # --- right axis -------------------------------------------------------
    def set_right_columns(self, columns) -> None:
        self.right_columns = set(columns)
        self._rebuild_right_menu()
        self._redraw()

    def _columns(self) -> list[str]:
        return [self.x_axis.itemText(i) for i in range(self.x_axis.count())]

    def _rebuild_right_menu(self) -> None:
        self.right_menu.clear()
        x_key = self.x_axis.currentText()
        for key in self._columns():
            if key == x_key:
                continue
            action = QAction(key, self.right_menu, checkable=True)
            action.setChecked(key in self.right_columns)
            action.toggled.connect(lambda on, key=key: self._toggle_right(key, on))
            self.right_menu.addAction(action)
        self.right_button.setEnabled(bool(self.right_menu.actions()))

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

    # --- data -------------------------------------------------------------
    def clear(self) -> None:
        """Forget the rows; the right-axis choice stays, for the next run."""
        self.rows.clear()
        for key in list(self._curves):
            self._remove_curve(key)
        self.x_axis.blockSignals(True)
        self.x_axis.clear()
        self.x_axis.blockSignals(False)
        self._rebuild_right_menu()
        self._update_right_axis()

    def add_row(self, row: dict) -> None:
        self.rows.append(row)
        added = False
        for key, value in row.items():
            if _is_number(value) and self.x_axis.findText(key) < 0:
                self.x_axis.blockSignals(True)
                self.x_axis.addItem(key)
                self.x_axis.blockSignals(False)
                added = True
        if added:
            if self.preferred_x and self.x_axis.findText(self.preferred_x) >= 0                     and self.x_axis.currentText() != self.preferred_x:
                self.x_axis.blockSignals(True)
                self.x_axis.setCurrentText(self.preferred_x)
                self.x_axis.blockSignals(False)
            self._rebuild_right_menu()
        if not self._timer.isActive():
            self._timer.start()

    def _view(self, side: str) -> pg.ViewBox:
        return self.right_view if side == "right" else self.plot.getPlotItem().vb

    def _remove_curve(self, key: str) -> None:
        curve = self._curves.pop(key)
        self.legend.removeItem(curve)
        self._view(self._curve_side.pop(key)).removeItem(curve)

    def _redraw(self) -> None:
        x_key = self.x_axis.currentText()
        self.plot.setLabel("bottom", x_key)
        columns = self._columns()
        if set(a.text() for a in self.right_menu.actions()) != set(columns) - {x_key}:
            self._rebuild_right_menu()
        for key in columns:
            side = "right" if key in self.right_columns else "left"
            if key in self._curves and (key == x_key or self._curve_side[key] != side):
                self._remove_curve(key)
            if key == x_key or key in self.hidden:
                if key in self._curves:
                    self._remove_curve(key)
                continue
            points = [(r[x_key], r[key]) for r in self.rows
                      if _is_number(r.get(x_key)) and _is_number(r.get(key))]
            if key not in self._curves:
                color = COLORS[columns.index(key) % len(COLORS)]
                curve = pg.PlotDataItem(
                    name=key, pen=pg.mkPen(color, width=2),
                    symbol="o", symbolSize=5, symbolBrush=color, symbolPen=color,
                )
                curve.setLogMode(self.log_x.isChecked(), False)
                self._view(side).addItem(curve)  # not PlotItem.addItem: legend is ours
                self.legend.addItem(curve, f"{key} (right)" if side == "right" else key)
                self._curves[key], self._curve_side[key] = curve, side
            self._curves[key].setData([p[0] for p in points], [p[1] for p in points])
        self._update_right_axis()
