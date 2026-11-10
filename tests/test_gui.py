# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from benchloom.gui.devices import DeviceDialog  # noqa: E402
from benchloom.gui.main_window import MainWindow  # noqa: E402
from benchloom.plugin import PluginRegistry  # noqa: E402
from benchloom.project import DeviceConfig, Project, create_project  # noqa: E402


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
    from benchloom.config import AppConfig, user_plugin_dir
    from benchloom.gui import plugins as plugins_module
    from benchloom.gui.plugins import PluginManagerDialog

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
    details = dialog.details
    assert details.status_field.text() == "Switched off"
    assert details.id_field.text() == "hp" and details.source_field.text() == "Built-in"
    assert details.device_list.count() == 3 and details.device_box.title() == "Devices (3)"
    details.device_list.setCurrentRow(2)  # the 53131A: its settings are listed one per line
    assert details.device_type.text() == "hp.53131a"
    assert details.device_default_name.text() == "counter"
    assert "Gate time" in details.device_settings.toPlainText()
    dialog.tree.setCurrentItem(dialog.item_for("hantek"))
    assert details.device_resource.text() == "usb://049F:505A"
    assert details.device_connections.text() == "usb, sim"

    source = tmp_path / "acme"
    source.mkdir()
    (source / "plugin.toml").write_text(
        '[plugin]\nid = "acme"\nname = "Acme"\n\n[[device]]\nid = "meter"\n'
        'driver = "meter:Meter"\n', encoding="utf-8")
    (source / "meter.py").write_text(
        "from benchloom.plugin import Device\nclass Meter(Device):\n    pass\n")
    installed = dialog.install(source, confirm=False)
    assert installed.path.parent == user_plugin_dir()
    assert dialog.selected().id == "acme" and dialog.remove_button.isEnabled()
    monkeypatch.setattr(plugins_module, "move_to_trash", lambda path: False)
    dialog.remove_selected(confirm=False)
    assert dialog.item_for("acme") is None and not installed.path.exists()

    broken = user_plugin_dir() / "broken"
    broken.mkdir()
    (broken / "plugin.toml").write_text("not toml [")
    dialog.reload()
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(dialog.tree.topLevelItemCount() - 1))
    assert details.status_field.text() == "Failed to load"
    assert details.description_label.text() == "Error" and not details.device_box.isVisibleTo(dialog)

    window.reload_plugins()  # what the main window does after the dialog
    assert "hp.3478a" not in window.devices.registry.devices


def test_settings_dialog_saves_and_applies(app, tmp_path):
    from benchloom.config import AppConfig
    from benchloom.gui.settings import SettingsDialog

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


def test_device_dialog_offers_the_fixed_baud_rate(app):
    dialog = DeviceDialog(PluginRegistry.default(), set())
    dialog.type_box.setCurrentIndex(dialog.type_box.findData("feeltech.fy6900"))
    dialog.connection_box.setCurrentIndex(dialog.connection_box.findData("serial"))
    dialog.serial_port.set_port("COM12")
    assert dialog.name_edit.text() == "gen"
    assert dialog.config().resource == "serial://COM12?baud=115200"


def test_new_project_dialog_offers_templates(app, tmp_path):
    from benchloom.gui.main_window import NewProjectDialog

    dialog = NewProjectDialog(location=tmp_path)
    assert [dialog.template_combo.itemData(i) for i in range(dialog.template_combo.count())] \
        == ["example", "bode"]
    assert dialog.lua_radio.isEnabled() and "power supply" in dialog.template_info.text()
    dialog.visual_radio.setChecked(True)
    dialog.template_combo.setCurrentIndex(1)
    assert dialog.template() == "bode" and dialog.mode() == "lua"
    assert not dialog.lua_radio.isEnabled() and not dialog.visual_radio.isEnabled()
    assert "scope CH2" in dialog.template_info.text()
    dialog.template_combo.setCurrentIndex(0)
    assert dialog.visual_radio.isEnabled()


def test_bode_project_plots_on_a_log_scale(app, tmp_path):
    project = create_project(tmp_path / "bode", template="bode")
    window = make_window(tmp_path)
    window.open_project(project.path)
    plot = window.plot
    assert plot.log_x.isChecked() and plot.right_columns == {"phase_deg"}
    window.run()
    status, error, _ = wait_for_run(app, window)
    assert status == "ok", error
    plot._redraw()
    assert plot.x_axis.currentText() == "frequency_Hz"
    assert plot._curves["phase_deg"] in plot.right_view.addedItems
    assert set(plot._curves) == {"gain_dB", "phase_deg"}  # time_s is hidden
    assert plot._curves["gain_dB"].opts["logMode"][0]  # also the curve outside the PlotItem
    assert plot.plot.getPlotItem().getAxis("bottom").logMode

    plot.log_x.setChecked(False)  # the user's choice wins over the project's next time
    assert not plot._curves["gain_dB"].opts["logMode"][0]
    window.close()
    window = make_window(tmp_path)
    window.open_project(project.path)
    assert not window.plot.log_x.isChecked()
    window.close()


