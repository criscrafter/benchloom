# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Keysight (HP, Agilent) 34420A nanovolt / micro-ohm meter, and a simulator for it.

Written from the Keysight 34420A User's Guide, part no. 34420-90001, edition 8
(April 2026): chapter 4, Remote Interface Reference (pp. 139-237), and chapter 3 for
ranges, channels, resistance modes, temperature and defaults. Page numbers in the
comments are that guide's.

The driver uses the SENSe commands and READ? rather than MEASure?/CONFigure, so the
settings each channel and function keeps (range, NPLC, null) stay as set (p. 150).
"""

from __future__ import annotations

import math
import re

from benchloom import CommandError, Param, ScpiDevice, command
from benchloom.scpi import ScpiSimulator, SimError, sim_bool, sim_number
from benchloom.transports import read_line_within

OVERLOAD = 9.9e37  # "9.90000000E+37" (p. 84)
NPLC_CHOICES = (0.02, 0.2, 1, 2, 10, 20, 100, 200)  # p. 162
VOLT_RANGES = {1: (1e-3, 10e-3, 100e-3, 1.0, 10.0, 100.0),  # p. 34
               2: (1e-3, 10e-3, 100e-3, 1.0, 10.0)}
OHM_RANGES = (1.0, 10.0, 100.0, 1e3, 10e3, 100e3, 1e6)  # p. 89
OVERRANGE = 1.2  # a range reads to 120 % of its value (p. 85)
# function name -> (SENSe function string, the subsystem its settings live in)
FUNCTIONS = {
    "voltage": ("VOLT:DC", "VOLT:DC"),
    "ratio": ("VOLT:DC:RAT", "VOLT:DC"),
    "difference": ("VOLT:DC:DIFF", "VOLT:DC"),
    "ohms_2wire": ("RES", "RES"),
    "ohms_4wire": ("FRES", "FRES"),
    "temperature": ("TEMP", "TEMP"),
}
THERMOCOUPLES = ("B", "E", "J", "K", "N", "R", "S", "T")  # pp. 37, 94
# The guide gives no reading rates (its specifications are in a separate data sheet),
# so the wait for a reading is the integration time at 50 Hz (the slower line) with a
# factor of 2 for unknown overhead, doubled again where two inputs are measured.
SLOWEST_LINE_HZ = 50.0


class HP34420A(ScpiDevice):
    # --- life of the device -------------------------------------------------------------
    def connect(self) -> None:
        self.base_timeout = self.transport.timeout
        self.serial = getattr(self.transport, "scheme", "") == "serial"
        if self.serial:
            self.transport.configure(stopbits=2)  # fixed on the meter (p. 199)
            self.transport.write("SYST:REM")  # must come first over RS-232 (p. 205)
        identity = self.transport.query("*IDN?")
        if "34420A" not in identity:
            raise CommandError(f"the instrument answers *IDN? with {identity!r}, "
                               "not as a 34420A")
        self.reset()
        nplc = float(self.settings.get("nplc", 10))
        if nplc not in NPLC_CHOICES:
            raise CommandError(f"nplc = {nplc:g} in the device entry: the 34420A takes "
                               f"{', '.join(f'{n:g}' for n in NPLC_CHOICES)}")
        if nplc != 10:
            self.set_nplc(nplc)

    @command("Reset the meter (*RST): DC volts on channel 1, autorange, NPLC 10, filters, "
             "null and math off")
    def reset(self) -> None:
        self._settings("*RST", "*CLS")  # the remote defaults of p. 134
        self.function, self.channel = "voltage", 1
        self.autorange = {("VOLT:DC", 1): True, ("VOLT:DC", 2): True, ("RES", 0): True,
                          ("FRES", 0): True}
        self.nplc = {"VOLT:DC": 10.0, "RES": 10.0, "FRES": 10.0, "TEMP": 10.0}
        self.offset_compensation = False

    def close(self) -> None:
        if self.serial:
            self.transport.write("SYST:LOC")

    # --- helpers --------------------------------------------------------------------------
    def _settings(self, *commands: str) -> None:
        for text in commands:
            self.transport.write(text)
        self.check_errors()

    def _abort(self) -> None:
        """Stop a measurement: device clear, or Ctrl-C over RS-232 (p. 211)."""
        if self.serial:
            self.transport.write_raw(b"\x03")
        else:
            self.transport.device_clear()

    @property
    def _subsystem(self) -> str:
        return FUNCTIONS[self.function][1]

    def _range_key(self) -> tuple[str, int]:
        if self._subsystem == "VOLT:DC":
            return "VOLT:DC", self.channel if self.function == "voltage" else 1
        return self._subsystem, 0

    def _select(self, function: str, channel: int | None = None) -> None:
        commands = []
        if function != self.function:
            commands.append(f'FUNC "{FUNCTIONS[function][0]}"')
        if function == "voltage" and channel is not None and channel != self.channel:
            commands.append(f"ROUT:TERM FRON{channel}")
        if commands:
            self.function = function
            if channel is not None:
                self.channel = channel
            self._settings(*commands)

    def _seconds_per_reading(self) -> float:
        seconds = self.nplc[self._subsystem] / SLOWEST_LINE_HZ * 2
        if self.offset_compensation and self.function in ("ohms_2wire", "ohms_4wire"):
            seconds *= 2  # one reading with the current on and one off (p. 90)
        if self.function in ("ratio", "difference", "temperature"):
            seconds *= 2  # two inputs; a thermocouple also reads its reference
        if self.autorange.get(self._range_key(), False):
            seconds += 1.0
        return seconds + 0.1  # the automatic delay is at most 60 ms (p. 110)

    @staticmethod
    def _number(text: str) -> float:
        try:
            value = float(text)
        except ValueError:
            raise CommandError(f"unexpected reading from the 34420A: {text!r}") from None
        if abs(value) >= OVERLOAD:
            return math.copysign(math.inf, value)
        return value

    def _readings(self, count: int) -> list[float]:
        if count != 1:
            self._settings(f"SAMP:COUN {count}")
        seconds = self._seconds_per_reading() * count + 2.0
        try:
            self.transport.write("READ?")
            try:
                line = read_line_within(self.transport, seconds)
            except TimeoutError:
                self._abort()
                raise CommandError(f"no reading from the 34420A within {seconds:.0f} s") \
                    from None
        finally:
            if count != 1:
                self._settings("SAMP:COUN 1")
        readings = [self._number(part) for part in line.split(",")]  # p. 210
        if len(readings) != count:
            raise CommandError(f"asked for {count} readings, the 34420A sent {len(readings)}")
        return readings

    # --- measuring ------------------------------------------------------------------------
    @command("Take one reading in the current function and channel (infinite on overload)")
    def measure(self) -> float:
        return self._readings(1)[0]

    @command("Take several readings in a row (1 to 50000) in the current function",
             params={"count": Param(min=1, max=50000)})
    def measure_many(self, count: int) -> list:
        return self._readings(count)

    @command("Measure DC voltage on channel 1 (to 120 V) or channel 2 (to 12 V)", unit="V",
             params={"channel": Param(min=1, max=2)})
    def measure_voltage(self, channel: int = 1) -> float:
        self._select("voltage", channel)
        return self.measure()

    @command("Measure the ratio channel 1 / channel 2 (each less its null)")
    def measure_ratio(self) -> float:
        self._select("ratio")
        return self.measure()

    @command("Measure the difference channel 1 - channel 2 (each less its null)", unit="V")
    def measure_difference(self) -> float:
        self._select("difference")
        return self.measure()

    @command("Measure resistance with two wires", unit="Ω")
    def measure_resistance(self) -> float:
        self._select("ohms_2wire")
        return self.measure()

    @command("Measure resistance with four wires", unit="Ω")
    def measure_resistance_4wire(self) -> float:
        self._select("ohms_4wire")
        return self.measure()

    @command("Measure temperature with the probe set by set_rtd, set_thermistor or "
             "set_thermocouple, in °C", unit="°C")
    def measure_temperature(self) -> float:
        self._select("temperature")
        return self.measure()

    # --- configuration --------------------------------------------------------------------
    @command("Select what to measure; for voltage, set_channel chooses the input",
             params={"function": Param(choices=tuple(FUNCTIONS))})
    def set_function(self, function: str) -> None:
        self._select(function)

    @command("Select the input for voltage readings: channel 1 or 2",
             params={"channel": Param(min=1, max=2)})
    def set_channel(self, channel: int) -> None:
        self.channel = channel
        self._settings(f"ROUT:TERM FRON{channel}")

    def _fixed_range(self, ranges, value: float, unit: str) -> float:
        fitting = [r for r in ranges if abs(value) <= r * OVERRANGE]
        if not fitting:
            raise CommandError(f"no range holds {value:g} {unit} (the largest reads up to "
                               f"{ranges[-1] * OVERRANGE:g} {unit})")
        return fitting[0]

    @command("Fixed range for the current function: the smallest that holds this value "
             "(voltage: the channel in use, channel 1 for ratio and difference)")
    def set_range(self, value: float) -> None:
        if self.function == "temperature":
            raise CommandError("temperature has no range (p. 165)")
        subsystem, channel = self._range_key()
        if subsystem == "VOLT:DC":
            self._set_voltage_range(channel, value)
            return
        full_scale = self._fixed_range(OHM_RANGES, value, "Ω")
        self.autorange[(subsystem, 0)] = False
        self._settings(f"{subsystem}:RANG {full_scale:G}")

    @command("Fixed voltage range of one channel: the smallest that holds this value",
             params={"channel": Param(min=1, max=2), "volts": Param(unit="V")})
    def set_channel_range(self, channel: int, volts: float) -> None:
        self._set_voltage_range(channel, volts)

    def _set_voltage_range(self, channel: int, volts: float) -> None:
        full_scale = self._fixed_range(VOLT_RANGES[channel], volts, "V")
        self.autorange[("VOLT:DC", channel)] = False
        self._settings(f"SENS{channel}:VOLT:DC:RANG {full_scale:G}")

    @command("Choose the range automatically (voltage: the channel in use)")
    def set_autorange(self) -> None:
        if self.function == "temperature":
            return
        subsystem, channel = self._range_key()
        self.autorange[(subsystem, channel)] = True
        prefix = f"SENS{channel}:" if subsystem == "VOLT:DC" else ""
        self._settings(f"{prefix}{subsystem}:RANG:AUTO ON")

    @command("Integration time of the current function in power line cycles: 0.02, 0.2, "
             "1, 2, 10, 20, 100 or 200; only whole cycles reject line noise",
             params={"nplc": Param(choices=NPLC_CHOICES)})
    def set_nplc(self, nplc: float) -> None:
        self.nplc[self._subsystem] = float(nplc)
        self._settings(f"{self._subsystem}:NPLC {nplc:g}")

    @command("Offset compensation for 2- and 4-wire resistance (not on 100 kΩ and 1 MΩ); "
             "takes two readings, current on and off")
    def set_offset_compensation(self, on: bool) -> None:
        self.offset_compensation = on
        self._settings(f"FRES:OCOM {'ON' if on else 'OFF'}")

    @command("4-wire low power mode: less test current (cancels the voltage limit)")
    def set_low_power(self, on: bool) -> None:
        self._settings(f"FRES:POW:LIM {'ON' if on else 'OFF'}")

    @command("4-wire voltage limit on the 10 Ω and 100 Ω ranges: 20, 100 or 500 mV, or 0 "
             "for none (cancels low power; may change the range)",
             params={"millivolts": Param(choices=(0, 20, 100, 500))})
    def set_voltage_limit(self, millivolts: int) -> None:
        if not millivolts:
            self._settings("FRES:VOLT:LIM OFF")
            return
        self._settings(f"FRES:VOLT:LIM:VAL {millivolts / 1000:g}", "FRES:VOLT:LIM ON")

    @command("Null: true uses the stored null value, false turns null off")
    def set_null(self, on: bool) -> None:
        self._settings(f"NULL {'ON' if on else 'OFF'}")

    @command("Null on the next reading: it becomes the null value of this channel or function")
    def null_next_reading(self) -> None:
        self._settings("NULL ONCE")

    @command("Use a 4-wire RTD: type 85 (alpha 0.00385, DIN/IEC 751) or 91 (0.00391), "
             "and its R0", params={"rtd_type": Param(choices=(85, 91)),
                                   "r0": Param(unit="Ω", min=4.9, max=2100)})
    def set_rtd(self, rtd_type: int = 85, r0: float = 100.0) -> None:
        self._settings("TEMP:TRAN:TYPE FRTD", f"TEMP:TRAN:FRTD:TYPE {rtd_type}",
                       f"TEMP:TRAN:FRTD:RES {r0:g}", "UNIT:TEMP C")

    @command("Use the 5 kΩ thermistor (on channel 1)")
    def set_thermistor(self) -> None:
        self._settings("TEMP:TRAN:TYPE THER", "UNIT:TEMP C")

    @command("Use a thermocouple (on channel 2) and its reference junction: internal, "
             "thermistor (on channel 1) or fixed at reference_c (0 to 55 °C)",
             params={"kind": Param(choices=THERMOCOUPLES),
                     "reference": Param(choices=("internal", "thermistor", "fixed")),
                     "reference_c": Param(unit="°C", min=0, max=55)})
    def set_thermocouple(self, kind: str, reference: str = "internal",
                         reference_c: float = 0.0) -> None:
        commands = ["TEMP:TRAN:TYPE TC", f"TEMP:TRAN:TC:TYPE {kind}",
                    "TEMP:TRAN:TC:RJUN:TYPE "
                    + {"internal": "INT", "thermistor": "THER", "fixed": "FIX"}[reference]]
        if reference == "fixed":
            commands.append(f"TEMP:TRAN:TC:RJUN {reference_c:g}")
        self._settings(*commands, "UNIT:TEMP C")

    # --- display --------------------------------------------------------------------------
    @command("Show up to 11 characters on the display")
    def display_text(self, text: str) -> None:
        text = text.replace('"', "").replace("'", "")[:11]
        self._settings(f'DISP:TEXT "{text}"')

    @command("Clear the message from the display")
    def display_normal(self) -> None:
        self._settings("DISP:TEXT:CLE")


class HP34420ASim(ScpiSimulator):
    """A 34420A on a bench: 1.2345 mV on channel 1, 0.6 mV on channel 2, a 100 Ω
    four-wire resistor, a 23 °C probe."""

    identity = "HEWLETT-PACKARD,34420A,0,1.0-1.0-1.0"  # the format of p. 115
    INPUTS = {1: 1.2345e-3, 2: 0.6e-3}
    OHMS = 100.0123
    TEMPERATURE = 23.0

    def __init__(self, **options):
        self.remote_first = None  # whether SYST:REM came before anything else
        super().__init__(**options)

    def reset(self) -> None:
        self.function, self.channel = "VOLT:DC", 1
        self.ranges = {("VOLT:DC", 1): None, ("VOLT:DC", 2): None, ("RES", 0): None,
                       ("FRES", 0): None}  # None = autorange
        self.nplc = {"VOLT:DC": 10.0, "RES": 10.0, "FRES": 10.0, "TEMP": 10.0}
        self.ocomp = self.low_power = self.volt_limit = False
        self.volt_limit_value = 0.02
        self.null = False
        self.samples = 1
        self.probe = {"TYPE": "FRTD"}
        self.display = ""

    def handle(self, line: str):
        if self.remote_first is None:
            self.remote_first = line.strip().upper() == "SYST:REM"
        return super().handle(line)

    def _reading(self) -> float:
        if self.function == "VOLT:DC":
            value, limit = self.INPUTS[self.channel], self.ranges[("VOLT:DC", self.channel)]
        elif self.function == "VOLT:DC:RAT":
            value, limit = self.INPUTS[1] / self.INPUTS[2], None
        elif self.function == "VOLT:DC:DIFF":
            value, limit = self.INPUTS[1] - self.INPUTS[2], None
        elif self.function in ("RES", "FRES"):
            value, limit = self.OHMS, self.ranges[(self.function, 0)]
        else:
            value, limit = self.TEMPERATURE, None
        if limit is not None and abs(value) > limit * OVERRANGE:
            value = OVERLOAD
        return value

    def _read(self, arg):
        return ",".join(f"{self._reading():+.8E}" for _ in range(self.samples))

    def _function(self, arg):
        name = arg.strip().strip('"').upper()
        if name not in {f for f, _ in FUNCTIONS.values()}:
            raise SimError(-224, "Illegal parameter value")
        self.function = name

    def _terminals(self, arg):
        names = {"FRON": 1, "FRON1": 1, "FRONT": 1, "FRONT1": 1, "FRON2": 2, "FRONT2": 2}
        if arg.upper() not in names:
            raise SimError(-224, "Illegal parameter value")
        self.channel = names[arg.upper()]

    def _range(self, key, ranges):
        def handler(arg):
            value = sim_number(arg, 0, max(ranges))
            self.ranges[key] = next(r for r in ranges if value <= r)
        return handler

    def _autorange(self, key):
        def handler(arg):
            if sim_bool(arg):
                self.ranges[key] = None
        return handler

    def _nplc(self, subsystem):
        def handler(arg):
            value = float(arg) if re.fullmatch(r"[\d.Ee+-]+", arg) else None
            if value not in NPLC_CHOICES:
                raise SimError(-224, "Illegal parameter value")
            self.nplc[subsystem] = value
        return handler

    def _set(self, name, convert):
        def handler(arg):
            setattr(self, name, convert(arg))
        return handler

    def _probe(self, key, allowed=None):
        def handler(arg):
            value = arg.upper()
            if allowed is not None and value not in allowed:
                raise SimError(-224, "Illegal parameter value")
            self.probe[key] = value
        return handler

    def _null(self, arg):
        if arg.upper() not in ("ON", "OFF", "ONCE"):
            raise SimError(-224, "Illegal parameter value")
        self.null = arg.upper() != "OFF"

    def _display(self, arg):
        self.display = arg.strip('"')[:11]

    def routes(self):
        volt = [(rf"SENS{c}:VOLT:DC:RANG", self._range(("VOLT:DC", c), VOLT_RANGES[c]))
                for c in (1, 2)]
        volt += [(rf"SENS{c}:VOLT:DC:RANG:AUTO", self._autorange(("VOLT:DC", c)))
                 for c in (1, 2)]
        ohms = [(rf"{s}:RANG", self._range((s, 0), OHM_RANGES)) for s in ("RES", "FRES")]
        ohms += [(rf"{s}:RANG:AUTO", self._autorange((s, 0))) for s in ("RES", "FRES")]
        nplc = [(rf"{s}:NPLC", self._nplc(s)) for s in ("VOLT:DC", "RES", "FRES", "TEMP")]
        return volt + ohms + nplc + [
            (r"SYST:REM|SYST:LOC", lambda arg: None),
            (r"READ\?", self._read),
            (r"FUNC", self._function),
            (r"ROUT:TERM", self._terminals),
            (r"SAMP:COUN", self._set("samples", lambda a: int(sim_number(a, 1, 50000)))),
            (r"FRES:OCOM", self._set("ocomp", sim_bool)),
            (r"FRES:POW:LIM", self._set("low_power", sim_bool)),
            (r"FRES:VOLT:LIM", self._set("volt_limit", sim_bool)),
            (r"FRES:VOLT:LIM:VAL", self._set("volt_limit_value",
                                             lambda a: sim_number(a, 0.02, 0.5))),
            (r"NULL", self._null),
            (r"TEMP:TRAN:TYPE", self._probe("TYPE", ("FRTD", "THER", "TC"))),
            (r"TEMP:TRAN:FRTD:TYPE", self._probe("RTD", ("85", "91"))),
            (r"TEMP:TRAN:FRTD:RES", self._probe("R0")),
            (r"TEMP:TRAN:TC:TYPE", self._probe("TC", THERMOCOUPLES)),
            (r"TEMP:TRAN:TC:RJUN:TYPE", self._probe("RJUN", ("FIX", "INT", "THER"))),
            (r"TEMP:TRAN:TC:RJUN", self._probe("RJUN_C")),
            (r"UNIT:TEMP", self._probe("UNIT", ("C", "CEL", "F", "FAR", "K"))),
            (r"DISP:TEXT", self._display),
            (r"DISP:TEXT:CLE", lambda arg: setattr(self, "display", "")),
        ]
