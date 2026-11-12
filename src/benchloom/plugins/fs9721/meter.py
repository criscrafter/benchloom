# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Handheld multimeters with a Fortune Semiconductor FS9721 display chip, and a
simulator for them.

The meter takes no commands. It sends a copy of its LCD about twice a second as
14 bytes at 2400 baud, 8N1. The upper nibble of each byte is its position (1 to E),
the lower nibble four LCD segments or symbols:

    byte 1      AC, DC, AUTO, RS232
    bytes 2-9   four digits, two bytes each: the first has three segments plus the
                minus sign (digit 1) or the decimal point before the digit, the
                second has the other four segments
    byte A      µ, n, k, diode
    byte B      m, %, M, beep (continuity)
    byte C      F, Ω, REL, HOLD
    byte D      A, V, Hz, low battery
    byte E      model-specific symbols: on the DT-4000ZC °C (bit 2), and bit 3
                set in every function except temperature (meaning unknown)

The optical receiver in the meter's cable takes its power from the serial port:
DTR high, RTS low. The layout follows the FS9721_LP3 data sheet as used by sigrok.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from benchloom import CommandError, Device, SimTransport, command

PACKET_SIZE = 14
PACKET_INTERVAL = 0.5  # the meter sends about two packets a second

# segment code (3 bits of the first byte << 4 | 4 bits of the second) -> character
SEGMENTS = {0x7D: "0", 0x05: "1", 0x5B: "2", 0x1F: "3", 0x27: "4", 0x3E: "5",
            0x7E: "6", 0x15: "7", 0x7F: "8", 0x3F: "9", 0x00: " ", 0x68: "L"}
CODES = {char: code for code, char in SEGMENTS.items()}

# (byte index, bit) of each symbol
FLAGS = {
    "AC": (0, 3), "DC": (0, 2), "AUTO": (0, 1), "RS232": (0, 0),
    "µ": (9, 3), "n": (9, 2), "k": (9, 1), "diode": (9, 0),
    "m": (10, 3), "%": (10, 2), "M": (10, 1), "beep": (10, 0),
    "F": (11, 3), "Ω": (11, 2), "REL": (11, 1), "HOLD": (11, 0),
    "A": (12, 3), "V": (12, 2), "Hz": (12, 1), "BAT": (12, 0),
    "°C": (13, 2),  # byte E is model-specific; this is the DT-4000ZC's
}
PREFIXES = {"n": -9, "µ": -6, "m": -3, "k": 3, "M": 6}  # power of ten
UNITS = ("V", "A", "Ω", "F", "Hz", "%", "°C")  # in the order they are checked


@dataclass(frozen=True)
class Reading:
    """One decoded display."""

    value: float  # in the base unit (V, A, Ω, F, Hz, %, °C); ±inf on overload
    unit: str  # one of UNITS, or "" when the meter shows none
    flags: frozenset[str]
    display: str  # the digits as shown, e.g. "-1.234" or " 0L "

    @property
    def mode(self) -> str:
        """The function the dial is set to, e.g. "DC V", "Ω" or "diode"."""
        if "diode" in self.flags:
            return "diode"
        if "beep" in self.flags:
            return "continuity"
        coupling = next((c for c in ("DC", "AC") if c in self.flags), "")
        if self.unit in ("V", "A") and coupling:
            return f"{coupling} {self.unit}"
        return self.unit or "unknown"

    @property
    def overload(self) -> bool:
        return math.isinf(self.value)

    def __str__(self) -> str:
        prefix = next((p for p in PREFIXES if p in self.flags), "")
        extra = [f for f in ("AC", "DC", "HOLD", "REL", "BAT") if f in self.flags]
        return " ".join([self.display.strip(), prefix + self.unit, *extra]).strip()


def is_packet(data: bytes) -> bool:
    return len(data) == PACKET_SIZE and all(b >> 4 == i + 1 for i, b in enumerate(data))


def decode(data: bytes) -> Reading:
    """Decode one 14-byte packet."""
    if not is_packet(data):
        raise ValueError(f"not an FS9721 packet: {data.hex(' ')}")
    flags = frozenset(name for name, (i, bit) in FLAGS.items() if data[i] >> bit & 1)
    text, negative = "", bool(data[1] & 0x08)
    for n in range(4):
        hi, lo = data[1 + 2 * n], data[2 + 2 * n]
        if n > 0 and hi & 0x08:
            text += "."
        code = (hi & 0x07) << 4 | (lo & 0x0F)
        if code not in SEGMENTS:
            raise ValueError(f"unknown digit pattern 0x{code:02X} in {data.hex(' ')}")
        text += SEGMENTS[code]
    display = ("-" if negative else "") + text
    unit = next((u for u in UNITS if u in flags), "")
    if "L" in text:
        value = -math.inf if negative else math.inf
    else:
        digits = text.replace(" ", "")
        if not digits.strip("."):
            raise ValueError(f"display is blank: {data.hex(' ')}")
        exponent = next((e for p, e in PREFIXES.items() if p in flags), 0)
        # from text: no float noise; + 0.0 turns REL's "-00.00" into 0.0, not -0.0
        value = float(f"{'-' if negative else ''}{digits}e{exponent}") + 0.0
    return Reading(value, unit, flags, display)


