# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import math

import pytest

from benchloom.engine import open_device
from benchloom.plugin import CommandError, PluginRegistry
from benchloom.plugins.hp.hp34970a import _reading, channel_list, parse_channels
from benchloom.project import DeviceConfig

REGISTRY = PluginRegistry.default()


def open_sim(cards=None, dmm=None, **settings):
    query = [f"cards={cards}"] * (cards is not None) + [f"dmm={dmm}"] * (dmm is not None)
    resource = "sim://" + ("?" + "&".join(query) if query else "")
    daq = open_device(REGISTRY, DeviceConfig("daq", "hp.34970a", resource, settings))
    return daq, daq.transport


def test_channel_lists():
    assert parse_channels("101:103,110") == [101, 102, 103, 110]
    assert parse_channels("(@205)") == [205]
    assert channel_list([101, 102]) == "(@101,102)"
    for bad in ("", "1o1", "110:101"):
        with pytest.raises(CommandError):
            parse_channels(bad)


def test_overload_reads_as_infinity():
    assert _reading("+9.90000000E+37") == math.inf
    assert _reading("-9.90000000E+37") == -math.inf
    assert _reading("+1.23400000E+00") == 1.234


def test_finds_the_cards_and_the_multimeter():
    daq, sim = open_sim()
    assert daq.call("list_cards") == (
        "slot 1: 34901A 20-channel multiplexer; slot 2: 34903A 20-channel actuator; "
        "slot 3: 34907A multifunction module; with internal multimeter")
    assert [daq.call("card_model", s) for s in (1, 2, 3)] == ["34901A", "34903A", "34907A"]
    assert daq.identify().startswith("HEWLETT-PACKARD,34970A")


def test_empty_mainframe_as_on_the_tested_unit():
    daq, sim = open_sim(",,")
    assert daq.call("list_cards") == ("slot 1: empty; slot 2: empty; slot 3: empty; "
                                      "with internal multimeter")
    for name, args in (("measure_dc_voltage", (101,)), ("close_channel", (201,)),
                       ("set_dac", (304, 1.0)), ("reset_card", (1,))):
        with pytest.raises(CommandError, match="empty"):
            daq.call(name, *args)


def test_a_multimeter_that_is_switched_off():
    daq, sim = open_sim(dmm="off")
    assert daq.call("list_cards").endswith("with internal multimeter (switched off)")
    with pytest.raises(CommandError, match="switched off"):
        daq.call("measure_dc_voltage", 101)
    daq.call("close_channel", 101)  # switching does not need it
    daq, sim = open_sim(dmm="none")
    assert daq.call("list_cards").endswith("without internal multimeter")
    with pytest.raises(CommandError, match="no internal multimeter"):
        daq.call("scan", "dc_voltage", "101")


def test_measurements_on_a_34901a():
    daq, sim = open_sim()
    assert daq.call("measure_dc_voltage", 105) == pytest.approx(0.5)
    assert daq.call("measure_ac_voltage", 103) == pytest.approx(0.3)
    assert daq.call("measure_resistance", 102) == pytest.approx(1002)
    assert daq.call("measure_resistance", 102, True) == pytest.approx(1001.5)
    assert daq.call("measure_dc_current", 121) == pytest.approx(0.021)
    assert daq.call("measure_frequency", 101) == pytest.approx(1001)
    assert daq.call("measure_period", 101) == pytest.approx(1 / 1001)
    assert daq.call("measure_temperature", 110) == pytest.approx(21.0)
    assert daq.call("measure_temperature", 110, "thermistor", "10000") == pytest.approx(21.0)
    assert daq.call("get_error") == '0,"No error"'


def test_scan_returns_every_channel_in_order():
    daq, sim = open_sim()
    assert daq.call("scan", "dc_voltage", "101:103,110") == pytest.approx([0.1, 0.2, 0.3, 1.0])
    assert daq.call("scan", "temperature", "101,102", "thermocouple", "J") == \
        pytest.approx([20.1, 20.2])
    assert sim.conf[101] == "TC,J"
    assert sim.scan_list == []  # cleared after the readings (ROUT:SCAN (@))


