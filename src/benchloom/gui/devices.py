# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Device list for the open project, and the dialog to add or edit a device."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..engine import open_device
from ..plugin import (
    DEVICE_KINDS, SOURCES, CommandInfo, DeviceSpec, PluginError, PluginRegistry, SettingSpec,
    kind_label,
)
from ..project import DeviceConfig, Project, device_name_problem
from ..transports import USBGPIB_IDS, TransportError, find_usbtmc, parse_resource
from .plugins import _Field, _TextField, setting_text

BAUD_RATES = ["9600", "19200", "38400", "57600", "115200"]

# (key, label, transport scheme)
CONNECTIONS = [
    ("sim", "Simulated (no hardware)", "sim"),
    ("serial", "Serial port / USB serial", "serial"),
    ("tcp", "Network (TCP)", "tcp"),
    ("usbtmc", "USB instrument or UsbGpib adapter (USBTMC)", "usbtmc"),
    ("prologix", "GPIB via Prologix / AR488 adapter", "prologix"),
    ("usb", "USB (the device's own protocol)", "usb"),
    ("visa", "VISA resource (vendor GPIB, USB-TMC, LAN)", "visa"),
    ("custom", "Custom resource string", None),
]


def serial_ports() -> list[tuple[str, str]]:
    from serial.tools import list_ports

    return [(p.device, p.description or "") for p in sorted(list_ports.comports())]


def signature(info: CommandInfo) -> str:
    return f"{info.name}({', '.join(p.name for p in info.params)})"


def labelled_signature(info: CommandInfo) -> str:
    """set_voltage(volts [V]) -> number [V]: for the editor's autocomplete list."""
    params = ", ".join(f"{p.name} [{p.unit}]" if p.unit else p.name for p in info.params)
    text = f"{info.name}({params})"
    if info.returns not in ("nil", "any"):
        text += f"  → {info.returns}" + (f" [{info.unit}]" if info.unit else "")
    return text


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


class _UsbTmcBox(QWidget):
    """USBTMC devices found on USB, editable, with a refresh button."""

    DEFAULT = f"{USBGPIB_IDS[0]:04X}:{USBGPIB_IDS[1]:04X}"

    def __init__(self):
        super().__init__()
        self.combo = QComboBox(editable=True)
        self.combo.setToolTip("USB vendor:product ID, optionally /serial number. "
                              f"{self.DEFAULT} is the xyphro UsbGpib adapter.")
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo, 1)
        layout.addWidget(refresh)
        self.refresh()

    def refresh(self) -> None:
        current = self.combo.currentText() or self.DEFAULT
        self.combo.clear()
        for vid, pid, serial in find_usbtmc():
            target = f"{vid:04X}:{pid:04X}" + (f"/{serial}" if serial else "")
            self.combo.addItem(target)
        self.combo.setEditText(current)

    def target(self) -> str:
        return self.combo.currentText().strip() or self.DEFAULT


ID_ROLE = Qt.ItemDataRole.UserRole  # on a device row of the type picker: its device type
LABEL_ROLE = Qt.ItemDataRole.UserRole + 1  # on a manufacturer row: its name


def _search_text(spec: DeviceSpec) -> str:
    return " ".join((spec.name, spec.id, spec.maker, spec.description, kind_label(spec.kind),
                     spec.kind, spec.plugin.name, spec.plugin.description)).casefold()


