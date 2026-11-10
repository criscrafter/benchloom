"""Base driver for instruments that speak SCPI / IEEE 488.2."""

from __future__ import annotations

import re
from typing import Callable

from .plugin import CommandError, Device, command
from .transports import SimTransport


class SimError(ValueError):
    """Raised by a simulator handler: the command is queued as this SCPI error."""

    def __init__(self, code: int, text: str):
        super().__init__(f'{code},"{text}"')


def sim_number(arg: str, low: float, high: float) -> float:
    try:
        value = float(arg)
    except ValueError:
        raise SimError(-224, "Illegal parameter value") from None
    if not low <= value <= high:
        raise SimError(-222, "Data out of range")
    return value


def sim_bool(arg: str) -> bool:
    text = arg.strip().upper()
    if text in ("ON", "1"):
        return True
    if text in ("OFF", "0"):
        return False
    raise SimError(-224, "Illegal parameter value")


class ScpiSimulator(SimTransport):
    """Base for simulators of SCPI instruments.

    Subclasses list ``(pattern, handler)`` pairs in :meth:`routes`; a pattern is a
    regular expression matched against the upper-case header. ``*IDN?``, ``*RST``,
    ``*CLS`` and ``SYST:ERR?`` work out of the box; unknown headers and bad values are
    queued as errors the way a real instrument does.
    """

    identity = "Patchbay,Simulator,0,0"

    def __init__(self, **options):
        super().__init__(**options)
        self.errors: list[str] = []
        self.reset()

    def reset(self) -> None:
        """Return to the power-on state."""

    def routes(self) -> list[tuple[str, Callable[[str], str | None]]]:
        return []

    def handle(self, line: str) -> str | None:
        replies = []
        for unit in line.split(";"):
            unit = unit.strip()
            if unit:
                header, _, arg = unit.partition(" ")
                reply = self._dispatch(header.upper().lstrip(":"), arg.strip())
                if reply is not None:
                    replies.append(reply)
        return ";".join(replies) if replies else None

    def _dispatch(self, header: str, arg: str) -> str | None:
        if header == "*IDN?":
            return self.identity
        if header == "*RST":
            self.reset()
            return None
        if header == "*CLS":
            self.errors.clear()
            return None
        if header in ("SYST:ERR?", "SYSTEM:ERROR?"):
            return self.errors.pop(0) if self.errors else '0,"No error"'
        for pattern, handler in self.routes():
            if re.fullmatch(pattern, header):
                try:
                    return handler(arg)
                except SimError as exc:
                    self.errors.append(str(exc))
                    return None
        self.errors.append('-113,"Undefined header"')
        return None


class ScpiDevice(Device):
    """Common commands of SCPI instruments, and helpers for drivers.

    Drivers send settings with :meth:`send`, which then asks the instrument for
    errors so a rejected command fails at the line that sent it. A device entry can
    turn that off with ``check_errors = false``.
    """

    def check_errors(self) -> None:
        if not self.settings.get("check_errors", True):
            return
        reply = self.transport.query("SYST:ERR?")
        code = reply.split(",", 1)[0].strip()
        try:
            number = int(float(code))
        except ValueError:
            raise CommandError(f"unexpected reply to SYST:ERR?: {reply!r}") from None
        if number != 0:
            raise CommandError(f"the instrument reported: {reply}")

    def send(self, text: str) -> None:
        self.transport.write(text)
        self.check_errors()

    def ask(self, text: str) -> str:
        return self.transport.query(text)

    def ask_number(self, text: str) -> float:
        reply = self.transport.query(text)
        try:
            return float(reply)
        except ValueError:
            raise CommandError(f"{text} returned {reply!r}, not a number") from None

    @command("Read the next entry of the instrument's error queue (SYST:ERR?)")
    def get_error(self) -> str:
        return self.transport.query("SYST:ERR?")

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