def test_channels_are_checked_against_the_cards():
    daq, sim = open_sim()
    cases = [
        (("measure_dc_current", 105), "only channels 21 and 22 of a 34901A"),
        (("measure_dc_voltage", 121), "cannot measure dc voltage"),
        (("measure_resistance", 112, True), "4-wire uses channels 01 to 10"),
        (("measure_dc_voltage", 201), "34903A 20-channel actuator in slot 2 cannot measure"),
        (("measure_dc_voltage", 125), "has no channel 25"),
        (("close_channel", 304), "34907A multifunction module in slot 3 cannot switch"),
        (("set_dac", 301, 1.0), "DACs are channels 04 and 05"),
        (("read_totalizer", 304), "the totalizer is channel 03"),
        (("measure_temperature", 101, "thermocouple", "X"), "type B, E, J, K"),
        (("measure_temperature", 101, "pyrometer", "K"), "must be one of"),
        (("scan", "dc_voltage", "101:105,201"), "slot 2 cannot measure"),
        (("scan", "voltage", "101"), "must be one of"),
    ]
    for (name, *args), message in cases:
        with pytest.raises(CommandError, match=message):
            daq.call(name, *args)
    assert sim.scan_list == []  # nothing invalid reached the instrument


def test_4_wire_on_a_34908a_is_refused():
    daq, sim = open_sim("34908A,34902A,")
    assert daq.call("measure_dc_voltage", 140) == pytest.approx(4.0)
    with pytest.raises(CommandError, match="this card has no 4-wire"):
        daq.call("measure_resistance", 101, True)
    assert daq.call("measure_resistance", 208, True) == pytest.approx(1007.5)
    with pytest.raises(CommandError, match="channels 01 to 08"):
        daq.call("measure_resistance", 209, True)


def test_relays_dacs_digital_and_totalizer():
    daq, sim = open_sim()
    daq.call("close_channel", 205)
    daq.call("close_channel", 112)  # multiplexer channels switch too
    assert daq.call("is_closed", 205) and not daq.call("is_closed", 206)
    daq.call("open_channel", 205)
    assert not daq.call("is_closed", 205)
    daq.call("close_channel", 207)
    daq.call("open_all", 2)
    assert sim.closed == {112}
    daq.call("set_dac", 304, -2.5)
    assert sim.dac == {304: -2.5}
    assert daq.call("read_digital", 301) == 0xA5
    daq.call("write_digital", 302, 0x0F)
    assert daq.call("read_digital", 302) == 0x0F
    assert daq.call("read_totalizer", 303) == 1000
    assert daq.call("read_totalizer", 303, True) == 2000
    assert sim.totals[303] == 0
    with pytest.raises(CommandError, match="has no relays"):
        daq.call("open_all", 3)


def test_matrix_and_rf_channels():
    daq, sim = open_sim("34904A,34905A,")
    daq.call("close_channel", 132)  # row 3, column 2
    with pytest.raises(CommandError, match="has no channel 19"):
        daq.call("close_channel", 119)  # there is no column 9
    daq.call("close_channel", 224)
    with pytest.raises(CommandError, match="has no channel 15"):
        daq.call("close_channel", 215)
    assert sim.closed == {132, 224}


def test_rf_cards_have_no_open_and_one_channel_per_bank():
    daq, sim = open_sim("34904A,34905A,")
    daq.call("close_channel", 211)
    daq.call("close_channel", 222)
    daq.call("close_channel", 213)  # opens 211, bank 2 keeps 222
    assert sim.closed == {213, 222}
    with pytest.raises(CommandError, match="close another channel of bank 1 .11 to 14."):
        daq.call("open_channel", 213)
    with pytest.raises(CommandError, match="has no OPEN"):
        daq.call("open_all", 2)
    assert sim.closed == {213, 222}
    sim.handle("ROUT:OPEN (@213)")
    assert sim.errors and sim.closed == {213, 222}  # refused by the card too


