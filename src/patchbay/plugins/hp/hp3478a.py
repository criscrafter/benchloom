"""HP 3478A multimeter, and a simulator for it.

The 3478A predates SCPI. It takes short program codes in a fixed order
(function F, range R, autozero Z, digits N, trigger T), for example
"F1RAZ1N5T3" = DC volts, autorange, autozero on, 5½ digits, take one reading.
Codes and ranges follow the HP 3478A quick reference card and technical manual.
"""

from __future__ import annotations

import math
import re

from patchbay import CommandError, Device, Param, command
from patchbay.transports import SimTransport

# function name -> (F code, ranges as (R code, full scale))
FUNCTIONS = {
    "dc_volts": (1, [(-2, 0.03), (-1, 0.3), (0, 3.0), (1, 30.0), (2, 300.0)]),
    "ac_volts": (2, [(-1, 0.3), (0, 3.0), (1, 30.0), (2, 300.0)]),
    "ohms_2wire": (3, [(1, 30.0), (2, 300.0), (3, 3e3), (4, 30e3), (5, 300e3), (6, 3e6),
                       (7, 30e6)]),
    "ohms_4wire": (4, [(1, 30.0), (2, 300.0), (3, 3e3), (4, 30e3), (5, 300e3), (6, 3e6),
                       (7, 30e6)]),
    "dc_amps": (5, [(-1, 0.3), (0, 3.0)]),
    "ac_amps": (6, [(-1, 0.3), (0, 3.0)]),
    "ohms_extended": (7, []),  # autorange only
}
UNITS = {"dc_volts": "V", "ac_volts": "V", "ohms_2wire": "Ω", "ohms_4wire": "Ω",
         "dc_amps": "A", "ac_amps": "A", "ohms_extended": "Ω"}
OVERLOAD = 9.99999e9
DIGIT_CODES = {5: 1, 4: 2, 3: 3}  # digits -> bits 1,0 of status byte 1


def format_reading(value: float) -> str:
    """Format a value the way the 3478A sends it, e.g. +1.23456E+0."""
    if math.isinf(value):
        return "+9.99999E+9" if value > 0 else "-9.99999E+9"
    exponent = 0
    if value:
        exponent = max(-3, min(6, 3 * math.floor(math.log10(abs(value)) / 3)))
    mantissa = value / 10 ** exponent
    digits = 5 - (len(str(int(abs(mantissa)))) - 1)
    return f"{mantissa:+.{digits}f}E{exponent:+d}"


class HP3478A(Device):
    def connect(self) -> None:
        self.function: str | None = None  # unknown until we set it
        self.range_code = "RA"
        self.digits = int(self.settings.get("digits", 5))
        self.autozero = True

    def _codes(self, trigger: str = "") -> str:
        f_code = FUNCTIONS[self.function][0]
        return (f"F{f_code}R{self.range_code.removeprefix('R')}"
                f"Z{1 if self.autozero else 0}N{self.digits}{trigger}")

    def _configure(self) -> None:
        if self.function is None:
            self.function = "dc_volts"
        self.transport.write(self._codes("T4"))  # T4 = hold: no reading until asked

    def _trigger_and_read(self) -> float:
        # One message both configures and triggers: any further code would cancel the reading.
        reply = self.transport.query(self._codes("T3"))
        try:
            value = float(reply)
        except ValueError:
            raise CommandError(f"unexpected reading from the 3478A: {reply!r}") from None
        if abs(value) >= OVERLOAD:
            return math.copysign(math.inf, value)
        return value

    # --- configuration ----------------------------------------------------------------
    @command("Select what to measure", params={"function": Param(choices=tuple(FUNCTIONS))})
    def set_function(self, function: str) -> None:
        if function != self.function:
            self.function = function
            self.range_code = "RA"
        self._configure()

    @command("Fixed range: the smallest range that holds this value (in the function's unit)")
    def set_range(self, value: float) -> None:
        if self.function is None:
            self.function = "dc_volts"
        ranges = FUNCTIONS[self.function][1]
        fitting = [code for code, full_scale in ranges if abs(value) <= full_scale]
        if not fitting:
            largest = f"{ranges[-1][1]:g} {UNITS[self.function]}" if ranges else "none"
            raise CommandError(f"no {self.function} range holds {value:g} "
                               f"(largest fixed range: {largest})")
        self.range_code = f"R{fitting[0]}"
        self._configure()

    @command("Choose the range automatically")
    def set_autorange(self) -> None:
        self.range_code = "RA"
        if self.function is not None:
            self._configure()

    @command("Resolution: 3, 4 or 5 (for 3½, 4½ or 5½ digits); fewer digits read faster",
             params={"digits": Param(choices=(3, 4, 5))})
    def set_digits(self, digits: int) -> None:
        self.digits = digits
        if self.function is not None:
            self._configure()

    @command("Switch autozero on (more accurate) or off (faster)")
    def set_autozero(self, on: bool) -> None:
        self.autozero = on
        if self.function is not None:
            self._configure()

    # --- measuring -------------------------------------------------------------------
    @command("Take one reading in the current function (infinite on overload)")
    def measure(self) -> float:
        if self.function is None:
            self.function = "dc_volts"
        return self._trigger_and_read()

    def _measure_as(self, function: str) -> float:
        if function != self.function:
            self.function, self.range_code = function, "RA"
        return self._trigger_and_read()

    @command("Measure DC voltage", unit="V")
    def measure_dc_voltage(self) -> float:
        return self._measure_as("dc_volts")

    @command("Measure AC voltage (true RMS)", unit="V")
    def measure_ac_voltage(self) -> float:
        return self._measure_as("ac_volts")

    @command("Measure resistance with two wires", unit="Ω")
    def measure_resistance(self) -> float:
        return self._measure_as("ohms_2wire")

    @command("Measure resistance with four wires (separate sense leads)", unit="Ω")
    def measure_resistance_4wire(self) -> float:
        return self._measure_as("ohms_4wire")

    @command("Measure DC current", unit="A")
    def measure_dc_current(self) -> float:
        return self._measure_as("dc_amps")

    @command("Measure AC current", unit="A")
    def measure_ac_current(self) -> float:
        return self._measure_as("ac_amps")

    # --- display and status ---------------------------------------------------------
    @command("Show up to 12 characters on the display (letters are shown in upper case)")
    def display_text(self, text: str) -> None:
        text = re.sub(r"[^ -_]", "", text.upper())[:12]  # the display knows ASCII 32-95
        self.transport.write(f"D2{text}")

    @command("Return the display to showing readings")
    def display_normal(self) -> None:
        self.transport.write("D1")

    @command("Is the front-panel FRONT/REAR switch set to the front terminals?")
    def front_terminals(self) -> bool:
        return self.transport.query("S").strip() == "1"

    @command("Self-test errors as text, or an empty text when there are none")
    def get_errors(self) -> str:
        reply = self.transport.query("E").strip()
        try:
            bits = int(reply, 8)
        except ValueError:
            raise CommandError(f"unexpected reply to E: {reply!r}") from None
        names = ["calibration", "RAM", "ROM", "A/D slope", "A/D self-test", "A/D link"]
        return ", ".join(name for i, name in enumerate(names) if bits & (1 << i))

    @command("Settings as the meter reports them, e.g. 'dc_volts, 5½ digits, autorange'")
    def get_status(self) -> str:
        self.transport.write("B")
        status = self.transport.read_raw(5)
        function = (status[0] >> 5) & 0x07
        digits = {1: "5½", 2: "4½", 3: "3½"}.get(status[0] & 0x03, "?")
        names = {f_code: name for name, (f_code, _) in FUNCTIONS.items()}
        parts = [names.get(function, f"function {function}"), f"{digits} digits",
                 "autorange" if status[1] & 0x02 else f"range {(status[0] >> 2) & 0x07}",
                 "autozero on" if status[1] & 0x04 else "autozero off",
                 "front terminals" if status[1] & 0x10 else "rear terminals"]
        return ", ".join(parts)

    def identify(self) -> str:
        return "HP 3478A: " + self.get_status()


