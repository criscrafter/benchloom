# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""HP 6655A DC power supply (120 V, 4 A), and a simulator for it.

Commands and limits are from the Agilent programming guide for the 664xA, 665xA,
667xA, 668xA and 669xA supplies (part no. 5964-8269).
"""

from __future__ import annotations

import random

from benchloom import Param, ScpiDevice, command
from benchloom.scpi import ScpiSimulator, sim_bool, sim_number

MAX_VOLTS = 122.85  # programmable maximum (Table 3-1)
MAX_AMPS = 4.095
MAX_OVP = 132.0

# Status Operation register (CV/CC) and Questionable register (faults) bits
OPER_CV, OPER_CC = 256, 1024
QUES_BITS = {1: "over-voltage", 2: "over-current", 16: "over-temperature",
             512: "remote inhibit", 1024: "unregulated"}


class HP6655A(ScpiDevice):
    @command("Set the output voltage", params={"volts": Param(unit="V", min=0, max=MAX_VOLTS)})
    def set_voltage(self, volts: float) -> None:
        self.send(f"VOLT {volts:.4f}")

    @command("Set the current limit", params={"amps": Param(unit="A", min=0, max=MAX_AMPS)})
    def set_current(self, amps: float) -> None:
        self.send(f"CURR {amps:.4f}")

    @command("Switch the output on or off")
    def set_output(self, on: bool) -> None:
        self.send("OUTP ON" if on else "OUTP OFF")

    @command("Is the output on?")
    def is_output_on(self) -> bool:
        return self.ask_number("OUTP?") == 1

    @command("Set the over-voltage protection level",
             params={"volts": Param(unit="V", min=0, max=MAX_OVP)})
    def set_ovp(self, volts: float) -> None:
        self.send(f"VOLT:PROT {volts:.3f}")

    @command("Switch over-current protection on or off (trips instead of limiting)")
    def set_ocp(self, on: bool) -> None:
        self.send("CURR:PROT:STAT ON" if on else "CURR:PROT:STAT OFF")

    @command("Clear a tripped protection; the output returns to its previous state")
    def clear_protection(self) -> None:
        self.send("OUTP:PROT:CLE")

    @command("Measure the output voltage", unit="V")
    def measure_voltage(self) -> float:
        return self.ask_number("MEAS:VOLT?")

    @command("Measure the output current", unit="A")
    def measure_current(self) -> float:
        return self.ask_number("MEAS:CURR?")

    @command("Measure the output power (voltage × current)", unit="W")
    def measure_power(self) -> float:
        return self.measure_voltage() * self.measure_current()

    @command("Regulation mode: CV (constant voltage), CC (current limit) or OFF")
    def get_mode(self) -> str:
        bits = int(self.ask_number("STAT:OPER:COND?"))
        if bits & OPER_CC:
            return "CC"
        if bits & OPER_CV:
            return "CV"
        return "OFF"

    @command("Active faults, e.g. 'over-voltage', or an empty text when there are none")
    def get_faults(self) -> str:
        bits = int(self.ask_number("STAT:QUES:COND?"))
        return ", ".join(name for bit, name in QUES_BITS.items() if bits & bit)

    def close(self) -> None:
        if not self.leave_output_on():
            self.transport.write("OUTP OFF")


class HP6655ASim(ScpiSimulator):
    """A 6655A feeding a 50 Ω resistor."""

    identity = "Hewlett-Packard,6655A,0,A.00.01"
    LOAD_OHMS = 50.0

    def reset(self) -> None:
        self.volts, self.amps, self.output = 0.0, 0.016, False
        self.ovp, self.ocp = MAX_OVP, False
        self.tripped = 0  # questionable bits of a latched protection

    def _state(self) -> tuple[float, float, int]:
        """Output voltage, current and operation bits."""
        if not self.output or self.tripped:
            return 0.0, 0.0, 0
        amps = self.volts / self.LOAD_OHMS
        if amps <= self.amps:
            return self.volts, amps, OPER_CV
        return self.amps * self.LOAD_OHMS, self.amps, OPER_CC

    def _check_protection(self) -> None:
        if self.output and self.volts > self.ovp:
            self.tripped |= 1
        if self.output and self.ocp and self._state()[2] == OPER_CC:
            self.tripped |= 2

    def routes(self):
        def setter(attribute, low, high):
            def handler(arg):
                setattr(self, attribute, sim_number(arg, low, high))
                self._check_protection()
            return handler

        def output(arg):
            self.output = sim_bool(arg)
            self._check_protection()

        def ocp(arg):
            self.ocp = sim_bool(arg)
            self._check_protection()

        def measure(index):
            def handler(arg):
                value = self._state()[index]
                if value:
                    value += random.gauss(0, 0.0005)
                return f"{value:+.5E}"
            return handler

        def clear(arg):
            self.tripped = 0

        return [
            (r"(SOUR:)?VOLT(:LEV)?", setter("volts", 0, MAX_VOLTS)),
            (r"(SOUR:)?VOLT(:LEV)?\?", lambda arg: f"{self.volts:+.5E}"),
            (r"(SOUR:)?CURR(:LEV)?", setter("amps", 0, MAX_AMPS)),
            (r"(SOUR:)?CURR(:LEV)?\?", lambda arg: f"{self.amps:+.5E}"),
            (r"(SOUR:)?VOLT:PROT(:LEV)?", setter("ovp", 0, MAX_OVP)),
            (r"(SOUR:)?VOLT:PROT(:LEV)?\?", lambda arg: f"{self.ovp:+.5E}"),
            (r"(SOUR:)?CURR:PROT:STAT", ocp),
            (r"OUTP(:STAT)?", output),
            (r"OUTP(:STAT)?\?", lambda arg: "1" if self.output else "0"),
            (r"OUTP:PROT:CLE", clear),
            (r"MEAS:VOLT(:DC)?\?", measure(0)),
            (r"MEAS:CURR(:DC)?\?", measure(1)),
            (r"STAT:OPER:COND\?", lambda arg: str(self._state()[2])),
            (r"STAT:QUES:COND\?", lambda arg: str(self.tripped)),
        ]

