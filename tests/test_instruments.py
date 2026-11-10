import math

import pytest

from patchbay.engine import open_device, run_project
from patchbay.modbus import crc16, crc_ok, with_crc
from patchbay.plugin import CommandError, PluginRegistry
from patchbay.project import DeviceConfig, Project, create_project

REGISTRY = PluginRegistry.default()


def open_sim(device_id, **settings):
    """Open a device on its simulator; also return the simulator for inspection."""
    device = open_device(REGISTRY, DeviceConfig("dev", device_id, "sim://", settings))
    return device, device.transport


def record_writes(transport):
    """Make the transport remember every frame written to it."""
    sent = []
    original = transport.write_raw

    def write_raw(data):
        sent.append(bytes(data))
        original(data)

    transport.write_raw = write_raw
    return sent


# --- Modbus / KP184 ------------------------------------------------------------------
def test_modbus_crc():
    # the textbook example: read one holding register of device 1
    assert with_crc(bytes.fromhex("010300000001")) == bytes.fromhex("01030000000184 0a")
    assert crc16(b"123456789") == 0x4B37
    assert crc_ok(bytes.fromhex("01030000000184 0a"))
    assert crc_ok(bytes.fromhex("0103000000010a84"))  # high byte first is accepted too
    assert not crc_ok(bytes.fromhex("01030000000184 0b"))


def test_kp184_frames_match_the_protocol():
    load, sim = open_sim("kunkin.kp184")
    sent = record_writes(sim)
    load.set_input(True)
    load.set_voltage(12)
    load.measure_voltage()
    assert sent[0] == with_crc(bytes.fromhex("0106010e00010400000001"))
    assert sent[1] == with_crc(bytes.fromhex("010601120001040000 2ee0"))  # 12000 mV
    assert sent[2] == with_crc(bytes.fromhex("010301220004"))



def test_kp184_write_acknowledgement_is_checked():
    # A real KP184 answers a write with 8 bytes: address, 06, register, low 16 bits
    # of the value, CRC. Anything else is reported.
    load, sim = open_sim("kunkin.kp184")
    load.set_current(1.234)
    assert sim.handle_bytes(with_crc(bytes.fromhex("0106011600010400 0004d2"))) ==         with_crc(bytes.fromhex("0106011604d2"))
    sim.handle_bytes = lambda data: with_crc(bytes.fromhex("0106011600 00"))
    with pytest.raises(CommandError, match="unexpected reply"):
        load.set_current(1.234)


def test_kp184_reports_a_mode_it_did_not_take():
    # Seen on hardware with the battery test active: CV is acknowledged, but the load
    # stays in CC.
    load, sim = open_sim("kunkin.kp184")
    original = sim._read
    sim._read = lambda register: 1 if register == 0x0110 else original(register)
    with pytest.raises(CommandError, match="did not switch to CV mode"):
        load.set_mode("CV")


def test_kp184_masks_the_remote_flag():
    load, sim = open_sim("kunkin.kp184")
    load.set_mode("CR")
    assert sim._read(0x0110) & 0x10000000
    assert load.get_mode() == "CR" and not load.is_input_on()

def test_kp184_modes_and_measurements():
    load, sim = open_sim("kunkin.kp184")
    assert not load.is_input_on()
    assert load.measure_current() == 0
    load.constant_current(5)
    load.set_input(True)
    assert load.get_mode() == "CC" and load.is_input_on()
    assert load.measure_current() == pytest.approx(5, abs=0.01)
    assert load.measure_voltage() == pytest.approx(12.35, abs=0.01)  # 12.6 V − 5 A × 50 mΩ
    assert load.measure_power() == pytest.approx(61.75, abs=0.2)
    load.constant_resistance(2.4)
    assert load.measure_current() == pytest.approx(12.6 / 2.45, abs=0.01)
    load.constant_power(50)
    assert load.measure_power() == pytest.approx(50, abs=0.3)
    load.constant_voltage(12)
    assert load.measure_voltage() == pytest.approx(12, abs=0.01)
    assert load.identify().startswith("KP184 at address 1: input on")


def test_kp184_close_switches_input_off_unless_told_not_to():
    load, sim = open_sim("kunkin.kp184")
    load.set_input(True)
    load.close()
    assert sim.registers[0x010E] == 0
    load, sim = open_sim("kunkin.kp184", leave_output_on=True)
    load.set_input(True)
    load.close()
    assert sim.registers[0x010E] == 1


def test_kp184_wrong_address_reports_no_reply():
    load, _ = open_sim("kunkin.kp184", address=5)
    with pytest.raises(CommandError, match="no reply from the KP184"):
        load.measure_voltage()


