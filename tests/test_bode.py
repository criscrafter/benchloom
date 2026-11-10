"""The Bode plot: the scope's gain/phase measurement, the simulated bench and the template."""
import math

import pytest

from patchbay.engine import open_device, run_project
from patchbay.plugin import CommandError, PluginRegistry
from patchbay.plugins.hantek.dso5000p import VOLTS_PER_DIV, fit_sine
from patchbay.project import DeviceConfig, Project, ProjectError, create_project, templates
from patchbay.transports import TransportError

REGISTRY = PluginRegistry.default()


def bench(cutoff_hz=150):
    gen = open_device(REGISTRY, DeviceConfig("gen", "feeltech.fy6900", "sim://", {}))
    scope = open_device(REGISTRY, DeviceConfig(
        "scope", "hantek.dso5000p", f"sim://?dut=rc_lowpass&cutoff_hz={cutoff_hz}", {}))
    gen.set_signal(1, "sine", 1000, 4, 0)
    gen.set_output(1, True)
    return gen, scope


def test_fit_sine_finds_amplitude_phase_and_offset():
    times = [i * 1e-5 for i in range(500)]  # 5 periods of 1 kHz
    volts = [0.3 + 1.5 * math.sin(2 * math.pi * 1000 * t - 0.6) for t in times]
    peak, phase, mean = fit_sine(times, volts, 1000)
    assert (peak, phase, mean) == pytest.approx((1.5, -0.6, 0.3), abs=1e-9)


@pytest.mark.parametrize("hertz, gain_db, phase", [
    (15, -0.043, -5.71), (150, -3.01, -45.0), (1500, -20.04, -84.29), (15000, -40.0, -89.43)])
def test_measure_response_of_a_simulated_rc_lowpass(hertz, gain_db, phase):
    gen, scope = bench()
    gen.set_frequency(1, hertz)
    r = scope.measure_response(hertz)
    assert r["gain_db"] == pytest.approx(gain_db, abs=0.05)
    assert r["phase_deg"] == pytest.approx(phase, abs=0.3)
    assert r["input_vpp"] == pytest.approx(4, rel=0.01)
    assert len(r["input"]["volts"]) == len(r["output"]["time"]) > 1000  # the traces measured
    # The output spans at least two divisions; it shares the input's range while it
    # can, since the scope's ranges differ by up to 2 %.
    ranges = scope.settings_block()
    volts_per_div = VOLTS_PER_DIV[ranges["VERT-CH2-VB"]]
    assert 2 <= r["output_vpp"] / volts_per_div <= 6
    assert (ranges["VERT-CH1-VB"] == ranges["VERT-CH2-VB"]) == (hertz <= 150)


def test_measure_response_limits():
    gen, scope = bench()
    with pytest.raises(CommandError, match="different channels"):
        scope.measure_response(1000, 1, 1)
    with pytest.raises(CommandError, match="too slow"):
        scope.measure_response(1)
    scope.measure_response(2)  # 40 ms/div, the slowest timebase that does not scroll
    assert scope.get_timebase() == 0.04
    gen.set_output(1, False)
    with pytest.raises(CommandError, match="no 1000 Hz signal"):
        scope.measure_response(1000)


def test_unknown_simulated_circuit():
    with pytest.raises(TransportError, match="unknown simulated circuit 'notch'"):
        open_device(REGISTRY, DeviceConfig("scope", "hantek.dso5000p", "sim://?dut=notch", {}))


def test_templates_are_listed():
    found = {t.id: t for t in templates()}
    assert found["example"].mode is None
    assert found["bode"].mode == "lua" and "Bode" in found["bode"].title
    with pytest.raises(ProjectError, match="no project template 'nope'"):
        create_project("unused", template="nope")


def test_bode_template_runs_on_simulators(tmp_path):
    project = create_project(tmp_path / "my bode's test", "visual", template="bode")
    assert project.mode == "lua"  # the template decides
    assert project.name == "my bode's test"
    assert project.plot == {"x": "frequency_Hz", "log_x": True, "right": ["phase_deg"],
                            "hide": ["time_s"]}
    assert set(project.devices) == {"gen", "scope"}
    assert (project.path / "panel.json").is_file()
    assert not (project.path / "template.toml").exists()

    printed = []
    result = run_project(project.path, on_print=printed.append,
                         panel_values={"start_hz": 5, "stop_hz": 5000, "points_per_decade": 3,
                                       "check_limits": True, "limit_low_hz": 140,
                                       "limit_high_hz": 145})
    assert result.status == "ok", result.error
    assert [round(r["frequency_Hz"]) for r in result.rows] == \
        [5, 11, 23, 50, 108, 232, 500, 1077, 2321, 5000]
    corner = next(line for line in printed if line.startswith("Upper -3 dB point"))
    assert float(corner.split()[4]) == pytest.approx(150, rel=0.01)
    assert not any(line.startswith("Lower") for line in printed)
    # one waveform per point, with input and output; the check fails: 150 Hz > 145 Hz
    waveforms = sorted((result.path / "waveforms").glob("*.csv"))
    assert len(waveforms) == 10 and waveforms[0].name == "001_5_Hz.csv"
    assert waveforms[0].read_text().splitlines()[0] == "time_s,input_V,output_V"
    assert result.verdict == "fail" and result.checks[0]["name"] == "-3 dB point"
    assert "FAIL" in (result.path / "report.html").read_text(encoding="utf-8")


def test_bode_template_finds_the_lower_point_of_a_highpass(tmp_path):
    project = create_project(tmp_path / "hp", template="bode")
    project.devices["scope"].resource = "sim://?dut=rc_highpass&cutoff_hz=1000"
    project.save()
    printed = []
    result = run_project(project.path, on_print=printed.append,
                         panel_values={"start_hz": 10, "stop_hz": 100000, "points_per_decade": 3,
                                       "save_waveforms": False})
    assert result.status == "ok", result.error
    corner = next(line for line in printed if line.startswith("Lower -3 dB point"))
    assert float(corner.split()[4]) == pytest.approx(1000, rel=0.02)
    assert not (result.path / "waveforms").exists() and result.verdict == "none"


def test_capture_reads_the_channels_that_are_on():
    gen, scope = bench()
    assert list(scope.capture()) == ["CH1"]
    scope.set_channel(2, True)
    both = scope.capture()
    assert list(both) == ["CH1", "CH2"] and len(both["CH2"]["volts"]) == 3200
    scope.set_channel(1, False)
    scope.set_channel(2, False)
    with pytest.raises(CommandError, match="both channels are off"):
        scope.capture()


def test_project_keeps_its_plot_settings(tmp_path):
    project = create_project(tmp_path / "b", template="bode")
    project.devices["gen"].resource = "serial://COM12"
    project.save()
    again = Project.load(project.path)
    assert again.plot == project.plot and again.devices["gen"].resource == "serial://COM12"
    (project.path / "patchbay.toml").write_text('plot = 1\n[project]\nname = "x"\n',
                                                encoding="utf-8")
    with pytest.raises(ProjectError, match=r"\[plot\] must be a table"):
        Project.load(project.path)