def test_numbers_on_the_front_panel_drop_trailing_zeros(app):
    from PySide6.QtCore import QLocale

    from benchloom.gui.panel_view import NumberBox, PanelView, WidgetDialog, format_number
    from benchloom.panel import Panel, Widget

    QLocale.setDefault(QLocale(QLocale.Language.German, QLocale.Country.Germany))
    try:
        box = NumberBox()
        box.setRange(0, 1e6)
        box.setValue(50.0)
        assert box.text() == "50"
        box.setValue(1234.5)
        assert box.text() == "1234,5"  # no thousands separator, the locale's point
        fixed = NumberBox(2)
        fixed.setRange(0, 100)
        fixed.setValue(3)
        assert fixed.text() == "3,00"
        assert format_number(-89.2581, "°") == "-89,26 °"
        assert format_number(5000.0, "Hz", 1) == "5000,0 Hz"
    finally:
        QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))

    panel = Panel(None, [Widget("display", "gain", "Gain", unit="dB")])
    dialog = WidgetDialog(panel, panel.widgets[0])
    assert dialog.decimals_box.currentData() is None  # automatic
    dialog.decimals_box.setCurrentIndex(dialog.decimals_box.findData(1))
    dialog.accept()
    assert panel.widgets[0].decimals == 1
    view = PanelView()
    view.set_panel(panel)
    view.show_value("gain", -3.0127)
    assert view.items["gain"].control.text() == "-3.0 dB"


def test_plot_columns_menu_hides_curves(app):
    from benchloom.gui.plot import PlotPanel

    plot = PlotPanel()
    for n in range(3):
        plot.add_row({"time_s": n, "V": n * 2.0, "I": n / 10})
    plot._redraw()
    assert [a.text() for a in plot.columns_menu.actions()] == ["V", "I"]
    seen = []
    plot.hidden_columns_changed.connect(seen.append)
    plot.columns_menu.actions()[0].setChecked(False)  # hide V
    assert seen == [["V"]] and set(plot._curves) == {"I"}
    assert [a.text() for a in plot.right_menu.actions()] == ["I"]  # V is not offered
    plot.columns_menu.actions()[0].setChecked(True)
    assert set(plot._curves) == {"V", "I"}


def test_run_shows_waveforms_verdict_and_report(app, tmp_path):
    project = create_project(tmp_path / "bode", template="bode")
    window = make_window(tmp_path)
    window.open_project(project.path)
    values = window.panel_view.control_values()
    for name, value in (("start_hz", 50.0), ("stop_hz", 500.0), ("points_per_decade", 2.0),
                        ("check_limits", True)):
        window.panel_view.items[name].show_value(value)
    assert window.panel_view.control_values() != values
    assert not window.report_action.isEnabled()
    window.run()
    status, error, path = wait_for_run(app, window)
    assert status == "ok", error
    names = [name for name, _ in window.waveforms.waveforms]
    assert names == ["50 Hz", "158.1 Hz", "500 Hz"]
    assert window.waveforms.list.currentRow() == 2  # follows the newest
    assert "input" in window.waveforms.info.text() and "Vpp" in window.waveforms.info.text()
    assert window.statusBar().currentMessage().startswith("PASS")
    assert "PASS: 1 of 1 checks passed" in window.console.toPlainText()
    assert window.report_action.isEnabled()
    gain = next(a for a in window.plot.columns_menu.actions() if a.text() == "gain_dB")
    gain.setChecked(False)  # remembered per project
    window.close()
    window = make_window(tmp_path)
    window.open_project(project.path)
    assert "gain_dB" in window.plot.hidden and "time_s" in window.plot.hidden
    window.close()


def test_about_shows_the_licence_and_warranty_notice(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: shown.append(box) or 0)
    window = make_window(tmp_path)
    window.about_action.trigger()
    text = shown[0].informativeText()
    assert "WITHOUT ANY WARRANTY" in text and "Affero General Public License" in text
    assert "assistance from AI tools" in text and "Friedrich-Christer Rosenthal" in text
    assert 'href="https://github.com/criscrafter/benchloom"' in text
    window.close()


def test_command_line_version_shows_the_notice(capsys):
    from benchloom.cli import main

    with pytest.raises(SystemExit):
        main(["--version"])
    out = " ".join(capsys.readouterr().out.split())
    assert out.startswith("Benchloom 0.1.0") and "WITHOUT ANY WARRANTY" in out
