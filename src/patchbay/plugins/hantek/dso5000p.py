"""Hantek DSO5000P oscilloscopes (DSO5102P, DSO5202P), and a simulator.

The scope speaks a binary protocol over USB bulk transfers (USB ID 049F:505A).
Framing, commands, the settings block and the key codes follow the reverse-
engineered protocol notes of kooscode/hantek-dso5000p-linux (MIT licence,
protocol/PROTOCOL.md), which mark what was verified on a real DSO5102P. This
driver uses only the verified, harmless parts of the normal protocol:

- echo, read settings, read waveform, read a file (the settings layout);
- write settings, limited to the channel and trigger fields that were tested;
- front-panel key presses (timebase, run/stop, single, autoset, force trigger).

It never sends the factory/debug protocol (some of its commands can brick the
scope or wipe its calibration), never writes the memory depth (that crashed the
scope) and never uploads files.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

from patchbay import CommandError, Device, Param, RawSimTransport, command, simbench

MARK = 0x53  # 'S', the normal protocol
WILDCARD_SUM = 0x66
REQUEST_GAP = 0.08  # the scope merges and rejects requests closer than 80 ms
AFTER_WRITE = 3.0  # wait at most this long after a settings write (it is deaf ~0.65 s)
KEY_GAP = 0.15

LAYOUT_FILE = Path(__file__).with_name("protocol.inf")  # copy for firmware 3.40.0

VOLTS_PER_DIV = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)
PROBES = (1, 10, 100, 1000)
COUPLINGS = ("DC", "AC", "GND")
TIMEBASES = tuple(m * 10.0 ** e for e in range(-9, 2) for m in (2, 4, 8))[:32]  # 2 ns … 40 s
SLOWEST_TRIGGERED = 0.04  # s/div; slower timebases scroll (scan mode)
TRIGGER_STATES = {0: "stop", 1: "ready", 2: "auto", 3: "triggered", 4: "scan", 6: "armed"}
TRIGGER_SOURCES = ("CH1", "CH2", "EXT", "EXT/5", "AC50")

# Settings fields a write may change (tested on hardware).
WRITABLE = {f"VERT-CH{n}-{name}" for n in (1, 2)
            for name in ("DISP", "VB", "COUP", "20MHZ", "PROBE", "POS")} | {
    "TRIG-TYPE", "TRIG-SRC", "TRIG-COUP", "TRIG-VPOS", "TRIG-EDGE-SLOPE", "TRIG-HOLDTIME"}

KEY_SINGLE, KEY_RUN_STOP, KEY_AUTOSET, KEY_FORCE = 18, 19, 17, 47
KEY_FASTER, KEY_SLOWER = 40, 41


# --- framing --------------------------------------------------------------------------
def frame(cmd: int, payload: bytes = b"") -> bytes:
    length = len(payload) + 2  # cmd + payload + checksum
    head = bytes([MARK, length & 0xFF, length >> 8, cmd]) + bytes(payload)
    return head + bytes([sum(head) & 0xFF])


def split_frames(buffer: bytearray) -> list[tuple[int, bytes]]:
    """Take complete frames off the front of ``buffer``: (cmd, payload) pairs."""
    frames = []
    while True:
        while buffer and buffer[0] != MARK:
            del buffer[0]  # resynchronise after noise
        if len(buffer) < 4:
            return frames
        length = buffer[1] | buffer[2] << 8
        if length < 2:
            del buffer[0]
            continue
        if len(buffer) < length + 3:
            return frames
        data = bytes(buffer[:length + 3])
        if data[-1] not in (sum(data[:-1]) & 0xFF, WILDCARD_SUM):
            del buffer[0]
            continue
        del buffer[:length + 3]
        frames.append((data[3], data[4:-1]))


# --- settings block -------------------------------------------------------------------
def parse_layout(text: str) -> list[tuple[str, int, int]]:
    """Fields of the settings block from a protocol.inf: (name, offset, size)."""
    fields, offset = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("[") or "]" not in line:
            continue
        name, _, size = line[1:].partition("]")
        if name in ("TOTAL", "START", "END"):
            continue
        size = int(size.split()[0])
        fields.append((name, offset, size))
        offset += size
    return fields


def decode(fields, block: bytes) -> dict[str, int]:
    return {name: int.from_bytes(block[o:o + n], "little", signed=n in (2, 8))
            for name, o, n in fields}


def encode(fields, values: dict[str, int]) -> bytes:
    block = bytearray(sum(n for _, _, n in fields))
    for name, o, n in fields:
        block[o:o + n] = int(values[name]).to_bytes(n, "little", signed=n in (2, 8))
    return bytes(block)


def fit_sine(times, volts, hertz: float) -> tuple[float, float, float]:
    """Peak amplitude, phase (radians, of a sine) and mean of a sine at ``hertz``.

    A least-squares fit of a*sin + b*cos + c, so noise and a few periods are fine.
    """
    w = 2 * math.pi * hertz
    rows = [(math.sin(w * t), math.cos(w * t), 1.0) for t in times]
    m = [[sum(r[i] * r[j] for r in rows) for j in range(3)] for i in range(3)]
    y = [sum(r[i] * v for r, v in zip(rows, volts)) for i in range(3)]
    for col in range(3):  # Gaussian elimination with partial pivoting
        pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
        m[col], m[pivot], y[col], y[pivot] = m[pivot], m[col], y[pivot], y[col]
        if abs(m[col][col]) < 1e-12:
            raise CommandError("cannot fit a sine: too few samples or too few periods")
        for r in range(col + 1, 3):
            k = m[r][col] / m[col][col]
            m[r] = [a - k * b for a, b in zip(m[r], m[col])]
            y[r] -= k * y[col]
    x = [0.0, 0.0, 0.0]
    for r in (2, 1, 0):
        x[r] = (y[r] - sum(m[r][c] * x[c] for c in range(r + 1, 3))) / m[r][r]
    a, b, c = x
    return math.hypot(a, b), math.atan2(b, a), c


def _index(values, wanted: float, what: str) -> int:
    for i, value in enumerate(values):
        if math.isclose(value, wanted, rel_tol=0.01):
            return i
    below = [v for v in values if v < wanted]
    above = [v for v in values if v > wanted]
    near = " or ".join(f"{v:g}" for v in (below[-1:] + above[:1]))
    raise CommandError(f"{wanted:g} is not a {what} of the scope; the nearest are {near}")


# --- driver ---------------------------------------------------------------------------
class DSO5000P(Device):
    def connect(self) -> None:
        self.transport.set_timeout(float(self.settings.get("timeout", 1.0)))
        self._last_request = 0.0
        self._sync()
        try:
            self.fields = parse_layout(self._read_file("/protocol.inf").decode("ascii", "replace"))
        except CommandError:
            self.fields = []
        if not self.fields:
            self.fields = parse_layout(LAYOUT_FILE.read_text(encoding="ascii"))

    # protocol
    def _send(self, cmd: int, payload: bytes = b"") -> None:
        # the gap counts from the end of the previous reply, not from its request
        wait = self._last_request + REQUEST_GAP - time.monotonic()
        if wait > 0 and self.transport.scheme != "sim":
            time.sleep(wait)
        self.transport.write_raw(frame(cmd, payload))
        self._last_request = time.monotonic()

    def _replies(self, cmd: int, done, timeout: float | None = None) -> list[bytes]:
        """Collect the payloads of replies to ``cmd`` until ``done(payload)``."""
        buffer, payloads = bytearray(), []
        deadline = time.monotonic() + (timeout or self.transport.timeout)
        while time.monotonic() < deadline:
            chunk = self.transport.read_some(65536)
            if not chunk:
                continue
            buffer += chunk
            for reply_cmd, payload in split_frames(buffer):
                if reply_cmd == cmd | 0x80:
                    payloads.append(payload)
                    if done(payload):
                        self._last_request = time.monotonic()
                        return payloads
        raise CommandError("the scope did not answer; is it connected and switched on?")

    def _exchange(self, cmd: int, payload: bytes = b"", done=lambda p: True,
                  timeout: float | None = None, attempts: int = 3) -> list[bytes]:
        """Send a request and collect its replies. The scope sometimes drops a
        request (for instance while its knobs are turned), so re-sync and retry."""
        for attempt in range(attempts):
            self._send(cmd, payload)
            try:
                return self._replies(cmd, done, timeout)
            except CommandError:
                if attempt == attempts - 1:
                    raise
                self._sync()

    def _request(self, cmd: int, payload: bytes = b"", timeout: float | None = None) -> bytes:
        return self._exchange(cmd, payload, timeout=timeout)[0]

    def _sync(self) -> None:
        """The first request after connecting is often lost: echo until it answers."""
        self.transport.clear_input()
        for _ in range(6):
            try:
                self._send(0x00, b"PB")
                if self._replies(0x00, lambda p: True, timeout=0.5)[0] == b"PB":
                    return
            except CommandError:
                pass
        raise CommandError("the scope does not answer; is it connected and switched on?")

    def _read_file(self, path: str) -> bytes:
        payloads = self._exchange(0x10, b"\x00" + path.encode("ascii"),
                                  lambda p: p[:1] in (b"\x02", b"\x03"), timeout=5.0)
        data = b"".join(p[1:] for p in payloads if p[:1] == b"\x01")
        end = payloads[-1]
        if end[:1] != b"\x02" or (len(end) > 1 and end[1] != sum(data) & 0xFF):
            raise CommandError(f"could not read {path} from the scope")
        return data

    def settings_block(self) -> dict[str, int]:
        block = self._request(0x01)
        if len(block) != sum(n for _, _, n in self.fields):
            raise CommandError(f"unexpected settings block of {len(block)} bytes")
        return decode(self.fields, block)

    def _change(self, **changes: int) -> None:
        unsafe = set(changes) - WRITABLE
        if unsafe:
            raise CommandError(f"refusing to write untested settings: {', '.join(sorted(unsafe))}")
        values = self.settings_block()  # always write back fresh values
        if all(values.get(k) == v for k, v in changes.items()):
            return  # nothing to change: a write costs about 0.7 s
        values.update(changes)
        reply = self._request(0x11, encode(self.fields, values))
        if reply[:1] != b"\x00":
            raise CommandError("the scope rejected the settings")
        self._wait_ready()

    def _wait_ready(self) -> None:
        """Echo until the scope answers again, as it does about 0.65 s after a settings
        write. A short read timeout matters: each unanswered echo would otherwise block
        for the whole transport timeout (1 s)."""
        if self.transport.scheme == "sim":
            return self._sync()
        old = self.transport.timeout
        self.transport.set_timeout(0.05)
        try:
            self.transport.clear_input()
            deadline = time.monotonic() + AFTER_WRITE
            while time.monotonic() < deadline:
                self._send(0x00, b"PB")
                try:
                    if self._replies(0x00, lambda p: True, timeout=0.1)[0] == b"PB":
                        return
                except CommandError:
                    pass
        finally:
            self.transport.set_timeout(old)
        self._sync()

    def _key(self, code: int) -> None:
        self._request(0x13, bytes([code]))
        if self.transport.scheme != "sim":
            time.sleep(KEY_GAP)

    # commands: channels
    @command("Switch channel 1 or 2 on or off", params={"channel": Param(min=1, max=2)})
    def set_channel(self, channel: int, on: bool) -> None:
        self._change(**{f"VERT-CH{channel}-DISP": int(on)})

    @command("Set volts per division of channel 1 or 2, including the probe factor",
             params={"channel": Param(min=1, max=2), "volts": Param(unit="V", min=0.001, max=10000)})
    def set_volts_per_div(self, channel: int, volts: float) -> None:
        probe = PROBES[self.settings_block()[f"VERT-CH{channel}-PROBE"]]
        index = _index(VOLTS_PER_DIV[1:], volts / probe, "volts/div setting") + 1  # 1 mV unused
        self._change(**{f"VERT-CH{channel}-VB": index})

    @command("Set the input coupling of channel 1 or 2",
             params={"channel": Param(min=1, max=2), "coupling": Param(choices=COUPLINGS)})
    def set_coupling(self, channel: int, coupling: str) -> None:
        self._change(**{f"VERT-CH{channel}-COUP": COUPLINGS.index(coupling)})

    @command("Set the probe factor of channel 1 or 2",
             params={"channel": Param(min=1, max=2), "factor": Param(choices=("1", "10", "100", "1000"))})
    def set_probe(self, channel: int, factor: str) -> None:
        self._change(**{f"VERT-CH{channel}-PROBE": PROBES.index(int(factor))})

    @command("Move the trace of channel 1 or 2 up or down",
             params={"channel": Param(min=1, max=2), "divisions": Param(unit="div", min=-5, max=5)})
    def set_position(self, channel: int, divisions: float) -> None:
        self._change(**{f"VERT-CH{channel}-POS": round(divisions * 25)})

    @command("Switch the 20 MHz bandwidth limit of channel 1 or 2 on or off",
             params={"channel": Param(min=1, max=2)})
    def set_bandwidth_limit(self, channel: int, on: bool) -> None:
        self._change(**{f"VERT-CH{channel}-20MHZ": int(on)})

    # commands: trigger
    @command("Edge trigger on a source and slope",
             params={"source": Param(choices=TRIGGER_SOURCES), "slope": Param(choices=("rising", "falling"))})
    def set_trigger(self, source: str, slope: str = "rising") -> None:
        self._change(**{"TRIG-TYPE": 0, "TRIG-SRC": TRIGGER_SOURCES.index(source),
                        "TRIG-EDGE-SLOPE": 0 if slope == "rising" else 1})

    @command("Set the trigger level in volts (trigger source CH1 or CH2)", params={"volts": Param(unit="V")})
    def set_trigger_level(self, volts: float) -> None:
        values = self.settings_block()
        source = values["TRIG-SRC"]
        if source not in (0, 1):
            raise CommandError("the trigger level in volts needs CH1 or CH2 as trigger source")
        n = source + 1
        scale = VOLTS_PER_DIV[values[f"VERT-CH{n}-VB"]] * PROBES[values[f"VERT-CH{n}-PROBE"]]
        self._change(**{"TRIG-VPOS": round(volts / scale * 25 + values[f"VERT-CH{n}-POS"])})

    @command("Read the trigger state: stop, ready, auto, triggered, scan or armed")
    def get_trigger_state(self) -> str:
        return TRIGGER_STATES.get(self.settings_block()["TRIG-STATE"], "unknown")

    # commands: timebase and acquisition (by key presses: a settings write of the
    # timebase is stored but not applied)
    @command("Set the timebase in seconds per division (2 ns to 40 s, 2-4-8 steps)",
             params={"seconds": Param(unit="s/div", min=2e-9, max=40)})
    def set_timebase(self, seconds: float) -> None:
        target = _index(TIMEBASES, seconds, "timebase")
        for _ in range(len(TIMEBASES)):
            current = self.settings_block()["HORIZ-TB"]
            if current == target:
                return
            self._key(KEY_SLOWER if target > current else KEY_FASTER)
            deadline = time.monotonic() + 1.0
            while self.settings_block()["HORIZ-TB"] == current and time.monotonic() < deadline:
                pass  # the block updates a little after the key press
        raise CommandError(f"the scope stopped at {TIMEBASES[current]:g} s/div and would not go "
                           f"to {seconds:g} s/div (some timebases are only offered with one "
                           "channel on, or in some acquisition modes)")

    @command("Read the timebase", unit="s/div")
    def get_timebase(self) -> float:
        return TIMEBASES[self.settings_block()["HORIZ-TB"]]

    @command("Start acquiring (Run)")
    def run(self) -> None:
        if self.settings_block()["TRIG-STATE"] == 0:
            self._key(KEY_RUN_STOP)

    @command("Stop acquiring (Stop); waveforms cannot be read while stopped")
    def stop(self) -> None:
        if self.settings_block()["TRIG-STATE"] != 0:
            self._key(KEY_RUN_STOP)

    @command("Press Autoset")
    def autoset(self) -> None:
        self._key(KEY_AUTOSET)

    @command("Force a trigger")
    def force_trigger(self) -> None:
        self._key(KEY_FORCE)

    # commands: data
    @command("Read the on-screen waveform of channel 1 or 2: {time = {...}, volts = {...}}",
             params={"channel": Param(min=1, max=2)})
    def read_waveform(self, channel: int) -> dict:
        values = self.settings_block()
        # At slow timebases the scope has no waveform to hand out while it is still
        # acquiring one. From 8 ms/div up it was usually ready within a second but
        # sometimes only after 3 to 8 s, at random, so a running scope gets 10 s.
        deadline = time.monotonic() + max(10.0, 3 * 16 * TIMEBASES[values["HORIZ-TB"]])
        checked = time.monotonic()
        while True:
            payloads = self._exchange(0x02, bytes([0x01, channel - 1]),
                                      lambda p: p[:1] in (b"\x02", b"\x03"), timeout=5.0)
            if payloads[-1][:1] != b"\x03":
                break
            running = values["TRIG-STATE"] != 0 and values.get(f"VERT-CH{channel}-DISP", 1)
            if not running or time.monotonic() > deadline:
                raise CommandError(
                    f"the scope has no waveform for CH{channel}: is the channel on and the "
                    "scope running and triggering? A stopped scope hands out no waveforms.")
            # Ask again straight away: a waveform seems to be there only for a moment
            # between acquisitions. Check now and then that the scope still runs.
            if time.monotonic() - checked > 1.0:
                values = self.settings_block()
                checked = time.monotonic()
        samples = []
        for p in payloads:
            if p[:1] == b"\x01":
                samples += [b - 256 if b > 127 else b for b in p[2:]]
        n = len(samples)
        scale = (VOLTS_PER_DIV[values[f"VERT-CH{channel}-VB"]]
                 * PROBES[values[f"VERT-CH{channel}-PROBE"]] / 25)
        position = values[f"VERT-CH{channel}-POS"]
        screen = 16 if values.get("CONTROL-DISP-MENU", 1) else 19.2
        dt = TIMEBASES[values["HORIZ-TB"]] * screen / max(n, 1)
        t0 = values["HORIZ-TRIGTIME"] * 1e-12
        return {"time": [t0 + (i - n / 2) * dt for i in range(n)],
                "volts": [(s - position) * scale for s in samples]}

    @command("Peak-to-peak voltage of channel 1 or 2, from the on-screen waveform", unit="V",
             params={"channel": Param(min=1, max=2)})
    def measure_vpp(self, channel: int) -> float:
        volts = self.read_waveform(channel)["volts"]
        return max(volts) - min(volts)

    @command("Mean voltage of channel 1 or 2, from the on-screen waveform", unit="V",
             params={"channel": Param(min=1, max=2)})
    def measure_mean(self, channel: int) -> float:
        volts = self.read_waveform(channel)["volts"]
        return sum(volts) / len(volts)

    @command("RMS voltage (AC+DC) of channel 1 or 2, from the on-screen waveform", unit="V",
             params={"channel": Param(min=1, max=2)})
    def measure_vrms(self, channel: int) -> float:
        volts = self.read_waveform(channel)["volts"]
        return math.sqrt(sum(v * v for v in volts) / len(volts))

    @command("Frequency of the trigger source, from the scope's hardware counter", unit="Hz")
    def measure_frequency(self) -> float:
        return self.settings_block()["TRIG-FREQUENCY"] / 1000  # reported in millihertz

    @command("Gain and phase of a sine going through a circuit: the input on one channel, "
             "the output on the other. Sets the timebase to show a few periods, ranges both "
             "channels and triggers on the input. Returns {gain_db, gain, phase_deg, "
             "input_vpp, output_vpp, input, output}; phase_deg is the output relative to "
             "the input, and input and output are the waveforms measured, for "
             "save_waveform",
             params={"hertz": Param(unit="Hz", min=0.1, max=100e6),
                     "input": Param(min=1, max=2), "output": Param(min=1, max=2),
                     "periods": Param(min=1, max=50)})
    def measure_response(self, hertz: float, input: int = 1, output: int = 2,
                         periods: float = 4) -> dict:
        if input == output:
            raise CommandError("the input and output must be on different channels")
        values = self.settings_block()
        for ch in (input, output):
            if not values[f"VERT-CH{ch}-DISP"]:
                self.set_channel(ch, True)
        # Both channels on: the scope goes no faster than 400 ns/div. From 80 ms/div
        # on it scrolls (scan mode), and the two channels then come from different
        # moments, so 40 ms/div is the slowest used: fewer periods at low frequencies.
        wanted = max(periods / (16 * hertz), 400e-9)
        timebase = min(next((tb for tb in TIMEBASES if tb >= wanted * 0.99), TIMEBASES[-1]),
                       SLOWEST_TRIGGERED)
        if 16 * timebase * hertz < 1:
            raise CommandError(f"{hertz:g} Hz is too slow: the scope shows at most "
                               f"{16 * SLOWEST_TRIGGERED:g} s without scrolling, so it needs "
                               f"{1 / (16 * SLOWEST_TRIGGERED):g} Hz or more")
        self.set_timebase(timebase)
        values = self.settings_block()
        if values["TRIG-SRC"] != input - 1 or values["TRIG-TYPE"] != 0:
            self.set_trigger(f"CH{input}", "rising")
        # A few screen times for the new frequency to reach the screen. A settings write
        # waits for the scope itself (see _wait_ready), which restarts acquiring.
        settle = 0 if self.transport.scheme == "sim" else max(0.2, 3 * 16 * timebase)
        fits: dict[int, tuple[float, float, float]] = {}
        waves: dict[int, dict] = {}
        todo = (input, output)
        for _ in range(6):
            time.sleep(settle)
            for ch in todo:  # only what changed is read again
                waves[ch] = self.read_waveform(ch)
                fits[ch] = fit_sine(waves[ch]["time"], waves[ch]["volts"], hertz)
            values = self.settings_block()
            best, span = {}, {}
            for ch in (input, output):
                probe = PROBES[values[f"VERT-CH{ch}-PROBE"]]
                span[ch] = 2 * fits[ch][0] + abs(fits[ch][2]) * 2  # room for the offset
                steps = [v * probe for v in VOLTS_PER_DIV[1:]]
                best[ch] = next((v for v in steps if v * 6 >= span[ch]), steps[-1])
            # The volts/div ranges differ by up to 2 % (0.5 vs 1 V/div), while the two
            # channels on the same range agree within 0.1 dB. So the output shares the
            # input's range as long as it still spans two divisions there.
            if best[output] <= best[input] and span[output] >= 2 * best[input]:
                best[output] = best[input]
            changes = {}
            for ch in (input, output):
                probe = PROBES[values[f"VERT-CH{ch}-PROBE"]]
                index = _index(VOLTS_PER_DIV, best[ch] / probe, "volts/div setting")
                if index != values[f"VERT-CH{ch}-VB"]:
                    changes[f"VERT-CH{ch}-VB"] = index
            if not changes:
                break
            if values["TRIG-SRC"] == input - 1:  # the trigger level in volts stays
                scale = VOLTS_PER_DIV[changes.get(f"VERT-CH{input}-VB",
                                                  values[f"VERT-CH{input}-VB"])]
                scale *= PROBES[values[f"VERT-CH{input}-PROBE"]]
                level = round(fits[input][2] / scale * 25 + values[f"VERT-CH{input}-POS"])
                if level != values["TRIG-VPOS"]:
                    changes["TRIG-VPOS"] = level
            self._change(**changes)  # one write for all of them
            todo = [ch for ch in (input, output) if f"VERT-CH{ch}-VB" in changes]
            settle = 3 * 16 * timebase if self.transport.scheme != "sim" else 0
        a_in, p_in, _ = fits[input]
        a_out, p_out, _ = fits[output]
        if a_in <= 0:
            raise CommandError(f"no {hertz:g} Hz signal on the input (CH{input})")
        phase = (math.degrees(p_out - p_in) + 180) % 360 - 180
        gain = a_out / a_in
        return {"gain_db": 20 * math.log10(gain) if gain > 0 else -math.inf, "gain": gain,
                "phase_deg": phase, "input_vpp": 2 * a_in, "output_vpp": 2 * a_out,
                "input": waves[input], "output": waves[output]}

    @command("Read the on-screen waveforms of the channels that are on: {CH1 = {time = "
             "{...}, volts = {...}}, CH2 = ...}, for save_waveform")
    def capture(self) -> dict:
        values = self.settings_block()
        shown = [ch for ch in (1, 2) if values[f"VERT-CH{ch}-DISP"]]
        if not shown:
            raise CommandError("both channels are off")
        return {f"CH{ch}": self.read_waveform(ch) for ch in shown}

    def identify(self) -> str:
        values = self.settings_block()
        return (f"Hantek DSO5000P: {TIMEBASES[values['HORIZ-TB']]:g} s/div, "
                f"trigger {TRIGGER_STATES.get(values['TRIG-STATE'], '?')}")


# --- simulator ------------------------------------------------------------------------
class DSO5000PSim(RawSimTransport):
    """A DSO5102P showing a 1 kHz, 2 Vpp sine on CH1; CH2 is off.

    With ``dut=rc_lowpass`` (``sim://?dut=rc_lowpass&cutoff_hz=150``) it is wired
    like a filter test instead: CH1 shows channel 1 of a simulated generator in the
    same run (see :mod:`patchbay.simbench`) and CH2 shows it after an RC low-pass.
    ``dut=rc_highpass`` puts an RC high-pass there instead.
    """

    SIGNAL_HZ, SIGNAL_PEAK = 1000.0, 1.0
    DUTS = ("rc_lowpass", "rc_highpass")

    def __init__(self, dut: str = "", cutoff_hz: float = 150.0, **options):
        if dut and dut not in self.DUTS:
            raise ValueError(f"unknown simulated circuit '{dut}' (known: {', '.join(self.DUTS)})")
        self.dut, self.cutoff_hz = dut, float(cutoff_hz)
        super().__init__(**options)
        self.layout_text = LAYOUT_FILE.read_text(encoding="ascii")
        self.fields = parse_layout(self.layout_text)
        self.values = {name: 0 for name, _, _ in self.fields}
        self.values.update({
            "VERT-CH1-DISP": 1, "VERT-CH1-VB": 8, "VERT-CH2-VB": 9,  # 500 mV, 1 V
            "TRIG-STATE": 2, "TRIG-FREQUENCY": int(self.SIGNAL_HZ * 1000),
            "HORIZ-TB": 15, "CONTROL-DISP-MENU": 1,  # 200 µs/div
        })
        self.requests: list[tuple[int, int]] = []  # (marker, cmd) of every request

    def handle_bytes(self, data: bytes) -> bytes | None:
        buffer = bytearray(data)
        if data[:1] != bytes([MARK]):
            self.requests.append((data[0] if data else 0, data[3] if len(data) > 3 else -1))
            return None  # the debug protocol is never answered here
        frames = split_frames(buffer)
        if len(frames) != 1 or buffer:
            return None  # merged or broken frames are rejected, as on the scope
        cmd, payload = frames[0]
        self.requests.append((MARK, cmd))
        if cmd == 0x00:
            return frame(0x80, payload)
        if cmd == 0x01:
            return frame(0x81, encode(self.fields, self.values))
        if cmd == 0x10:
            if payload[1:].decode("ascii", "replace") != "/protocol.inf":
                return frame(0x90, b"\x03")
            content = self.layout_text.encode("ascii")
            return frame(0x90, b"\x01" + content) + frame(0x90, bytes([2, sum(content) & 0xFF]))
        if cmd == 0x11:
            if len(payload) != sum(n for _, _, n in self.fields):
                return frame(0x91, b"\xff")
            self.values.update({k: v for k, v in decode(self.fields, payload).items()
                                if k in WRITABLE})
            return frame(0x91, b"\x00")
        if cmd == 0x13 and payload:
            self._press(payload[0])
            return frame(0x93, b"\x00")
        if cmd == 0x02 and payload[:1] == b"\x01":
            return self._waveform(payload[1])
        return None

    def _press(self, key: int) -> None:
        v = self.values
        if key == KEY_SLOWER:
            v["HORIZ-TB"] = min(v["HORIZ-TB"] + 1, len(TIMEBASES) - 1)
        elif key == KEY_FASTER:
            v["HORIZ-TB"] = max(v["HORIZ-TB"] - 1, 0)
        elif key == KEY_RUN_STOP:
            v["TRIG-STATE"] = 2 if v["TRIG-STATE"] == 0 else 0
        elif key == KEY_SINGLE:
            v["TRIG-STATE"] = 1

    def _waveform(self, src: int) -> bytes:
        v, n = self.values, 3200  # 4K memory, side menu shown
        channel = src + 1
        if src not in (0, 1) or not v[f"VERT-CH{channel}-DISP"] or v["TRIG-STATE"] == 0:
            return frame(0x82, bytes([3, src])) + frame(0x82, bytes([2, src]))
        scale = VOLTS_PER_DIV[v[f"VERT-CH{channel}-VB"]] * PROBES[v[f"VERT-CH{channel}-PROBE"]]
        dt = TIMEBASES[v["HORIZ-TB"]] * 16 / n
        hertz, peak, offset, shift = self.SIGNAL_HZ, self.SIGNAL_PEAK, 0.0, 0.0
        if self.dut:
            sig = simbench.signal("ch1") or simbench.Signal(1.0, 0.0)
            hertz, peak, offset = sig.hertz, sig.peak, sig.offset
            if channel == 2:  # after the filter: 1 / (1 + j f/fc), or j f/fc times that
                ratio = hertz / self.cutoff_hz
                peak /= math.sqrt(1 + ratio * ratio)
                shift = -math.atan(ratio)
                if self.dut == "rc_highpass":
                    peak *= ratio
                    shift += math.pi / 2
                    offset = 0.0  # a capacitor in series blocks DC
        samples = bytearray()
        for i in range(n):
            volts = offset + peak * math.sin(2 * math.pi * hertz * (i - n / 2) * dt + shift)
            count = round(volts / scale * 25 + v[f"VERT-CH{channel}-POS"])
            samples.append(max(-127, min(127, count)) & 0xFF)
        out = frame(0x82, bytes([0]) + n.to_bytes(3, "little"))
        for start in range(0, n, 10000):
            out += frame(0x82, bytes([1, src]) + samples[start:start + 10000])
        return out + frame(0x82, bytes([2, src]))
