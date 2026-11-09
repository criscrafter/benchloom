from __future__ import annotations

import random

from patchbay import SimTransport

LOAD_OHMS = 10.0


class DemoPsuSim(SimTransport):
    """Answers the demo supply's commands as the real instrument would."""

    def __init__(self, **options):
        super().__init__(**options)
        self.volts, self.limit, self.output = 0.0, 1.0, False

    def _measure(self) -> tuple[float, float]:
        if not self.output:
            return 0.0, 0.0
        volts = min(self.volts, self.limit * LOAD_OHMS)
        volts += random.gauss(0, 0.002)
        return volts, volts / LOAD_OHMS

    def handle(self, command: str) -> str | None:
        name, _, arg = command.upper().partition(" ")
        if name == "*IDN?":
            return "Patchbay,Demo PSU,SIM0001,0.1"
        if name == "*RST":
            self.volts, self.limit, self.output = 0.0, 1.0, False
        elif name == "VOLT":
            self.volts = float(arg)
        elif name == "VOLT?":
            return f"{self.volts:.4f}"
        elif name == "CURR":
            self.limit = float(arg)
        elif name == "CURR?":
            return f"{self.limit:.4f}"
        elif name == "OUTP":
            self.output = arg in ("ON", "1")
        elif name == "OUTP?":
            return "1" if self.output else "0"
        elif name == "MEAS:VOLT?":
            return f"{self._measure()[0]:.4f}"
        elif name == "MEAS:CURR?":
            return f"{self._measure()[1]:.4f}"
        return None
