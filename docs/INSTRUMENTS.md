# Instrument plugins

Every plugin below ships a simulator, so a test can be written with
`resource = "sim://"` and switched to the real instrument later by changing only
the resource.

Each device type says whether it was tried on a real instrument: **Tried on
hardware** in the Add device dialog and the plugin manager, the `tested` line of
its `plugin.toml`, and `benchloom describe`. Tried: the KP184, Chroma 6404,
DT-4000ZC; partly (one model of a family, or without options): the FY6900,
DSO5102P, 53131A, 5384A, 5334B and the 34970A mainframe without cards. **Not tried
yet:** the 6655A, 3478A, 3458A, 34420A, Fluke 8508A and Wavetek 1281 and 4950.
Those are written from the manuals listed for each instrument and tested against
their simulators; expect small fixes on first contact.

Sources, loads and AC sources switch their output off when a test ends,
including when it fails or is stopped. To keep the output on, add
`leave_output_on = true` to the device entry. A script that does not react to
Stop within three seconds is left behind and its devices are shut down anyway;
only a run whose process hangs completely, and is killed, cannot do this.

## Demo power supply (`demo.psu`)

- **Simulated only:** a 20 V / 5 A supply feeding a 10 Ω load, for trying
  Benchloom without hardware. The example project uses it. Its only resource is
  `sim://`.
- **Commands:** `set_voltage` (0 to 20 V), `set_current_limit` (0 to 5 A),
  `set_output`, `measure_voltage`, `measure_current`, plus the common SCPI
  commands. The output is limited by the current limit into the 10 Ω load, and
  readings carry a little noise.
- It is also the reference example in [Writing a plugin](writing-plugins.md).

## Generic instruments (`generic.scpi`, `generic.serial`)

For an instrument that has no plugin yet.

- **Generic SCPI instrument** (`generic.scpi`): any instrument that understands
  SCPI / IEEE 488.2, over `serial://`, `tcp://`, `prologix://` or `visa://`. It
  has the common SCPI commands only: `idn`, `reset`, `clear`, `get_error`,
  `write`, `query` and `query_number`. It does not know the instrument's
  outputs, so it switches nothing off when a test ends.
- **Line-based serial device** (`generic.serial`): Arduino sketches and other
  devices that exchange lines of text, over `serial://` or `tcp://`:
  `write_line`, `read_line`, `read_number`, `query`, `query_number`.
- **Simulators:** the SCPI one answers `*IDN?` and `0` to every other query; the
  serial one sends every line back.

## Kunkin KP184 electronic load (`kunkin.kp184`)

- **Connection:** the RS-232 or RS-485 port, through a USB serial adapter.
  `resource = "serial://COM5?baud=9600"`; 9600 baud, 8N1 is the factory setting.
