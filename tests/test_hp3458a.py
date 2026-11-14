# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import math

import pytest

from benchloom.engine import open_device
from benchloom.plugin import CommandError, PluginRegistry
from benchloom.project import DeviceConfig

REGISTRY = PluginRegistry.default()


def open_dmm(resource="sim://", **settings):
    dmm = open_device(REGISTRY, DeviceConfig("dmm", "hp.3458a", resource, settings))
    sim = dmm.transport
    sent = []
    original = sim.write_raw

    def write_raw(data):
        sent.append(bytes(data).decode().strip())
        original(data)

    sim.write_raw = write_raw
    return dmm, sim, sent


def test_3458a_is_listed_with_hp_agilent_keysight():
    spec = REGISTRY.devices["hp.3458a"]
    assert spec.brand == "Keysight" and spec.kind == "dmm"
    assert spec.maker == REGISTRY.devices["hp.3478a"].maker
    assert spec.default_name == "dmm"


def test_3458a_connect_sets_a_known_state():
    dmm, sim, _ = open_dmm(nplc=100, digits=7)
    assert sim.state["TARM"] == "HOLD" and sim.state["TRIG"] == "AUTO"
    assert sim.state["END"] == "ALWAYS" and sim.state["OFORMAT"] == "ASCII"
    assert sim.nplc == 100 and sim.ndig == 7
    assert dmm.line_frequency == pytest.approx(50)
    assert dmm.identify() == "Keysight 3458A, firmware 9,2"


def test_3458a_refuses_another_instrument():
    from benchloom_plugin_hp.hp3458a import HP3458A, HP3458ASim

    sim = HP3458ASim()
    sim.command = lambda header, args: "HP3478A" if header == "ID?" else None
    with pytest.raises(CommandError, match="not as a 3458A"):
        HP3458A(sim).connect()


def test_3458a_connect_clears_a_waiting_reading_and_switches_eoi_on_first():
    from benchloom_plugin_hp.hp3458a import HP3458A, HP3458ASim

    sim = HP3458ASim()  # just switched on: measuring, a reading waits to be read
    HP3458A(sim).connect()
    assert sim.replies_without_eoi == []  # END ALWAYS went out before the first query

    sim = HP3458ASim()
    sim.device_clear = sim.clear_input = lambda: None  # uncleared, the reading answers ID?
    with pytest.raises(CommandError, match="not as a 3458A"):
        HP3458A(sim).connect()


def test_3458a_power_on_state_needs_end_always():
    from benchloom_plugin_hp.hp3458a import HP3458ASim

    sim = HP3458ASim()
    assert sim.state["END"] == "OFF"  # the manual's power-on state
    sim.handle("PRESET NORM")
    assert sim.state["END"] == "OFF" and sim.nplc == 1 and sim.ndig == 6


def test_3458a_measurements():
    dmm, sim, sent = open_dmm()
    assert dmm.measure_dc_voltage() == pytest.approx(10.0000123)
    assert sent[-1] == "TARM SGL"
    assert dmm.measure_ac_voltage() == pytest.approx(1.0000456)
    assert "FUNC ACV,AUTO" in sent
    assert dmm.measure_acdc_voltage() == pytest.approx(1.0000789)
    assert dmm.measure_resistance() == pytest.approx(10000.212)
    assert dmm.measure_resistance_4wire() == pytest.approx(9999.9876)
    assert dmm.measure_dc_current() == pytest.approx(1.00000234e-3)
    assert dmm.measure_ac_current() == pytest.approx(0.500012e-3)
    assert dmm.measure_acdc_current() == pytest.approx(0.500034e-3)
    assert dmm.measure_frequency() == pytest.approx(1000.00012)
    assert dmm.measure_period() == pytest.approx(1e-3, rel=1e-6)
    sent.clear()
    dmm.measure_period()  # same function: nothing to set again
    assert sent == ["TARM SGL"]


def test_3458a_ranges_and_overload():
    dmm, sim, sent = open_dmm()
    dmm.set_function("dc_volts")
    dmm.set_range(5)
    assert "FUNC DCV,10" in sent and sim.range == 10
    assert dmm.measure() == pytest.approx(10.0000123)
    dmm.set_range(1)
    assert dmm.measure() == math.inf  # 10 V on the 1 V range
    dmm.set_range(1.1)  # a range reads up to 120 %: 1.1 V is on the 1 V range
    assert sent[-2] == "FUNC DCV,1" and sim.range == 1
    dmm.set_range(1050)
    assert sim.range == 1000
    with pytest.raises(CommandError, match="reads up to 1050 V"):
        dmm.set_range(1100)
    dmm.set_autorange()
    assert sent[-2] == "FUNC DCV,AUTO" and sim.range is None
    with pytest.raises(CommandError, match="no dc_volts range holds 2000"):
        dmm.set_range(2000)
    dmm.set_function("dc_amps")
    dmm.set_range(50e-9)
    assert "FUNC DCI,1E-07" in sent and sim.range == pytest.approx(100e-9)


