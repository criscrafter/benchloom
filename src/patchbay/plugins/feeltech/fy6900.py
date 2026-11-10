"""Feeltech (FeelElec) FY6900 and FY6800 two-channel function generators.

The generators speak a line protocol of their own over USB serial, fixed at
115200 baud, 8 data bits, no parity, 2 stop bits:

- ``WM<x><value>`` sets parameter x of channel 1, ``WF<x><value>`` of channel 2.
  The generator answers every write with an empty line, after about 150 ms.
- ``RM<x>`` / ``RF<x>`` read it back. A read is answered with the value and then
  an extra empty line. Unknown commands get no answer at all.
- ``UMO`` gives the model ("FY6900-60M"), ``UVE`` the firmware, ``UID`` the serial
  number.

Parameters: W waveform number, F frequency, A amplitude, O offset, D duty cycle,
P phase, N output on/off, H trapezoid edge; ``WMS``/``RSS`` the pulse width.
Modulation: ``WPF`` type (starts it), ``WPM`` source, ``WPN`` burst count, ``WPR``
AM depth, ``WPP`` PM deviation, ``WFM`` FM deviation, ``WFK`` FSK hop frequency.
Sweep: ``SOB`` object, ``SST``/``SEN`` start/end, ``STI`` time, ``SMO`` lin/log,
``SMD`` direction, ``SXY`` source, ``KEY17`` the front panel's run key. Counter:
``RCF``, ``RCT``, ``RC+``, ``RC-``, ``RCC``, ``WCG``, ``WCZ``. System: ``USA``/``USD``/
``RSA`` sync, ``USN``/``ULN`` memory, ``UBZ`` buzzer, ``UUL`` uplink.

Sources: the official FY6600 protocol (rev 1.5), fygen (github.com/mattwach/fygen,
also the waveform numbers), fy6900commander (github.com/katzenjens/fy6900commander)
and pyfy6900 (github.com/tspspi/pyfy6900), checked on a real FY6900-60M (firmware
V1.8) with an oscilloscope on both outputs:

- The frequency is written and read in Hz with six decimals. A 14-digit µHz
  value, which some FY6900 code uses, is taken as Hz and clamped to 60 MHz.
- Readings are scaled: amplitude in 0.1 mV, offset in mV as an unsigned 32-bit
  number (two's complement for negative offsets), duty cycle in 0.001 %, phase
  in 0.001°, AM depth in 0.1 %, PM deviation in 0.01°, pulse width in 0.1 ns.
- The firmware keeps the part of a frequency below 0.1 Hz in 16 bits: when it
  is 65536 µHz or more (the last five decimals between 0.065536 and 0.099999 Hz),
  65536 µHz is lost, and the output is that much too low (measured, not only the
  readback). Such frequencies cannot be set; the driver sets the nearest one
  that can, at most 17 mHz away. The FSK hop frequency has the same fault.
- Out-of-range values are clamped without an error (amplitude to 24 V, offset to
  12 V, duty cycle to 0.001-99.999 %), and a negative frequency becomes 60 MHz.
  Any waveform number up to 99 is accepted. So the driver checks its arguments
  and reads every setting back. The offset DAC has 12 bits and the duty cycle
  and phase can be one count off, so those checks allow 1 mV and one count.
- The duty cycle applies to rectangle (and pulse) waveforms; square stays at 50 %.
- With uplink on (it was, on the tested unit) the generator ignores the phase
  setting. With uplink off, a phase of 90° on channel 2 put it 86° ahead of
  channel 1 (about 4° of that is the scope).
- Writing a modulation type starts modulation of channel 1; writing the output
  state again ends it. AM, FM, PM, FSK, ASK, PSK and burst were seen on the scope
  with channel 2 as the modulating signal; the FM deviation is the total swing
  (500 Hz around 1 kHz: 750 to 1250 Hz).
- ``SBE1`` (the documented sweep start) swept the wrong range and ``SBE0`` only
  paused; the run key (``KEY17``) sweeps as set. Writing the output state ends a
  linear sweep, but a log sweep ignores it until paused with the run key, so the
  driver pauses first. Sweep settings cannot be read back.
- The counter on EXT IN (channel 2 wired to it): at a 1 s gate the frequency
  read 777, 98765, 1234567, 20000001 and 60000003 Hz for those signals, about
  2 s after a change, in whole Hz. The period, high and low time and duty cycle
  are exact at low frequencies (12.345 Hz: 81004456 ns) and resolve about 8 ns.
  ``WCC0`` is DC coupling and ``WCC1`` AC (as the FY6600 document says; fygen has
  them swapped). DC coupling switches at a positive level: it counted 0-5 V and
  2-4 V signals but not a 2 Vpp sine around 0 V. With AC coupling only the
  frequency of a 0-5 V rectangle was read; duty cycle and count read 0. The
  pulse count starts 1 to 2 s after ``WCZ0``; reading the frequency in between
  once stopped it. After a slow signal, a new one can take several seconds to
  show. ``WCP0``, documented as "pause", answers with the count.
- Uploading an arbitrary waveform (``DDS_WAVEnn``, as in fygen, pyfy6900 and
  fy6900commander) was acknowledged ("W", then "HN") but did not change what the
  slot plays, with either byte order, so it is not offered.

Channel 2 has no adjustable pulse, so its waveform numbers from DC on are one
lower. The FY6800 lacks rectangle and trapezoid, and takes the frequency in µHz;
its support is written from fygen and has not been tried on hardware.
"""

