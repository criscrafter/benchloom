# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Find devices: what is recognized, and above all what is (not) sent where."""

import types

import pytest

from benchloom import cli, discovery
from benchloom.discovery import Port, ask, look, match_idn
from benchloom.plugin import PluginError, PluginRegistry, read_manifest
from benchloom.plugins.fs9721.meter import encode
from benchloom.transports import TransportError


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(discovery.time, "sleep", lambda _: None)


@pytest.fixture(scope="module")
def registry():
    return PluginRegistry.default()


class Device:
    """A fake serial device: gets each line written, may send by itself."""

    stream = b""

    def __init__(self):
        self.received: list[tuple[int, str]] = []  # (baud, line)
        self.out = bytearray()

    def line(self, baud: int, text: str) -> bytes | None:
        return None


class Link:
    def __init__(self, device: Device, baud: int, settings: tuple):
        self.device, self.baud, self.settings = device, baud, settings
        self.closed = False

    def write(self, data: bytes) -> None:
        for raw in data.split(b"\n"):
            if raw:
                text = raw.replace(b"\x1b", b"").decode("latin-1")
                self.device.received.append((self.baud, text))
                reply = self.device.line(self.baud, text)
                if reply:
                    self.device.out += reply

    def clear(self) -> None:
        self.device.out.clear()

    def read_for(self, seconds: float) -> bytes:
        if self.baud == 2400:
            return self.device.stream
        data = bytes(self.device.out)
        self.device.out.clear()
        return data

    def read_line(self, seconds: float) -> bytes:
        end = self.device.out.find(b"\n")
        if end < 0:
            return b""
        data = bytes(self.device.out[:end + 1])
        del self.device.out[:end + 1]
        return data

    def close(self) -> None:
        self.closed = True


def opener_for(devices: dict[str, Device], opened: list | None = None):
    def opener(port, baud, bytesize=8, parity="N", stopbits=1, dtr=None, rts=None):
        if port not in devices:
            raise TransportError(f"cannot open {port}: no such port")
        link = Link(devices[port], int(baud), (bytesize, parity, stopbits, dtr, rts))
        if opened is not None:
            opened.append(link)
        return link
    return opener


class Feeltech(Device):
    def line(self, baud, text):
        return b"FY6900-60M\n" if (baud, text) == (115200, "UMO") else None


KP184_QUERY = bytes.fromhex("01 03 01 0E 00 04 24 36")


class KP184(Device):
    """A Kunkin load at address 1 on an RS-485 adapter that may echo what it sends."""

    def __init__(self, echo=False):
        super().__init__()
        self.echo = echo

    def line(self, baud, text):
        if (baud, text.encode("latin-1")) != (9600, KP184_QUERY):
            return None
        return (KP184_QUERY if self.echo else b"") + bytes.fromhex("01 03 04 00 00 00 00 fa 33")


class Meter(Device):
    stream = b"\x00\x01" + encode("1.234", {"RS232", "DC", "V"}) * 2


class AR488(Device):
    """An AR488 with instruments on its bus: address -> "3478a", "34420a" or "5334"."""

    def __init__(self, instruments: dict[int, str], mode="1"):
        super().__init__()
        self.instruments = instruments
        self.settings = {"mode": mode, "addr": "7", "auto": "1", "eoi": "0",
                         "read_tmo_ms": "1200"}
        self.to_instrument: dict[int, list[str]] = {a: [] for a in instruments}
        self.pending = b""
        self.cleared: list[int] = []

    def line(self, baud, text):
        if not text.startswith("++"):
            address = int(self.settings["addr"])
            self.to_instrument.setdefault(address, []).append(text)
            if text == "*IDN?" and self.instruments.get(address) == "34420a":
                self.pending = b"HEWLETT-PACKARD,34420A,0,1.2\n"
            return None
        name, _, value = text[2:].partition(" ")
        if name == "ver":
            return b"AR488 GPIB controller, ver. 0.53.46, 22/05/2026\r\n"
        if name in self.settings:
            if value:
                self.settings[name] = value
                return None
            return self.settings[name].encode() + b"\r\n"
        if name == "fndl":
            return " ".join(map(str, sorted(self.instruments))).encode() + b"\r\n"
        address = int(self.settings["addr"])
        kind = self.instruments.get(address)
        if name == "read" and value == "10":
            return {"3478a": b"+2.98955E+0\r\n", "5334": b"F    +9.9999221E+05\r\n"}.get(kind)
        if name == "read":
            reply, self.pending = self.pending, b""
            return reply
        if name == "clr":
            self.cleared.append(address)
        return None


