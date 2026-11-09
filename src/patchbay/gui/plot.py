"""Live plot of the rows a test logs."""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

# Categorical colours that stay distinguishable on light and dark backgrounds.
COLORS = ["#3b82f6", "#f97316", "#10b981", "#e11d48", "#8b5cf6", "#eab308", "#06b6d4", "#ec4899"]


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class PlotPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self._curves: dict[str, pg.PlotDataItem] = {}

        self.x_axis = QComboBox()
        self.x_axis.setMinimumWidth(120)
        self.x_axis.currentTextChanged.connect(self._redraw)
        top = QHBoxLayout()
        top.addWidget(QLabel("X axis"))
        top.addWidget(self.x_axis)
        top.addStretch()

        palette = self.palette()
        text = palette.text().color()
        self.plot = pg.PlotWidget(background=palette.base().color())
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        for name in ("left", "bottom"):
            axis = self.plot.getAxis(name)
            axis.setPen(text)
            axis.setTextPen(text)
        self.legend = self.plot.addLegend(offset=(10, 10), labelTextColor=text)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(top)
        layout.addWidget(self.plot)

        self._timer = QTimer(self, singleShot=True, interval=100)
        self._timer.timeout.connect(self._redraw)

    def clear(self) -> None:
        self.rows.clear()
        for curve in self._curves.values():
            self.plot.removeItem(curve)
        self._curves.clear()
        self.legend.clear()
        self.x_axis.blockSignals(True)
        self.x_axis.clear()
        self.x_axis.blockSignals(False)

    def add_row(self, row: dict) -> None:
        self.rows.append(row)
        for key, value in row.items():
            if _is_number(value) and self.x_axis.findText(key) < 0:
                self.x_axis.blockSignals(True)
                self.x_axis.addItem(key)
                self.x_axis.blockSignals(False)
        if not self._timer.isActive():
            self._timer.start()

    def _redraw(self) -> None:
        x_key = self.x_axis.currentText()
        self.plot.setLabel("bottom", x_key)
        columns = [self.x_axis.itemText(i) for i in range(self.x_axis.count())]
        for key in columns:
            if key == x_key:
                if key in self._curves:
                    self.plot.removeItem(self._curves.pop(key))
                continue
            points = [(r[x_key], r[key]) for r in self.rows
                      if _is_number(r.get(x_key)) and _is_number(r.get(key))]
            if key not in self._curves:
                color = COLORS[columns.index(key) % len(COLORS)]
                self._curves[key] = self.plot.plot(
                    name=key, pen=pg.mkPen(color, width=2),
                    symbol="o", symbolSize=5, symbolBrush=color, symbolPen=color,
                )
            self._curves[key].setData([p[0] for p in points], [p[1] for p in points])
