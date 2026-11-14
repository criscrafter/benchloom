# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Fluke 8508A reference multimeter, and a simulator for it.

Written from the Fluke 8508A Reference Multimeter Users Manual, PN 1673798 (July 2002,
Rev. 6, 3/13): chapter 4, Remote Operations Using the IEEE 488 Interface, chapter 5
for ranges and read rates, and appendix A for the error codes. Page numbers in the
comments are that manual's.

A function command sets the function and its options in one go, for example
"DCV AUTO,RESL8,FILT_OFF,FAST_OFF,TWO_WR" (pp. 4-10 to 4-17). The driver selects
external triggering (TRG_SRCE EXT), as the manual advises against internal triggers
under bus control (p. 4-33), and takes each reading with X?, which triggers one
reading and returns it (p. 4-34).
"""

from __future__ import annotations

import math
import re

from benchloom import CommandError, Device, Param, command
from benchloom.transports import SimTransport, read_line_within

OVERLOAD = 200e33  # "±200.0000E+33" (p. 4-37)
# function -> (header, ranges by full scale (pp. 5-5 to 5-13), resolutions, options)
FUNCTIONS = {
    "dc_volts": ("DCV", (199.99e-3, 1.9999, 19.999, 199.99, 1050.0), (5, 6, 7, 8)),
    "ac_volts": ("ACV", (199.99e-3, 1.9999, 19.999, 199.99, 1050.0), (5, 6)),
    "ohms": ("OHMS", (1.9999, 19.999, 199.99, 1.9999e3, 19.999e3, 199.99e3, 1.9999e6,
                      19.999e6, 199.99e6, 1.9999e9), (5, 6, 7, 8)),
    "true_ohms": ("TRUE_OHMS", (1.9999, 19.999, 199.99, 1.9999e3, 19.999e3), (5, 6, 7, 8)),
    "hv_ohms": ("HIV_OHMS", (19.999e6, 199.99e6, 1.9999e9), (5, 6, 7, 8)),
    "dc_amps": ("DCI", (199.99e-6, 1.9999e-3, 19.999e-3, 199.99e-3, 1.9999, 19.999),
                (5, 6, 7)),
    "ac_amps": ("ACI", (199.99e-6, 1.9999e-3, 19.999e-3, 199.99e-3, 1.9999, 19.999),
                (5, 6)),
}
UNITS = {"dc_volts": "V", "ac_volts": "V", "ohms": "Ω", "true_ohms": "Ω", "hv_ohms": "Ω",
         "dc_amps": "A", "ac_amps": "A"}
AC_FUNCTIONS = ("ac_volts", "ac_amps")
OHMS_FUNCTIONS = ("ohms", "true_ohms", "hv_ohms")
# the power-on and reset state of each function (pp. 4-11 to 4-17)
RESET_STATE = {
    "dc_volts": dict(range=1050.0, resolution=7, filter=False, fast=True, four_wire=False),
    "ac_volts": dict(range=1050.0, resolution=6, ac_filter=40, transfer=True, dc_coupled=False,
                     four_wire=False),
    "ohms": dict(range=19.999e3, resolution=7, filter=False, fast=True, four_wire=False,
                 low_current=False),
    "true_ohms": dict(range=19.999e3, resolution=7, fast=True, low_current=False),
    "hv_ohms": dict(range=19.999e6, resolution=6, filter=False, fast=False, four_wire=False),
    "dc_amps": dict(range=1.9999, resolution=7, filter=False, fast=True),
    "ac_amps": dict(range=1.9999, resolution=6, ac_filter=40, dc_coupled=False),
}
AC_FILTERS = (1, 10, 40, 100)
INPUTS = {"front": "FRONT", "rear": "REAR", "ratio": "DIV_REAR", "difference": "SUB_REAR",
          "deviation": "DEVTN", "off": "OFF"}
SCAN_INPUTS = ("ratio", "difference", "deviation")

# Read rates in readings per second, Normal and Fast (p. 5-14).
DC_RATE = {8: (1 / 25, 1 / 6), 7: (1 / 6, 1 / 2), 6: (2, 35), 5: (35, 150)}
TRUE_OHMS_RATE = {8: (1 / 90, 1 / 30), 7: (1 / 30, 1 / 10), 6: (1 / 4, 1 / 3),
                  5: (1 / 3, 1 / 3)}
AC_RATE = {6: {1: 1 / 50, 10: 1 / 5, 40: 1 / 2, 100: 1},  # transfer on
           5: {1: 1 / 50, 10: 1 / 5, 40: 1 / 2, 100: 2}}
AC_RATE_NO_TRANSFER = {1: 1 / 25, 10: 1 / 2.5, 40: 1, 100: 4}
# Default settling delays in seconds, the longest over all ranges of a function, by
# resolution 5 to 8, filter out / in (tables 4-2 and 4-3, p. 4-35). The tables name
# their ranges in decades (1 Ω ... 1 GΩ) unlike chapter 5, so the longest is used.
DC_DELAY = {False: {5: 0.08, 6: 0.1, 7: 1, 8: 5}, True: {5: 0.8, 6: 1, 7: 5, 8: 10}}
DCI_DELAY = {False: {5: 0.08, 6: 0.1, 7: 0.1}, True: {5: 0.8, 6: 1, 7: 1}}
OHMS_DELAY = {False: {5: 2.5, 6: 3, 7: 5, 8: 10}, True: {5: 8, 6: 10, 7: 30, 8: 30}}
TRUE_OHMS_DELAY = {5: 0.08, 6: 0.1, 7: 1, 8: 5}
HV_OHMS_DELAY = {False: {5: 10, 6: 10, 7: 20, 8: 50}, True: {5: 30, 6: 30, 7: 50, 8: 50}}
# ACV and ACI by filter, resolution 5 / 6, normal and (scan); ACI gives resolution 5 only,
# so its resolution 6 uses the ACV figures
AC_DELAY = {100: {5: (0.25, 0.5), 6: (0.3, 1.25)}, 40: {5: (0.6, 1.25), 6: (0.75, 3.25)},
            10: {5: (2, 5), 6: (2.5, 12.5)}, 1: {5: (20, 50), 6: (25, 125)}}
# execution errors (appendix A, p. A-2)
EXECUTION_ERRORS = {
    1001: "option not installed", 1002: "calibration disabled",
    1005: "input zero not allowed in scan", 1007: "data entry error",
    1008: "must be in AC function", 1010: "divide-by-zero not allowed",
    1013: "data out of limit", 1014: "illegal range/function combination",
    1015: "command allowed only in remote", 1016: "not in special calibration",
    1021: "test not allowed when cal enabled", 1024: "illegal range/input combination",
    1025: "auto not available in TruΩ ratio", 1026: "probe identity unrecognized",
    1027: "input zero not allowed in PRT", 1028: "only allowed in voltage function",
    1029: "scan modes not allowed in PRT", 1030: "scan modes not allowed in ACI/DCI",
    1031: "4wV not available via rear input", 1032: "input zero not allowed in cal",
    1033: "calibration not allowed in PRT", 1034: "PRT coefficient limit",
    1035: "cannot edit the default PRT", 1036: "block or rolling average required",
}
QUEUE_EMPTY = (0, 1000, 1012)  # "EXE queue empty", "no more errors in list"


def function_command(function: str, state: dict) -> str:
    """The function command with all its options, e.g. 'DCV AUTO,RESL8,FILT_OFF,...'."""
    header = FUNCTIONS[function][0]
    # A value selects the smallest range that holds it ("2 ... selects the 20 V range",
    # p. 4-11); half the full scale is clear of the switch-over point.
    parts = ["AUTO" if state["range"] is None else f"{state['range'] / 2:G}",
             f"RESL{state['resolution']}"]
    if "filter" in state:
        parts.append("FILT_ON" if state["filter"] else "FILT_OFF")
    if "ac_filter" in state:
        parts.append(f"FILT{state['ac_filter']}HZ")
    if "dc_coupled" in state:
        parts.append("DCCP" if state["dc_coupled"] else "ACCP")
    if "transfer" in state:
        parts.append("TFER_ON" if state["transfer"] else "TFER_OFF")
    if "fast" in state:
        parts.append("FAST_ON" if state["fast"] else "FAST_OFF")
    if "four_wire" in state:
        parts.append("FOUR_WR" if state["four_wire"] else "TWO_WR")
    if "low_current" in state:
        parts.append("LOI_ON" if state["low_current"] else "LOI_OFF")
    return f"{header} {','.join(parts)}"


class Fluke8508A(Device):
    # --- life of the device -------------------------------------------------------------
    def connect(self) -> None:
        self.transport.device_clear()
        self.transport.clear_input()
        identity = self.transport.query("*IDN?")
        if "8508" not in identity:
            raise CommandError(f"the instrument answers *IDN? with {identity!r}, "
                               "not as an 8508A")
        self.reset()

    def identify(self) -> str:
        return self.transport.query("*IDN?")

    # --- helpers --------------------------------------------------------------------------
    def check_errors(self) -> None:
        """Ask the event status register; on an error, read the error queues (p. 4-31)."""
        if not self.settings.get("check_errors", True):
            return
        reply = self.transport.query("*ESR?")
        try:
            esr = int(float(reply))
        except ValueError:
            raise CommandError(f"unexpected reply to *ESR?: {reply!r}") from None
        problems = []
        if esr & 32:
            problems.append("command error (not understood)")
        if esr & 4:
            problems.append("query error")
        if esr & 16:
            for code in self._queue("EXQ?"):
                problems.append(f"execution error {code}: "
                                f"{EXECUTION_ERRORS.get(code, 'see appendix A')}")
        if esr & 8:
            for code in self._queue("DDQ?"):
                problems.append(f"device error {code} (see appendix A)")
        if problems:
            raise CommandError("the 8508A reported: " + "; ".join(problems))

    def _queue(self, query: str) -> list[int]:
        codes = []
        for _ in range(20):  # read until empty (p. 4-32)
            reply = self.transport.query(query)
            try:
                code = int(float(reply))
            except ValueError:
                raise CommandError(f"unexpected reply to {query}: {reply!r}") from None
            if code in QUEUE_EMPTY:
                break
            codes.append(code)
        return codes

    def _settings(self, *commands: str) -> None:
        for text in commands:
            self.transport.write(text)
        self.check_errors()

    def _apply(self) -> None:
        self._settings(function_command(self.function, self.state[self.function]))

    def _select(self, function: str) -> None:
        if function != self.function:
            self.function = function
            self._apply()

    @property
    def _current(self) -> dict:
        return self.state[self.function]

    def _reading_seconds(self) -> float:
        """The longest one reading may take with the present settings (pp. 4-35, 5-14)."""
        state, function = self._current, self.function
        resolution = state["resolution"]
        scan = self.input in SCAN_INPUTS
        if function == "prt":  # PRT readings run at the True Ohms rate (p. 5-14)
            delay, rate = TRUE_OHMS_DELAY[resolution], TRUE_OHMS_RATE[resolution][0]
        elif function in AC_FUNCTIONS:
            delay = AC_DELAY[state["ac_filter"]][resolution][1 if scan else 0]
            if function == "ac_volts" and not state["transfer"]:
                rate = AC_RATE_NO_TRANSFER[state["ac_filter"]]
            else:
                rate = AC_RATE[resolution][state["ac_filter"]]
        else:
            fast = state.get("fast", False)
            if function == "dc_volts":
                delay = DC_DELAY[state["filter"]][resolution]
                rate = DC_RATE[resolution][fast]
            elif function == "dc_amps":
                delay = DCI_DELAY[state["filter"]][resolution]
                rate = DC_RATE[resolution][fast]
            elif function == "ohms":
                delay = OHMS_DELAY[state["filter"]][resolution]
                rate = DC_RATE[resolution][fast]
            elif function == "true_ohms":
                delay = TRUE_OHMS_DELAY[resolution]
                rate = TRUE_OHMS_RATE[resolution][fast]
            else:  # hv_ohms: no read rate of its own in the table; the slowest one
                delay = HV_OHMS_DELAY[state["filter"]][resolution]
                rate = TRUE_OHMS_RATE[resolution][fast]
        if self.delay is not None:
            delay = self.delay
        seconds = delay + 1 / rate
        if scan:
            seconds *= 2  # front, then rear
        if state["range"] is None:
            seconds *= 2  # autorange may need a second conversion
        if self.input in ("front", "rear", "off") and function in AC_FUNCTIONS \
                and not self.fast_gate:
            seconds += 1  # the 1 s frequency gate (p. 4-22)
        return seconds * 1.2 + 2

    @staticmethod
    def _number(text: str, what: str = "reading") -> float:
        try:
            value = float(text)
        except ValueError:
            raise CommandError(f"unexpected {what} from the 8508A: {text!r}") from None
        if abs(value) >= OVERLOAD:
            return math.copysign(math.inf, value)
        return value

    def _read(self) -> float:
        seconds = self._reading_seconds()
        self.transport.write("X?")
        try:
            reply = read_line_within(self.transport, seconds)
        except TimeoutError:
            self.transport.device_clear()
            raise CommandError(f"no reading from the 8508A within {seconds:.0f} s") from None
        return self._number(reply)

    # --- state ----------------------------------------------------------------------------
    @command("Reset the meter (*RST) to its power-on state, with external triggering for "
             "bus control")
    def reset(self) -> None:
        self._settings("*RST", "*CLS", "TRG_SRCE EXT")
        self.function = "dc_volts"
        self.state = {name: dict(values) for name, values in RESET_STATE.items()}
        self.input, self.delay, self.fast_gate = "front", None, True

    # --- measuring ------------------------------------------------------------------------
    @command("Take one reading in the current function (infinite on overload)")
    def measure(self) -> float:
        return self._read()

    @command("Take several readings in a row in the current function",
             params={"count": Param(min=1, max=10000)})
    def measure_many(self, count: int) -> list:
        return [self._read() for _ in range(count)]

    def _measure_as(self, function: str, **options) -> float:
        changed = function != self.function
        for key, value in options.items():
            if self.state[function].get(key) != value:
                self.state[function][key] = value
                changed = True
        self.function = function
        if changed:
            self._apply()
        return self._read()

    @command("Measure DC voltage", unit="V")
    def measure_dc_voltage(self) -> float:
        return self._measure_as("dc_volts")

    @command("Measure AC voltage (true RMS)", unit="V")
    def measure_ac_voltage(self) -> float:
        return self._measure_as("ac_volts")

    @command("Measure resistance with two wires", unit="Ω")
    def measure_resistance(self) -> float:
        return self._measure_as("ohms", four_wire=False)

    @command("Measure resistance with four wires", unit="Ω")
    def measure_resistance_4wire(self) -> float:
        return self._measure_as("ohms", four_wire=True)

    @command("Measure resistance with True Ohms: four wires, current reversed to cancel "
             "thermal EMFs, 2 Ω to 20 kΩ", unit="Ω")
    def measure_true_ohms(self) -> float:
        return self._measure_as("true_ohms")

    @command("Measure resistance with High Voltage Ohms, 20 MΩ to 2 GΩ (no autorange)",
             unit="Ω")
    def measure_hv_ohms(self) -> float:
        return self._measure_as("hv_ohms")

    @command("Measure DC current", unit="A")
    def measure_dc_current(self) -> float:
        return self._measure_as("dc_amps")

    @command("Measure AC current (true RMS)", unit="A")
    def measure_ac_current(self) -> float:
        return self._measure_as("ac_amps")

    @command("The frequency of the last AC reading (NaN if it could not be measured)",
             unit="Hz")
    def get_frequency(self) -> float:
        if self.function not in AC_FUNCTIONS:
            raise CommandError("frequency comes with AC volts or AC current readings")
        value = self._number(self.transport.query("FREQ?"), "frequency")
        return math.nan if math.isinf(value) else value

    @command("Measure temperature with a PRT stored in the meter, by its identity "
             "(up to 17 characters)", unit="°C",
             params={"resolution": Param(choices=(5, 6, 7, 8))})
    def measure_temperature(self, identity: str, resolution: int = 6) -> float:
        identity = identity.replace('"', "")[:17]
        self._settings(f'PRT "{identity}",DEG_C,RESL{resolution}')
        self.function = "prt"
        self.state["prt"] = dict(range=0.0, resolution=resolution)
        return self._read()

    # --- configuration --------------------------------------------------------------------
    @command("Select what to measure", params={"function": Param(choices=tuple(FUNCTIONS))})
    def set_function(self, function: str) -> None:
        self.function = function
        self._apply()

    def _function_or_fail(self) -> str:
        if self.function not in FUNCTIONS:
            raise CommandError("select a function first (not available for PRT)")
        return self.function

    @command("Fixed range: the smallest that holds this value (in the function's unit)")
    def set_range(self, value: float) -> None:
        function = self._function_or_fail()
        ranges = FUNCTIONS[function][1]
        fitting = [r for r in ranges if abs(value) <= r]
        if not fitting:
            raise CommandError(f"no {function} range holds {value:g} (the largest reads "
                               f"up to {ranges[-1]:g} {UNITS[function]})")
        self._current["range"] = fitting[0]
        self._apply()

    @command("Choose the range automatically (not for High Voltage Ohms)")
    def set_autorange(self) -> None:
        if self._function_or_fail() == "hv_ohms":
            raise CommandError("High Voltage Ohms has no autorange (p. 4-14)")
        self._current["range"] = None
        self._apply()

    @command("Resolution in digits: 5 to 8 for DC volts and ohms (8 = 8½), 5 to 7 for DC "
             "current, 5 or 6 for AC; more digits read slower",
             params={"digits": Param(choices=(5, 6, 7, 8))})
    def set_resolution(self, digits: int) -> None:
        function = self._function_or_fail()
        allowed = FUNCTIONS[function][2]
        if digits not in allowed:
            raise CommandError(f"{function} takes {', '.join(map(str, allowed))} digits")
        self._current["resolution"] = digits
        self._apply()

    @command("Fast A-D conversion (shorter integration, faster, a little noisier)")
    def set_fast(self, on: bool) -> None:
        if "fast" not in self._current:
            raise CommandError(f"{self.function} has no fast mode")
        self._current["fast"] = on
        self._apply()

    @command("Analog filter in the signal path (DC volts, DC current, ohms, HV ohms)")
    def set_filter(self, on: bool) -> None:
        if "filter" not in self._current:
            raise CommandError(f"{self.function} has no on/off filter; AC uses set_ac_filter")
        self._current["filter"] = on
        self._apply()

    @command("AC filter: the lowest frequency to measure, 1, 10, 40 or 100 Hz",
             params={"hertz": Param(choices=AC_FILTERS)})
    def set_ac_filter(self, hertz: int) -> None:
        if "ac_filter" not in self._current:
            raise CommandError("the AC filter belongs to AC volts and AC current")
        self._current["ac_filter"] = hertz
        self._apply()

    @command("AC coupling: false = AC coupled, true = DC coupled (for signals below 40 Hz)")
    def set_dc_coupling(self, on: bool) -> None:
        if "dc_coupled" not in self._current:
            raise CommandError("coupling belongs to AC volts and AC current")
        self._current["dc_coupled"] = on
        self._apply()

    @command("AC volts transfer mode: better linearity and temperature, slower")
    def set_transfer(self, on: bool) -> None:
        if "transfer" not in self._current:
            raise CommandError("transfer mode belongs to AC volts")
        self._current["transfer"] = on
        self._apply()

    @command("Four-wire connection for DC or AC volts and HV ohms (sense terminals)")
    def set_four_wire(self, on: bool) -> None:
        if "four_wire" not in self._current:
            raise CommandError(f"{self.function} has no 2/4-wire choice")
        self._current["four_wire"] = on
        self._apply()

    @command("Low current for ohms and True Ohms")
    def set_low_current(self, on: bool) -> None:
        if "low_current" not in self._current:
            raise CommandError("low current belongs to ohms and True Ohms")
        self._current["low_current"] = on
        self._apply()

    @command("Input: front, rear, ratio (front/rear), difference (front-rear), deviation "
             "((front-rear)/rear) or off", params={"which": Param(choices=tuple(INPUTS))})
    def set_input(self, which: str) -> None:
        self.input = which
        self._settings(f"INPUT {INPUTS[which]}")

    @command("Guard: internal (Lo and guard joined) or external (remote guard)",
             params={"guard": Param(choices=("internal", "external"))})
    def set_guard(self, guard: str) -> None:
        self._settings("GUARD INT" if guard == "internal" else "GUARD EXT")

    @command("Settling delay between trigger and reading (0 to 65000 s)",
             params={"seconds": Param(unit="s", min=0, max=65000)})
    def set_delay(self, seconds: float) -> None:
        self.delay = seconds
        self._settings(f"DELAY {seconds:g}")

    @command("Use the meter's default settling delay for the function, range and filter")
    def set_auto_delay(self) -> None:
        self.delay = None
        self._settings("DELAY DFLT")

    @command("Frequency gate for AC readings: fast (50 ms, 4½ digits) or slow (1 s, 6½)")
    def set_fast_gate(self, on: bool) -> None:
        self.fast_gate = on
        self._settings("GATE FAST_ON" if on else "GATE FAST_OFF")

    @command("Input zero for the present range and function (short the input first); "
             "true when it passed")
    def input_zero(self) -> bool:
        self.transport.write("ZERO?")
        try:
            reply = read_line_within(self.transport, 120)
        except TimeoutError:
            raise CommandError("no answer to ZERO? from the 8508A") from None
        self.check_errors()
        return reply.strip() == "0"

    @command("Self-test (*TST?); true when it passed")
    def self_test(self) -> bool:
        self.transport.write("*TST?")
        try:
            reply = read_line_within(self.transport, 600)
        except TimeoutError:
            raise CommandError("no answer to *TST? from the 8508A") from None
        return reply.strip() == "0"

    @command("Send a raw command")
    def write(self, text: str) -> None:
        self.transport.write(text)

    @command("Send a raw query and return the reply")
    def query(self, text: str) -> str:
        return self.transport.query(text)


class Fluke8508ASim(SimTransport):
    """An 8508A measuring a 10 V reference, 1 V AC at 1 kHz, a 10 kΩ resistor..."""

    INPUTS = {"DCV": 10.00000123, "ACV": 1.0000234, "OHMS": 10000.0456,
              "TRUE_OHMS": 10000.0401, "HIV_OHMS": 100.00123e6, "DCI": 1.0000012e-3,
              "ACI": 0.5000023e-3, "PRT": 23.4567}
    REAR = 5.0000012  # DC volts on the rear input
    FREQUENCY = 1000.0012
    RANGES = {header: ranges for header, ranges, _ in FUNCTIONS.values()}
    OPTIONS = {"FILT_ON", "FILT_OFF", "RESL5", "RESL6", "RESL7", "RESL8", "FAST_ON",
               "FAST_OFF", "TWO_WR", "FOUR_WR", "LOI_ON", "LOI_OFF", "FILT100HZ",
               "FILT40HZ", "FILT10HZ", "FILT1HZ", "ACCP", "DCCP", "TFER_ON", "TFER_OFF",
               "SPOT_ON", "SPOT_OFF"}

    def __init__(self, **options):
        super().__init__(**options)
        self.reset()
        self.prts = {"PT100 LAB"}

    def reset(self) -> None:
        self.function, self.range, self.options = "DCV", 1050.0, set()
        self.trigger, self.input, self.guard, self.delay = "INT", "FRONT", "INT", "DFLT"
        self.esr, self.exq, self.ddq = 0, [], []
        self.last = None

    def _execution_error(self, code: int) -> None:
        self.esr |= 16
        self.exq.append(code)

    def _reading(self) -> float:
        if self.function == "DCV" and self.input == "REAR":
            value = self.REAR
        elif self.function == "DCV" and self.input == "DIV_REAR":
            value = self.INPUTS["DCV"] / self.REAR
        else:
            value = self.INPUTS[self.function]
        if self.range is not None and self.function != "PRT" and abs(value) > self.range:
            value = OVERLOAD
        return value

    def _function(self, header: str, args: list[str]) -> None:
        if header == "PRT":
            identity = args[0].strip('"') if args else ""
            if identity not in self.prts:
                self._execution_error(1026)
                return
            self.function, self.range = "PRT", None
            return
        ranges = self.RANGES[header]
        new_range = self.range if header == self.function else max(ranges)
        options = set()
        for arg in args:
            if arg == "AUTO":
                if header == "HIV_OHMS":
                    self.esr |= 32  # not in its syntax
                    return
                new_range = None
            elif re.fullmatch(r"[\d.Ee+-]+", arg):
                value = float(arg)
                if value > max(ranges):
                    self._execution_error(1013)
                    return
                new_range = next(r for r in ranges if value <= r)
            elif arg in self.OPTIONS:
                options.add(arg)
            else:
                self.esr |= 32
                return
        self.function, self.range, self.options = header, new_range, options

    def handle(self, line: str) -> str | None:
        replies = []
        for unit in line.split(";"):
            unit = unit.strip()
            if not unit:
                continue
            header, _, rest = unit.partition(" ")
            header = header.upper()
            args = [a.strip() for a in rest.split(",")] if rest.strip() else []
            if header not in ("PRT",):
                args = [a.upper() for a in args]
            reply = self.command(header, args)
            if reply is not None:
                replies.append(reply)
        return ";".join(replies) if replies else None

    def command(self, header: str, args: list[str]) -> str | None:
        if header == "*IDN?":
            return "FLUKE,8508A,123456789,1.0"  # the four fields of p. 4-44
        if header == "*RST":
            self.reset()
        elif header == "*CLS":
            self.esr, self.exq, self.ddq = 0, [], []
        elif header == "*ESR?":
            esr, self.esr = self.esr, 0
            return str(esr)
        elif header == "EXQ?":
            return str(self.exq.pop() if self.exq else 0)  # last in, first out
        elif header == "DDQ?":
            return str(self.ddq.pop() if self.ddq else 0)
        elif header == "TRG_SRCE" and args and args[0] in ("INT", "EXT"):
            self.trigger = args[0]
        elif header in self.RANGES or header == "PRT":
            self._function(header, args)
        elif header == "INPUT" and args and args[0] in INPUTS.values():
            self.input = args[0]
        elif header == "GUARD" and args and args[0] in ("INT", "EXT"):
            self.guard = args[0]
        elif header == "GATE" and args and args[0] in ("FAST_ON", "FAST_OFF"):
            pass
        elif header == "DELAY" and args:
            if args[0] == "DFLT":
                self.delay = "DFLT"
            else:
                try:
                    value = float(args[0])
                except ValueError:
                    self.esr |= 32
                    return None
                if not 0 <= value <= 65000:
                    self._execution_error(1013)
                else:
                    self.delay = value
        elif header == "X?":
            self.last = self._reading()
            return f"{self.last:+.8E}"
        elif header == "RDG?":
            return f"{(self.last if self.last is not None else 0.0):+.8E}"
        elif header == "FREQ?":
            return f"{self.FREQUENCY:+.6E}" if self.function in ("ACV", "ACI") \
                else f"{OVERLOAD:+.4E}"
        elif header in ("ZERO?", "*TST?"):
            return "0"
        else:
            self.esr |= 32  # command error
        return None
