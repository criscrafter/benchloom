import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QSettings  # noqa: E402
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
