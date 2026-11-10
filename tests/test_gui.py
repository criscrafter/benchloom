import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from patchbay.gui.devices import DeviceDialog  # noqa: E402
from patchbay.gui.main_window import MainWindow  # noqa: E402
from patchbay.plugin import PluginRegistry  # noqa: E402
from patchbay.project import DeviceConfig, Project, create_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_window(tmp_path):
    # a settings file of its own keeps tests out of the user's recent-projects list
    return MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))


def wait_for_run(app, window, timeout=30):
    done = []
    window.runner.finished.connect(lambda *args: done.append(args))
    deadline = time.monotonic() + timeout
    while not done and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert done, "run did not finish"
    app.processEvents()
    return done[0]


def test_open_run_and_plot(app, tmp_path):
    project = create_project(tmp_path / "proj")
    window = make_window(tmp_path)
    assert window.open_project(project.path)
    assert "psu:set_voltage" in window.editor.toPlainText()
    assert window.devices.tree.topLevelItem(0).childCount() > 5

    window.run()
    status, error, path = wait_for_run(app, window)
    assert status == "ok", error
    assert "Sweep finished" in window.console.toPlainText()
    assert len(window.plot.rows) == 10
    assert window.run_action.isEnabled() and not window.stop_action.isEnabled()
    window.close()


def test_error_marks_the_line(app, tmp_path):
    project = create_project(tmp_path / "proj")
    window = make_window(tmp_path)
    window.open_project(project.path)
    window.editor.setPlainText("print('hi')\npsu:set_voltage(99)\n")
    window.run()
    status, error, _ = wait_for_run(app, window)
    assert status == "error"
    assert window.editor._error_line == 2
    assert "above the maximum" in window.console.toPlainText()
    window.close()


def test_stop_ends_an_endless_script(app, tmp_path):
    project = create_project(tmp_path / "proj")
    window = make_window(tmp_path)
    window.open_project(project.path)
    window.editor.setPlainText("while true do sleep(0.05) end\n")
    window.run()
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    window.stop()
    status, _, _ = wait_for_run(app, window)
    assert status == "stopped"
    window.close()


def test_device_dialog_round_trips_resources(app):
    registry = PluginRegistry.default()
    for resource in ("serial://COM7?baud=115200", "tcp://10.0.0.5:5025",
                     "prologix://COM4/12", "visa://GPIB0::5::INSTR",
                     "serial://COM3?baud=9600&term=\\r\\n"):
        dialog = DeviceDialog(registry, set(), DeviceConfig("dmm", "generic.scpi", resource))
        assert dialog.config().resource == resource
    dialog = DeviceDialog(registry, {"psu"})
    assert dialog.config().resource == "sim://"
    for resource in ("usbtmc://03EB:2065/GPIB_22_ABC", "usbtmc://03EB:2065"):
        dialog = DeviceDialog(registry, set(), DeviceConfig("dmm", "hp.3478a", resource))
        assert dialog.connection_box.currentData() == "usbtmc"
        assert dialog.config().resource == resource
    dialog = DeviceDialog(registry, set(), DeviceConfig("dmm", "hp.3478a", "usbgpib://GPIB_23_X"))
    assert dialog.config().resource == "usbtmc://03EB:2065/GPIB_23_X"
    # the scope's USB ID is filled in from its plugin
    dialog = DeviceDialog(registry, set())
    dialog.type_box.setCurrentIndex(dialog.type_box.findData("hantek.dso5000p"))
    dialog.connection_box.setCurrentIndex(dialog.connection_box.findData("usb"))
    assert dialog.config().resource == "usb://049F:505A"


def test_project_save_round_trip(tmp_path):
    project = create_project(tmp_path / "proj")
    project.devices["ard"] = DeviceConfig("ard", "generic.serial", "serial://COM3?baud=9600",
                                          {"startup_delay": 0.5})
    project.save()
    loaded = Project.load(project.path)
    assert list(loaded.devices) == ["psu", "ard"]
    assert loaded.devices["ard"].settings == {"startup_delay": 0.5}
    assert loaded.devices["ard"].resource == "serial://COM3?baud=9600"


