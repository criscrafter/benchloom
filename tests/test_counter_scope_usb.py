import math

import pytest

from patchbay.engine import open_device
from patchbay.plugin import CommandError, PluginRegistry
from patchbay.plugins.hantek.dso5000p import (
    LAYOUT_FILE, TIMEBASES, frame, parse_layout, split_frames,
)
from patchbay.project import DeviceConfig
from patchbay.transports import (
    PrologixTransport, TransportError, UsbBulkTransport, UsbTmcTransport, open_transport,
)

REGISTRY = PluginRegistry.default()


def open_sim(device_id, **settings):
    device = open_device(REGISTRY, DeviceConfig("dev", device_id, "sim://", settings))
    return device, device.transport


# --- HP 53131A ------------------------------------------------------------------------
def test_53131a_measures_every_function():
    counter, sim = open_sim("hp.53131a")
    assert counter.idn().startswith("HEWLETT-PACKARD,53131A")
    assert counter.measure_frequency() == pytest.approx(10e6, rel=1e-6)
    assert counter.measure_frequency(2) == pytest.approx(1e3, rel=1e-6)
    assert counter.measure_period() == pytest.approx(1e-7, rel=1e-6)
    assert counter.measure_ratio() == pytest.approx(1e4)
    assert counter.measure_duty_cycle() == pytest.approx(0.5)
    assert counter.measure_peak_to_peak() == pytest.approx(1.0)
    assert counter.measure_totalize(0.5) == pytest.approx(5e6)


def test_53131a_sends_the_gate_time_and_input_settings():
    counter, sim = open_sim("hp.53131a")
    sent = []
    original = sim.write
    sim.write = lambda text: (sent.append(text), original(text))
    counter.set_gate_time(1)
    counter.measure_frequency(2)
    assert sent[:4] == [":CONF:FREQ (@2)", ":FREQ:ARM:STAR:SOUR IMM",
                        ":FREQ:ARM:STOP:SOUR TIM", ":FREQ:ARM:STOP:TIM 1.000"]
    assert ":READ?" in sent
    counter.set_coupling(1, "DC")
    counter.set_impedance(2, "50")
    counter.set_attenuation(1, "10")
    counter.set_trigger_level(1, 12.5)  # allowed with x10 attenuation
    counter.set_trigger_slope(2, "falling")
    assert sim.inputs[1]["COUP"] == "DC" and sim.inputs[1]["LEV"] == 12.5
    assert sim.inputs[2]["IMP"] == 50 and sim.inputs[2]["SLOP"] == "NEG"
    counter.set_attenuation(1, "1")
    with pytest.raises(CommandError, match="-222"):
        counter.set_trigger_level(1, 12.5)  # ±5.125 V at x1


def test_53131a_clears_the_counter_when_no_signal_arrives():
    counter, sim = open_sim("hp.53131a")
    cleared = []
    sim.device_clear = lambda: cleared.append(True)
    with pytest.raises(CommandError, match="no reading on channel 3"):
        counter.measure_frequency(3)  # nothing on the RF input
    assert cleared == [True]
    assert sim.timeout == counter.base_timeout  # the long read timeout is undone


# --- Hantek DSO5000P ------------------------------------------------------------------
def test_hantek_framing():
    data = frame(0x01)
    assert data == bytes([0x53, 2, 0, 1, (0x53 + 2 + 1) & 0xFF])
    buffer = bytearray(b"\x00junk" + frame(0x80, b"PB") + frame(0x81, b"x" * 300)[:50])
    assert split_frames(buffer) == [(0x80, b"PB")]  # noise skipped, partial frame kept
    assert len(buffer) == 50
    broken = bytearray(frame(0x80, b"PB"))
    broken[-1] ^= 1
    assert split_frames(broken) == []


def test_hantek_layout_is_208_bytes():
    fields = parse_layout(LAYOUT_FILE.read_text(encoding="ascii"))
    assert sum(size for _, _, size in fields) == 208
    assert fields[0] == ("VERT-CH1-DISP", 0, 1)
    assert TIMEBASES[0] == 2e-9 and TIMEBASES[15] == pytest.approx(200e-6)
    assert TIMEBASES[31] == pytest.approx(40)