from __future__ import annotations

import re

from patchbay import CommandError, Device, Param, command
from patchbay.transports import SimTransport

CHANNELS = (1, 2)
PREFIX = {1: "M", 2: "F"}  # WM.. / RM.. for channel 1, WF.. / RF.. for channel 2

# Waveforms of channel 1 of the FY6900, in order of their numbers.
_FY6900_CH1 = [
    "sine", "square", "rectangle", "trapezoid", "cmos", "adjustable_pulse", "dc",
    "triangle", "ramp", "negative_ramp", "stair_triangle", "stair", "negative_stair",
    "exponential", "negative_exponential", "falling_exponential",
    "negative_falling_exponential", "logarithmic", "negative_logarithmic",
    "falling_logarithmic", "negative_falling_logarithmic", "full_wave",
    "negative_full_wave", "half_wave", "negative_half_wave", "lorentz", "multitone",
    "noise", "ecg", "trapezoid_pulse", "sinc", "impulse", "gaussian_noise", "am", "fm",
    "chirp",
] + [f"arb{n}" for n in range(1, 65)]


def _without(names: list[str], *removed: str) -> list[str]:
    return [name for name in names if name not in removed]


WAVEFORMS = {
    "FY6900": {1: _FY6900_CH1, 2: _without(_FY6900_CH1, "adjustable_pulse")},
    "FY6800": {1: _without(_FY6900_CH1, "rectangle", "trapezoid"),
               2: _without(_FY6900_CH1, "rectangle", "trapezoid", "adjustable_pulse")},
}
ALL_WAVEFORMS = tuple(_FY6900_CH1)

MODULATIONS = {"fsk": 0, "ask": 1, "psk": 2, "burst": 3, "am": 4, "fm": 5, "pm": 6}
MODULATION_SOURCES = {"ch2": 0, "external_ac": 1, "external_dc": 3}
SWEEP_OBJECTS = {"frequency": 0, "amplitude": 1, "offset": 2, "duty_cycle": 3}
SWEEP_DIRECTIONS = {"up": 0, "down": 1, "up_down": 2}
SYNC_ITEMS = {"waveform": 0, "frequency": 1, "amplitude": 2, "offset": 3, "duty_cycle": 4}
COUNTER_GATES = {1: 0, 10: 1, 100: 2}  # seconds -> WCG code
COUNTER_COUPLINGS = {"dc": 0, "ac": 1}
RUN_KEY = "KEY17"

MAX_VPP = 24.0
MAX_OFFSET = 12.0
MIN_DUTY, MAX_DUTY = 0.001, 99.999
DEFAULT_MAX_HZ = 60e6
ON = 255  # what the generator reads back for "on"

CHANNEL = Param(min=1, max=2, doc="1 or 2")
HERTZ = Param(unit="Hz", min=0, max=DEFAULT_MAX_HZ)


