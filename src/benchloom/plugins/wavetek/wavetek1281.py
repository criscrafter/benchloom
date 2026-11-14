# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Wavetek (Datron) 1281 selfcal digital multimeter, and a simulator for it.

Written from the Wavetek Model 1281 Selfcal Digital Multimeter User's Handbook:
Section 5, Systems Application via the IEEE 488 Interface (pp. 5-1 to 5-B2), and
Appendix A to Section 4 for the error codes. Page numbers in the comments are that
handbook's.

A function command sets the function and its options in one go, for example
"DCV 10,RESL6,FILT_ON" (p. 5-9). The driver selects external triggering, as the
handbook advises against internal triggers under bus control (p. 5-68), and takes
each reading with X?, which is *TRG;RDG? (p. 5-69).

Where the handbook disagrees with itself, the driver allows only what all of its
statements agree on; the disagreements are listed in docs/INSTRUMENTS.md.
"""

from __future__ import annotations

import math

from benchloom import CommandError, Device, Param, command
from benchloom.transports import SimTransport, read_line_within

OVERLOAD = 200e33  # "±200.0000E+33" (p. 5-74)
# Range selection: (value up to which a range is chosen, the range) from the tables of
# pp. 5-30 to 5-44; the last range takes anything above.
DCV_BANDS = ((0.2, 0.1), (2.0, 1.0), (20.0, 10.0), (200.0, 100.0), (math.inf, 1000.0))
OHMS_BANDS = ((200.0, 100.0), (2e3, 1e3), (20e3, 10e3), (200e3, 100e3), (2e6, 1e6))
HI_OHMS_BANDS = ((200e6, 100e6), (math.inf, 1e9))  # from 20 MΩ
TRUE_OHMS_BANDS = ((20.0, 10.0), (200.0, 100.0), (2e3, 1e3), (20e3, 10e3),
                   (math.inf, 100e3))
DCI_BANDS = ((2e-4, 1e-4), (2e-3, 1e-3), (2e-2, 10e-3), (0.2, 100e-3), (math.inf, 1.0))
ACI_BANDS = ((2e-4, 1e-4), (2e-3, 1e-3), (2e-2, 10e-3), (0.2, 100e-3), (2.0, 1.0))
# function -> header, bands, resolutions, the options it takes, the option it needs
FUNCTIONS = {
    "dc_volts": ("DCV", DCV_BANDS, (5, 6, 7, 8), {"filter", "fast"}, None),
    "ac_volts": ("ACV", DCV_BANDS, (4, 5, 6), {"ac_filter", "dc_coupled", "transfer"},
                 "ac"),
    "ohms": ("OHMS", OHMS_BANDS, (5, 6, 7, 8), {"filter", "fast", "four_wire",
                                                 "low_current"}, "ohms"),
    "hi_ohms": ("HI_OHMS", HI_OHMS_BANDS, (5, 6), {"filter", "four_wire"}, "ohms"),
    "true_ohms": ("TRUE_OHMS", TRUE_OHMS_BANDS, (5, 6, 7, 8), {"filter", "fast"}, "ohms"),
    "dc_amps": ("DCI", DCI_BANDS, (4, 5, 6), {"filter", "fast"}, "current"),
    "ac_amps": ("ACI", ACI_BANDS, (4, 5), {"ac_filter", "dc_coupled"}, "current"),
}
UNITS = {"dc_volts": "V", "ac_volts": "V", "ohms": "Ω", "hi_ohms": "Ω", "true_ohms": "Ω",
         "dc_amps": "A", "ac_amps": "A"}
AC_FUNCTIONS = ("ac_volts", "ac_amps")
# the power-on state of each function (appendix B, p. 5-B1); ranges become AUTO when the
# driver first selects a function, except HI_OHMS, which has no AUTO (p. 5-38)
RESET_STATE = {
    "dc_volts": dict(range=1000.0, resolution=6, filter=False, fast=False),
    "ac_volts": dict(range=1000.0, resolution=6, ac_filter=100, dc_coupled=False,
                     transfer=True),
    "ohms": dict(range=None, resolution=6, filter=False, fast=False, four_wire=False,
                 low_current=False),
    "hi_ohms": dict(range=100e6, resolution=6, filter=False, four_wire=False),
    "true_ohms": dict(range=None, resolution=6, filter=False, fast=False),
    "dc_amps": dict(range=None, resolution=6, filter=False, fast=False),
    "ac_amps": dict(range=None, resolution=5, ac_filter=100, dc_coupled=False),
}
AC_FILTERS = (1, 10, 40, 100)
INPUTS = {"front": "FRONT", "channel_a": "CH_A", "channel_b": "CH_B", "ratio": "DIV_B",
          "difference": "SUB_B", "deviation": "DEVTN", "off": "OFF"}
SCAN_INPUTS = ("ratio", "difference", "deviation")
# A-D integration in power line cycles by resolution, fast on / off (p. 5-71):
# C = 3.3 ms, D = 1, F = 16, G = 64 power line cycles
AD_CYCLES = {4: (None, 1), 5: (None, 1), 6: (1, 16), 7: (64, 256), 8: (256, 1024)}
AD_FAST_SECONDS = 3.3e-3
# default settling delays (p. 5-71), seconds, by resolution 4 to 8, filter out / in
DCV_DELAY = {False: (.06, .08, .1, 1, 5), True: (.6, .8, 1, 5, 10)}
DCI_DELAY = {False: (.06, .08, .1), True: (.6, .8, 1)}
AC_DELAY = {100: (.15, .25, .3), 40: (.4, .6, .75), 10: (1.5, 2, 2.5), 1: (15, 20, 25)}
OHMS_DELAY = [  # (up to this range, filter out, filter in)
    (100e3, (.06, .08, .1, 1, 5), (.6, .8, 1, 5, 10)),
    (1e6, (.3, .4, .5, 3, 10), (2, 2.5, 3, 5, 10)),
    (10e6, (2, 2.5, 3, 5, 10), (6, 8, 10, 30, 30)),
    (100e6, (6, 8, 10, 10, 10), (20, 25, 30, 30, 30)),  # 7, 8 digits: none given
    (1e9, (10, 10, 10, 10, 10), (30, 30, 30, 30, 30)),
]
# execution errors (appendix A to section 4, p. 4-A3)
EXECUTION_ERRORS = {
    1001: "option not installed", 1002: "calibration disabled",
    1003: "ratio/function combination not allowed", 1004: "filter incompatible with function",
    1005: "input zero not allowed in ratio", 1006: "calibration not allowed in ratio",
    1007: "data entry error", 1008: "must be in AC function", 1009: "pass number entry error",
    1010: "divide-by-zero not allowed", 1013: "data out of limit",
    1014: "illegal range/function combination", 1015: "command allowed only in remote",
    1016: "not in special calibration", 1017: "calibration not allowed with math",
    1018: "key not in the cal enabled position", 1019: "spec not compatible with function",
    1020: "internal source cal required", 1021: "test not allowed when cal enabled",
    1022: "no parameter for this function", 1023: "input zero not allowed in ACI",
}
QUEUE_EMPTY = (0, 1000, 1012)


def range_for(bands, value: float) -> float | None:
    for upper, nominal in bands:
        if abs(value) < upper:
            return nominal
    return None


def range_value(function: str, nominal: float) -> str:
    """The value to send for a range: one the handbook's table puts in that range."""
    if function == "hi_ohms" and nominal == 100e6:
        # the table gives 20 to 199.9999 MΩ, but the example on p. 5-39 says that
        # 100000000 selects the 1 GΩ range: stay clear of the disputed value
        return "50000000"
    return f"{nominal:G}"


