"""A KP184 connected to a 12.6 V battery with 50 mΩ internal resistance."""

from __future__ import annotations

import random

from patchbay import RawSimTransport
from patchbay.modbus import crc_ok, with_crc

SOURCE_VOLTS = 12.6
SOURCE_OHMS = 0.05
MAX_AMPS = 40.0

WRITABLE = {0x010E, 0x0110, 0x0112, 0x0116, 0x011A, 0x011E}


class KP184Sim(RawSimTransport):
    def __init__(self, address="1", **options):
        super().__init__(**options)
        self.address = int(address)
        self.registers = {0x010E: 0, 0x0110: 1, 0x0112: 0, 0x0116: 0, 0x011A: 100, 0x011E: 0}

    def _operating_point(self) -> tuple[float, float]:
        """Voltage and current at the load input."""
        if not self.registers[0x010E] & 1:
            return SOURCE_VOLTS, 0.0
        mode = self.registers[0x0110] & 3
        if mode == 0:  # CV: draw whatever current pulls the source down to the setpoint
            amps = (SOURCE_VOLTS - self.registers[0x0112] / 1000) / SOURCE_OHMS
        elif mode == 1:  # CC
            amps = self.registers[0x0116] / 1000
        elif mode == 2:  # CR
            ohms = max(self.registers[0x011A] / 10, 0.01)
            amps = SOURCE_VOLTS / (ohms + SOURCE_OHMS)
        else:  # CW: smaller root of P = (Vs - I·Rs)·I
            watts = self.registers[0x011E] / 100
            disc = max(SOURCE_VOLTS ** 2 - 4 * SOURCE_OHMS * watts, 0.0)
            amps = (SOURCE_VOLTS - disc ** 0.5) / (2 * SOURCE_OHMS)
        amps = min(max(amps, 0.0), MAX_AMPS)
        return SOURCE_VOLTS - amps * SOURCE_OHMS, amps

    def _read(self, register: int) -> int | None:
        if register in self.registers:
            return self.registers[register]
        volts, amps = self._operating_point()
        if register == 0x0122:
            return max(0, round((volts + random.gauss(0, 0.002)) * 1000))
        if register == 0x0126:
            return max(0, round((amps + (random.gauss(0, 0.001) if amps else 0)) * 1000))
        return None

    def handle_bytes(self, data: bytes) -> bytes | None:
        if len(data) < 8 or not crc_ok(data) or data[0] != self.address:
            return None  # a real device stays silent
        register = int.from_bytes(data[2:4], "big")
        if data[1] == 0x03 and len(data) == 8:
            value = self._read(register)
            if value is None:
                return None
            return with_crc(bytes([self.address, 0x03, 0x04]) + value.to_bytes(4, "big"))
        if data[1] == 0x06 and len(data) == 13 and data[4:7] == b"\x00\x01\x04":
            if register in WRITABLE:
                self.registers[register] = int.from_bytes(data[7:11], "big")
            return with_crc(data[:7])
        return None