def settable_hz(hertz: float) -> float:
    """The nearest frequency the FY6900 can be set to (see the module notes)."""
    micro = round(hertz * 1e6)
    below = micro % 100_000  # the part under 0.1 Hz, in µHz
    if below >= 65536:
        micro += (65535 - below) if below - 65535 <= 100_000 - below else (100_000 - below)
    return micro / 1e6


def _signed32(value: int) -> int:
    return value - (1 << 32) if value >= 1 << 31 else value


class FY6900(Device):
    """Driver for the FY6900, and (untested) the FY6800."""

    def connect(self) -> None:
        configure = getattr(self.transport, "configure", None)
        if configure is not None:  # the serial settings are fixed on the generator
            configure(baud=115200, bytesize=8, parity="N", stopbits=2)
        self.transport.clear_input()
        model = self._ask("UMO")
        match = re.match(r"(FY\d{4})(?:-(\d+)M)?", model)
        if match is None or match.group(1) not in WAVEFORMS:
            raise CommandError(f"'{model}' is not a supported Feeltech generator "
                               f"(supported: {', '.join(WAVEFORMS)})")
        self.model = match.group(1)
        self.max_hz = float(match.group(2)) * 1e6 if match.group(2) else DEFAULT_MAX_HZ
        self.check = bool(self.settings.get("check_settings", True))
        self.sweeping = False

    def close(self) -> None:
        if self.sweeping:
            self.stop_sweep()
        if not self.leave_output_on():
            for channel in CHANNELS:
                self._set(channel, "N", "0")

    # --- protocol -----------------------------------------------------------
    def _ask(self, text: str) -> str:
        self.transport.clear_input()
        self.transport.write(text)
        reply = self.transport.read().strip()
        try:  # the empty line that follows every value
            self.transport.read()
        except TimeoutError:
            pass
        if not reply:
            raise CommandError(f"the generator gave no value for {text}")
        return reply

    def _write(self, text: str) -> None:
        self.transport.clear_input()
        self.transport.write(text)
        ack = self.transport.read()
        if ack.strip():
            raise CommandError(f"unexpected reply '{ack.strip()}' to {text}")

    def _set(self, channel: int, key: str, value: str) -> None:
        self._write(f"W{PREFIX[channel]}{key}{value}")

    def _get(self, channel: int, key: str) -> str:
        return self._ask(f"R{PREFIX[channel]}{key}")

    def _channel(self, channel: int) -> int:
        if channel not in CHANNELS:
            raise CommandError(f"channel must be 1 or 2, not {channel}")
        return channel

    def _verify(self, what: str, wanted: float, actual: float, tolerance: float,
                unit: str) -> None:
        if self.check and abs(actual - wanted) > tolerance:
            raise CommandError(f"the generator set the {what} to {actual:g} {unit} instead "
                               f"of {wanted:g} {unit} (outside its limits)")

    def _verify_flag(self, what: str, wanted: bool, reply: str) -> None:
        if self.check and (int(reply) != 0) != wanted:
            raise CommandError(f"the generator did not switch {what} {'on' if wanted else 'off'}")

    def _frequency_text(self, hertz: float) -> tuple[str, float]:
        """What to send for a frequency, and the frequency that will result."""
        if self.model == "FY6800":
            return f"{round(hertz * 1e6):014d}", round(hertz, 6)  # µHz
        hertz = settable_hz(hertz)
        return f"{hertz:015.6f}", hertz

    @staticmethod
    def _hz(reply: str) -> float:
        return float(reply) if "." in reply else int(reply) / 1e6

    # --- identity -----------------------------------------------------------
    @command("Model, firmware version and serial number")
    def idn(self) -> str:
        return f"{self._ask('UMO')} {self._ask('UVE')} {self._ask('UID')}"

    # --- signal -------------------------------------------------------------
    @command("Set the waveform: sine, square, triangle, ramp, dc, … (see the plugin's "
             "list)", params={"channel": CHANNEL,
                              "waveform": Param(choices=ALL_WAVEFORMS)})
    def set_waveform(self, channel: int, waveform: str) -> None:
        names = WAVEFORMS[self.model][self._channel(channel)]
        if waveform not in names:
            raise CommandError(f"channel {channel} of the {self.model} has no "
                               f"'{waveform}' waveform")
        number = names.index(waveform)
        self._set(channel, "W", f"{number:02d}")
        actual = int(self._get(channel, "W"))
        if self.check and actual != number:
            raise CommandError(f"the generator chose waveform {actual} instead of {number}")

    @command("The waveform of a channel", params={"channel": CHANNEL})
    def get_waveform(self, channel: int) -> str:
        names = WAVEFORMS[self.model][self._channel(channel)]
        number = int(self._get(channel, "W"))
        return names[number] if number < len(names) else str(number)

    @command("Set the frequency, to 1 µHz. The FY6900 cannot produce frequencies whose "
             "decimals below 0.1 Hz are 0.065536 to 0.099999 (a firmware fault); for "
             "those the nearest it can produce is set, at most 17 mHz away",
             params={"channel": CHANNEL, "hertz": HERTZ})
    def set_frequency(self, channel: int, hertz: float) -> None:
        self._channel(channel)
        if hertz > self.max_hz:
            raise CommandError(f"the {self.model} goes up to {self.max_hz / 1e6:g} MHz")
        text, expected = self._frequency_text(hertz)
        self._set(channel, "F", text)
        self._verify("frequency", expected, self.get_frequency(channel), 2.5e-6, "Hz")

    @command("The frequency of a channel", unit="Hz", params={"channel": CHANNEL})
    def get_frequency(self, channel: int) -> float:
        return self._hz(self._get(self._channel(channel), "F"))

    @command("Set the amplitude (peak to peak). Up to 24 Vpp; less at high frequencies",
             params={"channel": CHANNEL, "vpp": Param(unit="Vpp", min=0, max=MAX_VPP)})
    def set_amplitude(self, channel: int, vpp: float) -> None:
        self._set(self._channel(channel), "A", f"{vpp:.4f}")
        self._verify("amplitude", round(vpp, 4), self.get_amplitude(channel), 0.00005, "Vpp")

    @command("The amplitude of a channel (peak to peak)", unit="Vpp",
             params={"channel": CHANNEL})
    def get_amplitude(self, channel: int) -> float:
        return int(self._get(self._channel(channel), "A")) / 10000

    @command("Set the DC offset", params={"channel": CHANNEL,
                                          "volts": Param(unit="V", min=-MAX_OFFSET,
                                                         max=MAX_OFFSET)})
    def set_offset(self, channel: int, volts: float) -> None:
        self._set(self._channel(channel), "O", f"{volts:.3f}")
        self._verify("offset", round(volts, 3), self.get_offset(channel), 0.0015, "V")

    @command("The DC offset of a channel", unit="V", params={"channel": CHANNEL})
    def get_offset(self, channel: int) -> float:
        return _signed32(int(self._get(self._channel(channel), "O"))) / 1000

    @command("Set the duty cycle of the rectangle and pulse waveforms (square stays at "
             "50 %)",
             params={"channel": CHANNEL,
                     "percent": Param(unit="%", min=MIN_DUTY, max=MAX_DUTY)})
    def set_duty_cycle(self, channel: int, percent: float) -> None:
        self._set(self._channel(channel), "D", f"{percent:.3f}")
        self._verify("duty cycle", round(percent, 3), self.get_duty_cycle(channel),
                     0.0015, "%")

    @command("The duty cycle of a channel", unit="%", params={"channel": CHANNEL})
    def get_duty_cycle(self, channel: int) -> float:
        return int(self._get(self._channel(channel), "D")) / 1000

    @command("Set the phase. Channel 2 leads channel 1 by the difference of their "
             "phases. Needs uplink off (set_uplink), else the generator ignores it",
             params={"channel": CHANNEL, "degrees": Param(unit="°", min=0, max=360)})
    def set_phase(self, channel: int, degrees: float) -> None:
        self._channel(channel)
        if self.get_uplink():
            raise CommandError("the generator ignores the phase while uplink is on; "
                               "switch it off with set_uplink(false)")
        degrees = degrees % 360
        self._set(channel, "P", f"{degrees:.3f}")
        self._verify("phase", round(degrees, 3), self.get_phase(channel), 0.0015, "°")

    @command("The phase of a channel", unit="°", params={"channel": CHANNEL})
    def get_phase(self, channel: int) -> float:
        return int(self._get(self._channel(channel), "P")) / 1000

    @command("Switch a channel's output on or off. On channel 1 this also ends "
             "modulation and a linear sweep", params={"channel": CHANNEL})
    def set_output(self, channel: int, on: bool) -> None:
        self._set(self._channel(channel), "N", "1" if on else "0")
        actual = self.get_output(channel)
        if self.check and actual != on:
            raise CommandError(f"the output of channel {channel} did not switch "
                               f"{'on' if on else 'off'}")

    @command("Whether a channel's output is on", params={"channel": CHANNEL})
    def get_output(self, channel: int) -> bool:
        return int(self._get(self._channel(channel), "N")) != 0

    @command("Set waveform, frequency, amplitude and offset of a channel in one go",
             params={"channel": CHANNEL, "waveform": Param(choices=ALL_WAVEFORMS),
                     "hertz": HERTZ, "vpp": Param(unit="Vpp", min=0, max=MAX_VPP),
                     "offset": Param(unit="V", min=-MAX_OFFSET, max=MAX_OFFSET)})
    def set_signal(self, channel: int, waveform: str, hertz: float, vpp: float,
                   offset: float = 0.0) -> None:
        self.set_waveform(channel, waveform)
        self.set_frequency(channel, hertz)
        self.set_amplitude(channel, vpp)
        self.set_offset(channel, offset)

    @command("Set the pulse width of the adjustable_pulse waveform (channel 1)",
             params={"nanoseconds": Param(unit="ns", min=1, max=1e9)})
    def set_pulse_width(self, nanoseconds: float) -> None:
        self._write(f"WMS{round(nanoseconds)}")
        actual = int(self._ask("RSS")) / 10
        self._verify("pulse width", round(nanoseconds), actual, 0.5, "ns")

    @command("Set the edge time of the trapezoid waveform (1000 gives a 10-90 % rise of "
             "about 1.65 µs; below about 100 the edge is as fast as it gets)",
             params={"channel": CHANNEL, "setting": Param(min=1, max=100000)})
    def set_trapezoid_edge(self, channel: int, setting: int) -> None:
        self._set(self._channel(channel), "H", str(setting))
        self._verify("trapezoid edge", setting, int(self._get(channel, "H")), 0, "")

    # --- modulation (channel 1) -------------------------------------------------
    @command("Start modulating channel 1: am, fm, pm, fsk, ask, psk or burst. The "
             "modulating signal comes from channel 2 or the EXT IN input (external_ac, "
             "external_dc). set_output or stop_modulation ends it",
             params={"kind": Param(choices=tuple(MODULATIONS)),
                     "source": Param(choices=tuple(MODULATION_SOURCES))})
    def start_modulation(self, kind: str, source: str = "ch2") -> None:
        if self.sweeping:
            raise CommandError("stop the sweep first (stop_sweep)")
        self._write(f"WPM{MODULATION_SOURCES[source]}")
        self._write(f"WPF{MODULATIONS[kind]}")
        if self.check and int(self._ask("RPF")) != MODULATIONS[kind]:
            raise CommandError(f"the generator did not switch to {kind} modulation")

    @command("End modulation; channel 1 goes back to its normal signal")
    def stop_modulation(self) -> None:
        self._set(1, "N", "1" if self.get_output(1) else "0")

    @command("Set the AM depth", params={"percent": Param(unit="%", min=0, max=120)})
    def set_am_depth(self, percent: float) -> None:
        self._write(f"WPR{percent:.1f}")
        self._verify("AM depth", round(percent, 1), int(self._ask("RPR")) / 10, 0.05, "%")

    @command("Set the FM deviation: the total swing of the frequency (500 Hz around "
             "1 kHz goes from 750 to 1250 Hz)", params={"hertz": HERTZ})
    def set_fm_deviation(self, hertz: float) -> None:
        text, expected = self._frequency_text(hertz)
        self._write(f"WFM{text}")
        self._verify("FM deviation", expected, self._hz(self._ask("RFM")), 2.5e-6, "Hz")

    @command("Set the second frequency of FSK", params={"hertz": HERTZ})
    def set_fsk_frequency(self, hertz: float) -> None:
        text, expected = self._frequency_text(hertz)
        self._write(f"WFK{text}")
        self._verify("FSK frequency", expected, self._hz(self._ask("RFK")), 2.5e-6, "Hz")

    @command("Set the PM deviation (the peak phase shift)",
             params={"degrees": Param(unit="°", min=0, max=360)})
    def set_pm_deviation(self, degrees: float) -> None:
        self._write(f"WPP{degrees:.2f}")
        self._verify("PM deviation", round(degrees, 2), int(self._ask("RPP")) / 100,
                     0.015, "°")

    @command("Set how many cycles a burst has", params={"cycles": Param(min=1, max=1048575)})
    def set_burst_count(self, cycles: int) -> None:
        self._write(f"WPN{cycles}")
        self._verify("burst count", cycles, int(self._ask("RPN")), 0, "cycles")

    # --- sweep (channel 1) --------------------------------------------------------
    @command("Set up a sweep of channel 1: frequency (Hz, whole numbers), amplitude "
             "(Vpp), offset (V) or duty_cycle (%), from start to end in a time of 1 to "
             "999 s, up, down or up_down, linear or logarithmic. The generator cannot "
             "read these settings back",
             params={"what": Param(choices=tuple(SWEEP_OBJECTS)),
                     "seconds": Param(unit="s", min=1, max=999),
                     "direction": Param(choices=tuple(SWEEP_DIRECTIONS))})
    def set_sweep(self, what: str, start: float, end: float, seconds: int,
                  direction: str = "up", logarithmic: bool = False) -> None:
        limits = {"frequency": (0, self.max_hz), "amplitude": (0, MAX_VPP),
                  "offset": (-MAX_OFFSET, MAX_OFFSET), "duty_cycle": (MIN_DUTY, MAX_DUTY)}
        low, high = limits[what]
        for name, value in (("start", start), ("end", end)):
            if not low <= value <= high:
                raise CommandError(f"the sweep {name} must be between {low:g} and {high:g}")
        if logarithmic and what == "frequency" and min(start, end) <= 0:
            raise CommandError("a logarithmic frequency sweep cannot start or end at 0 Hz")
        if what == "frequency":
            texts = [f"{round(v):08d}" for v in (start, end)]
        elif what == "duty_cycle":
            texts = [f"{v:.1f}" for v in (start, end)]
        elif what == "offset":  # the generator counts sweep offsets from -12 V
            texts = [f"{v + MAX_OFFSET:.3f}" for v in (start, end)]
        else:
            texts = [f"{v:.3f}" for v in (start, end)]
        for text in (f"SOB{SWEEP_OBJECTS[what]}", f"SST{texts[0]}", f"SEN{texts[1]}",
                     f"STI{round(seconds)}", f"SMO{1 if logarithmic else 0}",
                     f"SMD{SWEEP_DIRECTIONS[direction]}", "SXY0"):
            self._write(text)

    @command("Start the sweep set with set_sweep")
    def start_sweep(self) -> None:
        if not self.sweeping:
            self._write(RUN_KEY)
            self.sweeping = True

    @command("Stop the sweep; channel 1 goes back to its normal signal")
    def stop_sweep(self) -> None:
        if self.sweeping:
            self._write(RUN_KEY)  # pause first: a log sweep ignores the output write
            self.sweeping = False
        self._set(1, "N", "1" if self.get_output(1) else "0")

    # --- channel sync, uplink, buzzer, memory -------------------------------------
    @command("Let channel 2 follow channel 1's waveform, frequency, amplitude, offset "
             "or duty_cycle", params={"what": Param(choices=tuple(SYNC_ITEMS))})
    def set_sync(self, what: str, on: bool) -> None:
        number = SYNC_ITEMS[what]
        self._write(f"{'USA' if on else 'USD'}{number}")
        self._verify_flag(f"sync of the {what}", on, self._ask(f"RSA{number}"))

    @command("Whether channel 2 follows channel 1's waveform, frequency, …",
             params={"what": Param(choices=tuple(SYNC_ITEMS))})
    def get_sync(self, what: str) -> bool:
        return int(self._ask(f"RSA{SYNC_ITEMS[what]}")) != 0

    @command("Switch uplink (linking several generators) on or off. While it is on, "
             "the phase setting is ignored")
    def set_uplink(self, on: bool) -> None:
        self._write(f"UUL{1 if on else 0}")
        self._verify_flag("uplink", on, self._ask("RUL"))

    @command("Whether uplink is on")
    def get_uplink(self) -> bool:
        return int(self._ask("RUL")) != 0

    @command("Switch the key beep on or off")
    def set_buzzer(self, on: bool) -> None:
        self._write(f"UBZ{1 if on else 0}")
        self._verify_flag("the buzzer", on, self._ask("RBZ"))

    @command("Save both channels' settings in a memory slot. Slot 1 is loaded at "
             "power-on", params={"slot": Param(min=1, max=20)})
    def save_settings(self, slot: int) -> None:
        self._write(f"USN{slot:02d}")

    @command("Load both channels' settings from a memory slot (an empty slot changes "
             "nothing)", params={"slot": Param(min=1, max=20)})
    def load_settings(self, slot: int) -> None:
        self._write(f"ULN{slot:02d}")

    # --- counter (EXT IN) -----------------------------------------------------------
    @command("Set the gate time of the counter on EXT IN: 1, 10 or 100 s",
             params={"seconds": Param(unit="s", choices=(1, 10, 100))})
    def set_counter_gate(self, seconds: int) -> None:
        self._write(f"WCG{COUNTER_GATES[seconds]}")
        self._verify("counter gate", COUNTER_GATES[seconds], int(self._ask("RCG")), 0, "")

    @command("Set the coupling of EXT IN: dc (switches at a positive level, for logic "
             "signals; the default) or ac",
             params={"coupling": Param(choices=tuple(COUNTER_COUPLINGS))})
    def set_counter_coupling(self, coupling: str) -> None:
        self._write(f"WCC{COUNTER_COUPLINGS[coupling]}")

    @command("Frequency of the signal on EXT IN, from the last gate time, in whole Hz "
             "at a 1 s gate. A new signal shows after about two gate times; for slow "
             "signals 1 / measure_period is more precise", unit="Hz")
    def measure_frequency(self) -> float:
        gate = {code: seconds for seconds, code in COUNTER_GATES.items()}[int(self._ask("RCG"))]
        return int(self._ask("RCF")) / gate

    @command("Period of the signal on EXT IN", unit="s")
    def measure_period(self) -> float:
        return int(self._ask("RCT")) * 1e-9

    @command("High time of the signal on EXT IN", unit="s")
    def measure_high_time(self) -> float:
        return int(self._ask("RC+")) * 1e-9

    @command("Low time of the signal on EXT IN", unit="s")
    def measure_low_time(self) -> float:
        return int(self._ask("RC-")) * 1e-9

    @command("Duty cycle of the signal on EXT IN", unit="%")
    def measure_duty_cycle(self) -> float:
        return int(self._ask("RCD")) / 10

    @command("Pulses counted on EXT IN since reset_counter. Counting starts 1 to 2 s "
             "after the reset; read nothing else from the counter meanwhile, as reading "
             "the frequency can stop the count")
    def measure_count(self) -> int:
        return int(self._ask("RCC"))

    @command("Reset the pulse count of EXT IN")
    def reset_counter(self) -> None:
        self._write("WCZ0")