def test_3458a_settings():
    dmm, sim, sent = open_dmm()
    dmm.set_nplc(100)
    dmm.set_digits(8)
    dmm.set_autozero(False)
    dmm.set_offset_compensation(True)
    dmm.set_fixed_impedance(True)
    dmm.set_ac_bandwidth(10)
    dmm.set_ac_method("sync")
    assert sim.nplc == 100 and sim.ndig == 8
    assert sim.state["AZERO"] == "OFF" and sim.state["OCOMP"] == "ON"
    assert sim.state["FIXEDZ"] == "ON" and sim.state["SETACV"] == "SYNC"
    assert "ACBAND 10" in sent
    with pytest.raises(CommandError, match="maximum"):
        dmm.call("set_nplc", 2000)
    with pytest.raises(CommandError):
        dmm.call("set_digits", 9)


def test_3458a_reading_time_follows_the_settings():
    dmm, _, _ = open_dmm()
    dmm.set_nplc(100)
    slow = dmm._seconds_per_reading()
    assert slow > 4  # 100 cycles at 50 Hz, twice with autozero
    dmm.set_autozero(False)
    assert dmm._seconds_per_reading() < slow


def test_3458a_reading_time_counts_offset_compensation_and_delay():
    dmm, _, _ = open_dmm()  # 50 Hz, autozero on
    dmm.set_function("ohms_4wire")
    dmm.set_range(1000)
    dmm.set_offset_compensation(True)
    dmm.set_nplc(10)
    dmm.set_delay(0)
    # four phases of 0.2 s: 0.8 s, as 3458A users measure; then 20 % and 1 s of margin
    assert dmm._seconds_per_reading() == pytest.approx(0.8 * 1.2 + 1)
    dmm.set_nplc(100)
    dmm.set_delay(2)
    # ten parts of NPLC 10, two delays each: 8 s + 40 s = the 48 s reported on EEVblog
    assert dmm._seconds_per_reading() == pytest.approx(48 * 1.2 + 1)


def test_3458a_asks_the_delay_once_per_change_and_before_the_trigger():
    dmm, sim, sent = open_dmm()
    dmm.measure()
    dmm.measure()
    assert sent.count("DELAY?") == 1
    assert sent.index("DELAY?") < sent.index("TARM SGL")
    dmm.set_range(10)
    sent.clear()
    dmm.measure()
    assert sent == ["DELAY?", "TARM SGL"]  # the range changes the automatic delay
    dmm.set_delay(1.5)
    assert sim.delay == 1.5 and dmm._current_delay() == 1.5
    dmm.set_auto_delay()
    assert sim.delay is None and dmm._current_delay() == pytest.approx(sim.AUTO_DELAY)
    with pytest.raises(CommandError, match="maximum"):
        dmm.call("set_delay", 7000)


def test_3458a_series_of_readings():
    dmm, sim, sent = open_dmm()
    readings = dmm.measure_many(5)
    assert readings == [pytest.approx(10.0000123)] * 5
    assert "NRDGS 5,AUTO" in sent and sent[-1] == "NRDGS 1,AUTO"
    assert sim.nrdgs == 1
    assert dmm.measure() == pytest.approx(10.0000123)


def test_3458a_errors_fail_the_command():
    dmm, sim, _ = open_dmm()
    dmm.write("BOGUS 1")
    with pytest.raises(CommandError, match="syntax error"):
        dmm.check_errors()
    with pytest.raises(CommandError, match="parameter out of range"):
        dmm.set_ac_bandwidth(5)  # passes the driver's check, so the meter's is tested
        dmm.write("ACBAND 0.1")
        dmm.check_errors()
    dmm.write("NPLC 5000")
    assert dmm.get_errors() == "parameter out of range"
    assert dmm.get_errors() == ""


def test_3458a_error_register_bits():
    from benchloom_plugin_hp.hp3458a import decode_errors

    assert decode_errors(0) == ""
    assert decode_errors(16 + 512) == "command not allowed over the bus, out of calibration"
    assert decode_errors(16384) == "system error"


def test_3458a_errors_can_go_unchecked():
    dmm, sim, sent = open_dmm(check_errors=False)
    dmm.set_nplc(1)
    assert "ERR?" not in sent


def test_3458a_no_reading_aborts_and_reports():
    dmm, sim, _ = open_dmm()
    cleared = []
    sim.device_clear = lambda: cleared.append(True)
    sim.state["TRIG"] = "HOLD"  # the meter never takes the reading
    with pytest.raises(CommandError, match="no reading from the 3458A"):
        dmm.measure()
    assert cleared


def test_3458a_status_display_and_autocal():
    dmm, sim, sent = open_dmm()
    assert dmm.get_temperature() == pytest.approx(36.2)
    assert dmm.get_terminals() == "front"
    dmm.display_text('Say "hi"; 8½ digits')
    assert sim.message == "Say hi; 8 digits"
    dmm.display_normal()
    assert sim.message is None
    dmm.autocal("dcv")
    # the input buffer frees the bus during the autocal; ERR? is answered when it ends
    assert sent[-3:] == ["INBUF ON", "ACAL DCV;ERR?", "INBUF OFF"]
    assert sim.state["INBUF"] == "OFF"
    assert dmm.query("ID?") == "Keysight 3458A"


def test_3458a_close_lets_the_meter_run_on():
    dmm, sim, _ = open_dmm()
    dmm.close()
    assert sim.state["TARM"] == "AUTO"
