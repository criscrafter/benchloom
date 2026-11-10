# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The plugin manager: see, switch off, install and remove plugins."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QFile, Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import plugin_install
from ..config import AppConfig, user_plugin_dir
from ..plugin import SOURCES, DeviceSpec, PluginError, PluginInfo, PluginRegistry

ERROR_COLOR = QColor("#d9453c")

STATUS = {"enabled": "On", "disabled": "Switched off",
          "replaced": "Not used: a plugin with the same id is loaded after it"}


def move_to_trash(path: Path) -> bool:
    return QFile.moveToTrash(str(path))[0] if hasattr(QFile, "moveToTrash") else False


class _Field(QLineEdit):
    """A read-only one-line field; long values can be scrolled and copied."""

    def __init__(self):
        super().__init__(readOnly=True)

    def show_text(self, text: str) -> None:
        self.setText(text)
        self.setCursorPosition(0)
        self.setToolTip(text if len(text) > 40 else "")


class _TextField(QPlainTextEdit):
    """A read-only multi-line field for descriptions."""

    def __init__(self, lines: int = 4):
        super().__init__(readOnly=True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * lines + 12)


def setting_text(spec) -> str:
    """'Modbus address (int, 1 to 247, default 1)'."""
    parts = [{"int": "whole number", "float": "number", "bool": "on/off"}.get(spec.type, spec.type)]
    if spec.min is not None and spec.max is not None:
        parts.append(f"{spec.min:g} to {spec.max:g}")
    elif spec.min is not None:
        parts.append(f"at least {spec.min:g}")
    elif spec.max is not None:
        parts.append(f"at most {spec.max:g}")
    if isinstance(spec.default, bool):
        parts.append("default " + ("on" if spec.default else "off"))
    elif spec.default is not None:
        parts.append(f"default {spec.default:g}" if isinstance(spec.default, float)
                     else f"default {spec.default}")
    return f"{spec.label} ({', '.join(parts)})"