def function_command(function: str, state: dict) -> str:
    header, _, _, options, _ = FUNCTIONS[function]
    parts = ["AUTO" if state["range"] is None else range_value(function, state["range"]),
             f"RESL{state['resolution']}"]
    if "filter" in options:
        parts.append("FILT_ON" if state["filter"] else "FILT_OFF")
    if "ac_filter" in options:
        parts.append(f"FILT{state['ac_filter']}HZ")
    if "dc_coupled" in options:
        parts.append("DCCP" if state["dc_coupled"] else "ACCP")
    if "transfer" in options and state.get("transfer_set"):
        parts.append("TFER_ON" if state["transfer"] else "TFER_OFF")  # Option 12 only
    if "fast" in options:
        parts.append("FAST_ON" if state["fast"] else "FAST_OFF")
    if "four_wire" in options:
        parts.append("FWR" if state["four_wire"] else "TWR")
    if "low_current" in options:
        parts.append("LOI_ON" if state["low_current"] else "LOI_OFF")
    return f"{header} {','.join(parts)}"


class Wavetek1281(Device):
    # --- life of the device -------------------------------------------------------------
    def connect(self) -> None:
        self.transport.device_clear()
        self.transport.clear_input()
        identity = self.transport.query("*IDN?")
        fields = [f.strip() for f in identity.split(",")]
        # The handbook's own example answers "DATRON INSTRUMENTS,1271,..." (p. 5-86)
        if len(fields) < 2 or fields[1] not in ("1281", "1271"):
            raise CommandError(f"the instrument answers *IDN? with {identity!r}, "
                               "not as a 1281")
        self.options = self._read_options()
        try:
            self.line_frequency = float(self.transport.query("LINEF?"))
        except (ValueError, TimeoutError):
            self.line_frequency = 50.0
        if self.line_frequency not in (50.0, 60.0):
            self.line_frequency = 50.0
        self.reset()

    def _read_options(self) -> set[str]:
        """*OPT?: AC, current and resistance options fitted (p. 5-87)."""
        reply = self.transport.query("*OPT?")
        bits = [b.strip() for b in reply.split(",")]
        if len(bits) < 3 or any(b not in ("0", "1") for b in bits[:3]):
            raise CommandError(f"unexpected reply to *OPT?: {reply!r}")
        return {name for name, bit in zip(("ac", "current", "ohms"), bits) if bit == "1"}

    def identify(self) -> str:
        return self.transport.query("*IDN?")

    # --- helpers --------------------------------------------------------------------------
    def check_errors(self) -> None:
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
                                f"{EXECUTION_ERRORS.get(code, 'see appendix A to section 4')}")
        if esr & 8:
            for code in self._queue("DDQ?"):
                problems.append(f"device error {code} (see appendix A to section 4)")
        if problems:
            raise CommandError("the 1281 reported: " + "; ".join(problems))

    def _queue(self, query: str) -> list[int]:
        codes = []
        for _ in range(20):
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

    def _check_option(self, function: str) -> None:
        needed = FUNCTIONS[function][4]
        missing = []
        if needed == "ac" and "ac" not in self.options:
            missing.append("AC (option 10)")
        if needed in ("ohms", "current") and "ohms" not in self.options:
            missing.append("resistance (option 20)")
        if needed == "current" and "current" not in self.options:
            missing.append("current (option 30)")
        if function == "ac_amps" and "ac" not in self.options:
            missing.append("AC (option 10)")
        if missing:
            raise CommandError(f"{function} needs {' and '.join(missing)}, which *OPT? "
                               "says is not fitted")

    def _apply(self) -> None:
        self._settings(function_command(self.function, self.state[self.function]))

    @property
    def _current(self) -> dict:
        return self.state[self.function]

    def _delay(self) -> float:
        state, function = self._current, self.function
        index = state["resolution"] - 4
        if function == "dc_volts":
            return DCV_DELAY[state["filter"]][index]
        if function == "dc_amps":
            return DCI_DELAY[state["filter"]][index]
        if function in AC_FUNCTIONS:
            return AC_DELAY[state["ac_filter"]][index]
        rows = [row for row in OHMS_DELAY if state["range"] is None or state["range"] <= row[0]]
        if state["range"] is not None:
            rows = rows[:1]
        return max(row[2 if state.get("filter") else 1][index] for row in rows)

    def _reading_seconds(self) -> float:
        """The longest a reading may take (A-D modes and delays of p. 5-71)."""
        state = self._current
        fast = state.get("fast", False)
        cycles = AD_CYCLES[state["resolution"]][0 if fast else 1]
        conversion = AD_FAST_SECONDS if cycles is None else cycles / self.line_frequency
        delay = self._delay() if self.delay is None else self.delay
        # The handbook gives no read rates, so the conversion is doubled for overhead
        # (True Ohms also reverses its current)
        seconds = delay + conversion * 2
        if self.function == "true_ohms":
            seconds += conversion * 2
        if self.input in SCAN_INPUTS:
            seconds *= 2  # channel A, then channel B
        if state["range"] is None:
            seconds *= 2  # autorange triggers a new measurement on a range change
        if self.function in AC_FUNCTIONS and not self.fast_gate:
            seconds += 1  # the 1 s frequency gate (p. 5-49)
        return seconds * 1.2 + 2

    @staticmethod
    def _number(text: str, what: str = "reading") -> float:
        try:
            value = float(text)
        except ValueError:
            raise CommandError(f"unexpected {what} from the 1281: {text!r}") from None
        if abs(value) >= OVERLOAD * 0.9:  # "200.0000E+33, ± 10 %" (p. 5-75)
            return math.copysign(math.inf, value)
        return value

    def _read(self) -> float:
        seconds = self._reading_seconds()
        self.transport.write("X?")
        try:
            reply = read_line_within(self.transport, seconds)
        except TimeoutError:
            self.transport.device_clear()
            raise CommandError(f"no reading from the 1281 within {seconds:.0f} s") from None
        return self._number(reply)

    def _select(self, function: str, **options) -> None:
        self._check_option(function)
        state = self.state[function]
        changed = function != self.function or not state.get("sent")
        for key, value in options.items():
            if state.get(key) != value:
                state[key] = value
                changed = True
        self.function = function
        if changed:
            self._apply()
            state["sent"] = True

    # --- state ----------------------------------------------------------------------------
    @command("Reset the meter (*RST) to its power-on state, with external triggering for "
             "bus control")
    def reset(self) -> None:
        self._settings("*RST", "*CLS", "TRG_SRCE EXT")
        self.function = "dc_volts"
        self.state = {name: dict(values) for name, values in RESET_STATE.items()}
        self.state["dc_volts"]["sent"] = True  # active after reset, as described
        self.input, self.delay, self.fast_gate = "front", None, True

    # --- measuring ------------------------------------------------------------------------
    @command("Take one reading in the current function (infinite on overload)")
    def measure(self) -> float:
        return self._read()

    @command("Take several readings in a row in the current function",
             params={"count": Param(min=1, max=10000)})
    def measure_many(self, count: int) -> list:
        return [self._read() for _ in range(count)]

    @command("Measure DC voltage", unit="V")
    def measure_dc_voltage(self) -> float:
        self._select("dc_volts")
        return self._read()

    @command("Measure AC voltage (true RMS; option 10)", unit="V")
    def measure_ac_voltage(self) -> float:
        self._select("ac_volts")
        return self._read()

    @command("Measure resistance with two wires (option 20)", unit="Ω")
    def measure_resistance(self) -> float:
        self._select("ohms", four_wire=False)
        return self._read()

    @command("Measure resistance with four wires (option 20)", unit="Ω")
    def measure_resistance_4wire(self) -> float:
        self._select("ohms", four_wire=True)
        return self._read()

    @command("Measure resistance with True Ohms, 10 Ω to 100 kΩ (option 20)", unit="Ω")
    def measure_true_ohms(self) -> float:
        self._select("true_ohms")
        return self._read()

    @command("Measure resistance with Hi Ohms, 100 MΩ and 1 GΩ ranges, no autorange "
             "(option 20)", unit="Ω")
    def measure_hi_ohms(self) -> float:
        self._select("hi_ohms")
        return self._read()

    @command("Measure DC current (options 30 and 20)", unit="A")
    def measure_dc_current(self) -> float:
        self._select("dc_amps")
        return self._read()

    @command("Measure AC current (options 30, 20 and 10)", unit="A")
    def measure_ac_current(self) -> float:
        self._select("ac_amps")
        return self._read()

    @command("The frequency of the last AC reading (NaN if it could not be measured)",
             unit="Hz")
    def get_frequency(self) -> float:
        if self.function not in AC_FUNCTIONS:
            raise CommandError("frequency comes with AC volts or AC current readings")
        value = self._number(self.transport.query("FREQ?"), "frequency")
        return math.nan if math.isinf(value) else value

    # --- configuration --------------------------------------------------------------------
    @command("Select what to measure", params={"function": Param(choices=tuple(FUNCTIONS))})
    def set_function(self, function: str) -> None:
        self._check_option(function)
        self.function = function
        self._apply()
        self._current["sent"] = True

    @command("Fixed range: the one the handbook's table gives for this value (in the "
             "function's unit)")
    def set_range(self, value: float) -> None:
        nominal = range_for(FUNCTIONS[self.function][1], value)
        if nominal is None or (self.function == "hi_ohms" and abs(value) < 20e6):
            raise CommandError(f"the handbook gives no {self.function} range for {value:g} "
                               f"{UNITS[self.function]}")
        self._current["range"] = nominal
        self._apply()

    @command("Choose the range automatically (not for Hi Ohms)")
    def set_autorange(self) -> None:
        if self.function == "hi_ohms":
            raise CommandError("Hi Ohms has no autorange (p. 5-38)")
        self._current["range"] = None
        self._apply()

    @command("Resolution in digits: DC volts, ohms and True Ohms 5 to 8, Hi Ohms 5 or 6, "
             "AC volts and DC current 4 to 6, AC current 4 or 5",
             params={"digits": Param(choices=(4, 5, 6, 7, 8))})
    def set_resolution(self, digits: int) -> None:
        allowed = FUNCTIONS[self.function][2]
        if digits not in allowed:
            raise CommandError(f"{self.function} takes {', '.join(map(str, allowed))} digits")
        self._current["resolution"] = digits
        self._apply()

    def _set_option(self, option: str, value, what: str) -> None:
        if option not in FUNCTIONS[self.function][3]:
            raise CommandError(f"{self.function} has no {what}")
        self._current[option] = value
        self._apply()

    @command("Fast A-D (fewer power line cycles) for DC volts, ohms, True Ohms, DC current")
    def set_fast(self, on: bool) -> None:
        self._set_option("fast", on, "fast mode")

    @command("Analog filter for DC volts, ohms, Hi and True Ohms, DC current")
    def set_filter(self, on: bool) -> None:
        self._set_option("filter", on, "on/off filter (AC uses set_ac_filter)")

    @command("AC filter: the lowest frequency to measure, 1, 10, 40 or 100 Hz",
             params={"hertz": Param(choices=AC_FILTERS)})
    def set_ac_filter(self, hertz: int) -> None:
        self._set_option("ac_filter", hertz, "AC filter")

    @command("AC coupling: false = AC coupled, true = DC coupled (for signals below 40 Hz)")
    def set_dc_coupling(self, on: bool) -> None:
        self._set_option("dc_coupled", on, "coupling choice")

    @command("AC volts transfer mode (option 12 only)")
    def set_transfer(self, on: bool) -> None:
        self._current["transfer_set"] = True
        self._set_option("transfer", on, "transfer mode")

    @command("Four-wire connection for ohms and Hi Ohms")
    def set_four_wire(self, on: bool) -> None:
        self._set_option("four_wire", on, "2/4-wire choice")

    @command("Low current for ohms")
    def set_low_current(self, on: bool) -> None:
        self._set_option("low_current", on, "low current mode")

    @command("Input: front, channel_a, channel_b (rear), ratio A/B, difference A-B, "
             "deviation (A-B)/B, or off", params={"which": Param(choices=tuple(INPUTS))})
    def set_input(self, which: str) -> None:
        self.input = which
        self._settings(f"INPUT {INPUTS[which]}")

    @command("Guard: local (Lo and guard joined) or remote",
             params={"guard": Param(choices=("local", "remote"))})
    def set_guard(self, guard: str) -> None:
        self._settings("GUARD LCL" if guard == "local" else "GUARD REM")

    @command("Settling delay between trigger and reading (0 to 65000 s)",
             params={"seconds": Param(unit="s", min=0, max=65000)})
    def set_delay(self, seconds: float) -> None:
        self.delay = seconds
        self._settings(f"DELAY {seconds:g}")

    @command("Use the default settling delay for the function, range and filter")
    def set_auto_delay(self) -> None:
        self.delay = None
        self._settings("DELAY DFLT")

    @command("Frequency gate for AC readings: fast (50 ms, 4½ digits) or slow (1 s, 6½)")
    def set_fast_gate(self, on: bool) -> None:
        self.fast_gate = on
        self._settings("GATE FAST_ON" if on else "GATE FAST_OFF")

    def _long_query(self, text: str, seconds: float) -> str:
        self.transport.write(text)
        try:
            return read_line_within(self.transport, seconds)
        except TimeoutError:
            raise CommandError(f"no answer to {text} from the 1281") from None

    @command("Input zero for the selected range and function (all ranges in autorange; "
             "not ACI); true when it passed")
    def input_zero(self) -> bool:
        if self.function == "ac_amps":
            raise CommandError("input zero is not allowed in AC current (p. 5-72)")
        return self._long_query("ZERO?", 600).strip() == "0"

    @command("Confidence test (CNFTST?, about a minute); true when it passed")
    def confidence_test(self) -> bool:
        return self._long_query("CNFTST?", 300).strip() == "0"

    @command("Full selftest (*TST?); true when it passed")
    def self_test(self) -> bool:
        return self._long_query("*TST?", 3600).strip() == "0"

    @command("Send a raw command")
    def write(self, text: str) -> None:
        self.transport.write(text)

    @command("Send a raw query and return the reply")
    def query(self, text: str) -> str:
        return self.transport.query(text)


