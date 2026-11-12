# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The Runs panel: a project's past runs, newest first.

Double-click a run for its report. **Show** loads its data into the plot and the
Data tab, and its console; **Compare** draws its curves dashed in the plot next
to the current ones, also while the next run is being measured.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ..history import RunInfo, list_runs

PASS_COLOR, FAIL_COLOR = QColor("#16a34a"), QColor("#d9453c")
STATUS = {"ok": "Finished", "error": "Failed", "stopped": "Stopped", "unknown": "Incomplete"}


def result_text(run: RunInfo) -> str:
    if run.status == "error":
        return "ERROR"
    return {"pass": "PASS", "fail": "FAIL"}.get(run.result, "–")


def duration_text(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, seconds = divmod(int(round(seconds)), 60)
    return f"{minutes} min {seconds:02d} s" if minutes < 60 else \
        f"{minutes // 60} h {minutes % 60:02d} min"


class RunsPanel(QWidget):
    show_requested = Signal(object)  # Path of a run to show in the plot and Data tab
    compare_requested = Signal(object)  # Path of a run to draw next to the current one
    compare_cleared = Signal()  # stop drawing the run compared with

    def __init__(self, parent=None, trash=None):
        super().__init__(parent)
        self.project_dir: Path | None = None
        self.runs: list[RunInfo] = []
        self._trash = trash  # trash(path) -> bool; tests leave the recycle bin alone

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Started", "Result", "Status", "Rows", "Checks", "Duration"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        self.tree.itemDoubleClicked.connect(lambda *_: self.open_report())
        self.tree.currentItemChanged.connect(self._update_buttons)

        self.report_button = QPushButton("Report")
        self.show_button = QPushButton("Show")
        self.show_button.setToolTip("Show this run's data in the plot and the Data tab")
        self.compare_button = QPushButton("Compare")
        self.compare_button.setToolTip("Draw this run's curves dashed next to the current "
                                       "ones; they stay while the next run is measured")
        self.uncompare_button = QPushButton("Clear comparison")
        self.uncompare_button.setEnabled(False)
        self.uncompare_button.clicked.connect(self.compare_cleared)
        self.folder_button = QPushButton("Folder")
        self.delete_button = QPushButton("Delete")
        self.report_button.clicked.connect(self.open_report)
        self.show_button.clicked.connect(lambda: self._emit(self.show_requested))
        self.compare_button.clicked.connect(lambda: self._emit(self.compare_requested))
        self.folder_button.clicked.connect(self.open_folder)
        self.delete_button.clicked.connect(lambda: self.delete_selected())
        buttons = QHBoxLayout()
        for button in (self.report_button, self.show_button, self.compare_button,
                       self.uncompare_button, self.folder_button):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.delete_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)
        self._update_buttons()

    # --- content ----------------------------------------------------------------
    def set_project(self, project_dir: Path | None) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self.refresh()

    def refresh(self, select: Path | None = None) -> None:
        current = self.selected()
        select = Path(select) if select else (current.path if current else None)
        self.runs = list_runs(self.project_dir) if self.project_dir else []
        self.tree.clear()
        for run in self.runs:
            checks = f"{run.passed} of {run.passed + run.failed}" if run.passed + run.failed \
                else ""
            started = run.started.strftime("%Y-%m-%d %H:%M:%S") if run.started else run.name
            item = QTreeWidgetItem([started, result_text(run), STATUS.get(run.status, run.status),
                                    str(run.rows), checks, duration_text(run.duration_s)])
            item.setData(0, Qt.ItemDataRole.UserRole, run)
            color = {"PASS": PASS_COLOR, "FAIL": FAIL_COLOR, "ERROR": FAIL_COLOR}.get(
                result_text(run))
            if color is not None:
                item.setForeground(1, QBrush(color))
            if run.error:
                item.setToolTip(2, run.error)
            for column in (3, 5):
                item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
            self.tree.addTopLevelItem(item)
            if select is not None and run.path == select:
                self.tree.setCurrentItem(item)
        self._update_buttons()

    def set_comparing(self, on: bool) -> None:
        self.uncompare_button.setEnabled(on)

    def selected(self) -> RunInfo | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _update_buttons(self, *_) -> None:
        run = self.selected()
        for button in (self.show_button, self.compare_button, self.folder_button,
                       self.delete_button):
            button.setEnabled(run is not None)
        self.report_button.setEnabled(run is not None and run.has_report)

    # --- actions ------------------------------------------------------------------
    def _emit(self, signal) -> None:
        run = self.selected()
        if run is not None:
            signal.emit(run.path)

    def open_report(self) -> None:
        run = self.selected()
        if run is not None and run.has_report:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(run.path / "report.html")))

    def open_folder(self) -> None:
        run = self.selected()
        if run is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(run.path)))

    def delete_selected(self, confirm: bool = True) -> bool:
        run = self.selected()
        if run is None:
            return False
        if confirm:
            answer = QMessageBox.question(
                self, "Delete run",
                f"Move the run of {self.tree.currentItem().text(0)} to the recycle bin?"
                f"\n\n{run.path}")
            if answer != QMessageBox.StandardButton.Yes:
                return False
        trash = self._trash
        if trash is None:
            from .plugins import move_to_trash as trash
        if not trash(run.path):
            QMessageBox.warning(self, "Delete run", f"Could not move {run.path} to the "
                                "recycle bin. Delete the folder by hand if you want it gone.")
            return False
        self.refresh()
        return True
