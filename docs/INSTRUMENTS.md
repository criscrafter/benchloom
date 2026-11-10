# Instrument plugins

Every plugin below ships a simulator, so a test can be written with
`resource = "sim://"` and switched to the real instrument later by changing only
the resource.

The KP184, Chroma 6404 and Hantek DSO5102P drivers have been tried on real
instruments (see below). **The HP drivers have not been run against real
hardware yet.** They are written from the manuals or protocol notes listed for each
instrument and are tested against their simulators. Expect small fixes on first
contact.

Sources, loads and AC sources switch their output off when a test ends,
including when it fails or is stopped. To keep the output on, add
`leave_output_on = true` to the device entry. A forced stop (a script that does
not react to Stop within three seconds) cannot do this.

## Kunkin KP184 electronic load (`kunkin.kp184`)

- **Connection:** the RS-232 or RS-485 port, through a USB serial adapter.
  `resource = "serial://COM5?baud=9600"`; 9600 baud, 8N1 is the factory setting.
- **Device settings** (fields in the Add device dialog): Modbus address (set on
  the load, 1 by default); CRC high byte first (only if commands get no reply on
  newer firmware; some 2020+ firmware is reported to swap the CRC bytes); leave
  the input on after a test. `timeout = 0.5` can be added to the device entry by
  hand.
- **RS-485:** use a USB-RS485 adapter on the load's A/B terminals. The protocol
  is the same as on RS-232.
  - **Several loads on one adapter:** give each load its own address on its
    front panel. In Patchbay, add one device per load with the same port and
    baud rate and that load's address. Patchbay opens the port once and the
    loads share it.
  - **Echoing adapters:** some adapters hear their own transmission and pass it
    back before the reply. The driver recognises and skips that echo, so no
    setting is needed.
  - **Wiring:** the KP184's RS485A goes to the adapter's **+** terminal (T/R+,
    D+) and RS485B to **−**. Kunkin labels the lines the opposite way to the
    TIA-485 standard. With A and B swapped the load stays silent, and the
    adapter's RX/TX lamp may stay lit while idle.
  - **Tried on hardware:** a single KP184 through an FTDI USB-RS485 adapter, at
    9600 baud and address 1 (the factory settings), loading a 27.8 V bench
    supply up to 9.4 A and 250 W. A read takes about 90 ms. That adapter does
    not echo. Several loads on one bus and an echoing adapter are tested only
    against simulated loads so far.
- **Commands:** `set_input`, `set_mode` (CV, CC, CR, CW), setpoints per mode,
  `constant_current(amps)` and friends that set mode and value in one step,
  `measure_voltage`, `measure_current`, `measure_power` (computed as V × I),
  `read_raw_register` / `write_raw_register` for anything else.