def test_kp184_setpoints_are_range_checked():
    load, _ = open_sim("kunkin.kp184")
    with pytest.raises(CommandError, match="above the maximum of 400 W"):
        load.call("set_power", 500)
    with pytest.raises(CommandError, match="must be one of"):
        load.call("set_mode", "XX")


# --- HP 6655A --------------------------------------------------------------------------
def test_6655a_cv_and_cc():
    psu, sim = open_sim("hp.6655a")
    assert psu.idn().startswith("Hewlett-Packard,6655A")
    psu.set_voltage(10)
    psu.set_current(1)
    assert psu.get_mode() == "OFF"
    psu.set_output(True)
    assert psu.is_output_on()
    assert psu.measure_voltage() == pytest.approx(10, abs=0.01)
    assert psu.measure_current() == pytest.approx(0.2, abs=0.01)  # 10 V into 50 Ω
    assert psu.get_mode() == "CV"
    psu.set_current(0.1)
    assert psu.get_mode() == "CC"
    assert psu.measure_voltage() == pytest.approx(5, abs=0.01)


def test_6655a_protection():
    psu, sim = open_sim("hp.6655a")
    psu.set_voltage(20)
    psu.set_current(1)
    psu.set_output(True)
    psu.set_ovp(15)
    assert psu.get_faults() == "over-voltage"
    assert psu.measure_voltage() == pytest.approx(0, abs=0.01)
    psu.set_ovp(30)
    psu.clear_protection()
    assert psu.get_faults() == ""
    assert psu.measure_voltage() == pytest.approx(20, abs=0.01)


def test_6655a_instrument_errors_fail_the_command():
    psu, sim = open_sim("hp.6655a")
    with pytest.raises(CommandError, match='-113,"Undefined header"'):
        psu.send("VOLTS 5")
    psu.write("VOLT 500")  # raw write: no error check, the error stays queued
    assert psu.get_error().startswith("-222")
    psu, sim = open_sim("hp.6655a", check_errors=False)
    psu.send("VOLTS 5")  # not checked


def test_6655a_close_switches_output_off():
    psu, sim = open_sim("hp.6655a")
    psu.set_output(True)
    psu.close()
    assert sim.output is False


# --- HP 3478A --------------------------------------------------------------------------
def test_3478a_reading_format():
    from patchbay_plugin_hp.hp3478a import format_reading  # loaded by the registry

    assert format_reading(5.00123) == "+5.00123E+0"
    assert format_reading(12.3456) == "+12.3456E+0"
    assert format_reading(1000.21) == "+1.00021E+3"
    assert format_reading(0.0123456) == "+12.3456E-3"
    assert format_reading(-0.5) == "-500.000E-3"
    assert format_reading(math.inf) == "+9.99999E+9"


def test_3478a_measurements_and_codes():
    dmm, sim = open_sim("hp.3478a")
    sent = record_writes(sim)
    assert dmm.measure_dc_voltage() == pytest.approx(5.00123)
    assert sent[-1] == b"F1RAZ1N5T3\n"
    assert dmm.measure_resistance() == pytest.approx(1000.21)
    assert dmm.measure_resistance_4wire() == pytest.approx(999.87)
    assert dmm.measure_ac_voltage() == pytest.approx(1.23456)
    assert dmm.measure_dc_current() == pytest.approx(0.0123456)
    assert dmm.measure_ac_current() == pytest.approx(0.0567)
    dmm.set_function("dc_volts")
    assert sent[-1] == b"F1RAZ1N5T4\n"
    dmm.set_digits(4)
    dmm.set_range(0.3)
    assert sent[-1] == b"F1R-1Z1N4T4\n"
    assert dmm.measure() == math.inf  # 5 V on the 300 mV range overloads
    dmm.set_range(25)
    assert dmm.measure() == pytest.approx(5.00123)
    assert sent[-1] == b"F1R1Z1N4T3\n"
    with pytest.raises(CommandError, match="no dc_volts range holds 1000"):
        dmm.set_range(1000)


def test_3478a_status_and_display():
    dmm, sim = open_sim("hp.3478a")
    sent = record_writes(sim)
    dmm.measure_resistance()
    assert dmm.get_status() == "ohms_2wire, 5½ digits, autorange, autozero on, front terminals"
    assert dmm.identify().startswith("HP 3478A: ohms_2wire")
    dmm.display_text("Hello, test~ 1234567890")
    assert sent[-1] == b"D2HELLO, TEST \n"  # 12 characters
    assert dmm.front_terminals() is True
    assert dmm.get_errors() == ""


