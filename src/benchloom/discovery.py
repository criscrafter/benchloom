# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Find connected instruments ("Find devices").

Two steps, so that nothing is sent anywhere without the user choosing it:

- :func:`look` sends nothing and opens no port. It lists the serial ports and USBTMC
  devices, and recognizes USB devices by their ids alone (the Hantek scope).
- :func:`ask` talks to one port the user picked, in this order, and stops at the
  first step that recognizes something:

  1. listen, sending nothing, for devices that send by themselves (FS9721 meters);
  2. ``++ver``, to find a Prologix or AR488 GPIB adapter. On an adapter nothing
     else is sent to the port itself; an AR488 then finds the instruments on the
     bus by handshake alone (``++fndl``) and each one is first only listened to
     (old HP instruments send readings unasked), then asked ``*IDN?``. One that
     does not answer gets a device clear, which ends the error state an unknown
     command leaves in the older HP counters. The adapter's settings are put back;
  3. the queries plugins declare for serial devices: text lines (the Feeltech
     ``UMO``) or fixed binary frames (a Modbus read of the Kunkin KP184).

What a device type is recognized by comes from its plugin's ``[device.discovery]``
table (see :class:`benchloom.plugin.DiscoverySpec`). Networks are never scanned.
Every line sent is reported through the ``log`` callback.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable

from .plugin import DeviceSpec, PluginRegistry
from .transports import USBGPIB_IDS, TransportError, parse_resource, prologix_escape

ADAPTER_BAUD = 115200
LISTEN_SECONDS = 1.5  # an FS9721 meter sends twice a second
REPLY_SECONDS = 1.5
FRAME_SECONDS = 0.5  # a binary reply has no line end, so read this long
FNDL_SECONDS = 15.0  # an AR488 tries all 30 addresses
Log = Callable[[str], None]


@dataclass
class Match:
    device_id: str
    name: str
    reason: str  # why it matches, for people


@dataclass
class Found:
    """Something recognized, or at least something that answered."""

    resource: str  # to put in the device entry, e.g. "prologix://COM17/1"
    where: str  # for people, e.g. "COM17, GPIB address 1"
    how: str  # e.g. "sends readings unasked", "*IDN?", "USB id"
    reply: str = ""
    matches: list[Match] = field(default_factory=list)


@dataclass
class Port:
    """A port :func:`ask` could talk to."""

    kind: str  # "serial" or "usbtmc"
    name: str  # COM17, /dev/ttyUSB0, or 03EB:2065/<serial>
    description: str = ""
    usb_id: str = ""  # "1B4F:9206" for USB serial ports
    bluetooth: bool = False
    in_project: str = ""  # the device of the open project that uses it
    # GPIB address -> device of the open project, when the port is a GPIB adapter the
    # project uses: more instruments can share the bus, so the port is still asked,
    # but these addresses are left alone
    gpib_in_project: dict[int, str] = field(default_factory=dict)

    @property
    def note(self) -> str:
        """For people: what asking leaves out on this port."""
        if not self.gpib_in_project or self.why_not:
            return ""
        used = ", ".join(f"{a} ('{n}')" for a, n in sorted(self.gpib_in_project.items()))
        return f"GPIB adapter of this project; address {used} not asked"

    @property
    def why_not(self) -> str:
        """Why it should not be asked, or "" when it may."""
        if self.bluetooth:
            return ("a Bluetooth port: opening it makes Windows try to reach the paired "
                    "device, which can take long")
        if self.in_project:
            return f"used by '{self.in_project}' in this project"
        return ""


@dataclass
class Look:
    ports: list[Port] = field(default_factory=list)
    found: list[Found] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- looking (nothing sent, nothing opened) -------------------------------------------
def _is_bluetooth(info) -> bool:
    text = f"{info.hwid} {info.description}".lower()
    return "bthenum" in text or "bluetooth" in text or info.device.startswith("/dev/rfcomm")


def project_ports(resources: dict[str, str]) -> tuple[dict[str, str],
                                                       dict[str, dict[int, str]]]:
    """What the project's devices use: serial ports and USB targets -> device name,
    and GPIB adapter ports -> {address: device name}."""
    used: dict[str, str] = {}
    gpib: dict[str, dict[int, str]] = {}
    for name, resource in resources.items():
        try:
            scheme, target, _ = parse_resource(resource)
        except TransportError:
            continue
        if scheme == "serial":
            used[target] = name
        elif scheme == "prologix":
            port, _, address = target.rpartition("/")
            if address.isdigit():
                gpib.setdefault(port, {})[int(address)] = name
            else:
                used[port] = name
        elif scheme in ("usbtmc", "usb"):
            used[target.upper()] = name
    return used, gpib


