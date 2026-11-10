# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
from __future__ import annotations

from benchloom import Param, ScpiDevice, command


class DemoPsu(ScpiDevice):
    @command("Set the output voltage", params={"volts": Param(unit="V", min=0, max=20)})
    def set_voltage(self, volts: float) -> None:
        self.transport.write(f"VOLT {volts:.4f}")

    @command("Set the current limit", params={"amps": Param(unit="A", min=0, max=5)})
    def set_current_limit(self, amps: float) -> None:
        self.transport.write(f"CURR {amps:.4f}")

    @command("Switch the output on or off")
    def set_output(self, on: bool) -> None:
        self.transport.write("OUTP ON" if on else "OUTP OFF")

    @command("Measure the voltage at the output terminals", unit="V")
    def measure_voltage(self) -> float:
        return float(self.transport.query("MEAS:VOLT?"))

    @command("Measure the output current", unit="A")
    def measure_current(self) -> float:
        return float(self.transport.query("MEAS:CURR?"))
