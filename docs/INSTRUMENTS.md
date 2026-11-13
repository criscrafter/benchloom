# Instrument plugins

Every plugin below ships a simulator, so a test can be written with
`resource = "sim://"` and switched to the real instrument later by changing only
the resource.

The KP184, Chroma 6404, Hantek DSO5102P and Feeltech FY6900 drivers have been
tried on real instruments (see below). **The HP drivers have not been run against real
hardware yet**, apart from the 34970A mainframe without cards. They are written from the manuals or protocol notes listed for each
instrument and are tested against their simulators. Expect small fixes on first
contact.

Sources, loads and AC sources switch their output off when a test ends,
including when it fails or is stopped. To keep the output on, add
`leave_output_on = true` to the device entry. A script that does not react to
Stop within three seconds is left behind and its devices are shut down anyway;
only a run whose process hangs completely, and is killed, cannot do this.

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
      front panel. In Benchloom, add one device per load with the same port and
      baud rate and that load's address. Benchloom opens the port once and the
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
  last closed with. Benchloom works around it, so no setting is needed.

## GPIB through the xyphro UsbGpib adapter

[UsbGpib](https://github.com/xyphro/UsbGpib) turns one GPIB instrument into a
USB instrument (USBTMC). Benchloom can talk to it without a vendor VISA.

- **Connection:** "USB instrument or UsbGpib adapter" in the Add device dialog,
  or `resource = "usbtmc://03EB:2065"`. That is the first adapter found; add
  `/<serial>` to pick one. `usbgpib://` is the same, shorter. The adapter only
  appears on USB while a powered instrument is connected to it.
- **Once per PC (Windows):**
  1. Run `pip install "benchloom[usb]"` (pyusb, python-usbtmc and the libusb
     library).
  2. With the instrument switched on, use [Zadig](https://zadig.akeo.ie/) to
     install the WinUSB driver for the adapter (USB ID 03EB 2065). Zadig is a
     free download and needs no installation.
- **Alternative:** with a VISA installed (the adapter's author recommends R&S
  VISA), use `visa://USB0::0x03EB::0x2065::<serial>::INSTR` instead.
- **Instruments without `*IDN?`** (the HP 3478A): the adapter asks every
  instrument `*IDN?` at power-up. Turn that off once with
  `benchloom usbgpib "!autoid off"`. `benchloom usbgpib` with no command lists
  the adapters.
- **One instrument per adapter.** The adapter can address several GPIB
  instruments, but Benchloom does not switch between them yet.
- **Not tried on hardware.** The transport is tested against a stand-in for the
  USBTMC library only.

## HP 6655A power supply (`hp.6655a`)

- **Connection:** GPIB: `usbtmc://03EB:2065` (UsbGpib, above),
  `prologix://COM4/5` (Prologix or AR488 adapter, GPIB address 5) or
  `visa://GPIB0::5::INSTR`. With `prologix://`, Benchloom waits for the adapter
  to answer `++ver` (an Arduino Nano restarts when the port opens) and sets its
  read timeout (`++read_tmo_ms`) from the connection's `timeout`: the adapter
  keeps whatever the last program set, and a too short one makes slow
  measurements time out. A Prologix allows up to 3 s, an AR488 up to 32 s.
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

  Over GPIB (`prologix://COM17/11`, an AR488 on a Pro Micro, output off and
  nothing set): identification, the output state and faults, every settings
  query, all six measurements (about 0.9 s each) and 20 queries in a row (0.1 s)
  worked, with no errors left. No `SYST:REM` is needed over GPIB.
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

## Agilent 34970A data acquisition / switch unit (`hp.34970a`)

- **Connection:** GPIB, e.g. `prologix://COM17/1` through an AR488 (the factory
  address is 9; the tested unit was set to 1), or `usbtmc://`/`visa://`. RS-232 also
  works in principle (`serial://COM3?baud=9600`, flow control None, a null-modem
  cable); select the interface on the front panel.
- **Cards:** the driver asks the three slots which cards they hold when it
  connects (`list_cards()`, `card_model(slot)`) and checks every channel against
  them before sending anything, so a wrong channel fails with a message naming
  the card. Channels are the slot plus the channel on the card: 101 to 122 for a
  34901A in slot 1.

  | Card | Channels | What the driver does with them |
  |---|---|---|
  | 34901A 20-channel multiplexer | 01–20, current 21–22 | measure, 4-wire (n with n+10), current, switch |
  | 34902A 16-channel reed multiplexer | 01–16 | measure, 4-wire (n with n+8), switch |
  | 34908A 40-channel single-ended multiplexer | 01–40 | measure (no 4-wire, no current), switch |
  | 34903A 20-channel actuator | 01–20 | switch |
  | 34904A 4x8 matrix | row + column, 11–48 | switch |
  | 34905A/34906A dual RF multiplexer | 11–14, 21–24 | switch |
  | 34907A multifunction module | digital 01–02, totalizer 03, DAC 04–05 | `write_digital`, `read_digital`, `read_totalizer`, `set_dac` (±12 V) |

- **Measurements** (multiplexer channels, internal multimeter needed):
  `measure_dc_voltage`, `measure_ac_voltage`, `measure_resistance(channel,
  four_wire)`, `measure_dc_current`/`measure_ac_current` (34901A channels 21,
  22), `measure_frequency`, `measure_period`, `measure_temperature(channel,
  sensor, kind)` with sensor `thermocouple` (type B, E, J, K, N, R, S, T; the
  card's reference junction), `rtd`, `four_wire_rtd` (85 or 91) or
  `thermistor` (2252, 5000, 10000), in °C.
- **Scanning:** `scan(function, "101:110,115")` measures a channel list with one
  function and returns the readings in channel order. Each measurement is a
  one-sweep scan: configure, `INIT`, then `DATA:POIN?` until every reading is in,
  then `FETC?`. No single read waits for a slow measurement, so a long scan
  never runs into a GPIB adapter's read timeout (at most 3 s on a Prologix).
  Overloads read as ±infinity.
- **Switching:** `close_channel`, `open_channel`, `is_closed`, `open_all(slot)`.
  The instrument's own rules still apply: a multiplexer closes several channels
  only while none is in the scan list; a 34908A closes one at a time.
- **Other:** `set_display_text` (13 characters, no quotes), `clear_display_text`,
  `reset_card(slot)`.
- **End of a test:** `SYST:CPON ALL` returns every card to its power-on state
  (relays open, DACs 0 V, digital ports inputs), unless the device entry says
  `leave_output_on = true`. A mainframe without cards is left alone.
- **Simulator:** `sim://` has a 34901A, a 34903A and a 34907A;
  `sim://?cards=34908A,,34904A` chooses others (an empty place is an empty slot).
- **Source:** Agilent 34970A User's Guide (chapter 5, Remote Interface Reference;
  chapter 9, the modules).
- **Tried on hardware:** a 34970A (firmware 9-1-2) with the internal multimeter
  and **no cards**, through an AR488 on a Pro Micro, Windows 10. Connecting (0.2 s),
  finding three empty slots and the multimeter, the reading format and trigger
  settings, display text, the error queue, and the refusal of every channel
  command on empty slots all worked; `INIT` without a scan list gave the same
  error (+113) as the simulator. Everything that needs a card is tested against
  the simulator only.

## Hantek DSO5102P oscilloscope (`hantek.dso5000p`)

- **Connection:** USB, `resource = "usb://049F:505A"`, which the Add device
  dialog fills in. The scope uses its own protocol, not SCPI.
- **Once per PC (Windows):**
  1. Run `pip install "benchloom[usb]"`.
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
    - Data: `read_waveform(channel)` returns `{time = {...}, volts = {...}}`, and
      `capture()` returns that for every channel that is on, as
      `{CH1 = ..., CH2 = ...}`, ready for `save_waveform`.
      `measure_vpp`, `measure_vrms` and `measure_mean` are worked out from that
      waveform. `measure_frequency` reads the scope's hardware counter for the
      trigger source. The counter takes 2 to 3 seconds to show a new frequency,
      so wait that long after changing the signal.
    - Frequency response: `measure_response(hertz, input, output, periods)`
      (input CH1 and output CH2 by default) returns `{gain_db, gain, phase_deg,
      input_vpp, output_vpp, input, output}` for a sine of that frequency going
      through a circuit; `input` and `output` are the waveforms it measured. It sets the timebase to show about four periods, switches both
      channels on, triggers on the input, sets each channel's volts/div so the
      signal fills about six divisions, and fits a sine of the known frequency to
      both waveforms (least squares, so noise matters little). `phase_deg` is the
      output relative to the input, between -180 and 180°. The Bode plot project
      template uses it.
    - The volts/div ranges differ by up to 2 % (0.5 V/div read 0.18 dB below
      1 V/div), while the two channels on the same range agreed within 0.1 dB.
      So `measure_response` puts the output on the input's range as long as it
      still spans two divisions there, and gains near 0 dB compare like with
      like.
    - Settings writes are what take the time: the scope ignores requests for
      about 0.65 s after one. The driver writes all changes of a step at once,
      skips writes that change nothing, and asks again every 0.1 s until the
      scope answers (each unanswered request used to wait out the 1 s timeout).
      A Bode point takes about 1.2 s, or 2.2 s when a range changes.
- **Limits of the protocol:**
    - Only the on-screen part of the record comes over USB (3200 to 15360
      points).
    - Waveforms can only be read while the scope runs.
    - From about 8 ms/div up, a waveform is there only for a moment between
      acquisitions; asked at the wrong time, the scope answers that it has none.
      Asking again straight away got one usually within a second, but sometimes
      only after 3 to 8 s, at random, so `read_waveform` keeps asking for up to
      10 s. With a pause of 0.25 s between requests, 20 and 40 ms/div often got
      nothing at all.
    - From 80 ms/div on the scope scrolls (scan mode). The two channels are then
      read from different moments, so `measure_response` goes no slower than
      40 ms/div and refuses frequencies below about 1.6 Hz.
    - The timebase is set by pressing the time/div keys, because a settings
      write stores it without applying it.
- **Safety:** the driver uses only commands that were verified on a real
  DSO5102P. It never sends the factory/debug protocol (some of its commands can
  brick the scope or wipe its calibration), never writes the memory depth (that
  crashed the scope) and changes only the channel and trigger settings that
  were tested.
- **Frequency response, tried on hardware:** an FY6900 into 3.3 kΩ and a
  ceramic capacitor, generator to CH1, capacitor to CH2. The Bode template
  swept 5 Hz to 100 kHz at four points per decade in 75 s (126 s before the
  writes were sped up), with the same results:
  -0.06 dB at 5 Hz, -56.3 dB at 100 kHz (-57.0 dB expected for a single pole),
  the phase from -2° to -89°, and the -3 dB point at 140.5 Hz, where the phase
  gives 142 Hz (about 340 nF). A point takes 1 to 6 s, more below 50 Hz.
- **Simulator:** shows a 1 kHz sine on CH1. With
  `sim://?dut=rc_lowpass&cutoff_hz=150` it is wired like a filter test instead:
  CH1 shows channel 1 of a simulated FY6900 in the same run and CH2 shows it
  after an RC low-pass with that cutoff; `dut=rc_highpass` puts a high-pass
  there.
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

## Digitek DT-4000ZC multimeter (`fs9721.dt4000zc`)

- **Connection:** the meter's optical RS-232 cable, `resource =
  "serial://COM13?baud=2400"`. The meter sends 2400 baud, 8N1; the driver sets
  this itself. The receiver in the cable is powered from the serial port with
  DTR high and RTS low, which the driver also sets. Press the meter's **RS232**
  button so it sends (an "RS232" symbol shows on its display).
- **No commands:** the meter only sends a copy of its display, about twice a
  second. Choose the function on its dial; the driver reads what is shown.
- **Commands:**
    - `read()`: the display as a number in the base unit (V, A, Ω, F, Hz, % or
      °C), with the prefix applied (12.34 kΩ gives 12340). OL gives ±infinity.
    - `read_display()`: the display as text, e.g. `5.003 V DC` or
      `0L MΩ`; `read_mode()`: `DC V`, `AC V`, `DC A`, `AC A`, `Ω`, `F`,
      `Hz`, `%`, `°C`, `diode` or `continuity`.
    - `measure_voltage`, `measure_current`, `measure_resistance`,
      `measure_capacitance`, `measure_frequency`, `measure_duty_cycle`,
      `measure_temperature`: like `read()` with a unit, but fail when the dial
      is set to something else.
- **Garbled data:** if bytes arrive but none forms a valid packet, reading
  fails with "garbled" instead of returning a wrong number.
- **Cable fix for USB serial adapters:** the cable's phototransistor drives the
  line up quickly, but only the adapter's input (a few kΩ) pulls it back down,
  so the line falls too slowly: every 1 bit right after a 0 arrives as 0. A
  higher DTR voltage does not help. A **3.3 kΩ resistor from the data line
  (pin 2 at the PC side) to RTS (pin 7)**, which the driver holds at about
  −5 V, pulls it down fast enough. A PC's own serial port may not need it.
- **Other meters:** the plugin decodes the Fortune Semiconductor FS9721 display
  chip, used in many cheap meters (Tenma, Uni-T, Voltcraft and others). Those
  can be added as further device entries once their bytes are checked.
- **Temperature:** the °C symbol is in byte E, whose symbols differ between
  meters; bit 2 is the DT-4000ZC's.
- **Source:** the FS9721_LP3 layout as documented by sigrok.
- **Byte E:** besides °C, the DT-4000ZC sets its top bit (`e8`) in every
  function except temperature, in auto and manual ranges alike. What it means
  is not known; it is ignored.
- **REL** shows `-00.00` at zero; `read()` gives 0.0 for it, not -0.0.
- **Tried on hardware:** a DT-4000ZC with its cable on a CH340 USB-RS-232
  adapter (DTR about 5.2 V), on Windows 10, with the 3.3 kΩ resistor above.
  Read correctly: DC volts on a 10 V reference (9.97 V, as on the meter's
  display) and on the mV range, AC volts, DC current (A and mA), ohms in auto
  and manual ranges (including OL), continuity, diode, capacitance (nF),
  frequency, duty cycle, temperature, HOLD and REL; `measure_resistance`
  refused while the dial was on volts. AC current was not tried. Without the
  resistor every packet was garbled.

## Feeltech FY6900 function generator (`feeltech.fy6900`)

- **Connection:** the generator's USB port is a serial port:
  `resource = "serial://COM12"`. Its settings are fixed at 115200 baud, 8N2; the
  driver sets them itself, and the Add device dialog fills in 115200.
- **Signal** (channel is 1 or 2):
    - `set_signal(channel, waveform, hertz, vpp, offset)` sets all four at once.
    - `set_waveform`, `set_frequency`, `set_amplitude` (Vpp, up to 24 V),
      `set_offset` (±12 V), `set_duty_cycle`, `set_phase`, `set_output`, each with a
      `get_` command, and `idn`.
    - `set_pulse_width(ns)` for `adjustable_pulse` (channel 1; the pulse goes from
      0 V to the amplitude), `set_trapezoid_edge(channel, setting)` for
      `trapezoid` (1000 gives a 10-90 % rise of about 1.65 µs).
    - Waveforms: `sine`, `square`, `rectangle`, `trapezoid`, `cmos`,
      `adjustable_pulse` (channel 1 only), `dc`, `triangle`, `ramp`,
      `negative_ramp`, the stair, exponential, logarithmic and rectified waves,
      `lorentz`, `multitone`, `noise`, `ecg`, `trapezoid_pulse`, `sinc`, `impulse`,
      `gaussian_noise`, `am`, `fm`, `chirp`, and `arb1` to `arb64` (the stored
      arbitrary waveforms).
- **Modulation** of channel 1, by channel 2 or by a signal on EXT IN:
    - `start_modulation(kind, source)` with kind `am`, `fm`, `pm`, `fsk`, `ask`,
      `psk` or `burst` and source `ch2` (default), `external_ac` or `external_dc`.
      `stop_modulation()` or `set_output(1, …)` ends it.
    - `set_am_depth(percent)` (up to 120 %), `set_fm_deviation(hertz)` (the total
      swing: 500 Hz around 1 kHz goes from 750 to 1250 Hz), `set_pm_deviation(degrees)`,
      `set_fsk_frequency(hertz)` (the second frequency), `set_burst_count(cycles)`
      (a burst per rising edge of the source).
- **Sweep** of channel 1:
    - `set_sweep(what, start, end, seconds, direction, logarithmic)` with what
      `frequency` (whole Hz), `amplitude`, `offset` or `duty_cycle`, direction `up`,
      `down` or `up_down`; then `start_sweep()` and `stop_sweep()`.
    - The generator cannot read sweep settings back, so they are not checked.
- **Other:**
    - `set_sync(what, on)`: channel 2 follows channel 1's `waveform`, `frequency`,
      `amplitude`, `offset` or `duty_cycle`.
    - `set_uplink(on)` (linking several generators), `set_buzzer(on)`.
    - `save_settings(slot)` / `load_settings(slot)`, slots 1 to 20. Slot 1 is
      loaded at power-on.
    - Counter on EXT IN: `measure_frequency`, `measure_period`,
      `measure_high_time`, `measure_low_time`, `measure_duty_cycle`,
      `measure_count`, `reset_counter`, `set_counter_gate(1, 10 or 100 s)`,
      `set_counter_coupling(dc or ac)`.
    - The counter shows a new signal about two gate times after it changes. At
      a 1 s gate the frequency is in whole Hz, so for slow signals use
      `1 / measure_period()` (12.345 Hz read as 81004456 ns). DC coupling
      switches at a positive level, which suits logic signals; a 2 Vpp sine
      around 0 V was not counted. With AC coupling only the frequency was read.
      The pulse count starts 1 to 2 s after `reset_counter`; reading the
      frequency in between once stopped it. After a slow signal, a new one can
      take several seconds to show.
- **Checks:** the generator clamps out-of-range values without an error, so the
  driver reads every setting back and fails with what was set instead (for
  example a too high amplitude at a high frequency). The device setting "Read
  every setting back" switches this off. The offset DAC has 12 bits (4.095 V
  becomes 4.094 V), so the offset check allows 1 mV.
- **Frequencies it cannot produce:** the firmware loses 65536 µHz when the part
  of the frequency below 0.1 Hz is 0.065536 Hz or more (1000.567 Hz comes out as
  1000.501 Hz; measured on the output, not only in the readback). Those
  frequencies, about a third of all with three or more decimals, cannot be set.
  The driver sets the nearest one that can, at most 17 mHz away:
  `set_frequency(1, 1000.567)` gives 1000.565535 Hz. The FSK frequency has the
  same fault and gets the same treatment.
- **Phase needs uplink off.** With uplink on (it was on, on the tested unit) the
  generator stores the phase but ignores it, so `set_phase` fails with a hint
  to call `set_uplink(false)`. With uplink off, channel 2 leads channel 1 by the
  difference of the two phases.
- **Duty cycle** applies to `rectangle` and the pulse waveforms; `square` is
  always 50 %.
- **Not offered:**
    - Uploading arbitrary waveforms: the generator acknowledges the upload
      (`DDS_WAVEnn`, then "W" and "HN") but the slot keeps playing what it had,
      with either byte order, in one go or in chunks.
    - A manual trigger for bursts: no remote command for it is known.
- **Output safety:** a running sweep is stopped and both outputs are switched
  off when a test ends, unless the device entry says `leave_output_on = true`.
- **FY6800:** supported from fygen's notes (frequency in µHz, no rectangle or
  trapezoid), not tried on hardware. Other Feeltech models are refused at
  connect.
- **Tried on hardware:** an FY6900-60M with firmware V1.8 on Windows 10, its two
  outputs on the two inputs of a Hantek DSO5102P:
    - 4 Vpp read as 4.12 Vpp, and an offset of +1 V moved the mean by +1.02 V
      (the scope reads about 0.1 V high).
    - Sine, square, rectangle, triangle, ramp, negative ramp and DC look right
      on both channels, which confirms that channel 2 numbers its waveforms one
      lower from DC on.
    - Rectangle at 25 % and 75 % duty measured 25.0 % and 75.0 %; 2000 Hz
      measured 2000.0 Hz.
    - With uplink off, phases of 0, 90 and 180° on channel 2 measured 4, 86 and
      183°.
    - AM, FM, PM, FSK, ASK, PSK and burst, each modulated by channel 2, looked as
      set (for example a burst of 4 cycles, FSK between 1 and 3 kHz, PM of 90° at
      10 Hz as ±16 Hz).
    - Sweeps of frequency (linear and log, up and down), amplitude, offset and
      duty cycle ran as set and stopped cleanly.
    - Sync, pulse width and trapezoid edge did what they say.
    - A Lua project stepped the frequency while the scope measured it, and the
      outputs were off afterwards.
    - The counter, with channel 2 wired to EXT IN, read 777, 98765, 1234567,
      20000001 and 60000003 Hz for those frequencies, and the period, high time
      and duty cycle of a 25 % rectangle exactly at 1 kHz.
    - Not tried: the external modulation sources, and saving or loading memory
      slots (to keep the generator's stored settings).
- **Protocol quirks found on the way** (for whoever extends the driver):
    - Reads are answered with the value and an extra empty line; unknown
      commands get no answer at all.
    - The documented sweep start (`SBE1`) swept the wrong range and `SBE0` only
      paused, so the driver uses the front panel's run key (`KEY17`).
    - A log sweep ignores the output write that ends a linear sweep until it is
      paused, so the driver pauses first.
    - Sweep offsets are sent counted from -12 V.
    - Counter coupling: `WCC0` is DC and `WCC1` AC, as in the FY6600 document
      (fygen has them swapped). `WCP0`, documented as "pause", answers with the
      pulse count.
- **Sources:** the official FY6600 serial protocol (rev 1.5),
  [mattwach/fygen](https://github.com/mattwach/fygen) (waveform numbers from its
  `wavedef.py`), [katzenjens/fy6900commander](https://github.com/katzenjens/fy6900commander),
  [tspspi/pyfy6900](https://github.com/tspspi/pyfy6900), an EEVblog post on the
  FY6600/FY6800 serial interface, and an earlier test program by the author, all checked on the real
  generator. No code was copied from them.
