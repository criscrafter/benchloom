"""HP 53131A / 53132A universal counter, and a simulator for it.

Commands are from the HP 53131A/132A Programming Guide (part no. 53131-90044,
November 1996). Measurements use :CONFigure, the gate settings, then :READ?, so
the gate time set with set_gate_time applies to frequency, period and ratio.

A measurement waits for its signal; with none the counter holds off every further
command until a device clear. The driver therefore sends a device clear when a
reading does not arrive in time.
"""

from __future__ import annotations

import math
import random
import re

from patchbay import CommandError, Param, ScpiDevice, command
from patchbay.scpi import ScpiSimulator, SimError, sim_bool, sim_number

NOT_A_NUMBER = 9.91e37  # the counter's "no valid result"
MAX_LEVEL = 51.25  # trigger level with x10 attenuation; x1 allows ±5.125 V
MIN_GATE, MAX_GATE = 1e-3, 1000.0

CHANNEL = Param(min=1, max=2)
ANY_CHANNEL = Param(min=1, max=3)  # channel 3 is the optional RF input


class HP53131A(ScpiDevice):
    def connect(self) -> None:
        self.gate_time = float(self.settings.get("gate_time", 0.1))
        self.base_timeout = self.transport.timeout

    # --- helpers ----------------------------------------------------------------------
    def _settings(self, *commands: str) -> None:
        """Send several settings, then check the error queue once."""
        for text in commands:
            self.transport.write(text)
        self.check_errors()

    def _read(self, configure: str, channels: str, gated: bool = False,
              wait: float = 0.0) -> float:
        setup = [f":CONF:{configure} {channels}".rstrip()]
        if gated:
            setup += [":FREQ:ARM:STAR:SOUR IMM", ":FREQ:ARM:STOP:SOUR TIM",
                      f":FREQ:ARM:STOP:TIM {self.gate_time:.3f}"]
        self._settings(*setup)
        # a reading takes the gate time plus the counter's own overhead
        self.transport.set_timeout(max(self.base_timeout, (self.gate_time if gated else 0)
                                       + wait + 2.0))
        try:
            reply = self.transport.query(":READ?")
        except TimeoutError:
            self.transport.device_clear()  # the counter holds off commands until then
            where = " and ".join(f"channel {n}" for n in re.findall(r"@(\d)", channels))
            raise CommandError(
                f"no reading on {where or 'channel 1'}: is a signal connected and the "
                "trigger level right?") from None
        finally:
            self.transport.set_timeout(self.base_timeout)
        try:
            value = float(reply)
        except ValueError:
            raise CommandError(f":READ? returned {reply!r}, not a number") from None
        return math.nan if value >= NOT_A_NUMBER else value

    # --- measurements -----------------------------------------------------------------
    @command("Measure frequency on channel 1, 2 or 3", unit="Hz", params={"channel": ANY_CHANNEL})
    def measure_frequency(self, channel: int = 1) -> float:
        return self._read("FREQ", f"(@{channel})", gated=True)

    @command("Measure period on channel 1, 2 or 3", unit="s", params={"channel": ANY_CHANNEL})
    def measure_period(self, channel: int = 1) -> float:
        return self._read("PER", f"(@{channel})", gated=True)

    @command("Measure the frequency ratio channel 1 / channel 2")
    def measure_ratio(self) -> float:
        return self._read("FREQ:RAT", "(@1),(@2)", gated=True)

    @command("Measure the time interval from channel 1 to channel 2", unit="s")
    def measure_time_interval(self) -> float:
        return self._read("TINT", "(@1),(@2)")

    @command("Measure the phase of channel 2 relative to channel 1", unit="°")
    def measure_phase(self) -> float:
        return self._read("PHAS", "(@1),(@2)")

    @command("Measure the positive pulse width on channel 1", unit="s")
    def measure_pulse_width(self) -> float:
        return self._read("PWID", "(@1)")

    @command("Measure the negative pulse width on channel 1", unit="s")
    def measure_negative_width(self) -> float:
        return self._read("NWID", "(@1)")

    @command("Measure the duty cycle on channel 1 (0 to 1)")
    def measure_duty_cycle(self) -> float:
        return self._read("DCYC", "(@1)")

    @command("Measure the rise time (10 % to 90 %) on channel 1", unit="s")
    def measure_rise_time(self) -> float:
        return self._read("RTIM", "(@1)")

    @command("Measure the fall time (90 % to 10 %) on channel 1", unit="s")
    def measure_fall_time(self) -> float:
        return self._read("FTIM", "(@1)")

    @command("Measure the positive peak voltage on channel 1 or 2", unit="V",
             params={"channel": CHANNEL})
    def measure_max_voltage(self, channel: int = 1) -> float:
        return self._read("MAX", f"(@{channel})")

    @command("Measure the negative peak voltage on channel 1 or 2", unit="V",
             params={"channel": CHANNEL})
    def measure_min_voltage(self, channel: int = 1) -> float:
        return self._read("MIN", f"(@{channel})")

    @command("Measure the peak-to-peak voltage on channel 1 or 2", unit="V",
             params={"channel": CHANNEL})
    def measure_peak_to_peak(self, channel: int = 1) -> float:
        return self._read("PTP", f"(@{channel})")

    @command("Count the events on channel 1 during a time", unit="events",
             params={"seconds": Param(unit="s", min=1e-3, max=MAX_GATE)})
    def measure_totalize(self, seconds: float) -> float:
        return self._read("TOT:TIM", f"{seconds:.3f},(@1)", wait=seconds)

    # --- settings ---------------------------------------------------------------------
    @command("Set the gate time for frequency, period and ratio (longer = more digits)",
             params={"seconds": Param(unit="s", min=MIN_GATE, max=MAX_GATE)})
    def set_gate_time(self, seconds: float) -> None:
        self.gate_time = seconds

    @command("Set the input coupling of channel 1 or 2",
             params={"channel": CHANNEL, "coupling": Param(choices=("AC", "DC"))})
    def set_coupling(self, channel: int, coupling: str) -> None:
        self._settings(f":INP{channel}:COUP {coupling}")

    @command("Set the input impedance of channel 1 or 2: 50 Ω or 1 MΩ",
             params={"channel": CHANNEL, "impedance": Param(choices=("50", "1M"))})
    def set_impedance(self, channel: int, impedance: str) -> None:
        self._settings(f":INP{channel}:IMP {'50' if impedance == '50' else '1E6'}")

    @command("Set the input attenuation of channel 1 or 2: x1 or x10",
             params={"channel": CHANNEL, "factor": Param(choices=("1", "10"))})
    def set_attenuation(self, channel: int, factor: str) -> None:
        self._settings(f":INP{channel}:ATT {factor}")

    @command("Switch the 100 kHz low-pass filter of channel 1 or 2 on or off",
             params={"channel": CHANNEL})
    def set_filter(self, channel: int, on: bool) -> None:
        self._settings(f":INP{channel}:FILT {'ON' if on else 'OFF'}")

    @command("Set a fixed trigger level on channel 1 or 2 (turns auto trigger off)",
             params={"channel": CHANNEL, "volts": Param(unit="V", min=-MAX_LEVEL, max=MAX_LEVEL)})
    def set_trigger_level(self, channel: int, volts: float) -> None:
        self._settings(f":EVEN{channel}:LEV {volts:.3f}")

    @command("Let the counter choose the trigger level of channel 1 or 2",
             params={"channel": CHANNEL})
    def set_auto_trigger(self, channel: int) -> None:
        self._settings(f":EVEN{channel}:LEV:AUTO ON")

    @command("Trigger on the rising or falling edge on channel 1 or 2",
             params={"channel": CHANNEL, "slope": Param(choices=("rising", "falling"))})
    def set_trigger_slope(self, channel: int, slope: str) -> None:
        self._settings(f":EVEN{channel}:SLOP {'POS' if slope == 'rising' else 'NEG'}")


