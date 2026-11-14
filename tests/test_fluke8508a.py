# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import math

import pytest

from benchloom.engine import open_device
from benchloom.plugin import CommandError, PluginRegistry
from benchloom.project import DeviceConfig

REGISTRY = PluginRegistry.default()


def open_dmm(**settings):
    dmm = open_device(REGISTRY, DeviceConfig("dmm", "fluke.8508a", "sim://", settings))
    sim = dmm.transport
    sent = []
    original = sim.write_raw

    def write_raw(data):
        sent.append(bytes(data).decode().strip())
        original(data)

    sim.write_raw = write_raw
    return dmm, sim, sent


def test_8508a_is_listed_under_fluke():
    spec = REGISTRY.devices["fluke.8508a"]
    assert spec.brand == "Fluke" and spec.kind == "dmm" and spec.default_name == "dmm"


def test_8508a_connect_resets_and_triggers_externally():
    dmm, sim, _ = open_dmm()
    assert sim.trigger == "EXT" and sim.function == "DCV"
    from benchloom_plugin_fluke.fluke8508a import Fluke8508A, Fluke8508ASim

    other = Fluke8508ASim()
    other.command = lambda header, args: "FLUKE,8846A,1,1" if header == "*IDN?" else None
    with pytest.raises(CommandError, match="not as an 8508A"):
        Fluke8508A(other).connect()


def test_8508a_function_commands_carry_all_options():
    from benchloom_plugin_fluke.fluke8508a import RESET_STATE, function_command

    assert function_command("dc_volts", dict(RESET_STATE["dc_volts"], range=None,
                                             resolution=8, fast=False)) \
        == "DCV AUTO,RESL8,FILT_OFF,FAST_OFF,TWO_WR"
    assert function_command("ac_volts", RESET_STATE["ac_volts"]) \
        == "ACV 525,RESL6,FILT40HZ,ACCP,TFER_ON,TWO_WR"
    assert function_command("ohms", dict(RESET_STATE["ohms"], four_wire=True)) \
        == "OHMS 9999.5,RESL7,FILT_OFF,FAST_ON,FOUR_WR,LOI_OFF"


def test_8508a_measurements():
    dmm, sim, sent = open_dmm()
    assert dmm.measure_dc_voltage() == pytest.approx(10.00000123)
    assert sent[-1] == "X?"
    assert dmm.measure_ac_voltage() == pytest.approx(1.0000234)
    assert dmm.get_frequency() == pytest.approx(1000.0012)
    assert dmm.measure_resistance() == pytest.approx(10000.0456)
    assert dmm.measure_resistance_4wire() == pytest.approx(10000.0456)
    assert sim.options >= {"FOUR_WR"}
    assert dmm.measure_true_ohms() == pytest.approx(10000.0401)
    assert dmm.measure_hv_ohms() == math.inf  # reset range 20 MΩ, and no autorange
    dmm.set_range(150e6)
    assert dmm.measure() == pytest.approx(100.00123e6)
    assert dmm.measure_dc_current() == pytest.approx(1.0000012e-3)
    assert dmm.measure_ac_current() == pytest.approx(0.5000023e-3)
    assert dmm.measure_temperature("PT100 LAB") == pytest.approx(23.4567)
    with pytest.raises(CommandError, match="1026: probe identity unrecognized"):
        dmm.measure_temperature("NO SUCH PRT")