class Wavetek1281Sim(SimTransport):
    """A 1281 with all options, measuring a 10 V reference, 1 V AC at 1 kHz, 10 kΩ..."""

    INPUTS = {"DCV": 10.0000012, "ACV": 1.0000234, "OHMS": 10000.0456,
              "HI_OHMS": 250.00123e6, "TRUE_OHMS": 10000.0401, "DCI": 1.0000012e-3,
              "ACI": 0.5000023e-3}
    CHANNEL_B = 5.0000012
    FREQUENCY = 1000.0012
    BANDS = {header: bands for header, bands, *_ in FUNCTIONS.values()}
    OPTIONS = {"FILT_ON", "FILT_OFF", "RESL4", "RESL5", "RESL6", "RESL7", "RESL8",
               "FAST_ON", "FAST_OFF", "TWR", "FWR", "LOI_ON", "LOI_OFF", "FILT100HZ",
               "FILT40HZ", "FILT10HZ", "FILT1HZ", "ACCP", "DCCP", "TFER_ON", "TFER_OFF",
               "SPOT_ON", "SPOT_OFF"}

    def __init__(self, options="1,1,1,0,0,0,0", identity="DATRON INSTRUMENTS,1281,1,1",
                 **kwargs):
        super().__init__(**kwargs)
        self.option_bits, self.identity = options, identity
        self.reset()

    def reset(self) -> None:
        self.function, self.range, self.options = "DCV", 1000.0, set()
        self.trigger, self.input, self.guard, self.delay = "INT", "FRONT", "LCL", "DFLT"
        self.esr, self.exq, self.ddq = 0, [], []

    def _execution_error(self, code: int) -> None:
        self.esr |= 16
        self.exq.append(code)

    def _reading(self) -> float:
        if self.function == "DCV" and self.input in ("CH_B", "DIV_B"):
            value = self.CHANNEL_B if self.input == "CH_B" \
                else self.INPUTS["DCV"] / self.CHANNEL_B
        else:
            value = self.INPUTS[self.function]
        if self.range is not None and abs(value) >= 2 * self.range \
                and self.range != max(n for _, n in self.BANDS[self.function]):
            value = OVERLOAD
        return value

    def _function(self, header: str, args: list[str]) -> None:
        needs = {"ACV": "1", "OHMS": "3", "HI_OHMS": "3", "TRUE_OHMS": "3", "DCI": "23",
                 "ACI": "123"}.get(header, "")
        bits = self.option_bits.split(",")
        if any(bits[int(n) - 1] != "1" for n in needs):
            self._execution_error(1001)
            return
        new_range, options = self.range if header == self.function else None, set()
        for arg in args:
            if arg == "AUTO":
                if header == "HI_OHMS":
                    self.esr |= 32
                    return
                new_range = None
            elif arg.replace(".", "", 1).replace("E", "", 1).replace("+", "") \
                    .replace("-", "").isdigit():
                new_range = range_for(self.BANDS[header], float(arg))
                if new_range is None:
                    self._execution_error(1013)
                    return
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
            args = [a.strip().upper() for a in rest.split(",")] if rest.strip() else []
            reply = self.command(header.upper(), args)
            if reply is not None:
                replies.append(reply)
        return ";".join(replies) if replies else None

    def command(self, header: str, args: list[str]) -> str | None:
        if header == "*IDN?":
            return self.identity
        if header == "*OPT?":
            return self.option_bits
        if header == "LINEF?":
            return "50"
        if header == "*RST":
            self.reset()
        elif header == "*CLS":
            self.esr, self.exq, self.ddq = 0, [], []
        elif header == "*ESR?":
            esr, self.esr = self.esr, 0
            return str(esr)
        elif header == "EXQ?":
            return str(self.exq.pop() if self.exq else 0)
        elif header == "DDQ?":
            return str(self.ddq.pop() if self.ddq else 0)
        elif header == "TRG_SRCE" and args and args[0] in ("INT", "EXT"):
            self.trigger = args[0]
        elif header in self.BANDS:
            self._function(header, args)
        elif header == "INPUT" and args and args[0] in INPUTS.values():
            self.input = args[0]
        elif header == "GUARD" and args and args[0] in ("LCL", "REM"):
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
                if 0 <= value <= 65000:
                    self.delay = value
                else:
                    self._execution_error(1013)
        elif header == "X?":
            return f"{self._reading():+.8E}"
        elif header == "FREQ?":
            return f"{self.FREQUENCY:+.6E}" if self.function in ("ACV", "ACI") \
                else f"{OVERLOAD:+.4E}"
        elif header in ("ZERO?", "*TST?", "CNFTST?"):
            return "0"
        else:
            self.esr |= 32
        return None
