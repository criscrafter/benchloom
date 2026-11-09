"""The Patchbay main window."""

from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QDockWidget, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QRadioButton, QStackedWidget, QToolBar, QVBoxLayout, QWidget,
)

from ..project import PROJECT_FILE, Project, ProjectError, create_project
from .devices import DevicesPanel
from .editor import CodeEditor, mono_font
from .plot import PlotPanel
from .runner import RunController

MAX_RECENT = 8


def default_projects_dir() -> Path:
    return Path.home() / "Documents" / "Patchbay"


class NewProjectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New project")
        self.setMinimumWidth(480)
        self.name_edit = QLineEdit("my-test")
        self.location_edit = QLineEdit(str(default_projects_dir()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        location = QHBoxLayout()
        location.addWidget(self.location_edit, 1)
        location.addWidget(browse)

        self.lua_radio = QRadioButton("Lua script")
        self.visual_radio = QRadioButton("Visual (wires between nodes), not available yet")
        self.visual_radio.setEnabled(False)
        self.lua_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.lua_radio)
        group.addButton(self.visual_radio)
        modes = QVBoxLayout()
        modes.addWidget(self.lua_radio)
        modes.addWidget(self.visual_radio)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("Name", self.name_edit)
        form.addRow("Location", location)
        form.addRow("Test type", modes)
        form.addRow(QLabel("The project starts with a simulated power supply and a working "
                           "example, so it can be run straight away."))
        form.addRow(buttons)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Location", self.location_edit.text())
        if folder:
            self.location_edit.setText(folder)

    def path(self) -> Path:
        return Path(self.location_edit.text().strip()) / self.name_edit.text().strip()

    def mode(self) -> str:
        return "lua" if self.lua_radio.isChecked() else "visual"

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        if not name or re.search(r'[\\/:*?"<>|]', name):
            QMessageBox.warning(self, "Name", "Enter a project name without special characters.")
            return
        if (self.path() / PROJECT_FILE).exists():
            QMessageBox.warning(self, "Name", "A project with this name already exists there.")
            return
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.project: Project | None = None
        self.settings = settings or QSettings("Patchbay", "Patchbay")
        self.resize(1280, 800)

        # central area: welcome page, Lua editor, or the visual placeholder
        self.editor = CodeEditor()
        self.visual_placeholder = QLabel(
            "This is a visual project.\nThe node editor is not built yet."
        )
        self.visual_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_welcome())
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.visual_placeholder)
        self.setCentralWidget(self.stack)

        self.devices = DevicesPanel()
        self.devices.insert_requested.connect(self._insert_snippet)
        self.console = QPlainTextEdit(readOnly=True)
        self.console.setMaximumBlockCount(10000)
        self.console.setFont(mono_font(10))
        self.plot = PlotPanel()
        self._docks = [
            self._dock("Devices", self.devices, Qt.DockWidgetArea.LeftDockWidgetArea),
            self._dock("Plot", self.plot, Qt.DockWidgetArea.RightDockWidgetArea),
            self._dock("Console", self.console, Qt.DockWidgetArea.BottomDockWidgetArea),
        ]
        self.resizeDocks(self._docks[:2], [280, 460], Qt.Orientation.Horizontal)
        self.resizeDocks(self._docks[2:], [200], Qt.Orientation.Vertical)

        self.runner = RunController(self)
        self.runner.printed.connect(self._print)
        self.runner.row.connect(self.plot.add_row)
        self.runner.finished.connect(self._run_finished)
        self._last_run: Path | None = None

        self._build_actions()
        self._set_project(None)

    # --- construction -----------------------------------------------------
    def _dock(self, title: str, widget: QWidget, area) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(title)
        dock.setWidget(widget)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.addDockWidget(area, dock)
        return dock

    def _build_welcome(self) -> QWidget:
        title = QLabel("Patchbay")
        font = title.font()
        font.setPointSize(28)
        font.setBold(True)
        title.setFont(font)
        subtitle = QLabel("Automate your lab instruments.")
        subtitle.setEnabled(False)

        new_button = QPushButton("New project")
        open_button = QPushButton("Open project…")
        for button in (new_button, open_button):
            button.setMinimumHeight(40)
        new_button.setDefault(True)
        new_button.clicked.connect(self.new_project)
        open_button.clicked.connect(self.open_project_dialog)
        buttons = QHBoxLayout()
        buttons.addWidget(new_button)
        buttons.addWidget(open_button)

        self.recent_label = QLabel("Recent projects")
        self.recent_list = QListWidget()
        self.recent_list.itemActivated.connect(lambda item: self.open_project(item.text()))
        self.recent_list.itemClicked.connect(lambda item: self.open_project(item.text()))

        column = QVBoxLayout()
        column.addStretch(1)
        column.addWidget(title)
        column.addWidget(subtitle)
        column.addSpacing(24)
        column.addLayout(buttons)
        column.addSpacing(24)
        column.addWidget(self.recent_label)
        column.addWidget(self.recent_list, 2)
        column.addStretch(1)
        inner = QWidget()
        inner.setLayout(column)
        inner.setFixedWidth(520)
        page = QWidget()
        row = QHBoxLayout(page)
        row.addStretch()
        row.addWidget(inner)
        row.addStretch()
        return page

    def _build_actions(self) -> None:
        def action(text, slot, shortcut=None):
            item = QAction(text, self)
            item.triggered.connect(slot)
            if shortcut:
                item.setShortcut(QKeySequence(shortcut))
                item.setToolTip(f"{text} ({QKeySequence(shortcut).toString()})")
            return item

        self.new_action = action("New", self.new_project, "Ctrl+N")
        self.open_action = action("Open", self.open_project_dialog, "Ctrl+O")
        self.save_action = action("Save", self.save, "Ctrl+S")
        self.run_action = action("▶ Run", self.run, "F5")
        self.stop_action = action("■ Stop", self.stop, "Shift+F5")
        self.runs_action = action("Show run folder", self.show_run_folder)

        toolbar = QToolBar("Main")
        toolbar.setObjectName("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        toolbar.addActions([self.new_action, self.open_action, self.save_action])
        toolbar.addSeparator()
        toolbar.addActions([self.run_action, self.stop_action])
        toolbar.addSeparator()
        toolbar.addAction(self.runs_action)
        self.addToolBar(toolbar)

    # --- project handling -------------------------------------------------
    def _recent(self) -> list[str]:
        value = self.settings.value("recent", [])
        paths = [value] if isinstance(value, str) else list(value or [])
        return [p for p in paths if (Path(p) / PROJECT_FILE).is_file()]

    def _remember(self, path: Path) -> None:
        recent = [str(path)] + [p for p in self._recent() if p != str(path)]
        self.settings.setValue("recent", recent[:MAX_RECENT])

    def _set_project(self, project: Project | None) -> None:
        self.project = project
        self.devices.set_project(project)
        self.console.clear()
        self.plot.clear()
        self.editor.mark_error(None)
        self._last_run = None
        for dock in self._docks:
            dock.setVisible(project is not None)
        if project is None:
            self.setWindowTitle("Patchbay")
            recent = self._recent()
            self.recent_list.clear()
            self.recent_list.addItems(recent)
            self.recent_label.setVisible(bool(recent))
            self.recent_list.setVisible(bool(recent))
            self.stack.setCurrentIndex(0)
        else:
            self.setWindowTitle(f"{project.name} — Patchbay")
            if project.mode == "lua":
                script = project.path / project.script
                self.editor.setPlainText(
                    script.read_text(encoding="utf-8") if script.is_file() else ""
                )
                self.editor.document().setModified(False)
                self.stack.setCurrentIndex(1)
            else:
                self.stack.setCurrentIndex(2)
            self._remember(project.path)
        self._update_actions()

    def _update_actions(self) -> None:
        lua = self.project is not None and self.project.mode == "lua"
        running = self.runner.running
        self.save_action.setEnabled(lua)
        self.run_action.setEnabled(lua and not running)
        self.stop_action.setEnabled(running)
        self.runs_action.setEnabled(self._last_run is not None)
        self.new_action.setEnabled(not running)
        self.open_action.setEnabled(not running)
        self.devices.setEnabled(not running)

    def _keep_changes(self) -> bool:
        """Offer to save unsaved edits. False means the user cancelled."""
        if not (self.project and self.project.mode == "lua"
                and self.editor.document().isModified()):
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes", "Save the changes to the script?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            self.save()
        return answer != QMessageBox.StandardButton.Cancel

    def new_project(self) -> None:
        dialog = NewProjectDialog(self)
        if dialog.exec() and self._keep_changes():
            try:
                self._set_project(create_project(dialog.path(), dialog.mode()))
            except (ProjectError, OSError) as exc:
                QMessageBox.critical(self, "New project", str(exc))

    def open_project_dialog(self) -> None:
        start = str(self.project.path.parent if self.project else default_projects_dir())
        folder = QFileDialog.getExistingDirectory(self, "Open project folder", start)
        if folder:
            self.open_project(folder)

    def open_project(self, path: str | Path) -> bool:
        if not self._keep_changes():
            return False
        try:
            self._set_project(Project.load(path))
            return True
        except ProjectError as exc:
            QMessageBox.critical(self, "Open project", str(exc))
            return False

    def save(self) -> None:
        if self.project and self.project.mode == "lua":
            script = self.project.path / self.project.script
            script.write_text(self.editor.toPlainText(), encoding="utf-8")
            self.editor.document().setModified(False)

    # --- running ----------------------------------------------------------
    def run(self) -> None:
        if not self.project or self.project.mode != "lua" or self.runner.running:
            return
        self.save()
        self.console.clear()
        self.plot.clear()
        self.editor.mark_error(None)
        self.runner.start(self.project.path)
        self.statusBar().showMessage("Running…")
        self._update_actions()

    def stop(self) -> None:
        self.runner.stop()
        self.statusBar().showMessage("Stopping…")

    def _print(self, text: str, error: bool = False) -> None:
        if error or text.startswith("Error:"):
            self.console.appendHtml(
                f'<span style="color:#d9453c">{html.escape(text)}</span>'
            )
        else:
            self.console.appendPlainText(text)

    def _run_finished(self, status: str, error: str, path: str) -> None:
        self._last_run = Path(path) if path else None
        if error and not path:  # failed before the engine could report it itself
            self._print(f"Error: {error}", error=True)
        if status == "error" and self.project:
            match = re.search(rf"{re.escape(self.project.script)}:(\d+):", error)
            if match:
                self.editor.mark_error(int(match.group(1)))
        rows = len(self.plot.rows)
        message = {"ok": "Finished", "stopped": "Stopped", "error": "Failed"}[status]
        self.statusBar().showMessage(f"{message} · {rows} row(s) logged")
        self._update_actions()

    def show_run_folder(self) -> None:
        if self._last_run:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_run)))

    def _insert_snippet(self, snippet: str) -> None:
        if self.stack.currentWidget() is self.editor:
            self.editor.insertPlainText(snippet)
            self.editor.setFocus()

    def closeEvent(self, event) -> None:
        if not self._keep_changes():
            event.ignore()
            return
        if self.runner.running:
            self.runner.stop()
            self.runner._force_stop()
        event.accept()
