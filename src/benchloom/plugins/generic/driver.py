# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
from __future__ import annotations

import time

from benchloom import Device, ScpiDevice, command


class GenericScpi(ScpiDevice):
    """Only the commands every SCPI instrument has; see ScpiDevice."""


class SerialLine(Device):
    def connect(self) -> None:
        # Most Arduinos reset when the port opens and need a moment to boot.
        # Override with `startup_delay = 0` in the project's device entry.
        if self.transport.scheme != "sim":
            time.sleep(float(self.settings.get("startup_delay", 2.0)))

    @command("Send one line of text")
    def write_line(self, text: str) -> None:
        self.transport.write(text)

    @command("Wait for one line of text and return it")
    def read_line(self) -> str:
        return self.transport.read()

    @command("Wait for one line and return it as a number")
    def read_number(self) -> float:
        return float(self.transport.read())

    @command("Send a line and return the reply line")
    def query(self, text: str) -> str:
        return self.transport.query(text)

    @command("Send a line and return the reply as a number")
    def query_number(self, text: str) -> float:
        return float(self.transport.query(text))
