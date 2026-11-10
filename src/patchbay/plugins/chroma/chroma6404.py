"""Chroma 6404 programmable AC source, and a simulator for it.

Commands, ranges and status bits are from the Chroma 6404/6408 user's manual
(version 1.6, November 2015), chapter 6 "Remote Operation".
"""

from __future__ import annotations

import math
import random

from patchbay import Param, ScpiDevice, command
from patchbay.scpi import ScpiSimulator, SimError, sim_bool, sim_number

MAX_VOLTS = 300.0
MIN_HZ, MAX_HZ = 45.0, 500.0
MAX_PEAK_AMPS = 10.0  # 6404: 0 to 10 A in 0.04 A steps
MAX_VA = 375.0

# Questionable status register bits
FAULTS = {1: "under-voltage", 2: "short circuit", 8: "over-temperature",
          256: "over-current", 512: "fan failure", 1024: "over-power",
          2048: "peak current limit"}


class Chroma6404(ScpiDevice):
    def connect(self) -> None:
        if self.transport.scheme in ("serial", "tcp"):
            self.transport.write("SYST:REM")  # only needed (and accepted) over RS-232

    @command("Set the output voltage (RMS)", params={"volts": Param(unit="V", min=0, max=MAX_VOLTS)})
    def set_voltage(self, volts: float) -> None:
        self.send(f"VOLT {volts:.1f}")

    @command("Set the output frequency", params={"hertz": Param(unit="Hz", min=MIN_HZ, max=MAX_HZ)})
    def set_frequency(self, hertz: float) -> None:
        self.send(f"FREQ {hertz:.1f}")

    @command("Select the voltage range: 150, 300 or auto",
             params={"range": Param(choices=("150", "300", "auto"))})
    def set_range(self, range: str) -> None:  # noqa: A002 - shown to users as "range"
        self.send("VOLT:RANG:AUTO ON" if range == "auto" else f"VOLT:RANG {range}")

    @command("Set the peak current limit",
             params={"amps": Param(unit="A", min=0, max=MAX_PEAK_AMPS)})
    def set_current_limit(self, amps: float) -> None:
        self.send(f"CURR:PEAK {amps:.2f}")

    @command("Set the highest voltage the output may be set to (protects the load)",
             params={"volts": Param(unit="V", min=0, max=MAX_VOLTS)})
    def set_voltage_limit(self, volts: float) -> None:
        self.send(f"VOLT:LIM {volts:.1f}")

    @command("Switch the output on or off")
    def set_output(self, on: bool) -> None:
        self.send("OUTP ON" if on else "OUTP OFF")

    @command("Is the output on?")
    def is_output_on(self) -> bool:
        return self.ask_number("OUTP?") == 1

    @command("Clear a protection shutdown; switch the output on again afterwards")
    def clear_protection(self) -> None:
        self.send("OUTP:PROT:CLE")

    @command("Measure the output voltage (RMS)", unit="V")
    def measure_voltage(self) -> float:
        return self.ask_number("MEAS:VOLT:AC?")

    @command("Measure the output current (RMS)", unit="A")
    def measure_current(self) -> float:
        return self.ask_number("MEAS:CURR:AC?")

    @command("Measure the real output power", unit="W")
    def measure_power(self) -> float:
        return self.ask_number("MEAS:POW:AC?")

    @command("Measure the power factor of the load")
    def measure_power_factor(self) -> float:
        return self.ask_number("MEAS:POW:AC:PFAC?")

    @command("Measure the output frequency", unit="Hz")
    def measure_frequency(self) -> float:
        return self.ask_number("MEAS:FREQ?")

    @command("Measure the crest factor of the output current")
    def measure_crest_factor(self) -> float:
        return self.ask_number("MEAS:CURR:CRES?")

    @command("Active protections, e.g. 'over-power', or an empty text when there are none")
    def get_faults(self) -> str:
        bits = int(self.ask_number("STAT:QUES:COND?"))
        return ", ".join(name for bit, name in FAULTS.items() if bits & bit)

    def close(self) -> None:
        if not self.leave_output_on():
            self.transport.write("OUTP OFF")
        if self.transport.scheme in ("serial", "tcp"):
            self.transport.write("SYST:LOC")


