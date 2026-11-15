# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""HP 5334A / 5334B universal counters, and a simulator for them.

Commands are from the HP 5334A/B Operating and Programming Manual, part no.
05334-90047 (December 1993): the command set (Table 3-12, pp. 3-82 and 3-83), the
output format (pp. 3-84 and 3-85), error handling (pp. 3-87 and 3-88, Table 3-3 on
p. 3-48) and the preset and disallowed conditions (Table 3-13, pp. 3-99 to 3-101).
The counters predate SCPI: two-letter codes, some with a number (FN1 frequency A,
GA0.3 gate time, AT1.5 trigger level A, ...). The 5334A adds math, setup memory
and the DVM option, which this driver does not use.

Checked on a real 5334B through an AR488:

- Like the 5384A it measures all the time and, while addressed to talk, sends one
  reading after another, e.g. "F    +9.9999221E+05" (19 characters, then CR LF).
  The driver reads up to the line feed.
- With auto trigger on (the power-on state) a reading takes about 1.1 to 1.5 s at
  the 0.3 s gate; with it off about 0.4 s. The driver sends RE before each
  reading, so the result never predates the call.
- An illegal code puts the counter in an error state: TE answers e.g. "4.0", and
  it measures nothing until a device clear. The driver asks TE after each setting
  and sends the clear itself.
- The manual calls the remote trigger levels (TR1 with AT and BT) "5334A only",
  but the tested 5334B took them: AT1.5 and BT-0.5 read back as A +1.50 and
  B -0.50, and TR0 gave the knob's level back.
- Rise or fall time is chosen by the slope of channel A, which cannot change
  while in rise/fall mode (error 1.0), so the driver leaves that mode first.
  Rise/fall read 0.414 ms on a 1 kHz triangle (0.4 ms expected) and 30.8 µs on a
  10 kHz sine (29.5 µs); a 0-2 V triangle (offset +1 V) gave no reading.
- Without a signal the counter sends nothing; the read times out, and the first
  reading after the signal returns arrives about 0.5 s later.
