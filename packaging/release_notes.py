# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Release notes for a version, from its section in CHANGELOG.md.

    python packaging/release_notes.py v0.1.2 > notes.md

The release workflow publishes them with the downloads, so a release needs
no editing by hand. Lines wrapped in CHANGELOG.md are joined again, because GitHub
shows every line break in release notes.

Every point that starts with a bold title (``- **Title:** text``) is shown folded:
only the title, and the text when it is clicked. A version's section may be split
into ``### New`` and ``### Bug fixes``; those headings are kept.
"""

from __future__ import annotations

import html
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


POINT = re.compile(r"^- \*\*(?P<title>.+?):?\*\*:?\s*(?P<text>.*)$")


def _summary(title: str) -> str:
    """A title as HTML: GitHub does not read Markdown inside <summary>."""
    parts = re.split(r"`([^`]*)`", title)
    return "".join(f"<code>{html.escape(p)}</code>" if i % 2 else html.escape(p)
                   for i, p in enumerate(parts))


def folded(text: str) -> str:
    """Each point with a bold title folded, showing the title until it is clicked."""
    out = []
    for line in text.splitlines():
        match = POINT.match(line)
        if match and match["text"]:
            body = match["text"]  # it followed "Title:", so it may start in lower case
            body = body[0].upper() + body[1:]
            # blank lines around it: GitHub would otherwise take the next point as HTML
            out.append(f"\n<details><summary><b>{_summary(match['title'])}</b></summary>\n\n"
                       f"{body}\n\n</details>\n")
        else:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")


def _body(text: str) -> str:
    """The version's points, under its own headings (New, Bug fixes) if it has them."""
    if not re.search(r"^### ", text, re.M):
        text = "### What's new\n\n" + text
    return folded(text)


def notes(tag: str, changelog: str | None = None) -> str:
    version = tag.removeprefix("v")
    if changelog is None:
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return (
        f"**Windows:** download `Benchloom-{version}-windows-x64.zip` below, unzip it "
        "anywhere and start `Benchloom.exe`: no installation, no Python and no "
        "administrator rights needed. Windows may warn about an unknown publisher, "
        'because the program is not signed: choose "More info" and then "Run anyway".\n\n'
        f"**Linux:** download `Benchloom-{version}-linux-x86_64.tar.gz` below, unpack it "
        "and start `Benchloom/Benchloom`: no installation and no root rights needed. It "
        "runs on Ubuntu 22.04 or newer, and on other distributions with glibc 2.35 or "
        "newer. "
        "Other systems: install from source as the README describes.\n\n"
        f"{_body(section(changelog, version))}\n\n"
        "### Safety and licence\n\n"
        "Benchloom switches real power supplies, loads and generators: read the safety "
        "note in the README before connecting hardware. Provided without warranty, "
        "under the AGPL-3.0.\n"
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(notes(sys.argv[1]), end="")
