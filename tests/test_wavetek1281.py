# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import math

import pytest

from benchloom.engine import open_device
from benchloom.plugin import CommandError, PluginRegistry
from benchloom.project import DeviceConfig

REGISTRY = PluginRegistry.default()


def open_dmm(resource="sim://", **settings):
    dmm = open_device(REGISTRY, DeviceConfig("dmm", "wavetek.1281", resource, settings))
    sim = dmm.transport
    sent = []
    original = sim.write_raw

    def write_raw(data):
        sent.append(bytes(data).decode().strip())
        original(data)

    sim.write_raw = write_raw
    return dmm, sim, sent


def test_1281_is_listed_under_wavetek():
    spec = REGISTRY.devices["wavetek.1281"]
    assert spec.brand == "Wavetek" and spec.kind == "dmm"


def test_1281_connect_checks_model_and_options():
    dmm, sim, _ = open_dmm()
    assert sim.trigger == "EXT" and dmm.options == {"ac", "current", "ohms"}
    assert dmm.line_frequency == 50
    # the handbook's own *IDN? example names a 1271
    open_dmm("sim://?identity=DATRON INSTRUMENTS%2C1271%2C456789-01.09%2C890144/00.00")
    with pytest.raises(CommandError, match="not as a 1281"):
        open_dmm("sim://?identity=FLUKE%2C8508A%2C1%2C1")


def test_1281_refuses_functions_whose_option_is_missing():
    dmm, sim, sent = open_dmm("sim://?options=0%2C0%2C1%2C0%2C0%2C0%2C0")  # ohms only
    assert dmm.measure_resistance() == pytest.approx(10000.0456)
    with pytest.raises(CommandError, match="needs AC \\(option 10\\)"):
        dmm.measure_ac_voltage()
    with pytest.raises(CommandError, match="current \\(option 30\\)"):
        dmm.measure_dc_current()


def test_1281_function_commands_follow_the_syntax():
    from benchloom_plugin_wavetek.wavetek1281 import RESET_STATE, function_command

    assert function_command("dc_volts", dict(RESET_STATE["dc_volts"], range=10.0)) \
        == "DCV 10,RESL6,FILT_OFF,FAST_OFF"
    assert function_command("ohms", dict(RESET_STATE["ohms"], four_wire=True)) \
        == "OHMS AUTO,RESL6,FILT_OFF,FAST_OFF,FWR,LOI_OFF"
    assert function_command("ac_volts", RESET_STATE["ac_volts"]) \
        == "ACV 1000,RESL6,FILT100HZ,ACCP"  # transfer only with option 12
    assert function_command("hi_ohms", RESET_STATE["hi_ohms"]) \
        == "HI_OHMS 50000000,RESL6,FILT_OFF,TWR"  # clear of the disputed 100000000


def test_1281_measurements():
    dmm, sim, sent = open_dmm()
    assert dmm.measure_dc_voltage() == pytest.approx(10.0000012)
    assert sent[-1] == "X?"  # DCV is active after reset: nothing to set
    assert dmm.measure_ac_voltage() == pytest.approx(1.0000234)
    assert dmm.get_frequency() == pytest.approx(1000.0012)
    assert dmm.measure_resistance_4wire() == pytest.approx(10000.0456)
    assert "FWR" in sim.options
    assert dmm.measure_true_ohms() == pytest.approx(10000.0401)
    assert dmm.measure_hi_ohms() == math.inf  # 250 MΩ on the 100 MΩ range (it reads to 199.99 MΩ)
    dmm.set_range(500e6)
    assert dmm.measure() == pytest.approx(250.00123e6)
    assert dmm.measure_dc_current() == pytest.approx(1.0000012e-3)
    assert dmm.measure_ac_current() == pytest.approx(0.5000023e-3)


def test_1281_ranges_follow_the_handbook_tables():
    dmm, sim, sent = open_dmm()
    dmm.measure_dc_voltage()
    dmm.set_range(1.5)  # 0.2 to 1.99999999 selects the 1 V range
    assert sent[-2] == "DCV 1,RESL6,FILT_OFF,FAST_OFF" and sim.range == 1.0
    assert dmm.measure() == math.inf
    dmm.set_range(15.6789)  # the handbook's example: the 10 V range
    assert sim.range == 10.0
    dmm.measure_resistance()
    with pytest.raises(CommandError, match="no ohms range for 5e"):
        dmm.set_range(5e6)  # "> 2000000 use HI_OHMS"
    dmm.measure_hi_ohms()
    with pytest.raises(CommandError, match="no autorange"):
        dmm.set_autorange()


def test_1281_resolutions_and_options_per_function():
    dmm, sim, _ = open_dmm()
    dmm.measure_dc_voltage()
    dmm.set_resolution(8)
    dmm.set_fast(True)
    assert {"RESL8", "FAST_ON"} <= sim.options
    with pytest.raises(CommandError, match="takes 5, 6, 7, 8"):
        dmm.set_resolution(4)
    dmm.measure_ac_current()
    with pytest.raises(CommandError, match="takes 4, 5"):
        dmm.set_resolution(6)
    with pytest.raises(CommandError, match="no fast mode"):
        dmm.set_fast(True)
    dmm.set_ac_filter(1)
    dmm.set_dc_coupling(True)
    assert {"FILT1HZ", "DCCP"} <= sim.options
    dmm.measure_ac_voltage()
    dmm.set_transfer(False)
    assert "TFER_OFF" in sim.options


def test_1281_inputs_guard_and_delay():
    dmm, sim, _ = open_dmm()
    dmm.measure_dc_voltage()
    dmm.set_input("channel_b")
    assert dmm.measure() == pytest.approx(5.0000012)
    dmm.set_input("ratio")
    assert sim.input == "DIV_B" and dmm.measure() == pytest.approx(10.0000012 / 5.0000012)
    dmm.set_guard("remote")
    assert sim.guard == "REM"
    dmm.set_delay(3)
    assert sim.delay == 3
    dmm.set_auto_delay()
    assert sim.delay == "DFLT"


def test_1281_reading_time_from_the_a_d_modes():
    dmm, _, _ = open_dmm()
    dmm.measure_dc_voltage()
    dmm.set_resolution(8)  # fast off: 16 x 64 = 1024 power line cycles, 5 s delay
    assert dmm._reading_seconds() == pytest.approx((5 + 1024 / 50 * 2) * 1.2 + 2)
    dmm.set_fast(True)  # 4 x 64 = 256 cycles
    assert dmm._reading_seconds() == pytest.approx((5 + 256 / 50 * 2) * 1.2 + 2)


def test_1281_errors_are_read_from_the_queues():
    dmm, sim, _ = open_dmm()
    dmm.write("BOGUS")
    with pytest.raises(CommandError, match="command error"):
        dmm.check_errors()
    dmm.write("DELAY 70000")
    with pytest.raises(CommandError, match="1013: data out of limit"):
        dmm.check_errors()


def test_1281_zero_and_tests():
    dmm, _, _ = open_dmm()
    assert dmm.input_zero() and dmm.confidence_test() and dmm.self_test()
    dmm.measure_ac_current()
    with pytest.raises(CommandError, match="not allowed in AC current"):
        dmm.input_zero()
