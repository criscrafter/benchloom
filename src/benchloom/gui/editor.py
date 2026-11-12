# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Lua editor with line numbers, syntax colours, autocomplete, and markers for the
line a running test is at and the line it failed on.

Autocomplete offers the project's devices and Benchloom's functions as you type a
name, a device's commands with their arguments after ``psu:``, and the front panel's
widgets after ``panel.``. Ctrl+Space opens it anywhere.
"""

from __future__ import annotations

import re

from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFont, QPainter, QStandardItem, QStandardItemModel, QSyntaxHighlighter,
    QTextCharFormat, QTextCursor, QTextFormat,
)
from PySide6.QtWidgets import QCompleter, QPlainTextEdit, QTextEdit, QWidget

from .theme import PALETTE_EVENTS

KEYWORDS = (
    "and break do else elseif end false for function goto if in local nil not or "
    "repeat return then true until while"
).split()
BUILTINS = "sleep log time print check save_waveform panel".split()
RUNNING_COLOR = QColor("#16a34a")  # the line a running test is at
INSERT_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass
class Completion:
    text: str  # what is inserted
    label: str  # what the list shows, such as "set_voltage(volts [V])"
    tip: str = ""


# Benchloom's own functions, offered with the devices as names are typed.
GLOBALS = [
    Completion("sleep(", "sleep(seconds)", "Waits; Stop ends the wait at once."),
    Completion("log { ", "log { name = value, … }", "Adds one row to data.csv, the plot "
               "and the Data tab."),
    Completion("check(", "check(name, value, low, high, unit)", "A pass/fail check."),
    Completion("save_waveform(", "save_waveform(name, traces)",
               "Saves scope traces as CSV, SVG and PNG."),
    Completion("print(", "print(...)", "Writes to the console."),
    Completion("time()", "time()", "Seconds since the run started."),
    Completion("panel.", "panel", "The front panel: panel.<name> reads or sets a widget."),
]
_CONTEXT = re.compile(r"(?:(\b[A-Za-z_]\w*)\s*([:.])\s*)?([A-Za-z_]\w*)?$")


def in_string_or_comment(line: str) -> bool:
    """Whether the end of this line (the text before the cursor) is inside a string
    or a comment, where nothing is offered."""
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif line.startswith("--", i):
            return True
        i += 1
    return quote is not None

_WORD = re.compile(r"\b[A-Za-z_]\w*\b")
_NUMBER = re.compile(r"\b(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?)\b")
_STRING_OR_COMMENT = re.compile(r"""--\[\[|--.*|"(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?""")


def mono_font(size: int = 11) -> QFont:
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono", "monospace"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(size)
    return font


def is_dark(palette) -> bool:
    return palette.window().color().lightness() < 128


class LuaHighlighter(QSyntaxHighlighter):
    def __init__(self, document, dark: bool):
        super().__init__(document)
        self.formats = {}
        self.set_dark(dark)

    def set_dark(self, dark: bool) -> None:
        colors = (
            {"keyword": "#c792ea", "builtin": "#82aaff", "number": "#f78c6c",
             "string": "#c3e88d", "comment": "#7f8c98"}
            if dark else
            {"keyword": "#8431c5", "builtin": "#1f5fbf", "number": "#b3541e",
             "string": "#2f7d32", "comment": "#7a8490"}
        )
        self.formats = {}
        for name, color in colors.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if name == "comment":
                fmt.setFontItalic(True)
            self.formats[name] = fmt
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        for match in _WORD.finditer(text):
            word = match.group()
            kind = "keyword" if word in KEYWORDS else "builtin" if word in BUILTINS else None
            if kind:
                self.setFormat(match.start(), len(word), self.formats[kind])
        for match in _NUMBER.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self.formats["number"])

        self.setCurrentBlockState(0)
        pos = 0
        if self.previousBlockState() == 1:  # inside a --[[ long comment ]]
            pos = self._long_comment(text, 0)
        while pos < len(text):
            match = _STRING_OR_COMMENT.search(text, pos)
            if not match:
                break
            if match.group() == "--[[":
                pos = self._long_comment(text, match.start())
            else:
                kind = "comment" if match.group().startswith("--") else "string"
                self.setFormat(match.start(), match.end() - match.start(), self.formats[kind])
                pos = match.end()

    def _long_comment(self, text: str, start: int) -> int:
        end = text.find("]]", start)
        if end < 0:
            self.setCurrentBlockState(1)
            end = len(text)
        else:
            end += 2
        self.setFormat(start, end - start, self.formats["comment"])
        return end


