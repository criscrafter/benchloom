# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The Benchloom main window."""

from __future__ import annotations

import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QFile, QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QSizePolicy,
    QStackedWidget, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from .. import HOMEPAGE, NOTICE, __version__
from ..config import AppConfig
from ..engine import GRAPH_CHUNK
from ..graph import Catalog, GraphError, compile_graph
from ..panel import Panel, PanelError, rename_in_graph, rename_in_lua
from ..plugin import PluginRegistry
from ..project import (
    Project, ProjectError, create_project, duplicate_project, is_project, last_saved,
    move_project,
    project_name_problem, rename_project, stamp_version, templates,
)
from .devices import DevicesPanel
from .editor import CodeEditor, mono_font
from .graph_editor import GraphEditor
from .panel_view import PanelView
from .plot import PlotPanel
from .runs import RunsPanel
from .table import DataTable
from .waveforms import WaveformPanel
from .plugins import PluginManagerDialog
from .runner import RunController
from .settings import (  # noqa: F401
    GuiSettings, SettingsDialog, app_settings, default_projects_dir,
)
from .theme import THEMES, apply_theme
from .update_bar import UpdateBar, UpdateChecker

MAX_RECENT = 8


def _plot_key(kind: str, project_path: str | Path) -> str:
    """The settings key of a project's plot choice."""
    return kind + "/" + str(project_path).replace("\\", "/").replace("/", "|")


def _when(timestamp: float | None, now: datetime | None = None) -> str:
    if timestamp is None:
        return "unknown"
    moment = datetime.fromtimestamp(timestamp)
    days = ((now or datetime.now()).date() - moment.date()).days
    if days == 0:
        return f"today {moment:%H:%M}"
    if days == 1:
        return f"yesterday {moment:%H:%M}"
    return f"{moment:%Y-%m-%d %H:%M}"


def _recent_text(path: str, pinned: bool = False) -> str:
    """A recent project in the welcome list: name, folder, when and by which version
    it was saved."""
    try:
        project = Project.load(path)
        name, version = project.name, project.saved_with
    except ProjectError:
        name, version = Path(path).name, ""
    version = f"Benchloom {version}" if version else "Benchloom version unknown"
    pin = "  (pinned)" if pinned else ""
    return f"{name}{pin}\n{path}\nSaved {_when(last_saved(path))} · {version}"


SHOW_FOLDER = {"win32": "Show in Explorer", "darwin": "Show in Finder"}.get(
    sys.platform, "Show in file manager")


def _shutdown_failed(run_path: Path) -> list[str]:
    """The devices of a finished run whose shutdown failed, from its run.json."""
    try:
        meta = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
        return [name for name, how in (meta.get("shutdown") or {}).items() if how != "done"]
    except (OSError, ValueError, AttributeError):
        return []


