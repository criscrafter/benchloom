# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import pytest

from benchloom.engine import open_device
from benchloom.plugin import CommandError, PluginRegistry
from benchloom.project import DeviceConfig

REGISTRY = PluginRegistry.default()


def open_mts(resource="sim://", **settings):
    mts = open_device(REGISTRY, DeviceConfig("mts", "wavetek.4950", resource, settings))
    sim = mts.transport
    sent = []
    original = sim.write_raw

    def write_raw(data):
        sent.append(bytes(data).decode().strip())
        original(data)

    sim.write_raw = write_raw
    return mts, sim, sent


def test_4950_is_listed_under_wavetek_as_a_transfer_standard():
    spec = REGISTRY.devices["wavetek.4950"]
    assert spec.brand == "Wavetek" and spec.kind == "transfer_standard"
    assert spec.default_name == "mts"


def test_4950_connect_resets_and_reads_the_fitment():
    mts, sim, _ = open_mts(accuracy="low")
    assert sim.trigger == "EXT" and sim.accuracy == "LOW"
    assert mts.options == {"ac", "ohms", "current"}
    with pytest.raises(CommandError, match="must be high or low"):
        open_mts(accuracy="medium")


def test_4950_measures_at_nominal_points():
    mts, sim, sent = open_mts()
    assert mts.dc_voltage(10) == pytest.approx(10.0000012)
    assert "DCV 10,PCENT_100,LCL_GUARD" in sent
    assert sent[-2:] == ["*TRG", "RDG?"]
    assert mts.ac_voltage(1, "1K") == pytest.approx(1.0000234)
    assert "ACV 1,FREQ_1K,PCENT_100,FWR,LCL_GUARD" in sent
    assert mts.get_frequency() == pytest.approx(1000.0012)
    assert mts.resistance(10e3) == pytest.approx(10000.0456)
    assert mts.dc_current(1e-3, remote_guard=True) == pytest.approx(1.0000012e-3)
    assert "DCI 0.001,PCENT_100,REM_GUARD" in sent
    assert mts.ac_current(1e-3, "1K") == pytest.approx(1.0000023e-3)
    assert mts.measure_many(2) == [pytest.approx(1.0000023e-3)] * 2


def test_4950_range_selection_follows_the_handbook():
    from benchloom_plugin_wavetek.wavetek4950 import nominal_range

    assert nominal_range("dc_volts", 0.199999999) == 0.1
    assert nominal_range("dc_volts", 19.5) == 10.0
    assert nominal_range("ac_volts", 0.0015) == 1e-3
    assert nominal_range("ohms", 19.9) == 10.0
    with pytest.raises(CommandError, match="no ohms range for 1"):
        nominal_range("ohms", 1)  # the 10 Ω range starts at 2 Ω
    with pytest.raises(CommandError, match="no dc_amps range"):
        nominal_range("dc_amps", 1e-5)


def test_4950_bands_are_checked_as_the_handbook_says():
    mts, sim, _ = open_mts()
    with pytest.raises(CommandError, match="10 V range only"):
        mts.dc_voltage(1, "high")
    with pytest.raises(CommandError, match="must be one of 'zero', 'full', 'high'"):
        mts.call("dc_voltage", 10, "30%")
    with pytest.raises(CommandError, match="10 Ω range only"):
        mts.resistance(100, "10%")
    with pytest.raises(CommandError, match="not on the 100 MΩ range"):
        mts.resistance(100e6, "high")
    with pytest.raises(CommandError, match="two-wire AC volts"):
        mts.ac_voltage(1, "1K", four_wire=False)


def test_4950_reading_outside_the_band_is_refused_with_the_reason():
    mts, sim, _ = open_mts()
    with pytest.raises(CommandError, match="invalid: above the measurement band"):
        mts.dc_voltage(10, "zero")  # 10 V in the 0 V ± 1 V band
    with pytest.raises(CommandError, match="below the measurement band"):
        mts.dc_voltage(100)  # 10 V where 100 V ± 10 V is expected
    mts.set_band_limits(False)
    assert mts.dc_voltage(100) == pytest.approx(10.0000012)


def test_4950_results_and_parameters():
    mts, sim, _ = open_mts()
    mts.dc_voltage(10)
    assert mts.get_quality() == pytest.approx(0.12)
    assert mts.get_sample_size() == 64
    assert mts.get_warm_up_minutes() == 90
    mts.set_accuracy("low")
    mts.set_corrections("base")
    assert sim.accuracy == "LOW" and sim.corrections == "BASE"
    assert mts._sample_seconds() == pytest.approx(4 * 0.2 * 3 + 5)


def test_4950_sample_time_follows_the_sample_sizes():
    mts, _, _ = open_mts()
    mts.function, mts.range = "dc_volts", 0.1
    assert mts._sample_seconds() == pytest.approx(128 * 0.2 * 3 + 5)  # 100 mV, high
    mts.function, mts.range = "ac_volts", 1.0
    assert mts._sample_seconds() == pytest.approx(8 * 0.2 * 3 + 5 + 10)


def test_4950_input_zero_needs_zero_band_and_high_accuracy():
    mts, sim, _ = open_mts()
    mts.set_band_limits(False)
    mts.dc_voltage(10)
    with pytest.raises(CommandError, match="zero band"):
        mts.input_zero()
    mts.dc_voltage(10, "zero")
    assert mts.input_zero() is True


def test_4950_errors_carry_the_meters_own_text():
    mts, sim, _ = open_mts()
    mts.write("BOGUS")
    with pytest.raises(CommandError, match='100 Unrecognized command'):
        mts.check_errors()
    mts.write("DCV 10")
    with pytest.raises(CommandError, match="1026 Illegal band selection"):
        mts.check_errors()
    assert mts.get_errors() == ""


def test_4950_missing_assembly_is_refused():
    mts, _, _ = open_mts("sim://?options=0%2C1%2C0")  # ohms only
    assert mts.resistance(10e3) == pytest.approx(10000.0456)
    with pytest.raises(CommandError, match="needs the ac assembly"):
        mts.ac_voltage(1)
    with pytest.raises(CommandError, match="needs the current and ac assembly"):
        mts.ac_current(1e-3)