def encode(display: str, flags=()) -> bytes:
    """Build the packet for a display such as "-1.234" or "0L" and a set of FLAGS."""
    negative = display.startswith("-")
    body = display.lstrip("-")
    digits, dp = [], set()
    for char in body:
        if char == ".":
            dp.add(len(digits))  # point before this digit
        else:
            digits.append(char)
    digits = [" "] * (4 - len(digits)) + digits
    shift = 4 - len(body.replace(".", ""))
    dp = {p + shift for p in dp}
    if len(digits) != 4 or len(dp) > 1 or shift in dp:
        raise ValueError(f"not a display of four digits and at most one point after the "
                         f"first: {display!r}")
    data = [0x10] + [0] * 13
    for n, char in enumerate(digits):
        code = CODES[char]
        data[1 + 2 * n] = (0x20 + 0x20 * n) | code >> 4
        data[2 + 2 * n] = (0x30 + 0x20 * n) | code & 0x0F
        if (n == 0 and negative) or (n > 0 and n in dp):
            data[1 + 2 * n] |= 0x08
    for i in range(9, 14):
        data[i] = (i + 1) << 4
    for name in flags:
        i, bit = FLAGS[name]
        data[i] |= 1 << bit
    return bytes(data)


class FS9721Meter(Device):
    """Reads the display of an FS9721 multimeter. Set the function on its dial."""

    def connect(self) -> None:
        configure = getattr(self.transport, "configure", None)
        if configure is not None:  # fixed on the meter
            configure(baud=2400, bytesize=8, parity="N", stopbits=1)
        set_lines = getattr(self.transport, "set_lines", None)
        if set_lines is not None:  # powers the optical receiver in the cable
            set_lines(dtr=True, rts=False)

    def identify(self) -> str:
        return f"display shows {self.reading()}"

    def reading(self) -> Reading:
        """The next complete display the meter sends."""
        self.transport.clear_input()  # older packets may be stale
        deadline = time.monotonic() + max(3 * PACKET_INTERVAL, self.transport.timeout)
        buffer = bytearray()
        garbled = 0
        while time.monotonic() < deadline:
            buffer += self.transport.read_some(PACKET_SIZE)
            # keep the bytes from the last position-1 byte on: a packet starts there
            while len(buffer) >= PACKET_SIZE:
                start = next((i for i, b in enumerate(buffer) if b >> 4 == 1), None)
                if start is None:
                    garbled += len(buffer)
                    buffer.clear()
                    break
                del buffer[:start]
                if len(buffer) < PACKET_SIZE:
                    break
                packet = bytes(buffer[:PACKET_SIZE])
                if is_packet(packet):
                    try:
                        return decode(packet)
                    except ValueError as exc:
                        raise CommandError(str(exc)) from None
                garbled += 1
                del buffer[0]
        if garbled:
            raise CommandError(
                "the meter sends data, but it is garbled: check that the cable sits "
                "firmly on the meter. With a USB serial adapter the cable's signal may "
                "fall too slowly: a 3.3 kΩ resistor from the data line (pin 2 at the "
                "PC) to RTS (pin 7) fixed this on a tested meter")
        raise CommandError("no data from the meter: is it on, and RS232 mode switched on?")

    def _measure(self, unit: str) -> float:
        reading = self.reading()
        if reading.unit != unit or reading.mode in ("diode", "continuity"):
            raise CommandError(f"the meter is set to {reading.mode}, not {unit}: "
                               "turn its dial")
        return reading.value

    @command("Read the display as a number in its base unit (V, A, Ω, F, Hz, % or °C); "
             "±infinity when the meter shows OL")
    def read(self) -> float:
        return self.reading().value

    @command("The display as text, e.g. \"5.003 V DC\"")
    def read_display(self) -> str:
        return str(self.reading())

    @command("The function the dial is set to: \"DC V\", \"AC V\", \"DC A\", \"AC A\", "
             "\"Ω\", \"F\", \"Hz\", \"%\", \"°C\", \"diode\" or \"continuity\"")
    def read_mode(self) -> str:
        return self.reading().mode

    @command("Measure voltage (dial on V)", unit="V")
    def measure_voltage(self) -> float:
        return self._measure("V")

    @command("Measure current (dial on A, mA or µA)", unit="A")
    def measure_current(self) -> float:
        return self._measure("A")

    @command("Measure resistance (dial on Ω)", unit="Ω")
    def measure_resistance(self) -> float:
        return self._measure("Ω")

    @command("Measure capacitance (dial on F)", unit="F")
    def measure_capacitance(self) -> float:
        return self._measure("F")

    @command("Measure frequency (dial on Hz)", unit="Hz")
    def measure_frequency(self) -> float:
        return self._measure("Hz")

    @command("Measure the duty cycle (dial on %)", unit="%")
    def measure_duty_cycle(self) -> float:
        return self._measure("%")

    @command("Measure temperature with a thermocouple (dial on °C)", unit="°C")
    def measure_temperature(self) -> float:
        return self._measure("°C")


class FS9721Sim(SimTransport):
    """A meter on DC volts showing 5.003 V. Tests change ``display`` and ``flags``."""

    def __init__(self, **options):
        super().__init__(**options)
        self.display = "5.003"
        self.flags = {"DC", "AUTO", "RS232", "V"}
        self.lines: dict[str, bool] = {}
        self.baud = None

    def configure(self, *, baud=None, **options) -> None:
        self.baud = baud

    def set_lines(self, *, dtr=None, rts=None) -> None:
        self.lines.update({k: v for k, v in (("dtr", dtr), ("rts", rts)) if v is not None})

    def handle(self, command: str) -> None:
        return None  # the meter ignores anything sent to it

    def read_raw(self, size: int) -> bytes:
        while len(self._out) < size:
            if not self.lines.get("dtr") or self.lines.get("rts"):
                raise TimeoutError("the cable's receiver has no power")
            self._out += encode(self.display, self.flags)
        return super().read_raw(size)

    def read_some(self, size: int) -> bytes:
        return self.read_raw(size)
