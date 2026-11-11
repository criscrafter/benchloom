# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""benchloom-cli.exe: the command line."""
import sys

from entry import main

if __name__ == "__main__":
    sys.exit(main(gui=False))
