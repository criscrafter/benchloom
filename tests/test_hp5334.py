# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import math

import pytest

from benchloom.engine import open_device
from benchloom.plugin import CommandError, PluginRegistry
from benchloom.plugins.hp.hp5334 import HP5334, PAIR, READING, HP5334Sim
from benchloom.project import DeviceConfig

REGISTRY = PluginRegistry.default()


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr("benchloom.plugins.hp.hp5334.SETTLE", 0)
    monkeypatch.setattr("benchloom.plugins.hp.hp5334.time.sleep", lambda s: None)


def open_counter(**settings):
    counter = open_device(REGISTRY, DeviceConfig("counter", "hp.5334a", "sim://", settings))
    return counter, counter.transport


def record(sim):
    sent = []
    original = sim.write
    sim.write = lambda text: (sent.append(text), original(text))
    return sent


def test_connect_initializes_and_identifies():
    counter, sim = open_counter(gate_time=1.0)
    assert counter.model == "HP5334B" and counter.idn() == "HP5334B"
    assert sim.function == 1 and sim.gate == 1.0 and counter.gate_time == 1.0


def test_measures_every_function():
    counter, sim = open_counter()
    assert counter.measure_frequency() == pytest.approx(1e6)
    assert counter.measure_frequency("B") == pytest.approx(250e3)
    assert counter.measure_period() == pytest.approx(1e-6)
    assert counter.measure_ratio() == pytest.approx(4.0)
    assert counter.measure_time_interval() == pytest.approx(1e-6)
    assert counter.measure_pulse_width() == pytest.approx(0.5e-6)
    assert counter.measure_rise_time() == pytest.approx(0.4e-6)
    assert counter.measure_peaks("A") == {"high": 0.5, "low": -0.5}
    # read-levels mode presets auto trigger off: the knob (0.2 V, as on the bench)
    assert counter.read_trigger_levels() == {"A": 0.2, "B": 0.2}


def test_restarts_before_each_reading_and_sends_a_function_only_when_it_changes():
    counter, sim = open_counter()
    sent = record(sim)
    counter.measure_frequency()
    counter.measure_frequency()
    assert sent == ["RE", "RE"]
    sent.clear()
    counter.measure_period()
    assert sent == ["FN4", "TE", "RE"]


def test_fall_time_leaves_rise_fall_mode_to_change_the_slope():
    counter, sim = open_counter()
    counter.measure_rise_time()
    sent = record(sim)
    counter.measure_fall_time()  # AS1 in rise/fall mode would be error 1.0
    sent = [s for s in sent if s != "TE"]
    assert sent[0] == "FN1" and sent[-3:] == ["AS1", "FN11", "RE"]
    assert sim.slope["A"] == 1
    with pytest.raises(CommandError, match="error 1.0: parameter disallowed"):
        counter._send("AS0")  # what the driver avoids
    assert sim.error == 0  # cleared again


def test_leaving_rise_fall_turns_com_a_off_again():
    counter, sim = open_counter()
    counter.measure_rise_time()  # the counter presets COM A for rise/fall
    assert sim.settings["CO"] == 1
    assert counter.measure_frequency("B") == pytest.approx(250e3)  # not A's 1 MHz
    assert sim.settings["CO"] == 0
    counter.set_common(True)
    counter.measure_rise_time()
    counter.measure_frequency("A")
    assert sim.settings["CO"] == 1  # chosen, so it stays


def test_settings_leave_special_modes_and_trigger_choice_comes_back():
    counter, sim = open_counter()
    counter.read_trigger_levels()  # FN13 presets auto trigger off ...
    assert not sim.auto
    counter.set_auto_trigger(True)  # ... and refuses AU1 (error 1.0 on the bench)
    assert sim.function == 1 and sim.auto
    counter.set_trigger_level("A", 0.5)
    counter.measure_pulse_width()  # pulse width presets auto trigger on
    assert sim.auto
    counter.measure_frequency()  # back to the fixed level
    assert not sim.auto and sim.dacs and sim.levels["A"] == 0.5
    counter.start_totalize()
    counter.stop_totalize()
    counter.set_auto_trigger(True)  # disallowed in totalize: leaves it first
    assert sim.auto and counter.trigger == "auto"