def serial_port(name="COM9", **kw):
    return Port("serial", name, "USB serial", **kw)


# --- manifests --------------------------------------------------------------------
def test_built_in_plugins_declare_discovery(registry):
    devices = registry.devices
    assert devices["hantek.dso5000p"].discovery.usb == ("049F:505A",)
    assert devices["feeltech.fy6900"].discovery.serial["query"] == "UMO"
    assert devices["fs9721.dt4000zc"].discovery.listen["baud"] == 2400
    assert devices["kunkin.kp184"].discovery.serial["recognize"] == "driver:recognize"
    assert devices["hp.3478a"].discovery.talk
    assert devices["generic.scpi"].discovery is None


def _manifest(tmp_path, discovery_table: str):
    folder = tmp_path / "p"
    folder.mkdir(exist_ok=True)
    (folder / "plugin.toml").write_text(
        '[plugin]\nid = "p"\n[[device]]\nid = "d"\ndriver = "m:D"\n'
        f"[device.discovery]\n{discovery_table}\n", encoding="utf-8")
    return folder


@pytest.mark.parametrize("table, problem", [
    ("idn = '('", "not a valid pattern"),
    ("usb = ['49F:505A']", "not a USB id"),
    ("serial = { baud = 9600, query = 'X?' }", "missing reply"),
    ("serial = { baud = 1200, query = 'X?', reply = 'X' }", "1200 baud"),
    ("listen = { baud = 2400, recognize = 'nofunction' }", "module:function"),
    ("serial = { baud = 9600, query = 'X?', reply = 'X', term = 'x' }", "unknown term"),
    ("serial = { baud = 9600, query_hex = '01 0' , recognize = 'm:f' }", "not hex"),
    ("serial = { baud = 9600, query_hex = '01', recognize = 'f' }", "module:function"),
    ("serial = { baud = 9600, query_hex = '01' }", "missing recognize"),
    ("serial = { baud = 9600, query = 'X?', reply = 'X', recognize = 'm:f' }",
     "recognize goes with query_hex"),
])
def test_bad_discovery_tables_are_refused(tmp_path, table, problem):
    with pytest.raises(PluginError, match=problem):
        read_manifest(_manifest(tmp_path, table))


# --- looking sends nothing ----------------------------------------------------------
def test_look_lists_ports_and_marks_bluetooth_and_project_ports(monkeypatch, registry):
    def port(device, description, hwid, vid=None, pid=None):
        return types.SimpleNamespace(device=device, description=description, hwid=hwid,
                                     vid=vid, pid=pid)

    fake = [port("COM17", "USB serial", "USB VID:PID=1B4F:9206", 0x1B4F, 0x9206),
            port("COM5", "Serial over Bluetooth", "BTHENUM\\{00001101}"),
            port("COM12", "CH340", "USB VID:PID=1A86:7523", 0x1A86, 0x7523)]
    from serial.tools import list_ports

    monkeypatch.setattr(list_ports, "comports", lambda: fake)
    monkeypatch.setattr("benchloom.transports.libusb_backend", lambda: None)
    result = look(registry, {"gen": "serial://COM12?baud=115200"})
    ports = {p.name: p for p in result.ports}
    assert ports["COM17"].usb_id == "1B4F:9206" and not ports["COM17"].why_not
    assert "Bluetooth" in ports["COM5"].why_not
    assert "'gen'" in ports["COM12"].why_not
    with pytest.raises(ValueError, match="Bluetooth"):
        ask(ports["COM5"], registry)


# --- asking a serial port ---------------------------------------------------------
def test_a_feeltech_answers_its_model_query(registry):
    device = Feeltech()
    opened = []
    found = ask(serial_port(), registry, opener=opener_for({"COM9": device}, opened))
    assert [m.device_id for m in found[0].matches] == ["feeltech.fy6900"]
    assert found[0].resource == "serial://COM9?baud=115200"
    assert found[0].reply == "FY6900-60M"
    assert all(link.closed for link in opened)
    # the query went out with the generator's fixed 8N2
    assert opened[-1].settings[:3] == (8, "N", 2)


