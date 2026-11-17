# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The "Find devices" dialog: look at what is connected, then ask the ports picked.

Also :class:`FindConnectionDialog`, which looks for one device type only (the Find
button of the device dialog).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from ..discovery import Found, Port, ask, look, needs_port, what_is_sent
from ..plugin import DeviceSpec, PluginRegistry

PORT_ROLE = Qt.ItemDataRole.UserRole
MATCH_ROLE = Qt.ItemDataRole.UserRole + 1  # on a match row: (device type, resource)

INTRO = ("Benchloom first only looks: it lists the ports and recognizes USB devices by "
         "their ids, sending nothing. To find out what is on a port, tick it and press "
         "Ask. Asking sends a few short queries, listed below as they go out; on a GPIB "
         "adapter each instrument is first only listened to, then asked *IDN?. Ask only "
         "ports with instruments on them, not ones that drive motors, printers or "
         "other machines. Nothing on a network is scanned.")


class _AskThread(QThread):
    logged = Signal(str)
    done = Signal(object)  # list of (Port, list[Found] or str error)

    def __init__(self, registry: PluginRegistry, ports: list[Port], ask_idn: bool,
                 only: str | None = None):
        super().__init__()
        self.registry, self.ports, self.ask_idn = registry, ports, ask_idn
        self.only = only

    def run(self) -> None:
        results = []
        for port in self.ports:
            self.logged.emit(f"Asking {port.name}…")
            try:
                results.append((port, ask(port, self.registry, log=self.logged.emit,
                                          ask_idn=self.ask_idn, only=self.only)))
            except Exception as exc:  # a port that cannot be opened, a broken plugin
                results.append((port, str(exc)))
                self.logged.emit(f"{port.name}: {exc}")
        self.done.emit(results)


