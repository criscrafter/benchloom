# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import pytest

from benchloom.engine import open_device
from benchloom.plugin import CommandError, PluginRegistry
from benchloom.plugins.hp.hp5384a import READING
from benchloom.project import DeviceConfig

REGISTRY = PluginRegistry.default()


def open_counter(**settings):
    counter = open_device(REGISTRY, DeviceConfig("counter", "hp.5384a", "sim://", settings))
    return counter, counter.transport


def record(sim):
    sent = []
    original = sim.write
    sim.write = lambda text: (sent.append(text), original(text))
    return sent


def test_connect_initializes_and_identifies():
    counter, sim = open_counter()
    assert counter.idn() == "HP5384A" and counter.model == "HP5384A"
    assert sim.function == "FU1" and sim.gate == 0.1


def test_measures_frequency_period_and_check():
    counter, sim = open_counter()
    assert counter.measure_frequency() == pytest.approx(1e6)
    assert counter.measure_frequency("B") == pytest.approx(100e6)
    assert counter.measure_period() == pytest.approx(1e-6)
    assert counter.measure_check() == pytest.approx(10e6)
    assert counter.measure_frequency("A") == pytest.approx(1e6)


def test_restarts_before_each_reading_and_sends_a_function_only_when_it_changes():
    counter, sim = open_counter()
    sent = record(sim)
    counter.measure_frequency()
    counter.measure_frequency()
    assert sent == ["RE", "RE"]  # FU1 is already set by IN
    sent.clear()
    counter.measure_period()
    assert sent == ["FU2", "SE", "RE"]


def test_settings_and_gate_time():
    counter, sim = open_counter(gate_time=1.0)
    assert sim.gate == 1.0 and counter.gate_time == 1.0
    counter.set_gate_time(10)
    assert sim.gate == 10.0
    with pytest.raises(CommandError, match="0.1, 1 or 10 s"):
        counter.set_gate_time(2)
    counter.set_attenuation("20")
    counter.set_filter(True)
    counter.set_manual_level(True)
    assert sim.attenuator == 20 and sim.filter and sim.manual_level
    counter.initialize()
    assert (sim.attenuator, sim.filter, sim.manual_level, sim.gate) == (1, False, False, 0.1)
    assert counter.gate_time == 0.1


def test_readings_parse_as_the_counter_sends_them():
    assert READING.match("F   +9.9999236E+5").group(1, 2) == ("F", "+9.9999236E+5")
    assert READING.match("S  +1.00000762E-6").group(2) == "+1.00000762E-6"
    counter, sim = open_counter()
    assert sim._reading() == "F   +1.0000000E+6"  # 17 characters, as in the manual


def test_an_illegal_code_is_reported_and_cleared():
    counter, sim = open_counter()
    with pytest.raises(CommandError, match="error 53: illegal character in the DR"):
        counter._send("DR" + "A" * 13)  # past the driver's own check
    assert sim.error == 0  # the driver sent the device clear
    with pytest.raises(CommandError, match="12 characters"):
        counter.call("set_display_text", "ABCDEFGHIJKLM")  # caught before sending
    counter.set_display_text("BENCH.LOOM:12")  # punctuation does not count
    counter.clear_display_text()
    assert sim.display == ""
    with pytest.raises(CommandError, match="error 50"):
        counter._send("XX")
    assert counter.measure_frequency() == pytest.approx(1e6)


def test_no_signal_fails_and_the_next_reading_works():
    counter, sim = open_counter()
    sim.signals["B"] = None
    with pytest.raises(CommandError, match="no reading on channel B"):
        counter.measure_frequency("B")
    assert sim.timeout == counter.base_timeout  # the long read timeout is undone
    assert counter.measure_frequency("A") == pytest.approx(1e6)


def test_rejects_another_instrument():
    from benchloom.plugins.hp.hp5384a import HP5384ASim

    class Other(HP5384ASim):
        identity = "HP3478A"

    from benchloom.plugins.hp.hp5384a import HP5384A
    with pytest.raises(CommandError, match="not HP5384A or HP5385A"):
        HP5384A(Other()).connect()