def test_open_all_on_a_matrix_skips_the_missing_channels():
    daq, sim = open_sim("34904A,,")
    for channel in (111, 132, 148):
        daq.call("close_channel", channel)
    daq.call("open_all", 1)  # ROUT:OPEN (@111:148), with 19, 20, 29 ... in between
    assert sim.closed == set()
    assert daq.call("get_error") == '0,"No error"'


def test_simulated_ranges_follow_the_instrument():
    daq, sim = open_sim("34901A,34901A,")
    sim.handle("CONF:VOLT:DC (@101:220)")  # current channels 121, 122 skipped (p. 181)
    volts = list(range(101, 121)) + list(range(201, 221))
    assert sim.scan_list == volts and not sim.errors
    sim.handle("CONF:VOLT:DC (@101:122)")  # an end that cannot measure volts
    assert sim.errors and sim.scan_list == volts


def test_multiplexers_keep_one_relay_closed_while_scanning():
    daq, sim = open_sim("34901A,34908A,34903A")
    daq.call("close_channel", 101)
    daq.call("close_channel", 102)
    assert sim.closed == {101, 102}  # no scan list yet
    daq.call("measure_dc_voltage", 105)  # clears the scan list after it
    daq.call("close_channel", 103)
    assert sim.closed == {101, 102, 103}
    daq.call("start_monitor", "dc_voltage", 105)  # a monitor keeps (@105) in it
    daq.call("close_channel", 104)
    assert sim.closed == {104}
    daq.call("stop_monitor")
    daq.call("close_channel", 201)
    daq.call("close_channel", 240)
    daq.call("close_channel", 301)
    daq.call("close_channel", 302)
    assert sim.closed == {104, 240, 301, 302}  # the 34908A always one, the actuator many


def test_close_returns_the_cards_to_power_on():
    daq, sim = open_sim()
    daq.call("close_channel", 203)
    daq.call("set_dac", 305, 3.0)
    daq.call("write_digital", 301, 1)
    daq.close()
    assert (sim.closed, sim.dac, sim.digital_out) == (set(), {}, {})
    daq, sim = open_sim(leave_output_on=True)
    daq.call("close_channel", 203)
    daq.close()
    assert sim.closed == {203}


def test_close_after_a_failed_connect_does_nothing():
    from benchloom.plugins.hp.hp34970a import HP34970A

    HP34970A(transport=None).close()  # no cards read yet: must not raise


def test_display_text():
    daq, sim = open_sim()
    daq.call("set_display_text", "BENCHLOOM")
    assert sim.display == "BENCHLOOM"
    daq.call("clear_display_text")
    assert sim.display == ""
    with pytest.raises(CommandError, match="13 characters"):
        daq.call("set_display_text", "FOURTEEN CHARS")
    daq.call("set_display_text", "V=1.234,5.678")  # 13 places: . and , share one
    assert sim.display == "V=1.234,5.678"
    with pytest.raises(CommandError, match="quotes"):
        daq.call("set_display_text", "IT'S")


def test_a_card_the_driver_does_not_know():
    daq, sim = open_sim()
    daq.cards[1] = type(daq.cards[1])("34999A", "unknown module")
    daq.unknown[1] = "34999A"
    with pytest.raises(CommandError, match="which this driver does not know"):
        daq.call("measure_dc_voltage", 101)