class DiscoveryDialog(QDialog):
    def __init__(self, registry: PluginRegistry, resources: dict[str, str] | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find devices")
        self.resize(820, 640)
        self.registry = registry
        self.resources = resources or {}
        self._thread: _AskThread | None = None

        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        self.ports = QTreeWidget()
        self.ports.setHeaderLabels(["Port", "Description"])
        self.ports.setRootIsDecorated(False)
        self.ports.itemChanged.connect(self._update_buttons)
        self.ask_idn = QCheckBox("Ask silent GPIB instruments *IDN?")
        self.ask_idn.setChecked(True)
        self.ask_idn.setToolTip(
            "Instruments older than IEEE 488.2 do not know *IDN?. The HP 5334 and 5384 "
            "counters stop measuring after an unknown command until a device clear, "
            "which Benchloom sends when *IDN? gets no answer.")
        self.look_button = QPushButton("Look again")
        self.ask_button = QPushButton("Ask ticked ports")
        self.look_button.clicked.connect(self.look)
        self.ask_button.clicked.connect(self.ask)

        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Found", "Details"])
        self.results.itemSelectionChanged.connect(self._update_buttons)
        self.results.itemDoubleClicked.connect(lambda *_: self._add())
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("What was sent, line by line")

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.add_button = self.buttons.addButton("Add device…",
                                                 QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self._add)
        self.buttons.rejected.connect(self.reject)

        port_row = QHBoxLayout()
        port_row.addWidget(self.ask_idn, 1)
        port_row.addWidget(self.look_button)
        port_row.addWidget(self.ask_button)
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(self.ports, 1)
        top_layout.addLayout(port_row)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(self.results)
        splitter.addWidget(self.log)
        splitter.setSizes([200, 220, 120])
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.buttons)
        self.look()

    # --- looking and asking -------------------------------------------------------
    def look(self) -> None:
        result = look(self.registry, self.resources)
        self.ports.blockSignals(True)
        self.ports.clear()
        for port in result.ports:
            item = QTreeWidgetItem([port.name, port.description
                                    + (f"  [{port.usb_id}]" if port.usb_id else "")])
            item.setData(0, PORT_ROLE, port)
            if port.why_not:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setText(1, f"{item.text(1)}  (not asked: {port.why_not})")
                item.setToolTip(0, port.why_not)
            else:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked)
                if port.note:
                    item.setText(1, f"{item.text(1)}  ({port.note})")
            self.ports.addTopLevelItem(item)
        self.ports.resizeColumnToContents(0)
        self.ports.blockSignals(False)
        self.results.clear()
        self._show(result.found)
        for note in result.notes:
            self.log.appendPlainText(note)
        self._update_buttons()

    def ticked(self) -> list[Port]:
        return [self.ports.topLevelItem(i).data(0, PORT_ROLE)
                for i in range(self.ports.topLevelItemCount())
                if self.ports.topLevelItem(i).checkState(0) == Qt.CheckState.Checked]

    def ask(self) -> None:
        ports = self.ticked()
        if not ports or self._thread is not None:
            return
        self._thread = _AskThread(self.registry, ports, self.ask_idn.isChecked())
        self._thread.logged.connect(self.log.appendPlainText)
        self._thread.done.connect(self._asked)
        self._update_buttons()
        self._thread.start()

    def _asked(self, results) -> None:
        self._thread.wait()
        self._thread = None
        for port, found in results:
            if isinstance(found, str):
                item = QTreeWidgetItem([port.name, f"Could not ask: {found}"])
                self.results.addTopLevelItem(item)
            elif not found:
                self.results.addTopLevelItem(QTreeWidgetItem([port.name, "Nothing recognized"]))
            else:
                self._show(found)
        self._update_buttons()

    def _show(self, found: list[Found]) -> None:
        for entry in found:
            details = entry.how + (f": {entry.reply}" if entry.reply else "")
            item = QTreeWidgetItem([entry.where, details])
            item.setToolTip(1, f"resource = {entry.resource}")
            self.results.addTopLevelItem(item)
            for match in entry.matches:
                child = QTreeWidgetItem([match.name, match.reason])
                child.setData(0, MATCH_ROLE, (match.device_id, entry.resource))
                child.setToolTip(0, f"{match.device_id} on {entry.resource}")
                item.addChild(child)
            if not entry.matches:
                item.addChild(QTreeWidgetItem(["No plugin recognizes it",
                                               f"resource = {entry.resource}"]))
            item.setExpanded(True)
        self.results.resizeColumnToContents(0)

    # --- choosing ---------------------------------------------------------------------
    def chosen(self) -> tuple[str, str] | None:
        """The device type and resource of the selected match."""
        items = self.results.selectedItems()
        return items[0].data(0, MATCH_ROLE) if items else None

    def _add(self) -> None:
        if self.chosen() is None:
            QMessageBox.information(self, "Find devices",
                                    "Select a device type under something found.")
            return
        self.accept()

    def _update_buttons(self, *_) -> None:
        busy = self._thread is not None
        self.ask_button.setEnabled(not busy and bool(self.ticked()))
        self.look_button.setEnabled(not busy)
        self.add_button.setEnabled(not busy and self.chosen() is not None)
        self.ask_button.setText("Asking…" if busy else "Ask ticked ports")

    def reject(self) -> None:
        if self._thread is not None:
            return  # a port is being asked; it ends within seconds
        super().reject()

    def accept(self) -> None:
        if self._thread is None:
            super().accept()