def test_hantek_reads_and_scales_waveforms():
    scope, sim = open_sim("hantek.dso5000p")
    wave = scope.read_waveform(1)
    assert len(wave["volts"]) == 3200
    assert max(wave["volts"]) == pytest.approx(1.0, abs=0.02)  # 1 V peak at 500 mV/div
    assert wave["time"][1] - wave["time"][0] == pytest.approx(200e-6 * 16 / 3200)
    assert scope.measure_vpp(1) == pytest.approx(2.0, abs=0.04)
    assert scope.measure_vrms(1) == pytest.approx(1 / math.sqrt(2), abs=0.03)
    assert scope.measure_frequency() == pytest.approx(1000)
    with pytest.raises(CommandError, match="no waveform for CH2"):
        scope.read_waveform(2)  # channel off


def test_hantek_settings_and_keys():
    scope, sim = open_sim("hantek.dso5000p")
    scope.set_probe(1, "10")
    scope.set_volts_per_div(1, 5)  # 500 mV on the scope x 10 probe
    scope.set_trigger("CH1", "falling")
    scope.set_trigger_level(2.5)  # half a division at 5 V/div
    values = scope.settings_block()
    assert values["VERT-CH1-VB"] == 8 and values["VERT-CH1-PROBE"] == 1
    assert values["TRIG-EDGE-SLOPE"] == 1 and values["TRIG-VPOS"] == 12
    scope.set_timebase(2e-3)
    assert scope.get_timebase() == pytest.approx(2e-3)
    with pytest.raises(CommandError, match="nearest are 0.0008 or 0.002"):
        scope.set_timebase(1e-3)
    scope.stop()
    assert scope.get_trigger_state() == "stop"
    with pytest.raises(CommandError, match="stopped scope"):
        scope.read_waveform(1)
    scope.run()
    assert scope.get_trigger_state() != "stop"


def test_hantek_never_sends_risky_commands():
    scope, sim = open_sim("hantek.dso5000p")
    with pytest.raises(CommandError, match="untested settings: ACQURIE-STORE-DEPTH"):
        scope._change(**{"ACQURIE-STORE-DEPTH": 2})
    with pytest.raises(CommandError, match="untested settings: HORIZ-TB"):
        scope._change(**{"HORIZ-TB": 3})  # stored but not applied by the scope
    scope.set_channel(2, True)
    scope.set_coupling(1, "AC")
    scope.set_timebase(8e-4)
    scope.autoset()
    scope.read_waveform(1)
    assert {marker for marker, _ in sim.requests} == {0x53}  # never the debug protocol
    assert {cmd for _, cmd in sim.requests} <= {0x00, 0x01, 0x02, 0x10, 0x11, 0x13}


# --- transports -----------------------------------------------------------------------
def test_usb_resources_parse():
    with pytest.raises(TransportError, match="03EB:2065 not found"):
        open_transport("usbgpib://")
    tmc = UsbTmcTransport("03eb:2065/GPIB_22_ABC")
    assert (tmc.vid, tmc.pid, tmc.serial) == (0x03EB, 0x2065, "GPIB_22_ABC")
    bulk = UsbBulkTransport("049F:505A")
    assert (bulk.vid, bulk.pid) == (0x049F, 0x505A)
    with pytest.raises(TransportError, match="must look like"):
        UsbTmcTransport("UsbGpib")


class FakeUsbTmc:
    """Stands in for python-usbtmc's Instrument: one reply per read_raw()."""

    def __init__(self, replies):
        self.replies, self.written, self.pulses, self.cleared = list(replies), [], 0, 0

    def read_raw(self):
        if not self.replies:
            raise OSError("[Errno 10060] Operation timed out")
        return self.replies.pop(0)

    def write_raw(self, data):
        self.written.append(data)

    def write(self, text):
        self.written.append(text)

    def read(self):
        return self.read_raw().decode()

    def pulse(self):
        self.pulses += 1

    def clear(self):
        self.cleared += 1


def test_usbtmc_reads_whole_messages_and_talks_to_the_adapter():
    tmc = UsbTmcTransport("03EB:2065")
    tmc._instrument = FakeUsbTmc([b"+1.23456E+0\r\n", b"AB", b"CD\n", b"2.4\n"])
    assert tmc.query("F1") == "+1.23456E+0"  # one message, no termination needed
    assert tmc.read_raw(3) == b"ABC" and tmc.read_until(b"\n") == b"D\n"
    assert tmc.adapter_command("!ver?") == "2.4"
    assert tmc._instrument.pulses == 1  # '!' commands need the indicator pulse first
    with pytest.raises(TimeoutError):
        tmc.read()
    tmc.device_clear()
    assert tmc._instrument.cleared == 1


def test_prologix_device_clear():
    gpib = PrologixTransport("COM99", 23)
    sent = []
    gpib._serial = type("S", (), {"write": lambda self, data: sent.append(data)})()
    gpib.device_clear()
    assert sent == [b"++clr\n"]
