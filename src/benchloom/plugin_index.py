# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The plugin registry: a list of plugins others wrote, to browse and install.

The list is one JSON file, ``registry/index.json`` in the Benchloom repository,
changed by pull request. Each entry names where the plugin's .zip is and its
SHA-256 checksum, so what is installed is exactly the file that was listed: a
changed download is refused. The plugins themselves are written by others and
are Python code; the list says where they are, not that they are safe.

``plugin_index`` in config.json may name another list (an https:// URL or a
file), for a company's own plugins, say.

An entry::

    {"id": "acme", "name": "ACME supplies", "version": "1.2.0",
     "description": "...", "author": "...", "license": "MIT",
     "homepage": "https://...", "devices": ["ACME PS-3005"],
     "download": "https://.../acme-1.2.0.zip", "sha256": "<64 hex digits>",
     "benchloom": "0.1.3"}

``benchloom`` is the oldest Benchloom version the plugin works with (optional).
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__
from .config import AppConfig
from .plugin import PluginError, PluginInfo

DEFAULT_INDEX = "https://raw.githubusercontent.com/criscrafter/benchloom/main/registry/index.json"
INDEX_FORMAT = 1
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_PLUGIN_BYTES = 50 * 1024 * 1024
TIMEOUT_S = 20
USER_AGENT = f"Benchloom/{__version__}"


@dataclass
class IndexEntry:
    id: str
    name: str
    version: str
    download: str
    sha256: str
    description: str = ""
    author: str = ""
    license: str = ""
    homepage: str = ""
    devices: list[str] = field(default_factory=list)
    benchloom: str = ""  # oldest Benchloom version it works with

    @property
    def compatible(self) -> bool:
        return not self.benchloom or version_key(__version__) >= version_key(self.benchloom)


def version_key(version: str) -> tuple:
    """'1.10.2' > '1.9': numbers compared as numbers; text after them ignored."""
    return tuple(int(n) for n in re.findall(r"\d+", version.split("-")[0].split("+")[0])[:4])


def index_location(config: AppConfig | None = None) -> str:
    config = config or AppConfig.load()
    location = config._other.get("plugin_index")
    return location if isinstance(location, str) and location.strip() else DEFAULT_INDEX


def _read(location: str, limit: int) -> bytes:
    """An https:// URL or a local file, at most ``limit`` bytes."""
    if location.startswith("https://"):
        request = urllib.request.Request(location, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                data = response.read(limit + 1)
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise PluginError(f"could not download {location}: {reason}") from exc
    elif re.match(r"^[a-z][a-z0-9+.-]*://", location, re.I) and not location.startswith("file:"):
        raise PluginError(f"{location}: only https:// addresses and local files are used")
    else:
        path = Path(urllib.request.url2pathname(urllib.parse.urlparse(location).path)
                    if location.startswith("file:") else location)
        try:
            with path.open("rb") as file:
                data = file.read(limit + 1)
        except OSError as exc:
            raise PluginError(f"could not read {path}: {exc}") from exc
    if len(data) > limit:
        raise PluginError(f"{location} is larger than {limit // (1024 * 1024)} MB")
    return data


def parse_index(data: bytes | str, source: str = "the plugin list") -> list[IndexEntry]:
    try:
        index = json.loads(data)
    except ValueError as exc:
        raise PluginError(f"{source} is not valid JSON: {exc}") from exc
    if not isinstance(index, dict) or not isinstance(index.get("plugins"), list):
        raise PluginError(f"{source} has no list of plugins")
    if index.get("format", INDEX_FORMAT) > INDEX_FORMAT:
        raise PluginError(f"{source} needs a newer Benchloom")
    entries, known = [], set(IndexEntry.__dataclass_fields__)
    for raw in index["plugins"]:
        if not isinstance(raw, dict):
            continue
        try:
            entry = IndexEntry(**{k: v for k, v in raw.items() if k in known})
        except TypeError:
            continue  # an entry without id, name, version, download or sha256
        if (not all(isinstance(getattr(entry, k), str) and getattr(entry, k)
                    for k in ("id", "name", "version", "download"))
                or not re.fullmatch(r"[0-9a-fA-F]{64}", str(entry.sha256))
                or not entry.download.startswith("https://")):
            continue  # skipped, rather than one bad entry hiding the others
        entry.sha256 = entry.sha256.lower()
        entry.devices = [str(d) for d in entry.devices] if isinstance(entry.devices, list) else []
        entries.append(entry)
    return sorted(entries, key=lambda e: e.name.lower())


def fetch_index(location: str | None = None) -> list[IndexEntry]:
    location = location or index_location()
    return parse_index(_read(location, MAX_INDEX_BYTES), location)


def status(entry: IndexEntry, installed: list[PluginInfo]) -> str:
    """'new', 'installed', 'update' (a newer version is listed) or 'built-in'."""
    same = [p for p in installed if p.id == entry.id]
    if not same:
        return "new"
    newest = max(version_key(p.version) for p in same)
    if version_key(entry.version) > newest:
        return "update"
    if all(p.source == "built-in" for p in same):
        return "built-in"
    return "installed"


def download(entry: IndexEntry, folder: Path,
             read: Callable[[str, int], bytes] | None = None) -> Path:
    """Download the entry's .zip into ``folder`` and check its checksum."""
    data = (read or _read)(entry.download, MAX_PLUGIN_BYTES)
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry.sha256:
        raise PluginError(
            f"the download of {entry.name} is not the file that was listed (checksum "
            f"{digest[:12]}…, listed {entry.sha256[:12]}…); it was not installed")
    name = re.sub(r"[^\w.-]", "_", f"{entry.id}-{entry.version}")
    path = Path(folder) / f"{name}.zip"
    path.write_bytes(data)
    return path


def install(entry: IndexEntry, replace: bool = False,
            read: Callable[[str, int], bytes] | None = None) -> PluginInfo:
    """Download, check and install a listed plugin."""
    from . import plugin_install

    with tempfile.TemporaryDirectory() as tmp:
        archive = download(entry, Path(tmp), read)
        info = plugin_install.peek(archive)
        if info.id != entry.id:
            raise PluginError(f"the download of {entry.name} holds plugin '{info.id}', "
                              f"not '{entry.id}'; it was not installed")
        return plugin_install.install(archive, replace=replace)


def make_entry(archive: Path, url: str) -> dict:
    """The index entry for a plugin .zip that will be downloadable from ``url``."""
    from . import plugin_install

    archive = Path(archive)
    info = plugin_install.peek(archive)
    entry = IndexEntry(
        id=info.id, name=info.name, version=info.version, download=url,
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        description=info.description, devices=[d.name for d in info.devices],
        author="", license="", homepage="", benchloom=__version__)
    return asdict(entry)