def look(registry: PluginRegistry, resources: dict[str, str] | None = None) -> Look:
    """What is connected, without sending anything. ``resources`` are the open
    project's devices (name -> resource); their ports are marked as in use."""
    result = Look()
    used, gpib = project_ports(resources or {})
    try:
        from serial.tools import list_ports

        for info in sorted(list_ports.comports(), key=lambda p: p.device):
            usb_id = f"{info.vid:04X}:{info.pid:04X}" if info.vid is not None else ""
            result.ports.append(Port("serial", info.device, info.description or "", usb_id,
                                     _is_bluetooth(info), used.get(info.device, ""),
                                     dict(gpib.get(info.device, {}))))
    except ImportError:
        result.notes.append("pyserial is missing, so serial ports are not listed")
    by_usb_id: dict[str, list[DeviceSpec]] = {}
    for spec in registry.devices.values():
        for usb_id in spec.discovery.usb if spec.discovery else ():
            by_usb_id.setdefault(usb_id, []).append(spec)
    try:
        import usb.core

        from .transports import _is_usbtmc, libusb_backend

        backend = libusb_backend()
        devices = list(usb.core.find(find_all=True, backend=backend)) if backend else []
        if backend is None:
            result.notes.append("libusb was not found, so USB devices are not listed")
    except ImportError:
        devices = []
        result.notes.append('USB support is not installed (pip install "benchloom[usb]"), '
                            "so USB devices are not listed")
    for device in devices:
        usb_id = f"{device.idVendor:04X}:{device.idProduct:04X}"
        if usb_id in by_usb_id:
            result.found.append(Found(
                f"usb://{usb_id}", f"USB {usb_id}", "USB id", matches=[
                    Match(s.id, s.name, f"its plugin names the USB id {usb_id}")
                    for s in by_usb_id[usb_id]]))
        if _is_usbtmc(device):
            try:
                serial = device.serial_number or ""
            except Exception:  # no WinUSB driver bound, or no permission (udev rules)
                serial = ""
            target = usb_id + (f"/{serial}" if serial else "")
            kind = "UsbGpib adapter" if (device.idVendor, device.idProduct) == USBGPIB_IDS \
                else "USBTMC instrument"
            result.ports.append(Port("usbtmc", target, kind, usb_id,
                                     in_project=used.get(target, used.get(usb_id, ""))))
    return result


# --- asking -----------------------------------------------------------------------
class SerialLink:
    """A serial port opened for :func:`ask`, with short timeouts."""

    def __init__(self, port: str, baud: int, bytesize=8, parity="N", stopbits=1,
                 dtr: bool | None = None, rts: bool | None = None):
        import serial

        options = {}
        if sys.platform != "win32":
            options["exclusive"] = True  # never share a port another program has open
        try:
            self._serial = serial.Serial(port, int(baud), bytesize=int(bytesize),
                                         parity=str(parity).upper()[:1],
                                         stopbits=stopbits, timeout=0.1, **options)
        except (serial.SerialException, ValueError) as exc:
            raise TransportError(f"cannot open {port}: {exc}") from exc
        if dtr is not None:
            self._serial.dtr = bool(dtr)
        if rts is not None:
            self._serial.rts = bool(rts)

    def write(self, data: bytes) -> None:
        self._serial.write(data)

    def clear(self) -> None:
        self._serial.reset_input_buffer()

    def read_for(self, seconds: float) -> bytes:
        deadline, data = time.monotonic() + seconds, b""
        while time.monotonic() < deadline:
            data += self._serial.read(256)
        return data

    def read_line(self, seconds: float) -> bytes:
        deadline, data = time.monotonic() + seconds, b""
        while time.monotonic() < deadline and not data.endswith(b"\n"):
            data += self._serial.read(1)
        return data

    def close(self) -> None:
        self._serial.close()


Opener = Callable[..., SerialLink]


def _text(data: bytes) -> str:
    return data.decode("latin-1", "replace").strip()


MODEL = re.compile(r"\b(\d{3,5}[A-Z]?)\b")