class _Gutter(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(mono_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.highlighter = LuaHighlighter(self.document(), is_dark(self.palette()))
        self._error_line: int | None = None
        self._running_line: int | None = None
        self.devices: dict[str, list[Completion]] = {}  # name -> its commands
        self.panel_names: list[str] = []
        self.completer = QCompleter(self)
        self.completer.setWidget(self)
        # one model, refilled for each list: replacing it while the popup handles a key
        # deletes it under Qt's feet
        self._choices = QStandardItemModel(self.completer)
        self.completer.setModel(self._choices)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self.completer.activated.connect(self._insert_text_of)
        self._replace = 0  # characters before the cursor that a completion replaces
        self._gutter = _Gutter(self)
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._on_update_request)
        self._update_margin()

    def set_font_size(self, size: int) -> None:
        self.setFont(mono_font(size))
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self._update_margin()
        self._gutter.update()
        rect = self.contentsRect()
        self._gutter.setGeometry(QRect(rect.left(), rect.top(), self.gutter_width(), rect.height()))

    def changeEvent(self, event) -> None:
        if event.type() in PALETTE_EVENTS:
            self.highlighter.set_dark(is_dark(self.palette()))
        super().changeEvent(event)

    # --- line numbers -----------------------------------------------------
    def gutter_width(self) -> int:
        digits = max(2, len(str(self.blockCount())))
        return 16 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margin(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _on_update_request(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._gutter.setGeometry(QRect(rect.left(), rect.top(), self.gutter_width(), rect.height()))

    def paint_gutter(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), self.palette().window())
        painter.setFont(self.font())
        normal = self.palette().placeholderText().color()
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible():
                number = block.blockNumber() + 1
                painter.setPen(QColor("#d9453c") if number == self._error_line
                               else RUNNING_COLOR if number == self._running_line else normal)
                painter.drawText(0, top, self._gutter.width() - 8, height,
                                 Qt.AlignmentFlag.AlignRight, str(number))
            top += round(self.blockBoundingRect(block).height())
            block = block.next()

    # --- editing ----------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        popup = self.completer.popup()
        if popup.isVisible() and event.key() in (
                Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Escape, Qt.Key.Key_Tab,
                Qt.Key.Key_Backtab):
            event.ignore()  # the completer picks or closes
            return
        forced = (event.key() == Qt.Key.Key_Space
                  and event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if forced:
            self.complete(force=True)
            return
        if event.key() == Qt.Key.Key_Tab and not self.textCursor().hasSelection():
            self.insertPlainText("    ")
            return
        super().keyPressEvent(event)
        text = event.text()
        if text and (text.isalnum() or text in "_:."):
            self.complete()
        elif popup.isVisible():
            if event.key() == Qt.Key.Key_Backspace:
                self.complete()
            else:
                popup.hide()

    # --- autocomplete -----------------------------------------------------
    def set_names(self, devices: dict[str, list[Completion]], panel_names: list[str]) -> None:
        """The project's devices with their commands, and its front panel widgets."""
        self.devices = dict(devices)
        self.panel_names = list(panel_names)

    def completions(self, line: str) -> tuple[list[Completion], str]:
        """What to offer at the end of ``line`` (the text before the cursor), and the
        part of a name already typed, which the choice replaces."""
        if in_string_or_comment(line):
            return [], ""
        match = _CONTEXT.search(line)
        owner, sep, typed = match.group(1), match.group(2), match.group(3) or ""
        if sep == ":" and owner in self.devices:
            return self.devices[owner], typed
        if sep == "." and owner == "panel":
            return [Completion(n, n) for n in self.panel_names], typed
        if sep:
            return [], ""
        before = line[:len(line) - len(typed)].rstrip()
        if before.endswith((":", ".")):
            return [], ""
        names = [Completion(f"{name}:", name, "a device of this project")
                 for name in self.devices]
        return names + GLOBALS, typed

    def complete(self, force: bool = False) -> None:
        cursor = self.textCursor()
        line = cursor.block().text()[:cursor.positionInBlock()]
        choices, typed = self.completions(line)
        popup = self.completer.popup()
        if not choices or (not force and not typed and not line.rstrip().endswith((":", "."))):
            popup.hide()
            return
        self._choices.clear()
        for choice in choices:
            item = QStandardItem(choice.label)
            item.setData(choice.text, INSERT_ROLE)
            item.setToolTip(choice.tip)
            self._choices.appendRow(item)
        self.completer.setCompletionPrefix(typed)
        if self.completer.completionCount() == 0:
            popup.hide()
            return
        if self.completer.completionCount() == 1 and \
                self.completer.currentCompletion() == typed and typed:
            popup.hide()  # already typed in full
            return
        self._replace = len(typed)
        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(max(260, popup.sizeHintForColumn(0) + 30))
        self.completer.complete(rect)

    def _insert_text_of(self, label: str) -> None:
        index = self.completer.popup().currentIndex()
        text = index.data(INSERT_ROLE) if index.isValid() else None
        if text is None:  # fall back to the label of the choice
            text = label.split("(")[0]
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor,
                            self._replace)
        cursor.insertText(text)
        self.setTextCursor(cursor)
        if text.endswith((":", ".")):  # straight on to the commands or widgets, once
            QTimer.singleShot(0, self.complete)  # the completer has finished this choice

    def mark_error(self, line: int | None) -> None:
        """Highlight ``line`` (1-based) as the place a run failed, or clear the mark."""
        self._error_line = line
        if line is not None:
            block = self.document().findBlockByNumber(line - 1)
            if block.isValid():
                self.setTextCursor(QTextCursor(block))
                self.centerCursor()
        self._update_marks()

    def mark_running(self, line: int | None) -> None:
        """Highlight ``line`` (1-based) as the one a running test is at; None clears it.
        The view does not move, so editing elsewhere is not disturbed."""
        if line != self._running_line:
            self._running_line = line
            self._update_marks()

    def _update_marks(self) -> None:
        selections = []
        for line, color, alpha in ((self._running_line, RUNNING_COLOR, 55),
                                   (self._error_line, QColor("#d9453c"), 60)):
            block = self.document().findBlockByNumber(line - 1) if line else None
            if block is not None and block.isValid():
                selection = QTextEdit.ExtraSelection()
                color = QColor(color)
                color.setAlpha(alpha)
                selection.format.setBackground(color)
                selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                selection.cursor = QTextCursor(block)
                selections.append(selection)
        self.setExtraSelections(selections)
        self._gutter.update()
