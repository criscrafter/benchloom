"""Helpers for instruments that speak Modbus RTU (or something close to it)."""

from __future__ import annotations


def crc16(data: bytes) -> int:
    """Modbus RTU CRC-16 (polynomial 0xA001, start 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(frame: bytes, swapped: bool = False) -> bytes:
    """Append the CRC: low byte first as Modbus requires, or high byte first if ``swapped``."""
    crc = crc16(frame)
    low, high = crc & 0xFF, crc >> 8
    return bytes(frame) + (bytes([high, low]) if swapped else bytes([low, high]))


def crc_ok(frame: bytes) -> bool:
    """True if the last two bytes are the CRC of the rest, in either byte order.

    Some firmware sends the CRC high byte first, so both orders are accepted.
    """
    if len(frame) < 3:
        return False
    body, tail = frame[:-2], frame[-2:]
    return tail in (with_crc(body)[-2:], with_crc(body, swapped=True)[-2:])