def test_averaging_waits_for_100_gates():
    counter, sim = open_counter()
    counter.set_gate_time(0.1)
    counter.set_100_gate_average(True)
    timeouts = []
    original = sim.set_timeout
    sim.set_timeout = lambda s: (timeouts.append(s), original(s))
    counter.measure_frequency()
    assert timeouts[0] == pytest.approx(100 * (0.1 + 1.0) + 3)  # auto trigger: ~1 s apart
    counter.set_trigger_level("A", 0.0)
    timeouts.clear()
    counter.measure_frequency()
    assert timeouts[0] == pytest.approx(100 * (0.1 + 0.1) + 3)
    sim.max_read_ms = 32000  # an AR488 waits at most 32 s
    counter.set_auto_trigger(True)
    with pytest.raises(CommandError, match="longer than the adapter waits"):
        counter.measure_frequency()


def test_trigger_levels_and_settings():
    counter, sim = open_counter()
    counter.set_trigger_level("A", 1.5)
    counter.set_trigger_level("B", -0.5)
    assert not sim.auto and sim.dacs
    assert counter.read_trigger_levels() == {"A": 1.5, "B": -0.5}
    with pytest.raises(CommandError, match="±5.1 V with the x1 attenuator"):
        counter.set_trigger_level("A", 12.0)
    counter.set_attenuation("A", "10")
    counter.set_trigger_level("A", 12.0)
    counter.use_trigger_knob()
    assert counter.read_trigger_levels() == {"A": 0.2, "B": 0.2}
    counter.set_auto_trigger(True)
    counter.set_coupling("B", "DC")
    counter.set_impedance("A", "50")
    counter.set_filter(True)
    counter.set_common(True)
    counter.set_100_gate_average(True)
    counter.set_trigger_slope("B", "falling")
    assert sim.settings == {"AX": 1, "BA": 0, "AZ": 1, "FI": 1, "CO": 1, "GV": 1}
    assert sim.slope["B"] == 1
    counter.set_gate_time(0.001)
    assert sim.gate == 0.001


def test_totalize():
    counter, sim = open_counter()
    counter.start_totalize()
    assert counter.read_totalize() == 1000
    assert counter.read_totalize() == 2000
    assert counter.stop_totalize() == 2000
    with pytest.raises(CommandError, match="no count is running"):
        counter.read_totalize()
    assert counter.measure_totalize(1.0) == 0  # sim counts only while being read
    assert counter.measure_frequency() == pytest.approx(1e6)  # FN1 is sent again


def test_errors_are_reported_and_cleared():
    counter, sim = open_counter()
    with pytest.raises(CommandError, match="error 4.0: mnemonic not recognizable"):
        counter._send("XX")
    assert sim.error == 0 and counter.get_error() == 0
    with pytest.raises(CommandError, match="error 4.3: data exceeds valid range"):
        counter._send("FN99")
    assert counter.measure_frequency() == pytest.approx(1e6)


def test_no_signal_fails_and_the_next_reading_works():
    counter, sim = open_counter()
    sim.signals["B"] = None
    with pytest.raises(CommandError, match="no reading on channels A and B"):
        counter.measure_ratio()
    assert sim.timeout == counter.base_timeout
    assert counter.measure_frequency() == pytest.approx(1e6)


def test_readings_parse_as_the_counter_sends_them():
    assert READING.match("F    +9.9999221E+05").group(1, 2) == ("F", "+9.9999221E+05")
    assert READING.match("t        +1.854E+03").group(2) == "+1.854E+03"
    assert READING.match("O+9.99999999999E+99").group(1) == "O"
    assert PAIR.match("A   +0.20,B   -2.00").group(2, 4) == ("+0.20", "-2.00")
    sim = HP5334Sim()
    assert sim._number("F", 458341.265, 9) == "F   +4.58341265E+05"  # the manual's example
    assert len(sim._number("F", 458341.265, 9)) == 19
    counter, sim = open_counter()
    sim.reply("O+9.99999999999E+99")
    counter.transport.write = lambda text: None  # keep the queued overflow
    assert math.isnan(counter._measure("FN1", "channel A"))


def test_rejects_another_instrument():
    class Other(HP5334Sim):
        identity = "HP5384A"

    with pytest.raises(CommandError, match="not HP5334A or HP5334B"):
        HP5334(Other()).connect()
