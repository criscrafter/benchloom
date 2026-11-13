# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import os
import zipfile

import pytest

from benchloom import plugin_install
from benchloom.cli import main as cli
from benchloom.config import AppConfig, user_plugin_dir
from benchloom.plugin import PluginError, PluginRegistry
from benchloom.project import DeviceConfig, create_project

MANIFEST = """[plugin]
id = "{id}"
name = "{name}"
version = "{version}"

[[device]]
id = "meter"
name = "Test meter"
driver = "meter:Meter"
transports = ["sim"]
"""

DRIVER = """from benchloom.plugin import Device, command


class Meter(Device):
    @command
    def read(self) -> float:
        return {value}
"""


def make_plugin(folder, plugin_id="acme", version="1.0", value=1.5, name="Acme"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.toml").write_text(
        MANIFEST.format(id=plugin_id, name=name, version=version), encoding="utf-8")
    (folder / "meter.py").write_text(DRIVER.format(value=value), encoding="utf-8")
    return folder


def zip_folder(folder, archive, prefix=""):
    with zipfile.ZipFile(archive, "w") as zf:
        for path in folder.rglob("*"):
            zf.write(path, prefix + path.relative_to(folder).as_posix())
    return archive


# --- config and registry ----------------------------------------------------------
def test_config_round_trip_keeps_unknown_keys(benchloom_home):
    (benchloom_home / "config.json").write_text('{"future": 1, "disabled_plugins": ["x"]}')
    config = AppConfig.load()
    assert config.disabled_plugins == ["x"]
    config.set_enabled("x", True)
    config.set_enabled("hp", False)
    config.save()
    again = AppConfig.load()
    assert again.disabled_plugins == ["hp"] and again._other == {"future": 1}


def test_broken_config_is_ignored(benchloom_home):
    (benchloom_home / "config.json").write_text("not json")
    assert AppConfig.load().disabled_plugins == []


def test_disabled_plugin_is_found_but_not_offered():
    config = AppConfig(disabled_plugins=["hp"])
    registry = PluginRegistry.default(config=config)
    hp = next(info for info in registry.found if info.id == "hp")
    assert registry.status(hp) == "disabled" and hp.source == "built-in"
    assert "hp.3478a" not in registry.devices
    with pytest.raises(PluginError, match="switched off in the plugin manager"):
        registry.device("hp.3478a")


def test_later_plugins_replace_earlier_ones(tmp_path):
    make_plugin(user_plugin_dir() / "acme", version="1.0")
    project = create_project(tmp_path / "proj")
    make_plugin(project.path / "plugins" / "acme", version="2.0")
    registry = PluginRegistry.default(project.path, trust_project=True)
    user, local = [info for info in registry.found if info.id == "acme"]
    assert (user.source, local.source) == ("user", "project")
    assert registry.status(user) == "replaced" and registry.status(local) == "enabled"
    assert registry.plugins["acme"].version == "2.0"


def test_extra_plugin_folders_from_the_config(tmp_path):
    make_plugin(tmp_path / "dev" / "acme")
    AppConfig(plugin_paths=[str(tmp_path / "dev")]).save()
    registry = PluginRegistry.default()
    assert registry.plugins["acme"].source == "folder"


def test_broken_plugin_is_listed(tmp_path):
    bad = user_plugin_dir() / "bad"
    bad.mkdir(parents=True)
    (bad / "plugin.toml").write_text("[plugin]\nname = 'no id'\n")
    registry = PluginRegistry.default()
    assert [folder for folder, _ in registry.broken] == [bad]


# --- install and remove -----------------------------------------------------------
def test_install_from_folder_and_replace(tmp_path):
    source = make_plugin(tmp_path / "src" / "acme")
    info = plugin_install.install(source)
    assert info.path == user_plugin_dir() / "acme" and info.source == ""
    registry = PluginRegistry.default()
    driver = registry.device("acme.meter").load_driver()
    assert driver(None).read() == 1.5
    with pytest.raises(PluginError, match="installed already"):
        plugin_install.install(source)
    make_plugin(source, version="1.1", value=2.5)
    assert plugin_install.install(source, replace=True).version == "1.1"
    # the old code is forgotten, so the new driver is loaded
    driver = PluginRegistry.default().device("acme.meter").load_driver()
    assert driver(None).read() == 2.5