- **Device settings** (fields in the Add device dialog): Modbus address (set on
  the load, 1 by default); CRC high byte first (only if commands get no reply on
  newer firmware; some 2020+ firmware is reported to swap the CRC bytes); leave
  the input on after a test. The driver waits 0.5 s for a reply; another wait,
  such as `timeout = 1.0`, can be added to the device entry by hand.
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
- **Once per PC (Linux):** install Benchloom's udev rules
  (`benchloom udev-rules`, see
  [Getting started](getting-started.md#linux-serial-and-usb-access)). No other
  driver is needed.
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

## Keysight 3458A multimeter (`hp.3458a`)

- **Connection:** GPIB, as for the 6655A. The factory address is 22 (shown and
  set with the front-panel `ADDRESS` key). Through UsbGpib, turn off its
  power-up `*IDN?` as for the 3478A: the 3458A does not know `*IDN?`.
- **Command set:** HP-ML, not SCPI. At the start the driver sends a device
  clear (the meter may still be measuring and have readings waiting) and
  `END ALWAYS` (at power-on replies end without EOI, which VISA and USBTMC wait
  for), then checks `ID?`, sends `PRESET NORM` and holds the meter (`TARM HOLD`,
  `TRIG AUTO`); each measurement is one `TARM SGL`. `autocal` turns the input
  buffer on (`INBUF ON`) while it runs, so the meter does not hold the bus. When a test ends it sends `TARM AUTO`, so the meter measures
  on its own again.
- **Commands:**
    - Measurements: `measure_dc_voltage`, `measure_ac_voltage`,
      `measure_acdc_voltage`, `measure_resistance`, `measure_resistance_4wire`,
      `measure_dc_current`, `measure_ac_current`, `measure_acdc_current`,
      `measure_frequency`, `measure_period`, or `set_function` + `measure`;
      `measure_many(count)` returns a series of readings as a table.
    - Settings: `set_range(value)` picks the smallest range holding the value
      (for frequency and period, the input voltage), `set_autorange`,
      `set_nplc` (0 to 1000), `set_digits` (3 to 8), `set_autozero`,
      `set_offset_compensation`, `set_delay` (settling delay) and
      `set_auto_delay`, `set_fixed_impedance` (10 MΩ or over 10 GΩ on
      DC volts up to 10 V), `set_ac_bandwidth`, `set_ac_method` (analog, random,
      sync).
    - Other: `get_temperature` (to compare with the last autocal),
      `autocal(kind)` (all, dcv, ac, ohms; waits until done: 16 minutes for all,
      under 3 for dcv or ac, 11 for ohms), `display_text`, `display_normal`, `get_terminals`, `get_errors`, and
      `write` / `query` for raw HP-ML.
- **Device entry options:** `nplc = 10` and `digits = 8` at the start;
  `check_errors = false` to skip asking `ERR?` after each setting. `digits`
  (`NDIG`) sets only the display; the readings sent keep the resolution the
  integration time gives.
- **Ranges:** like the meter, `set_range` takes a range up to 120 % of its
  value (1.1 V is measured on the 1 V range), and up to 1050 V and 1.05 A on
  the top ranges.
- **Autocal:** let the meter warm up two hours first and disconnect the inputs.
  Afterwards the manual advises waiting before measuring: 30 minutes after
  `all` or `ohms`, 15 after `dcv` or `ac`. If autocal is secured, send it raw,
  for example `dmm:write("ACAL ALL,3458")`.
- **`set_fixed_impedance(true)`** stays on for resistance, where the 10 MΩ
  across the input makes readings wrong.
- **Slow readings:** the driver waits as long as the settings need: NPLC, line
  frequency from `LINE?`, autozero, offset compensation, AC settling and the
  settling delay, which it asks with `DELAY?` (that answers the automatic delay
  too) whenever a setting changed. The meter splits more than 10 power line
  cycles into parts of NPLC 10, and with offset compensation waits the delay
  twice in each: NPLC 100 with OCOMP and a 2 s delay takes about 48 s, as
  3458A users have found. Offset compensation on high ohm ranges needs long
  delays (1-2 s at 10 kΩ, about 5 s at 100 kΩ, by the same users); a test
  that wants speed can turn autozero off there, since 4-wire with OCOMP already
  cancels offsets. A
  Prologix adapter gives up on a read after 3 s, so longer waits are made of
  several reads. If no reading comes in time, the driver sends a device clear
  and fails with "no reading from the 3458A".
- **Overload** returns infinity (`math.huge` in Lua).
- **Source:** Keysight 3458A Benutzerhandbuch (user's guide, German), part no.
  03458-90511, edition 10, March 2023: commands, ranges, error register,
  overload value, autocal times and power-on states checked against it.
- **Uncertain:** which error bit an unknown command sets (the manual does not
  say; the simulator takes it as a syntax error), and whether `TARM AUTO` at the
  end is enough for the front panel to update without pressing `LOCAL`.

## Keysight 34420A nanovolt / micro-ohm meter (`hp.34420a`)

- **Connection:** GPIB (`prologix://COM4/22`, `usbtmc://…`, `visa://GPIB0::22::INSTR`)
  or RS-232. Over RS-232 the meter uses DTR/DSR handshake and two stop bits, fixed
  (p. 199): `serial://COM3?baud=9600&dsrdtr=1`, with the parity and data bits set on
  the meter (`&parity=E&bytesize=7` for even parity, 7 bits). The driver sets two stop
  bits itself and sends `SYST:REM` first, as the meter requires (p. 205); a reading
  that does not come is cancelled with Ctrl-C, the RS-232 device clear (p. 211).
- **At the start** the driver checks `*IDN?`, sends `*RST` (DC volts on channel 1,
  autorange, NPLC 10, filters, null and math off) and works with the SENSe commands
  and `READ?`, so each channel's and function's range, NPLC and null stay as set.
- **Commands:**
    - Measurements: `measure_voltage(channel)` (channel 1 to 120 V, channel 2 to
      12 V), `measure_ratio`, `measure_difference`, `measure_resistance`,
      `measure_resistance_4wire`, `measure_temperature`, or `set_function` +
      `measure`; `measure_many(count)` (up to 50000, in one reply).
    - Settings: `set_channel`, `set_range(value)` (the smallest range holding the
      value, to 120 % of the range), `set_channel_range(channel, volts)`,
      `set_autorange`, `set_nplc` (0.02, 0.2, 1, 2, 10, 20, 100, 200, per function),
      `set_offset_compensation`, `set_low_power`, `set_voltage_limit` (20, 100,
      500 mV or 0), `set_null`, `null_next_reading`.
    - Temperature: `set_rtd(rtd_type, r0)` (85 or 91), `set_thermistor`,
      `set_thermocouple(kind, reference, reference_c)`; these also set °C, which the
      meter keeps in non-volatile memory.
    - `display_text` (11 characters), `display_normal`, and the common SCPI commands.
- **Overload** (`9.90000000E+37`) returns infinity.
- **Source:** Keysight 34420A User's Guide, part no. 34420-90001, edition 8 (April
  2026), chapters 3 and 4.
- **Uncertain:**
    - The guide gives no reading rates (they are in a separate data sheet). The driver
      waits twice the integration time at 50 Hz, doubled again with offset
      compensation and for ratio, difference and temperature, plus 2 s.
    - The factory RS-232 setting: none / 8 bits on p. 199, even / 7 bits on p. 219.
    - Thermocouple type N is on pp. 37, 94 and 144, but not in the list on p. 166.
    - The fixed reference junction: 0 to 55 °C remotely (p. 166), -1 to 55 °C from the
      front panel (p. 94); the driver allows 0 to 55 °C.
- **Not covered:** the digital and analog filters (the guide advises against them
  remotely), statistics and scaling, chart output, calibration.

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

## HP / Agilent 53131A / 53132A universal counter (`hp.53131a`)

- **Connection:** GPIB, as for the 6655A, e.g. `prologix://COM17/1` through an
  AR488. Tried on a real 53131A (firmware 3944) with an FY6900 as the source.
- **53131A and 53132A:** both take the same commands, so one driver serves both.
  The 53132A resolves 12 digits in one second instead of 10, and time intervals
  to 150 ps instead of 500 ps. It is untested on hardware.
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
- **Trigger:** every measurement starts with `:CONFigure`, which sets the trigger
  level of both channels back to auto and the slope to rising (coupling,
  impedance, attenuation and filter stay). The driver sends the level and slope
  from `set_trigger_level` and `set_trigger_slope` again after it, for
  frequency, period, ratio, time interval, phase and totalize. Pulse width, duty
  cycle and rise/fall time use the counter's own levels (50 %, 10/90 %).
- **Auto trigger** needs a signal above 100 Hz and 100 mVpp; below 100 Hz set a
  trigger level. Totalize never auto-triggers, so set a level before it.
- **Phase:** `measure_phase` is channel 1 relative to channel 2, positive when
  channel 1 leads; one period of channel 2 is 360°.
- **Rise and fall time** need clean edges: on a fast edge that rings, the ringing
  crosses the 10 % and 90 % levels, and the result is a pulse width instead.
  The peak voltages include the overshoot too.
- **No signal:** a measurement waits for its signal, and the counter then
  ignores every other command until a device clear. If no reading arrives within
  the gate time plus 2 s, the driver sends a device clear and `*CLS` (the clear
  leaves `-230,"Data corrupt or stale"` behind) and fails with "no reading on
  channel N". The next measurement works normally.
- **Not a number:** the counter's "no valid result" (9.91E37) is returned as NaN.
- **Measured** on the 53131A from an FY6900 (1 MΩ, DC coupled): frequency from
  1 kHz to 60 MHz to within 8 ppm (the generator's clock), period, ratio 4.0,
  phase 90.02°, time interval 24.98 µs at 10 kHz, duty cycle 0.29999, pulse
  widths 300.0 µs and 700.0 µs, rise time 0.399 ms on a triangle, totalize 1000
  in 1 s, peaks of a 2 Vpp sine ±1.0 V. Channel 3 without the option fails
  with `-241,"Hardware missing"`.
- **Sources:** HP 53131A/132A Programming Guide, part no. 53131-90044 (November
  1996); Agilent 53131A/132A Operating Guide, part no. 53131-90055 (October
  2006): phase (p. 2-15), auto trigger (pp. 2-44 to 2-45, input specifications
  p. 3-2), model differences (p. xvi).

## HP 5384A / 5385A frequency counter (`hp.5384a`)

- **Connection:** HP-IB, e.g. `prologix://COM17/4` through an AR488 (the factory
  address is 3; the tested unit was set to 4), or `visa://`. Tried on a real
  5384A with an FY6900 as the source; the 5385A takes the same commands and is
  untested.
- **Reads up to the line feed:** the counter measures all the time and, while
  addressed to talk, sends one reading after another without EOI. A read up to
  EOI never ends and blocks the adapter until the signal goes away. The driver
  therefore switches a `prologix://` connection to reads that end at the line
  feed (`++read 10` with `++eoi 0`, since the AR488 waits for EOI whenever
  `++eoi` is on). Other instruments can use this with `?read_end=lf`.
- **Commands:**
    - Measurements: `measure_frequency(channel)` (`A`: 10 Hz to 100 MHz; `B`:
      50 to 225 MHz on the 5384A, 90 to 1000 MHz on the 5385A, 50 Ω),
      `measure_period` (channel A), `measure_check` (the internal 10 MHz).
    - Settings: `set_gate_time` (0.1, 1 or 10 s), `set_attenuation` (x1, x20 on
      A), `set_filter` (100 kHz low-pass on A), `set_manual_level` (the
      front-panel knob sets A's trigger level, ±0.1 V, and B's attenuation),
      `initialize`, `set_display_text` (12 characters), `clear_display_text`,
      `get_error`.
- **Connecting** sends a device clear and `IN`, so the counter starts at its
  power-on state: frequency A, x1, no filter, automatic level, and the gate time
  from the device setting.
- **Each reading** starts with `RE`, so it is never older than the call: about
  0.3 s at the 0.1 s gate, 1.5 s at 1 s, 10.3 s at 10 s.
- **No signal:** the counter sends nothing; after the gate time ±15 %, one
  period at 10 Hz and 1.5 s, the driver fails with "no reading on channel A".
  The next reading works without a clear.
- **Errors:** after each setting the driver asks `SE`. An illegal code puts the
  counter in an error state (e.g. "Er 55" on the display) in which it stops
  measuring; the driver sends a device clear and fails with the error text of
  Table 3-2. An `SE` sent right after `FU5` went unanswered, so the driver waits
  0.1 s first.
- **Measured** from an FY6900 (1 Vpp sine): channel A 10 Hz to 60 MHz, 7 ppm
  low (the generator's clock), 9.99965 Hz at 10 Hz; period 1.00000736 µs at
  1 MHz; channel B 50, 55 and 60 MHz; check 10.000000 MHz; gate 10 s
  999992.6192 Hz.
- **Source:** HP 5384A/5385A Operating and Service Manual, part no. 05384-90006
  (February 1987): command set (Table 3-9, p. 3-17), errors (Table 3-2, p. 3-3),
  status byte (Table 3-8, p. 3-16), output format (p. 3-16), specifications
  (Table 1-1, pp. 1-3 and 1-4).

## HP 5334A / 5334B universal counter (`hp.5334a`)

- **Connection:** HP-IB, e.g. `prologix://COM17/9` through an AR488 (the factory
  address is 3; the tested unit was set to 9), or `visa://`. Tried on a real
  5334B with an FY6900 as the source; the 5334A takes the same commands (plus
  math, setup memory and the DVM option, which the driver does not use) and is
  untested. Like the 5384A, the counter keeps talking without EOI, so the driver
  reads up to the line feed.
- **Commands:**
    - Measurements: `measure_frequency(channel)` (`A`, `B`: 0.001 Hz to 100 MHz;
      `C`: option 030), `measure_period`, `measure_ratio` (A/B),
      `measure_time_interval` (A to B), `measure_pulse_width(polarity)`,
      `measure_rise_time`, `measure_fall_time`, `measure_peaks(channel)` (high
      and low), `read_trigger_levels`, `measure_totalize(seconds)` or
      `start_totalize` / `read_totalize` / `stop_totalize`.
    - Settings: `set_gate_time` (0.001 to 99.999 s), `set_coupling`,
      `set_impedance`, `set_attenuation` (only with auto trigger off),
      `set_trigger_slope`, `set_filter` (100 kHz on A), `set_common` (COM A),
      `set_auto_trigger`, `set_trigger_level`, `use_trigger_knob`,
      `set_100_gate_average`, `initialize`, `get_error`.
- **Connecting** sends a device clear and `IN`: frequency A, auto trigger, 0.3 s
  gate (or the device setting).
- **Each reading** starts with `RE`, so it is never older than the call. With auto
  trigger on (the power-on state) a reading takes about 1.4 s at the 0.3 s gate,
  2.3 s at 1 s; with a fixed level about 0.4 s.
- **Auto trigger** works from 100 Hz (Table 1-1): at 10 Hz it gave no reading; with
  `set_trigger_level("A", 0)` it read 10.0000335 Hz.
- **Trigger levels:** the manual calls the remote levels (`TR1` with `AT`/`BT`)
  "5334A only", but the tested 5334B took them: `AT1.5` and `BT-0.5` read back
  as +1.50 and −0.50, and a level above the signal stopped the readings.
  `use_trigger_knob` gives the levels back to the front-panel knob.
- **Presets:** several functions set inputs themselves (Table 3-13): rise/fall time
  turns COM A and auto trigger on, pulse width and peaks turn auto trigger on,
  totalize, read levels and frequency C turn it off, and in these modes many
  settings are refused (error 1.0). The counter keeps the preset afterwards: after
  rise/fall, channel B showed channel A's signal. So the driver applies settings
  from frequency A, and on leaving such a mode puts back COM A and the chosen
  trigger mode (auto, knob or fixed levels).
- **Rise or fall** follows the slope of channel A, which cannot change in rise/fall
  mode; the driver leaves the mode to change it. A 1 kHz triangle around 0 V read
  0.409 and 0.412 ms (0.4 ms expected), a 10 kHz sine 30.8 µs (29.5 µs); a 0-2 V
  triangle (offset +1 V) gave no reading.
- **100-gate average:** the counter pauses about 85 ms between gates with auto
  trigger off and about 1 s with it on, so 100 gates of 0.01 s took 9.6 s, of
  0.1 s 18.6 s. An AR488 waits at most 32 s (a Prologix 3 s) for a reading; the
  driver says so at once when an average would take longer. Use averaging with
  auto trigger off.
- **Totalize** counts at the trigger level (auto trigger is off in this mode): set
  one first. `measure_totalize(1.0)` at 1 kHz counted 970, because the time
  between the start and stop commands is not exact.
- **Errors:** after each setting the driver asks `TE`; an illegal code (e.g.
  error 4.0, mnemonic not recognizable) stops the counter until a device clear,
  which the driver sends before failing with the text of Table 3-3.
- **Measured** from an FY6900 (1 Vpp sine, 1 MΩ): channel A 1 kHz to 60 MHz, 7 ppm
  low (the generator's clock); period 1.00000674 ms at 1 kHz; pulse width
  299.998 µs and 700.01 µs on a 30 % rectangle; peaks +1.04/−0.96 V on 2 Vpp
  (+0.26/−0.22 V at 50 Ω). Channel B 1 kHz to 60 MHz, likewise; ratio 4.0 (1 MHz
  / 250 kHz) and 10000.0 (10 MHz / 1 kHz). Time interval A to B at 10 kHz: 0.38 µs
  with both channels in phase (the generator's skew between its outputs),
  25.40 µs with channel 2 at 90° and 50.42 µs at 180°, 50.54 µs with slope B
  falling. After a rise-time measurement channel B read its own 250 kHz again.
- **Source:** HP 5334A/B Operating and Programming Manual, part no. 05334-90047
  (December 1993): command set (Table 3-12, pp. 3-82 and 3-83), output format
  (pp. 3-84 and 3-85), error handling (pp. 3-87 and 3-88), errors (Table 3-3,
  p. 3-48), status byte (Table 3-11, p. 3-77), presets (Table 3-13, pp. 3-99 to
  3-101), specifications (Table 1-1, pp. 1-2 to 1-4).

## Agilent 34970A data acquisition / switch unit (`hp.34970a`)

- **Connection:** GPIB, e.g. `prologix://COM17/1` through an AR488 (the factory
  address is 9; the tested unit was set to 1), or `usbtmc://`/`visa://`. RS-232 also
  works in principle, with a null-modem cable; select the interface on the front
  panel. The factory settings are 57600 baud, 8 data bits, no parity and XON/XOFF.
  Set the flow control to DTR/DSR there and use
  `serial://COM3?baud=57600&dsrdtr=1`. The manual advises against no flow control
  unless the baud rate is below 9600.
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

- **Measurements** (multiplexer channels, internal multimeter needed and switched
  on; `list_cards()` says "switched off" when it is not):
  `measure_dc_voltage`, `measure_ac_voltage`, `measure_resistance(channel,
  four_wire)`, `measure_dc_current`/`measure_ac_current` (34901A channels 21,
  22), `measure_frequency`, `measure_period`, `measure_temperature(channel,
  sensor, kind)` with sensor `thermocouple` (type B, E, J, K, N, R, S, T; the
  card's reference junction), `rtd`, `four_wire_rtd` (85 or 91) or
  `thermistor` (2252, 5000, 10000), in °C.
- **Measurement options** (0 keeps the instrument's default):
  - `range`: DC and AC volts 0.1, 1, 10, 100, 300 V; resistance 100 Ω to
    100 MΩ in decades; current 0.01, 0.1, 1 A; 0 is autorange (p. 21, p. 214).
  - `resolution` in the function's unit, for DC volts, DC current and
    resistance (AC is fixed at 6½ digits, p. 208). It sets the integration time
    (table on p. 203), so give it or `nplc`, not both.
  - `nplc`: 0.02, 0.2, 1, 2, 10, 20, 100 or 200 for DC volts, DC current,
    resistance and temperature (p. 218); the default is 1.
  - `aperture`: the gate time of frequency and period, 0.01, 0.1 (default) or
    1 s (p. 217).

  `CONF` puts range, resolution, NPLC and channel delay back to their defaults
  (p. 214), so the driver sends the NPLC or gate time after it every time.
- **Scanning:** `scan(function, "101:110,115", ..., range, resolution, nplc,
  aperture)` measures a channel list with one function and returns the readings
  in channel order. Each measurement is a one-sweep scan: configure, `INIT`, then
  `DATA:POIN?` until every reading is in, then `FETC?`, then `ROUT:SCAN (@)` to
  empty the scan list again (p. 240). No single read waits for a slow
  measurement, so a long scan never runs into a GPIB adapter's read timeout (at
  most 3 s on a Prologix). The time a scan may take comes from the automatic
  channel delays (p. 89) and the reading rates at 50 Hz (p. 408), doubled.
  Overloads read as ±infinity.
- **Monitor:** a measurement configures its channel and scans it again each
  time, so the relay switches for every reading. To read one channel over and
  over without that, `start_monitor(function, channel, ...)` (same options as
  `scan`) configures it, puts it in the scan list and sends `ROUT:MON (@ch)` and
  `ROUT:MON:STAT ON`; `read_monitor()` returns the latest reading
  (`ROUT:MON:DATA?`, p. 238); `stop_monitor()` ends it. Any measurement or scan
  stops a running monitor first, and so does the end of a test. Monitoring needs
  the internal multimeter and does not work on actuator, matrix or RF cards
  (p. 93). `start_monitor` waits two reading times before it returns, so the
  first `read_monitor` is not a reading from before.
- **Switching:** `close_channel`, `open_channel`, `is_closed`, `open_all(slot)`.
  The instrument's own rules still apply, and closing a relay may open others:
  - a 34901A or 34902A closes several channels only while none of its channels is
    in the scan list. The driver empties the scan list after every measurement,
    but a running monitor keeps its channel there: while it runs, closing a
    relay on that card opens the one closed before;
  - a 34908A always has only one channel closed;
  - on a 34901A, channels 21 and 22 cannot both be connected; closing one also
    affects the other (p. 164);
  - the RF cards (34905A/6A) keep one channel per bank connected and have no
    OPEN: `open_channel` and `open_all` refuse and say to close another channel
    of the bank.
- **Other:** `set_display_text` (13 characters, no quotes; a comma, period or
  semicolon shares the place of the character before it), `clear_display_text`,
  `reset_card(slot)`.
- **End of a test:** `SYST:CPON ALL` returns every card to its power-on state
  (relays open, DACs 0 V, digital ports inputs), unless the device entry says
  `leave_output_on = true`. A mainframe without cards is left alone.
- **Simulator:** `sim://` has a 34901A, a 34903A and a 34907A;
  `sim://?cards=34908A,,34904A` chooses others (an empty place is an empty slot),
  `dmm=off` switches the multimeter off and `dmm=none` leaves it out. It follows
  the relay rules above and skips the channels a range covers but a card lacks.
- **Source:** Agilent 34970A User's Guide (chapter 5, Remote Interface Reference;
  chapter 4, Module Overview, pages 163–177).
- **Not in the manual:** the reply of `SYST:CTYP?` for an empty slot (taken from
  the tested unit); what `SENS:DIG:DATA:BYTE?` reads on a port written as output
  (the simulator returns the last byte written); which error number a refused
  `ROUT:OPEN` on an RF card gives (the simulator uses +221, also for the monitor
  errors). Whether the monitored channel's relay stays closed between readings:
  the manual does not say, but a tester saw it on a real unit. How soon after
  `ROUT:MON:STAT ON` the first reading of the channel is there: the manual gives
  no figure, so `start_monitor` waits two reading times (one may already be under
  way); the reading times are the manual's, the factor two is the driver's.
- **Tried on hardware:** a 34970A (firmware 9-1-2) with the internal multimeter
  and **no cards**, through an AR488 on a Pro Micro, Windows 10. Connecting (0.2 s),
  finding three empty slots and the multimeter, the reading format and trigger
  settings, display text, the error queue, and the refusal of every channel
  command on empty slots all worked; `INIT` without a scan list gave the same
  error (+113) as the simulator. The measurement options and the monitor came
  from a tester's feedback on a real unit, who also saw the monitored relay stay
  closed; apart from that, everything that needs a card is tested against the
  simulator only.

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
- **Once per PC (Linux):** install Benchloom's udev rules
  (`benchloom udev-rules`, see
  [Getting started](getting-started.md#linux-serial-and-usb-access)). No other
  driver is needed.
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
  to call `set_uplink(false)`. With uplink off, a higher phase delays the
  channel: channel 2 lags channel 1 by the difference of the two phases. With
  channel 2 at 90° and 10 kHz, its edges came 25 µs after channel 1's on an HP
  53131A (24.98 µs) and an HP 5334B (25.40 µs). (Earlier versions of this page
  said "leads", from a misread oscilloscope check.)
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

## Fluke 8508A reference multimeter (`fluke.8508a`)

- **Connection:** GPIB, as for the 6655A; the address is set in the Utility menu.
- **At the start** the driver sends a device clear, checks `*IDN?`, sends `*RST` and
  selects external triggering (`TRG_SRCE EXT`): the manual advises against internal
  triggers under bus control (p. 4-33). Each reading is `X?` (trigger and read).
- **Commands:**
    - Measurements: `measure_dc_voltage`, `measure_ac_voltage` (and `get_frequency`),
      `measure_resistance`, `measure_resistance_4wire`, `measure_true_ohms` (4-wire,
      current reversed, 2 Ω to 20 kΩ), `measure_hv_ohms` (20 MΩ to 2 GΩ, no autorange),
      `measure_dc_current`, `measure_ac_current`, `measure_temperature(identity)` with
      a PRT stored in the meter, `measure`, `measure_many`.
    - Settings, each for the function in use: `set_range` (the smallest range whose
      full scale holds the value: 199.99 mV, 1.9999 V … 1050 V), `set_autorange`,
      `set_resolution` (5 to 8 digits; DC current 5 to 7, AC 5 or 6), `set_fast`,
      `set_filter`, `set_ac_filter` (1, 10, 40, 100 Hz), `set_dc_coupling`,
      `set_transfer`, `set_four_wire`, `set_low_current`.
    - `set_input` (front, rear, ratio, difference, deviation, off), `set_guard`,
      `set_delay`, `set_auto_delay`, `set_fast_gate`, `input_zero`, `self_test`.
- **Waiting:** the default settling delay of the manual's tables (p. 4-35, up to 50 s)
  or the delay set, plus one reading at the read rate of p. 5-14 (down to 1/90
  reading per second), doubled for front-and-rear inputs and for autorange, plus 20 %
  and 2 s.
- **Errors:** after each setting the driver reads `*ESR?`, and on an error the
  execution and device error queues (`EXQ?`, `DDQ?`), with the texts of appendix A.
- **Overload** (`±200.0000E+33`) returns infinity.
- **Source:** Fluke 8508A Reference Multimeter Users Manual, PN 1673798 (July 2002,
  Rev. 6, 3/13): chapter 4, chapter 5 (ranges, read rates), appendix A.
- **Uncertain:**
    - The delay tables name their ranges in decades (1 Ω … 1 GΩ), chapter 5 in 2-and-20
      steps (2 Ω … 2 GΩ); the driver uses the longest delay of a function.
    - High Voltage Ohms has no read rate of its own; the slowest one (PRT / True Ohms)
      is used. The ACI delays are given for 5 digits only; 6 digits uses those of ACV.
    - The manufacturer field of `*IDN?` is not given; the driver checks the model only.
- **Not covered:** math (averaging, M, C, Z, dB), limits, the block memory, entering
  PRT data, spot frequencies, calibration.

## Wavetek 1281 selfcal multimeter (`wavetek.1281`)

- **Connection:** GPIB, as for the 6655A.
- **At the start** the driver sends a device clear, checks `*IDN?`, reads the fitted
  options with `*OPT?` (AC option 10, resistance option 20, current option 30) and the
  line frequency (`LINEF?`), sends `*RST` and selects external triggering. A function
  whose option is missing is refused before anything is sent. Each reading is `X?`.
- **Commands:** as for the 8508A, with the 1281's own functions and limits:
  `measure_hi_ohms` (100 MΩ and 1 GΩ, no autorange) instead of High Voltage Ohms;
  True Ohms on 10 Ω to 100 kΩ; resolutions DC volts, ohms and True Ohms 5 to 8, Hi
  Ohms 5 or 6, AC volts and DC current 4 to 6, AC current 4 or 5; `set_input`
  (front, rear channels A and B, ratio A/B, difference, deviation, off), `set_guard`
  (local, remote), `input_zero`, `confidence_test`, `self_test`. Ranges follow the
  handbook's tables (0.2 to 1.99999999 selects the 1 V range, and so on, pp. 5-30 to
  5-44), so each range reads to twice its value.
- **Waiting:** the default delay of p. 5-71 (or the delay set), plus twice the A-D
  integration of p. 5-71 (3.3 ms up to 1024 power line cycles, about 20 s at 50 Hz for
  8½ digits), doubled for True Ohms, two channels and autorange, plus 20 % and 2 s.
- **Overload** (`±200.0000E+33`) returns infinity.
- **Source:** Wavetek Model 1281 Selfcal Digital Multimeter User's Handbook, Section 5
  and appendix A to Section 4.
- **Uncertain** (the handbook disagrees with itself; the driver uses only what all of
  it agrees on):
    - Resolutions: the diagrams of DC volts, ohms and True Ohms also show 4½ digits,
      the index does not; Hi Ohms is 4 to 6 in its diagram and the index, 5 to 6 in its
      text.
    - AC filters: 1000, 360, 40 and 10 Hz in the index; 100, 40, 10 and 1 Hz in the
      diagrams and the power-on state, which the driver uses.
    - Hi Ohms: the table says 20 to 199.9999 MΩ selects 100 MΩ, the example says
      100000000 selects 1 GΩ; the driver sends 50000000 for the 100 MΩ range.
    - Ohms: the power-on range is 10 MΩ, but the range table ends at 1 MΩ ("> 2000000
      use HI_OHMS"); the driver starts ohms in autorange and gives no fixed range for
      2 to 20 MΩ.
    - `*IDN?`: the example answers `DATRON INSTRUMENTS,1271,…`; the driver accepts a
      model of 1281 or 1271.
    - Appendix A writes `*EXQ?` and `*DDQ?`, Section 5 `EXQ?` and `DDQ?`; the driver
      uses Section 5's.
    - No read rates are given; the waits come from the A-D modes, doubled.
- **Not covered:** math, limits, the block memory, specification readouts,
  calibration.

## Wavetek 4950 multifunction transfer standard (`wavetek.4950`)

- **Connection:** GPIB, as for the 6655A.
- **A transfer standard measures at nominal points.** Each measurement names its
  range and band: `mts:dc_voltage(10)` measures the 10 V point (10 V ±10 %),
  `mts:dc_voltage(10, "zero")` 0 V ±1 V, `mts:dc_voltage(10, "high")` 19 V. A reading
  outside its band is refused with the reason (above or below the band, frequency out
  of its band), unless `set_band_limits(false)`.
- **At the start** the driver sends a device clear, checks `*IDN?` (model 4950), reads
  the fitted assemblies with `*OPT?` (AC, ohms, current), sends `*RST`, external
  triggering and the accuracy mode of the device entry (`accuracy = "high"` by
  default, as the handbook disagrees on the power-on mode). A reading is `*TRG`, then
  `RDG?`, which waits for the mean of the sample.
- **Commands:** `dc_voltage(volts, band)`, `ac_voltage(volts, frequency, band,
  four_wire)` (frequency band centres 10 Hz to 1 MHz, ±10 %), `resistance(ohms, band,
  four_wire)`, `dc_current(amps, band)`, `ac_current(amps, frequency)`, each with an
  optional remote guard; `measure`, `measure_many`; `get_frequency`, `get_quality`
  (standard error of the mean, ppm of range or reading, or absolute),
  `get_sample_size`, `get_warm_up_minutes`; `set_accuracy`, `set_corrections`
  (certified or base), `set_band_limits`, `input_zero`, `self_test`,
  `resume_self_test`, `get_errors`.
- **Waiting:** each conversion integrates for 200 ms (Section 1); a sample has up to
  128 (table of p. 5-39). The driver allows three times the integration per
  conversion plus 5 s, and 10 s more for AC.
- **Errors:** the command, execution and device error queues answer with a code and
  the meter's own text; the driver passes both on.
- **Source:** Wavetek Model 4950 Multifunction Transfer Standard Instrument User's
  Handbook, 850270 Issue 4 (December 1998), Section 5.
- **Uncertain:**
    - Which range takes which frequency band: the table on pp. 5-30 and 5-36 could not
      be read reliably from the PDF, so the driver leaves that check to the meter
      (error 1026).
    - The time between conversions and the AC settling time are not given.
    - The power-on accuracy mode (low on p. 5-39, high in appendix B) and some power-on
      ranges and bands differ within the handbook; the driver always sends them.
- **Not covered:** calibration, entering the 10 A shunt and 2-wire lead serial numbers
  (needed for the 10 A range and 2-wire ohms; use `write`), `TEMP?`, `DATE?`, `TIME?`.
