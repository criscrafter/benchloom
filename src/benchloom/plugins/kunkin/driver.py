# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Kunkin KP184 electronic load.

The KP184 speaks a Modbus-RTU variant: every register holds a 32-bit value.
Reading sends function 0x03 with a count of 4 (bytes) and gets 4 data bytes back;
writing sends function 0x06 followed by count 1, byte count 4 and 4 data bytes,
and the load acknowledges with 8 bytes: address, 0x06, the register, the low 16
bits of the value and the CRC (checked on a real KP184).

Bit 28 of the status registers (input, mode) is set once the load has received a
command; the driver masks it out.

The register map follows the community documentation of the protocol (see the
tuxmike/KP184 and markmerz/kunkin-kp184-py projects and the EEVblog thread
"Serial to usb on Kunkin KP184 electronic load"). Registers reported there as not
working (dynamic test, compare, half-current) are left out.
"""

from __future__ import annotations

import time

from benchloom import CommandError, Device, Param, command
from benchloom.modbus import crc_ok, with_crc

REG_LOAD_ON = 0x010E
REG_MODE = 0x0110
REG_CV = 0x0112  # mV
REG_CC = 0x0116  # mA
REG_CR = 0x011A  # 0.1 ohm
REG_CW = 0x011E  # 0.01 W
REG_VOLTAGE = 0x0122  # mV
REG_CURRENT = 0x0126  # mA

MODES = ("CV", "CC", "CR", "CW")


class KP184(Device):
    def connect(self) -> None:
        self.address = int(self.settings.get("address", 1))
        if not 1 <= self.address <= 247:
            raise CommandError(f"address must be 1 to 247, not {self.address}")
        self.crc_swapped = bool(self.settings.get("crc_high_byte_first", False))
        # Replies are short; a long timeout only makes a missing reply slow to report.
        self.transport.set_timeout(float(self.settings.get("timeout", 0.5)))

    # --- protocol -----------------------------------------------------------------
    def _exchange(self, frame: bytes, reply_size: int, strict: bool) -> bytes:
        if self.transport.scheme != "sim":
            time.sleep(0.01)  # Modbus RTU needs a pause between frames
        self.transport.clear_input()
        sent = with_crc(frame, self.crc_swapped)
        self.transport.write_raw(sent)
        reply = self._skip_echo(sent, self.transport.read_some(reply_size), reply_size)
        if not reply:
            raise CommandError("no reply from the KP184; check the port, baud rate and "
                               f"address ({self.address})")
        if reply[0] != self.address or reply[1] & 0x7F != frame[1]:
            raise CommandError(f"unexpected reply from the KP184: {reply.hex(' ')}")
        if reply[1] & 0x80:
            raise CommandError(f"the KP184 rejected the command (code {reply[2]:#04x})")
        if strict and (len(reply) < reply_size or not crc_ok(reply)):
            raise CommandError(f"garbled reply from the KP184: {reply.hex(' ')}")
        return reply

    def _skip_echo(self, sent: bytes, reply: bytes, reply_size: int) -> bytes:
        """Drop our own request if the adapter echoed it (some RS-485 adapters do).

        Only a complete copy of the request counts as echo, so a real reply that
        merely begins like the request is kept.
        """
        n = min(len(reply), len(sent))
        if n < 4 or reply[:n] != sent[:n]:
            return reply
        if len(reply) < len(sent):
            reply += self.transport.read_some(len(sent) - len(reply))
            if reply[:len(sent)] != sent:
                return reply  # not an echo after all
        rest = reply[len(sent):]
        if len(rest) < reply_size:
            rest += self.transport.read_some(reply_size - len(rest))
        return rest

    def read_register(self, register: int) -> int:
        frame = bytes([self.address, 0x03, register >> 8, register & 0xFF, 0x00, 0x04])
        reply = self._exchange(frame, 9, strict=True)
        if reply[2] != 4:
            raise CommandError(f"unexpected reply from the KP184: {reply.hex(' ')}")
        return int.from_bytes(reply[3:7], "big")

    def write_register(self, register: int, value: int) -> None:
        value = int(round(value))
        if not 0 <= value <= 0xFFFFFFFF:
            raise CommandError(f"register value {value} out of range")
        frame = (bytes([self.address, 0x06, register >> 8, register & 0xFF, 0x00, 0x01, 0x04])
                 + value.to_bytes(4, "big"))
        reply = self._exchange(frame, 8, strict=True)
        if reply[2:6] != frame[2:4] + value.to_bytes(4, "big")[2:]:
            raise CommandError(f"unexpected reply from the KP184: {reply.hex(' ')}")

    # --- commands -------------------------------------------------------------------
    @command("Switch the load input on or off")
    def set_input(self, on: bool) -> None:
        self.write_register(REG_LOAD_ON, 1 if on else 0)

    @command("Is the load input on?")
    def is_input_on(self) -> bool:
        return bool(self.read_register(REG_LOAD_ON) & 0x01)

    @command("Select the operating mode: CV, CC, CR or CW",
             params={"mode": Param(choices=MODES)})
    def set_mode(self, mode: str) -> None:
        self.write_register(REG_MODE, MODES.index(mode))
        # The load acknowledges a mode it then ignores (seen with CV while its battery
        # test was active), so check.
        actual = self.get_mode()
        if actual != mode:
            raise CommandError(f"the KP184 did not switch to {mode} mode (it is in "
                               f"{actual} mode); is a front-panel function such as the "
                               "battery test active?")

    @command("Read the operating mode")
    def get_mode(self) -> str:
        return MODES[self.read_register(REG_MODE) & 0x03]

    @command("Set the constant-voltage setpoint (used in CV mode)",
             params={"volts": Param(unit="V", min=0, max=150)})
    def set_voltage(self, volts: float) -> None:
        self.write_register(REG_CV, volts * 1000)

    @command("Set the constant-current setpoint (used in CC mode)",
             params={"amps": Param(unit="A", min=0, max=40)})
    def set_current(self, amps: float) -> None:
        self.write_register(REG_CC, amps * 1000)

    @command("Set the constant-resistance setpoint (used in CR mode)",
             params={"ohms": Param(unit="Ω", min=0, max=9999.9)})
    def set_resistance(self, ohms: float) -> None:
        self.write_register(REG_CR, ohms * 10)

    @command("Set the constant-power setpoint (used in CW mode)",
             params={"watts": Param(unit="W", min=0, max=400)})
    def set_power(self, watts: float) -> None:
        self.write_register(REG_CW, watts * 100)

    @command("Constant current: select CC mode and set the current",
             params={"amps": Param(unit="A", min=0, max=40)})
    def constant_current(self, amps: float) -> None:
        self.set_current(amps)
        self.set_mode("CC")

    @command("Constant voltage: select CV mode and set the voltage",
             params={"volts": Param(unit="V", min=0, max=150)})
    def constant_voltage(self, volts: float) -> None:
        self.set_voltage(volts)
        self.set_mode("CV")

    @command("Constant resistance: select CR mode and set the resistance",
             params={"ohms": Param(unit="Ω", min=0, max=9999.9)})
    def constant_resistance(self, ohms: float) -> None:
        self.set_resistance(ohms)
        self.set_mode("CR")

    @command("Constant power: select CW mode and set the power",
             params={"watts": Param(unit="W", min=0, max=400)})
    def constant_power(self, watts: float) -> None:
        self.set_power(watts)
        self.set_mode("CW")

    @command("Measure the voltage at the load input", unit="V")
    def measure_voltage(self) -> float:
        return self.read_register(REG_VOLTAGE) / 1000

    @command("Measure the current through the load", unit="A")
    def measure_current(self) -> float:
        return self.read_register(REG_CURRENT) / 1000

    @command("Measure the power taken by the load (voltage × current)", unit="W")
    def measure_power(self) -> float:
        return self.measure_voltage() * self.measure_current()

    @command("Read a raw 32-bit register (for registers this driver does not cover)")
    def read_raw_register(self, register: int) -> int:
        return self.read_register(register)

    @command("Write a raw 32-bit register (for registers this driver does not cover)")
    def write_raw_register(self, register: int, value: int) -> None:
        self.write_register(register, value)

    def identify(self) -> str:
        state = "on" if self.is_input_on() else "off"
        return f"KP184 at address {self.address}: input {state}, {self.measure_voltage():.3f} V"

    def close(self) -> None:
        if not self.leave_output_on():
            self.set_input(False)
