# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Lua editor with line numbers, syntax colours and an error-line marker."""

from __future__ import annotations

import re

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from .theme import PALETTE_EVENTS

KEYWORDS = (
    "and break do else elseif end false for function goto if in local nil not or "
    "repeat return then true until while"
).split()
BUILTINS = "sleep log time print".split()

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
                painter.setPen(QColor("#d9453c") if number == self._error_line else normal)
                painter.drawText(0, top, self._gutter.width() - 8, height,
                                 Qt.AlignmentFlag.AlignRight, str(number))
            top += round(self.blockBoundingRect(block).height())
            block = block.next()

    # --- editing ----------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Tab and not self.textCursor().hasSelection():
            self.insertPlainText("    ")
            return
        super().keyPressEvent(event)

    def mark_error(self, line: int | None) -> None:
        """Highlight ``line`` (1-based) as the place a run failed, or clear the mark."""
        self._error_line = line
        selections = []
        if line is not None:
            block = self.document().findBlockByNumber(line - 1)
            if block.isValid():
                selection = QTextEdit.ExtraSelection()
                color = QColor("#d9453c")
                color.setAlpha(60)
                selection.format.setBackground(color)
                selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                selection.cursor = QTextCursor(block)
                selections.append(selection)
                self.setTextCursor(QTextCursor(block))
                self.centerCursor()
        self.setExtraSelections(selections)
        self._gutter.update()