def match_idn(reply: str, registry: PluginRegistry) -> list[Match]:
    """Device types a ``*IDN?`` reply fits: by the plugin's pattern, or else by the
    model number (the reply's second field) appearing in a device type's name."""
    matches = [Match(s.id, s.name, "its *IDN? reply fits the plugin's pattern")
               for s in registry.devices.values()
               if s.discovery and s.discovery.idn and re.search(s.discovery.idn, reply)]
    if matches:
        return matches
    fields = [f.strip() for f in reply.split(",")]
    model = fields[1].upper() if len(fields) > 1 else ""
    if model:
        matches = [Match(s.id, s.name, f"the model {model} appears in the device type's name")
                   for s in registry.devices.values()
                   if model in MODEL.findall(s.name.upper()) and s.transports != ("sim",)]
    return matches


def _generic(reply: str, registry: PluginRegistry) -> list[Match]:
    spec = registry.devices.get("generic.scpi")
    return [Match(spec.id, spec.name, "it answers *IDN?, but no plugin knows the model")] \
        if spec else []


# --- one device type ("Find" in the device dialog) ------------------------------------
def _on_gpib(spec: DeviceSpec) -> bool:
    return bool(spec.discovery and (spec.discovery.talk or spec.discovery.idn)
                and "prologix" in spec.transports)


def _on_usbtmc(spec: DeviceSpec) -> bool:
    return bool(spec.discovery and spec.discovery.idn
                and {"usbtmc", "usbgpib"} & set(spec.transports))


def findable(spec: DeviceSpec) -> bool:
    """Does the plugin say how to recognize this device type?"""
    d = spec.discovery
    return bool(d and (d.usb or d.serial or d.listen or _on_gpib(spec) or _on_usbtmc(spec)))


def needs_port(spec: DeviceSpec, port: Port) -> bool:
    """Could this device type be found by asking ``port``?"""
    d = spec.discovery
    if not d or port.why_not:
        return False
    if port.kind == "usbtmc":
        return _on_usbtmc(spec)
    return bool(d.serial or d.listen or _on_gpib(spec))


def what_is_sent(spec: DeviceSpec) -> list[str]:
    """For people: what asking a port for this device type sends."""
    d = spec.discovery
    sent = []
    if d and d.listen:
        sent.append(f"nothing at first: it listens {LISTEN_SECONDS:g} s at {d.listen['baud']} baud")
    sent.append(f"++ver at {ADAPTER_BAUD} baud, to find a GPIB adapter")
    if _on_gpib(spec):
        sent.append("on an AR488: ++fndl, then *IDN? to instruments that send nothing "
                    "unasked, and a device clear to those that do not answer")
    if d and d.serial:
        query = d.serial.get("query") or d.serial["query_hex"]
        sent.append(f"{query} at {d.serial['baud']} baud")
    return sent


def _only(registry: PluginRegistry, device_id: str | None):
    """The registry, or a stand-in for it holding just one device type (the steps
    of :func:`ask` only use ``registry.devices``)."""
    if device_id is None:
        return registry
    return SimpleNamespace(devices={device_id: registry.devices[device_id]})


def ask(port: Port, registry: PluginRegistry, *, log: Log = lambda _: None,
        ask_idn: bool = True, opener: Opener = SerialLink,
        only: str | None = None) -> list[Found]:
    """Talk to one port to find out what is connected (see the module description).

    With ``only`` (a device type id), only that type's own discovery is used and only
    what fits it is returned. ``++ver`` is still sent first, so that a query meant
    for a serial device never reaches a GPIB bus through an adapter.
    """
    if port.why_not:
        raise ValueError(f"{port.name} is not asked: {port.why_not}")
    registry = _only(registry, only)
    if port.kind == "usbtmc":
        found = _ask_usbtmc(port, registry, log)
    else:
        found = _listen(port.name, registry, log, opener)
        if not found:
            scan = only is None or _on_gpib(registry.devices[only])
            found = _adapter(port.name, registry, log, opener, ask_idn, scan,
                             port.gpib_in_project)
        if found is None:
            found = _serial_queries(port.name, registry, log, opener)
    if only is None:
        return found
    return [f for f in found if any(m.device_id == only for m in f.matches)]


def _settings(entry: dict) -> tuple:
    return (int(entry["baud"]), int(entry.get("bytesize", 8)),
            str(entry.get("parity", "N")).upper()[:1], float(entry.get("stopbits", 1)))


def _stopbits(value: float):
    return int(value) if float(value).is_integer() else value


