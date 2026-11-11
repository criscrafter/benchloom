# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Release notes for a version, from its section in CHANGELOG.md.

    python packaging/release_notes.py v0.1.2 > notes.md

The release workflow publishes them with the Windows download, so a release needs
no editing by hand. Lines wrapped in CHANGELOG.md are joined again, because GitHub
shows every line break in release notes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def section(changelog: str, version: str) -> str:
    """The text under '## <version>' up to the next '## ', with wrapped lines joined."""
    match = re.search(rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)", changelog,
                      re.S | re.M)
    if not match:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    lines: list[str] = []
    for line in match.group(1).strip().splitlines():
        text = line.strip()
        starts_block = not text or text.startswith(("- ", "* ", "#", "```", "|"))
        if lines and lines[-1] and not starts_block:
            lines[-1] += " " + text  # a wrapped line continues the one before
        else:
            lines.append(text if text else "")
    return "\n".join(lines)


def notes(tag: str, changelog: str | None = None) -> str:
    version = tag.removeprefix("v")
    if changelog is None:
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return (
        f"**Windows:** download `Benchloom-{version}-windows-x64.zip` below, unzip it "
        "anywhere and start `Benchloom.exe`: no installation, no Python and no "
        "administrator rights needed. Windows may warn about an unknown publisher, "
        'because the program is not signed: choose "More info" and then "Run anyway". '
        "Other systems: install from source as the README describes.\n\n"
        f"### What's new\n\n{section(changelog, version)}\n\n"
        "### Safety and licence\n\n"
        "Benchloom switches real power supplies, loads and generators: read the safety "
        "note in the README before connecting hardware. Provided without warranty, "
        "under the AGPL-3.0.\n"
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(notes(sys.argv[1]), end="")