class NewProjectDialog(QDialog):
    def __init__(self, parent=None, location: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle("New project")
        self.setMinimumWidth(480)
        self.name_edit = QLineEdit("my-test")
        self.location_edit = QLineEdit(str(location or default_projects_dir()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        location = QHBoxLayout()
        location.addWidget(self.location_edit, 1)
        location.addWidget(browse)

        self.lua_radio = QRadioButton("Lua script")
        self.visual_radio = QRadioButton("Visual (wires between nodes)")
        self.lua_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.lua_radio)
        group.addButton(self.visual_radio)
        modes = QVBoxLayout()
        modes.addWidget(self.lua_radio)
        modes.addWidget(self.visual_radio)

        self.templates = templates()
        self.template_combo = QComboBox()
        for template in self.templates:
            self.template_combo.addItem(template.title, template.id)
        self.template_info = QLabel(wordWrap=True)
        self.template_info.setMinimumHeight(self.fontMetrics().lineSpacing() * 4)
        self.template_combo.currentIndexChanged.connect(self._template_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("Name", self.name_edit)
        form.addRow("Location", location)
        form.addRow("Start from", self.template_combo)
        form.addRow(self.template_info)
        form.addRow("Test type", modes)
        form.addRow(buttons)
        self._template_changed()

    def _template_changed(self, *_) -> None:
        template = self.templates[self.template_combo.currentIndex()]
        text = template.description
        if template.mode:
            text += f" It is a {'Lua' if template.mode == 'lua' else 'visual'} test."
            (self.lua_radio if template.mode == "lua" else self.visual_radio).setChecked(True)
        self.template_info.setText(text)
        for radio in (self.lua_radio, self.visual_radio):
            radio.setEnabled(template.mode is None)

    def template(self) -> str:
        return self.template_combo.currentData()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Location", self.location_edit.text())
        if folder:
            self.location_edit.setText(folder)

    def path(self) -> Path:
        return Path(self.location_edit.text().strip()) / self.name_edit.text().strip()

    def mode(self) -> str:
        return "lua" if self.lua_radio.isChecked() else "visual"

    def accept(self) -> None:
        problem = project_name_problem(self.name_edit.text())
        if problem:
            QMessageBox.warning(self, "Name", problem)
            return
        if is_project(self.path()):
            QMessageBox.warning(self, "Name", "A project with this name already exists there.")
            return
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.project: Project | None = None
        self._close_when_stopped = False  # the window was closed while a test ran
        self._closing = False
        self.settings = settings or app_settings()
        self.prefs = GuiSettings(self.settings)
        self.resize(1280, 800)
        apply_theme(self.theme())
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_theme_changed)

        # central area: welcome page, Lua editor, or the visual placeholder
        self.editor = CodeEditor()
        self.graph = GraphEditor()
        self._line_map: list[str | None] = []
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_welcome())
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.graph)
        self.setCentralWidget(self.stack)
        self.update_checker = UpdateChecker(self)
        self.update_checker.done.connect(self._update_checked)
        self._update_automatic = True

        self.devices = DevicesPanel()
        self.devices.command_activated.connect(self._command_activated)
        self.devices.changed.connect(self._devices_changed)
        self.devices.changed.connect(self.update_completions)
        self.console = QPlainTextEdit(readOnly=True)
        self.plot = PlotPanel()
        self.plot.right_columns_changed.connect(self._right_columns_changed)
        self.plot.log_x_changed.connect(self._log_x_changed)
        self.plot.hidden_columns_changed.connect(self._hidden_columns_changed)
        self.panel_view = PanelView()
        self.waveforms = WaveformPanel()
        self.table = DataTable()
        self.runs = RunsPanel()
        self.runs.show_requested.connect(self.show_run)
        self.runs.compare_requested.connect(self.compare_run)
        self.runs.compare_cleared.connect(self.clear_comparison)
        self._docks = [
            self._dock("Devices", self.devices, Qt.DockWidgetArea.LeftDockWidgetArea),
            self._dock("Plot", self.plot, Qt.DockWidgetArea.RightDockWidgetArea),
            self._dock("Console", self.console, Qt.DockWidgetArea.BottomDockWidgetArea),
            self._dock("Front panel", self.panel_view, Qt.DockWidgetArea.RightDockWidgetArea),
            self._dock("Waveforms", self.waveforms, Qt.DockWidgetArea.RightDockWidgetArea),
            self._dock("Data", self.table, Qt.DockWidgetArea.RightDockWidgetArea),
            self._dock("Runs", self.runs, Qt.DockWidgetArea.BottomDockWidgetArea),
        ]
        self.tabifyDockWidget(self._docks[2], self._docks[6])
        self.tabifyDockWidget(self._docks[1], self._docks[5])
        self.tabifyDockWidget(self._docks[5], self._docks[3])
        self.tabifyDockWidget(self._docks[3], self._docks[4])
        self._docks[1].raise_()
        self._docks[2].raise_()
        self.resizeDocks(self._docks[:2], [280, 460], Qt.Orientation.Horizontal)
        self.resizeDocks(self._docks[2:3], [200], Qt.Orientation.Vertical)

        self.runner = RunController(self)
        self.runner.printed.connect(self._print)
        self.runner.row.connect(self.plot.add_row)
        self.runner.row.connect(self.table.add_row)
        self.runner.panel.connect(self.panel_view.show_value)
        self.runner.waveform.connect(self.waveforms.add)
        self.runner.node.connect(lambda node_id: self.graph.mark_running(node_id or None))
        self.runner.line.connect(self._line_running)
        self.runner.check.connect(self._check_done)
        self._failed_checks = self._checks = 0
        self.panel_view.control_changed.connect(self.runner.send_control)
        self.panel_view.renamed.connect(self._widget_renamed)
        self.panel_view.edited.connect(self.update_completions)
        self.runner.finished.connect(self._run_finished)
        self._last_run: Path | None = None

        self._build_actions()
        self.apply_preferences()
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
        title = QLabel("Benchloom")
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
        # long folder paths are shortened in the middle rather than scrolled
        self.recent_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.recent_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_list.setSpacing(2)
        # a click selects; a double click or Enter opens
        self.recent_list.itemActivated.connect(
            lambda item: self.open_project(item.data(Qt.ItemDataRole.UserRole)))
        self.recent_list.itemSelectionChanged.connect(self._recent_selection_changed)
        self.recent_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recent_list.customContextMenuRequested.connect(self._recent_menu)

        def recent_button(text, slot, tip, shortcut=None):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            if shortcut:  # only while the list has the focus
                item = QAction(text, self.recent_list)
                item.setShortcut(QKeySequence(shortcut))
                item.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
                item.triggered.connect(slot)
                self.recent_list.addAction(item)
            return button

        self.recent_open = recent_button("Open", self.open_selected_recent,
                                         "Open the project (or double-click it)")
        self.recent_rename = recent_button("Rename…", self.rename_selected_recent,
                                           "Rename the project and its folder (F2)", "F2")
        self.recent_move = recent_button("Move…", self.move_selected_recent,
                                         "Move the project folder to another place")
        self.recent_duplicate = recent_button(
            "Duplicate…", self.duplicate_selected_recent,
            "Copy the project under a new name, without its past runs")
        self.recent_delete = recent_button("Delete", self.delete_selected_recent,
                                           "Move the project folder to the Recycle Bin (Del)",
                                           "Del")
        # the less used ones behind a ⋯ button
        self.pin_action = QAction("Pin to top", self)
        self.pin_action.triggered.connect(self.toggle_pin_selected_recent)
        self.show_folder_action = QAction(SHOW_FOLDER, self)
        self.show_folder_action.triggered.connect(self.show_selected_recent_folder)
        more_menu = QMenu(self)
        more_menu.addActions([self.pin_action, self.show_folder_action])
        self.recent_more = QToolButton(text="⋯")
        self.recent_more.setToolTip("More: pin to the top, " + SHOW_FOLDER.lower())
        self.recent_more.setMenu(more_menu)
        self.recent_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.recent_buttons = QWidget()
        recent_row = QHBoxLayout(self.recent_buttons)
        recent_row.setContentsMargins(0, 0, 0, 0)
        recent_row.addWidget(self.recent_open)
        recent_row.addStretch()
        for button in (self.recent_rename, self.recent_move, self.recent_duplicate,
                       self.recent_delete, self.recent_more):
            recent_row.addWidget(button)

        column = QVBoxLayout()
        column.addStretch(1)
        column.addWidget(title)
        column.addWidget(subtitle)
        column.addSpacing(24)
        column.addLayout(buttons)
        column.addSpacing(24)
        column.addWidget(self.recent_label)
        column.addWidget(self.recent_list, 2)
        column.addWidget(self.recent_buttons)
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
        self.report_action = action("Report", self.show_report)
        self.report_action.setToolTip("Open the last run's report: checks, data, waveforms")
        self.lua_action = action("View as Lua", self.view_lua)
        self.plugins_action = action("Plugins", self.open_plugin_manager)
        self.plugins_action.setToolTip("See, switch off, install and remove plugins")
        self.settings_action = action("Settings", self.open_settings, "Ctrl+,")
        self.about_action = action("About", self.show_about)
        self.about_action.setToolTip("Version, licence and warranty notice")

        toolbar = QToolBar("Main")
        toolbar.setObjectName("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        toolbar.addActions([self.new_action, self.open_action, self.save_action])
        toolbar.addSeparator()
        toolbar.addActions([self.run_action, self.stop_action])
        toolbar.addSeparator()
        toolbar.addActions([self.runs_action, self.report_action, self.lua_action])
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        toolbar.addAction(self.plugins_action)
        toolbar.addAction(self.settings_action)
        toolbar.addAction(self.about_action)
        toolbar.addWidget(self._build_theme_button())
        self.addToolBar(toolbar)

        # a newer version: a bar across the window, under the toolbar, while it is shown
        self.update_bar = UpdateBar()
        self.update_bar.skipped.connect(lambda version: self.prefs.set("skipped_version", version))
        updates = QToolBar("New version")
        updates.setObjectName("Updates")
        updates.setMovable(False)
        updates.toggleViewAction().setVisible(False)  # not in the toolbars' menu
        updates.addWidget(self.update_bar)
        updates.hide()
        self.update_bar.shown.connect(updates.setVisible)
        self.addToolBarBreak()
        self.addToolBar(updates)

    # --- theme ------------------------------------------------------------
    def theme(self) -> str:
        return self.prefs.get("theme")

    def _build_theme_button(self) -> QToolButton:
        menu = QMenu(self)
        group = QActionGroup(self)
        self.theme_actions = {}
        for key, label in THEMES.items():
            item = QAction(label, self, checkable=True)
            item.setChecked(key == self.theme())
            item.triggered.connect(lambda _=False, key=key: self.set_theme(key))
            group.addAction(item)
            menu.addAction(item)
            self.theme_actions[key] = item
        button = QToolButton(text="Theme")
        button.setToolTip("Light, dark, or follow the system")
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return button

    def set_theme(self, theme: str) -> None:
        self.prefs.set("theme", theme)
        self.theme_actions[theme].setChecked(True)
        apply_theme(theme)

    # --- settings and plugins -----------------------------------------------
    def apply_preferences(self) -> None:
        self.editor.set_font_size(self.prefs.get("editor_font_size"))
        self.console.setFont(mono_font(self.prefs.get("console_font_size")))
        self.console.setMaximumBlockCount(self.prefs.get("console_lines"))

    def show_about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("About Benchloom")
        box.setIcon(QMessageBox.Icon.Information)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<b>Benchloom {__version__}</b><br>Open lab automation")
        box.setInformativeText(
            html.escape(NOTICE.split("\n", 1)[1].strip()).replace("\n\n", "<br><br>")
            .replace("\n", " ").replace(HOMEPAGE, f'<a href="{HOMEPAGE}">{HOMEPAGE}</a>'))
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        box.addButton(QMessageBox.StandardButton.Close)
        check = box.addButton("Check for updates", QMessageBox.ButtonRole.ActionRole)
        box.exec()
        wanted = box.clickedButton() is check
        box.deleteLater()  # dialogs made with a parent stay until deleted
        if wanted:
            self.check_for_updates(automatic=False)

    # --- updates ----------------------------------------------------------
    UPDATE_INTERVAL_S = 24 * 3600

    def check_for_updates(self, automatic: bool = True) -> bool:
        """Ask GitHub whether a newer version is out. Automatic checks (at start) run
        at most once a day, only while switched on in Settings, and stay quiet unless
        there is something new; a check asked for in About always reports back."""
        if automatic:
            if not self.prefs.get("check_updates"):
                return False
            try:
                if time.time() - float(self.prefs.get("last_update_check")) <                         self.UPDATE_INTERVAL_S:
                    return False
            except ValueError:
                pass  # never checked
        if not self.update_checker.start():
            return False
        self._update_automatic = automatic
        if not automatic:
            self.statusBar().showMessage("Checking for a new version…")
        return True

    def _update_checked(self, release, error: str) -> None:
        automatic = self._update_automatic
        if release is None:
            if not automatic:
                self.statusBar().clearMessage()
                QMessageBox.warning(self, "Check for updates",
                                    f"Benchloom could not check for a new version: {error}")
            return
        self.prefs.set("last_update_check", str(time.time()))
        if release.newer_than():
            if not (automatic and release.version == self.prefs.get("skipped_version")):
                self.update_bar.show_release(release)
            if not automatic:
                self.statusBar().showMessage(f"Benchloom {release.version} is available", 5000)
        elif not automatic:
            self.statusBar().clearMessage()
            QMessageBox.information(self, "Check for updates",
                                    f"Benchloom {__version__} is the newest version.")

    def open_settings(self) -> bool:
        dialog = SettingsDialog(self.prefs, self)
        accepted = dialog.exec()
        folders_changed = accepted and dialog.plugin_folders_changed  # set by OK
        dialog.deleteLater()
        if not accepted:
            return False
        self.set_theme(self.theme())
        self.apply_preferences()
        if folders_changed:
            self.reload_plugins()
        return True

    def open_plugin_manager(self) -> None:
        dialog = PluginManagerDialog(self.project.path if self.project else None, self)
        dialog.exec()
        changed = dialog.changed
        dialog.deleteLater()
        if changed:
            self.reload_plugins()

    def reload_plugins(self) -> None:
        """Plugins changed: rebuild the device list and the node catalog."""
        if self.project is not None:
            self.devices.set_project(self.project)
            self._devices_changed()
            self.update_completions()

    def _system_theme_changed(self, *_) -> None:
        if self.theme() == "system":
            apply_theme("system")

    # --- plot settings per project ------------------------------------------
    def _plot_key(self, kind: str = "plot_right_axis") -> str:
        return _plot_key(kind, self.project.path)

    def _right_columns_changed(self, columns: list) -> None:
        if self.project is not None:
            self.settings.setValue(self._plot_key(), columns)

    def _log_x_changed(self, on: bool) -> None:
        if self.project is not None:
            self.settings.setValue(self._plot_key("plot_log_x"), on)

    def _hidden_columns_changed(self, columns: list) -> None:
        if self.project is not None:
            self.settings.setValue(self._plot_key("plot_hidden"), columns)

    def _apply_plot_settings(self, project: Project | None) -> None:
        """The project's [plot] choices, unless changed in the plot for this project."""
        prefs = project.plot if project is not None else {}
        right = prefs.get("right", [])
        hide = prefs.get("hide", [])
        log_x = bool(prefs.get("log_x", False))
        if project is not None:
            if self.settings.contains(self._plot_key()):
                right = self.settings.value(self._plot_key(), [])
            if self.settings.contains(self._plot_key("plot_hidden")):
                hide = self.settings.value(self._plot_key("plot_hidden"), [])
            if self.settings.contains(self._plot_key("plot_log_x")):
                log_x = str(self.settings.value(self._plot_key("plot_log_x"))).lower() == "true"
        self.plot.preferred_x = prefs.get("x") if isinstance(prefs.get("x"), str) else None
        self.plot.hidden = {str(c) for c in ([hide] if isinstance(hide, str) else hide or [])}
        self.plot.set_right_columns([right] if isinstance(right, str) else list(right or []))
        self.plot.set_log_x(log_x)

    # --- project handling -------------------------------------------------
    def _recent_entries(self) -> list[str]:
        value = self.settings.value("recent", [])
        return [value] if isinstance(value, str) else [str(p) for p in value or []]

    def _recent(self) -> list[str]:
        return [p for p in self._recent_entries() if is_project(p)]

    def _remember(self, path: Path) -> None:
        recent = [str(path)] + [p for p in self._recent() if p != str(path)]
        self.settings.setValue("recent", recent[:MAX_RECENT])

    # --- recent projects on the welcome page -----------------------------------
    def _pinned(self) -> list[str]:
        value = self.settings.value("pinned", [])
        return [value] if isinstance(value, str) else [str(p) for p in value or []]

    def _shown_recent(self) -> list[str]:
        """The welcome list: pinned projects first, then the last opened ones."""
        pinned = [p for p in self._pinned() if is_project(p)]
        return pinned + [p for p in self._recent() if p not in pinned]

    def _fill_recent(self, select: str | None = None) -> None:
        recent = self._shown_recent()
        pinned = set(self._pinned())
        self.recent_list.clear()
        for path in recent:
            item = QListWidgetItem(_recent_text(path, path in pinned))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.recent_list.addItem(item)
            if path == select:
                self.recent_list.setCurrentItem(item)
        for widget in (self.recent_label, self.recent_list, self.recent_buttons):
            widget.setVisible(bool(recent))
        self._recent_selection_changed()

    def _selected_recent(self) -> str | None:
        item = self.recent_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item and item.isSelected() else None

    def _recent_selection_changed(self, *_) -> None:
        path = self._selected_recent()
        for button in (self.recent_open, self.recent_rename, self.recent_move,
                       self.recent_duplicate, self.recent_delete, self.recent_more):
            button.setEnabled(path is not None)
        self.pin_action.setText("Unpin" if path in self._pinned() else "Pin to top")

    def _recent_menu(self, pos) -> None:
        item = self.recent_list.itemAt(pos)
        if item is None:
            return
        self.recent_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("Open", self.open_selected_recent)
        menu.addSeparator()
        menu.addAction("Rename…", self.rename_selected_recent)
        menu.addAction("Move…", self.move_selected_recent)
        menu.addAction("Duplicate…", self.duplicate_selected_recent)
        menu.addAction("Delete", self.delete_selected_recent)
        menu.addSeparator()
        menu.addAction(self.pin_action)
        menu.addAction(self.show_folder_action)
        menu.exec(self.recent_list.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def toggle_pin_selected_recent(self) -> None:
        path = self._selected_recent()
        if not path:
            return
        pinned = self._pinned()
        self.settings.setValue(
            "pinned", [p for p in pinned if p != path] if path in pinned else pinned + [path])
        self._fill_recent(select=path)

    def show_selected_recent_folder(self) -> None:
        path = self._selected_recent()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def duplicate_selected_recent(self) -> None:
        path = self._selected_recent()
        if not path:
            return
        base = Path(path).name + " copy"
        suggested, number = base, 2
        while Path(path).with_name(suggested).exists():
            suggested, number = f"{base} {number}", number + 1
        name, ok = QInputDialog.getText(
            self, "Duplicate project", "Name of the copy (past runs are not copied)",
            text=suggested)
        if not ok:
            return
        try:
            copy = duplicate_project(path, name)
        except ProjectError as exc:
            QMessageBox.warning(self, "Duplicate project", str(exc))
            return
        # in the list right away, under the original
        recent = self._recent_entries()
        at = recent.index(path) + 1 if path in recent else 0
        self.settings.setValue("recent", (recent[:at] + [str(copy)] + recent[at:])[:MAX_RECENT])
        self._fill_recent(select=str(copy))

    def open_selected_recent(self) -> None:
        path = self._selected_recent()
        if path:
            self.open_project(path)

    def rename_selected_recent(self) -> None:
        path = self._selected_recent()
        if not path:
            return
        old = Path(path).name
        name, ok = QInputDialog.getText(self, "Rename project", "New name", text=old)
        if not ok or name.strip() == old:
            return
        try:
            self._project_moved(path, rename_project(path, name))
        except ProjectError as exc:
            QMessageBox.warning(self, "Rename project", str(exc))

    def move_selected_recent(self) -> None:
        path = self._selected_recent()
        if not path:
            return
        folder = QFileDialog.getExistingDirectory(
            self, f"Move {Path(path).name} to", str(Path(path).parent))
        if not folder:
            return
        try:
            self._project_moved(path, move_project(path, folder))
        except ProjectError as exc:
            QMessageBox.warning(self, "Move project", str(exc))

    def delete_selected_recent(self) -> None:
        path = self._selected_recent()
        if not path:
            return
        answer = QMessageBox.question(
            self, "Delete project",
            f"Move the project {Path(path).name} to the Recycle Bin?\n\n"
            f"The whole folder goes, with its runs and data:\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self.move_to_trash(path):
            QMessageBox.warning(
                self, "Delete project",
                f"Could not move {path} to the Recycle Bin. Is a file of the project "
                "open in another program? Nothing was deleted.")
            return
        self._forget_project(path)
        self._fill_recent()

    @staticmethod
    def move_to_trash(path: str) -> bool:
        """Move a folder to the Recycle Bin (the trash elsewhere), never deleting it
        for good."""
        ok, _ = QFile.moveToTrash(path)
        return bool(ok)

    def _project_moved(self, old: str, new: Path) -> None:
        """A project got another folder: keep its place in the recent list, its plot
        choices and whether its plugins are trusted."""
        new_path = str(new)
        recent = [new_path if p == old else p for p in self._recent_entries()]
        self.settings.setValue("recent", recent[:MAX_RECENT])
        self.settings.setValue("pinned", [new_path if p == old else p for p in self._pinned()])
        for kind in ("plot_right_axis", "plot_log_x", "plot_hidden"):
            old_key, new_key = _plot_key(kind, old), _plot_key(kind, new_path)
            if self.settings.contains(old_key):
                self.settings.setValue(new_key, self.settings.value(old_key))
                self.settings.remove(old_key)
        config = AppConfig.load()
        if config.trusts(old):
            config.set_trusted(old, False)
            config.set_trusted(new_path)
            config.save()
        self._fill_recent(select=new_path)

    def _forget_project(self, path: str) -> None:
        self.settings.setValue("recent", [p for p in self._recent_entries() if p != path])
        self.settings.setValue("pinned", [p for p in self._pinned() if p != path])
        for kind in ("plot_right_axis", "plot_log_x", "plot_hidden"):
            self.settings.remove(_plot_key(kind, path))
        config = AppConfig.load()
        if config.trusts(path):
            config.set_trusted(path, False)
            config.save()

    def _set_project(self, project: Project | None) -> None:
        self.project = project
        self.devices.set_project(project)
        panel = None
        if project is not None:
            try:
                panel = Panel.load(project.path)
            except PanelError as exc:
                QMessageBox.warning(self, "Open project", f"Could not read the front panel: {exc}")
                panel = Panel(project.path / "panel.json")
        self.panel_view.set_panel(panel)
        self.update_completions()
        self.console.clear()
        self.plot.clear()
        self.table.clear()
        self.waveforms.clear()
        self.clear_comparison()
        self.runs.set_project(project.path if project is not None else None)
        self._apply_plot_settings(project)
        self.editor.mark_error(None)
        self._last_run = None
        for dock in self._docks:
            dock.setVisible(project is not None)
        if project is None:
            self.setWindowTitle("Benchloom")
            self._fill_recent()
            self.stack.setCurrentIndex(0)
        else:
            self.setWindowTitle(f"{project.name} — Benchloom")
            if project.mode == "lua":
                script = project.path / project.script
                self.editor.setPlainText(
                    script.read_text(encoding="utf-8") if script.is_file() else ""
                )
                self.editor.document().setModified(False)
                self.stack.setCurrentIndex(1)
            else:
                self._load_graph()
                self.stack.setCurrentIndex(2)
            self._remember(project.path)
            (self._docks[3] if panel.widgets else self._docks[1]).raise_()
        self._update_actions()

    def _update_actions(self) -> None:
        is_open = self.project is not None
        running = self.runner.running
        self.save_action.setEnabled(is_open)
        self.run_action.setEnabled(is_open and not running)
        self.lua_action.setEnabled(is_open and self.project.mode == "visual")
        self.stop_action.setEnabled(running)
        self.runs_action.setEnabled(self._last_run is not None)
        self.report_action.setEnabled(self._last_run is not None
                                      and (self._last_run / "report.html").is_file())
        self.new_action.setEnabled(not running)
        self.open_action.setEnabled(not running)
        self.devices.setEnabled(not running)
        self.plugins_action.setEnabled(not running)

    def _keep_changes(self) -> bool:
        """Offer to save unsaved edits. False means the user cancelled."""
        if not self._modified():
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes", "Save the changes to the test?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            self.save()
        return answer != QMessageBox.StandardButton.Cancel

    def new_project(self) -> None:
        dialog = NewProjectDialog(self, self.prefs.projects_dir())
        accepted = dialog.exec()
        chosen = (dialog.path(), dialog.mode(), dialog.template()) if accepted else None
        dialog.deleteLater()
        if chosen and self._keep_changes():
            try:
                self._set_project(create_project(*chosen))
            except (ProjectError, OSError) as exc:
                QMessageBox.critical(self, "New project", str(exc))

    def open_project_dialog(self) -> None:
        start = str(self.project.path.parent if self.project else self.prefs.projects_dir())
        folder = QFileDialog.getExistingDirectory(self, "Open project folder", start)
        if folder:
            self.open_project(folder)

    def open_project(self, path: str | Path) -> bool:
        if not self._keep_changes():
            return False
        try:
            project = Project.load(path)
            self._ask_about_project_plugins(project)
            self._set_project(project)
            return True
        except ProjectError as exc:
            QMessageBox.critical(self, "Open project", str(exc))
            return False

    def _ask_about_project_plugins(self, project: Project) -> None:
        """A project's own plugins are Python code: load them only if the user says so.

        Asked each time the project is opened until the answer is yes; nothing of the
        plugins has run when the question appears.
        """
        config = AppConfig.load()
        blocked = PluginRegistry.default(project.path, config).blocked
        if blocked and self.confirm_project_plugins(project, blocked):
            config.set_trusted(project.path)
            config.save()

    def confirm_project_plugins(self, project: Project, plugins: list) -> bool:
        known = PluginRegistry.default().plugins
        items = "".join(
            f"<li><b>{html.escape(p.name)}</b>"
            + (" (replaces a plugin that Benchloom already has)"
               if p.id in known else "") + "</li>" for p in plugins)
        box = QMessageBox(self)
        box.setWindowTitle("Plugins in this project")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<p>The project <b>{html.escape(project.name)}</b> brings its own "
                    f"plugins:</p><ul>{items}</ul>")
        box.setInformativeText(
            "Plugins are Python code and run with your permissions, unlike the test itself. "
            "Load them only if you trust where this project comes from.\n\n"
            "Without them the project opens, but devices that need these plugins do not work.")
        load = box.addButton("Load plugins", QMessageBox.ButtonRole.AcceptRole)
        skip = box.addButton("Open without them", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(skip)
        box.setEscapeButton(skip)
        box.exec()
        allowed = box.clickedButton() is load
        box.deleteLater()
        return allowed

    def _modified(self) -> bool:
        if self.project is None:
            return False
        if self.project.mode == "lua":
            return self.editor.document().isModified()
        return self.graph.modified

    def save(self) -> None:
        if self.project is None:
            return
        script = self.project.path / self.project.script
        if self.project.mode == "lua":
            script.write_text(self.editor.toPlainText(), encoding="utf-8")
            self.editor.document().setModified(False)
        else:
            script.write_text(json.dumps(self.graph.to_graph(), indent=1), encoding="utf-8")
            self.graph.modified = False
        try:
            stamp_version(self.project.path)
        except (ProjectError, OSError):
            pass  # the test is saved; only the note of the version is missing

    # --- visual projects ----------------------------------------------------
    def _load_graph(self) -> None:
        script = self.project.path / self.project.script
        try:
            graph = json.loads(script.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Open project", f"Could not read {script.name}: {exc}")
            graph = {}
        self.graph.load(graph, self._catalog())

    def _catalog(self) -> Catalog:
        return Catalog(self.project, self.devices.registry, self.panel_view.panel)

    def _devices_changed(self) -> None:
        """Devices were added, renamed or removed: rebuild nodes against the new list."""
        if self.project and self.project.mode == "visual":
            modified = self.graph.modified
            self.graph.load(self.graph.to_graph(), self._catalog())
            self.graph.modified = modified

    def _line_running(self, line: int) -> None:
        if self.project is not None and self.project.mode == "lua":
            self.editor.mark_running(line or None)

    def update_completions(self, *_) -> None:
        """Tell the editor's autocomplete the devices, their commands and the panel."""
        from .devices import describe, labelled_signature
        from .editor import Completion

        devices = {
            name: [Completion(f"{info.name}(" + ("" if info.params else ")"),
                              labelled_signature(info), describe(info))
                   for info in infos]
            for name, infos in self.devices.commands.items()}
        panel = self.panel_view.panel
        names = [w.name for w in panel.widgets if w.has_value] if panel else []
        self.editor.set_names(devices, names)

    def _widget_renamed(self, old: str, new: str) -> None:
        """Keep the test working when a front panel widget gets another name."""
        if not self.project:
            return
        if self.project.mode == "visual":
            graph = self.graph.to_graph()
            if rename_in_graph(graph, old, new):
                self.graph.load(graph, self._catalog())
                self.graph.modified = True
                self.statusBar().showMessage(f"The nodes now use panel.{new}", 5000)
            return
        code, count = rename_in_lua(self.editor.toPlainText(), old, new)
        if not count:
            return
        answer = QMessageBox.question(
            self, "Rename in the script",
            f"{self.project.script} uses panel.{old} in {count} place(s). "
            f"Change them to panel.{new}?")
        if answer == QMessageBox.StandardButton.Yes:
            cursor = self.editor.textCursor()
            cursor.beginEditBlock()  # one step to undo
            cursor.select(cursor.SelectionType.Document)
            cursor.insertText(code)
            cursor.endEditBlock()

    def _compile(self) -> str | None:
        """Compile the graph; on a problem, mark the node and return None."""
        self.graph.mark_error(None)
        try:
            code, self._line_map = compile_graph(self.graph.to_graph(), self.graph.catalog)
            return code
        except GraphError as exc:
            self.graph.mark_error(exc.node_id)
            self._print(f"Error: {exc}", error=True)
            self.statusBar().showMessage("The test has a problem; see the marked node")
            return None

    def view_lua(self) -> None:
        code = self._compile()
        if code is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("This test as Lua")
        dialog.resize(640, 480)
        view = CodeEditor()
        view.setPlainText(code)
        view.setReadOnly(True)
        QVBoxLayout(dialog).addWidget(view)
        dialog.exec()
        dialog.deleteLater()

    # --- running ----------------------------------------------------------
    def run(self) -> None:
        if not self.project or self.runner.running:
            return
        self.console.clear()
        self.plot.clear()
        self.table.clear()
        self.waveforms.clear()
        self._failed_checks = self._checks = 0
        self.editor.mark_error(None)
        if self.project.mode == "visual" and self._compile() is None:
            return
        self.save()
        self.panel_view.panel.save()  # the controls' current values become their saved values
        self.panel_view.start_run()
        self.runner.start(self.project.path, self.panel_view.control_values())
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
        elif text.startswith("FAIL  "):
            self.console.appendHtml(
                f'<span style="color:#d9453c;font-weight:600">{html.escape(text)}</span>')
        elif text.startswith("PASS  "):
            self.console.appendHtml(f'<span style="color:#16a34a">{html.escape(text)}</span>')
        else:
            self.console.appendPlainText(text)

    def _check_done(self, record: dict) -> None:
        self._checks += 1
        self._failed_checks += not record.get("passed")

    def _run_finished(self, status: str, error: str, path: str) -> None:
        self.runs.refresh(select=Path(path) if path else None)
        self.table.flush()
        self.graph.mark_running(None)
        self.editor.mark_running(None)
        self._last_run = Path(path) if path else None
        if error and not path:  # failed before the engine could report it itself
            self._print(f"Error: {error}", error=True)
        if status == "error" and self.project:
            name = self.project.script if self.project.mode == "lua" else GRAPH_CHUNK
            match = re.search(rf"{re.escape(name)}:(\d+):", error)
            line = int(match.group(1)) if match else 0
            if line and self.project.mode == "lua":
                self.editor.mark_error(line)
            elif 0 < line <= len(self._line_map):
                self.graph.mark_error(self._line_map[line - 1])
        rows = len(self.plot.rows)
        message = {"ok": "Finished", "stopped": "Stopped", "error": "Failed"}[status]
        parts = [message, f"{rows} row(s) logged"]
        if self._checks:
            verdict = "FAIL" if self._failed_checks else "PASS"
            parts.insert(0, verdict)
            parts.append(f"{self._checks - self._failed_checks} of {self._checks} checks passed")
            self._print(f"{verdict}: {self._checks - self._failed_checks} of {self._checks} "
                        "checks passed", error=bool(self._failed_checks))
        if self.waveforms.waveforms:
            parts.append(f"{len(self.waveforms.waveforms)} waveform(s) saved")
        failed = _shutdown_failed(Path(path)) if path else []
        if failed:  # last, so a PASS above does not hide it
            self._print(f"WARNING: shutting down {', '.join(failed)} failed; check that "
                        "their outputs are off.", error=True)
            parts.append(f"shutdown failed: {', '.join(failed)}")
        self.statusBar().showMessage(" · ".join(parts))
        self._update_actions()
        if self._close_when_stopped:
            self._close_when_stopped = False
            self._closing = True  # unsaved changes were asked about already
            self.close()

    # --- past runs ------------------------------------------------------------
    def _run_label(self, run_dir: Path) -> str:
        from ..history import read_run

        run = read_run(run_dir)
        return run.started.strftime("%Y-%m-%d %H:%M:%S") if run.started else run_dir.name

    def show_run(self, run_dir) -> None:
        """Show a past run's data in the plot and the Data tab, and its console."""
        if self.runner.running:
            self.statusBar().showMessage("A test is running; show past runs after it.", 5000)
            return
        from ..history import read_rows

        run_dir = Path(run_dir)
        self.plot.clear()
        self.table.clear()
        self.waveforms.clear()
        for row in read_rows(run_dir):
            self.plot.add_row(row)
            self.table.add_row(row)
        self.table.flush()
        self.console.clear()
        try:
            self.console.setPlainText((run_dir / "console.log").read_text(encoding="utf-8"))
        except OSError:
            pass
        self._last_run = run_dir
        self._update_actions()
        self._docks[1].raise_()
        self.statusBar().showMessage(f"Showing the run of {self._run_label(run_dir)}")

    def compare_run(self, run_dir) -> None:
        """Draw a past run's curves dashed next to the current ones."""
        from ..history import read_rows

        run_dir = Path(run_dir)
        label = self._run_label(run_dir)
        self.plot.set_reference(read_rows(run_dir), label)
        self.runs.set_comparing(True)
        self._docks[1].raise_()
        self.statusBar().showMessage(f"Comparing with the run of {label} (dashed). Compare "
                                     "again with another run, or Clear comparison.", 8000)

    def clear_comparison(self) -> None:
        self.plot.set_reference([])
        self.runs.set_comparing(False)

    def show_report(self) -> None:
        if self._last_run and (self._last_run / "report.html").is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_run / "report.html")))

    def show_run_folder(self) -> None:
        if self._last_run:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_run)))

    def _command_activated(self, device: str, command: str, snippet: str) -> None:
        if self.stack.currentWidget() is self.editor:
            self.editor.insertPlainText(snippet)
            self.editor.setFocus()
        elif self.stack.currentWidget() is self.graph:
            self.graph.add_node({"type": "device", "device": device, "command": command})

    def closeEvent(self, event) -> None:
        if not self._closing and not self._keep_changes():
            event.ignore()
            return
        if self.runner.running:
            # Not killed: the run switches its outputs off first, then the window closes.
            self._close_when_stopped = True
            self.stop()
            self.statusBar().showMessage("Stopping the test and switching the devices off…")
            event.ignore()
            return
        event.accept()