@pytest.mark.parametrize("prefix", ["", "acme-main/"])
def test_install_from_zip(tmp_path, prefix):
    archive = zip_folder(make_plugin(tmp_path / "acme"), tmp_path / "acme.zip", prefix)
    assert plugin_install.peek(archive).id == "acme"
    assert not (user_plugin_dir() / "acme").exists()  # peek installs nothing
    info = plugin_install.install(archive)
    assert (info.path / "meter.py").is_file()


def test_install_refuses_bad_plugins(tmp_path):
    with pytest.raises(PluginError, match="no plugin.toml"):
        plugin_install.install(tmp_path)
    folder = make_plugin(tmp_path / "acme")
    (folder / "meter.py").unlink()
    with pytest.raises(PluginError, match="missing meter.py"):
        plugin_install.install(folder)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("plugin.toml", MANIFEST.format(id="x", name="x", version="1"))
        zf.writestr("../escaped.py", "")
    with pytest.raises(PluginError, match="unsafe path"):
        plugin_install.install(evil)
    (tmp_path / "junk.zip").write_text("not a zip")
    with pytest.raises(PluginError, match="not a .zip"):
        plugin_install.install(tmp_path / "junk.zip")


def test_remove_only_installed_plugins(tmp_path):
    info = plugin_install.install(make_plugin(tmp_path / "acme"))
    builtin = PluginRegistry.default().plugins["hp"]
    assert plugin_install.is_removable(info) and not plugin_install.is_removable(builtin)
    with pytest.raises(PluginError, match="not an installed plugin"):
        plugin_install.remove(builtin)
    trashed = []
    plugin_install.remove(info, trash=lambda path: trashed.append(path) or False)
    assert trashed == [info.path] and not info.path.exists()


def test_cli_plugins(tmp_path, capsys):
    assert cli(["plugins", "install", str(make_plugin(tmp_path / "acme"))]) == 0
    assert cli(["plugins", "disable", "acme"]) == 0
    assert cli(["plugins"]) == 0
    assert "off acme" in capsys.readouterr().out
    assert cli(["plugins", "remove", "hp"]) == 2
    assert cli(["plugins", "remove", "acme"]) == 0
    assert not (user_plugin_dir() / "acme").exists()


def test_run_sees_disabled_plugins(tmp_path):
    """Runs read the same config, so a switched-off plugin cannot be used by a test."""
    from benchloom.engine import run_project

    project = create_project(tmp_path / "proj")
    AppConfig(disabled_plugins=["demo"]).save()
    result = run_project(project.path)
    assert result.status == "error" and "switched off" in result.error
    assert os.environ["BENCHLOOM_HOME"]


def test_hp_agilent_and_keysight_share_one_group(tmp_path):
    for folder, maker in (("a", "Keysight"), ("b", "Hewlett-Packard"), ("c", "Rigol")):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "plugin.toml").write_text(
            f'[plugin]\nid = "{folder}"\nmanufacturer = "{maker}"\n'
            f'[[device]]\nid = "x"\ndriver = "d:D"\n', encoding="utf-8")
    devices = PluginRegistry([tmp_path]).devices
    assert devices["a.x"].maker == devices["b.x"].maker == "HP / Agilent / Keysight"
    assert (devices["a.x"].brand, devices["b.x"].brand) == ("Keysight", "Hewlett-Packard")
    assert devices["c.x"].maker == devices["c.x"].brand == "Rigol"
    builtin = PluginRegistry.default().devices
    assert builtin["hp.34970a"].brand == "Agilent" and builtin["hp.3478a"].brand == "HP"
    assert builtin["hp.34970a"].maker == builtin["hp.3478a"].maker
