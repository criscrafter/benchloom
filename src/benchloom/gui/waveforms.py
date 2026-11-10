# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The waveforms a running test saves with ``save_waveform``, to look through.

The newest is shown as it arrives; picking one in the list shows that one. The
same traces are in the run folder as CSV, SVG and PNG.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QListWidget, QSplitter, QVBoxLayout, QWidget

from ..svgplot import describe, time_unit
from .plot import COLORS, style_plot
from .theme import PALETTE_EVENTS


class WaveformPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.waveforms: list[tuple[str, dict]] = []
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", "Voltage", "V")
        self.legend = self.plot.addLegend(offset=(10, 10))
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right = QWidget()
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.plot, 1)
        column.addWidget(self.info)
        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([160, 500])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(split)
        self._apply_palette()
        self.clear()

    def changeEvent(self, event) -> None:
        if event.type() in PALETTE_EVENTS:
            self._apply_palette()
        super().changeEvent(event)

    def _apply_palette(self) -> None:
        style_plot(self.plot, self.palette())
        self.legend.setLabelTextColor(self.palette().text().color())

    def clear(self) -> None:
        self.waveforms.clear()
        self.list.clear()
        self.plot.clear()
        self.info.setText("Waveforms a test saves with save_waveform(name, traces) show here.")

    def add(self, name: str, traces: dict) -> None:
        """A waveform the test saved: name -> (times, volts) per trace."""
        self.waveforms.append((name, traces))
        self.list.addItem(name)
        self.list.setCurrentRow(self.list.count() - 1)  # follow the newest

    def _show(self, row: int) -> None:
        self.plot.clear()
        if not 0 <= row < len(self.waveforms):
            return
        name, traces = self.waveforms[row]
        scale, unit = time_unit([t for times, _ in traces.values() for t in times])
        for i, (trace, (times, volts)) in enumerate(traces.items()):
            color = COLORS[i % len(COLORS)]
            self.plot.plot([t / scale for t in times], volts, name=trace,
                           pen=pg.mkPen(color, width=2))
        self.plot.setLabel("bottom", f"Time ({unit})")
        self.plot.setTitle(name)
        self.info.setText("   ".join(f"{trace}: {describe(times, volts)}"
                                     for trace, (times, volts) in traces.items()))
