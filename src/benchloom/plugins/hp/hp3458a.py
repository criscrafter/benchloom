# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""HP / Agilent / Keysight 3458A multimeter, and a simulator for it.

The 3458A speaks HP's own multimeter language (HP-ML), not SCPI: commands such as
"FUNC DCV,10", "NPLC 100" or "TARM SGL", queries such as "ID?" and "ERR?", several
of them in one line separated by semicolons. Commands, ranges, the error register and
the power-on states follow the Keysight 3458A Benutzerhandbuch (part no. 03458-90511,
edition 10, March 2023).

The driver keeps the meter waiting (TARM HOLD, TRIG AUTO) and asks for a reading
with TARM SGL: the meter then takes NRDGS readings and sends them, one per line.
Long integration times make a reading slow (NPLC 100 with autozero is 4 s at 50 Hz),
so the driver works out how long to wait from the settings.
"""

from __future__ import annotations

import math
import re
import time

from benchloom import CommandError, Device, Param, command
from benchloom.transports import SimTransport

# function name -> (HP-ML name, ranges by full scale)
_ACV_RANGES = (10e-3, 100e-3, 1.0, 10.0, 100.0, 1000.0)
FUNCTIONS = {
    "dc_volts": ("DCV", (100e-3, 1.0, 10.0, 100.0, 1000.0)),
    "ac_volts": ("ACV", _ACV_RANGES),
    "acdc_volts": ("ACDCV", _ACV_RANGES),
    "ohms_2wire": ("OHM", (10.0, 100.0, 1e3, 10e3, 100e3, 1e6, 10e6, 100e6, 1e9)),
    "ohms_4wire": ("OHMF", (10.0, 100.0, 1e3, 10e3, 100e3, 1e6, 10e6, 100e6, 1e9)),
    "dc_amps": ("DCI", (100e-9, 1e-6, 10e-6, 100e-6, 1e-3, 10e-3, 100e-3, 1.0)),
    "ac_amps": ("ACI", (100e-6, 1e-3, 10e-3, 100e-3, 1.0)),
    "acdc_amps": ("ACDCI", (100e-6, 1e-3, 10e-3, 100e-3, 1.0)),
    "frequency": ("FREQ", _ACV_RANGES),  # the range is that of the input voltage
    "period": ("PER", _ACV_RANGES),
}
UNITS = {"dc_volts": "V", "ac_volts": "V", "acdc_volts": "V", "ohms_2wire": "Ω",
         "ohms_4wire": "Ω", "dc_amps": "A", "ac_amps": "A", "acdc_amps": "A",
         "frequency": "V", "period": "V"}  # the unit of the range, not of the reading
AC_FUNCTIONS = {"ac_volts", "acdc_volts", "ac_amps", "acdc_amps", "frequency", "period"}
OVERLOAD = 1e38  # what the meter sends for an overload (±1.0E+38)
# bits of the error register (ERR?), bit 0 first
ERROR_BITS = ["hardware error (see AUXERR?)", "calibration error", "trigger too fast",
              "syntax error", "command not allowed over the bus", "undefined parameter",
              "parameter out of range", "memory error",
              "overload that could damage the meter", "out of calibration",
              "calibration required", "settings conflict (sub-sampling memory)",
              "math error", "subprogram error", "system error"]
# the manual gives 16 min (ALL), 2:45 (DCV, AC) and 11 min (OHMS); with a margin
AUTOCAL_SECONDS = {"all": 1500, "dcv": 300, "ac": 300, "ohms": 1000}
# a range reads up to 120 % of its nominal value, the top voltage and current ranges less
TOP_LIMITS = {1000.0: 1050.0, 1.0: 1.05}


def range_limit(function: str, full_scale: float) -> float:
    """The largest value a range measures: 120 % of it, 1050 V and 1.05 A on the top ranges."""
    if full_scale == FUNCTIONS[function][1][-1] and full_scale in TOP_LIMITS \
            and UNITS[function] in ("V", "A"):
        return TOP_LIMITS[full_scale]
    return full_scale * 1.2


def decode_errors(bits: int) -> str:
    return ", ".join(name for i, name in enumerate(ERROR_BITS) if bits & (1 << i))


class HP3458A(Device):
    # --- life of the device -------------------------------------------------------------
    def connect(self) -> None:
        self.base_timeout = self.transport.timeout
        self.function = "dc_volts"
        self.range: float | None = None  # None = autorange
        self.nplc = float(self.settings.get("nplc", 10))
        self.digits = int(self.settings.get("digits", 8))
        self.autozero = True
        self.offset_compensation = False
        # After power-on, or after a test that left it measuring (TARM AUTO), the meter
        # may be busy or have readings waiting; a device clear stops that and empties
        # its output, as the manual advises before RESET.
        self.transport.device_clear()
        self.transport.clear_input()
        # At power-on END is OFF: replies end without EOI, which VISA and USBTMC wait
        # for. So switch EOI on before the first query.
        self.transport.write("END ALWAYS")
        model = self.transport.query("ID?").strip()
        if "3458" not in model:
            raise CommandError(f"the instrument answers ID? with {model!r}, not as a 3458A")
        self.transport.query("ERR?")  # forget errors left from before
        self._settings("PRESET NORM", "END ALWAYS", "OFORMAT ASCII", "MEM OFF", "MATH OFF",
                       "TARM HOLD", "TRIG AUTO", "NRDGS 1,AUTO", "FUNC DCV,AUTO",
                       f"NPLC {self.nplc:g}", f"NDIG {self.digits}")
        try:
            self.line_frequency = float(self.transport.query("LINE?"))
        except (ValueError, TimeoutError):
            self.line_frequency = 50.0
        if not 40 <= self.line_frequency <= 70:
            self.line_frequency = 50.0

    def close(self) -> None:
        # Let the meter measure on its own again, as after power-on.
        self.transport.write("TARM AUTO")

    def identify(self) -> str:
        model = self.transport.query("ID?").strip()
        revision = self.transport.query("REV?").strip()
        return f"{model}, firmware {revision}"

    # --- helpers --------------------------------------------------------------------------
    def check_errors(self) -> None:
        if not self.settings.get("check_errors", True):
            return
        reply = self.transport.query("ERR?")
        try:
            bits = int(float(reply))
        except ValueError:
            raise CommandError(f"unexpected reply to ERR?: {reply!r}") from None
        if bits:
            raise CommandError(f"the 3458A reported: {decode_errors(bits)}")

    def _settings(self, *commands: str) -> None:
        """Send settings, then check the error register once."""
        self._delay_now = None  # the automatic delay follows function, range, NPLC...
        for text in commands:
            self.transport.write(text)
        self.check_errors()

    def _current_delay(self) -> float:
        """The settling delay the meter uses now, in seconds (DELAY? also answers when
        the delay is automatic). Asked again only after a setting changed."""
        if self._delay_now is None:
            try:
                self._delay_now = max(0.0, float(self.transport.query("DELAY?")))
            except (ValueError, TimeoutError):
                self._delay_now = 5.0  # unknown: assume a long one
        return self._delay_now

    def _seconds_per_reading(self) -> float:
        """A generous estimate of how long one reading takes with the present settings.

        The meter splits integrations over 10 power line cycles into parts of NPLC 10.
        Offset compensation measures each part with the current on and off and waits
        the settling delay before both, so NPLC 100 with OCOMP and a 2 s delay takes
        about 48 s (found by 3458A users and explained on the EEVblog forum).
        """
        integration = max(self.nplc, 0.01) / self.line_frequency
        phases = 2 if self.autozero else 1  # every reading also measures the zero
        delays = 1
        if self.offset_compensation and self.function in ("ohms_2wire", "ohms_4wire"):
            phases *= 2
            delays = 2 * max(1, math.ceil(self.nplc / 10))
        delay = self._current_delay()
        seconds = integration * phases + delays * delay
        if self.function in AC_FUNCTIONS:
            seconds += 3.0  # AC conversion settles; frequency waits for its gate
        if self.range is None:
            seconds += 1.0 + delay  # autorange may take a few tries
        return seconds * 1.2 + 1.0

    def _read_line(self, seconds: float, what: str) -> str:
        """Wait up to ``seconds`` for one line from the meter.

        A Prologix adapter gives up on a read after 3 s (an AR488 after 32 s), so for
        long waits the read is repeated until the time is up.
        """
        max_ms = getattr(self.transport, "max_read_ms", None)
        step = min(seconds, max_ms / 1000) if max_ms else seconds
        deadline = time.monotonic() + seconds
        self.transport.set_timeout(max(self.base_timeout, step))
        try:
            while True:
                try:
                    return self.transport.read()
                except TimeoutError:
                    if not max_ms or time.monotonic() >= deadline:
                        break
        finally:
            self.transport.set_timeout(self.base_timeout)
        self.transport.device_clear()  # aborts the reading in progress
        raise CommandError(f"no {what} from the 3458A within {seconds:.0f} s")

    @staticmethod
    def _number(reply: str) -> float:
        try:
            value = float(reply)
        except ValueError:
            raise CommandError(f"unexpected reading from the 3458A: {reply!r}") from None
        if abs(value) >= OVERLOAD:
            return math.copysign(math.inf, value)
        return value

    def _select(self, function: str) -> None:
        if function != self.function:
            self.function, self.range = function, None
            self._settings(f"FUNC {FUNCTIONS[function][0]},AUTO")

    def _readings(self, count: int) -> list[float]:
        if count != 1:
            self._settings(f"NRDGS {count},AUTO")
        try:
            per_reading = self._seconds_per_reading()  # may ask DELAY?: before the trigger
            self.transport.write("TARM SGL")
            return [self._number(self._read_line(per_reading, "reading"))
                    for _ in range(count)]
        finally:
            if count != 1:
                self.transport.write("NRDGS 1,AUTO")

    # --- configuration --------------------------------------------------------------------
    @command("Select what to measure", params={"function": Param(choices=tuple(FUNCTIONS))})
    def set_function(self, function: str) -> None:
        self.function, self.range = function, None
        self._settings(f"FUNC {FUNCTIONS[function][0]},AUTO")

    @command("Fixed range: the smallest range that holds this value (in the function's "
             "unit; for frequency and period, the input voltage)")
    def set_range(self, value: float) -> None:
        name, ranges = FUNCTIONS[self.function]
        fitting = [full_scale for full_scale in ranges
                   if abs(value) <= range_limit(self.function, full_scale)]
        if not fitting:
            largest = range_limit(self.function, ranges[-1])
            raise CommandError(f"no {self.function} range holds {value:g} (the largest "
                               f"reads up to {largest:g} {UNITS[self.function]})")
        self.range = fitting[0]
        self._settings(f"FUNC {name},{self.range:G}")

    @command("Choose the range automatically")
    def set_autorange(self) -> None:
        self.range = None
        self._settings(f"FUNC {FUNCTIONS[self.function][0]},AUTO")

    @command("Integration time in power line cycles: more is quieter and slower "
             "(0 to 1000; 10 or more for 7½ digits and up, 100 for the full 8½)",
             params={"nplc": Param(min=0, max=1000)})
    def set_nplc(self, nplc: float) -> None:
        self.nplc = nplc
        self._settings(f"NPLC {nplc:g}")

    @command("Digits on the display: 3 to 8 (8 = 8½ digits); readings sent keep the full "
             "resolution of the integration time",
             params={"digits": Param(min=3, max=8)})
    def set_digits(self, digits: int) -> None:
        self.digits = digits
        self._settings(f"NDIG {digits}")

    @command("Switch autozero on (more accurate, half the speed) or off")
    def set_autozero(self, on: bool) -> None:
        self.autozero = on
        self._settings("AZERO ON" if on else "AZERO OFF")

    @command("Offset compensation for resistance: cancels thermal voltages, half the speed")
    def set_offset_compensation(self, on: bool) -> None:
        self.offset_compensation = on
        self._settings("OCOMP ON" if on else "OCOMP OFF")

    @command("DC volts input impedance: true = fixed 10 MΩ, false = over 10 GΩ on the "
             "100 mV to 10 V ranges (on, it stays across resistance readings and skews them)")
    def set_fixed_impedance(self, on: bool) -> None:
        self._settings("FIXEDZ ON" if on else "FIXEDZ OFF")

    @command("Lowest frequency expected in AC measurements (the low end of ACBAND)",
             params={"hertz": Param(unit="Hz", min=1, max=10e6)})
    def set_ac_bandwidth(self, hertz: float) -> None:
        self._settings(f"ACBAND {hertz:g}")

    @command("AC volts method: analog (fast), random (sampled, to 10 MHz) or sync "
             "(sampled, most accurate on repetitive signals)",
             params={"method": Param(choices=("analog", "random", "sync"))})
    def set_ac_method(self, method: str) -> None:
        self._settings("SETACV " + {"analog": "ANA", "random": "RNDM", "sync": "SYNC"}[method])

    @command("Settling delay between trigger and reading; 0 = the shortest possible. With "
             "offset compensation it comes twice per 10 power line cycles",
             params={"seconds": Param(unit="s", min=0, max=6000)})
    def set_delay(self, seconds: float) -> None:
        self._settings(f"DELAY {seconds:g}")

    @command("Let the meter choose the settling delay for the function and range (the default)")
    def set_auto_delay(self) -> None:
        self._settings("DELAY -1")

    # --- measuring ------------------------------------------------------------------------
    @command("Take one reading in the current function (infinite on overload)")
    def measure(self) -> float:
        return self._readings(1)[0]

    @command("Take several readings in a row in the current function",
             params={"count": Param(min=1, max=10000)})
    def measure_many(self, count: int) -> list:
        return self._readings(count)

    @command("Measure DC voltage", unit="V")
    def measure_dc_voltage(self) -> float:
        self._select("dc_volts")
        return self.measure()

    @command("Measure AC voltage (true RMS of the AC part)", unit="V")
    def measure_ac_voltage(self) -> float:
        self._select("ac_volts")
        return self.measure()

    @command("Measure AC + DC voltage (true RMS)", unit="V")
    def measure_acdc_voltage(self) -> float:
        self._select("acdc_volts")
        return self.measure()

    @command("Measure resistance with two wires", unit="Ω")
    def measure_resistance(self) -> float:
        self._select("ohms_2wire")
        return self.measure()

    @command("Measure resistance with four wires (separate sense leads)", unit="Ω")
    def measure_resistance_4wire(self) -> float:
        self._select("ohms_4wire")
        return self.measure()

    @command("Measure DC current", unit="A")
    def measure_dc_current(self) -> float:
        self._select("dc_amps")
        return self.measure()

    @command("Measure AC current (true RMS of the AC part)", unit="A")
    def measure_ac_current(self) -> float:
        self._select("ac_amps")
        return self.measure()

    @command("Measure AC + DC current (true RMS)", unit="A")
    def measure_acdc_current(self) -> float:
        self._select("acdc_amps")
        return self.measure()

    @command("Measure the frequency of the voltage at the input", unit="Hz")
    def measure_frequency(self) -> float:
        self._select("frequency")
        return self.measure()

    @command("Measure the period of the voltage at the input", unit="s")
    def measure_period(self) -> float:
        self._select("period")
        return self.measure()

    # --- calibration, display and status --------------------------------------------------
    @command("The meter's internal temperature, to compare with that of its last autocal",
             unit="°C")
    def get_temperature(self) -> float:
        return self._number(self.transport.query("TEMP?"))

    @command("Run the autocal and wait for it: all (16 min), dcv or ac (3 min), ohms (11 min); "
             "disconnect the inputs, and let the meter settle 15-30 min before measuring",
             params={"kind": Param(choices=tuple(AUTOCAL_SECONDS))})
    def autocal(self, kind: str = "all") -> None:
        seconds = AUTOCAL_SECONDS[kind]
        # With its input buffer off (the default) the meter holds the bus while it works,
        # so a command sent during the autocal could time out. With INBUF ON it takes
        # the whole line at once and frees the bus; ERR? is answered once the autocal
        # is done. The long timeout on the write covers a meter that holds the bus anyway.
        self.transport.write("INBUF ON")
        try:
            self.transport.set_timeout(max(self.base_timeout, seconds))
            try:
                self.transport.write(f"ACAL {kind.upper()};ERR?")
            finally:
                self.transport.set_timeout(self.base_timeout)
            reply = self._read_line(seconds, "end of the autocal")
        finally:
            self.transport.write("INBUF OFF")
        try:
            bits = int(float(reply))
        except ValueError:
            raise CommandError(f"unexpected reply to ERR?: {reply!r}") from None
        if bits:
            raise CommandError(f"autocal failed: {decode_errors(bits)}")

    @command("Show a message on the display (up to 75 characters; letters, digits, spaces "
             "and ! # $ % & ( ) ^ \\ / @ ; : [ ] , . + - = * < > ? _, others are left out)")
    def display_text(self, text: str) -> None:
        text = re.sub(r"[^A-Za-z0-9 !#$%&()^\\/@;:\[\],.+\-=*<>?_]", "", text)[:75]
        self._settings(f'DISP MSG,"{text}"')

    @command("Return the display to showing readings")
    def display_normal(self) -> None:
        self._settings("DISP ON")

    @command("Which input terminals are selected: front, rear or open")
    def get_terminals(self) -> str:
        reply = self.transport.query("TERM?").strip().upper()
        names = {"0": "open", "1": "front", "2": "rear",
                 "OPEN": "open", "FRONT": "front", "REAR": "rear"}
        if reply not in names:
            raise CommandError(f"unexpected reply to TERM?: {reply!r}")
        return names[reply]

    @command("Errors since the last check, as text; an empty text when there are none")
    def get_errors(self) -> str:
        reply = self.transport.query("ERR?")
        try:
            return decode_errors(int(float(reply)))
        except ValueError:
            raise CommandError(f"unexpected reply to ERR?: {reply!r}") from None

    @command("Send a raw HP-ML command")
    def write(self, text: str) -> None:
        self._delay_now = None  # it may have changed what the delay is
        self.transport.write(text)

    @command("Send a raw HP-ML query and return the reply")
    def query(self, text: str) -> str:
        return self.transport.query(text)


def format_reading(value: float, digits: int) -> str:
    """Format a value roughly the way the 3458A sends it, e.g. ' 1.00000123E+01'."""
    if math.isinf(value):
        return f"{math.copysign(OVERLOAD, value): .{digits}E}"
    return f"{value: .{digits}E}"


class HP3458ASim(SimTransport):
    """A 3458A measuring a fixed set of inputs (a 10 V reference, a 10 kΩ resistor...)."""

    INPUTS = {"DCV": 10.0000123, "ACV": 1.0000456, "ACDCV": 1.0000789, "OHM": 10000.212,
              "OHMF": 9999.9876, "DCI": 1.00000234e-3, "ACI": 0.500012e-3,
              "ACDCI": 0.500034e-3, "FREQ": 1000.00012, "PER": 1 / 1000.00012}
    RANGES = {name: ranges for name, ranges in FUNCTIONS.values()}
    AUTO_DELAY = 0.03  # seconds
    SIMPLE = {"END": ("OFF", "ON", "ALWAYS"), "OFORMAT": ("ASCII", "SINT", "DINT", "SREAL",
                                                           "DREAL"),
              "MEM": ("OFF", "LIFO", "FIFO", "CONT"), "MATH": ("OFF",),
              "AZERO": ("ON", "OFF", "ONCE"), "OCOMP": ("ON", "OFF"),
              "FIXEDZ": ("ON", "OFF"), "SETACV": ("ANA", "RNDM", "SYNC"),
              "INBUF": ("ON", "OFF"),
              "TRIG": ("AUTO", "EXT", "SGL", "HOLD", "SYN", "LEVEL", "LINE"),
              "TARM": ("AUTO", "EXT", "SGL", "HOLD", "SYN")}

    def __init__(self, line_hz="50", **options):
        super().__init__(**options)
        self.line_hz = float(line_hz)
        self.state = {"END": "OFF"}  # the power-on state; PRESET leaves END alone
        self.preset()
        self.state["TRIG"] = "AUTO"
        self.nplc, self.ndig = 10.0, 7
        self.errors = 0
        self.replies_without_eoi = []  # queries answered while END was OFF
        # TARM AUTO, TRIG AUTO: the meter is measuring, and a reading waits to be read
        self.reply(self._reading())

    def device_clear(self) -> None:
        self.clear_input()  # stops a reading and empties the output, as on the meter

    def preset(self) -> None:
        """PRESET NORM, as far as the simulator keeps it."""
        self.function, self.range = "DCV", None
        self.state.update({"TARM": "AUTO", "TRIG": "SYN", "AZERO": "ON", "OCOMP": "OFF",
                           "FIXEDZ": "OFF", "OFORMAT": "ASCII", "MEM": "OFF"})
        self.nplc, self.ndig, self.nrdgs = 1.0, 6, 1
        self.delay: float | None = None  # None = automatic (DELAY -1)
        self.message = None

    def _error(self, bit: int) -> None:
        self.errors |= 1 << bit

    @staticmethod
    def _limit(code: str, full_scale: float) -> float:
        name = next(n for n, (c, _) in FUNCTIONS.items() if c == code)
        return range_limit(name, full_scale)

    def _reading(self) -> str:
        value = self.INPUTS[self.function]
        if abs(value) > self._limit(self.function,
                                    self.range or max(self.RANGES[self.function])):
            value = math.inf
        return format_reading(value, 9)  # NDIG sets only the display

    def _number(self, arg: str, low: float, high: float) -> float | None:
        try:
            value = float(arg)
        except ValueError:
            self._error(5)
            return None
        if not low <= value <= high:
            self._error(6)
            return None
        return value

    def _func(self, args: list[str]) -> None:
        name = args[0] if args and args[0] else self.function
        if name not in self.RANGES:
            self._error(5)
            return
        ranges = self.RANGES[name]
        if len(args) < 2 or args[1] == "AUTO":
            self.function, self.range = name, None
            return
        value = self._number(args[1], 0, self._limit(name, max(ranges)))
        if value is None:
            return
        self.function = name
        self.range = next(r for r in ranges if value <= self._limit(name, r))

    def command(self, header: str, args: list[str]) -> str | None:
        if header == "ID?":
            return "Keysight 3458A"  # older meters answer "HP3458A"
        if header == "REV?":
            return "9,2"
        if header == "ERR?":
            errors, self.errors = self.errors, 0
            return str(errors)
        if header == "LINE?":
            return f"{self.line_hz:.4f}"
        if header == "TEMP?":
            return "36.2"
        if header == "DELAY?":  # answers the automatic delay too; the real one varies
            return f"{self.AUTO_DELAY if self.delay is None else self.delay:.7E}"
        if header == "DELAY":
            if args and args[0] == "-1":
                self.delay = None
            else:
                value = self._number(args[0] if args else "", 0, 6000)
                if value is not None:
                    self.delay = value
            return None
        if header == "TERM?":
            return "1"  # front; 2 = rear
        if header in ("PRESET", "RESET"):
            self.preset()
        elif header in self.RANGES:  # "DCV 10" is short for "FUNC DCV,10"
            self._func([header, *args])
        elif header == "FUNC":
            self._func(args)
        elif header == "NPLC":
            value = self._number(args[0] if args else "", 0, 1000)
            if value is not None:
                self.nplc = value
        elif header == "NDIG":
            value = self._number(args[0] if args else "", 3, 8)
            if value is not None:
                self.ndig = int(value)
        elif header == "NRDGS":
            value = self._number(args[0] if args else "", 1, 16777215)
            if value is not None:
                self.nrdgs = int(value)
        elif header == "ACBAND":
            self._number(args[0] if args else "", 1, 10e6)
        elif header == "ACAL":
            if not args or args[0] not in ("ALL", "DCV", "AC", "OHMS"):
                self._error(5)
        elif header == "DISP":
            if args and args[0] == "MSG" and len(args) > 1:
                self.message = ",".join(args[1:]).strip('"')
            elif args and args[0] in ("ON", "OFF"):
                self.message = None
            else:
                self._error(5)
        elif header in self.SIMPLE:
            if not args or args[0] not in self.SIMPLE[header]:
                self._error(5)
                return None
            self.state[header] = args[0]
            if header == "TARM" and args[0] == "SGL" and self.state["TRIG"] == "AUTO":
                return "\r\n".join(self._reading() for _ in range(self.nrdgs))
        else:
            self._error(3)  # the manual does not say; taken as a syntax error
        return None

    def handle(self, line: str) -> str | None:
        replies = []
        for part in re.findall(r"(?:'[^']*'|\"[^\"]*\"|[^;'\"])+", line):  # ; outside quotes
            match = re.match(r"\s*([A-Za-z?]+)\s*(.*)", part)
            if not match:
                if part.strip():
                    self._error(3)
                continue
            header = match.group(1).upper()
            rest = match.group(2).strip()
            if header == "DISP":  # keep the message as it was written
                args = [a.strip() for a in rest.split(",", 1)] if rest else []
                if args:
                    args[0] = args[0].upper()
            else:
                args = [a.strip() for a in rest.upper().split(",")] if rest else []
            reply = self.command(header, args)
            if reply is not None:
                replies.append(reply)
                if self.state["END"] == "OFF":
                    self.replies_without_eoi.append(header)
        return "\r\n".join(replies) if replies else None
