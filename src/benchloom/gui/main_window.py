# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The Benchloom main window."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QRadioButton, QSizePolicy, QStackedWidget, QToolBar,
    QToolButton, QVBoxLayout, QWidget,
)

from .. import HOMEPAGE, NOTICE, __version__
from ..config import AppConfig
from ..engine import GRAPH_CHUNK
from ..graph import Catalog, GraphError, compile_graph
from ..panel import Panel, PanelError, rename_in_graph, rename_in_lua
from ..plugin import PluginRegistry
from ..project import Project, ProjectError, create_project, is_project, templates
from .devices import DevicesPanel
from .editor import CodeEditor, mono_font
from .graph_editor import GraphEditor
from .panel_view import PanelView
from .plot import PlotPanel
from .table import DataTable
from .waveforms import WaveformPanel
from .plugins import PluginManagerDialog
from .runner import RunController
from .settings import (  # noqa: F401
    GuiSettings, SettingsDialog, app_settings, default_projects_dir,
)
from .theme import THEMES, apply_theme

MAX_RECENT = 8


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
        name = self.name_edit.text().strip()
        if not name or re.search(r'[\\/:*?"<>|]', name):
            QMessageBox.warning(self, "Name", "Enter a project name without special characters.")
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

        self.devices = DevicesPanel()
        self.devices.command_activated.connect(self._command_activated)
        self.devices.changed.connect(self._devices_changed)
        self.console = QPlainTextEdit(readOnly=True)
        self.plot = PlotPanel()
        self.plot.right_columns_changed.connect(self._right_columns_changed)
        self.plot.log_x_changed.connect(self._log_x_changed)
        self.plot.hidden_columns_changed.connect(self._hidden_columns_changed)
        self.panel_view = PanelView()
        self.waveforms = WaveformPanel()
        self.table = DataTable()
        self._docks = [
            self._dock("Devices", self.devices, Qt.DockWidgetArea.LeftDockWidgetArea),
            self._dock("Plot", self.plot, Qt.DockWidgetArea.RightDockWidgetArea),
            self._dock("Console", self.console, Qt.DockWidgetArea.BottomDockWidgetArea),
            self._dock("Front panel", self.panel_view, Qt.DockWidgetArea.RightDockWidgetArea),
            self._dock("Waveforms", self.waveforms, Qt.DockWidgetArea.RightDockWidgetArea),
            self._dock("Data", self.table, Qt.DockWidgetArea.RightDockWidgetArea),
        ]
        self.tabifyDockWidget(self._docks[1], self._docks[5])
        self.tabifyDockWidget(self._docks[5], self._docks[3])
        self.tabifyDockWidget(self._docks[3], self._docks[4])
        self._docks[1].raise_()
        self.resizeDocks(self._docks[:2], [280, 460], Qt.Orientation.Horizontal)
        self.resizeDocks(self._docks[2:3], [200], Qt.Orientation.Vertical)

        self.runner = RunController(self)
        self.runner.printed.connect(self._print)
        self.runner.row.connect(self.plot.add_row)
        self.runner.row.connect(self.table.add_row)
        self.runner.panel.connect(self.panel_view.show_value)
        self.runner.waveform.connect(self.waveforms.add)
        self.runner.node.connect(lambda node_id: self.graph.mark_running(node_id or None))
        self.runner.check.connect(self._check_done)
        self._failed_checks = self._checks = 0
        self.panel_view.control_changed.connect(self.runner.send_control)
        self.panel_view.renamed.connect(self._widget_renamed)
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
        box.exec()

    def open_settings(self) -> bool:
        dialog = SettingsDialog(self.prefs, self)
        if not dialog.exec():
            return False
        self.set_theme(self.theme())
        self.apply_preferences()
        if dialog.plugin_folders_changed:
            self.reload_plugins()
        return True

    def open_plugin_manager(self) -> None:
        dialog = PluginManagerDialog(self.project.path if self.project else None, self)
        dialog.exec()
        if dialog.changed:
            self.reload_plugins()

    def reload_plugins(self) -> None:
        """Plugins changed: rebuild the device list and the node catalog."""
        if self.project is not None:
            self.devices.set_project(self.project)
            self._devices_changed()

    def _system_theme_changed(self, *_) -> None:
        if self.theme() == "system":
            apply_theme("system")

    # --- plot settings per project ------------------------------------------
    def _plot_key(self, kind: str = "plot_right_axis") -> str:
        return kind + "/" + str(self.project.path).replace("\\", "/").replace("/", "|")

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
    def _recent(self) -> list[str]:
        value = self.settings.value("recent", [])
        paths = [value] if isinstance(value, str) else list(value or [])
        return [p for p in paths if is_project(p)]

    def _remember(self, path: Path) -> None:
        recent = [str(path)] + [p for p in self._recent() if p != str(path)]
        self.settings.setValue("recent", recent[:MAX_RECENT])

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
        self.console.clear()
        self.plot.clear()
        self.table.clear()
        self.waveforms.clear()
        self._apply_plot_settings(project)
        self.editor.mark_error(None)
        self._last_run = None
        for dock in self._docks:
            dock.setVisible(project is not None)
        if project is None:
            self.setWindowTitle("Benchloom")
            recent = self._recent()
            self.recent_list.clear()
            self.recent_list.addItems(recent)
            self.recent_label.setVisible(bool(recent))
            self.recent_list.setVisible(bool(recent))
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
        if dialog.exec() and self._keep_changes():
            try:
                self._set_project(create_project(dialog.path(), dialog.mode(),
                                                 dialog.template()))
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
        return box.clickedButton() is load

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
        self.table.flush()
        self.graph.mark_running(None)
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
        self.statusBar().showMessage(" · ".join(parts))
        self._update_actions()
        if self._close_when_stopped:
            self._close_when_stopped = False
            self._closing = True  # unsaved changes were asked about already
            self.close()

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