class DeviceTypePicker(QWidget):
    """The plugins' device types to pick one from: a search box, a filter by kind and
    a tree of "Official" and "Third party" plugins, grouped by manufacturer."""

    changed = Signal()
    SECTIONS = (("official", "Official"), ("third_party", "Third party"))

    def __init__(self, registry: PluginRegistry, parent=None):
        super().__init__(parent)
        specs = sorted(registry.devices.values(),
                       key=lambda s: (s.maker.casefold(), s.name.casefold()))
        self._current: str | None = None
        self._kinds: dict[str, str] = {}  # device type -> its kind

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, id, manufacturer, description…")
        self.search.setClearButtonEnabled(True)
        self.kind_box = QComboBox()
        self.kind_box.addItem("All kinds", "")
        kinds = {spec.kind for spec in specs}
        # the known kinds in their usual order, then the plugins' own, "other" last
        ordered = [k for k in DEVICE_KINDS if k in kinds and k != "other"]
        ordered += sorted(kinds - set(DEVICE_KINDS), key=lambda k: kind_label(k).casefold())
        ordered += ["other"] if "other" in kinds else []
        for kind in ordered:
            self.kind_box.addItem(kind_label(kind), kind)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Device type", "Kind"])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setMinimumHeight(200)
        self.empty_label = QLabel("No device type matches.")
        self.empty_label.setEnabled(False)
        self.empty_label.hide()

        self.sections: dict[str, QTreeWidgetItem] = {}
        self.items: dict[str, QTreeWidgetItem] = {}  # device type -> its row
        self._search_texts: dict[str, str] = {}
        for key, label in self.SECTIONS:
            section = QTreeWidgetItem([label])
            section.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = section.font(0)
            font.setBold(True)
            section.setFont(0, font)
            self.sections[key] = section
            self.tree.addTopLevelItem(section)
            groups: dict[str, QTreeWidgetItem] = {}
            for spec in specs:
                if ("official" if spec.plugin.official else "third_party") != key:
                    continue
                group = groups.get(spec.maker.casefold())
                if group is None:
                    group = QTreeWidgetItem([spec.maker])
                    group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    group.setData(0, LABEL_ROLE, spec.maker)
                    groups[spec.maker.casefold()] = group
                    section.addChild(group)
                item = QTreeWidgetItem([spec.name, kind_label(spec.kind)])
                item.setData(0, ID_ROLE, spec.id)
                tip = f"{spec.name} ({spec.id})"
                if spec.description:
                    tip += "\n\n" + spec.description
                item.setToolTip(0, tip)
                group.addChild(item)
                self.items[spec.id] = item
                self._kinds[spec.id] = spec.kind
                self._search_texts[spec.id] = _search_text(spec)
            section.setExpanded(True)

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.kind_box)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(filters)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.empty_label)

        self.search.textChanged.connect(self.apply_filter)
        self.kind_box.currentIndexChanged.connect(self.apply_filter)
        self.tree.currentItemChanged.connect(self._item_changed)
        self.apply_filter()

    def current_id(self) -> str | None:
        return self._current

    def set_current(self, device_id: str | None) -> None:
        item = self.items.get(device_id)
        if item is not None:
            self._reveal(item)
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)

    def set_kind(self, kind: str) -> None:
        self.kind_box.setCurrentIndex(max(self.kind_box.findData(kind), 0))

    def _reveal(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()

    def _item_changed(self, item: QTreeWidgetItem | None, _previous=None) -> None:
        device_id = item.data(0, ID_ROLE) if item is not None else None
        if device_id and device_id != self._current:
            self._current = device_id
            self.changed.emit()

    def matches(self, device_id: str) -> bool:
        kind = self.kind_box.currentData()
        if kind and self._kinds[device_id] != kind:
            return False
        text = self._search_texts[device_id]
        return all(word in text for word in self.search.text().casefold().split())

    def apply_filter(self) -> None:
        """Hide the device types that do not match, and the groups and sections left
        empty; while a search or a kind is set, open every group that matches."""
        filtering = bool(self.search.text().strip() or self.kind_box.currentData())
        any_shown = False
        for section in self.sections.values():
            section_count = 0
            for g in range(section.childCount()):
                group = section.child(g)
                count = 0
                for i in range(group.childCount()):
                    item = group.child(i)
                    shown = self.matches(item.data(0, ID_ROLE))
                    item.setHidden(not shown)
                    count += shown
                group.setHidden(count == 0)
                group.setText(0, f"{group.data(0, LABEL_ROLE)}  ({count})")
                if filtering:
                    group.setExpanded(count > 0)
                section_count += count
            section.setHidden(section_count == 0)
            if section_count and filtering:
                section.setExpanded(True)
            any_shown = any_shown or section_count > 0
        self.tree.setVisible(any_shown)
        self.empty_label.setVisible(not any_shown)
        if not filtering and self._current in self.items:
            self._reveal(self.items[self._current])
        # the type picked is always one in view: the first match, if the filter hid it
        shown = self.visible_ids()
        if shown and self._current not in shown:
            self.set_current(shown[0])

    def visible_ids(self) -> list[str]:
        """The device types shown, in tree order."""
        return [i for i, item in self.items.items() if not item.isHidden()]


class DeviceTypeInfo(QGroupBox):
    """Fields describing the device type picked, as the plugin manager shows them."""

    def __init__(self, parent=None):
        super().__init__("Device type", parent)
        self.name_field, self.id_field, self.kind_field = _Field(), _Field(), _Field()
        self.manufacturer_field, self.plugin_field = _Field(), _Field()
        self.connections_field, self.simulator_field = _Field(), _Field()
        self.resource_field = _Field()
        self.settings_field = _TextField(3)
        self.description_field = _TextField(6)
        form = self.form = QFormLayout(self)
        form.addRow("Name", self.name_field)
        form.addRow("Device type", self.id_field)
        form.addRow("Kind", self.kind_field)
        form.addRow("Manufacturer", self.manufacturer_field)
        form.addRow("Plugin", self.plugin_field)
        form.addRow("Connections", self.connections_field)
        form.addRow("Default address", self.resource_field)
        form.addRow("Simulator", self.simulator_field)
        form.addRow("Settings", self.settings_field)
        form.addRow("Description", self.description_field)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.show_device(None)

    def show_device(self, spec: DeviceSpec | None) -> None:
        if spec is None:
            for field in (self.name_field, self.id_field, self.kind_field,
                          self.manufacturer_field, self.plugin_field, self.connections_field,
                          self.resource_field, self.simulator_field):
                field.clear()
            self.settings_field.clear()
            self.description_field.setPlainText("Choose a device type on the left.")
            for row in range(self.form.rowCount()):
                self.form.setRowVisible(row, row == self.form.rowCount() - 1)
            return
        plugin = spec.plugin
        self.name_field.show_text(spec.name)
        self.id_field.show_text(spec.id)
        self.kind_field.show_text(kind_label(spec.kind))
        self.manufacturer_field.show_text(spec.maker)
        where = SOURCES.get(plugin.source, plugin.source)
        self.plugin_field.show_text(f"{plugin.name} {plugin.version}"
                                    + (f" ({where})" if where else ""))
        self.connections_field.show_text(", ".join(spec.transports) or "any")
        self.resource_field.show_text(spec.default_resource)
        self.simulator_field.show_text("Yes" if spec.simulator else "No")
        self.settings_field.setPlainText("\n".join(setting_text(s) for s in spec.settings))
        self.description_field.setPlainText(spec.description)
        for row in range(self.form.rowCount()):
            self.form.setRowVisible(row, True)
        for field in (self.resource_field, self.settings_field, self.description_field):
            text = field.text() if isinstance(field, QLineEdit) else field.toPlainText()
            self.form.setRowVisible(field, bool(text))


class DeviceDialog(QDialog):
    def __init__(self, registry: PluginRegistry, taken_names: set[str],
                 config: DeviceConfig | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit device" if config else "Add device")
        self.setMinimumWidth(560)
        self.resize(900, 680)
        self.registry = registry
        self.taken_names = taken_names
        self.settings = dict(config.settings) if config else {}

        self.type_picker = DeviceTypePicker(registry)
        self.type_info = DeviceTypeInfo()
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
        self.usbtmc_device = _UsbTmcBox()
        self.usb_id = QLineEdit()
        self.usb_id.setPlaceholderText("049F:505A")
        self.visa_resource = QLineEdit()
        self.visa_resource.setPlaceholderText("GPIB0::5::INSTR")
        self.custom_resource = QLineEdit()
        self.custom_resource.setPlaceholderText("serial://COM3?baud=9600&term=\\r\\n")
        self._add_page("sim", [(None, QLabel("Uses the plugin's built-in simulator."))])
        self._add_page("serial", [("Port", self.serial_port), ("Baud rate", self.serial_baud)])
        self._add_page("tcp", [("Host", self.tcp_host), ("Port", self.tcp_port)])
        self._add_page("usbtmc", [("Device", self.usbtmc_device), (None, QLabel(
            "Needs the WinUSB driver on Windows (install it once with Zadig), "
            "or use VISA instead."))])
        self._add_page("usb", [("USB ID", self.usb_id), (None, QLabel(
            "Needs the WinUSB driver on Windows (install it once with Zadig)."))])
        self._add_page("prologix", [("Adapter port", self.gpib_port),
                                    ("GPIB address", self.gpib_address)])
        self._add_page("visa", [("Resource", self.visa_resource)])
        self._add_page("custom", [("Resource", self.custom_resource)])

        # the settings the device type declares in its plugin.toml
        self.settings_box = QGroupBox("Device settings")
        self.settings_form = QFormLayout(self.settings_box)
        self._setting_fields: dict[str, tuple[SettingSpec, QWidget]] = {}

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
        form.addRow("Name", self.name_edit)
        form.addRow("Connection", self.connection_box)
        # the device types on the left, the one picked described on the right
        info_column = QWidget()
        info_layout = QVBoxLayout(info_column)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.addWidget(self.type_info)
        info_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(info_column)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.type_picker)
        self.splitter.addWidget(scroll)
        self.splitter.setSizes([440, 440])

        layout = QVBoxLayout(self)
        layout.addWidget(self.splitter, 1)
        layout.addLayout(form)
        layout.addWidget(self.pages)
        layout.addWidget(self.settings_box)
        layout.addLayout(test_row)
        layout.addWidget(buttons)

        self.connection_box.currentIndexChanged.connect(self._show_page)
        if config:
            self.type_picker.set_current(config.device)
            self.name_edit.setText(config.name)
        else:
            self.type_picker.set_current(self._first_type())
        self.type_picker.changed.connect(self._type_changed)
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

    def _first_type(self) -> str | None:
        """The demo supply if there is one (it needs no hardware), else the first type."""
        if "demo.psu" in self.type_picker.items:
            return "demo.psu"
        return next(iter(self.type_picker.items), None)

    def device_type(self) -> str | None:
        return self.type_picker.current_id()

    def set_device_type(self, device_id: str) -> None:
        self.type_picker.set_current(device_id)

    def _type_changed(self) -> None:
        """Offer only the connections the chosen device type supports."""
        spec = self.registry.devices.get(self.device_type())
        self.type_info.show_device(spec)
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
        if spec and spec.default_resource.startswith("usb://") and not self.usb_id.text():
            self.usb_id.setText(spec.default_resource[len("usb://"):])
        if spec and spec.default_resource.startswith("serial://"):
            # a device with a fixed baud rate; editing loads the saved resource after this
            try:
                baud = parse_resource(spec.default_resource)[2].get("baud")
            except TransportError:
                baud = None
            if baud:
                self.serial_baud.setEditText(str(baud))
        self._show_page()
        self._build_settings(spec)
        # follow the device type with the suggested name until the user types their own
        if spec and self.name_edit.text() in ("", getattr(self, "_suggested", "")):
            self._suggested = self._free_name(spec.default_name or spec.id.rsplit(".", 1)[-1])
            self.name_edit.setText(self._suggested)

    def _build_settings(self, spec) -> None:
        while self.settings_form.rowCount():
            self.settings_form.removeRow(0)
        self._setting_fields.clear()
        for setting in spec.settings if spec else ():
            value = self.settings.get(setting.name, setting.default)
            if setting.type == "bool":
                field = QCheckBox()
                field.setChecked(bool(value))
            elif setting.type in ("int", "float"):
                field = QSpinBox() if setting.type == "int" else QDoubleSpinBox(decimals=3)
                field.setRange(setting.min if setting.min is not None else -1e9,
                               setting.max if setting.max is not None else 1e9)
                field.setValue(value if value is not None else 0)
            else:
                field = QLineEdit("" if value is None else str(value))
            field.setToolTip(setting.help)
            label = QLabel(setting.label)
            label.setToolTip(setting.help)
            self.settings_form.addRow(label, field)
            self._setting_fields[setting.name] = (setting, field)
        self.settings_box.setVisible(bool(self._setting_fields))

    def _settings(self) -> dict:
        """The device entry's settings: declared ones from the fields, others kept."""
        settings = dict(self.settings)
        for name, (setting, field) in self._setting_fields.items():
            if isinstance(field, QCheckBox):
                value = field.isChecked()
            elif isinstance(field, (QSpinBox, QDoubleSpinBox)):
                value = field.value()
            else:
                value = field.text().strip()
            if value == setting.default or (value == "" and setting.default is None):
                settings.pop(name, None)  # defaults stay out of the project file
            else:
                settings[name] = value
        return settings

    def _free_name(self, base: str) -> str:
        base = re.sub(r"\W", "_", base)
        if device_name_problem(base):
            base = "dev_" + base
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
        if scheme in ("usbtmc", "usbgpib") and not options and self._select("usbtmc"):
            if scheme == "usbgpib":
                target = _UsbTmcBox.DEFAULT + (f"/{target}" if target else "")
            self.usbtmc_device.combo.setEditText(target)
            return
        if scheme == "usb" and not options and self._select("usb"):
            self.usb_id.setText(target)
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
        if kind == "usbtmc":
            return f"usbtmc://{self.usbtmc_device.target()}"
        if kind == "usb":
            return f"usb://{self.usb_id.text().strip() or self.usb_id.placeholderText()}"
        if kind == "visa":
            return f"visa://{self.visa_resource.text().strip()}"
        return self.custom_resource.text().strip()

    def config(self) -> DeviceConfig:
        return DeviceConfig(self.name_edit.text().strip(), self.device_type(),
                            self.resource(), self._settings())

    def _test(self) -> None:
        """Open the device once and, where it has one, read its identification."""
        self.test_result.setText("Connecting…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            device = open_device(self.registry, self.config())
            try:
                reply = device.identify()
            finally:
                # not device.close(): a connection test must not switch outputs off
                device.transport.close()
            self.test_result.setText(f"Connected. {reply}".strip())
        except Exception as exc:
            self.test_result.setText(f"Failed: {exc}")
        finally:
            QApplication.restoreOverrideCursor()

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        problem = device_name_problem(name)
        if problem:
            QMessageBox.warning(self, "Name", f"The name '{name}' {problem}.")
            return
        if name in self.taken_names:
            QMessageBox.warning(self, "Name", f"There is already a device named '{name}'.")
            return
        if self.device_type() is None:
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
        self.commands: dict[str, list[CommandInfo]] = {}  # device name -> its commands

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
        self.commands = {}
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
                    infos = list(spec.load_driver().commands().values())
                    self.commands[config.name] = infos
                    for info in infos:
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
        accepted = dialog.exec()
        config = dialog.config() if accepted else None
        dialog.deleteLater()  # a child of this panel otherwise, one more per use
        if config is not None:
            self.project.devices[config.name] = config
            self._commit()

    def edit_device(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        others = set(self.project.devices) - {name}
        dialog = DeviceDialog(self.registry, others, self.project.devices[name], parent=self)
        accepted = dialog.exec()
        config = dialog.config() if accepted else None
        dialog.deleteLater()
        if config is not None:
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