@pytest.mark.parametrize("echo", [False, True])
def test_a_kp184_answers_a_modbus_read(registry, echo):
    device = KP184(echo)
    found = ask(serial_port("COM8"), registry, opener=opener_for({"COM8": device}))
    assert [m.device_id for m in found[0].matches] == ["kunkin.kp184"]
    assert found[0].resource == "serial://COM8?baud=9600"
    assert found[0].reply == "01 03 04 00 00 00 00 fa 33"
    # only a read went to the load: function 3, nothing written
    assert [t.encode("latin-1") for b, t in device.received if b == 9600] == [KP184_QUERY]


def test_a_meter_is_recognized_by_listening_only(registry):
    device = Meter()
    opened = []
    found = ask(serial_port(), registry, opener=opener_for({"COM9": device}, opened))
    assert [m.device_id for m in found[0].matches] == ["fs9721.dt4000zc"]
    assert found[0].resource == "serial://COM9?baud=2400"
    assert device.received == []  # nothing was sent
    assert opened[0].settings[3:] == (True, False)  # DTR on, RTS off: the cable's power


def test_an_unknown_device_gets_only_the_declared_queries(registry):
    device = Device()
    log = []
    assert ask(serial_port(), registry, opener=opener_for({"COM9": device}),
               log=log.append) == []
    assert {text for _, text in device.received} == {"++ver", "UMO",
                                                     KP184_QUERY.decode("latin-1")}
    assert "nothing recognized" in log[-1]


def test_a_port_that_cannot_be_opened_is_reported(registry):
    log = []
    assert ask(serial_port("COM99"), registry, opener=opener_for({}), log=log.append) == []
    assert "cannot open COM99" in log[0]


# --- a GPIB adapter -------------------------------------------------------------------
def test_ar488_bus_scan(registry):
    adapter = AR488({1: "3478a", 5: "34420a", 9: "silent", 12: "5334"})
    found = {f.where: f for f in ask(serial_port("COM17"), registry,
                                     opener=opener_for({"COM17": adapter}))}
    meter = found["COM17, GPIB address 1"]
    assert meter.resource == "prologix://COM17/1"
    assert meter.how == "sends readings unasked"
    assert [m.device_id for m in meter.matches] == ["hp.3478a"]
    assert [m.device_id for m in found["COM17, GPIB address 5"].matches] == ["hp.34420a"]
    counter = {m.device_id for m in found["COM17, GPIB address 12"].matches}
    assert counter == {"hp.5334a", "hp.5384a"}  # both send this format
    assert found["COM17, GPIB address 9"].matches == []

    # instruments that talked unasked were sent nothing at all
    assert adapter.to_instrument[1] == [] and adapter.to_instrument[12] == []
    # only *IDN? reached the others, and the silent one got a device clear
    assert adapter.to_instrument[5] == ["*IDN?"] and adapter.to_instrument[9] == ["*IDN?"]
    assert adapter.cleared == [9]
    # the adapter's settings are back as they were
    assert adapter.settings == {"mode": "1", "addr": "7", "auto": "1", "eoi": "0",
                                "read_tmo_ms": "1200"}
    # and no serial-device query (UMO) went to the adapter, where it would reach GPIB
    assert all(not text.startswith("UMO") for _, text in adapter.received)


def test_ar488_without_idn_only_listens(registry):
    adapter = AR488({5: "34420a"})
    found = ask(serial_port("COM17"), registry, opener=opener_for({"COM17": adapter}),
                ask_idn=False)
    assert adapter.to_instrument[5] == []
    assert found[0].how == "answers on the bus, not asked further"


def test_ar488_in_device_mode_is_not_scanned(registry):
    adapter = AR488({1: "3478a"}, mode="0")
    found = ask(serial_port("COM17"), registry, opener=opener_for({"COM17": adapter}))
    assert "device mode" in found[0].how
    assert not any(text == "++fndl" for _, text in adapter.received)


