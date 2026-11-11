# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Benchloom.exe: the desktop app."""
import sys

from entry import main

if __name__ == "__main__":
    sys.exit(main(gui=True))
