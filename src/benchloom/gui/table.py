# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The rows a test logs, as a table next to the plot.

Columns appear in the order the test first logs them, so a row that adds a new
column widens the table. New rows arrive in batches a few times a second, which
keeps the table fast at high logging rates. While the last row is in view the
table follows the newest one; scrolling up stops that until you scroll back
down. Ctrl+C copies the selected cells as tab-separated text, which pastes into
a spreadsheet as cells; numbers are copied with their full precision.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QTableView, QVBoxLayout, QWidget,
)

from ..panel import format_value


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def cell_text(value) -> str:
    """How a value shows in the table: short numbers, like the plot's labels."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if _is_number(value):
        return format_value(value)
    return str(value)


def copy_text(value) -> str:
    """How a value is copied: numbers in full, so nothing is lost on the way."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return str(value).replace("\t", " ").replace("\n", " ")


class RowModel(QAbstractTableModel):
    """Rows as dicts. ``order`` lists columns that come first, in that order; the
    others follow as they first appear. Lua tables have no order of their own."""

    def __init__(self, parent=None, order: list[str] | None = None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.columns: list[str] = []
        self.order = list(order or [])

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def value(self, row: int, column: int):
        return self.rows[row].get(self.columns[column])

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        value = self.value(index.row(), index.column())
        if role == Qt.ItemDataRole.DisplayRole:
            return cell_text(value)
        if role == Qt.ItemDataRole.ToolTipRole and _is_number(value):
            return copy_text(value)
        if role == Qt.ItemDataRole.TextAlignmentRole and _is_number(value):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.columns[section] if section < len(self.columns) else None
        return str(section + 1)

    def clear(self) -> None:
        self.beginResetModel()
        self.rows.clear()
        self.columns.clear()
        self.endResetModel()

    def extend(self, rows: list[dict]) -> None:
        new = [key for row in rows for key in row if key not in self.columns]
        new = list(dict.fromkeys(new))
        if new and self.order:
            new.sort(key=lambda k: self.order.index(k) if k in self.order else len(self.order))
        if new:
            self.beginInsertColumns(QModelIndex(), len(self.columns),
                                    len(self.columns) + len(new) - 1)
            self.columns.extend(new)
            self.endInsertColumns()
        if rows:
            self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(rows) - 1)
            self.rows.extend(rows)
            self.endInsertRows()


class DataTable(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = RowModel(self)
        self._pending: list[dict] = []
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setDefaultSectionSize(
            self.view.fontMetrics().height() + 6)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        copy = QAction("Copy", self.view, shortcut=QKeySequence.StandardKey.Copy,
                       shortcutContext=Qt.ShortcutContext.WidgetShortcut)
        copy.triggered.connect(self.copy_selection)
        copy_all = QAction("Copy all rows", self.view)
        copy_all.triggered.connect(self.copy_all)
        self.view.addActions([copy, copy_all])
        self.status = QLabel()
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.status, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)
        self._timer = QTimer(self, singleShot=True, interval=150)
        self._timer.timeout.connect(self.flush)
        self.clear()

    @property
    def rows(self) -> list[dict]:
        return self.model.rows

    def clear(self) -> None:
        self._pending.clear()
        self.model.clear()
        self._update_status()

    def add_row(self, row: dict) -> None:
        self._pending.append(row)
        if not self._timer.isActive():
            self._timer.start()

    def flush(self) -> None:
        """Show the rows that arrived since the last batch."""
        if not self._pending:
            return
        bar = self.view.verticalScrollBar()
        follow = bar.value() >= bar.maximum()
        fresh = not self.model.columns
        rows, self._pending = self._pending, []
        self.model.extend(rows)
        if fresh:
            self.view.resizeColumnsToContents()
        if follow:
            self.view.scrollToBottom()
        self._update_status()

    def _update_status(self) -> None:
        count = len(self.model.rows)
        self.status.setText("Rows a test logs with log { … } show here." if not count
                            else f"{count} row{'s' if count != 1 else ''}")

    def selection_text(self, indexes=None) -> str:
        """The selected cells as tab-separated lines, with a header row."""
        if indexes is None:
            indexes = self.view.selectionModel().selectedIndexes()
        if not indexes:
            return ""
        rows = sorted({i.row() for i in indexes})
        columns = sorted({i.column() for i in indexes})
        chosen = {(i.row(), i.column()) for i in indexes}
        lines = ["\t".join(self.model.columns[c] for c in columns)]
        for r in rows:
            lines.append("\t".join(copy_text(self.model.value(r, c)) if (r, c) in chosen else ""
                                   for c in columns))
        return "\n".join(lines) + "\n"

    def copy_selection(self) -> None:
        text = self.selection_text()
        if text:
            QGuiApplication.clipboard().setText(text)

    def copy_all(self) -> None:
        self.flush()
        model = self.model
        indexes = [model.index(r, c) for r in range(model.rowCount())
                   for c in range(model.columnCount())]
        text = self.selection_text(indexes)
        if text:
            QGuiApplication.clipboard().setText(text)
