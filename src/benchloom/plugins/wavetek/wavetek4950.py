# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Wavetek (Datron) 4950 multifunction transfer standard, and a simulator for it.

Written from the Wavetek Model 4950 Multifunction Transfer Standard Instrument User's
Handbook, 850270 Issue 4 (December 1998): Section 5, Systems Application via the
IEEE 488 Interface (pp. 5-0-2 to 5-B2), and Section 1 for the A-D integration time.
Page numbers in the comments are that handbook's.

The 4950 is a transfer standard: it measures at nominal points only. A function
command names the range and the measurement band around the point, for example
"DCV 10,PCENT_100,LCL_GUARD" (pp. 5-28 to 5-37); outside the band the reading is
invalid (+200E+33) and a measurement event bit says why. A reading is *TRG, which
starts a sample of internal conversions, then RDG?, which waits for the sample's mean
(pp. 5-44, 5-46).
"""

from __future__ import annotations

import math
import re

from benchloom import CommandError, Device, Param, command
from benchloom.transports import SimTransport, read_line_within

INVALID = 200e33  # "+200E+33" (p. 5-46)
CONVERSION_SECONDS = 0.2  # "Integration time is fixed at 200ms" (p. 1-15)
# range selection: (lowest value, highest value, nominal range) (pp. 5-28 to 5-36)
DCV_RANGES = ((0, 0.2, 0.1), (0.2, 2, 1.0), (2, 20, 10.0), (20, 200, 100.0),
              (200, math.inf, 1000.0))
ACV_RANGES = ((0, 0.002, 1e-3), (0.002, 0.02, 10e-3), (0.02, 0.2, 0.1), (0.2, 2, 1.0),
              (2, 20, 10.0), (20, 200, 100.0), (200, math.inf, 1000.0))
OHMS_RANGES = ((2, 20, 10.0), (20, 200, 100.0), (200, 2e3, 1e3), (2e3, 20e3, 10e3),
               (20e3, 200e3, 100e3), (200e3, 2e6, 1e6), (2e6, 20e6, 10e6),
               (20e6, 200e6, 100e6))
AMP_RANGES = ((2e-5, 2e-4, 100e-6), (2e-4, 2e-3, 1e-3), (2e-3, 2e-2, 10e-3),
              (2e-2, 0.2, 100e-3), (0.2, 2, 1.0), (2, 20, 10.0))
FUNCTIONS = {"dc_volts": ("DCV", DCV_RANGES), "ac_volts": ("ACV", ACV_RANGES),
             "ohms": ("OHMS", OHMS_RANGES), "dc_amps": ("DCI", AMP_RANGES),
             "ac_amps": ("ACI", AMP_RANGES)}
UNITS = {"dc_volts": "V", "ac_volts": "V", "ohms": "Ω", "dc_amps": "A", "ac_amps": "A"}
# measurement bands each function takes (pp. 5-28, 5-31, 5-32, 5-34); ACI has none
BANDS = {"zero": "PCENT_0", "10%": "PCENT_10", "30%": "PCENT_30", "full": "PCENT_100",
         "high": "PCENT_190"}
FUNCTION_BANDS = {"dc_volts": ("zero", "full", "high"), "ac_volts": ("full", "high"),
                  "ohms": ("zero", "10%", "30%", "full", "high"), "dc_amps": ("zero", "full")}
# frequency band centres (pp. 5-30, 5-36); which range takes which band is the meter's
# to check (execution error 1026)
ACV_FREQUENCIES = ("10", "20", "30", "40", "55", "300", "1K", "10K", "20K", "30K", "50K",
                   "100K", "200K", "300K", "500K", "1M")
ACI_FREQUENCIES = ("10", "20", "30", "40", "55", "300", "1K", "5K", "10K", "20K", "30K")
# conversions per sample, low / high accuracy (table of p. 5-39)
SAMPLE_SIZE = {"dc_volts": lambda r: (4, 128 if r == 0.1 else 64),
               "ac_volts": lambda r: (1, 8),
               "ohms": lambda r: (4, 128 if r in (10.0, 10e6, 100e6) else 64),
               "dc_amps": lambda r: (1, 32), "ac_amps": lambda r: (1, 8)}
# bits of the Measurement Event Status Register (pp. 5-28 to 5-36)
MESR_BITS = {16: "above the measurement band", 8: "below the measurement band",
             4: "frequency above its band", 2: "frequency below its band"}


def frequency_value(token: str) -> float:
    multiplier = {"K": 1e3, "M": 1e6}.get(token[-1], 1)
    return float(token.rstrip("KM")) * multiplier


def nominal_range(function: str, value: float) -> float:
    for low, high, nominal in FUNCTIONS[function][1]:
        if low <= abs(value) < high:
            return nominal
    raise CommandError(f"the handbook gives no {function} range for {value:g} "
                       f"{UNITS[function]}")


class Wavetek4950(Device):
    # --- life of the device -------------------------------------------------------------
    def connect(self) -> None:
        self.transport.device_clear()
        self.transport.clear_input()
        identity = self.transport.query("*IDN?")
        fields = [f.strip() for f in identity.split(",")]
        if len(fields) < 2 or fields[1] != "4950":
            raise CommandError(f"the instrument answers *IDN? with {identity!r}, "
                               "not as a 4950")
        reply = self.transport.query("*OPT?")  # AC, ohms, current assemblies (p. 5-58)
        bits = [b.strip() for b in reply.split(",")]
        if len(bits) < 3 or any(b not in ("0", "1") for b in bits[:3]):
            raise CommandError(f"unexpected reply to *OPT?: {reply!r}")
        self.options = {n for n, b in zip(("ac", "ohms", "current"), bits) if b == "1"}
        self.reset()

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
        for bit, query in ((32, "CMQ?"), (16, "EXQ?"), (8, "DDQ?")):
            if esr & bit:
                problems += self._queue(query)
        if esr & 4:
            problems.append("query error")
        if problems:
            raise CommandError("the 4950 reported: " + "; ".join(problems))

    def _queue(self, query: str) -> list[str]:
        """Read an error queue until empty: replies are 'nnnn,"text"' (p. 5-70)."""
        messages = []
        for _ in range(20):
            reply = self.transport.query(query)
            code, _, text = reply.partition(",")
            try:
                number = int(code)
            except ValueError:
                raise CommandError(f"unexpected reply to {query}: {reply!r}") from None
            if number == 0:
                break
            message = text.strip().strip('"').strip()
            messages.append(f"{number} {message}")
        return messages

    def _settings(self, *commands: str) -> None:
        for text in commands:
            self.transport.write(text)
        self.check_errors()

    def _need(self, function: str) -> None:
        needed = {"ac_volts": ["ac"], "ohms": ["ohms"], "dc_amps": ["current"],
                  "ac_amps": ["current", "ac"]}.get(function, [])
        missing = [n for n in needed if n not in self.options]
        if missing:
            raise CommandError(f"{function} needs the {' and '.join(missing)} assembly, "
                               "which *OPT? says is not fitted")

    def _sample_seconds(self) -> float:
        low, high = SAMPLE_SIZE[self.function](self.range)
        size = high if self.accuracy == "high" else low
        # The handbook gives the 200 ms integration of each conversion but not the time
        # between conversions or AC settling, so three times the integration is allowed
        # for each conversion, and 10 s more for AC
        seconds = size * CONVERSION_SECONDS * 3 + 5
        if self.function in ("ac_volts", "ac_amps"):
            seconds += 10
        return seconds

    def _configure(self, function: str, text: str) -> None:
        self._need(function)
        self._settings(text)
        self.function = function

    def _measure(self) -> float:
        if self.function is None:
            raise CommandError("configure a measurement first (e.g. dc_voltage)")
        seconds = self._sample_seconds()
        self.transport.write("*TRG")
        self.transport.write("RDG?")
        try:
            reply = read_line_within(self.transport, seconds)
        except TimeoutError:
            self.transport.device_clear()
            raise CommandError(f"no reading from the 4950 within {seconds:.0f} s") from None
        try:
            value = float(reply)
        except ValueError:
            raise CommandError(f"unexpected reading from the 4950: {reply!r}") from None
        if value >= INVALID * 0.9:
            raise CommandError("the 4950 reading is invalid: " + self._why_invalid())
        return value

    def _why_invalid(self) -> str:
        try:
            mesr = int(float(self.transport.query("MESR?")))
        except (ValueError, TimeoutError):
            return "no reason given"
        reasons = [text for bit, text in MESR_BITS.items() if mesr & bit]
        return ", ".join(reasons) if reasons else "no reason given"

    def _guard(self, remote_guard: bool) -> str:
        return ",REM_GUARD" if remote_guard else ",LCL_GUARD"

    # --- state ----------------------------------------------------------------------------
    @command("Reset (*RST), clear the status, external triggers, and the accuracy mode "
             "of the device entry")
    def reset(self) -> None:
        self.accuracy = str(self.settings.get("accuracy", "high")).lower()
        if self.accuracy not in ("high", "low"):
            raise CommandError("accuracy in the device entry must be high or low")
        self._settings("*RST", "*CLS", "TRIG_SRCE EXT", f"ACCURACY {self.accuracy.upper()}")
        self.function, self.range, self.band = None, None, None

    # --- measurement definition -----------------------------------------------------------
    def _band(self, function: str, band: str, nominal: float) -> str:
        if band not in FUNCTION_BANDS[function]:
            raise CommandError(f"{function} takes the bands "
                               f"{', '.join(FUNCTION_BANDS[function])}")
        if band == "high" and function in ("dc_volts", "ac_volts") and nominal != 10.0:
            raise CommandError("the high (190 %) band is on the 10 V range only")
        if band == "high" and function == "ohms" and nominal == 100e6:
            raise CommandError("the high (190 %) band is not on the 100 MΩ range")
        if band == "10%" and nominal != 10.0:
            raise CommandError("the 10 % band is on the 10 Ω range only")
        return BANDS[band]

    @command("Measure DC volts at a nominal point: range by value (0.1 to 1000 V), band "
             "zero (0 V ±10 %), full (nominal ±10 %) or high (19 V on the 10 V range)",
             unit="V", params={"band": Param(choices=FUNCTION_BANDS["dc_volts"])})
    def dc_voltage(self, volts: float, band: str = "full", remote_guard: bool = False) -> float:
        nominal = nominal_range("dc_volts", volts)
        self._configure("dc_volts", f"DCV {nominal:G},{self._band('dc_volts', band, nominal)}"
                        + self._guard(remote_guard))
        self.range, self.band = nominal, band
        return self._measure()

    @command("Measure AC volts at a nominal point and frequency band (centre: 10 ... 1M Hz, "
             "±10 %); band full or high (10 V range, 1 kHz)", unit="V",
             params={"frequency": Param(choices=ACV_FREQUENCIES),
                     "band": Param(choices=FUNCTION_BANDS["ac_volts"])})
    def ac_voltage(self, volts: float, frequency: str = "1K", band: str = "full",
                   four_wire: bool = True, remote_guard: bool = False) -> float:
        nominal = nominal_range("ac_volts", volts)
        if not four_wire and (nominal not in (1.0, 10.0) or frequency_value(frequency) <= 100e3):
            raise CommandError("two-wire AC volts needs the 1 V or 10 V range and a frequency "
                               "band above 100 kHz (p. 5-31)")
        text = (f"ACV {nominal:G},FREQ_{frequency},{self._band('ac_volts', band, nominal)}"
                + (",FWR" if four_wire else ",TWR") + self._guard(remote_guard))
        self._configure("ac_volts", text)
        self.range, self.band = nominal, band
        return self._measure()

    @command("Measure resistance at a nominal point (10 Ω to 100 MΩ); band zero, 10% "
             "(10 Ω only), 30%, full or high (190 %, not 100 MΩ); two-wire needs the "
             "lead's serial number entered", unit="Ω",
             params={"band": Param(choices=FUNCTION_BANDS["ohms"])})
    def resistance(self, ohms: float, band: str = "full", four_wire: bool = True,
                   remote_guard: bool = False) -> float:
        nominal = nominal_range("ohms", ohms)
        text = (f"OHMS {nominal:G},{self._band('ohms', band, nominal)}"
                + (",FWR" if four_wire else ",TWR") + self._guard(remote_guard))
        self._configure("ohms", text)
        self.range, self.band = nominal, band
        return self._measure()

    @command("Measure DC current at a nominal point (100 µA to 10 A; 10 A needs the shunt "
             "serial number); band zero or full", unit="A",
             params={"band": Param(choices=FUNCTION_BANDS["dc_amps"])})
    def dc_current(self, amps: float, band: str = "full", remote_guard: bool = False) -> float:
        nominal = nominal_range("dc_amps", amps)
        self._configure("dc_amps", f"DCI {nominal:G},{self._band('dc_amps', band, nominal)}"
                        + self._guard(remote_guard))
        self.range, self.band = nominal, band
        return self._measure()

    @command("Measure AC current at a nominal point and frequency band (centre 10 ... 30k "
             "Hz, ±10 %); 100 µA to 10 A", unit="A",
             params={"frequency": Param(choices=ACI_FREQUENCIES)})
    def ac_current(self, amps: float, frequency: str = "1K", remote_guard: bool = False) -> float:
        nominal = nominal_range("ac_amps", amps)
        self._configure("ac_amps", f"ACI {nominal:G},FREQ_{frequency}"
                        + self._guard(remote_guard))
        self.range, self.band = nominal, "full"
        return self._measure()

    @command("Measure again with the present settings")
    def measure(self) -> float:
        return self._measure()

    @command("Take several measurements in a row with the present settings",
             params={"count": Param(min=1, max=1000)})
    def measure_many(self, count: int) -> list:
        return [self._measure() for _ in range(count)]

    # --- results --------------------------------------------------------------------------
    @command("The frequency of the last AC measurement", unit="Hz")
    def get_frequency(self) -> float:
        value = float(self.transport.query("FREQ?"))
        if value >= INVALID * 0.9:
            raise CommandError("no valid frequency (DC function, or the circuits could not "
                               "measure it)")
        return value

    @command("Quality of the last measurement: the standard error of the mean of its sample, "
             "in ppm of full range, ppm of the reading, or absolute (the function's unit)",
             params={"kind": Param(choices=("ppm_range", "ppm_reading", "absolute"))})
    def get_quality(self, kind: str = "ppm_reading") -> float:
        query = {"ppm_range": "DEVTN?", "ppm_reading": "DEVTN? READING",
                 "absolute": "DEVTN? ABSOLUTE"}[kind]
        value = float(self.transport.query(query))
        if value >= INVALID * 0.9:
            raise CommandError("no valid quality: out of band, or a one-reading sample "
                               "(low accuracy in ACV, DCI or ACI)")
        return value

    @command("Number of conversions in the last sample")
    def get_sample_size(self) -> int:
        return int(float(self.transport.query("SMP_SIZE?")))

    @command("Minutes left until the warm-up period (up to 6 hours) is over", unit="min")
    def get_warm_up_minutes(self) -> float:
        reply = self.transport.query("STANDBY?")
        match = re.search(r"(\d+)\s*:\s*(\d+)", reply)
        if not match:
            raise CommandError(f"unexpected reply to STANDBY?: {reply!r}")
        return int(match.group(1)) * 60 + int(match.group(2))

    # --- parameters -----------------------------------------------------------------------
    @command("Accuracy mode: high (large samples, one more digit) or low (fast)",
             params={"mode": Param(choices=("high", "low"))})
    def set_accuracy(self, mode: str) -> None:
        self.accuracy = mode
        self._settings(f"ACCURACY {mode.upper()}")

    @command("Corrections applied: certified (the last certified calibration) or base "
             "(from manufacture, for loop-integrity checks)",
             params={"corrections": Param(choices=("certified", "base"))})
    def set_corrections(self, corrections: str) -> None:
        self._settings(f"CORRECTN {corrections.upper()}")

    @command("Band limits: true reports readings outside the band as invalid (the "
             "default), false measures outside them (use with great care)")
    def set_band_limits(self, on: bool) -> None:
        self._settings("BAND ON" if on else "BAND OFF")

    def _long_query(self, text: str, seconds: float) -> str:
        self.transport.write(text)
        try:
            return read_line_within(self.transport, seconds)
        except TimeoutError:
            raise CommandError(f"no answer to {text} from the 4950") from None

    @command("Input zero on the present DCV, DCI or ohms range: needs the zero band, high "
             "accuracy and the zero connection of p. 5-45; true when it passed")
    def input_zero(self) -> bool:
        if self.function not in ("dc_volts", "dc_amps", "ohms") or self.band != "zero" \
                or self.accuracy != "high":
            raise CommandError("input zero needs a DCV, DCI or ohms range in the zero band "
                               "with high accuracy (p. 5-44)")
        ok = self._long_query("ZERO?", self._sample_seconds() + 30).strip() == "0"
        self.check_errors()
        return ok

    @command("Selftest (*TST?); true when it passed (failures are in get_errors)")
    def self_test(self) -> bool:
        return self._long_query("*TST?", 3600).strip() == "0"

    @command("Resume a selftest halted by a failure (RTST?); true when it passed")
    def resume_self_test(self) -> bool:
        return self._long_query("RTST?", 3600).strip() == "0"

    @command("Read and clear the error queues, as text")
    def get_errors(self) -> str:
        return "; ".join(self._queue("CMQ?") + self._queue("EXQ?") + self._queue("DDQ?"))

    @command("Send a raw command")
    def write(self, text: str) -> None:
        self.transport.write(text)

    @command("Send a raw query and return the reply")
    def query(self, text: str) -> str:
        return self.transport.query(text)


class Wavetek4950Sim(SimTransport):
    """A 4950 with all assemblies, fed with signals at nominal points: 10 V DC, 1 V AC at
    1 kHz, 10 kΩ, 1 mA DC and 1 mA AC."""

    SIGNALS = {"DCV": 10.0000012, "ACV": 1.0000234, "OHMS": 10000.0456, "DCI": 1.0000012e-3,
               "ACI": 1.0000023e-3}
    FREQUENCY = 1000.0012
    BAND_SPAN = {"PCENT_0": (0.0, 0.1), "PCENT_10": (0.1, 0.1), "PCENT_30": (0.3, 0.1),
                 "PCENT_100": (1.0, 0.1), "PCENT_190": (1.9, 0.1)}

    def __init__(self, options="1,1,1", **kwargs):
        super().__init__(**kwargs)
        self.option_bits = options
        self.reset()

    def reset(self) -> None:
        self.function, self.range, self.band, self.freq = "DCV", 1000.0, "PCENT_0", None
        self.accuracy, self.trigger, self.corrections, self.band_limits = \
            "HIGH", "EXT", "CERTIFIED", True
        self.guard, self.wires = "LCL_GUARD", "FWR"
        self.esr, self.mesr, self.queues = 0, 0, {"CMQ?": [], "EXQ?": [], "DDQ?": []}
        self.result = None

    def _error(self, queue: str, code: int, text: str) -> None:
        self.esr |= {"CMQ?": 32, "EXQ?": 16, "DDQ?": 8}[queue]
        self.queues[queue].append(f'{code},"{text}"')

    def _function(self, header: str, args: list[str]) -> None:
        if not args:
            self._error("CMQ?", 1, "missing range")
            return
        try:
            value = float(args[0])
        except ValueError:
            self._error("CMQ?", 2, "bad range")
            return
        bands = [a for a in args[1:] if a.startswith("PCENT_")]
        freqs = [a for a in args[1:] if a.startswith("FREQ_")]
        if header in ("DCV", "OHMS", "DCI") and not bands:
            self._error("EXQ?", 1026, "Illegal band selection")
            return
        name = {"DCV": "dc_volts", "ACV": "ac_volts", "OHMS": "ohms", "DCI": "dc_amps",
                "ACI": "ac_amps"}[header]
        self.function, self.range = header, nominal_range(name, value)
        self.band = bands[0] if bands else "PCENT_100"
        self.freq = freqs[0] if freqs else None
        for a in args[1:]:
            if a in ("LCL_GUARD", "REM_GUARD"):
                self.guard = a
            elif a in ("TWR", "FWR"):
                self.wires = a

    def _sample(self) -> None:
        value = self.SIGNALS[self.function]
        centre, span = self.BAND_SPAN[self.band]
        low, high = (centre - span) * self.range, (centre + span) * self.range
        self.mesr = 0
        if self.band_limits and not low <= abs(value) <= high:
            self.mesr |= 16 if abs(value) > high else 8
            self.result = INVALID
        else:
            self.result = value

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
            return "DATRON WAVETEK,4950,456789-01.00,890197-01.00"  # the form of p. 5-51
        if header == "*OPT?":
            return self.option_bits
        if header == "*RST":
            self.reset()
        elif header == "*CLS":
            self.esr = 0
            self.queues = {"CMQ?": [], "EXQ?": [], "DDQ?": []}
        elif header == "*ESR?":
            esr, self.esr = self.esr, 0
            return str(esr)
        elif header in self.queues:
            queue = self.queues[header]
            return queue.pop() if queue else '0,"Empty"'
        elif header == "TRIG_SRCE" and args and args[0] in ("INT", "EXT"):
            self.trigger = args[0]
        elif header == "ACCURACY" and args and args[0] in ("HIGH", "LOW"):
            self.accuracy = args[0]
        elif header == "CORRECTN" and args and args[0] in ("BASE", "CERTIFIED"):
            self.corrections = args[0]
        elif header == "BAND" and args and args[0] in ("ON", "OFF"):
            self.band_limits = args[0] == "ON"
        elif header in ("DCV", "ACV", "OHMS", "DCI", "ACI"):
            self._function(header, args)
        elif header == "*TRG":
            if self.trigger != "EXT":
                self._error("EXQ?", 1025, "External trigger mode not selected")
            else:
                self._sample()
        elif header == "RDG?":
            return f"{self.result if self.result is not None else INVALID:+.7E}"
        elif header == "MESR?":
            mesr, self.mesr = self.mesr, 0
            return str(mesr)
        elif header == "FREQ?":
            return f"{self.FREQUENCY:+.6E}" if self.function in ("ACV", "ACI") \
                else f"{INVALID:+.0E}"
        elif header == "DEVTN?":
            return "+1.2000E-01" if self.result not in (None, INVALID) else f"{INVALID:+.0E}"
        elif header == "SMP_SIZE?":
            return "64" if self.accuracy == "HIGH" else "4"
        elif header == "STANDBY?":
            return '"01:30"'
        elif header in ("ZERO?", "*TST?", "RTST?"):
            return "0"
        else:
            self._error("CMQ?", 100, "Unrecognized command")
        return None