def test_idn_matching(registry):
    assert [m.device_id for m in match_idn("FLUKE,8508A,123,1.0", registry)] == ["fluke.8508a"]
    assert [m.device_id for m in match_idn("DATRON INSTRUMENTS,1271,1,2", registry)] \
        == ["wavetek.1281"]
    # no pattern, but the model is in a device type's name
    assert [m.device_id for m in match_idn("HEWLETT-PACKARD,53131A,0,4806", registry)] \
        == ["hp.53131a"]
    assert match_idn("ACME,X1,0,1", registry) == []


# --- command line ---------------------------------------------------------------------
def test_cli_discover_sends_nothing_without_ask(monkeypatch, capsys):
    monkeypatch.setattr(discovery, "look", lambda registry, resources: discovery.Look(
        ports=[serial_port("COM17")]))
    monkeypatch.setattr(discovery, "ask", lambda *a, **k: pytest.fail("asked a port"))
    assert cli.main(["discover"]) == 0
    out = capsys.readouterr().out
    assert "COM17" in out and "--ask COM17" in out


# --- one device type -----------------------------------------------------------------
def test_only_one_type_sends_only_its_own_query(registry):
    device = KP184()
    found = ask(serial_port("COM8"), registry, opener=opener_for({"COM8": device}),
                only="kunkin.kp184")
    assert [m.device_id for m in found[0].matches] == ["kunkin.kp184"]
    # ++ver still goes first (see below), but no other plugin's query
    assert {t for _, t in device.received} == {"++ver", KP184_QUERY.decode("latin-1")}


def test_only_a_serial_type_does_not_scan_a_gpib_bus(registry):
    adapter = AR488({1: "3478a"})
    assert ask(serial_port("COM17"), registry, opener=opener_for({"COM17": adapter}),
               only="kunkin.kp184") == []
    # the adapter was recognized by ++ver, and then left alone: the Modbus frame would
    # otherwise have gone through it onto the bus
    assert [t for _, t in adapter.received] == ["++ver"]


def test_only_a_gpib_type_keeps_just_its_matches(registry):
    adapter = AR488({1: "3478a", 5: "34420a"})
    found = ask(serial_port("COM17"), registry, opener=opener_for({"COM17": adapter}),
                only="hp.34420a")
    assert [f.resource for f in found] == ["prologix://COM17/5"]


def test_which_types_can_be_found(registry):
    devices = registry.devices
    assert discovery.findable(devices["kunkin.kp184"])
    assert discovery.findable(devices["hantek.dso5000p"])
    assert not discovery.findable(devices["demo.psu"])
    usbtmc = Port("usbtmc", "0957:0607")
    assert not discovery.needs_port(devices["kunkin.kp184"], usbtmc)
    assert not discovery.needs_port(devices["hantek.dso5000p"], serial_port())
    assert discovery.needs_port(devices["hp.3478a"], serial_port())


# --- a GPIB adapter the project already uses -------------------------------------------
def test_a_projects_gpib_adapter_stays_askable(monkeypatch, registry):
    from serial.tools import list_ports

    fake = [types.SimpleNamespace(device=name, description="USB serial",
                                  hwid="USB VID:PID=1B4F:9206", vid=0x1B4F, pid=0x9206)
            for name in ("COM17", "COM18")]
    monkeypatch.setattr(list_ports, "comports", lambda: fake)
    monkeypatch.setattr("benchloom.transports.libusb_backend", lambda: None)
    ports = {p.name: p for p in look(registry, {"dmm": "prologix://COM17/1",
                                                "load": "serial://COM18?baud=9600"}).ports}
    assert not ports["COM17"].why_not
    assert ports["COM17"].gpib_in_project == {1: "dmm"}
    assert "address 1 ('dmm') not asked" in ports["COM17"].note
    assert "'load'" in ports["COM18"].why_not  # a serial device's port stays blocked


def test_a_projects_gpib_instruments_are_skipped(registry):
    adapter = AR488({1: "3478a", 5: "34420a"})
    port = serial_port("COM17", gpib_in_project={1: "dmm"})
    log = []
    found = ask(port, registry, opener=opener_for({"COM17": adapter}), log=log.append)
    assert [f.resource for f in found] == ["prologix://COM17/5"]
    assert adapter.to_instrument[1] == []
    assert "++addr 1" not in [text for _, text in adapter.received]  # never addressed
    assert any("address 1 is 'dmm' in this project" in line for line in log)
