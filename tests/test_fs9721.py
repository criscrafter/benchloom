# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import math

import pytest

from benchloom.engine import open_device
from benchloom.plugin import CommandError, PluginRegistry
from benchloom.plugins.fs9721.meter import decode, encode, is_packet
from benchloom.project import DeviceConfig
from benchloom.transports import SerialTransport

REGISTRY = PluginRegistry.default()


def open_sim():
    device = open_device(REGISTRY, DeviceConfig("dmm", "fs9721.dt4000zc", "sim://", {}))
    return device, device.transport


@pytest.mark.parametrize("display, flags, value, mode, text", [
    ("5.003", {"DC", "AUTO", "V"}, 5.003, "DC V", "5.003 V DC"),
    ("-1.234", {"DC", "m", "V"}, -1.234e-3, "DC V", "-1.234 mV DC"),
    ("230.1", {"AC", "V"}, 230.1, "AC V", "230.1 V AC"),
    ("12.34", {"k", "Ω"}, 12340.0, "Ω", "12.34 kΩ"),
    ("1.000", {"M", "Hz"}, 1e6, "Hz", "1.000 MHz"),
    ("47.0", {"n", "F"}, 47e-9, "F", "47.0 nF"),
    ("123.4", {"DC", "µ", "A"}, 123.4e-6, "DC A", "123.4 µA DC"),
    ("49.9", {"%"}, 49.9, "%", "49.9 %"),
    ("0.512", {"diode", "V"}, 0.512, "diode", "0.512 V"),
    ("5.003", {"DC", "V", "HOLD"}, 5.003, "DC V", "5.003 V DC HOLD"),
])
def test_decode(display, flags, value, mode, text):
    reading = decode(encode(display, flags))
    assert reading.value == pytest.approx(value, rel=1e-12)
    assert (reading.mode, str(reading)) == (mode, text)


def test_decode_overload_and_bad_packets():
    assert decode(encode("0L", {"M", "Ω"})).value == math.inf
    assert decode(encode("-0L", {"DC", "V"})).value == -math.inf
    good = encode("5.003", {"DC", "V"})
    assert is_packet(good) and not is_packet(good[1:] + good[:1])
    with pytest.raises(ValueError):
        decode(good[:13] + b"\x00")
    with pytest.raises(ValueError):
        encode(".123")  # the point never comes before the first digit


def test_decode_a_packet_from_a_real_dt4000zc():
    # captured from the meter on its mV range; byte E has a symbol of its own set
    packet = bytes.fromhex("17 28 35 47 5e 63 7f 8f 9e a0 b8 c0 d4 e8".replace(" ", ""))
    reading = decode(packet)
    assert reading.value == pytest.approx(-0.1696)
    assert (reading.mode, str(reading)) == ("DC V", "-169.6 mV DC")
    assert {"AUTO", "RS232"} <= reading.flags


