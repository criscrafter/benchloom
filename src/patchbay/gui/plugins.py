"""The plugin manager: see, switch off, install and remove plugins."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QFile, Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from .. import plugin_install
from ..config import AppConfig, user_plugin_dir
from ..plugin import SOURCES, PluginError, PluginInfo, PluginRegistry

ERROR_COLOR = QColor("#d9453c")


def move_to_trash(path: Path) -> bool:
    return QFile.moveToTrash(str(path))[0] if hasattr(QFile, "moveToTrash") else False


class PluginManagerDialog(QDialog):
    """Lists every plugin found. ``changed`` is true after the dialog if plugins
    were switched on or off, installed or removed."""

    def __init__(self, project_dir: Path | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plugins")
        self.resize(900, 560)
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
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        self.details.anchorClicked.connect(QDesktopServices.openUrl)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.details)
        splitter.setSizes([500, 400])

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
            folder, error = data
            self.details.setHtml(f"<h3>{html.escape(folder.name)}</h3>"
                                 f"<p style='color:{ERROR_COLOR.name()}'>{html.escape(error)}</p>"
                                 f"<p>{self._folder_link(folder)}</p>")
            return
        if not isinstance(data, PluginInfo):
            self.details.clear()
            return
        info = data
        status = {"enabled": "On", "disabled": "Switched off",
                  "replaced": "Not used: replaced by a plugin with the same id loaded later"}
        parts = [f"<h3>{html.escape(info.name)} <small>{html.escape(info.version)}</small></h3>",
                 f"<p><b>Id:</b> {html.escape(info.id)}<br>"
                 f"<b>Status:</b> {status[self.registry.status(info)]}<br>"
                 f"<b>Source:</b> {html.escape(SOURCES.get(info.source, info.source))}<br>"
                 f"<b>Folder:</b> {self._folder_link(info.path)}</p>"]
        if info.description:
            parts.append(f"<p>{html.escape(info.description)}</p>")
        for device in info.devices:
            connections = ", ".join(device.transports) or "any"
            parts.append(f"<p><b>{html.escape(device.name)}</b><br>"
                         f"<code>{html.escape(device.id)}</code> · {html.escape(connections)}"
                         + (f"<br>{html.escape(device.description)}"
                            if device.description else "") + "</p>")
        self.details.setHtml("".join(parts))

    @staticmethod
    def _folder_link(folder: Path) -> str:
        url = QUrl.fromLocalFile(str(folder)).toString()
        return f"<a href='{html.escape(url)}'>{html.escape(str(folder))}</a>"

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