def _listen(port: str, registry: PluginRegistry, log: Log, opener: Opener) -> list[Found]:
    groups: dict[tuple, list[DeviceSpec]] = {}
    for spec in registry.devices.values():
        listen = spec.discovery.listen if spec.discovery else None
        if listen:
            key = _settings(listen) + (listen.get("dtr"), listen.get("rts"))
            groups.setdefault(key, []).append(spec)
    for (baud, bytesize, parity, stopbits, dtr, rts), specs in groups.items():
        try:
            link = opener(port, baud, bytesize, parity, _stopbits(stopbits), dtr=dtr, rts=rts)
        except TransportError as exc:
            log(f"{port}: {exc}")
            return []
        try:
            time.sleep(0.2)  # let a receiver powered from DTR settle
            link.clear()
            data = link.read_for(LISTEN_SECONDS)
        finally:
            link.close()
        log(f"{port}: listened {LISTEN_SECONDS:g} s at {baud} baud, sending nothing: "
            f"{len(data)} bytes")
        matches = []
        for spec in specs:
            try:
                recognize = spec.plugin.load(spec.discovery.listen["recognize"])
                if data and recognize(data):
                    matches.append(Match(spec.id, spec.name, "it sends the device's packets"))
            except Exception as exc:  # a broken plugin must not stop the search
                log(f"{port}: {spec.id} could not check the data: {exc}")
        if matches:
            return [Found(f"serial://{port}?baud={baud}", port, "sends by itself",
                          data[:28].hex(" "), matches)]
    return []


def _adapter(port: str, registry: PluginRegistry, log: Log, opener: Opener,
             ask_idn: bool, scan: bool = True,
             skip: dict[int, str] | None = None) -> list[Found] | None:
    """None if no GPIB adapter answers on the port, else what is on its bus."""
    try:
        link = opener(port, ADAPTER_BAUD)
    except TransportError as exc:
        log(f"{port}: {exc}")
        return []  # the port cannot be opened: nothing more to try
    try:
        version = ""
        for _ in range(3):  # an Arduino Nano restarts when the port opens
            link.clear()
            link.write(b"++ver\n")
            log(f"{port}: sent ++ver at {ADAPTER_BAUD} baud (a GPIB adapter's own command)")
            version = _text(link.read_line(1.0))
            if version:
                break
        if not version:
            return None
        log(f"{port}: answers {version!r}")
        if "AR488" not in version.upper() and "PROLOGIX" not in version.upper():
            return None
        if not scan:
            log(f"{port}: a GPIB adapter, and the device type is not a GPIB instrument")
            return []
        if "AR488" not in version.upper():
            log(f"{port}: a Prologix adapter cannot list the instruments on its bus; add them "
                "with their GPIB address by hand")
            return [Found(f"prologix://{port}/5", port, "GPIB adapter", version)]
        return _scan_ar488(port, link, registry, log, ask_idn, skip or {})
    finally:
        link.close()


def _scan_ar488(port: str, link, registry: PluginRegistry, log: Log,
                ask_idn: bool, skip: dict[int, str]) -> list[Found]:
    def send(text: str) -> None:
        link.write(text.encode("ascii") + b"\n")
        log(f"{port}: sent {text}")

    def ask_adapter(text: str) -> str:
        link.clear()
        send(text)
        return _text(link.read_line(1.0))

    saved = {name: ask_adapter(f"++{name}") for name in ("mode", "addr", "auto", "eoi",
                                                         "read_tmo_ms")}
    if saved["mode"] != "1":
        log(f"{port}: the AR488 is in device mode, not controller mode; not scanning")
        return [Found(f"prologix://{port}/5", port, "GPIB adapter (device mode)")]
    found: list[Found] = []
    try:
        send("++auto 0")
        send(f"++read_tmo_ms {round(REPLY_SECONDS * 1000)}")
        link.clear()
        send("++fndl")
        reply = _text(link.read_line(FNDL_SECONDS))
        addresses = [int(a) for a in re.findall(r"\d+", reply) if 0 <= int(a) <= 30]
        log(f"{port}: instruments at GPIB address(es) {', '.join(map(str, addresses)) or 'none'}")
        for address in addresses:
            if address in skip:
                log(f"{port}: address {address} is '{skip[address]}' in this project; "
                    "not asked")
                continue
            found.append(_gpib_instrument(port, address, link, send, registry, log, ask_idn))
    finally:
        for name in ("addr", "auto", "eoi", "read_tmo_ms"):
            if saved[name].isdigit():
                send(f"++{name} {saved[name]}")
    return found


