# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The online plugin list: reading it, checking downloads, installing, and the window."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from test_plugin_manager import make_plugin, zip_folder

from benchloom import plugin_index
from benchloom.cli import main as cli
from benchloom.config import AppConfig, user_plugin_dir
from benchloom.plugin import PluginError, PluginInfo

URL = "https://example.org/acme-1.0.zip"


def listed(tmp_path, version="1.0", plugin_id="acme", **extra):
    """A plugin .zip and its list entry; downloads are served from a dict, not the web."""
    archive = zip_folder(make_plugin(tmp_path / f"{plugin_id}-{version}", plugin_id, version),
                         tmp_path / f"{plugin_id}-{version}.zip")
    entry = plugin_index.make_entry(archive, URL.replace("1.0", version))
    entry.update(extra)
    return entry, {entry["download"]: archive.read_bytes()}


def reader(files):
    def read(location, limit):
        if location not in files:
            raise PluginError(f"could not download {location}: not found")
        return files[location]
    return read


def entries(*raw):
    return plugin_index.parse_index(json.dumps({"format": 1, "plugins": list(raw)}))


def test_the_list_in_the_repository_is_valid():
    path = Path(__file__).parent.parent / "registry" / "index.json"
    assert plugin_index.parse_index(path.read_bytes()) == []
    assert plugin_index.DEFAULT_INDEX.endswith("/main/registry/index.json")


def test_bad_entries_are_skipped_not_fatal(tmp_path):
    good, _ = listed(tmp_path)
    bad = [dict(good, download="http://example.org/x.zip"), dict(good, sha256="abc"),
           {k: v for k, v in good.items() if k != "version"}, "not an entry",
           dict(good, id="zz", name="Zeta", sha256=good["sha256"].upper())]
    found = entries(*bad, dict(good, name="alpha"))
    assert [e.name for e in found] == ["alpha", "Zeta"]  # sorted, checksum case ignored
    assert found[1].sha256 == good["sha256"]
    with pytest.raises(PluginError, match="newer Benchloom"):
        plugin_index.parse_index(json.dumps({"format": 99, "plugins": []}))
    with pytest.raises(PluginError, match="not valid JSON"):
        plugin_index.parse_index("{")


def test_versions_and_status():
    key = plugin_index.version_key
    assert key("1.10.2") > key("1.9") == key("1.9-beta") > key("0.1.3")
    entry = entries({"id": "acme", "name": "A", "version": "2.0", "download": URL,
                     "sha256": "0" * 64, "benchloom": "999.0"})[0]
    assert not entry.compatible
    plugin = lambda version, source="user": PluginInfo("acme", "A", version, Path("."),
                                                        source=source)
    assert plugin_index.status(entry, []) == "new"
    assert plugin_index.status(entry, [plugin("1.0")]) == "update"
    assert plugin_index.status(entry, [plugin("2.0")]) == "installed"
    assert plugin_index.status(entry, [plugin("2.0", "built-in")]) == "built-in"


def test_reading_the_list_from_a_file_or_the_config(tmp_path):
    good, _ = listed(tmp_path)
    path = tmp_path / "index.json"
    path.write_text(json.dumps({"plugins": [good]}), encoding="utf-8")
    assert [e.id for e in plugin_index.fetch_index(str(path))] == ["acme"]
    assert [e.id for e in plugin_index.fetch_index(path.as_uri())] == ["acme"]
    with pytest.raises(PluginError, match="only https://"):
        plugin_index.fetch_index("http://example.org/index.json")
    config = AppConfig.load()
    config._other["plugin_index"] = str(path)
    config.save()
    assert plugin_index.index_location() == str(path)


def test_install_checks_the_download(tmp_path):
    good, files = listed(tmp_path)
    entry = entries(good)[0]
    info = plugin_index.install(entry, read=reader(files))
    assert info.id == "acme" and Path(info.path).parent == user_plugin_dir()

    tampered = {URL: files[URL] + b"x"}
    with pytest.raises(PluginError, match="not the file that was listed"):
        plugin_index.install(entry, replace=True, read=reader(tampered))

    other, other_files = listed(tmp_path, plugin_id="other")
    renamed = entries(dict(other, id="acme"))[0]
    with pytest.raises(PluginError, match="holds plugin 'other'"):
        plugin_index.install(renamed, replace=True, read=reader(other_files))


