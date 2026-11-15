# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""HP 5384A / 5385A frequency counters, and a simulator for them.

Commands are from the HP 5384A/5385A Operating and Service Manual, section III
(Table 3-9, Instrument Command Set; Table 3-2, error messages; paragraphs
3-130 to 3-164). The counters predate SCPI: two-letter codes such as FU1 (frequency
A), FU2 (period A), FU3 (frequency B), GA1/GA2/GA3 (gate 0.1/1/10 s), AT0/AT1
(attenuator A x1/x20), FI0/FI1 (100 kHz filter A), ML0/ML1 (front-panel manual
level), RE (restart the measurement), IN (power-on state), ID (identity), SE (error
number), DR/DL (remote display text / back to readings). Both models take the same
commands; channel B covers 50-225 MHz on the 5384A and 90-1000 MHz on the 5385A.

Checked on a real 5384A through an AR488:

- The counter measures all the time and, while addressed to talk, sends one
  reading after another without EOI, e.g. "F   +9.9999236E+5" (17 characters,
  then CR LF; "S" instead of "F" for period). A read up to EOI therefore never
  ends and blocks the adapter, so the driver switches a Prologix/AR488 transport
  to reads that end at the line feed.
- After a code that changes the measurement, or after RE, the next reading is a
  new one: about 0.4 s at the 0.1 s gate, 1.2 s at 1 s. The driver sends RE before
  each reading, so the result never predates the call.
- Without a signal the counter sends nothing; the read times out, and the next
  reading works without a device clear.
- An illegal code sets the error bit of the status byte, SE answers with its
  number (e.g. "50"; "55" for FU5), and the counter stops measuring until a
  device clear. The driver asks SE after each setting and sends the clear
  itself. An SE sent right after FU5 went unanswered; 0.05 s later it was
  answered, so the driver waits 0.1 s first.