class HP3478ASim(SimTransport):
    """A 3478A measuring a fixed set of inputs, with the front terminals selected."""

    INPUTS = {1: 5.00123, 2: 1.23456, 3: 1000.21, 4: 999.87, 5: 0.0123456, 6: 0.0567,
              7: 1.5e8}
    CODE = re.compile(r"F[1-7]|R-?[0-7]|RA|Z[01]|N[345]|T[1-5]|D1|D[23].*|M[0-7]{2}|[KBES]|"
                      r"H[0-7]")

    def __init__(self, **options):
        super().__init__(**options)
        self.function, self.range, self.autozero, self.digits = 1, None, True, 4
        self.trigger = 1
        self.syntax_error = False

    def _range_full_scale(self) -> float | None:
        if self.range is None:
            return None
        name = next(n for n, (f, _) in FUNCTIONS.items() if f == self.function)
        for code, full_scale in FUNCTIONS[name][1]:
            if code == self.range:
                return full_scale
        self.syntax_error = True
        return None

    def _reading(self) -> str:
        value = self.INPUTS[self.function]
        full_scale = self._range_full_scale()
        if full_scale is not None and abs(value) > full_scale * 1.2:
            return "+9.99999E+9"
        return format_reading(value)

    def _status(self) -> bytes:
        range_index = 0 if self.range is None else self.range + 3
        byte1 = (self.function << 5) | ((range_index & 0x07) << 2) | DIGIT_CODES[self.digits]
        byte2 = ((self.trigger == 1) | (self.range is None) << 1 | self.autozero << 2 | 1 << 4)
        return bytes([byte1, byte2, 0, 0, 32])

    def handle(self, line: str) -> str | bytes | None:
        text = re.sub(r"[ ,;]", "", line.upper())
        position, replies = 0, []
        while position < len(text):
            match = self.CODE.match(text, position)
            if match is None:
                self.syntax_error = True
                break
            code, position = match.group(), match.end()
            if code[0] == "F":
                self.function = int(code[1])
            elif code == "RA":
                self.range = None
            elif code[0] == "R":
                self.range = int(code[1:])
            elif code[0] == "Z":
                self.autozero = code == "Z1"
            elif code[0] == "N":
                self.digits = int(code[1])
            elif code[0] == "T":
                self.trigger = int(code[1])
                if self.trigger in (1, 3, 5):
                    replies = [self._reading()]
            elif code == "B":
                return self._status()
            elif code == "E":
                return "00"
            elif code == "S":
                return "1"
        return replies[-1] if replies else None