def _gpib_instrument(port: str, address: int, link, send, registry: PluginRegistry,
                     log: Log, ask_idn: bool) -> Found:
    where = f"{port}, GPIB address {address}"
    resource = f"prologix://{port}/{address}"
    send(f"++addr {address}")
    # first only listen: addressed to talk, older HP instruments send a reading unasked
    link.clear()
    send("++read 10")
    line = _text(link.read_line(REPLY_SECONDS + 0.5))
    if line:
        matches = [Match(s.id, s.name, "it sends readings in this instrument's format")
                   for s in registry.devices.values()
                   if s.discovery and s.discovery.talk and re.search(s.discovery.talk, line)]
        return Found(resource, where, "sends readings unasked", line, matches)
    if not ask_idn:
        return Found(resource, where, "answers on the bus, not asked further")
    send("++eoi 1")
    link.clear()
    link.write(prologix_escape(b"*IDN?") + b"\n")
    log(f"{port}: sent *IDN? to address {address}")
    send("++read eoi")
    reply = _text(link.read_line(REPLY_SECONDS + 0.5))
    if reply:
        return Found(resource, where, "*IDN?", reply,
                     match_idn(reply, registry) or _generic(reply, registry))
    send("++clr")  # ends the error state an unknown command leaves in old HP counters
    return Found(resource, where, "answers on the bus, but not to *IDN?")


def _serial_queries(port: str, registry: PluginRegistry, log: Log,
                    opener: Opener) -> list[Found]:
    groups: dict[tuple, list[DeviceSpec]] = {}
    for spec in registry.devices.values():
        query = spec.discovery.serial if spec.discovery else None
        if query:
            frame = bytes.fromhex(query["query_hex"]) if query.get("query_hex") else None
            key = _settings(query) + ((frame,) if frame else (query["query"],))
            groups.setdefault(key, []).append(spec)
    for (baud, bytesize, parity, stopbits, query), specs in groups.items():
        binary = isinstance(query, bytes)
        shown = query.hex(" ") if binary else query
        try:
            link = opener(port, baud, bytesize, parity, _stopbits(stopbits))
        except TransportError as exc:
            log(f"{port}: {exc}")
            return []
        try:
            link.clear()
            link.write(query if binary else query.encode("latin-1") + b"\n")
            log(f"{port}: sent {shown} at {baud} baud (asked by {', '.join(s.id for s in specs)})")
            if binary:
                data = link.read_for(FRAME_SECONDS)
                if data.startswith(query):  # some RS-485 adapters echo what is sent
                    data = data[len(query):]
                reply = data.hex(" ")
            else:
                reply = _text(link.read_line(1.0))
        finally:
            link.close()
        matches = []
        for spec in specs:
            if not reply:
                break
            try:
                if binary:
                    fits = spec.plugin.load(spec.discovery.serial["recognize"])(data)
                else:
                    fits = re.search(spec.discovery.serial["reply"], reply)
            except Exception as exc:  # a broken plugin must not stop the search
                log(f"{port}: {spec.id} could not check the reply: {exc}")
                continue
            if fits:
                matches.append(Match(spec.id, spec.name, "it answers the query as this "
                                     "device does"))
        if matches:
            return [Found(f"serial://{port}?baud={baud}", port, shown, reply, matches)]
        if reply:
            log(f"{port}: answers {shown} with {reply!r}, which no plugin expects")
    log(f"{port}: nothing recognized")
    return []


def _ask_usbtmc(port: Port, registry: PluginRegistry, log: Log) -> list[Found]:
    from .transports import UsbTmcTransport

    usbgpib = port.usb_id == f"{USBGPIB_IDS[0]:04X}:{USBGPIB_IDS[1]:04X}"
    serial = port.name.partition("/")[2]
    resource = f"usbgpib://{serial}" if usbgpib else f"usbtmc://{port.name}"
    transport = UsbTmcTransport(port.name, timeout=REPLY_SECONDS)
    transport.open()
    try:
        transport.write("*IDN?")
        log(f"{port.name}: sent *IDN?")
        try:
            reply = transport.read().strip()
        except TimeoutError:
            reply = ""
    finally:
        transport.close()
    if not reply:
        return [Found(resource, port.name, "no answer to *IDN?")]
    return [Found(resource, port.name, "*IDN?", reply,
                  match_idn(reply, registry) or _generic(reply, registry))]