class PluginDetails(QWidget):
    """Fields describing one plugin and the device selected in its device list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.plugin_box = QGroupBox("Plugin")
        self.name_field, self.version_field, self.id_field = _Field(), _Field(), _Field()
        self.status_field, self.source_field, self.folder_field = _Field(), _Field(), _Field()
        self.description_field = _TextField(3)
        self.open_folder_button = QPushButton("Open")
        self.open_folder_button.clicked.connect(self._open_folder)
        folder = QHBoxLayout()
        folder.addWidget(self.folder_field, 1)
        folder.addWidget(self.open_folder_button)
        form = self.plugin_form = QFormLayout(self.plugin_box)
        form.addRow("Name", self.name_field)
        form.addRow("Version", self.version_field)
        form.addRow("Id", self.id_field)
        form.addRow("Status", self.status_field)
        form.addRow("Source", self.source_field)
        form.addRow("Folder", folder)
        self.description_label = QLabel("Description")
        form.addRow(self.description_label, self.description_field)

        self.device_box = QGroupBox("Devices")
        self.device_list = QListWidget()
        self.device_list.currentItemChanged.connect(self._show_device)
        self.device_name, self.device_type = _Field(), _Field()
        self.device_connections, self.device_default_name = _Field(), _Field()
        self.device_resource, self.device_simulator = _Field(), _Field()
        self.device_settings = _TextField(3)
        self.device_description = _TextField(4)
        device_form = self.device_form = QFormLayout()
        device_form.addRow("Name", self.device_name)
        device_form.addRow("Device type", self.device_type)
        device_form.addRow("Connections", self.device_connections)
        device_form.addRow("Default name", self.device_default_name)
        device_form.addRow("Default address", self.device_resource)
        device_form.addRow("Simulator", self.device_simulator)
        device_form.addRow("Settings", self.device_settings)
        device_form.addRow("Description", self.device_description)
        column = QVBoxLayout(self.device_box)
        column.addWidget(self.device_list)
        column.addLayout(device_form)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plugin_box)
        layout.addWidget(self.device_box)
        layout.addStretch()
        for box in (self.plugin_box, self.device_box):  # spare height goes to the stretch
            box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._folder: Path | None = None
        self.clear()

    def clear(self) -> None:
        for field in (self.name_field, self.version_field, self.id_field, self.status_field,
                      self.source_field, self.folder_field):
            field.clear()
        self.description_field.clear()
        self.description_label.setText("Description")
        self.status_field.setStyleSheet("")
        self._folder = None
        self.open_folder_button.setEnabled(False)
        self.device_list.clear()
        self.device_box.setVisible(False)
        for row in range(self.plugin_form.rowCount()):
            self.plugin_form.setRowVisible(row, True)

    def _hide_empty_rows(self, form: QFormLayout, optional) -> None:
        """Hide the rows of ``optional`` fields that have nothing to show."""
        for field in optional:
            text = field.text() if isinstance(field, QLineEdit) else field.toPlainText()
            form.setRowVisible(field, bool(text))

    def show_plugin(self, info: PluginInfo, status: str) -> None:
        self.clear()
        self.name_field.show_text(info.name)
        self.version_field.show_text(info.version)
        self.id_field.show_text(info.id)
        self.status_field.show_text(STATUS[status])
        self.source_field.show_text(SOURCES.get(info.source, info.source))
        self._set_folder(Path(info.path))
        self.description_field.setPlainText(info.description)
        self._hide_empty_rows(self.plugin_form, [self.description_field])
        self.device_box.setTitle(f"Devices ({len(info.devices)})")
        self.device_box.setVisible(bool(info.devices))
        for device in info.devices:
            item = QListWidgetItem(device.name)
            item.setData(Qt.ItemDataRole.UserRole, device)
            self.device_list.addItem(item)
        rows = min(max(len(info.devices), 1), 4)
        self.device_list.setFixedHeight(rows * self.device_list.sizeHintForRow(0)
                                        + 2 * self.device_list.frameWidth() + 2)
        if info.devices:
            self.device_list.setCurrentRow(0)

    def show_broken(self, folder: Path, error: str) -> None:
        self.clear()
        self.name_field.show_text(folder.name)
        self.status_field.show_text("Failed to load")
        self.status_field.setStyleSheet(f"color: {ERROR_COLOR.name()}")
        self._set_folder(folder)
        self.description_label.setText("Error")
        # the folder is shown above, so drop it from the start of the message
        manifest = str(folder / "plugin.toml") + ": "
        self.description_field.setPlainText(error.removeprefix(manifest))
        for field in (self.version_field, self.id_field, self.source_field):
            self.plugin_form.setRowVisible(field, False)

    def _set_folder(self, folder: Path) -> None:
        self._folder = folder
        self.folder_field.show_text(str(folder))
        self.open_folder_button.setEnabled(True)

    def _open_folder(self) -> None:
        if self._folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder)))

    def _show_device(self, item, _=None) -> None:
        device: DeviceSpec | None = item.data(Qt.ItemDataRole.UserRole) if item else None
        if device is None:
            return
        self.device_name.show_text(device.name)
        self.device_type.show_text(device.id)
        self.device_connections.show_text(", ".join(device.transports) or "any")
        self.device_default_name.show_text(device.default_name)
        self.device_resource.show_text(device.default_resource)
        self.device_simulator.show_text("Yes" if device.simulator else "No")
        self.device_settings.setPlainText(
            "\n".join(setting_text(spec) for spec in device.settings))
        self.device_description.setPlainText(device.description)
        self._hide_empty_rows(self.device_form, [
            self.device_default_name, self.device_resource, self.device_settings,
            self.device_description])


class PluginManagerDialog(QDialog):
    """Lists every plugin found. ``changed`` is true after the dialog if plugins
    were switched on or off, installed or removed."""

    def __init__(self, project_dir: Path | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plugins")
        self.resize(1000, 720)
        self.project_dir = project_dir
        self.changed = False
        self.registry: PluginRegistry | None = None
        self._filling = False

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Plugin", "Version", "Devices", "Source"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(self._show_details)
        self.details = PluginDetails()
        scroll = QScrollArea()
        scroll.setWidget(self.details)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(scroll)
        splitter.setSizes([440, 520])

        hint = QLabel("Untick a plugin to switch it off. Its devices disappear from the "
                      "Add device list and from runs until it is switched on again.")
        hint.setWordWrap(True)
        hint.setEnabled(False)

        self.install_folder_button = QPushButton("Install from folder…")
        self.install_zip_button = QPushButton("Install from .zip…")
        self.remove_button = QPushButton("Remove")
        self.open_button = QPushButton("Open plugin folder")
        self.install_folder_button.clicked.connect(self.install_from_folder)
        self.install_zip_button.clicked.connect(self.install_from_zip)
        self.remove_button.clicked.connect(self.remove_selected)
        self.open_button.clicked.connect(self.open_user_folder)
        buttons = QHBoxLayout()
        for button in (self.install_folder_button, self.install_zip_button, self.remove_button):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.open_button)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)
        layout.addWidget(hint)
        layout.addLayout(buttons)
        layout.addWidget(close)
        self.reload()

    # --- list -------------------------------------------------------------
    def reload(self, select: str | None = None) -> None:
        current = self.selected()
        select = select or (str(current.path) if current else None)
        self.registry = PluginRegistry.default(self.project_dir)
        self._filling = True
        self.tree.clear()
        for info in self.registry.found:
            status = self.registry.status(info)
            item = QTreeWidgetItem([info.name, info.version, str(len(info.devices)),
                                    SOURCES.get(info.source, info.source)])
            item.setData(0, Qt.ItemDataRole.UserRole, info)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked if status == "disabled"
                               else Qt.CheckState.Checked)
            if status == "replaced":
                item.setToolTip(0, "Not used: a plugin with the same id is loaded after it")
                for column in range(4):
                    item.setForeground(column, self.palette().placeholderText())
            self.tree.addTopLevelItem(item)
            if select and str(info.path) == select:
                self.tree.setCurrentItem(item)
        for folder, error in self.registry.broken:
            item = QTreeWidgetItem([folder.name, "", "", "Failed to load"])
            item.setData(0, Qt.ItemDataRole.UserRole, (folder, error))
            for column in range(4):
                item.setForeground(column, QBrush(ERROR_COLOR))
            item.setToolTip(0, error)
            self.tree.addTopLevelItem(item)
        self._filling = False
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self._show_details()

    def selected(self) -> PluginInfo | None:
        item = self.tree.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return data if isinstance(data, PluginInfo) else None

    def item_for(self, plugin_id: str) -> QTreeWidgetItem | None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, PluginInfo) and data.id == plugin_id:
                return item
        return None

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        info = item.data(0, Qt.ItemDataRole.UserRole)
        if self._filling or column != 0 or not isinstance(info, PluginInfo):
            return
        self.set_enabled(info.id, item.checkState(0) == Qt.CheckState.Checked)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        config = AppConfig.load()
        config.set_enabled(plugin_id, enabled)
        config.save()
        self.changed = True
        # update the rows in place: rebuilding the list from inside its own
        # itemChanged signal would delete the item being changed
        self.registry = PluginRegistry.default(self.project_dir)
        self._filling = True
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            info = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(info, PluginInfo) and info.id == plugin_id:
                item.setCheckState(0, Qt.CheckState.Checked if enabled
                                   else Qt.CheckState.Unchecked)
        self._filling = False
        self._show_details()

    def _show_details(self, *_) -> None:
        item = self.tree.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self.remove_button.setEnabled(isinstance(data, PluginInfo)
                                      and plugin_install.is_removable(data))
        if isinstance(data, tuple):
            self.details.show_broken(*data)
        elif isinstance(data, PluginInfo):
            self.details.show_plugin(data, self.registry.status(data))
        else:
            self.details.clear()

    # --- install and remove -------------------------------------------------
    def install_from_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Install plugin from folder")
        if folder:
            self.install(Path(folder))

    def install_from_zip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Install plugin from .zip", "",
                                              "Plugin archives (*.zip)")
        if path:
            self.install(Path(path))

    def install(self, source: Path, confirm: bool = True) -> PluginInfo | None:
        title = "Install plugin"
        try:
            info = plugin_install.peek(source)
        except PluginError as exc:
            QMessageBox.warning(self, title, str(exc))
            return None
        replace = plugin_install.installed_folder(info.id) is not None
        if confirm:
            devices = "".join(f"<li>{html.escape(d.name)}</li>" for d in info.devices)
            what = "Replace the installed copy of" if replace else "Install"
            overrides = [p for p in self.registry.found
                         if p.id == info.id and p.source == "built-in"]
            note = ("<p>It replaces the built-in plugin with the same id.</p>"
                    if overrides else "")
            answer = QMessageBox.question(
                self, title,
                f"<p>{what} <b>{html.escape(info.name)}</b> {html.escape(info.version)}?</p>"
                f"<ul>{devices}</ul>{note}<p>{html.escape(plugin_install.TRUST_WARNING)}</p>")
            if answer != QMessageBox.StandardButton.Yes:
                return None
        try:
            installed = plugin_install.install(source, replace=replace)
        except (PluginError, OSError) as exc:
            QMessageBox.warning(self, title, f"Could not install the plugin: {exc}")
            return None
        self.changed = True
        self.reload(select=str(installed.path))
        return installed

    def remove_selected(self, confirm: bool = True) -> None:
        info = self.selected()
        if info is None or not plugin_install.is_removable(info):
            return
        if confirm:
            answer = QMessageBox.question(
                self, "Remove plugin",
                f"Remove {info.name}? Its folder goes to the recycle bin, and projects that "
                f"use its devices cannot run until it is installed again.\n\n{info.path}")
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            plugin_install.remove(info, trash=move_to_trash)
        except (PluginError, OSError) as exc:
            QMessageBox.warning(self, "Remove plugin", f"Could not remove the plugin: {exc}")
            return
        self.changed = True
        self.reload()

    def open_user_folder(self) -> None:
        folder = user_plugin_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
