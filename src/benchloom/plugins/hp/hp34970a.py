# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Agilent 34970A data acquisition / switch unit, and a simulator for it.

Commands are from the Agilent 34970A User's Guide (chapter 5, Remote Interface
Reference, and the module pages at the end of chapter 4). The mainframe has three slots
(100, 200, 300) and an optional internal multimeter. Channel numbers are the slot
plus the channel on the card: 101 to 122 on a 34901A in slot 1, for example.

The driver asks the slots which cards they hold when it connects and checks every
channel against that, so a wrong channel fails with a message naming the card
instead of an instrument error. Measurements run as a one-sweep scan: configure,
INIT, wait until DATA:POIN? has every reading, FETC?. That way no single read
waits for a slow measurement, which GPIB adapters with a short read timeout
(the AR488's is 1.2 s) would give up on.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

from benchloom import CommandError, Param, ScpiDevice, command
from benchloom.scpi import ScpiSimulator, SimError, sim_number

SLOTS = (1, 2, 3)
OVERLOAD = 9.9e37  # the reading for an overload or open input
MAX_DAC = 12.0
CHANNELS = Param(doc='channels like "101:110,115"')
CHANNEL = Param(min=101, max=399)


@dataclass(frozen=True)
class Card:
    """What a plug-in module can do, by channel number on the card (1 to 99)."""

    model: str
    name: str
    measure: frozenset[int] = frozenset()  # routed to the internal multimeter
    current: frozenset[int] = frozenset()  # DC/AC current channels
    four_wire_offset: int = 0  # n pairs with n + offset for 4-wire; 0: no 4-wire
    switch: frozenset[int] = frozenset()  # channels ROUT:CLOS/OPEN accept
    digital: frozenset[int] = frozenset()
    totalizer: frozenset[int] = frozenset()
    dac: frozenset[int] = frozenset()
    # how many relays may be closed at once (User's Guide p. 164-176, 259):
    # "many"; "one_while_scanning" (one per card while any of its channels is in the
    # scan list); "one" (always one per card); "one_per_bank" (RF cards: one per
    # bank, and a bank is opened only by closing another of its channels)
    closes: str = "many"

    @property
    def close_only(self) -> bool:
        """The card ignores ROUT:OPEN; the RF multiplexers work like that."""
        return self.closes == "one_per_bank"

    @property
    def channels(self) -> frozenset[int]:
        return (self.measure | self.current | self.switch | self.digital | self.totalizer
                | self.dac)


def _span(first: int, last: int) -> frozenset[int]:
    return frozenset(range(first, last + 1))


_MATRIX = frozenset(10 * row + col for row in range(1, 5) for col in range(1, 9))
_RF = _span(11, 14) | _span(21, 24)
CARDS = {card.model: card for card in (
    Card("34901A", "20-channel multiplexer", measure=_span(1, 20), current=_span(21, 22),
         four_wire_offset=10, switch=_span(1, 22), closes="one_while_scanning"),
    Card("34902A", "16-channel reed multiplexer", measure=_span(1, 16),
         four_wire_offset=8, switch=_span(1, 16), closes="one_while_scanning"),
    Card("34903A", "20-channel actuator", switch=_span(1, 20)),
    Card("34904A", "4x8 matrix", switch=_MATRIX),
    Card("34905A", "dual 4-channel RF multiplexer (50 Ω)", switch=_RF,
         closes="one_per_bank"),
    Card("34906A", "dual 4-channel RF multiplexer (75 Ω)", switch=_RF,
         closes="one_per_bank"),
    Card("34907A", "multifunction module", digital=_span(1, 2), totalizer=_span(3, 3),
         dac=_span(4, 5)),
    Card("34908A", "40-channel single-ended multiplexer", measure=_span(1, 40),
         switch=_span(1, 40), closes="one"),
)}

# function name -> (CONF/MEAS header, which channels it needs)
FUNCTIONS = {
    "dc_voltage": ("VOLT:DC", "measure"),
    "ac_voltage": ("VOLT:AC", "measure"),
    "resistance": ("RES", "measure"),
    "four_wire_resistance": ("FRES", "four_wire"),
    "dc_current": ("CURR:DC", "current"),
    "ac_current": ("CURR:AC", "current"),
    "frequency": ("FREQ", "measure"),
    "period": ("PER", "measure"),
    "temperature": ("TEMP", "measure"),
}
SENSORS = {  # sensor -> (SCPI name, allowed types)
    "thermocouple": ("TC", ("B", "E", "J", "K", "N", "R", "S", "T")),
    "rtd": ("RTD", ("85", "91")),
    "four_wire_rtd": ("FRTD", ("85", "91")),
    "thermistor": ("THER", ("2252", "5000", "10000")),
}

# ranges p. 21; resolution p. 203 and 208; NPLC p. 218; gate times p. 217
_VOLTS = (0.1, 1.0, 10.0, 100.0, 300.0)
_OHMS = (100.0, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8)
_AMPS = (0.01, 0.1, 1.0)
RANGES = {"VOLT:DC": _VOLTS, "VOLT:AC": _VOLTS, "RES": _OHMS, "FRES": _OHMS,
          "CURR:DC": _AMPS, "CURR:AC": _AMPS}
RESOLUTION_HEADERS = ("VOLT:DC", "RES", "FRES", "CURR:DC")  # AC: fixed 6½ digits
NPLC_HEADERS = ("VOLT:DC", "RES", "FRES", "CURR:DC", "TEMP")
NPLC_CHOICES = (0.02, 0.2, 1.0, 2.0, 10.0, 20.0, 100.0, 200.0)
APERTURES = (0.01, 0.1, 1.0)
NPLC = Param(choices=(0,) + NPLC_CHOICES, doc="power line cycles; 0: the default, 1")
APERTURE = Param(unit="s", choices=(0,) + APERTURES,
                 doc="frequency and period gate time; 0: the default, 0.1 s")


def range_param(header: str, unit: str) -> Param:
    return Param(unit=unit, choices=(0,) + RANGES[header], doc="0: autorange")


def resolution_param(unit: str) -> Param:
    return Param(unit=unit, min=0, doc="0: the default (1 PLC); or give nplc instead")


OPTION_PARAMS = {
    "function": Param(choices=tuple(FUNCTIONS)),
    "sensor": Param(choices=tuple(SENSORS)),
    "kind": Param(doc="thermocouple B E J K N R S T, RTD 85 or 91, "
                      "thermistor 2252 5000 10000"),
    "range": Param(min=0, doc="in the function's unit; 0: autorange"),
    "resolution": Param(min=0, doc="in the function's unit; 0: the default"),
    "nplc": NPLC,
    "aperture": APERTURE,
}


def _what(function: str) -> str:
    return function.replace("_", " ")


def _range_and_resolution(function: str, header: str, range: float,
                          resolution: float) -> str:
    """The range and resolution part of a CONF command; "" keeps the defaults."""
    range, resolution = float(range or 0), float(resolution or 0)
    if range:
        if header not in RANGES:
            raise CommandError(f"{_what(function)} has no range to choose")
        if not any(math.isclose(range, r) for r in RANGES[header]):
            raise CommandError(f"{_what(function)} ranges are "
                               f"{', '.join(f'{r:g}' for r in RANGES[header])}, "
                               f"not {range:g} (0: autorange)")
    if resolution < 0:
        raise CommandError("the resolution must be above 0 (0: the default)")
    if resolution:
        if header not in RESOLUTION_HEADERS:
            raise CommandError(f"a resolution is set for DC volts, DC current and "
                               f"resistance only, not {_what(function)}")
        return f"{range:g},{resolution:g}," if range else f"AUTO,{resolution:g},"
    return f"{range:g}," if range else ""


def _check_timing(function: str, header: str, resolution: float, nplc: float,
                  aperture: float) -> tuple[float, float]:
    nplc, aperture = float(nplc or 0), float(aperture or 0)
    if nplc:
        if header not in NPLC_HEADERS:
            raise CommandError(f"the integration time (NPLC) is set for DC volts, DC "
                               f"current, resistance and temperature only, not "
                               f"{_what(function)}")
        if nplc not in NPLC_CHOICES:
            raise CommandError(f"NPLC is {', '.join(f'{n:g}' for n in NPLC_CHOICES)}, "
                               f"not {nplc:g} (0: the default, 1)")
        if resolution:
            raise CommandError("give the resolution or the NPLC, not both: the "
                               "resolution sets the integration time (p. 203)")
    if aperture:
        if header not in ("FREQ", "PER"):
            raise CommandError(f"the gate time is set for frequency and period only, "
                               f"not {_what(function)}")
        if aperture not in APERTURES:
            raise CommandError(f"the gate time is 0.01, 0.1 or 1 s, not {aperture:g}")
    return nplc, aperture


def reading_seconds(header: str, nplc: float = 0, aperture: float = 0) -> float:
    """How long one reading takes at 50 Hz: the longest automatic channel delay for the
    function (p. 89) plus the time from the single-channel reading rates (p. 408)."""
    if header in ("VOLT:AC", "CURR:AC"):
        return 1.0 + 1.0  # medium (20 Hz) filter, the default: 1 s delay, 1 reading/s
    if header in ("FREQ", "PER"):
        return 0.3 + {0.01: 1 / 70, 0.1: 1 / 9, 1.0: 1.0}[aperture or 0.1]
    # resistance, RTD, thermistor: up to 200 ms on 10 MΩ and 100 MΩ; DC volts,
    # thermocouple, DC current: 2 ms
    delay = 0.2 if header in ("RES", "FRES", "TEMP") else 0.002
    return delay + (nplc or 1.0) / 47  # 1 PLC: 47 readings/s at 50 Hz


def parse_channels(text: str) -> list[int]:
    """'101:105,110' -> [101, 102, 103, 104, 105, 110]."""
    channels: list[int] = []
    for part in str(text).replace("(@", "").replace(")", "").split(","):
        part = part.strip()
        if not part:
            continue
        first, _, last = part.partition(":")
        try:
            low, high = int(first), int(last or first)
        except ValueError:
            raise CommandError(f"not a channel or range: {part!r} (use e.g. 101:110,115)") \
                from None
        if high < low:
            raise CommandError(f"range {part} runs backwards")
        channels += range(low, high + 1)
    if not channels:
        raise CommandError("no channels given")
    return channels


def channel_list(channels) -> str:
    return "(@" + ",".join(str(c) for c in channels) + ")"


def display_places(text: str) -> int:
    """Characters the front panel needs: , . ; share the place before them (p. 266)."""
    return sum(1 for i, c in enumerate(text) if not (i and c in ",.;"))


def _reading(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise CommandError(f"the instrument sent {text!r}, not a reading") from None
    if abs(value) >= OVERLOAD:
        return math.copysign(math.inf, value)
    return value


class HP34970A(ScpiDevice):
    def connect(self) -> None:
        self.base_timeout = self.transport.timeout
        self.cards: dict[int, Card | None] = {}
        self.unknown: dict[int, str] = {}  # slot -> model the driver does not know
        for slot in SLOTS:
            reply = self.ask(f"SYST:CTYP? {slot}00")
            fields = [f.strip() for f in reply.split(",")]
            model = fields[1] if len(fields) > 1 else ""
            if model in ("", "0"):
                self.cards[slot] = None
            elif model in CARDS:
                self.cards[slot] = CARDS[model]
            else:
                self.cards[slot] = Card(model, "unknown module")
                self.unknown[slot] = model
        self.has_dmm = self.ask("INST:DMM:INST?").strip() == "1"
        # installed is not enough: INST:DMM OFF (or the front panel) switches it off
        self.dmm_on = self.has_dmm and self.ask("INST:DMM?").strip() == "1"
        self.monitoring: int | None = None
        self.reading_seconds = 1.0
        # plain readings: no channel numbers, time stamps, units or alarms in them
        for name in ("CHAN", "TIME", "UNIT", "ALAR"):
            self.transport.write(f"FORM:READ:{name} OFF")
        self.transport.write("TRIG:SOUR IMM")
        self.transport.write("TRIG:COUN 1")
        self.check_errors()

    def close(self) -> None:
        """Return the cards to their power-on state (relays open, DACs at 0 V)."""
        # also called when connect() failed early, before the cards were read
        if getattr(self, "monitoring", None):
            self.transport.write("ROUT:MON:STAT OFF")
            self.monitoring = None
        if self.leave_output_on() or not any(getattr(self, "cards", {}).values()):
            return
        self.transport.write("ABOR")
        self.transport.write("SYST:CPON ALL")

    def identify(self) -> str:
        return f"{self.idn()}; {self.list_cards()}"

    # --- channel checks ---------------------------------------------------------------
    def _card(self, channel: int) -> tuple[Card, int]:
        slot, number = divmod(int(channel), 100)
        if slot not in SLOTS:
            raise CommandError(f"channel {channel}: there is no slot {slot} (use 101 to 399)")
        card = self.cards.get(slot)
        if card is None:
            raise CommandError(f"channel {channel}: slot {slot} is empty")
        if slot in self.unknown:
            raise CommandError(f"channel {channel}: slot {slot} holds a {card.model}, "
                               "which this driver does not know")
        if number not in card.channels:
            raise CommandError(f"channel {channel}: the {card.model} {card.name} in slot "
                               f"{slot} has no channel {number:02d}")
        return card, number

    def _check(self, channel: int, need: str, what: str) -> None:
        card, number = self._card(channel)
        ok = {
            "measure": number in card.measure,
            "current": number in card.current,
            "four_wire": number in card.measure and card.four_wire_offset
                         and number <= card.four_wire_offset,
            "switch": number in card.switch,
            "digital": number in card.digital,
            "totalizer": number in card.totalizer,
            "dac": number in card.dac,
        }[need]
        if ok:
            return
        slot = int(channel) // 100
        hint = {
            "current": " (only channels 21 and 22 of a 34901A measure current)",
            "four_wire": (f" (4-wire uses channels 01 to {card.four_wire_offset:02d}, each "
                          f"paired with the channel {card.four_wire_offset} above)"
                          if card.four_wire_offset else " (this card has no 4-wire)"),
        }.get(need, "")
        raise CommandError(f"channel {channel}: the {card.model} {card.name} in slot {slot} "
                           f"cannot {what}{hint}")

    def _need_dmm(self) -> None:
        if not self.has_dmm:
            raise CommandError("this 34970A has no internal multimeter to measure with")
        if not self.dmm_on:
            raise CommandError("the internal multimeter is switched off; switch it on "
                               "from the front panel (or with INST:DMM ON, which also "
                               "resets the instrument)")

    # --- scanning -----------------------------------------------------------------------
    def _configure(self, function: str, channels, sensor: str = "thermocouple",
                   kind: str = "K", range: float = 0, resolution: float = 0,
                   nplc: float = 0, aperture: float = 0) -> str:
        if function not in FUNCTIONS:
            raise CommandError(f"unknown function {function!r}: use one of "
                               f"{', '.join(FUNCTIONS)}")
        header, need = FUNCTIONS[function]
        args = ""
        if function == "temperature":
            if sensor not in SENSORS:
                raise CommandError(f"unknown sensor {sensor!r}: use one of {', '.join(SENSORS)}")
            name, kinds = SENSORS[sensor]
            kind = str(kind).upper()
            if kind not in kinds:
                raise CommandError(f"a {sensor} is type {', '.join(kinds)}, not {kind!r}")
            if sensor == "four_wire_rtd":
                need = "four_wire"
            args = f"{name},{kind},"
        args += _range_and_resolution(function, header, range, resolution)
        nplc, aperture = _check_timing(function, header, resolution, nplc, aperture)
        verbs = {"temperature": f"measure temperature with a {sensor}",
                 "four_wire_resistance": "measure 4-wire resistance"}
        what = verbs.get(function, f"measure {function.replace('_', ' ')}")
        for channel in channels:
            self._check(channel, need, what)
        listed = channel_list(channels)
        self.send(f"CONF:{header} {args}{listed}")
        # CONF sets range, resolution, NPLC and channel delay to their defaults (p. 214),
        # so the integration time has to follow it every time
        if nplc:
            self.send(f"SENS:{header}:NPLC {nplc:g},{listed}")
        if aperture:
            self.send(f"SENS:{header}:APER {aperture:g},{listed}")
        if function == "temperature":
            self.send(f"UNIT:TEMP C,{listed}")
        self.reading_seconds = reading_seconds(header, nplc, aperture)
        return listed

    def _run(self, count: int) -> list[float]:
        """Start one sweep of the scan list and return its readings."""
        self.send("INIT")
        # the reading times come from p. 89 and p. 408; twice that leaves room for the bus
        deadline = time.monotonic() + 3.0 + 2 * self.reading_seconds * count
        while True:
            points = int(self.ask_number("DATA:POIN?"))
            if points >= count:
                break
            if time.monotonic() > deadline:
                self.transport.write("ABOR")
                raise CommandError(f"the scan gave {points} of {count} readings in time")
            time.sleep(0.05)
        reply = self.ask("FETC?")
        values = [_reading(v) for v in reply.split(",") if v.strip()]
        if len(values) != count:
            raise CommandError(f"FETC? returned {len(values)} readings, expected {count}")
        # an empty scan list lets the multiplexers close several relays again (p. 240)
        self.send("ROUT:SCAN (@)")
        return values

    def _measure(self, function: str, channel: int, **options) -> float:
        self._need_dmm()
        self._stop_monitor()
        self._configure(function, [int(channel)], **options)
        return self._run(1)[0]

    @command("Scan channels once with one function and return the readings in channel "
             "order. Functions: dc_voltage, ac_voltage, resistance, "
             "four_wire_resistance, dc_current, ac_current, frequency, period, temperature "
             "(sensor and kind as for measure_temperature). range, resolution, nplc and "
             "aperture as for the measure commands; 0 keeps the default. Stops a monitor",
             params={"channels": CHANNELS, **OPTION_PARAMS})
    def scan(self, function: str, channels: str, sensor: str = "thermocouple",
             kind: str = "K", range: float = 0, resolution: float = 0, nplc: float = 0,
             aperture: float = 0) -> list[float]:
        self._need_dmm()
        self._stop_monitor()
        listed = parse_channels(channels)
        self._configure(function, listed, sensor, kind, range, resolution, nplc, aperture)
        return self._run(len(listed))

    # --- single measurements ------------------------------------------------------------
    @command("Measure DC voltage on a multiplexer channel. range 0.1 to 300 V (0: "
             "autorange); resolution in V or nplc, not both (0: 1 PLC)", unit="V",
             params={"channel": CHANNEL, "range": range_param("VOLT:DC", "V"),
                     "resolution": resolution_param("V"), "nplc": NPLC})
    def measure_dc_voltage(self, channel: int, range: float = 0, resolution: float = 0,
                           nplc: float = 0) -> float:
        return self._measure("dc_voltage", channel, range=range, resolution=resolution,
                             nplc=nplc)

    @command("Measure AC voltage (RMS) on a multiplexer channel. range 0.1 to 300 V "
             "(0: autorange)", unit="V",
             params={"channel": CHANNEL, "range": range_param("VOLT:AC", "V")})
    def measure_ac_voltage(self, channel: int, range: float = 0) -> float:
        return self._measure("ac_voltage", channel, range=range)

    @command("Measure resistance on a multiplexer channel; four_wire pairs it with the "
             "channel 10 (34901A) or 8 (34902A) above. range 100 Ω to 100 MΩ "
             "(0: autorange); resolution in Ω or nplc, not both (0: 1 PLC)", unit="Ω",
             params={"channel": CHANNEL, "range": range_param("RES", "Ω"),
                     "resolution": resolution_param("Ω"), "nplc": NPLC})
    def measure_resistance(self, channel: int, four_wire: bool = False, range: float = 0,
                           resolution: float = 0, nplc: float = 0) -> float:
        return self._measure("four_wire_resistance" if four_wire else "resistance", channel,
                             range=range, resolution=resolution, nplc=nplc)

    @command("Measure DC current on channel 21 or 22 of a 34901A. range 0.01, 0.1 or 1 A "
             "(0: autorange); resolution in A or nplc, not both (0: 1 PLC)", unit="A",
             params={"channel": CHANNEL, "range": range_param("CURR:DC", "A"),
                     "resolution": resolution_param("A"), "nplc": NPLC})
    def measure_dc_current(self, channel: int, range: float = 0, resolution: float = 0,
                           nplc: float = 0) -> float:
        return self._measure("dc_current", channel, range=range, resolution=resolution,
                             nplc=nplc)

    @command("Measure AC current (RMS) on channel 21 or 22 of a 34901A. range 0.01, 0.1 "
             "or 1 A (0: autorange)", unit="A",
             params={"channel": CHANNEL, "range": range_param("CURR:AC", "A")})
    def measure_ac_current(self, channel: int, range: float = 0) -> float:
        return self._measure("ac_current", channel, range=range)

    @command("Measure frequency on a multiplexer channel. aperture is the gate time: "
             "0.01, 0.1 (default) or 1 s", unit="Hz",
             params={"channel": CHANNEL, "aperture": APERTURE})
    def measure_frequency(self, channel: int, aperture: float = 0) -> float:
        return self._measure("frequency", channel, aperture=aperture)

    @command("Measure period on a multiplexer channel. aperture is the gate time: 0.01, "
             "0.1 (default) or 1 s", unit="s",
             params={"channel": CHANNEL, "aperture": APERTURE})
    def measure_period(self, channel: int, aperture: float = 0) -> float:
        return self._measure("period", channel, aperture=aperture)

    @command("Measure temperature on a multiplexer channel. Thermocouples use the card's "
             "built-in reference junction. nplc 0.02 to 200 (0: 1 PLC)", unit="°C",
             params={"channel": CHANNEL, "sensor": OPTION_PARAMS["sensor"],
                     "kind": OPTION_PARAMS["kind"], "nplc": NPLC})
    def measure_temperature(self, channel: int, sensor: str = "thermocouple",
                            kind: str = "K", nplc: float = 0) -> float:
        return self._measure("temperature", channel, sensor=sensor, kind=kind, nplc=nplc)

    # --- monitor ------------------------------------------------------------------------
    @command("Monitor one multiplexer channel: the instrument keeps measuring it without "
             "a new scan for each reading, and read_monitor returns the latest reading. "
             "Options as for scan. Measuring or scanning stops the monitor",
             params={"channel": CHANNEL, **OPTION_PARAMS})
    def start_monitor(self, function: str, channel: int, sensor: str = "thermocouple",
                      kind: str = "K", range: float = 0, resolution: float = 0,
                      nplc: float = 0, aperture: float = 0) -> None:
        self._need_dmm()
        self._stop_monitor()
        # the channel must be configured and in the scan list (p. 93): CONF does both
        listed = self._configure(function, [int(channel)], sensor, kind, range, resolution,
                                 nplc, aperture)
        self.send(f"ROUT:MON {listed}")
        self.send("ROUT:MON:STAT ON")
        self.monitoring = int(channel)
        # a reading under way when the monitor starts may be from before; two reading
        # times (p. 89, p. 408) later the latest reading is this channel's
        time.sleep(2 * self.reading_seconds)

    @command("The latest reading of the monitored channel (ROUT:MON:DATA?)")
    def read_monitor(self) -> float:
        if not self.monitoring:
            raise CommandError("no monitor is running: start one with start_monitor")
        return _reading(self.ask("ROUT:MON:DATA?"))

    @command("Stop the monitor")
    def stop_monitor(self) -> None:
        self._stop_monitor()

    def _stop_monitor(self) -> None:
        if getattr(self, "monitoring", None):
            self.send("ROUT:MON:STAT OFF")
            self.send("ROUT:SCAN (@)")
            self.monitoring = None

    # --- switching ----------------------------------------------------------------------
    @command("Close a relay: a multiplexer, actuator, matrix (s + row + column) or RF "
             "channel. The instrument may open others: a 34901A or 34902A keeps one "
             "closed while any of its channels is in the scan list (every measurement "
             "sets one), a 34908A always only one, an RF card one per bank",
             params={"channel": CHANNEL})
    def close_channel(self, channel: int) -> None:
        self._check(channel, "switch", "switch")
        self.send(f"ROUT:CLOS (@{int(channel)})")

    @command("Open a relay. RF cards (34905A/6A) have no OPEN: close another channel "
             "in the same bank instead", params={"channel": CHANNEL})
    def open_channel(self, channel: int) -> None:
        self._check(channel, "switch", "switch")
        card, number = self._card(channel)
        if card.close_only:
            bank = number // 10
            raise CommandError(
                f"channel {channel}: the {card.model} {card.name} has no OPEN, one "
                f"channel of each bank is always connected; close another channel of "
                f"bank {bank} ({bank}1 to {bank}4) instead")
        self.send(f"ROUT:OPEN (@{int(channel)})")

    @command("Is the relay of a channel closed?", params={"channel": CHANNEL})
    def is_closed(self, channel: int) -> bool:
        self._check(channel, "switch", "switch")
        return self.ask_number(f"ROUT:CLOS? (@{int(channel)})") == 1

    @command("Open every relay on the card in a slot", params={"slot": Param(min=1, max=3)})
    def open_all(self, slot: int) -> None:
        card = self.cards.get(slot)
        if card is None:
            raise CommandError(f"slot {slot} is empty")
        if not card.switch:
            raise CommandError(f"the {card.model} {card.name} in slot {slot} has no relays")
        if card.close_only:
            raise CommandError(f"the {card.model} {card.name} in slot {slot} has no OPEN: "
                               "one channel of each bank is always connected")
        # the instrument skips the missing channels inside a range (p. 181)
        first, last = min(card.switch), max(card.switch)
        self.send(f"ROUT:OPEN (@{slot}{first:02d}:{slot}{last:02d})")

    # --- multifunction module (34907A) --------------------------------------------------
    @command("Set a DAC output of a 34907A (channel s04 or s05)",
             params={"channel": CHANNEL,
                     "volts": Param(unit="V", min=-MAX_DAC, max=MAX_DAC)})
    def set_dac(self, channel: int, volts: float) -> None:
        self._check(channel, "dac", "output a voltage (DACs are channels 04 and 05 of a "
                                    "34907A)")
        self.send(f"SOUR:VOLT {volts:.4f},(@{int(channel)})")

    @command("Write a byte to a digital port of a 34907A (channel s01 or s02)",
             params={"channel": CHANNEL, "value": Param(min=0, max=255)})
    def write_digital(self, channel: int, value: int) -> None:
        self._check(channel, "digital", "drive a digital port")
        self.send(f"SOUR:DIG:DATA:BYTE {int(value)},(@{int(channel)})")

    @command("Read a byte from a digital port of a 34907A (channel s01 or s02)",
             params={"channel": CHANNEL})
    def read_digital(self, channel: int) -> int:
        self._check(channel, "digital", "read a digital port")
        return int(self.ask_number(f"SENS:DIG:DATA:BYTE? (@{int(channel)})"))

    @command("Read the totalizer count of a 34907A (channel s03); reset sets it to 0 "
             "after reading", params={"channel": CHANNEL})
    def read_totalizer(self, channel: int, reset: bool = False) -> int:
        self._check(channel, "totalizer", "count (the totalizer is channel 03 of a 34907A)")
        count = int(self.ask_number(f"SENS:TOT:DATA? (@{int(channel)})"))
        if reset:
            self.send(f"SENS:TOT:CLE:IMM (@{int(channel)})")
        return count

    # --- mainframe ----------------------------------------------------------------------
    @command("The cards in the three slots, e.g. \"slot 1: 34901A 20-channel "
             "multiplexer; slot 2: empty; slot 3: empty\"")
    def list_cards(self) -> str:
        parts = []
        for slot in SLOTS:
            card = self.cards.get(slot)
            parts.append(f"slot {slot}: " + (f"{card.model} {card.name}" if card else "empty"))
        dmm = "with" if self.has_dmm else "without"
        off = "" if self.dmm_on or not self.has_dmm else " (switched off)"
        return "; ".join(parts) + f"; {dmm} internal multimeter{off}"

    @command("The model of the card in a slot, or \"\" when it is empty",
             params={"slot": Param(min=1, max=3)})
    def card_model(self, slot: int) -> str:
        card = self.cards.get(slot)
        return card.model if card else ""

    @command("Reset the card in a slot to its power-on state (relays open, DACs 0 V)",
             params={"slot": Param(min=1, max=3)})
    def reset_card(self, slot: int) -> None:
        if self.cards.get(slot) is None:
            raise CommandError(f"slot {slot} is empty")
        self.send(f"SYST:CPON {slot}00")

    @command("Show a message on the front panel (up to 13 characters; a comma, period "
             "or semicolon shares the place of the character before it)",
             params={"text": Param(doc="letters, digits and simple punctuation")})
    def set_display_text(self, text: str) -> None:
        places = display_places(text)
        if places > 13:
            raise CommandError(f"the display shows 13 characters, not {places}")
        if "'" in text or '"' in text:
            raise CommandError("the display text cannot contain quotes")
        self.send(f"DISP:TEXT '{text}'")

    @command("Clear the front-panel message")
    def clear_display_text(self) -> None:
        self.send("DISP:TEXT:CLE")


# --- simulator ------------------------------------------------------------------------
class HP34970ASim(ScpiSimulator):
    """A 34970A with an internal multimeter. ``sim://?cards=34901A,,34907A`` chooses the
    cards (empty between commas: an empty slot); the default is a 34901A, a 34903A and
    a 34907A. ``dmm=off`` has the internal multimeter switched off, ``dmm=none``
    leaves it out. Multiplexer channel n reads n/10 V, 1 kΩ + n Ω, 1 kHz + n Hz and
    20 + n/10 °C; current channels 1 mA times the channel.

    Channel ranges skip the channels inside them that a card lacks or that cannot do
    what is asked; only the ends must be valid (User's Guide p. 181). Closing a relay
    opens others as the cards do (see ``Card.closes``), and RF cards refuse OPEN."""

    identity = "HEWLETT-PACKARD,34970A,0,9-1-2"

    def __init__(self, cards: str = "34901A,34903A,34907A", dmm: str = "on", **options):
        self.slot_models = (str(cards).split(",") + ["", "", ""])[:3]
        self.dmm = str(dmm).lower()
        super().__init__(**options)

    def reset(self) -> None:
        self.cards = {slot: CARDS.get(model.strip().upper())
                      for slot, model in zip(SLOTS, self.slot_models)}
        self.scan_list: list[int] = []
        self.functions: dict[int, str] = {}
        self.conf: dict[int, str] = {}  # channel -> the CONF arguments before the list
        self.timing: dict[int, dict[str, float]] = {}  # channel -> NPLC, APER
        self.monitor: int | None = None
        self.monitor_on = False
        self.memory: list[float] = []
        self.closed: set[int] = set()
        self.dac: dict[int, float] = {}
        self.digital_out: dict[int, int] = {}
        self.digital_in: dict[int, int] = {301: 0xA5}  # what port 01 of slot 3 reads
        self.totals: dict[int, int] = {}
        self.display = ""

    def _channels(self, arg: str, need=None) -> list[int]:
        match = re.search(r"\(@([^)]*)\)", arg)
        if not match:
            raise SimError(-109, "Missing parameter")
        def usable(channel: int) -> bool:
            card = self.cards.get(channel // 100)
            return (card is not None and channel % 100 in card.channels
                    and (need is None or channel % 100 in need(card)))

        def check(channel: int) -> None:  # a single channel or the end of a range
            if channel // 100 not in SLOTS:
                raise SimError(+111, "Channel list: slot number out of range")
            card = self.cards.get(channel // 100)
            if card is None or channel % 100 not in card.channels:
                raise SimError(+112, "Channel list: channel number out of range")
            if need is not None and channel % 100 not in need(card):
                raise SimError(+221, "Settings conflict")

        channels: list[int] = []
        for part in match.group(1).split(","):
            first, sep, last = part.strip().partition(":")
            try:
                low, high = int(first), int(last if sep else first)
            except ValueError:
                raise SimError(+110, "Channel list: syntax error") from None
            if high < low:
                raise SimError(+110, "Channel list: syntax error")
            check(low)
            check(high)
            channels += [c for c in range(low, high + 1) if usable(c)]
        return channels

    def _value(self, channel: int, function: str) -> float:
        n = channel % 100
        if function in ("VOLT:DC", "VOLT:AC"):
            return n / 10
        if function == "RES":
            return 1000.0 + n
        if function == "FRES":
            return 1000.0 + n - 0.5  # 4-wire leaves out the lead resistance
        if function in ("CURR:DC", "CURR:AC"):
            return n * 1e-3
        if function == "FREQ":
            return 1000.0 + n
        if function == "PER":
            return 1 / (1000.0 + n)
        if function == "TEMP":
            return 20 + n / 10
        raise SimError(-113, "Undefined header")

    def routes(self):
        def configure(function, need):
            def handler(arg):
                channels = self._channels(arg, need)
                for channel in channels:
                    self.functions[channel] = function
                    self.conf[channel] = arg.split("(@")[0].rstrip(",").strip()
                    self.timing[channel] = {}  # back to the defaults (p. 214)
                self.scan_list = channels
            return handler

        def timing(function, name, choices):
            def handler(arg):
                value, rest = value_and_channels(arg)
                number = sim_number(value, min(choices), max(choices))
                if number not in choices:
                    raise SimError(-224, "Illegal parameter value")
                channels = self._channels(rest)
                if any(self.functions.get(c) != function for c in channels):
                    raise SimError(+221, "Settings conflict")  # p. 214
                for channel in channels:
                    self.timing[channel][name] = number
            return handler

        def scan(arg):
            if re.fullmatch(r"\(@\s*\)", arg.strip()):
                self.scan_list = []
            else:
                self.scan_list = self._channels(arg)
            if self.monitor_on and self.monitor not in self.scan_list:
                self.monitor_on = False

        def monitor(arg):
            channels = self._channels(arg)
            if len(channels) != 1:
                raise SimError(+221, "Settings conflict")
            self.monitor = channels[0]

        def monitor_state(arg):
            on = arg.strip().upper() in ("ON", "1")
            # a multiplexer channel must be configured and in the scan list (p. 93)
            if on and (self.monitor is None or self.monitor not in self.scan_list):
                raise SimError(+221, "Settings conflict")
            self.monitor_on = on

        def monitor_data(arg):
            if not self.monitor_on:
                raise SimError(+221, "Settings conflict")
            return f"{self._value(self.monitor, self.functions[self.monitor]):+.8E}"

        def init(arg):
            if not self.scan_list:
                raise SimError(+113, "Channel list: empty scan list")
            self.memory = [self._value(c, self.functions[c]) for c in self.scan_list]

        def fetch(arg):
            return ",".join(f"{v:+.8E}" for v in self.memory)

        def ctyp(arg):
            card = self.cards.get(int(sim_number(arg, 100, 300)) // 100)
            return f"HEWLETT-PACKARD,{card.model},0,1.0" if card else "HEWLETT-PACKARD,0,0,0"

        def close(arg):
            for channel in self._channels(arg, lambda card: card.switch):
                slot = channel // 100
                rule = self.cards[slot].closes
                if rule == "one_per_bank":
                    self.closed = {c for c in self.closed
                                   if c // 100 != slot or c % 100 // 10 != channel % 100 // 10}
                elif rule == "one" or (rule == "one_while_scanning"
                                       and any(c // 100 == slot for c in self.scan_list)):
                    self.closed = {c for c in self.closed if c // 100 != slot}
                self.closed.add(channel)

        def open_(arg):
            self.closed.difference_update(self._channels(
                arg, lambda card: frozenset() if card.close_only else card.switch))

        def closed(arg):
            return ",".join("1" if c in self.closed else "0" for c in self._channels(arg))

        def value_and_channels(arg):
            value, _, rest = arg.partition(",")
            return value, rest

        def dac(arg):
            value, rest = value_and_channels(arg)
            volts = sim_number(value, -MAX_DAC, MAX_DAC)
            for channel in self._channels(rest, lambda card: card.dac):
                self.dac[channel] = volts

        def dig_out(arg):
            value, rest = value_and_channels(arg)
            byte = int(sim_number(value, 0, 255))
            for channel in self._channels(rest, lambda card: card.digital):
                self.digital_out[channel] = byte

        def dig_in(arg):
            channel = self._channels(arg, lambda card: card.digital)[0]
            return str(self.digital_out.get(channel, self.digital_in.get(channel, 0)))

        def totalize(arg):
            channel = self._channels(arg, lambda card: card.totalizer)[0]
            self.totals[channel] = self.totals.get(channel, 0) + 1000
            return f"{self.totals[channel]:+.8E}"

        def clear_total(arg):
            for channel in self._channels(arg, lambda card: card.totalizer):
                self.totals[channel] = 0

        def cpon(arg):
            slots = SLOTS if arg.strip().upper() == "ALL" else [int(sim_number(arg, 100, 300)) // 100]
            for slot in slots:
                self.closed = {c for c in self.closed if c // 100 != slot}
                self.dac = {c: v for c, v in self.dac.items() if c // 100 != slot}
                self.digital_out = {c: v for c, v in self.digital_out.items() if c // 100 != slot}

        def text(arg):
            self.display = arg.strip().strip("'\"")

        def measure_need(card):
            return card.measure

        def current_need(card):
            return card.current

        def four_wire_need(card):
            return {n for n in card.measure if card.four_wire_offset
                    and n <= card.four_wire_offset}

        def temperature(arg):
            sensor = arg.split(",")[0].strip().upper()
            need = four_wire_need if sensor == "FRTD" else measure_need
            configure("TEMP", need)(arg)

        needs = {"VOLT:DC": measure_need, "VOLT:AC": measure_need, "RES": measure_need,
                 "FRES": four_wire_need, "CURR:DC": current_need, "CURR:AC": current_need,
                 "FREQ": measure_need, "PER": measure_need}
        routes = [(rf"CONF:{re.escape(f)}", configure(f, need)) for f, need in needs.items()]
        routes += [
            (r"CONF:TEMP", temperature),
            (r"ROUT:SCAN", scan),
            (r"ROUT:MON", monitor),
            (r"ROUT:MON:STAT", monitor_state),
            (r"ROUT:MON:DATA\?", monitor_data),
            (r"UNIT:TEMP", lambda arg: None),
            (r"INIT", init),
            (r"DATA:POIN\?", lambda arg: str(len(self.memory))),
            (r"FETC\?", fetch),
            (r"ABOR", lambda arg: None),
            (r"FORM:READ:(CHAN|TIME|UNIT|ALAR)", lambda arg: None),
            (r"TRIG:SOUR", lambda arg: None),
            (r"TRIG:COUN", lambda arg: None),
            (r"SYST:CTYP\?", ctyp),
            (r"INST:DMM\?", lambda arg: "1" if self.dmm == "on" else "0"),
            (r"INST:DMM:INST\?", lambda arg: "0" if self.dmm == "none" else "1"),
            (r"ROUT:CLOS", close),
            (r"ROUT:OPEN", open_),
            (r"ROUT:CLOS\?", closed),
            (r"SOUR:VOLT", dac),
            (r"SOUR:DIG:DATA:BYTE", dig_out),
            (r"SENS:DIG:DATA:BYTE\?", dig_in),
            (r"SENS:TOT:DATA\?", totalize),
            (r"SENS:TOT:CLE:IMM", clear_total),
            (r"SYST:CPON", cpon),
            (r"DISP:TEXT", text),
            (r"DISP:TEXT:CLE", lambda arg: setattr(self, "display", "")),
            (r"DISP:TEXT\?", lambda arg: f'"{self.display}"'),
            (r"\*OPC\?", lambda arg: "1"),
        ]
        nplc = (0.02, 0.2, 1.0, 2.0, 10.0, 20.0, 100.0, 200.0)
        for function in ("VOLT:DC", "RES", "FRES", "CURR:DC", "TEMP"):
            routes.append((rf"SENS:{re.escape(function)}:NPLC", timing(function, "NPLC", nplc)))
        for function in ("FREQ", "PER"):
            routes.append((rf"SENS:{function}:APER", timing(function, "APER", (0.01, 0.1, 1.0))))
        return routes
