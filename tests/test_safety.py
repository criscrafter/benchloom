# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""A project's own plugins load only with the user's consent, and a test that is
stopped switches its devices off, also when its script does not stop."""
import json
import multiprocessing
import os
import queue
import time
from pathlib import Path

import pytest

from benchloom.cli import main as cli
from benchloom.config import AppConfig
from benchloom.engine import run_project
from benchloom.plugin import PluginError, PluginRegistry
from benchloom.project import create_project
from benchloom.worker import run_worker

MANIFEST = """[plugin]
id = "{id}"
name = "Bench"

[[device]]
id = "supply"
driver = "supply:Supply"
simulator = "supply:Sim"
"""

# Leaves a file behind when its code is loaded, and another when it is shut down.
DRIVER = """from pathlib import Path

from benchloom import SimTransport
from benchloom.plugin import Device, command

HERE = Path(__file__).parent
(HERE / "loaded.txt").write_text("x")


class Sim(SimTransport):
    def handle(self, command):
        return None


class Supply(Device):
    @command
    def on(self) -> None:
        pass

    def close(self) -> None:
        (HERE / "switched_off.txt").write_text("x")
"""

PROJECT = """[project]
name = "proj"
script = "main.lua"

[devices.psu]
device = "{id}.supply"
resource = "sim://"
"""


def make_project(tmp_path, lua="psu:on()", plugin_id="bench"):
    project = create_project(tmp_path / "proj")
    folder = project.path / "plugins" / plugin_id
    folder.mkdir(parents=True)
    (folder / "plugin.toml").write_text(MANIFEST.format(id=plugin_id), encoding="utf-8")
    (folder / "supply.py").write_text(DRIVER, encoding="utf-8")
    (project.path / "benchloom.toml").write_text(PROJECT.format(id=plugin_id), encoding="utf-8")
    (project.path / "main.lua").write_text(lua, encoding="utf-8")
    return project, folder


# --- a project's own plugins --------------------------------------------------------
def test_project_plugins_are_not_loaded_without_consent(tmp_path):
    project, folder = make_project(tmp_path)
    registry = PluginRegistry.default(project.path)
    assert [info.id for info in registry.blocked] == ["bench"]
    assert "bench" not in registry.plugins
    with pytest.raises(PluginError, match="was not allowed to load"):
        registry.device("bench.supply")

    lines = []
    result = run_project(project.path, on_print=lines.append)
    assert result.status == "error" and "was not allowed to load" in result.error
    assert lines[0].startswith("Note: this project's own plugin 'Bench' was not loaded")
    assert not (folder / "loaded.txt").exists()  # none of its code ran


def test_a_project_plugin_cannot_take_the_place_of_a_built_in_one_unasked(tmp_path):
    project, folder = make_project(tmp_path, plugin_id="demo")
    registry = PluginRegistry.default(project.path)
    assert registry.plugins["demo"].source == "built-in"
    assert [info.id for info in registry.blocked] == ["demo"]


def test_consent_is_remembered_per_project(tmp_path):
    project, folder = make_project(tmp_path)
    other, other_folder = make_project(tmp_path / "other")
    result = run_project(project.path, on_print=lambda _: None, trust_plugins=True)
    assert result.status == "ok", result.error
    assert (folder / "loaded.txt").exists() and (folder / "switched_off.txt").exists()
    assert AppConfig.load().trusts(project.path)
    assert run_project(project.path, on_print=lambda _: None).status == "ok"  # no flag needed
    assert run_project(other.path, on_print=lambda _: None).status == "error"
    assert not (other_folder / "loaded.txt").exists()

    config = AppConfig.load()
    config.set_trusted(project.path, False)
    config.save()
    assert PluginRegistry.default(project.path).blocked


def test_run_on_the_command_line_needs_the_flag(tmp_path, capsys):
    project, folder = make_project(tmp_path)
    assert cli(["run", str(project.path)]) == 1
    assert not (folder / "loaded.txt").exists()
    assert cli(["run", str(project.path), "--trust-plugins"]) == 0
    capsys.readouterr()


# --- stopping -------------------------------------------------------------------------
def run_in_worker(project, stop_after, stop_timeout):
    """Run the project as the app does, in its own process; returns the messages and
    whether the process ended by itself."""
    context = multiprocessing.get_context("spawn")
    messages, stop = context.Queue(), context.Event()
    process = context.Process(target=run_worker, daemon=True,
                              args=(str(project.path), messages, stop, None, None, stop_timeout))
    process.start()
    seen, stopped_at = [], None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            message = messages.get(timeout=0.1)
        except queue.Empty:
            message = None
        if message is not None:
            seen.append(message)
            if message == ("print", "started") and stopped_at is None:
                stopped_at = time.monotonic() + stop_after
            if message[0] == "done":
                break
        if stopped_at is not None and time.monotonic() >= stopped_at:
            stop.set()
    process.join(10)
    ended = not process.is_alive()
    if not ended:
        process.terminate()
    return seen, ended


@pytest.mark.parametrize("lua", [
    # a loop in a coroutine: Stop does not reach it
    "psu:on() print('started') coroutine.wrap(function() while true do end end)()",
    # a script that catches the stop
    "psu:on() print('started') while true do pcall(function() while true do end end) end",
])
def test_a_script_that_does_not_stop_still_gets_its_devices_switched_off(tmp_path, lua):
    project, folder = make_project(tmp_path, lua)
    config = AppConfig.load()
    config.set_trusted(project.path)
    config.save()
    seen, ended = run_in_worker(project, stop_after=0.2, stop_timeout=0.5)
    assert ended, "the run's process did not end by itself"
    assert ("shutdown",) in seen
    done = seen[-1]
    assert done[:2] == ("done", "stopped") and done[3], seen
    assert (folder / "switched_off.txt").exists()
    meta = json.loads((Path(done[3]) / "run.json").read_text(encoding="utf-8"))
    assert meta["status"] == "stopped"
    assert any(m[0] == "print" and "did not stop within" in m[1] for m in seen)


def test_a_script_that_stops_is_not_cut_short(tmp_path):
    project, folder = make_project(tmp_path, "psu:on() print('started') while true do sleep(0.05) end")
    config = AppConfig.load()
    config.set_trusted(project.path)
    config.save()
    seen, ended = run_in_worker(project, stop_after=0.2, stop_timeout=5)
    assert ended and seen[-1][:2] == ("done", "stopped")
    assert ("print", "Stopped.") in seen
    assert not any(m[0] == "print" and "did not stop within" in m[1] for m in seen)
    assert (folder / "switched_off.txt").exists()


# --- desktop app ----------------------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def make_window(tmp_path):
    from PySide6.QtCore import QSettings

    from benchloom.gui.main_window import MainWindow

    return MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))


def test_opening_a_project_asks_before_loading_its_plugins(app, tmp_path):
    project, folder = make_project(tmp_path)
    window = make_window(tmp_path)
    asked = []
    window.confirm_project_plugins = lambda project, plugins: asked.append(
        [p.name for p in plugins]) or False
    assert window.open_project(project.path)
    assert asked == [["Bench"]]
    assert not (folder / "loaded.txt").exists()  # declined: nothing of it ran
    assert window.devices.tree.topLevelItem(0).childCount() == 0
    assert "was not allowed to load" in window.devices.tree.topLevelItem(0).toolTip(0)

    window.confirm_project_plugins = lambda project, plugins: asked.append("again") or True
    assert window.open_project(project.path)  # asked again, and allowed this time
    assert (folder / "loaded.txt").exists()
    assert window.devices.tree.topLevelItem(0).childCount() == 1
    window.confirm_project_plugins = lambda project, plugins: pytest.fail("asked again")
    assert window.open_project(project.path)
    window.close()


def test_closing_the_window_lets_the_run_switch_its_devices_off(app, tmp_path):
    project, folder = make_project(tmp_path, "psu:on() print('started') while true do sleep(0.05) end")
    config = AppConfig.load()
    config.set_trusted(project.path)
    config.save()
    window = make_window(tmp_path)
    assert window.open_project(project.path)
    done = []
    window.runner.finished.connect(lambda *args: done.append(args))
    window.run()
    deadline = time.monotonic() + 30
    while "started" not in window.console.toPlainText() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    window.show()
    window.close()
    assert window.isVisible() and window.runner.running  # still there, stopping
    while not done and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert done and done[0][0] == "stopped" and done[0][2]  # a clean stop, with a run folder
    assert "forced" not in window.console.toPlainText()
    assert (folder / "switched_off.txt").exists()
    assert not window.isVisible()  # and then the window closed


SLOW_DRIVER = DRIVER.replace(
    "    def close(self) -> None:",
    "    @command\n"
    "    def slow(self) -> None:\n"
    "        import time\n"
    "        time.sleep(7)\n"
    "\n"
    "    def close(self) -> None:")


def test_stop_during_a_slow_device_command_still_switches_the_devices_off(app, tmp_path):
    # The command outlasts the app's first deadline (FORCE_STOP_AFTER_MS) but ends within
    # the run's wait for it (STOP_COMMAND_WAIT_S): the app must wait, not kill the run.
    from benchloom.gui.runner import RunController

    project, folder = make_project(tmp_path, "psu:on() print('started') psu:slow()")
    (folder / "supply.py").write_text(SLOW_DRIVER, encoding="utf-8")
    config = AppConfig.load()
    config.set_trusted(project.path)
    config.save()
    runner = RunController()
    printed, done = [], []
    runner.printed.connect(printed.append)
    runner.finished.connect(lambda *args: done.append(args))
    runner.start(project.path)
    deadline = time.monotonic() + 60
    while "started" not in printed and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    time.sleep(0.3)  # inside psu:slow() now
    runner.stop()
    while not done and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert done and done[0][0] == "stopped" and done[0][2], (done, printed)
    assert not any("forced" in text for text in printed), printed
    assert (folder / "switched_off.txt").exists()
