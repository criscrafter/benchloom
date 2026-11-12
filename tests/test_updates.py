# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The update check: reading GitHub's answer, and the bar in the app. No network."""
import json
import os
import time

import pytest

from benchloom import __version__
from benchloom.updates import Release, UpdateError, is_newer, latest_release


def answer(**info):
    return lambda url, timeout: json.dumps(info).encode()


def test_versions_compare_as_numbers():
    assert is_newer("0.1.10", "0.1.9") and is_newer("1.0", "0.9.9")
    assert not is_newer("0.1.5", "0.1.5") and not is_newer("0.1.4", "0.1.5")
    assert not is_newer("nonsense", "0.1.5")


def test_the_latest_release_is_read_from_githubs_answer():
    release = latest_release(read=answer(
        tag_name="v9.0.1", html_url="https://github.com/criscrafter/benchloom/releases/tag/v9.0.1",
        body="Notes"))
    assert release == Release("9.0.1", "https://github.com/criscrafter/benchloom/releases/tag/v9.0.1",
                              "Notes")
    assert release.newer_than(__version__)
    # a link elsewhere is never opened; the releases page is used instead
    odd = latest_release(read=answer(tag_name="v9.0.1", html_url="https://example.com/x"))
    assert odd.url == "https://github.com/criscrafter/benchloom/releases/latest"
    with pytest.raises(UpdateError):
        latest_release(read=lambda url, timeout: b"<html>")
    with pytest.raises(UpdateError):
        latest_release(read=answer(message="Not Found"))


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def window_with(tmp_path, fetch):
    from PySide6.QtCore import QSettings

    from benchloom.gui.main_window import MainWindow

    window = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    window.update_checker._fetch = fetch
    return window


def wait_for(app, window):
    deadline = time.monotonic() + 10
    while window.update_checker.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    for _ in range(5):
        app.processEvents()


def test_a_newer_version_shows_the_bar_once_a_day(app, tmp_path):
    asked = []

    def fetch():
        asked.append(1)
        return Release("99.0", "https://github.com/criscrafter/benchloom/releases/tag/v99.0")

    window = window_with(tmp_path, fetch)
    assert window.check_for_updates()
    wait_for(app, window)
    assert window.update_bar.is_open
    assert "Benchloom 99.0 is available" in window.update_bar.label.text()
    assert __version__ in window.update_bar.label.text()
    assert not window.check_for_updates()  # checked already today
    assert len(asked) == 1

    window.update_bar.skip()  # not mentioned again by the automatic check
    assert not window.update_bar.is_open and window.prefs.get("skipped_version") == "99.0"
    window.prefs.set("last_update_check", "0")
    assert window.check_for_updates()
    wait_for(app, window)
    assert not window.update_bar.is_open and len(asked) == 2
    window.close()


def test_no_bar_when_up_to_date_switched_off_or_offline(app, tmp_path):
    window = window_with(tmp_path, lambda: Release(__version__, "https://github.com/x"))
    assert window.check_for_updates()
    wait_for(app, window)
    assert not window.update_bar.is_open

    window.prefs.set("last_update_check", "0")
    window.prefs.set("check_updates", False)
    assert not window.check_for_updates()  # switched off in Settings

    def offline():
        raise UpdateError("could not reach GitHub: no network")

    window.prefs.set("check_updates", True)
    window.update_checker._fetch = offline
    assert window.check_for_updates()  # quietly: no message box at start
    wait_for(app, window)
    assert not window.update_bar.is_open
    assert window.prefs.get("last_update_check") == "0"  # tried again next start
    window.close()
