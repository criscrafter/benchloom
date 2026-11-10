"""Front panel: live controls and indicators, with an edit mode to lay them out."""

from __future__ import annotations

import time

import pyqtgraph as pg
from PySide6.QtCore import QLocale, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame,
    QHBoxLayout,
    QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QToolButton, QVBoxLayout, QWidget,
)

from .plot import style_plot
from .theme import PALETTE_EVENTS
from ..panel import CONTROLS, INDICATORS, WIDGET_TYPES, Panel, Widget, format_value, valid_name

GRID = 10
MIN_W, MIN_H = 60, 30
HANDLE = 14
EDIT_COLOR = QColor("#3b82f6")
LED_ON, LED_OFF =QColor("#22c55e"), QColor("#6b7280")


def snap(value: float) -> int:
    return int(round(value / GRID) * GRID)


def format_number(value, unit: str = "", decimals: int | None = None) -> str:
    """How a display or bar shows a value, with the decimal point of the locale."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        text = "–" if value is None else str(value)
    else:
        text = format_value(float(value), decimals, QLocale().decimalPoint())
    return f"{text} {unit}".strip()


class NumberBox(QDoubleSpinBox):
    """A spin box that shows its value like a display: no trailing zeros unless fixed."""

    def __init__(self, decimals: int | None = None):
        super().__init__()
        self.fixed = decimals
        self.setDecimals(6 if decimals is None else decimals)

    def textFromValue(self, value: float) -> str:
        locale = self.locale()
        locale.setNumberOptions(QLocale.NumberOption.OmitGroupSeparator)
        text = locale.toString(value, "f", self.decimals())
        point = locale.decimalPoint()
        if self.fixed is None and point in text:
            text = text.rstrip("0").rstrip(point)
        return text


class _Led(QWidget):
    def __init__(self):
        super().__init__()
        self.on = False
        self.setMinimumSize(22, 22)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = min(self.width(), self.height(), 28) - 4
        painter.setPen(QPen(self.palette().mid().color(), 1.5))
        painter.setBrush(LED_ON if self.on else LED_OFF)
        painter.drawEllipse(2, (self.height() - size) // 2, size, size)


class _EditOverlay(QWidget):
    """Covers a widget in edit mode: drag to move, drag the corner to resize."""

    def __init__(self, item: "PanelItem"):
        super().__init__(item)
        self.item = item
        self._press: QPoint | None = None
        self._start = QRect()
        self._resizing = False
        self.setMouseTracking(True)
        self.setToolTip("Drag to move, drag the corner to resize, double-click for settings")

    def _in_handle(self, pos: QPoint) -> bool:
        return pos.x() > self.width() - HANDLE and pos.y() > self.height() - HANDLE

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        color = EDIT_COLOR  # not the palette highlight, which fades in inactive windows
        painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.fillRect(self.width() - HANDLE, self.height() - HANDLE, HANDLE, HANDLE, color)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._start = self.item.geometry()
            self._resizing = self._in_handle(event.position().toPoint())

    def mouseMoveEvent(self, event) -> None:
        if self._press is None:
            over = self._in_handle(event.position().toPoint())
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if over else Qt.CursorShape.SizeAllCursor)
            return
        delta = event.globalPosition().toPoint() - self._press
        if self._resizing:
            self.item.resize(max(MIN_W, snap(self._start.width() + delta.x())),
                             max(MIN_H, snap(self._start.height() + delta.y())))
        else:
            self.item.move(max(0, snap(self._start.x() + delta.x())),
                           max(0, snap(self._start.y() + delta.y())))

    def mouseReleaseEvent(self, event) -> None:
        if self._press is not None:
            self._press = None
            self.item.view.geometry_edited(self.item)

    def mouseDoubleClickEvent(self, event) -> None:
        self._press = None
        self.item.view.edit_properties(self.item)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("Settings…", lambda: self.item.view.edit_properties(self.item))
        menu.addAction("Delete", lambda: self.item.view.remove(self.item))
        menu.exec(event.globalPos())


class PanelItem(QFrame):
    """One widget on the panel: an optional caption and the control or indicator."""

    def __init__(self, view: "PanelView", model: Widget):
        super().__init__(view.canvas)
        self.view, self.model = view, model
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 6)
        layout.setSpacing(2)
        if model.type not in ("button", "label"):
            caption = QLabel(model.label or model.name)
            caption.setEnabled(False)
            layout.addWidget(caption)
        self.control = self._build()
        layout.addWidget(self.control, 1)
        self.setGeometry(model.x, model.y, model.w, model.h)
        self.overlay = _EditOverlay(self)
        self.overlay.setVisible(view.editing)
        self.show()

    def changeEvent(self, event) -> None:
        if event.type() in PALETTE_EVENTS and isinstance(getattr(self, "control", None),
                                                          pg.PlotWidget):
            style_plot(self.control, self.palette())
        super().changeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.overlay.setGeometry(self.rect())
        self.overlay.raise_()

    def _changed(self, value) -> None:
        self.model.value = value
        self.view.control_changed.emit(self.model.name, value)

    def _build(self) -> QWidget:
        model, kind = self.model, self.model.type
        if kind == "number":
            box = NumberBox(model.decimals)
            box.setRange(model.min, model.max)
            box.setSuffix(f" {model.unit}" if model.unit else "")
            box.setValue(model.initial())
            box.setKeyboardTracking(False)
            box.valueChanged.connect(self._changed)
            return box
        if kind == "switch":
            button = QPushButton()
            button.setCheckable(True)
            button.setChecked(model.initial())
            button.setText("On" if button.isChecked() else "Off")
            button.toggled.connect(lambda on: (button.setText("On" if on else "Off"),
                                               self._changed(on)))
            return button
        if kind == "button":
            button = QPushButton(model.label or model.name)
            # a click is an event, not a saved value
            button.clicked.connect(
                lambda: self.view.control_changed.emit(model.name, True))
            return button
        if kind == "text":
            edit = QLineEdit(model.initial())
            edit.editingFinished.connect(lambda: self._changed(edit.text()))
            return edit
        if kind == "display":
            label = QLabel(format_number(None, model.unit, model.decimals))
            font = label.font()
            font.setPointSize(18)
            font.setBold(True)
            label.setFont(font)
            return label
        if kind == "led":
            return _Led()
        if kind == "bar":
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setFormat(format_number(None, model.unit, model.decimals))
            return bar
        if kind == "chart":
            plot = pg.PlotWidget()
            plot.showGrid(x=True, y=True, alpha=0.2)
            style_plot(plot, self.palette())
            plot.setLabel("left", model.unit or None)
            self._points: tuple[list, list] = ([], [])
            self._curve = plot.plot(pen=pg.mkPen("#3b82f6", width=2))
            return plot
        return QLabel(model.label or model.name)  # static label

    def show_value(self, value) -> None:
        """Display a value the test set (or, for a control, changed)."""
        model, kind, control = self.model, self.model.type, self.control
        if kind == "display":
            control.setText(format_number(value, model.unit, model.decimals))
        elif kind == "led":
            control.on = bool(value)
            control.update()
        elif kind == "bar" and isinstance(value, (int, float)):
            span = (model.max - model.min) or 1.0
            control.setValue(int(max(0.0, min(1.0, (value - model.min) / span)) * 1000))
            control.setFormat(format_number(value, model.unit, model.decimals))
        elif kind == "chart" and isinstance(value, (int, float)):
            xs, ys = self._points
            xs.append(self.view.elapsed())
            ys.append(value)
            self._curve.setData(xs, ys)
        elif kind in ("number", "switch", "text"):
            control.blockSignals(True)
            if kind == "number" and isinstance(value, (int, float)):
                control.setValue(value)
            elif kind == "switch":
                control.setChecked(bool(value))
                control.setText("On" if value else "Off")
            elif kind == "text":
                control.setText("" if value is None else str(value))
            control.blockSignals(False)
            model.value = value

    def reset(self) -> None:
        """Called when a run starts."""
        if self.model.type == "chart":
            self._points = ([], [])
            self._curve.setData([], [])


class WidgetDialog(QDialog):
    def __init__(self, panel: Panel, model: Widget, parent=None):
        super().__init__(parent)
        self.panel, self.model = panel, model
        self.setWindowTitle(f"{WIDGET_TYPES[model.type][0]} settings")
        self.setMinimumWidth(340)
        form = QFormLayout(self)
        kind = model.type

        self.name_edit = QLineEdit(model.name)
        self.label_edit = QLineEdit(model.label)
        self.unit_edit = QLineEdit(model.unit)
        self.min_box = self._number(model.min)
        self.max_box = self._number(model.max)
        if kind != "label":
            form.addRow("Name in the test", self.name_edit)
        form.addRow("Text" if kind in ("button", "label") else "Caption", self.label_edit)
        if kind in ("number", "display", "bar", "chart"):
            form.addRow("Unit", self.unit_edit)
        if kind in ("number", "bar"):
            form.addRow("Minimum", self.min_box)
            form.addRow("Maximum", self.max_box)
        self.decimals_box = QComboBox()
        self.decimals_box.addItem("Automatic", None)
        for n in range(10):
            self.decimals_box.addItem(str(n), n)
        self.decimals_box.setCurrentIndex(self.decimals_box.findData(model.decimals))
        self.decimals_box.setToolTip("Digits after the decimal point. Automatic shows about "
                                     "four significant digits, without trailing zeros.")
        if kind in ("number", "display", "bar"):
            form.addRow("Decimals", self.decimals_box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @staticmethod
    def _number(value: float) -> QDoubleSpinBox:
        box = NumberBox()
        box.setRange(-1e9, 1e9)
        box.setValue(value)
        return box

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        if not valid_name(name):
            QMessageBox.warning(self, "Name", "The name must start with a letter and contain "
                                "only letters, digits and underscores.")
            return
        other = self.panel.get(name)
        if other is not None and other is not self.model:
            QMessageBox.warning(self, "Name", f"There is already a widget named '{name}'.")
            return
        if self.max_box.value() <= self.min_box.value():
            QMessageBox.warning(self, "Range", "The maximum must be above the minimum.")
            return
        model = self.model
        model.name, model.label = name, self.label_edit.text().strip()
        model.unit = self.unit_edit.text().strip()
        model.min, model.max = self.min_box.value(), self.max_box.value()
        model.decimals = self.decimals_box.currentData()
        super().accept()


class PanelView(QWidget):
    control_changed = Signal(str, object)  # the user changed a control
    edited = Signal()  # widgets were added, removed, renamed or moved

    def __init__(self, parent=None):
        super().__init__(parent)
        self.panel: Panel | None = None
        self.items: dict[str, PanelItem] = {}
        self.editing = False
        self._run_started = time.monotonic()

        self.edit_box = QCheckBox("Edit layout")
        self.edit_box.toggled.connect(self.set_editing)
        self.add_button = QToolButton()
        self.add_button.setText("Add")
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.add_button)
        for title, kinds in (("Controls", CONTROLS), ("Indicators", INDICATORS),
                             ("Other", ("label",))):
            menu.addSection(title)
            for kind in kinds:
                menu.addAction(WIDGET_TYPES[kind][0], lambda k=kind: self.add(k))
        self.add_button.setMenu(menu)
        self.hint = QLabel()
        self.hint.setEnabled(False)
        bar = QHBoxLayout()
        bar.addWidget(self.edit_box)
        bar.addWidget(self.add_button)
        bar.addWidget(self.hint, 1)

        self.canvas = QWidget()
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setWidgetResizable(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(bar)
        layout.addWidget(self.scroll, 1)
        self.set_panel(None)

    # --- content ----------------------------------------------------------------
    def set_panel(self, panel: Panel | None) -> None:
        self.panel = panel
        for item in self.items.values():
            item.deleteLater()
        self.items = {}
        if panel:
            for model in panel.widgets:
                self.items[model.name] = PanelItem(self, model)
        self.edit_box.setEnabled(panel is not None)
        self.edit_box.setChecked(False)
        self.set_editing(False)
        self._refresh()

    def _refresh(self) -> None:
        self.add_button.setEnabled(self.editing)
        right = max((i.model.x + i.model.w for i in self.items.values()), default=0)
        bottom = max((i.model.y + i.model.h for i in self.items.values()), default=0)
        extra = 200 if self.editing else 10  # room to drag into while editing
        self.canvas.setMinimumSize(0, 0)
        self.canvas.resize(QSize(right + extra, bottom + extra).expandedTo(QSize(300, 200)))
        if self.items:
            self.hint.setText("" if not self.editing else "Drag to arrange. Double-click for settings.")
        else:
            self.hint.setText("Tick Edit layout, then Add, to build a front panel.")

    def set_editing(self, on: bool) -> None:
        self.editing = on
        for item in self.items.values():
            item.overlay.setVisible(on)
            item.overlay.raise_()
        self._refresh()

    def _commit(self) -> None:
        self.panel.save()
        self._refresh()
        self.edited.emit()

    def add(self, kind: str) -> PanelItem:
        model = self.panel.add(kind)
        item = self.items[model.name] = PanelItem(self, model)
        self._commit()
        self.scroll.ensureWidgetVisible(item)
        return item

    def remove(self, item: PanelItem) -> None:
        self.panel.widgets.remove(item.model)
        del self.items[item.model.name]
        item.deleteLater()
        self._commit()

    def geometry_edited(self, item: PanelItem) -> None:
        geometry = item.geometry()
        model = item.model
        model.x, model.y, model.w, model.h = (geometry.x(), geometry.y(),
                                              geometry.width(), geometry.height())
        self._commit()

    def edit_properties(self, item: PanelItem) -> None:
        if WidgetDialog(self.panel, item.model, self).exec():
            self.rebuild(item)

    def rebuild(self, item: PanelItem) -> PanelItem:
        """Recreate a widget after its settings changed."""
        self.items = {name: i for name, i in self.items.items() if i is not item}
        item.deleteLater()
        new = self.items[item.model.name] = PanelItem(self, item.model)
        self._commit()
        return new

    # --- live values --------------------------------------------------------------
    def control_values(self) -> dict:
        return self.panel.control_values() if self.panel else {}

    def elapsed(self) -> float:
        return time.monotonic() - self._run_started

    def start_run(self) -> None:
        self._run_started = time.monotonic()
        for item in self.items.values():
            item.reset()

    def show_value(self, name: str, value) -> None:
        item = self.items.get(name)
        if item is not None:
            item.show_value(value)
