import pytest

from patchbay.engine import open_device
from patchbay.plugin import CommandError, PluginRegistry
from patchbay.plugins.feeltech.fy6900 import WAVEFORMS, settable_hz
from patchbay.project import DeviceConfig

REGISTRY = PluginRegistry.default()


def open_gen(**settings):
    gen = open_device(REGISTRY, DeviceConfig("gen", "feeltech.fy6900", "sim://", settings))
    return gen, gen.transport


def test_identity_and_fixed_serial_settings():
    gen, sim = open_gen()
    assert gen.idn() == "FY6900-60M V1.8 12345678"
    assert sim.config == {"baud": 115200, "bytesize": 8, "parity": "N", "stopbits": 2}
    assert gen.model == "FY6900" and gen.max_hz == 60e6


def test_reads_skip_the_extra_empty_line():
    """Every value is followed by an empty line; a reply must not shift onto the next."""
    gen, sim = open_gen()
    assert [gen.get_waveform(1), gen.get_frequency(1), gen.get_amplitude(2)] == \
        ["sine", 10000.0, 5.0]


def test_set_signal_writes_the_right_commands():
    gen, sim = open_gen()
    sim.log.clear()
    gen.set_signal(2, "triangle", 1234.5, 3.3, -1.2)
    writes = [line for line in sim.log if line.startswith("W")]
    assert writes == ["WFW06", "WFF00001234.500000", "WFA3.3000", "WFO-1.200"]
    assert gen.get_offset(2) == pytest.approx(-1.2)  # two's complement on the wire


def test_waveform_numbers_differ_per_channel():
    gen, sim = open_gen()
    gen.set_waveform(1, "triangle")
    gen.set_waveform(2, "triangle")
    assert (sim.channels["M"]["W"], sim.channels["F"]["W"]) == (7, 6)
    with pytest.raises(CommandError, match="channel 2 of the FY6900 has no 'adjustable_pulse'"):
        gen.set_waveform(2, "adjustable_pulse")
    assert WAVEFORMS["FY6900"][1][-1] == "arb64" and len(WAVEFORMS["FY6900"][1]) == 100


def test_unsettable_frequencies_go_to_the_nearest_one():
    assert settable_hz(1000.567) == pytest.approx(1000.565535)
    assert settable_hz(1000.0828) == pytest.approx(1000.1)
    assert settable_hz(1000.5625) == 1000.5625  # 62500 µHz below 0.1 Hz: fine
    gen, sim = open_gen()
    gen.set_frequency(1, 1000.567)
    assert gen.get_frequency(1) == pytest.approx(1000.565535)


def test_clamping_is_reported():
    gen, sim = open_gen()
    sim.channels["M"]["A"] = 0
    with pytest.raises(CommandError, match="argument 'vpp' is 25 Vpp, above the maximum"):
        gen.call("set_amplitude", 1, 25)
    sim.max_hz = 30e6  # a generator that clamps lower than the model name says
    with pytest.raises(CommandError, match=r"set the frequency to 3e\+07 Hz instead of 4e\+07"):
        gen.set_frequency(1, 40e6)
    unchecked, sim = open_gen(check_settings=False)
    sim.max_hz = 30e6
    unchecked.set_frequency(1, 40e6)  # no error without the check


def test_outputs_off_at_the_end_unless_left_on():
    gen, sim = open_gen()
    gen.set_output(1, True)
    gen.set_output(2, True)
    assert gen.get_output(1)
    gen.close()
    assert sim.channels["M"]["N"] == 0 and sim.channels["F"]["N"] == 0
    gen, sim = open_gen(leave_output_on=True)
    gen.set_output(1, True)
    gen.close()
    assert sim.channels["M"]["N"] == 255


def test_fy6800_writes_microhertz():
    gen = open_device(REGISTRY, DeviceConfig("gen", "feeltech.fy6900", "sim://?model=FY6800-60M", {}))
    gen.set_frequency(1, 1000.567)  # no firmware fault workaround on the FY6800
    assert "WMF00001000567000" in gen.transport.log
    assert gen.get_frequency(1) == pytest.approx(1000.567)
    with pytest.raises(CommandError, match="no 'rectangle'"):
        gen.set_waveform(1, "rectangle")


def test_unknown_model_is_refused():
    with pytest.raises(CommandError, match="'FY3224S' is not a supported"):
        open_device(REGISTRY, DeviceConfig("gen", "feeltech.fy6900", "sim://?model=FY3224S", {}))


def test_serial_transport_configure():
    from patchbay.transports import SerialTransport

    port = SerialTransport("COM99", baud=9600)
    port.configure(baud=115200, stopbits=2, parity="n")
    assert port.serial_options["baudrate"] == 115200
    assert port.serial_options["stopbits"] == 2 and port.serial_options["parity"] == "N"


# --- modulation, sweep and system functions -----------------------------------------------
def writes(sim):
    return [line for line in sim.log if not line.startswith(("R", "U")) or line[:3] in
            ("USA", "USD", "USN", "ULN", "UBZ", "UUL")]