class FindConnectionDialog(QDialog):
    """Look for one device type and let the user pick which of the ones found to use.

    USB devices are recognized at once, without sending anything. Ports are only
    asked when the user presses Search; they start ticked, since only this device
    type's own queries go out, and what goes out is listed above them.
    """

    def __init__(self, registry: PluginRegistry, spec: DeviceSpec,
                 resources: dict[str, str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Find {spec.name}")
        self.resize(720, 560)
        self.registry, self.spec = registry, spec
        self.resources = resources or {}
        self._thread: _AskThread | None = None

        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.intro.setTextFormat(Qt.TextFormat.PlainText)
        self.ports = QTreeWidget()
        self.ports.setHeaderLabels(["Port", "Description"])
        self.ports.setRootIsDecorated(False)
        self.ports.itemChanged.connect(self._update_buttons)
        self.search_button = QPushButton("Search ticked ports")
        self.search_button.clicked.connect(self.search)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Found", "Connection", "Reply"])
        self.results.setRootIsDecorated(False)
        self.results.itemSelectionChanged.connect(self._update_buttons)
        self.results.itemDoubleClicked.connect(lambda *_: self.accept())
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("What was sent, line by line")

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.use_button = self.buttons.addButton("Use this one",
                                                 QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        self.port_box = QWidget()
        port_layout = QVBoxLayout(self.port_box)
        port_layout.setContentsMargins(0, 0, 0, 0)
        port_layout.addWidget(self.ports, 1)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.search_button)
        port_layout.addLayout(row)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.port_box)
        splitter.addWidget(self.results)
        splitter.addWidget(self.log)
        splitter.setSizes([160, 160, 100])
        layout = QVBoxLayout(self)
        layout.addWidget(self.intro)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.buttons)
        self._look()

    def _look(self) -> None:
        result = look(self.registry, self.resources)
        for note in result.notes:
            self.log.appendPlainText(note)
        for entry in result.found:
            self._add_result(entry)
        self.ports.blockSignals(True)
        for port in result.ports:
            # ports this type could be on; ones that may not be asked are shown greyed
            wanted = needs_port(self.spec, port) or (
                port.why_not and needs_port(self.spec, Port(port.kind, port.name)))
            if not wanted:
                continue
            item = QTreeWidgetItem([port.name, port.description
                                    + (f"  [{port.usb_id}]" if port.usb_id else "")])
            item.setData(0, PORT_ROLE, port)
            if port.why_not:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setText(1, f"{item.text(1)}  (not asked: {port.why_not})")
            else:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked)
                if port.note:
                    item.setText(1, f"{item.text(1)}  ({port.note})")
            self.ports.addTopLevelItem(item)
        self.ports.resizeColumnToContents(0)
        self.ports.blockSignals(False)
        asks_ports = self.ports.topLevelItemCount() > 0
        self.port_box.setVisible(asks_ports)
        usb = list(self.spec.discovery.usb) if self.spec.discovery else []
        lines = []
        if usb:
            lines.append(f"Recognized by its USB id ({', '.join(usb)}) without sending "
                         "anything.")
        if asks_ports:
            lines.append("Searching asks the ticked ports. Untick any port with something "
                         "on it that moves, heats or must not be disturbed. Each port gets:")
            lines += [f"  \N{BULLET} {line}" for line in what_is_sent(self.spec)]
        self.intro.setText("\n".join(lines))
        self._update_buttons()

    def ticked(self) -> list[Port]:
        return [self.ports.topLevelItem(i).data(0, PORT_ROLE)
                for i in range(self.ports.topLevelItemCount())
                if self.ports.topLevelItem(i).checkState(0) == Qt.CheckState.Checked]

    def search(self) -> None:
        ports = self.ticked()
        if not ports or self._thread is not None:
            return
        self._thread = _AskThread(self.registry, ports, True, only=self.spec.id)
        self._thread.logged.connect(self.log.appendPlainText)
        self._thread.done.connect(self._searched)
        self._update_buttons()
        self._thread.start()

    def _searched(self, results) -> None:
        self._thread.wait()
        self._thread = None
        for _port, found in results:
            for entry in found if isinstance(found, list) else ():
                self._add_result(entry)
        self._update_buttons()

    def _add_result(self, entry: Found) -> None:
        if not any(m.device_id == self.spec.id for m in entry.matches):
            return
        if entry.resource in self.found():
            return  # searched twice
        item = QTreeWidgetItem([entry.where, entry.resource, entry.reply])
        item.setData(0, MATCH_ROLE, entry.resource)
        self.results.addTopLevelItem(item)
        for column in range(2):
            self.results.resizeColumnToContents(column)
        if self.results.topLevelItemCount() == 1:
            self.results.setCurrentItem(item)

    def found(self) -> list[str]:
        """The resources found so far."""
        return [self.results.topLevelItem(i).data(0, MATCH_ROLE)
                for i in range(self.results.topLevelItemCount())]

    def chosen(self) -> str | None:
        items = self.results.selectedItems()
        return items[0].data(0, MATCH_ROLE) if items else None

    def _update_buttons(self, *_) -> None:
        busy = self._thread is not None
        self.search_button.setEnabled(not busy and bool(self.ticked()))
        self.search_button.setText("Searching…" if busy else "Search ticked ports")
        self.use_button.setEnabled(not busy and self.chosen() is not None)
        count = self.results.topLevelItemCount()
        if busy:
            self.status.setText("Searching…")
        elif count > 1:
            self.status.setText(f"Found {count}. Pick the one to use.")
        elif count == 1:
            self.status.setText("Found one.")
        elif self.ports.topLevelItemCount():
            self.status.setText("Nothing found yet.")
        else:
            self.status.setText("Not connected, or not recognized.")

    def reject(self) -> None:
        if self._thread is None:
            super().reject()

    def accept(self) -> None:
        if self._thread is None and self.chosen() is not None:
            super().accept()