class Chroma6404Sim(ScpiSimulator):
    """A 6404 driving a 100 Ω resistive load."""

    identity = "Chroma,6404,0,1.00"
    LOAD_OHMS = 100.0

    def reset(self) -> None:
        self.volts, self.hertz, self.output = 0.0, 60.0, False
        self.range, self.auto_range = 150, False
        self.peak_amps, self.limit = MAX_PEAK_AMPS, MAX_VOLTS
        self.tripped = 0

    def _output(self) -> tuple[float, float, int]:
        """RMS voltage, RMS current and questionable bits."""
        if not self.output or self.tripped:
            return 0.0, 0.0, self.tripped
        full_scale = 300 if self.auto_range and self.volts > 150 else self.range
        volts = min(self.volts, self.limit, full_scale)
        amps = volts / self.LOAD_OHMS
        bits = 0
        if amps * math.sqrt(2) > self.peak_amps:  # the source clamps the peak current
            amps = self.peak_amps / math.sqrt(2)
            volts = amps * self.LOAD_OHMS
            bits |= 2048
        return volts, amps, bits

    def _check_power(self) -> None:
        volts, amps, _ = self._output()
        if volts * amps > MAX_VA:
            self.tripped |= 1024
            self.output = False

    def routes(self):
        def setter(attribute, low, high):
            def handler(arg):
                setattr(self, attribute, sim_number(arg, low, high))
                self._check_power()
            return handler

        def output(arg):
            self.output = sim_bool(arg)
            self._check_power()

        def voltage_range(arg):
            value = sim_number(arg, 150, 300)
            if value not in (150, 300):
                raise SimError(-224, "Illegal parameter value")
            self.range, self.auto_range = int(value), False
            self._check_power()

        def auto_range(arg):
            self.auto_range = sim_bool(arg)
            self._check_power()

        def clear(arg):
            self.tripped = 0

        def measure(kind):
            def handler(arg):
                volts, amps, _ = self._output()
                noise = random.gauss(1, 0.0005) if volts else 1
                value = {"V": volts, "I": amps, "P": volts * amps, "PF": 1.0 if amps else 0.0,
                         "F": self.hertz if volts else 0.0,
                         "CF": math.sqrt(2) if amps else 0.0}[kind]
                return f"{value * (noise if kind in 'VIP' else 1):.3f}"
            return handler

        return [
            (r"(SOUR:)?VOLT(:LEV)?", setter("volts", 0, MAX_VOLTS)),
            (r"(SOUR:)?VOLT(:LEV)?\?", lambda arg: f"{self.volts:.1f}"),
            (r"(SOUR:)?FREQ", setter("hertz", MIN_HZ, MAX_HZ)),
            (r"(SOUR:)?FREQ\?", lambda arg: f"{self.hertz:.1f}"),
            (r"(SOUR:)?VOLT:RANG", voltage_range),
            (r"(SOUR:)?VOLT:RANG\?", lambda arg: str(self.range)),
            (r"(SOUR:)?VOLT:RANG:AUTO", auto_range),
            (r"(SOUR:)?VOLT:LIM", setter("limit", 0, MAX_VOLTS)),
            (r"(SOUR:)?CURR:PEAK", setter("peak_amps", 0, MAX_PEAK_AMPS)),
            (r"OUTP(:STAT)?", output),
            (r"OUTP(:STAT)?\?", lambda arg: "1" if self.output else "0"),
            (r"OUTP:PROT:CLE", clear),
            (r"MEAS:VOLT:AC\?", measure("V")),
            (r"MEAS:CURR:AC\?", measure("I")),
            (r"MEAS:POW:AC\?", measure("P")),
            (r"MEAS:POW:AC:PFAC\?", measure("PF")),
            (r"MEAS:FREQ\?", measure("F")),
            (r"MEAS:CURR:CRES\?", measure("CF")),
            (r"STAT:QUES:COND\?", lambda arg: str(self._output()[2])),
            (r"SYST:(REM|LOC)", lambda arg: None),
        ]
