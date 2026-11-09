from __future__ import annotations

from patchbay import SimTransport


class GenericScpiSim(SimTransport):
    def handle(self, command: str) -> str | None:
        if command.upper() == "*IDN?":
            return "Patchbay,Generic SCPI,SIM0002,0.1"
        return "0" if command.endswith("?") else None


class EchoSim(SimTransport):
    """Replies to every line with the same line."""

    def handle(self, command: str) -> str | None:
        return command
