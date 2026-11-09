"""Base driver for instruments that speak SCPI / IEEE 488.2."""

from __future__ import annotations

from .plugin import Device, command


class ScpiDevice(Device):
    @command("Read the identification string (*IDN?)")
    def idn(self) -> str:
        return self.transport.query("*IDN?")

    @command("Reset the instrument to its power-on state (*RST)")
    def reset(self) -> None:
        self.transport.write("*RST")

    @command("Clear the status registers and error queue (*CLS)")
    def clear(self) -> None:
        self.transport.write("*CLS")

    @command("Send a raw command")
    def write(self, text: str) -> None:
        self.transport.write(text)

    @command("Send a raw command and return the reply")
    def query(self, text: str) -> str:
        return self.transport.query(text)

    @command("Send a raw command and return the reply as a number")
    def query_number(self, text: str) -> float:
        return float(self.transport.query(text))
