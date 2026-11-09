"""Device list for the open project, and the dialog to add or edit a device."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QStackedWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..engine import open_device
from ..plugin import CommandInfo, PluginError, PluginRegistry
from ..project import DeviceConfig, Project
from ..transports import TransportError, parse_resource

BAUD_RATES = ["9600", "19200", "38400", "57600", "115200"]

# (key, label, transport scheme)
CONNECTIONS = [
    ("sim", "Simulated (no hardware)", "sim"),
    ("serial", "Serial port / USB serial", "serial"),
    ("tcp", "Network (TCP)", "tcp"),
    ("prologix", "GPIB via Prologix / AR488 adapter", "prologix"),
    ("visa", "VISA resource (vendor GPIB, USB-TMC, LAN)", "visa"),
    ("custom", "Custom resource string", None),
]


def serial_ports() -> list[tuple[str, str]]:
    from serial.tools import list_ports

    return [(p.device, p.description or "") for p in sorted(list_ports.comports())]


def signature(info: CommandInfo) -> str:
    return f"{info.name}({', '.join(p.name for p in info.params)})"


def describe(info: CommandInfo) -> str:
    lines = [info.doc] if info.doc else []
    for p in info.params:
        text = f"{p.name}: {p.type}"
        if p.unit:
            text += f" [{p.unit}]"
        if p.min is not None or p.max is not None:
            low = "" if p.min is None else f"{p.min:g}"
            high = "" if p.max is None else f"{p.max:g}"
            text += f", {low} to {high}"
        lines.append(text)
    if info.returns not in ("nil", "any"):
        lines.append(f"returns {info.returns}" + (f" [{info.unit}]" if info.unit else ""))
    return "\n".join(lines)


class _PortBox(QWidget):
    """Editable list of serial ports with a refresh button."""

    def __init__(self):
        super().__init__()
        self.combo = QComboBox(editable=True)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo, 1)
        layout.addWidget(refresh)
        self.refresh()

    def refresh(self) -> None:
        current = self.combo.currentText()
        self.combo.clear()
        for port, description in serial_ports():
            self.combo.addItem(f"{port}  ({description})" if description else port, port)
        if current:
            self.set_port(current)

    def port(self) -> str:
        text = self.combo.currentText().strip()
        index = self.combo.findText(text)
        return self.combo.itemData(index) if index >= 0 else text

    def set_port(self, port: str) -> None:
        index = self.combo.findData(port)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        else:
            self.combo.setEditText(port)


class DeviceDialog(QDialog):
    def __init__(self, registry: PluginRegistry, taken_names: set[str],
                 config: DeviceConfig | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit device" if config else "Add device")
        self.setMinimumWidth(460)
        self.registry = registry
        self.taken_names = taken_names
        self.settings = dict(config.settings) if config else {}

        self.type_box = QComboBox()
        for spec in sorted(registry.devices.values(), key=lambda s: s.name.lower()):
            self.type_box.addItem(f"{spec.name}  ({spec.id})", spec.id)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("name used in the test, e.g. psu")
        self.connection_box = QComboBox()
        self.pages = QStackedWidget()
        self._page_index: dict[str, int] = {}

        # one page of fields per connection kind
        self.serial_port = _PortBox()
        self.serial_baud = QComboBox(editable=True)
        self.serial_baud.addItems(BAUD_RATES)
        self.tcp_host = QLineEdit()
        self.tcp_host.setPlaceholderText("192.168.1.20")
        self.tcp_port = QSpinBox(minimum=1, maximum=65535, value=5025)
        self.gpib_port = _PortBox()
        self.gpib_address = QSpinBox(minimum=0, maximum=30, value=5)
        self.visa_resource = QLineEdit()
        self.visa_resource.setPlaceholderText("GPIB0::5::INSTR")
        self.custom_resource = QLineEdit()
        self.custom_resource.setPlaceholderText("serial://COM3?baud=9600&term=\\r\\n")
        self._add_page("sim", [(None, QLabel("Uses the plugin's built-in simulator."))])
        self._add_page("serial", [("Port", self.serial_port), ("Baud rate", self.serial_baud)])
        self._add_page("tcp", [("Host", self.tcp_host), ("Port", self.tcp_port)])
        self._add_page("prologix", [("Adapter port", self.gpib_port),
                                    ("GPIB address", self.gpib_address)])
        self._add_page("visa", [("Resource", self.visa_resource)])
        self._add_page("custom", [("Resource", self.custom_resource)])

        self.test_button = QPushButton("Test connection")
        self.test_button.clicked.connect(self._test)
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Device type", self.type_box)
        form.addRow("Name", self.name_edit)
        form.addRow("Connection", self.connection_box)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.pages)
        layout.addLayout(test_row)
        layout.addWidget(buttons)

        self.connection_box.currentIndexChanged.connect(self._show_page)
        self.type_box.currentIndexChanged.connect(self._type_changed)
        if config:
            index = self.type_box.findData(config.device)
            if index >= 0:
                self.type_box.setCurrentIndex(index)
            self.name_edit.setText(config.name)
        self._type_changed()
        if config:
            self._load_resource(config.resource)

    def _add_page(self, key: str, rows) -> None:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        for label, widget in rows:
            form.addRow(label, widget) if label else form.addRow(widget)
        self._page_index[key] = self.pages.addWidget(page)

    def _type_changed(self) -> None:
        """Offer only the connections the chosen device type supports."""
        spec = self.registry.devices.get(self.type_box.currentData())
        allowed = set(spec.transports) if spec and spec.transports else None
        previous = self.connection_box.currentData()
        self.connection_box.blockSignals(True)
        self.connection_box.clear()
        for key, label, scheme in CONNECTIONS:
            if scheme is None or allowed is None or scheme in allowed:
                if key == "sim" and not (spec and spec.simulator):
                    continue
                self.connection_box.addItem(label, key)
        index = self.connection_box.findData(previous)
        self.connection_box.setCurrentIndex(max(index, 0))
        self.connection_box.blockSignals(False)
        self._show_page()
        if spec and not self.name_edit.text():
            self.name_edit.setText(self._free_name(spec.id.rsplit(".", 1)[-1]))

    def _free_name(self, base: str) -> str:
        base = re.sub(r"\W", "_", base)
        name, n = base, 2
        while name in self.taken_names:
            name, n = f"{base}{n}", n + 1
        return name

    def _show_page(self) -> None:
        self.pages.setCurrentIndex(self._page_index.get(self.connection_box.currentData(), 0))
        self.test_result.clear()

    def _select(self, key: str) -> bool:
        index = self.connection_box.findData(key)
        if index >= 0:
            self.connection_box.setCurrentIndex(index)
        return index >= 0

    def _load_resource(self, resource: str) -> None:
        try:
            scheme, target, options = parse_resource(resource)
        except TransportError:
            scheme, target, options = "", "", {}
        if scheme == "sim" and not options and self._select("sim"):
            return
        if scheme == "serial" and set(options) <= {"baud"} and self._select("serial"):
            self.serial_port.set_port(target)
            self.serial_baud.setEditText(options.get("baud", "9600"))
            return
        if scheme == "tcp" and not options and ":" in target and self._select("tcp"):
            host, _, port = target.rpartition(":")
            if port.isdigit():
                self.tcp_host.setText(host)
                self.tcp_port.setValue(int(port))
                return
        if scheme == "prologix" and not options and "/" in target and self._select("prologix"):
            port, _, address = target.rpartition("/")
            if address.isdigit():
                self.gpib_port.set_port(port)
                self.gpib_address.setValue(int(address))
                return
        if scheme == "visa" and not options and self._select("visa"):
            self.visa_resource.setText(target)
            return
        self._select("custom")
        self.custom_resource.setText(resource)

    def resource(self) -> str:
        kind = self.connection_box.currentData()
        if kind == "sim":
            return "sim://"
        if kind == "serial":
            baud = self.serial_baud.currentText().strip() or "9600"
            return f"serial://{self.serial_port.port()}?baud={baud}"
        if kind == "tcp":
            return f"tcp://{self.tcp_host.text().strip()}:{self.tcp_port.value()}"
        if kind == "prologix":
            return f"prologix://{self.gpib_port.port()}/{self.gpib_address.value()}"
        if kind == "visa":
            return f"visa://{self.visa_resource.text().strip()}"
        return self.custom_resource.text().strip()

    def config(self) -> DeviceConfig:
        return DeviceConfig(self.name_edit.text().strip(), self.type_box.currentData(),
                            self.resource(), self.settings)

    def _test(self) -> None:
        """Open the device once and, where it has one, read its identification."""
        self.test_result.setText("Connecting…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            device = open_device(self.registry, self.config())
            try:
                reply = device.call("idn") if "idn" in type(device).commands() else ""
            finally:
                device.close()
                device.transport.close()
            self.test_result.setText(f"Connected. {reply}".strip())
        except Exception as exc:
            self.test_result.setText(f"Failed: {exc}")
        finally:
            QApplication.restoreOverrideCursor()

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            QMessageBox.warning(self, "Name", "The name must start with a letter and contain "
                                "only letters, digits and underscores.")
            return
        if name in self.taken_names:
            QMessageBox.warning(self, "Name", f"There is already a device named '{name}'.")
            return
        if self.type_box.currentData() is None:
            QMessageBox.warning(self, "Device type", "Choose a device type.")
            return
        super().accept()


class DevicesPanel(QWidget):
    """Tree of the project's devices and their commands."""

    command_activated = Signal(str, str, str)  # device name, command name, Lua snippet
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Project | None = None
        self.registry: PluginRegistry | None = None

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.add_button = QPushButton("Add…")
        self.edit_button = QPushButton("Edit…")
        self.remove_button = QPushButton("Remove")
        self.add_button.clicked.connect(self.add_device)
        self.edit_button.clicked.connect(self.edit_device)
        self.remove_button.clicked.connect(self.remove_device)
        buttons = QHBoxLayout()
        for button in (self.add_button, self.edit_button, self.remove_button):
            buttons.addWidget(button)
        hint = QLabel("Double-click a command to add it to the test.")
        hint.setWordWrap(True)
        hint.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.tree, 1)
        layout.addWidget(hint)
        layout.addLayout(buttons)
        self.set_project(None)

    def set_project(self, project: Project | None) -> None:
        self.project = project
        self.registry = PluginRegistry.default(project.path) if project else None
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        self.add_button.setEnabled(self.project is not None)
        if self.project:
            for config in self.project.devices.values():
                item = QTreeWidgetItem([f"{config.name}  ·  {config.resource}"])
                item.setData(0, Qt.ItemDataRole.UserRole, config.name)
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                try:
                    spec = self.registry.device(config.device)
                    item.setToolTip(0, f"{spec.name} ({spec.id})")
                    for info in spec.load_driver().commands().values():
                        child = QTreeWidgetItem([signature(info)])
                        child.setToolTip(0, describe(info))
                        child.setData(0, Qt.ItemDataRole.UserRole + 1,
                                      (config.name, info.name,
                                       f"{config.name}:{signature(info)}"))
                        item.addChild(child)
                except PluginError as exc:
                    item.setForeground(0, QBrush(QColor("#d9453c")))
                    item.setToolTip(0, str(exc))
                self.tree.addTopLevelItem(item)
                item.setExpanded(True)
        self._update_buttons()

    def _selected_name(self) -> str | None:
        items = self.tree.selectedItems()
        if not items:
            return None
        item = items[0].parent() or items[0]
        return item.data(0, Qt.ItemDataRole.UserRole)

    def _update_buttons(self) -> None:
        selected = self._selected_name() is not None
        self.edit_button.setEnabled(selected)
        self.remove_button.setEnabled(selected)

    def _double_clicked(self, item: QTreeWidgetItem) -> None:
        command = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if command:
            self.command_activated.emit(*command)
        else:
            self.edit_device()

    def _commit(self) -> None:
        self.project.save()
        self.refresh()
        self.changed.emit()

    def add_device(self) -> None:
        dialog = DeviceDialog(self.registry, set(self.project.devices), parent=self)
        if dialog.exec():
            config = dialog.config()
            self.project.devices[config.name] = config
            self._commit()

    def edit_device(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        others = set(self.project.devices) - {name}
        dialog = DeviceDialog(self.registry, others, self.project.devices[name], parent=self)
        if dialog.exec():
            config = dialog.config()
            # keep the device's position in the list even if it was renamed
            self.project.devices = {
                (config.name if key == name else key): (config if key == name else value)
                for key, value in self.project.devices.items()
            }
            self._commit()

    def remove_device(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        answer = QMessageBox.question(self, "Remove device",
                                      f"Remove '{name}' from this project?")
        if answer == QMessageBox.StandardButton.Yes:
            del self.project.devices[name]
            self._commit()
