# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Linux desktop integration: the menu entry, udev rules and the access hints.
They only write files and text, so they run on every system."""

import configparser
import os
import sys

import pytest

from benchloom import cli, linux, transports
from benchloom.plugin import PluginRegistry


@pytest.fixture
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    return tmp_path / "share"


def test_desktop_entry_is_valid_and_quotes_its_command():
    text = linux.desktop_entry(["/home/a b/Benchloom/Benchloom", "--x=1", "50%", 'q"$'])
    entry = configparser.ConfigParser(interpolation=None)
    entry.optionxform = str
    entry.read_string(text)
    section = entry["Desktop Entry"]
    assert section["Type"] == "Application"
    assert section["Icon"] == linux.APP_ID
    assert section["StartupWMClass"] == "Benchloom"
    # quoted per argument; \\ in the file is one backslash; % doubled
    assert section["Exec"] == ('"/home/a b/Benchloom/Benchloom" "--x=1" 50%% '
                               '"q\\\\"\\\\$"')
    assert linux.desktop_entry(["/opt/benchloom-gui"]).count("Exec=/opt/benchloom-gui\n") == 1


def test_desktop_entry_install_and_remove(data_home):
    entry, icon = linux.install_desktop_entry()
    assert entry == data_home / "applications" / "benchloom.desktop"
    assert icon == data_home / "icons" / "hicolor" / "256x256" / "apps" / "benchloom.png"
    assert icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "Exec=" in entry.read_text(encoding="utf-8")
    assert set(linux.remove_desktop_entry()) == {entry, icon}
    assert not entry.exists() and not icon.exists()
    assert linux.remove_desktop_entry() == []


def test_relative_xdg_data_home_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", "relative/share")
    assert linux.data_home() == linux.Path.home() / ".local" / "share"


def test_app_command_prefers_the_gui_script(monkeypatch):
    command = linux.app_command()
    assert command[0].endswith(("benchloom-gui", "benchloom-gui.exe")) or command[-1] == "gui"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/Benchloom/benchloom-cli")
    assert linux.Path(linux.app_command()[0]).name == "Benchloom"


def test_udev_rules_cover_plugin_usb_devices():
    registry = PluginRegistry.default()
    ids = {(vid, pid) for vid, pid, _ in linux.usb_ids(registry)}
    assert (0x049F, 0x505A) in ids  # Hantek DSO5102P, from its plugin.toml
    assert transports.USBGPIB_IDS in ids
    rules = linux.udev_rules(registry)
    assert 'ATTR{idVendor}=="049f", ATTR{idProduct}=="505a", TAG+="uaccess"' in rules
    assert 'ENV{ID_USB_INTERFACES}=="*:fe03??:*", TAG+="uaccess"' in rules
    assert "GROUP" not in rules
    with_group = linux.udev_rules(registry, "plugdev")
    assert with_group.count('GROUP="plugdev", MODE="0660"') == len(ids) + 1 == sum(
        1 for line in with_group.splitlines() if line.startswith("SUBSYSTEM"))


def test_udev_rules_command(capsys):
    assert cli.main(["udev-rules"]) == 0
    assert 'SUBSYSTEM=="usb"' in capsys.readouterr().out


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="the command works there")
def test_desktop_entry_command_is_for_linux_only(capsys):
    assert cli.main(["desktop-entry"]) == 2
    assert "Linux" in capsys.readouterr().err


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_desktop_entry_command(data_home, capsys):
    assert cli.main(["desktop-entry"]) == 0
    assert (data_home / "applications" / "benchloom.desktop").exists()
    assert cli.main(["desktop-entry", "--remove"]) == 0
    assert not (data_home / "applications" / "benchloom.desktop").exists()


@pytest.mark.parametrize("platform, words", [("win32", "WinUSB"), ("linux", "udev"),
                                             ("darwin", "")])
def test_usb_access_hint_per_system(monkeypatch, platform, words):
    monkeypatch.setattr(transports.sys, "platform", platform)
    hint = transports.usb_access_hint()
    assert words in hint and (hint == "") == (words == "")


def test_serial_access_hint_only_on_linux(monkeypatch):
    monkeypatch.setattr(transports.sys, "platform", "win32")
    assert transports.serial_access_hint("COM3") == ""
    monkeypatch.setattr(transports.sys, "platform", "linux")
    hint = transports.serial_access_hint("/dev/ttyNONE")  # no such port: the usual group
    assert "dialout" in hint and "usermod" in hint


def test_window_icon_has_small_sizes():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from benchloom.gui import app_icon

    app = widgets.QApplication.instance() or widgets.QApplication([])  # noqa: F841

    sizes = {(s.width(), s.height()) for s in app_icon().availableSizes()}
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes  # X11 needs the small ones