def test_8508a_ranges_resolution_and_overload():
    dmm, sim, sent = open_dmm()
    dmm.measure_dc_voltage()
    dmm.set_range(2)  # 2 V does not fit the 2 V range (full scale 1.9999 V)
    assert sim.range == pytest.approx(19.999)
    dmm.set_range(1.5)
    assert sent[-2].startswith("DCV 0.99995,") and sim.range == pytest.approx(1.9999)
    assert dmm.measure() == math.inf  # 10 V on the 2 V range
    dmm.set_autorange()
    assert dmm.measure() == pytest.approx(10.00000123)
    with pytest.raises(CommandError, match="largest reads up to 1050 V"):
        dmm.set_range(1100)
    dmm.set_resolution(8)
    assert "RESL8" in sim.options
    dmm.measure_dc_current()
    with pytest.raises(CommandError, match="takes 5, 6, 7 digits"):
        dmm.set_resolution(8)
    dmm.measure_hv_ohms()
    with pytest.raises(CommandError, match="no autorange"):
        dmm.set_autorange()


def test_8508a_options_belong_to_their_functions():
    dmm, sim, _ = open_dmm()
    dmm.measure_ac_voltage()
    dmm.set_ac_filter(10)
    dmm.set_dc_coupling(True)
    dmm.set_transfer(False)
    assert {"FILT10HZ", "DCCP", "TFER_OFF"} <= sim.options
    with pytest.raises(CommandError, match="no on/off filter"):
        dmm.set_filter(True)
    dmm.measure_resistance()
    dmm.set_low_current(True)
    dmm.set_filter(True)
    assert {"LOI_ON", "FILT_ON"} <= sim.options
    with pytest.raises(CommandError, match="belongs to AC"):
        dmm.set_ac_filter(40)


def test_8508a_inputs_guard_and_delay():
    dmm, sim, _ = open_dmm()
    dmm.measure_dc_voltage()
    dmm.set_input("rear")
    assert dmm.measure() == pytest.approx(5.0000012)
    dmm.set_input("ratio")
    assert sim.input == "DIV_REAR" and dmm.measure() == pytest.approx(10.00000123 / 5.0000012)
    dmm.set_guard("external")
    assert sim.guard == "EXT"
    dmm.set_delay(2.5)
    assert sim.delay == 2.5 and dmm._reading_seconds() > 2 * 2.5
    dmm.set_auto_delay()
    assert sim.delay == "DFLT"
    with pytest.raises(CommandError, match="maximum"):
        dmm.call("set_delay", 70000)


def test_8508a_reading_time_follows_the_manual_tables():
    dmm, _, _ = open_dmm()
    dmm.measure_dc_voltage()
    dmm.set_resolution(8)
    dmm.set_fast(False)
    # 8½ digits, normal: 1/25 readings per second and a 5 s default delay at 1 kV
    assert dmm._reading_seconds() == pytest.approx((5 + 25) * 1.2 + 2)
    dmm.measure_ac_voltage()
    dmm.set_ac_filter(1)
    # 1 Hz filter, 6½ digits: 1/50 readings per second and a 25 s delay
    assert dmm._reading_seconds() == pytest.approx((25 + 50) * 1.2 + 2)
    dmm.set_fast_gate(False)  # the 1 s frequency gate adds a second
    assert dmm._reading_seconds() == pytest.approx((25 + 50 + 1) * 1.2 + 2)


def test_8508a_errors_are_read_from_the_queues():
    dmm, sim, _ = open_dmm()
    dmm.write("BOGUS")
    with pytest.raises(CommandError, match="command error"):
        dmm.check_errors()
    dmm.write("DELAY 99999")
    with pytest.raises(CommandError, match="1013: data out of limit"):
        dmm.check_errors()
    dmm.check_errors()  # cleared by reading


def test_8508a_zero_and_self_test():
    dmm, _, _ = open_dmm()
    assert dmm.input_zero() is True
    assert dmm.self_test() is True


def test_8508a_no_reading_aborts_and_reports():
    dmm, sim, _ = open_dmm()
    cleared = []
    sim.device_clear = lambda: cleared.append(True)
    dmm.measure_dc_voltage()
    dmm.set_resolution(5)
    sim.command = lambda header, args: None  # X? goes unanswered
    with pytest.raises(CommandError, match="no reading from the 8508A"):
        dmm.measure()
    assert cleared