"""

from __future__ import annotations

import math
import re
import time

from benchloom import CommandError, Device, Param, command
from benchloom.transports import SimTransport

ERRORS = {  # Table 3-3
    1.0: "parameter disallowed in present mode",
    1.1: "attenuators controlled by AUTO TRIG",
    1.2: "50-ohm B, AC B settings preset by COM A",
    1.3: "slope B set by slope A in rise/fall mode",
    1.4: "parameter disallowed in high speed mode",
    1.5: "calibration data unaccessible in present mode",
    2.0: "invalid key entry",
    2.1: "data outside valid range",
    2.2: "data exceeds maximum resolution",
    2.3: "mantissa digit buffer full",
    2.4: "decimal point previously entered",
    3.0: "multiple key closures",
    4.0: "mnemonic not recognizable",
    4.1: "numeric syntax error",
    4.2: "alpha character expected",
    4.3: "data exceeds valid range",
    4.4: "attention (ATN) asserted in talk-only mode",
}
READING = re.compile(r"^([A-Za-z])\s*([+-]?\d+(?:\.\d*)?E[+-]?\d+)$")
PAIR = re.compile(r"^([A-Z])\s*([+-]?[\d.]+),([A-Z])\s*([+-]?[\d.]+)$")
OVERFLOW = "O"
SETTLE = 0.1  # s between a code and the TE after it
MIN_GATE, MAX_GATE = 0.001, 99.999
LEVEL_X1, LEVEL_X10 = 5.1, 51.0  # trigger level range, x1 and x10 attenuator
DEAD_TIME, DEAD_TIME_AUTO = 0.1, 1.0  # s between gates: auto trigger off / on

CHANNEL = Param(choices=("A", "B"))
# Functions without presets of their own, where every setting is allowed (Table 3-13)
PLAIN = {"FN1", "FN2", "FN4", "FN5", "FN7"}
# Functions that preset auto trigger (AU1 or AU0) and, for rise/fall, COM A
PRESETS = {"FN3", "FN8", "FN9", "FN10", "FN11", "FN13", "FN14", "FN15"}


class HP5334(Device):
    def connect(self) -> None:
        if hasattr(self.transport, "read_to_line_end"):
            self.transport.read_to_line_end()  # the counter never ends a reading with EOI
        self.base_timeout = self.transport.timeout
        self.transport.device_clear()  # leaves an error state, if there is one
        self.model = self.idn()
        if not self.model.startswith(("HP5334A", "HP5334B")):
            raise CommandError(f"ID answered {self.model!r}, not HP5334A or HP5334B")
        self.initialize()
        gate = float(self.settings.get("gate_time", 0.3))
        if gate != 0.3:
            self.set_gate_time(gate)

    # --- helpers ----------------------------------------------------------------------
    def _send(self, codes: str) -> None:
        """Send codes, then ask for the error; clear the counter and fail on one."""
        self.transport.write(codes)
        time.sleep(SETTLE)
        try:
            reply = self.transport.query("TE").strip()
        except TimeoutError:
            time.sleep(0.5)  # the adapter may still be waiting as long as we did
            self.transport.clear_input()
            self.transport.device_clear()
            raise CommandError(f"{codes!r}: the counter did not answer TE; it was "
                               "cleared") from None
        try:
            number = float(reply)
        except ValueError:
            raise CommandError(f"TE returned {reply!r}, not an error number") from None
        if number:
            self.transport.device_clear()  # the counter measures nothing until then
            text = ERRORS.get(round(number, 1), "unknown error")
            raise CommandError(f"{codes!r}: the counter reported error {number:.1f}: {text}")

    def _function(self, code: str) -> None:
        if code == self.function:
            return
        previous = self.function
        self._send(code)
        self.function = code
        # Rise/fall turns COM A on and some functions preset auto trigger on or off;
        # the counter keeps that afterwards, so put back what was chosen.
        if previous == "FN11" and code != "FN11" and not self.common:
            self._send("CO0")
        if previous in PRESETS and code in PLAIN:
            self._send(self._trigger_codes())

    def _trigger_codes(self) -> str:
        if self.trigger == "auto":
            return "AU1"
        if self.trigger == "knob":
            return "AU0,TR0"
        levels = ",".join(f"{ch}T{volts:.2f}" for ch, volts in self.levels.items()
                          if volts is not None)
        return "AU0,TR1" + (f",{levels}" if levels else "")

    def _setting(self, codes: str) -> None:
        """Send a setting from a plain function: special modes disallow many."""
        if self.function not in PLAIN:
            self._function("FN1")
        self._send(codes)

    def _read_line(self, what: str, restart: bool = True) -> str:
        if restart:
            self.transport.write("RE")  # restart, so the reading is a new one
        wait = self._expected_wait()
        limit = getattr(self.transport, "max_read_ms", None)
        if limit and wait > limit / 1000:
            raise CommandError(
                f"a reading takes about {wait:.0f} s here, longer than the adapter "
                f"waits ({limit / 1000:g} s): use a shorter gate time or, for the "
                "100-gate average, switch auto trigger off")
        self.transport.set_timeout(max(self.base_timeout, wait))
        try:
            return self.transport.read().strip()
        except TimeoutError:
            time.sleep(0.5)  # let the adapter finish its own wait first
            self.transport.clear_input()
            raise CommandError(f"no reading on {what}: is a signal connected, in range "
                               "and crossing the trigger level?") from None
        finally:
            self.transport.set_timeout(self.base_timeout)

    def _expected_wait(self) -> float:
        """Seconds to wait for a reading. Between gates the counter pauses about 1 s
        with auto trigger on and 85 ms with it off (measured; the specifications say
        1 s and 80 ms); the 100-gate average pays that for every gate."""
        if not self.average:
            return self.gate_time + 3.0
        pause = DEAD_TIME_AUTO if self.trigger == "auto" else DEAD_TIME
        return 100 * (self.gate_time + pause) + 3.0

    def _measure(self, code: str, what: str) -> float:
        self._function(code)
        reply = self._read_line(what)
        match = READING.match(reply)
        if not match:
            raise CommandError(f"the counter sent {reply!r}, not a reading")
        if match.group(1) == OVERFLOW:
            return math.nan
        return float(match.group(2))

    def _pair(self, code: str, what: str) -> tuple[float, float]:
        self._function(code)
        reply = self._read_line(what)
        if reply.startswith(OVERFLOW):
            return math.nan, math.nan
        match = PAIR.match(reply)
        if not match:
            raise CommandError(f"the counter sent {reply!r}, not two values")
        return float(match.group(2)), float(match.group(4))

    def _slope_a(self, slope: str) -> None:
        if slope != self.slopes["A"]:
            # slope A cannot change in rise/fall mode, so _setting leaves it first
            self._setting("AS1" if slope == "falling" else "AS0")
            self.slopes["A"] = slope

    # --- measurements -----------------------------------------------------------------
    @command("Model, e.g. HP5334B")
    def idn(self) -> str:
        return self.transport.query("ID").strip()

    @command("Measure frequency on channel A or B (0.001 Hz to 100 MHz), or C (option "
             "030, 90 MHz to 1.3 GHz)", unit="Hz",
             params={"channel": Param(choices=("A", "B", "C"))})
    def measure_frequency(self, channel: str = "A") -> float:
        code = {"A": "FN1", "B": "FN2", "C": "FN3"}[channel]
        return self._measure(code, f"channel {channel}")

    @command("Measure period on channel A (10 ns to 1000 s)", unit="s")
    def measure_period(self) -> float:
        return self._measure("FN4", "channel A")

    @command("Measure the time interval from channel A to channel B", unit="s")
    def measure_time_interval(self) -> float:
        return self._measure("FN5", "channels A and B")

    @command("Measure the frequency ratio channel A / channel B")
    def measure_ratio(self) -> float:
        return self._measure("FN7", "channels A and B")

    @command("Measure the pulse width on channel A (5 ns to 10 ms): the time the signal "
             "stays above (positive) or below (negative) the trigger level", unit="s",
             params={"polarity": Param(choices=("positive", "negative"))})
    def measure_pulse_width(self, polarity: str = "positive") -> float:
        self._slope_a("falling" if polarity == "negative" else "rising")
        return self._measure("FN10", "channel A")

    @command("Measure the rise time (10 % to 90 %) on channel A (30 ns to 10 ms; at "
             "least 500 mV peak to peak)", unit="s")
    def measure_rise_time(self) -> float:
        self._slope_a("rising")
        return self._measure("FN11", "channel A")

    @command("Measure the fall time (90 % to 10 %) on channel A", unit="s")
    def measure_fall_time(self) -> float:
        self._slope_a("falling")
        return self._measure("FN11", "channel A")

    @command("Measure the highest and lowest voltage on channel A or B", unit="V",
             params={"channel": CHANNEL})
    def measure_peaks(self, channel: str = "A") -> dict:
        high, low = self._pair("FN14" if channel == "A" else "FN15", f"channel {channel}")
        return {"high": high, "low": low}

    @command("Read the trigger levels of channels A and B", unit="V")
    def read_trigger_levels(self) -> dict:
        a, b = self._pair("FN13", "the trigger levels")
        return {"A": a, "B": b}

    @command("Count the events on channel A during a time. The counter does not "
             "auto-trigger here: it counts where the trigger level is (knob or "
             "set_trigger_level)", unit="events",
             params={"seconds": Param(unit="s", min=0.01, max=1e6)})
    def measure_totalize(self, seconds: float) -> float:
        self.start_totalize()
        time.sleep(seconds)
        return self.stop_totalize()

    @command("Start counting events on channel A from zero")
    def start_totalize(self) -> None:
        self.function = None  # leaving totalize must send the function again
        self._send("FN9")
        self.function = "FN9"

    @command("The count so far, while counting", unit="events")
    def read_totalize(self) -> float:
        if self.function != "FN9":
            raise CommandError("no count is running: start it with start_totalize")
        return self._total(self._read_line("channel A", restart=False))

    @command("Stop counting and return the final count", unit="events")
    def stop_totalize(self) -> float:
        if self.function != "FN9":
            raise CommandError("no count is running: start it with start_totalize")
        self.transport.write("FN8")
        self.function = "FN8"
        while True:  # interim counts ("t") may still be on their way
            reply = self._read_line("channel A", restart=False)
            if reply.startswith("T"):
                return self._total(reply)

    @staticmethod
    def _total(reply: str) -> float:
        match = READING.match(reply)
        if not match or match.group(1) not in "Tt":
            raise CommandError(f"the counter sent {reply!r}, not a count")
        return float(match.group(2))

    # --- settings ---------------------------------------------------------------------
    @command("Set the gate time, 0.001 to 99.999 s in 1 ms steps (longer = more digits)",
             params={"seconds": Param(unit="s", min=MIN_GATE, max=MAX_GATE)})
    def set_gate_time(self, seconds: float) -> None:
        self._setting(f"GA{seconds:.3f}")
        self.gate_time = round(seconds, 3)

    @command("Set the input coupling of channel A or B",
             params={"channel": CHANNEL, "coupling": Param(choices=("AC", "DC"))})
    def set_coupling(self, channel: str, coupling: str) -> None:
        self._setting(f"{channel}A{1 if coupling == 'AC' else 0}")

    @command("Set the input impedance of channel A or B: 50 Ω or 1 MΩ",
             params={"channel": CHANNEL, "impedance": Param(choices=("50", "1M"))})
    def set_impedance(self, channel: str, impedance: str) -> None:
        self._setting(f"{channel}Z{1 if impedance == '50' else 0}")

    @command("Set the attenuator of channel A or B: x1 or x10 (only with auto trigger "
             "off; auto trigger sets it itself)",
             params={"channel": CHANNEL, "factor": Param(choices=("1", "10"))})
    def set_attenuation(self, channel: str, factor: str) -> None:
        self._setting(f"{channel}X{1 if factor == '10' else 0}")
        self.attenuation[channel] = int(factor)

    @command("Trigger on the rising or falling edge of channel A or B",
             params={"channel": CHANNEL, "slope": Param(choices=("rising", "falling"))})
    def set_trigger_slope(self, channel: str, slope: str) -> None:
        if channel == "A":
            self._slope_a(slope)
        else:
            self._setting("BS1" if slope == "falling" else "BS0")
            self.slopes["B"] = slope

    @command("Switch the 100 kHz low-pass filter of channel A on or off")
    def set_filter(self, on: bool) -> None:
        self._setting("FI1" if on else "FI0")

    @command("Feed channel A's input to both channels (COM A; rise/fall time switches "
             "it on for itself)")
    def set_common(self, on: bool) -> None:
        self._setting("CO1" if on else "CO0")
        self.common = on

    @command("Let the counter set trigger levels and attenuators itself (100 Hz to "
             "100 MHz); off = the front-panel knob, or set_trigger_level")
    def set_auto_trigger(self, on: bool) -> None:
        self._setting("AU1" if on else "AU0")
        self.trigger = "auto" if on else ("levels" if self.dacs else "knob")

    @command("Set a fixed trigger level on channel A or B (turns auto trigger off): "
             "±5.1 V, ±51 V with the x10 attenuator",
             params={"channel": CHANNEL,
                     "volts": Param(unit="V", min=-LEVEL_X10, max=LEVEL_X10)})
    def set_trigger_level(self, channel: str, volts: float) -> None:
        limit = LEVEL_X10 if self.attenuation[channel] == 10 else LEVEL_X1
        if abs(volts) > limit:
            raise CommandError(f"the trigger level of channel {channel} goes to "
                               f"±{limit:g} V with the x{self.attenuation[channel]} "
                               "attenuator")
        self._setting(f"AU0,TR1,{channel}T{volts:.2f}")
        self.levels[channel] = volts
        self.trigger, self.dacs = "levels", True

    @command("Hand the trigger levels back to the front-panel knob (auto trigger off)")
    def use_trigger_knob(self) -> None:
        self._setting("AU0,TR0")
        self.trigger, self.dacs = "knob", False

    @command("Average over 100 gates (one more digit; a reading then takes 100 gate "
             "times; not for totalize)")
    def set_100_gate_average(self, on: bool) -> None:
        self._setting("GV1" if on else "GV0")
        self.average = on

    @command("Back to the power-on state: frequency A, auto trigger, 0.3 s gate, "
             "inputs as after power-up")
    def initialize(self) -> None:
        self._send("IN")
        self.function, self.gate_time = "FN1", 0.3
        self.slopes = {"A": "rising", "B": "rising"}
        self.attenuation = {"A": 1, "B": 1}
        self.common = self.average = self.dacs = False
        self.trigger = "auto"
        self.levels: dict[str, float | None] = {"A": None, "B": None}

    @command("The counter's error number (0 = none; see the manual's Table 3-3)")
    def get_error(self) -> float:
        return float(self.transport.query("TE").strip())


# --- simulator ------------------------------------------------------------------------
class HP5334Sim(SimTransport):
    """A 5334B with a 1 MHz, 1 Vpp sine on channel A and 250 kHz on channel B. Like the
    real counter it sends a reading whenever it is read, and none without a signal or
    in an error state."""

    identity = "HP5334B"
    CODE = re.compile(r"([A-Z]{2})([+-]?(?:\d+\.?\d*|\.\d+)(?:E[+-]?\d+)?)?")

    def __init__(self, **options):
        super().__init__(**options)
        self.signals = {"A": {"freq": 1e6, "high": 0.5, "low": -0.5},
                        "B": {"freq": 250e3, "high": 0.5, "low": -0.5}}
        self.error = 0.0
        self.initialize()

    def initialize(self) -> None:
        self.function, self.gate, self.auto = 1, 0.3, True
        self.dacs, self.levels, self.knob = False, {"A": 0.0, "B": 0.0}, 0.2
        self.slope = {"A": 0, "B": 0}
        self.settings = {}
        self.count, self.final = 0.0, None

    def device_clear(self) -> None:
        self.error = 0.0

    def _fail(self, number: float) -> None:
        if not self.error:
            self.error = number

    def handle(self, line: str) -> str | None:
        text, at = line.upper(), 0
        while at < len(text):
            if text[at] in " ,;\t":
                at += 1
                continue
            match = self.CODE.match(text, at)
            if not match:
                self._fail(4.2 if not text[at].isalpha() else 4.0)
                return None
            at = match.end()
            reply = self._code(match.group(1), match.group(2))
            if reply is not None:
                return reply
        return None

    def _code(self, code: str, number: str | None) -> str | None:
        if code == "TE":
            return f"{self.error:.1f}"
        if code == "ID":
            return self.identity
        if code == "IN":
            self.error = 0.0
            self.initialize()
            return None
        if self.error:  # ignored until a device clear
            return None
        value = float(number) if number else None
        binary = code in ("AA", "AS", "AU", "AX", "AZ", "BA", "BS", "BX", "BZ", "CO",
                          "FI", "SE", "TR", "GS", "GV", "HS", "WA", "MD")
        if binary:
            if value not in (0, 1):
                self._fail(4.3)
            elif code == "AS" and self.function == 11:
                self._fail(1.0)
            elif code == "AU" and value and self.function in (3, 8, 9, 13):
                self._fail(1.0)  # as on the bench: AU1 in read-levels mode
            elif code == "AU":
                self.auto = bool(value)
                if self.auto:
                    self.dacs = False
            elif code == "TR":
                self.dacs = bool(value)
            elif code in ("AS", "BS"):
                self.slope[code[0]] = int(value)
            else:
                self.settings[code] = int(value)
        elif code in ("FN", "FU"):
            if value is None or value != int(value) or not 1 <= value <= 15:
                self._fail(4.3)
            else:
                self.function = int(value)
                if self.function in (10, 11, 14, 15):  # Table 3-13 presets
                    self.auto, self.dacs = True, False
                elif self.function in (3, 8, 9, 13):
                    self.auto = False
                if self.function == 11:
                    self.settings["CO"] = 1
                if self.function == 9:
                    self.count, self.final = 0.0, None
                elif self.function == 8 and self.final is None:
                    self.final = self.count
        elif code == "GA":
            if value is None or not MIN_GATE <= value <= MAX_GATE:
                self._fail(4.3)
            else:
                self.gate = value
        elif code in ("AT", "BT"):
            if value is None or abs(value) > LEVEL_X10:
                self._fail(4.3)
            else:
                self.levels[code[0]] = value
        elif code in ("RE", "SM", "TC"):
            pass
        else:
            self._fail(4.0)
        return None

    @staticmethod
    def _number(alpha: str, value: float, digits: int) -> str:
        if value == 0:
            return f"{alpha}{'0E+00':>18}"
        exponent = math.floor(math.log10(abs(value)))
        mantissa = f"{value / 10 ** exponent:+.{digits - 1}f}"
        return f"{alpha}{mantissa:>14}E{exponent:+03d}"

    def _level(self, channel: str) -> float:
        if self.auto:
            signal = self.signals[channel]
            return (signal["high"] + signal["low"]) / 2 if signal else 0.0
        return self.levels[channel] if self.dacs else self.knob

    def _reading(self) -> str | None:
        fn, a, b = self.function, self.signals["A"], self.signals["B"]
        if self.settings.get("CO"):
            b = a  # COM A: channel A's input on both channels
        needs_b = fn in (2, 5, 7)
        if (a is None and not needs_b and fn not in (13, 15)) or (needs_b and b is None):
            return None
        if fn == 13:
            return f"A{self._level('A'):+7.2f},B{self._level('B'):+7.2f}"
        if fn in (14, 15):
            signal = a if fn == 14 else b
            high, low = (signal["high"], signal["low"]) if signal else (0.0, 0.0)
            return f"H{high:+7.2f},L{low:+7.2f}"
        if fn == 9:
            self.count += 1000.0
            return self._number("t", self.count, 4)
        if fn == 8:
            if self.final is None:
                return None
            final, self.final = self.final, None
            return self._number("T", final, 4)
        digits = 8 if self.gate < 1 else 9
        if fn == 1:
            return self._number("F", a["freq"], digits)
        if fn == 2:
            return self._number("F", b["freq"], digits)
        if fn == 4:
            return self._number("S", 1 / a["freq"], digits)
        if fn == 5:
            return self._number("S", 0.25 / b["freq"], 6)
        if fn == 7:
            return self._number("R", a["freq"] / b["freq"], digits)
        if fn == 10:
            return self._number("S", 0.5 / a["freq"], 6)
        if fn == 11:
            return self._number("S", 0.4 / a["freq"], 6)
        return None

    def read_until(self, term: bytes) -> bytes:
        if not self._out and not self.error:
            self.reply(self._reading())
        return super().read_until(term)