@pytest.mark.parametrize("packet, value, mode, text", [
    ("17 27 3d 43 5f 6b 7f 81 95 a0 b0 c0 d4 e8", 9.97, "DC V", "09.97 V DC"),
    ("17 27 3d 43 5f 6b 7f 81 95 a0 b0 c1 d4 e8", 9.97, "DC V", "09.97 V DC HOLD"),
    ("15 2f 3d 47 5d 6f 7d 87 9d a0 b0 c2 d4 e8", 0.0, "DC V", "-00.00 V DC REL"),
    ("13 27 3d 4f 5d 67 7d 87 9d a4 b0 c8 d0 e8", 0.0, "F", "0.000 nF"),
    ("11 27 3d 4f 5d 67 7d 87 9d a0 b0 c0 d2 e8", 0.0, "Hz", "0.000 Hz"),
    ("17 29 3f 47 5f 69 75 81 95 a0 b8 c0 d8 e8", -38.77e-3, "DC A", "-38.77 mA DC"),
    ("17 2f 3d 4b 5e 65 7b 81 9f a0 b0 c0 d8 e8", -0.523, "DC A", "-0.523 A DC"),
    ("1b 25 3b 4f 5f 63 7e 87 9f a0 b0 c0 d4 e8", 2.858, "AC V", "2.858 V AC"),
    ("11 20 35 43 5f 65 7b 89 9f a0 b1 c4 d0 e8", 192.3, "continuity", "192.3 Ω"),
    ("13 27 3d 47 5d 67 7d 8a 97 a2 b0 c4 d0 e8", 400.0, "Ω", "000.4 kΩ"),
    ("11 20 30 47 5d 6e 78 80 90 a0 b2 c4 d0 e8", math.inf, "Ω", "0.L MΩ"),
    ("11 20 30 4f 5d 66 78 80 90 a1 b0 c0 d4 e8", math.inf, "diode", ".0L V"),
    ("11 27 3d 47 5d 67 7d 8f 9d a0 b4 c0 d0 e8", 0.0, "%", "000.0 %"),
    ("13 2f 3d 47 5d 62 77 8b 9e a0 b0 c0 d0 e4", -4.5, "°C", "-004.5 °C"),
])
def test_decode_more_packets_from_a_real_dt4000zc(packet, value, mode, text):
    reading = decode(bytes.fromhex(packet.replace(" ", "")))
    assert reading.value == (value if math.isinf(value) else
                             pytest.approx(value, rel=1e-12, abs=0))
    assert math.copysign(1, reading.value) == math.copysign(1, value)  # REL: 0.0, not -0.0
    assert (reading.mode, str(reading)) == (mode, text)


def test_driver_sets_the_port_and_powers_the_cable():
    meter, sim = open_sim()
    assert sim.baud == 2400
    assert sim.lines == {"dtr": True, "rts": False}
    assert meter.identify() == "display shows 5.003 V DC"


def test_driver_reads_and_checks_the_dial():
    meter, sim = open_sim()
    assert meter.call("read") == pytest.approx(5.003)
    assert meter.call("measure_voltage") == pytest.approx(5.003)
    assert meter.call("read_mode") == "DC V"
    with pytest.raises(CommandError, match="set to DC V, not Ω"):
        meter.call("measure_resistance")
    sim.display, sim.flags = "4.70", {"k", "Ω"}
    assert meter.call("measure_resistance") == pytest.approx(4700)
    sim.display, sim.flags = "0.512", {"diode", "V"}
    with pytest.raises(CommandError, match="set to diode"):
        meter.call("measure_voltage")


def test_driver_finds_packets_in_a_stream_that_starts_mid_packet():
    meter, sim = open_sim()
    sim.read_raw = None  # use the stream below instead of the simulator's packets
    packet = encode("1.234", {"DC", "V"})
    stream = bytearray(packet[5:] + b"\x00\x13" + packet)

    def read_some(size):
        data = bytes(stream[:size])
        del stream[:size]
        return data

    sim.read_some, sim.clear_input = read_some, lambda: None
    assert meter.call("read") == pytest.approx(1.234)


def test_driver_reports_garbled_data_and_silence():
    meter, sim = open_sim()
    sim.clear_input = lambda: None
    sim.read_some = lambda size: bytes.fromhex("060e38021e427e0000002080 80c0".replace(" ", ""))
    sim.timeout = 0.05
    with pytest.raises(CommandError, match="garbled"):
        meter.call("read")
    sim.read_some = lambda size: b""
    with pytest.raises(CommandError, match="no data from the meter"):
        meter.call("read")


def test_serial_transport_sets_lines_before_and_after_open():
    port = SerialTransport("COM99", baud=2400)
    port.set_lines(dtr=True, rts=False)
    assert port.lines == {"dtr": True, "rts": False}

    class FakeSerial:
        dtr = rts = None

    port._serial = FakeSerial()
    port.set_lines(rts=True)
    assert (port._serial.dtr, port._serial.rts) == (True, True)