"""

from __future__ import annotations

import math
import re
import time

from benchloom import CommandError, Device, Param, command
from benchloom.transports import SimTransport

GATES = {"0.1": ("GA1", 0.1), "1": ("GA2", 1.0), "10": ("GA3", 10.0)}
FUNCTIONS = {"frequency_a": "FU1", "period_a": "FU2", "frequency_b": "FU3"}
ERRORS = {  # Table 3-2
    10: "self-test error",
    50: "unrecognizable mnemonic in command",
    51: "illegal numeric in command",
    52: "first character of command is illegal",
    53: "illegal character in the DR (remote display) command",
    55: "invalid HP 5384A/85A command",
    56: "invalid diagnostic command",
    60: "ROM failure in I/O processor",
    61: "RAM failure in I/O processor",
    70: "controller connected to talk-only instrument",
}
READING = re.compile(r"^([A-Z])\s*([+-]?[0-9.]+E[+-]?\d+)$")
LONGEST_PERIOD = 0.1  # 10 Hz, the lowest frequency of channel A
DISPLAY_CHARS = 12  # not counting the punctuation between them
SETTLE = 0.1  # s between a code and the SE after it (0.05 s was enough on the bench)


class HP5384A(Device):
    def connect(self) -> None:
        if hasattr(self.transport, "read_to_line_end"):
            self.transport.read_to_line_end()  # the counter never sends EOI
        self.base_timeout = self.transport.timeout
        self.transport.device_clear()  # leaves an error state, if there is one
        self.model = self.idn()
        if not self.model.startswith(("HP5384A", "HP5385A")):
            raise CommandError(f"ID answered {self.model!r}, not HP5384A or HP5385A")
        self._send("IN")  # power-on state: frequency A, x1, no filter, auto level, 0.1 s
        self.function = "FU1"
        self.gate_time = 0.1
        gate = float(self.settings.get("gate_time", 0.1))
        if gate != 0.1:
            self.set_gate_time(gate)

    # --- helpers ----------------------------------------------------------------------
    def _send(self, codes: str) -> None:
        """Send codes, then ask for the error number; clear and fail on an error."""
        self.transport.write(codes)
        time.sleep(SETTLE)  # an SE sent at once after a bad code (FU5) goes unanswered
        try:
            reply = self.transport.query("SE").strip()
        except TimeoutError:
            time.sleep(0.5)  # the adapter may still be waiting as long as we did
            self.transport.clear_input()
            self.transport.device_clear()
            raise CommandError(f"{codes!r}: the counter did not answer SE; it was "
                               "cleared") from None
        try:
            number = int(reply)
        except ValueError:
            raise CommandError(f"SE returned {reply!r}, not an error number") from None
        if number:
            self.transport.device_clear()  # the counter stops measuring until then
            text = ERRORS.get(number, "unknown error")
            raise CommandError(f"{codes!r}: the counter reported error {number}: {text}")

    def _measure(self, function: str, what: str) -> float:
        if function != self.function:
            self._send(function)
            self.function = function
        self.transport.write("RE")  # restart, so the reading is a new one
        wait = self.gate_time * 1.15 + LONGEST_PERIOD + 1.5  # gate ±15 % + one period
        self.transport.set_timeout(max(self.base_timeout, wait))
        try:
            reply = self.transport.read().strip()
        except TimeoutError:
            # the adapter may still be waiting as long as we did: let it finish first
            time.sleep(0.5)
            self.transport.clear_input()
            raise CommandError(f"no reading on {what}: is a signal connected and in "
                               "range?") from None
        finally:
            self.transport.set_timeout(self.base_timeout)
        match = READING.match(reply)
        if not match:
            raise CommandError(f"the counter sent {reply!r}, not a reading")
        return float(match.group(2))

    # --- measurements -----------------------------------------------------------------
    @command("Model, e.g. HP5384A")
    def idn(self) -> str:
        return self.transport.query("ID").strip()

    @command("Measure frequency on channel A (10 Hz to 100 MHz) or B (5384A: 50 to "
             "225 MHz; 5385A: 90 to 1000 MHz)", unit="Hz",
             params={"channel": Param(choices=("A", "B"))})
    def measure_frequency(self, channel: str = "A") -> float:
        if channel == "B":
            return self._measure("FU3", "channel B")
        return self._measure("FU1", "channel A")

    @command("Measure period on channel A (10 ns to 0.1 s)", unit="s")
    def measure_period(self) -> float:
        return self._measure("FU2", "channel A")

    @command("Self-check: measure the internal 10 MHz reference (should read 10 MHz "
             "± 1 digit)", unit="Hz")
    def measure_check(self) -> float:
        return self._measure("CK", "the internal 10 MHz")

    # --- settings ---------------------------------------------------------------------
    @command("Set the gate time: 0.1, 1 or 10 s (longer = more digits)",
             params={"seconds": Param(unit="s", min=0.1, max=10)})
    def set_gate_time(self, seconds: float) -> None:
        key = f"{float(seconds):g}"
        if key not in GATES:
            raise CommandError(f"the gate time must be 0.1, 1 or 10 s, not {seconds:g} s")
        code, value = GATES[key]
        self._send(code)
        self.gate_time = value

    @command("Switch the x20 attenuator of channel A in or out",
             params={"factor": Param(choices=("1", "20"))})
    def set_attenuation(self, factor: str) -> None:
        self._send("AT1" if factor == "20" else "AT0")

    @command("Switch the 100 kHz low-pass filter of channel A on or off")
    def set_filter(self, on: bool) -> None:
        self._send("FI1" if on else "FI0")

    @command("Let the front-panel knob set the trigger level of channel A (±0.1 V, "
             "x20 with the attenuator) and the attenuation of channel B; off = "
             "automatic")
    def set_manual_level(self, on: bool) -> None:
        self._send("ML1" if on else "ML0")

    @command("Back to the power-on state: frequency A, x1, no filter, automatic "
             "level, 0.1 s gate")
    def initialize(self) -> None:
        self._send("IN")
        self.function, self.gate_time = "FU1", 0.1

    @command("Show a text on the display: up to 12 characters, with a comma, colon or "
             "period allowed between them")
    def set_display_text(self, text: str) -> None:
        if len(re.sub(r"[,:.]", "", text)) > DISPLAY_CHARS:
            raise CommandError(f"the display holds {DISPLAY_CHARS} characters "
                               "(not counting , : .)")
        self._send(f"DR{text}")

    @command("Show readings on the display again")
    def clear_display_text(self) -> None:
        self._send("DL")

    @command("The counter's error number (0 = none; see the manual's Table 3-2)")
    def get_error(self) -> int:
        return int(self.transport.query("SE").strip())


# --- simulator ------------------------------------------------------------------------
class HP5384ASim(SimTransport):
    """A 5384A with a 1 MHz signal on channel A and 100 MHz on channel B. Like the
    real counter it sends a reading whenever it is read, and none without a signal
    or in an error state."""

    identity = "HP5384A"
    CODES = re.compile(r"FU[123]|CK|AT[01]|FI[01]|ML[01]|GA[123]|RE|DI|DD|DN|FN1[1-4]|"
                       r"IN|WA[01]|DL|ID|SI|SM\d{1,3}|LE|SE")

    def __init__(self, **options):
        super().__init__(**options)
        self.signals = {"A": 1e6, "B": 100e6}
        self.error = 0
        self.display = ""
        self.initialize()

    def initialize(self) -> None:
        self.function, self.gate, self.attenuator = "FU1", 0.1, 1
        self.filter = self.manual_level = False

    def device_clear(self) -> None:
        self.error = 0

    def _fail(self, number: int) -> None:
        if not self.error:
            self.error = number

    def handle(self, line: str) -> str | None:
        upper, at = line.upper(), 0
        while at < len(upper):
            if upper[at] in " ,;\t":
                at += 1
                continue
            if upper.startswith("DR", at):
                self.display = line[at + 2:]
                if self.error:
                    return None
                if len(re.sub(r"[,:.]", "", self.display)) > DISPLAY_CHARS:
                    self._fail(53)
                return None
            match = self.CODES.match(upper, at)
            if not match:
                self._fail(50 if upper[at].isalpha() else 52)
                return None
            at = match.end()
            code = match.group()
            if code == "SE":
                return f"{self.error:02d}"
            if code in ("ID", "SI"):
                return self.identity
            if self.error:  # ignored until a device clear
                continue
            if code in ("FU1", "FU2", "FU3", "CK"):
                self.function = code
            elif code.startswith("GA"):
                self.gate = {"GA1": 0.1, "GA2": 1.0, "GA3": 10.0}[code]
            elif code.startswith("AT"):
                self.attenuator = 20 if code == "AT1" else 1
            elif code.startswith("FI"):
                self.filter = code == "FI1"
            elif code.startswith("ML"):
                self.manual_level = code == "ML1"
            elif code == "IN":
                self.initialize()
            elif code == "DL":
                self.display = ""
        return None

    def _reading(self) -> str | None:
        if self.function == "CK":
            alpha, value = "F", 10e6
        else:
            signal = self.signals["B" if self.function == "FU3" else "A"]
            if signal is None:
                return None
            alpha, value = ("S", 1 / signal) if self.function == "FU2" else ("F", signal)
        digits = {0.1: 8, 1.0: 9, 10.0: 10}[self.gate]
        exponent = math.floor(math.log10(value))
        mantissa = f"{value / 10 ** exponent:+.{digits - 1}f}"
        return f"{alpha}{mantissa:>13}E{exponent:+d}"

    def read_until(self, term: bytes) -> bytes:
        if not self._out and not self.error:
            self.reply(self._reading())
        return super().read_until(term)