def test_device_dialog_shows_the_settings_a_plugin_declares(app):
    registry = PluginRegistry.default()
    config = DeviceConfig("eload", "kunkin.kp184", "serial://COM5?baud=9600",
                          {"address": 3, "timeout": 1.0})
    dialog = DeviceDialog(registry, set(), config)
    assert set(dialog._setting_fields) == {"address", "crc_high_byte_first", "leave_output_on"}
    address = dialog._setting_fields["address"][1]
    assert address.value() == 3 and address.maximum() == 247
    dialog._setting_fields["leave_output_on"][1].setChecked(True)
    # undeclared settings (timeout) are kept; defaults are left out of the file
    assert dialog.config().settings == {"address": 3, "timeout": 1.0, "leave_output_on": True}
    address.setValue(1)
    dialog._setting_fields["leave_output_on"][1].setChecked(False)
    assert dialog.config().settings == {"timeout": 1.0}

    dialog.type_box.setCurrentIndex(dialog.type_box.findData("generic.scpi"))
    assert not dialog._setting_fields and dialog.settings_box.isHidden()


# --- plugin manager and settings ------------------------------------------------------
def test_plugin_manager_switches_off_installs_and_removes(app, tmp_path, monkeypatch):
    from patchbay.config import AppConfig, user_plugin_dir
    from patchbay.gui import plugins as plugins_module
    from patchbay.gui.plugins import PluginManagerDialog

    project = create_project(tmp_path / "proj")
    window = make_window(tmp_path)
    window.open_project(project.path)
    dialog = PluginManagerDialog(project.path, window)
    assert dialog.tree.topLevelItemCount() == len(dialog.registry.found) >= 6
    item = dialog.item_for("hp")
    item.setCheckState(0, Qt.CheckState.Unchecked)
    assert "hp" in AppConfig.load().disabled_plugins and dialog.changed
    assert dialog.item_for("hp").checkState(0) == Qt.CheckState.Unchecked
    dialog.tree.setCurrentItem(dialog.item_for("hp"))
    assert not dialog.remove_button.isEnabled()  # built-in plugins are not removed
    assert "Switched off" in dialog.details.toPlainText()

    source = tmp_path / "acme"
    source.mkdir()
    (source / "plugin.toml").write_text(
        '[plugin]\nid = "acme"\nname = "Acme"\n\n[[device]]\nid = "meter"\n'
        'driver = "meter:Meter"\n', encoding="utf-8")
    (source / "meter.py").write_text(
        "from patchbay.plugin import Device\nclass Meter(Device):\n    pass\n")
    installed = dialog.install(source, confirm=False)
    assert installed.path.parent == user_plugin_dir()
    assert dialog.selected().id == "acme" and dialog.remove_button.isEnabled()
    monkeypatch.setattr(plugins_module, "move_to_trash", lambda path: False)
    dialog.remove_selected(confirm=False)
    assert dialog.item_for("acme") is None and not installed.path.exists()

    window.reload_plugins()  # what the main window does after the dialog
    assert "hp.3478a" not in window.devices.registry.devices


def test_settings_dialog_saves_and_applies(app, tmp_path):
    from patchbay.config import AppConfig
    from patchbay.gui.settings import SettingsDialog

    window = make_window(tmp_path)
    dialog = SettingsDialog(window.prefs, window)
    assert dialog.theme_combo.currentData() == "system"
    dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("dark"))
    dialog.projects_edit.setText(str(tmp_path / "projects"))
    dialog.reopen_check.setChecked(True)
    dialog.editor_font.setValue(15)
    dialog.console_font.setValue(12)
    dialog.console_lines.setValue(2000)
    dialog.add_plugin_folder(str(tmp_path / "dev"))
    dialog.add_plugin_folder(str(tmp_path / "dev"))  # listed once
    dialog.accept()
    assert dialog.plugin_folders_changed
    assert AppConfig.load().plugin_paths == [str(tmp_path / "dev")]
    window.set_theme(window.theme())
    window.apply_preferences()
    assert window.theme() == "dark" and window.theme_actions["dark"].isChecked()
    assert window.editor.font().pointSize() == 15
    assert window.console.font().pointSize() == 12
    assert window.console.maximumBlockCount() == 2000
    assert window.prefs.projects_dir() == tmp_path / "projects"
    assert window.prefs.get("reopen_last") is True

    # a fresh window reads the same values back; Restore defaults resets the fields
    again = SettingsDialog(make_window(tmp_path).prefs)
    assert again.editor_font.value() == 15
    again.restore_defaults()
    assert again.editor_font.value() == 11 and again.theme_combo.currentData() == "system"
    assert again.folder_list.count() == 1  # plugin folders are kept
    window.set_theme("system")