def test_range_resolution_and_integration_time():
    daq, sim = open_sim()
    daq.call("measure_dc_voltage", 101, 10)
    assert sim.conf[101] == "10"
    daq.call("measure_dc_voltage", 101, 0, 0.0001)
    assert sim.conf[101] == "AUTO,0.0001"
    daq.call("measure_dc_voltage", 101, 1, 0, 100)
    assert (sim.conf[101], sim.timing[101]) == ("1", {"NPLC": 100})
    daq.call("measure_dc_voltage", 101)  # CONF puts NPLC back to 1 (p. 214)
    assert (sim.conf[101], sim.timing[101]) == ("", {})
    daq.call("measure_resistance", 102, True, 1e4, 0, 10)
    assert (sim.conf[102], sim.timing[102]) == ("10000", {"NPLC": 10})
    daq.call("measure_dc_current", 121, 0.1)
    assert sim.conf[121] == "0.1"
    daq.call("measure_ac_voltage", 103, 100)
    assert sim.conf[103] == "100"
    daq.call("measure_frequency", 104, 1)
    assert sim.timing[104] == {"APER": 1}
    daq.call("measure_temperature", 105, "rtd", "85", 20)
    assert (sim.conf[105], sim.timing[105]) == ("RTD,85", {"NPLC": 20})
    assert daq.call("scan", "dc_voltage", "106:107", "thermocouple", "K", 10, 0, 2) == \
        pytest.approx([0.6, 0.7])
    assert sim.timing[107] == {"NPLC": 2}
    assert daq.call("get_error") == '0,"No error"'


def test_measurement_options_are_checked():
    daq, sim = open_sim()
    cases = [
        (("measure_dc_voltage", 101, 5), "must be one of 0, 0.1, 1.0, 10.0"),
        (("scan", "dc_voltage", "101", "thermocouple", "K", 5), "ranges are 0.1, 1, 10, 100, 300"),
        (("scan", "dc_voltage", "101", "thermocouple", "K", 0, 0, 5), "one of 0, 0.02"),
        (("measure_dc_voltage", 101, 0, 0.001, 10), "not both"),
        (("scan", "dc_current", "121", "thermocouple", "K", 3), "ranges are 0.01, 0.1, 1"),
        (("scan", "ac_voltage", "101", "thermocouple", "K", 0, 0, 10), "not ac voltage"),
        (("scan", "ac_voltage", "101", "thermocouple", "K", 0, 0.001), "not ac voltage"),
        (("scan", "frequency", "101", "thermocouple", "K", 10), "no range to choose"),
        (("scan", "dc_voltage", "101", "thermocouple", "K", 0, 0, 0, 1), "frequency and "
                                                                       "period only"),
        (("scan", "period", "101", "thermocouple", "K", 0, 0, 0, 0.5), "one of 0, 0.01"),
    ]
    for (name, *args), message in cases:
        with pytest.raises(CommandError, match=message):
            daq.call(name, *args)
    assert sim.conf == {}  # nothing reached the instrument


def test_monitor_reads_without_a_new_scan():
    daq, sim = open_sim()
    with pytest.raises(CommandError, match="no monitor is running"):
        daq.call("read_monitor")
    daq.call("start_monitor", "resistance", 103, "thermocouple", "K", 1000, 0, 10)
    assert (sim.monitor, sim.monitor_on, sim.scan_list) == (103, True, [103])
    assert sim.timing[103] == {"NPLC": 10}
    sim.memory = []  # a reading must not come from a scan
    assert daq.call("read_monitor") == pytest.approx(1003)
    assert sim.memory == []
    daq.call("start_monitor", "temperature", 104)  # moves the monitor
    assert daq.call("read_monitor") == pytest.approx(20.4)
    daq.call("measure_dc_voltage", 105)  # a measurement stops it
    assert (sim.monitor_on, sim.scan_list) == (False, [])
    with pytest.raises(CommandError, match="no monitor is running"):
        daq.call("read_monitor")
    with pytest.raises(CommandError, match="cannot measure"):
        daq.call("start_monitor", "dc_voltage", 201)  # an actuator channel
    daq.call("start_monitor", "dc_voltage", 101)
    daq.close()
    assert not sim.monitor_on


def test_reading_times_from_the_manual():
    from benchloom.plugins.hp.hp34970a import reading_seconds

    assert reading_seconds("VOLT:DC") == pytest.approx(0.002 + 1 / 47)
    assert reading_seconds("VOLT:DC", nplc=200) == pytest.approx(0.002 + 200 / 47)
    assert reading_seconds("RES", nplc=10) == pytest.approx(0.2 + 10 / 47)
    assert reading_seconds("VOLT:AC") == 2.0
    assert reading_seconds("FREQ", aperture=1) == pytest.approx(1.3)
