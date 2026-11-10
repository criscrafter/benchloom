# Instrument plugins

Every plugin below ships a simulator, so a test can be written with
`resource = "sim://"` and switched to the real instrument later by changing only
the resource.

The KP184 driver has been tried on a real load (see below). **The other drivers
have not been run against real hardware yet.** They are written from the manuals
listed for each instrument and are tested against their simulators. Expect small
fixes on first contact.

Sources, loads and AC sources switch their output off when a test ends,
including when it fails or is stopped. To keep the output on, add
`leave_output_on = true` to the device entry. A forced stop (a script that does
not react to Stop within three seconds) cannot do this.

## Kunkin KP184 electronic load (`kunkin.kp184`)

- **Connection:** the RS-232 or RS-485 port, through a USB serial adapter.
  `resource = "serial://COM5?baud=9600"`; 9600 baud, 8N1 is the factory setting.
- **Device entry options:** `address = 1` (Modbus address set on the load),
  `crc_high_byte_first = true` (only if commands get no reply on newer firmware;
  some 2020+ firmware is reported to swap the CRC bytes), `timeout = 0.5`.
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

## HP 6655A power supply (`hp.6655a`)

- **Connection:** GPIB. `prologix://COM4/5` (Prologix or AR488 adapter, GPIB
  address 5) or `visa://GPIB0::5::INSTR`.
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
- **Ratings:** 0-150 V or 0-300 V, 45-500 Hz, peak current limit 0-10 A, 375 VA.
- **Commands:** `set_voltage`, `set_frequency`, `set_range` (150, 300, auto),
  `set_current_limit` (peak), `set_voltage_limit`, `set_output`,
  `clear_protection`, `measure_voltage`, `measure_current`, `measure_power`,
  `measure_power_factor`, `measure_frequency`, `measure_crest_factor`,
  `get_faults`, plus the common SCPI commands. Settings are error-checked as for
  the 6655A.
- **Source:** Chroma 6404/6408 user's manual, version 1.6 (November 2015),
  chapter 6.
