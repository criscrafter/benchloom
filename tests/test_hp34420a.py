# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import math

import pytest

from benchloom.engine import open_device
from benchloom.plugin import CommandError, PluginRegistry
from benchloom.project import DeviceConfig

REGISTRY = PluginRegistry.default()


def open_nvm(**settings):
    nvm = open_device(REGISTRY, DeviceConfig("nvm", "hp.34420a", "sim://", settings))
    sim = nvm.transport
    sent = []
    original = sim.write_raw

    def write_raw(data):
        sent.append(bytes(data).decode().strip())
        original(data)

    sim.write_raw = write_raw
    return nvm, sim, sent


def test_34420a_is_listed_as_a_keysight_meter():
    spec = REGISTRY.devices["hp.34420a"]
    assert spec.brand == "Keysight" and spec.kind == "dmm"
    assert spec.maker == REGISTRY.devices["hp.3478a"].maker


def test_34420a_connect_resets_and_checks_the_model():
    nvm, sim, _ = open_nvm(nplc=100)
    from benchloom_plugin_hp.hp34420a import HP34420A, HP34420ASim  # loaded by now

    assert sim.nplc["VOLT:DC"] == 100 and sim.function == "VOLT:DC"
    sim = HP34420ASim()
    sim.identity = "HEWLETT-PACKARD,34401A,0,1-1-1"
    with pytest.raises(CommandError, match="not as a 34420A"):
        HP34420A(sim).connect()
    with pytest.raises(CommandError, match="takes 0.02, 0.2"):
        open_nvm(nplc=5)


def test_34420a_channels_ratio_and_difference():
    nvm, sim, sent = open_nvm()
    assert nvm.measure_voltage(1) == pytest.approx(1.2345e-3)
    assert sent[-1] == "READ?"
    assert nvm.measure_voltage(2) == pytest.approx(0.6e-3)
    assert "ROUT:TERM FRON2" in sent
    assert nvm.measure_ratio() == pytest.approx(1.2345 / 0.6)
    assert 'FUNC "VOLT:DC:RAT"' in sent
    assert nvm.measure_difference() == pytest.approx(0.6345e-3)


def test_34420a_ranges_per_channel_and_overload():
    nvm, sim, sent = open_nvm()
    nvm.measure_voltage(1)
    nvm.set_range(1.1e-3)  # a range reads to 120 %: 1.1 mV fits the 1 mV range
    assert sent[-2] == "SENS1:VOLT:DC:RANG 0.001"
    assert nvm.measure() == math.inf  # 1.2345 mV on the 1 mV range
    nvm.set_channel_range(1, 5e-3)
    assert nvm.measure() == pytest.approx(1.2345e-3)
    with pytest.raises(CommandError, match="largest reads up to 12 V"):
        nvm.set_channel_range(2, 50)  # channel 2 stops at the 10 V range
    nvm.set_channel_range(1, 100)
    nvm.set_autorange()
    assert sent[-2] == "SENS1:VOLT:DC:RANG:AUTO ON"
    nvm.measure_resistance_4wire()
    nvm.set_range(50)
    assert sent[-2] == "FRES:RANG 100"
    with pytest.raises(CommandError, match="no range holds 2e"):
        nvm.set_range(2e6)
    nvm.measure_temperature()
    with pytest.raises(CommandError, match="no range"):
        nvm.set_range(1)


def test_34420a_resistance_modes():
    nvm, sim, sent = open_nvm()
    assert nvm.measure_resistance_4wire() == pytest.approx(100.0123)
    nvm.set_offset_compensation(True)
    nvm.set_low_power(True)
    assert sim.ocomp and sim.low_power
    nvm.set_voltage_limit(100)
    assert sim.volt_limit and sim.volt_limit_value == pytest.approx(0.1)
    nvm.set_voltage_limit(0)
    assert not sim.volt_limit
    with pytest.raises(CommandError):
        nvm.call("set_voltage_limit", 50)


def test_34420a_nplc_and_reading_time():
    nvm, sim, _ = open_nvm()
    nvm.measure_voltage(1)
    nvm.set_nplc(200)
    assert sim.nplc["VOLT:DC"] == 200 and sim.nplc["FRES"] == 10  # each function its own
    assert nvm._seconds_per_reading() > 8  # 200 cycles at 50 Hz, twice over
    with pytest.raises(CommandError):
        nvm.call("set_nplc", 50)


def test_34420a_temperature_probes():
    nvm, sim, _ = open_nvm()
    nvm.set_rtd(91, 1000)
    assert sim.probe["TYPE"] == "FRTD" and sim.probe["RTD"] == "91"
    assert sim.probe["UNIT"] == "C"
    nvm.set_thermocouple("K", "fixed", 23.5)
    assert sim.probe["TC"] == "K" and sim.probe["RJUN"] == "FIX"
    assert sim.probe["RJUN_C"] == "23.5"
    nvm.set_thermistor()
    assert sim.probe["TYPE"] == "THER"
    assert nvm.measure_temperature() == pytest.approx(23.0)
    assert sim.errors == []


def test_34420a_series_of_readings_come_in_one_line():
    nvm, sim, sent = open_nvm()
    assert nvm.measure_many(4) == [pytest.approx(1.2345e-3)] * 4
    assert "SAMP:COUN 4" in sent and "SAMP:COUN 1" in sent
    assert sim.samples == 1


def test_34420a_display_and_errors():
    nvm, sim, _ = open_nvm()
    nvm.display_text('Hello "nanovolts"')
    assert sim.display == "Hello nanov"  # 11 characters
    nvm.display_normal()
    assert sim.display == ""
    nvm.write("VOLT:DC:NPLC 3")
    with pytest.raises(CommandError, match="Illegal parameter"):
        nvm.check_errors()


def test_34420a_rs232_sends_system_remote_first_and_two_stop_bits():
    from benchloom_plugin_hp.hp34420a import HP34420A, HP34420ASim

    sim = HP34420ASim()
    sim.scheme = "serial"  # stand in for a serial port
    configured = {}
    sim.configure = lambda **kw: configured.update(kw)
    nvm = HP34420A(sim)
    nvm.connect()
    assert sim.remote_first and configured == {"stopbits": 2}
    raw = []
    sim.write_raw = lambda data: raw.append(bytes(data))
    nvm._abort()
    assert raw == [b"\x03"]  # Ctrl-C is the device clear over RS-232
    nvm.close()
    assert raw[-1] == b"SYST:LOC\n"


def test_34420a_no_reading_aborts_and_reports():
    nvm, sim, _ = open_nvm()
    cleared = []
    sim.device_clear = lambda: cleared.append(True)
    sim.routes = lambda: []  # READ? goes unanswered
    with pytest.raises(CommandError, match="no reading from the 34420A"):
        nvm.measure()
    assert cleared