- **Protocol source:** the open-source drivers
  [tuxmike/KP184](https://github.com/tuxmike/KP184) and
  [markmerz/kunkin-kp184-py](https://github.com/markmerz/kunkin-kp184-py), which
  are based on the EEVblog thread "Serial to usb on Kunkin KP184 electronic load".
  Kunkin's register numbering in its own manual is written for the KP182 family.
- **Not covered:** dynamic test, battery test, compare mode. Those registers are
  reported as unreliable.
- **Tried on hardware:** a KP184 through a CH340 USB-RS232 adapter (9600 baud,
  address 1, normal CRC order), loading a 22.37 V bench supply up to 6 A and
  about 130 W. Covered:
  - all four modes, setpoints and input on/off;
  - Lua test runs, including switching off at the end and on Stop.

  | Mode | Result |
  |---|---|
  | CC, 0.5 to 6 A | within 2 mA |
  | CR, 4 to 10 Ω | within 0.4 % (3.9 % at 100 Ω, i.e. 0.22 A) |
  | CW, 20 to 100 W | within 1 % |
  | CV below the supply voltage | holds the setpoint within a few mV |

- **Timing:** a read takes about 80 ms and a setting about 130 ms. The load
  refreshes its readings about every 0.23 s, so after changing a setpoint or
  switching the input, wait about 0.6 s before measuring. Readings taken sooner
  still show the old value.
- **Found on hardware:**
  - A write is acknowledged with 8 bytes: address, 0x06, register, low 16 bits of
    the value, CRC.
  - Once the load has received a write, bit 28 is set in its status registers;
    the driver ignores it.
  - Writing the undocumented mode values 4 to 7 is accepted and can leave the
    load in its battery test (display shows Ah). While that is active, CV is
    acknowledged but not taken. `set_mode` reads the mode back and reports this.
- **CH340 adapters on Windows** refuse to reopen a port with the settings it was
  last closed with. Patchbay works around it, so no setting is needed.

## GPIB through the xyphro UsbGpib adapter

[UsbGpib](https://github.com/xyphro/UsbGpib) turns one GPIB instrument into a
USB instrument (USBTMC). Patchbay can talk to it without a vendor VISA.

- **Connection:** "USB instrument or UsbGpib adapter" in the Add device dialog,
  or `resource = "usbtmc://03EB:2065"`. That is the first adapter found; add
  `/<serial>` to pick one. `usbgpib://` is the same, shorter. The adapter only
  appears on USB while a powered instrument is connected to it.
- **Once per PC (Windows):**
  1. Run `pip install "patchbay[usb]"` (pyusb, python-usbtmc and the libusb
     library).
  2. With the instrument switched on, use [Zadig](https://zadig.akeo.ie/) to
     install the WinUSB driver for the adapter (USB ID 03EB 2065). Zadig is a
     free download and needs no installation.
- **Alternative:** with a VISA installed (the adapter's author recommends R&S
  VISA), use `visa://USB0::0x03EB::0x2065::<serial>::INSTR` instead.
- **Instruments without `*IDN?`** (the HP 3478A): the adapter asks every
  instrument `*IDN?` at power-up. Turn that off once with
  `patchbay usbgpib "!autoid off"`. `patchbay usbgpib` with no command lists
  the adapters.
- **One instrument per adapter.** The adapter can address several GPIB
  instruments, but Patchbay does not switch between them yet.
- **Not tried on hardware.** The transport is tested against a stand-in for the
  USBTMC library only.

## HP 6655A power supply (`hp.6655a`)

- **Connection:** GPIB: `usbtmc://03EB:2065` (UsbGpib, above),
  `prologix://COM4/5` (Prologix or AR488 adapter, GPIB address 5) or
  `visa://GPIB0::5::INSTR`.
- **Ratings:** programmable to 122.85 V and 4.095 A; over-voltage protection to
  132 V (from Table 3-1 of the programming guide).
- **Commands:** `set_voltage`, `set_current`, `set_output`, `set_ovp`, `set_ocp`,
  `clear_protection`, `measure_voltage`, `measure_current`, `measure_power`,
  `get_mode` (CV, CC or OFF), `get_faults`, plus the common SCPI commands.
- **Error checking:** after each setting the driver asks the supply for errors,
  so a rejected command fails at the line that sent it. Turn this off with
  `check_errors = false` if it is too slow.
- **Source:** Agilent programming guide for the 664xA, 665xA, 667xA, 668xA and
  669xA supplies (part no. 5964-8269). The other 665xA models work with the same
  driver, but its limits are the 6655A's.

## HP 3478A multimeter (`hp.3478a`)

- **Connection:** GPIB, as above. The meter's address is set with the rear DIP
  switches.
- **Commands:** `measure_dc_voltage`, `measure_ac_voltage`, `measure_resistance`,
  `measure_resistance_4wire`, `measure_dc_current`, `measure_ac_current`, or
  `set_function` + `measure`; `set_range(value)` picks the smallest range holding
  the value, `set_autorange`, `set_digits` (3, 4 or 5), `set_autozero`,
  `display_text`, `display_normal`, `get_status`, `get_errors`,
  `front_terminals`.
- **Overload** returns infinity (`math.huge` in Lua).
- **Device entry option:** `digits = 5` (resolution at the start).
- **Slow readings:** 5½ digits on AC or high-ohm ranges can take over a second;
  add `?timeout=5` to the resource if readings time out.
- **Source:** HP 3478A quick reference card and technical manual
  (TM 11-6625-3071-14).
- **Uncertain:** the range field of `get_status` is shown as the meter's raw
  range number.

## Chroma 6404 AC source (`chroma.6404`)

- **Connection:** GPIB (`prologix://COM4/30`, `visa://…`) or RS-232
  (`serial://COM6?baud=9600`). Over RS-232 the driver puts the source in remote
  mode at the start and back to local at the end. Use the baud rate and parity set
  in the source's setup menu. The manual names RTS/CTS among the signals in use;
  add `&rtscts=1` if the cable carries them and plain RS-232 does not work.
- **Ratings:** 0-150 V or 0-300 V, 45-500 Hz, 375 VA. Peak current limit
  2-10 A on the 150 V range and 1-10 A on the 300 V range, in 0.04 A steps
  (found on the instrument; the manual says 0-10 A).
- **Commands:** `set_voltage`, `set_frequency`, `set_range` (150, 300, auto),
  `set_current_limit` (peak), `set_voltage_limit`, `set_output`,
  `clear_protection`, `measure_voltage`, `measure_current`, `measure_power`,
  `measure_power_factor`, `measure_frequency`, `measure_crest_factor`,
  `get_faults`, plus the common SCPI commands. Settings are error-checked as for
  the 6655A.
- **Source:** Chroma 6404/6408 user's manual, version 1.6 (November 2015),
  chapter 6.
- **Tried on hardware:** a 6404 with firmware B.02.20 over RS-232, through a CH340
  USB adapter, with nothing connected to the output. The factory settings work
  as they are: `serial://COM3?baud=9600`, no handshake. Covered:
  - every setting and query;
  - the output from 5 to 30 V at 50 and 400 Hz, which reads back within 0.1 V;
  - Lua test runs, including switching off at the end and on Stop.

  With a 9.5 W 230 V LED filament bulb as the load:

  | Test | Result |
  |---|---|
  | 0 to 230 V at 50 Hz | draws nothing below about 125 V; 9.5-9.7 W at 230 V, power factor about 0.6, crest factor about 3 |
  | 45 to 500 Hz at 230 V | 9.3-9.8 W throughout; frequency exact |

  Not tried: the over-power and over-current trips, which a 9.5 W bulb cannot
  reach.
- **Current readings** have 10 mA resolution (a 70 mA load reads 0.07 A); the
  power reading is finer.
- **Switch-on:** the `peak current limit` status (`get_faults`) shows for a
  moment at most switch-ons into a rectifier load, even with the 10 A limit,
  while the output keeps running. Wait a second after switching on before
  checking faults.
- **Clamping:** the source silently clamps the voltage setting in three cases:
  - a voltage above the voltage limit;
  - lowering the limit below the set voltage;
  - switching to the 150 V range with more than 150 V set.

  In each case the clamped value stays even if the limit is raised again, so
  `set_voltage`, `set_voltage_limit` and `set_range` read the setting back and
  fail if it changed. A voltage above a fixed range is rejected outright.
- **Timing:** settings and queries take 15-60 ms, but each measurement takes
  about a second, because the source makes a new measurement for every query.
  A row with voltage, current and frequency therefore takes about 3 s.

## HP 53131A universal counter (`hp.53131a`)

- **Connection:** GPIB, as for the 6655A. The 53132A works with the same driver.
- **Commands:**
  - Measurements: `measure_frequency(channel)` (1, 2 or 3), `measure_period`,
    `measure_ratio` (channel 1 / 2), `measure_time_interval` (1 to 2),
    `measure_phase`, `measure_pulse_width`, `measure_negative_width`,
    `measure_duty_cycle`, `measure_rise_time`, `measure_fall_time`,
    `measure_max_voltage`, `measure_min_voltage`, `measure_peak_to_peak`,
    `measure_totalize(seconds)`.
  - Settings: `set_gate_time`, `set_coupling`, `set_impedance` (50 Ω or 1 MΩ),
    `set_attenuation` (x1, x10), `set_filter` (100 kHz low-pass),
    `set_trigger_level` (turns auto trigger off), `set_auto_trigger`,
    `set_trigger_slope`.
- **Gate time:** frequency, period and ratio use time gating with the gate time
  set by `set_gate_time` or the device setting (0.1 s by default). A longer gate
  gives more digits.
- **No signal:** a measurement waits for its signal, and the counter then
  ignores every other command until a device clear. If no reading arrives within
  the gate time plus 2 s, the driver sends a device clear and fails with "no
  reading on channel N".
- **Not a number:** the counter's "no valid result" (9.91E37) is returned as NaN.
- **Source:** HP 53131A/132A Programming Guide, part no. 53131-90044 (November
  1996).

## Hantek DSO5102P oscilloscope (`hantek.dso5000p`)

- **Connection:** USB, `resource = "usb://049F:505A"`, which the Add device
  dialog fills in. The scope uses its own protocol, not SCPI.
- **Once per PC (Windows):**
  1. Run `pip install "patchbay[usb]"`.
  2. Use [Zadig](https://zadig.akeo.ie/) to replace Hantek's driver with
     WinUSB for the scope. In Zadig, choose Options → List All Devices; the
     scope is listed as **"Gadget Serial v2.4"** (USB ID 049F 505A), while
     Device Manager calls it "DSO Measurement Device".

  Hantek's own PC software no longer finds the scope after that, until its
  driver is put back in Device Manager.
- **Commands:**
  - Channels: `set_channel`, `set_volts_per_div` (including the probe factor),
    `set_coupling`, `set_probe`, `set_position`, `set_bandwidth_limit`.
  - Trigger: `set_trigger(source, slope)`, `set_trigger_level` (volts),
    `get_trigger_state`, `force_trigger`.
  - Timebase and acquisition: `set_timebase` (2 ns to 40 s per division, 2-4-8
    steps), `get_timebase`, `run`, `stop`, `autoset`.
  - Data: `read_waveform(channel)` returns `{time = {...}, volts = {...}}`.
    `measure_vpp`, `measure_vrms` and `measure_mean` are worked out from that
    waveform. `measure_frequency` reads the scope's hardware counter for the
    trigger source.
- **Limits of the protocol:**
  - Only the on-screen part of the record comes over USB (3200 to 15360
    points).
  - Waveforms can only be read while the scope runs.
  - The timebase is set by pressing the time/div keys, because a settings
    write stores it without applying it.
- **Safety:** the driver uses only commands that were verified on a real
  DSO5102P. It never sends the factory/debug protocol (some of its commands can
  brick the scope or wipe its calibration), never writes the memory depth (that
  crashed the scope) and changes only the channel and trigger settings that
  were tested.
- **Tried on hardware:** a DSO5102P on Windows 10 with WinUSB, measuring its
  own 1 kHz probe-compensation output on CH1.
  - **Waveform:** 3840 points read in 0.18 s. The signal is a 0 to 5 V square
    wave, and its period from the waveform is 1.0000 ms.
  - **Measurements:** the hardware counter reads 1000.000 Hz; mean 2.49 V,
    RMS 3.51 V.
  - **Settings and keys:** volts/div, position, trigger level, timebase (by key
    presses, 0.4 to 0.7 s per change), stop and run all work. Reading a
    waveform while stopped fails as described above.
  - **Settings layout:** the copy read from the scope matches the bundled one.
- **Pacing:** the scope drops a request that follows a reply too closely. The
  driver therefore waits 80 ms after the end of each reply, not after the
  request. It also re-syncs and retries a request that goes unanswered.
- **Source:** the reverse-engineered protocol notes of
  [kooscode/hantek-dso5000p-linux](https://github.com/kooscode/hantek-dso5000p-linux)
  (MIT licence). `protocol.inf`, the layout of the settings block, is copied from
  there. At connect the driver reads the scope's own copy and falls back to this
  one.

