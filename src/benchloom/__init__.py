# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Benchloom: open lab automation."""

from .plugin import CommandError, Device, Param, command
from .scpi import ScpiDevice
from .transports import RawSimTransport, SimTransport, Transport

__version__ = "0.1.2"
HOMEPAGE = "https://github.com/criscrafter/benchloom"

# The notices an interactive program under the AGPL shows: copyright, no warranty,
# the licence. The app's About dialog and `benchloom --version` print them.
NOTICE = f"""\
Benchloom {__version__}
Copyright (C) 2026 Friedrich-Christer Rosenthal

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. You are responsible for operating connected equipment
safely and for checking results. See the GNU Affero General Public License for
details.

Benchloom is developed with substantial assistance from AI tools. Every change
is reviewed by the maintainer and checked by automated tests.

Source code and licence: {HOMEPAGE}"""

__all__ = [
    "CommandError",
    "Device",
    "Param",
    "RawSimTransport",
    "ScpiDevice",
    "SimTransport",
    "Transport",
    "command",
]