def test_modulation_starts_with_its_type_and_ends_with_the_output_state():
    gen, sim = open_gen()
    gen.set_output(1, True)
    sim.log.clear()
    gen.set_am_depth(80)
    gen.set_fm_deviation(500)
    gen.set_fsk_frequency(1234.567)  # the frequency fault applies here too
    gen.set_pm_deviation(45.5)
    gen.set_burst_count(4)
    gen.start_modulation("am")
    assert sim.modulating and sim.modulation["F"] == 4 and sim.modulation["M"] == 0
    gen.stop_modulation()
    assert not sim.modulating and gen.get_output(1)  # the output stays on
    assert writes(sim) == ["WPR80.0", "WFM00000500.000000", "WFK00001234.565535", "WPP45.50",
                           "WPN4", "WPM0", "WPF4", "WMN1"]


def test_stop_modulation_keeps_an_output_off():
    gen, sim = open_gen()
    gen.start_modulation("burst", "external_dc")
    assert sim.modulation["M"] == 3
    gen.stop_modulation()
    assert not sim.modulating and sim.channels["M"]["N"] == 0


def test_sweep_formats():
    gen, sim = open_gen()
    gen.set_sweep("offset", -3, 3, 6)
    assert sim.sweep == {"SOB": "2", "SST": "9.000", "SEN": "15.000", "STI": "6", "SMO": "0",
                         "SMD": "0", "SXY": "0"}  # offsets count from -12 V
    gen.set_sweep("frequency", 1000, 3000.4, 4, "up_down", True)
    assert (sim.sweep["SST"], sim.sweep["SEN"], sim.sweep["SMO"], sim.sweep["SMD"]) == \
        ("00001000", "00003000", "1", "2")
    gen.set_sweep("duty_cycle", 20, 80, 4)
    assert sim.sweep["SST"] == "20.0"
    with pytest.raises(CommandError, match="between -12 and 12"):
        gen.set_sweep("offset", -13, 3, 6)
    with pytest.raises(CommandError, match="cannot start or end at 0 Hz"):
        gen.set_sweep("frequency", 0, 1000, 4, "up", True)


def test_a_log_sweep_is_paused_before_it_is_stopped():
    gen, sim = open_gen()
    gen.set_sweep("frequency", 100, 1000, 5, "up", True)
    gen.start_sweep()
    gen.start_sweep()  # already running: the run key is not pressed again
    assert sim.sweeping and sim.log.count("KEY17") == 1
    with pytest.raises(CommandError, match="stop the sweep first"):
        gen.start_modulation("am")
    gen.stop_sweep()
    assert not sim.sweeping and sim.log.count("KEY17") == 2


def test_close_stops_a_running_sweep():
    gen, sim = open_gen()
    gen.set_sweep("frequency", 100, 1000, 5, "up", True)
    gen.start_sweep()
    gen.close()
    assert not sim.sweeping and sim.channels["M"]["N"] == 0


def test_phase_needs_uplink_off():
    gen, sim = open_gen()
    with pytest.raises(CommandError, match="ignores the phase while uplink is on"):
        gen.set_phase(2, 90)
    gen.set_uplink(False)
    gen.set_phase(2, 90)
    assert gen.get_phase(2) == 90 and not gen.get_uplink()


def test_sync_pulse_trapezoid_buzzer_and_memory():
    gen, sim = open_gen()
    gen.set_sync("frequency", True)
    gen.set_frequency(1, 4567)
    assert gen.get_sync("frequency") and gen.get_frequency(2) == 4567
    gen.set_sync("frequency", False)
    assert not gen.get_sync("frequency")
    gen.set_pulse_width(25000)
    assert sim.channels["M"]["S"] == 250000  # read back in tenths of a ns
    gen.set_trapezoid_edge(2, 400)
    assert sim.channels["F"]["H"] == 400
    gen.set_buzzer(False)
    assert sim.system["BZ"] == 0
    gen.save_settings(3)
    gen.set_amplitude(1, 1.5)
    gen.load_settings(3)
    assert gen.get_amplitude(1) == 5.0
    assert "USN03" in sim.log and "ULN03" in sim.log


def test_counter_scales_by_the_gate_time():
    gen, sim = open_gen()
    assert gen.measure_frequency() == 1000
    gen.set_counter_gate(10)
    assert sim.system["CG"] == 1 and gen.measure_frequency() == 1000
    assert gen.measure_period() == pytest.approx(1e-3)
    assert gen.measure_high_time() == pytest.approx(0.25e-3)
    assert gen.measure_low_time() == pytest.approx(0.75e-3)
    assert gen.measure_count() == 4321
    assert gen.measure_duty_cycle() == 25.0
    gen.reset_counter()
    assert "WCZ0" in sim.log
    gen.set_counter_coupling("ac")
    assert sim.system["CC"] == 1