# --- Chroma 6404 -----------------------------------------------------------------------
def test_6404_output_and_measurements():
    ac, sim = open_sim("chroma.6404")
    assert ac.idn().startswith("Chroma,6404")
    ac.set_frequency(50)
    ac.set_voltage(120)
    ac.set_output(True)
    assert ac.measure_voltage() == pytest.approx(120, abs=0.2)
    assert ac.measure_current() == pytest.approx(1.2, abs=0.01)  # 100 Ω load
    assert ac.measure_power() == pytest.approx(144, abs=0.5)
    assert ac.measure_frequency() == pytest.approx(50)
    assert ac.measure_power_factor() == pytest.approx(1)
    assert ac.measure_crest_factor() == pytest.approx(1.414, abs=0.001)


def test_6404_ranges_and_protection():
    ac, sim = open_sim("chroma.6404")
    ac.set_current_limit(10)
    ac.set_voltage(230)
    ac.set_output(True)
    assert ac.measure_voltage() == pytest.approx(150, abs=0.2)  # 150 V range clamps
    ac.set_range("auto")
    assert ac.get_faults() == "over-power"  # 230 V into 100 Ω is 529 VA > 375 VA
    assert not ac.is_output_on()
    ac.set_voltage(180)
    ac.clear_protection()
    ac.set_output(True)
    assert ac.measure_voltage() == pytest.approx(180, abs=0.3)
    ac.set_current_limit(1)
    assert ac.get_faults() == "peak current limit"
    with pytest.raises(CommandError, match="must be one of"):
        ac.call("set_range", "200")


def test_6404_rs232_remote_and_local():
    ac, sim = open_sim("chroma.6404")
    sent = record_writes(sim)
    sim.scheme = "serial"  # pretend the connection is RS-232
    ac.connect()
    ac.set_output(True)
    ac.close()
    assert sent[0] == b"SYST:REM\n"
    assert sent[-2:] == [b"OUTP OFF\n", b"SYST:LOC\n"]
    assert sim.output is False


# --- all together ----------------------------------------------------------------------
def test_bench_project_with_every_instrument(tmp_path):
    project = create_project(tmp_path / "bench")
    project.devices = {
        "psu": DeviceConfig("psu", "hp.6655a", "sim://"),
        "dmm": DeviceConfig("dmm", "hp.3478a", "sim://"),
        "eload": DeviceConfig("eload", "kunkin.kp184", "sim://"),
        "ac": DeviceConfig("ac", "chroma.6404", "sim://"),
    }
    project.save()
    (project.path / "main.lua").write_text("""
        psu:set_current(2)
        psu:set_voltage(24)
        psu:set_output(true)
        ac:set_voltage(115)
        ac:set_output(true)
        for amps = 1, 3 do
            eload:constant_current(amps)
            eload:set_input(true)
            sleep(0.01)
            log { set_A = amps, load_V = eload:measure_voltage(), load_A = eload:measure_current(),
                  psu_V = psu:measure_voltage(), dmm_V = dmm:measure_dc_voltage(),
                  ac_W = ac:measure_power() }
        end
        print(psu:get_mode(), eload:get_mode(), dmm:get_status())
    """, encoding="utf-8")
    lines = []
    result = run_project(project.path, on_print=lines.append)
    assert result.status == "ok", result.error
    assert len(result.rows) == 3
    assert result.rows[2]["load_A"] == pytest.approx(3, abs=0.01)
    assert result.rows[0]["psu_V"] == pytest.approx(24, abs=0.02)
    assert result.rows[0]["dmm_V"] == pytest.approx(5.00123)
    assert result.rows[0]["ac_W"] == pytest.approx(132.25, abs=0.5)
    assert lines[0].startswith("CV\tCC\tdc_volts")
    assert Project.load(project.path).devices["eload"].device == "kunkin.kp184"


def test_reserved_device_names_are_refused(tmp_path):
    from patchbay.project import ProjectError, device_name_problem

    for name in ("load", "print", "math", "panel", "sleep", "end", "_x", "2psu"):
        assert device_name_problem(name), name
    assert device_name_problem("eload") is None
    project = create_project(tmp_path / "proj")
    (project.path / "patchbay.toml").write_text(
        '[devices.print]\ndevice = "demo.psu"\n', encoding="utf-8")
    with pytest.raises(ProjectError, match="reserved"):
        Project.load(project.path)


def test_device_names_suggested_by_plugins():
    devices = REGISTRY.devices
    assert devices["hp.6655a"].default_name == "psu"
    assert devices["hp.3478a"].default_name == "dmm"
    assert devices["kunkin.kp184"].default_name == "eload"
    assert devices["chroma.6404"].default_name == "ac"