# --- simulator ------------------------------------------------------------------------
class HP53131ASim(ScpiSimulator):
    """A counter with a 10 MHz, 1 Vpp sine on channel 1 and a 1 kHz, 0-5 V square
    wave (30 % duty cycle, 10 ns edges) on channel 2."""

    identity = "HEWLETT-PACKARD,53131A,0,3703"
    SIGNALS = {
        1: {"freq": 10e6, "low": -0.5, "high": 0.5, "duty": 0.5, "edge": 25e-9},
        2: {"freq": 1e3, "low": 0.0, "high": 5.0, "duty": 0.3, "edge": 10e-9},
        3: None,  # nothing on the RF input
    }

    def reset(self) -> None:
        self.function, self.channels = "FREQ", [1]
        self.gate = 0.1
        self.inputs = {n: {"COUP": "AC", "IMP": 1e6, "ATT": 1, "FILT": False,
                           "LEV": None, "SLOP": "POS"} for n in (1, 2)}

    def _value(self) -> float:
        first = self.SIGNALS.get(self.channels[0])
        if first is None:
            return math.nan  # no signal: the real counter would wait
        f, kind = first["freq"], self.function
        jitter = 1 + random.gauss(0, 1e-9 / max(self.gate, 1e-3))
        if kind == "FREQ":
            return f * jitter
        if kind == "PER":
            return 1 / (f * jitter)
        if kind == "FREQ:RAT":
            return f / self.SIGNALS[self.channels[1]]["freq"]
        if kind == "TINT":
            return 1 / self.SIGNALS[2]["freq"] / 4
        if kind == "PHAS":
            return 90.0
        if kind == "PWID":
            return first["duty"] / f
        if kind == "NWID":
            return (1 - first["duty"]) / f
        if kind == "DCYC":
            return first["duty"]
        if kind in ("RTIM", "FTIM"):
            return first["edge"]
        if kind == "MAX":
            return first["high"]
        if kind == "MIN":
            return first["low"]
        if kind == "PTP":
            return first["high"] - first["low"]
        if kind == "TOT:TIM":
            return float(round(f * self.total_time))
        raise SimError(-113, "Undefined header")

    def routes(self):
        def configure(header):
            def handler(arg):
                text = arg.replace(" ", "")
                channels = [int(n) for n in re.findall(r"@(\d)", text)] or [1]
                if any(n not in self.SIGNALS for n in channels):
                    raise SimError(-222, "Data out of range")
                if header == "TOT:TIM":
                    self.total_time = sim_number(text.split(",")[0], 1e-3, MAX_GATE)
                self.function, self.channels = header, channels
            return handler

        def read(arg):
            value = self._value()
            if math.isnan(value):
                return None  # a real counter keeps waiting: the driver times out
            return f"{value:+.11E}"

        def make_input(channel, key, parse):
            def handler(arg):
                self.inputs[channel][key] = parse(arg)
            return handler

        def coupling(arg):
            if arg.upper() not in ("AC", "DC"):
                raise SimError(-224, "Illegal parameter value")
            return arg.upper()

        def impedance(arg):
            value = float(arg)
            if value not in (50.0, 1e6):
                raise SimError(-224, "Illegal parameter value")
            return value

        def attenuation(arg):
            value = int(float(arg))
            if value not in (1, 10):
                raise SimError(-224, "Illegal parameter value")
            return value

        def slope(arg):
            if arg.upper() not in ("POS", "NEG"):
                raise SimError(-224, "Illegal parameter value")
            return arg.upper()

        def gate(arg):
            self.gate = sim_number(arg, MIN_GATE, MAX_GATE)

        routes = [(r"(SENS:)?CONF:" + re.escape(name), configure(name))
                  for name in ("FREQ:RAT", "FREQ", "PER", "TINT", "PHAS", "PWID", "NWID",
                               "DCYC", "RTIM", "FTIM", "MAX", "MIN", "PTP", "TOT:TIM")]
        routes += [
            (r"READ\?", read),
            (r"(SENS:)?FREQ:ARM:STAR:SOUR", lambda arg: None),
            (r"(SENS:)?FREQ:ARM:STOP:SOUR", lambda arg: None),
            (r"(SENS:)?FREQ:ARM:STOP:TIM", gate),
        ]
        for n in (1, 2):
            limit = 5.125 * self.inputs[n]["ATT"]
            routes += [
                (rf"INP{n}:COUP", make_input(n, "COUP", coupling)),
                (rf"INP{n}:IMP", make_input(n, "IMP", impedance)),
                (rf"INP{n}:ATT", make_input(n, "ATT", attenuation)),
                (rf"INP{n}:FILT", make_input(n, "FILT", sim_bool)),
                (rf"(SENS:)?EVEN{n}:LEV",
                 make_input(n, "LEV", lambda arg, limit=limit: sim_number(arg, -limit, limit))),
                (rf"(SENS:)?EVEN{n}:LEV:AUTO", make_input(n, "LEV", lambda arg: None)),
                (rf"(SENS:)?EVEN{n}:SLOP", make_input(n, "SLOP", slope)),
                (rf"INP{n}:COUP\?", lambda arg, n=n: self.inputs[n]["COUP"]),
            ]
        return routes
