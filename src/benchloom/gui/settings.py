# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The Settings window and the desktop-app settings it edits.

Desktop-only settings live in QSettings (see :class:`GuiSettings`); the extra
plugin folders live in the app config file because test runs need them too.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from ..config import AppConfig, home_dir, user_plugin_dir
from .theme import THEMES


def default_projects_dir() -> Path:
    folder = Path.home() / "Documents" / "Benchloom"
    legacy = Path.home() / "Documents" / "Patchbay"  # before the rename
    return legacy if legacy.is_dir() and not folder.exists() else folder


def app_settings():
    """The desktop app's QSettings. The first time, settings saved under the old name
    Patchbay (recent projects, theme, plot choices) are copied over."""
    settings = QSettings("Benchloom", "Benchloom")
    if not settings.allKeys():
        old = QSettings("Patchbay", "Patchbay")
        for key in old.allKeys():
            settings.setValue(key, old.value(key))
        settings.sync()
    return settings


class GuiSettings:
    """Typed access to the desktop app's QSettings."""

    DEFAULTS = {"theme": "system", "projects_dir": "", "reopen_last": False,
                "check_updates": True, "skipped_version": "", "last_update_check": "",
                "editor_font_size": 11, "console_font_size": 10, "console_lines": 10000}
    LIMITS = {"editor_font_size": (6, 40), "console_font_size": (6, 40),
              "console_lines": (500, 1_000_000)}

    def __init__(self, store: QSettings):
        self.store = store

    def get(self, key: str):
        default = self.DEFAULTS[key]
        value = self.store.value(key, default)
        if isinstance(default, bool):
            return value if isinstance(value, bool) else str(value).lower() == "true"
        if isinstance(default, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                return default
            low, high = self.LIMITS[key]
            return min(max(value, low), high)
        value = str(value)
        if key == "theme" and value not in THEMES:
            return default
        return value

    def set(self, key: str, value) -> None:
        self.store.setValue(key, value)

    def projects_dir(self) -> Path:
        return Path(self.get("projects_dir") or default_projects_dir())


class SettingsDialog(QDialog):
    def __init__(self, settings: GuiSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self.settings = settings
        self.config = AppConfig.load()

        tabs = QTabWidget()
        tabs.addTab(self._general_page(), "General")
        tabs.addTab(self._editor_page(), "Editor and console")
        tabs.addTab(self._plugins_page(), "Plugins")
        self.tabs = tabs

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel
                                   | QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self.restore_defaults)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)
        self._load()

    # --- pages ------------------------------------------------------------
    def _general_page(self) -> QWidget:
        self.theme_combo = QComboBox()
        for key, label in THEMES.items():
            self.theme_combo.addItem(label, key)
        self.projects_edit = QLineEdit()
        self.projects_edit.setPlaceholderText(str(default_projects_dir()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_projects)
        row = QHBoxLayout()
        row.addWidget(self.projects_edit, 1)
        row.addWidget(browse)
        self.reopen_check = QCheckBox("Open the last project when Benchloom starts")
        self.updates_check = QCheckBox("Check for new versions when Benchloom starts")
        page = QWidget()
        form = QFormLayout(page)
        form.addRow("Theme", self.theme_combo)
        form.addRow("Projects folder", row)
        form.addRow(self._hint("Where new projects go and where Open project starts."))
        form.addRow("", self.reopen_check)
        form.addRow("", self.updates_check)
        form.addRow(self._hint("At most once a day, Benchloom asks GitHub for its latest "
                               "release and shows a bar if there is a newer one. Nothing "
                               "about you or your projects is sent. About → Check for "
                               "updates checks at any time."))
        return page

    def _editor_page(self) -> QWidget:
        def spin(key, suffix=""):
            box = QSpinBox()
            box.setRange(*GuiSettings.LIMITS[key])
            box.setSuffix(suffix)
            return box

        self.editor_font = spin("editor_font_size", " pt")
        self.console_font = spin("console_font_size", " pt")
        self.console_lines = spin("console_lines", " lines")
        self.console_lines.setSingleStep(1000)
        page = QWidget()
        form = QFormLayout(page)
        form.addRow("Lua editor font size", self.editor_font)
        form.addRow("Console font size", self.console_font)
        form.addRow("Console keeps the last", self.console_lines)
        form.addRow(self._hint("Older console lines are dropped; every run's full log is "
                               "kept in its run folder."))
        return page

    def _plugins_page(self) -> QWidget:
        user = QLabel(f"<a href='{QUrl.fromLocalFile(str(user_plugin_dir())).toString()}'>"
                      f"{user_plugin_dir()}</a>")
        user.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        user.linkActivated.connect(self._open_folder)
        self.folder_list = QListWidget()
        add = QPushButton("Add folder…")
        self.remove_folder_button = QPushButton("Remove")
        add.clicked.connect(self._add_folder)
        self.remove_folder_button.clicked.connect(self._remove_folder)
        self.folder_list.currentRowChanged.connect(
            lambda row: self.remove_folder_button.setEnabled(row >= 0))
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(self.remove_folder_button)
        buttons.addStretch()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Installed plugins are kept in:"))
        layout.addWidget(user)
        layout.addSpacing(8)
        layout.addWidget(QLabel("Also load plugins from these folders:"))
        layout.addWidget(self.folder_list, 1)
        layout.addLayout(buttons)
        layout.addWidget(self._hint(
            "Each folder holds plugin folders, the way a project's plugins folder does. "
            "Useful while writing a plugin: changes are picked up without installing it. "
            f"These folders are saved in {home_dir() / 'config.json'} and apply to the "
            "command line too."))
        return page

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setEnabled(False)
        return label

    # --- actions ----------------------------------------------------------
    def _browse_projects(self) -> None:
        start = self.projects_edit.text() or str(default_projects_dir())
        folder = QFileDialog.getExistingDirectory(self, "Projects folder", start)
        if folder:
            self.projects_edit.setText(folder)

    def _open_folder(self, url: str) -> None:
        user_plugin_dir().mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl(url))

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Plugin folder")
        if folder:
            self.add_plugin_folder(folder)

    def add_plugin_folder(self, folder: str) -> None:
        existing = [self.folder_list.item(i).text() for i in range(self.folder_list.count())]
        if str(Path(folder)) not in existing:
            self.folder_list.addItem(str(Path(folder)))

    def _remove_folder(self) -> None:
        row = self.folder_list.currentRow()
        if row >= 0:
            self.folder_list.takeItem(row)

    # --- load and save ------------------------------------------------------
    def _load(self, defaults: bool = False) -> None:
        get = (lambda key: GuiSettings.DEFAULTS[key]) if defaults else self.settings.get
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(get("theme")))
        self.projects_edit.setText(get("projects_dir"))
        self.reopen_check.setChecked(get("reopen_last"))
        self.updates_check.setChecked(get("check_updates"))
        self.editor_font.setValue(get("editor_font_size"))
        self.console_font.setValue(get("console_font_size"))
        self.console_lines.setValue(get("console_lines"))
        if not defaults:
            self.folder_list.clear()
            self.folder_list.addItems(self.config.plugin_paths)
        self.remove_folder_button.setEnabled(self.folder_list.currentRow() >= 0)

    def restore_defaults(self) -> None:
        """Reset the fields (not the plugin folders); nothing is saved until OK."""
        self._load(defaults=True)

    def plugin_folders(self) -> list[str]:
        return [self.folder_list.item(i).text() for i in range(self.folder_list.count())]

    def accept(self) -> None:
        values = {
            "theme": self.theme_combo.currentData(),
            "projects_dir": self.projects_edit.text().strip(),
            "reopen_last": self.reopen_check.isChecked(),
            "check_updates": self.updates_check.isChecked(),
            "editor_font_size": self.editor_font.value(),
            "console_font_size": self.console_font.value(),
            "console_lines": self.console_lines.value(),
        }
        for key, value in values.items():
            self.settings.set(key, value)
        self.plugin_folders_changed = self.plugin_folders() != self.config.plugin_paths
        if self.plugin_folders_changed:
            config = AppConfig.load()  # the plugin manager may have changed other keys
            config.plugin_paths = self.plugin_folders()
            config.save()
        super().accept()