class FY6900Sim(SimTransport):
    """A simulated FY6900-60M that clamps like the real one (see the module notes).

    EXT IN carries a 1 kHz signal with 25 % duty, for the counter commands.
    """

    def __init__(self, model: str = "FY6900-60M", **options):
        super().__init__(**options)
        self.model = model
        self.max_hz = float(model.split("-")[1].rstrip("M")) * 1e6 if "-" in model else 60e6
        self.channels = {key: {"W": 0, "F": 10000.0, "A": 50000, "O": 0, "D": 50000,
                               "P": 0, "N": 0, "H": 28, "S": 100000, "K": 20000.0,
                               "M": 2000.0} for key in PREFIX.values()}
        self.modulation = {"F": 0, "M": 0, "N": 2, "R": 500, "P": 0}
        self.modulating = False
        self.sweep: dict[str, str] = {}
        self.sweeping = False
        self.system = {"UL": ON, "BZ": ON, "MS": ON, "CG": 0, "CC": 0}
        self.sync = [0] * 5
        self.slots: dict[int, dict] = {}
        self.log: list[str] = []
        self.config: dict = {}

    def configure(self, **options) -> None:
        self.config = options

    def _value(self, text) -> str:
        return f"{text}{self.read_term}"  # a value line, then an empty line

    def _hertz(self, number: float) -> float:
        if self.model.startswith("FY6800"):
            number /= 1e6
        if number < 0 or number > self.max_hz:
            return self.max_hz
        micro = round(number * 1e6)
        if not self.model.startswith("FY6800") and micro % 100_000 >= 65536:
            micro -= 65536  # the FY6900 firmware fault
        return micro / 1e6

    def handle(self, command: str) -> str | None:
        self.log.append(command)
        reads = {"UMO": self.model, "UVE": "V1.8", "UID": "12345678",
                 "RUL": self.system["UL"], "RBZ": self.system["BZ"], "RMS": self.system["MS"],
                 "RCG": self.system["CG"], "RSS": self.channels["M"]["S"],
                 "RCF": 1000 * (1, 10, 100)[self.system["CG"]], "RCT": 1_000_000,
                 "RC+": 250_000, "RC-": 750_000, "RCC": 4321, "RCD": 250}
        reads.update({f"RP{key}": value for key, value in self.modulation.items()})
        reads.update({f"RSA{n}": value for n, value in enumerate(self.sync)})
        if command in reads:
            return self._value(reads[command])
        if command == "KEY17":
            self.sweeping = not self.sweeping
            return ""
        head, value = command[:3], command[3:]
        if head in ("UUL", "UBZ"):
            self.system[head[1:]] = ON if value == "1" else 0
            return ""
        if head in ("USA", "USD"):
            self.sync[int(value)] = ON if head == "USA" else 0
            return ""
        if head == "USN":
            self.slots[int(value)] = {k: dict(v) for k, v in self.channels.items()}
            return ""
        if head == "ULN":
            if int(value) in self.slots:
                self.channels = {k: dict(v) for k, v in self.slots[int(value)].items()}
            return ""
        if head[:1] == "S" and head[:2] in ("SO", "SS", "SE", "ST", "SM", "SX"):
            self.sweep[head] = value
            return ""
        if head == "WCG":
            self.system["CG"] = int(value)
            return ""
        if head == "WCC":
            self.system["CC"] = int(value)
            return ""
        if head in ("WCZ",):
            return ""
        if head[:2] == "WP":
            number = float(value)
            key = head[2]
            if key == "R":
                self.modulation["R"] = round(number * 10)
            elif key == "P":
                self.modulation["P"] = round(number * 100)
            else:
                self.modulation[key] = int(number)
            if key == "F":
                self.modulating = True
            return ""
        kind, prefix, key = command[:1], command[1:2], command[2:3]
        if prefix not in self.channels:
            return None  # unknown commands get no answer
        ch = self.channels[prefix]
        if kind == "R":
            if key in "FKM":
                return self._value(f"{ch[key]:015.6f}")
            return self._value(ch[key] & 0xFFFFFFFF if key == "O" else ch.get(key, 0))
        try:
            number = float(value)
        except ValueError:
            return ""
        if key == "W":
            ch["W"] = int(number) if 0 <= number <= 99 else ch["W"]
        elif key in "FKM":
            ch[key] = self._hertz(number)
            if key == "F" and prefix == "M" and self.sync[1]:
                self.channels["F"]["F"] = ch["F"]
        elif key == "A":
            ch["A"] = round(min(max(number, 0), 24) * 10000)
        elif key == "O":
            ch["O"] = round(min(max(number, -12), 12) * 1000)
        elif key == "D":
            ch["D"] = round(min(max(number, 0.001), 99.999) * 1000)
        elif key == "P":
            ch["P"] = round((number % 360) * 1000)
        elif key == "N":
            ch["N"] = ON if number else 0
            if prefix == "M":
                self.modulating = False
                if self.sweeping and self.sweep.get("SMO") != "1":
                    self.sweeping = False  # a log sweep ignores this (see the notes)
        elif key == "H":
            ch["H"] = int(number)
        elif key == "S":
            ch["S"] = round(number * 10)
        return ""
