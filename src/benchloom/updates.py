# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Is there a newer Benchloom? Asks GitHub for the latest release.

The request goes to GitHub's public API and sends nothing but the usual
connection details and ``User-Agent: Benchloom/<version>``. The desktop app asks
at most once a day, and only while "Check for new versions" is on in Settings.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from . import __version__
from .plugin_index import version_key

LATEST_RELEASE = "https://api.github.com/repos/criscrafter/benchloom/releases/latest"
RELEASES_PAGE = "https://github.com/criscrafter/benchloom/releases/latest"
USER_AGENT = f"Benchloom/{__version__}"
_LIMIT = 1_000_000  # bytes; a release's JSON is far smaller


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str  # "0.1.6", without the v of the tag
    url: str  # the release page: what's new, and the downloads
    notes: str = ""

    def newer_than(self, version: str = __version__) -> bool:
        return is_newer(self.version, version)


def is_newer(version: str, than: str = __version__) -> bool:
    try:
        return version_key(version) > version_key(than)
    except ValueError:
        return False


def _read(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(_LIMIT + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError(f"could not reach GitHub: {reason}") from exc


def latest_release(timeout: float = 10, read: Callable[[str, float], bytes] | None = None,
                   ) -> Release:
    """The newest published release (drafts and pre-releases are left out)."""
    data = (read or _read)(LATEST_RELEASE, timeout)
    if len(data) > _LIMIT:
        raise UpdateError("GitHub's answer is too large")
    try:
        info = json.loads(data.decode("utf-8"))
        tag = str(info["tag_name"])
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError("GitHub's answer could not be read") from exc
    url = info.get("html_url")
    if not (isinstance(url, str) and url.startswith("https://github.com/")):
        url = RELEASES_PAGE
    return Release(tag.removeprefix("v"), url, str(info.get("body") or ""))


def how_to_update() -> str:
    """For the message: the download for the Windows app, pip for an installed package."""
    if getattr(sys, "frozen", False):
        return "Download the new version from its release page."
    return "Update with: pip install --upgrade benchloom"  # keeps the extras installed