def test_command_line_search_install_and_entry(tmp_path, capsys, monkeypatch):
    old, files = listed(tmp_path, "1.0", description="Bench supply driver")
    path = tmp_path / "index.json"
    path.write_text(json.dumps({"plugins": [old]}), encoding="utf-8")
    config = AppConfig.load()
    config._other["plugin_index"] = str(path)
    config.save()
    real_read = plugin_index._read
    monkeypatch.setattr(plugin_index, "_read",
                        lambda location, limit: files[location] if location in files
                        else real_read(location, limit))

    assert cli(["plugins", "search", "supply"]) == 0
    assert "acme" in capsys.readouterr().out
    assert cli(["plugins", "search", "oscilloscope"]) == 0
    assert "No plugins found" in capsys.readouterr().out
    assert cli(["plugins", "install", "acme"]) == 0
    assert "Installed Acme 1.0" in capsys.readouterr().out
    assert cli(["plugins", "search"]) == 0
    assert "installed" in capsys.readouterr().out
    assert cli(["plugins", "install", "nothing-like-it"]) == 2

    archive = tmp_path / "acme-1.0.zip"
    assert cli(["plugins", "entry", str(archive), "--url", URL]) == 0
    entry = json.loads(capsys.readouterr().out)
    assert entry["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert entry["devices"] == ["Test meter"] and entry["download"] == URL


# --- the window ------------------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_browse_window_installs_and_updates(app, tmp_path):
    from benchloom.gui.plugins import BrowseDialog
    from benchloom.plugin import PluginRegistry

    old, files = listed(tmp_path, "1.0", author="Jane", homepage="https://example.org")
    new, new_files = listed(tmp_path, "1.1")
    files.update(new_files)
    hp = dict(old, id="hp", name="HP again", version="0.0.1")
    lists = {"index": [old, hp]}
    dialog = BrowseDialog(PluginRegistry.default().found, location="index",
                          fetch=lambda _: entries(*lists["index"]), read=reader(files))
    assert dialog.load()
    rows = {dialog.tree.topLevelItem(i).text(0): dialog.tree.topLevelItem(i)
            for i in range(dialog.tree.topLevelItemCount())}
    assert set(rows) == {"Acme", "HP again"} and rows["HP again"].text(2) == "Built in"
    dialog.tree.setCurrentItem(rows["HP again"])
    assert not dialog.install_button.isEnabled()  # older than the built-in one

    dialog.tree.setCurrentItem(rows["Acme"])
    assert "Jane" in dialog.details.toPlainText()
    assert dialog.install_button.text() == "Install"
    assert dialog.install_selected(confirm=False).version == "1.0"
    assert dialog.changed and dialog.tree.currentItem().text(2) == "Installed"

    lists["index"] = [new]
    dialog.load()
    assert dialog.tree.currentItem().text(2) == "Update available"
    assert dialog.install_button.text() == "Update"
    assert dialog.install_selected(confirm=False).version == "1.1"
    assert len([p for p in user_plugin_dir().iterdir() if p.is_dir()]) == 1  # replaced

    dialog.search.setText("nothing like it")
    assert dialog.tree.topLevelItemCount() == 0 and not dialog.install_button.isEnabled()
    dialog.close()


def test_browse_window_says_when_the_list_cannot_be_loaded(app):
    from benchloom.gui.plugins import BrowseDialog

    def fail(_):
        raise PluginError("could not download x: offline")

    dialog = BrowseDialog([], location="x", fetch=fail)
    assert not dialog.load()
    assert "offline" in dialog.message.text() and dialog.tree.topLevelItemCount() == 0
    dialog = BrowseDialog([], location="x", fetch=lambda _: [])
    assert dialog.load() and "no plugins yet" in dialog.message.text()
    dialog.close()
