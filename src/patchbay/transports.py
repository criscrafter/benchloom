"""Transports: how bytes get to an instrument.

A device entry in a project names its connection with a resource string:

    sim://                          the plugin's simulator
    serial://COM3?baud=9600         serial port (serial:///dev/ttyUSB0 on Linux)
    tcp://192.168.1.20:5025         raw socket
    prologix://COM4/5               GPIB address 5 through a Prologix or AR488
                                    USB adapter (no vendor driver needed)
    visa://GPIB0::5::INSTR          anything VISA can open (needs the "visa"
                                    extra; uses a vendor VISA when installed,
                                    otherwise pyvisa-py)

Common options: ``timeout`` (seconds), ``term`` (line ending for both
directions, e.g. ``term=\\r\\n``), ``write_term``, ``read_term``.
Serial options: ``baud``, ``bytesize``, ``parity`` (N, E, O), ``stopbits``,
``rtscts=1`` for hardware handshake.
"""

from __future__ import annotations

import socket
from urllib.parse import parse_qsl


class TransportError(Exception):
    pass


def _unescape(text: str) -> str:
    return text.encode("latin-1").decode("unicode_escape")


class Transport:
    scheme = ""

    def __init__(self, *, timeout=2.0, term=None, write_term="\n", read_term="\n",
                 encoding="latin-1", **unknown):
        if unknown:
            raise TransportError(f"unknown {self.scheme} option(s): {', '.join(unknown)}")
        self.timeout = float(timeout)
        self.write_term = _unescape(term if term is not None else write_term)
        self.read_term = _unescape(term if term is not None else read_term)
        self.encoding = encoding

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def write_raw(self, data: bytes) -> None:
        raise NotImplementedError

    def read_raw(self, size: int) -> bytes:
        raise NotImplementedError

    def read_until(self, term: bytes) -> bytes:
        raise NotImplementedError

    def read_some(self, size: int) -> bytes:
        """Read up to ``size`` bytes; return what arrived before the timeout, maybe nothing."""
        try:
            return self.read_raw(size)
        except TimeoutError:
            return b""

    def clear_input(self) -> None:
        """Throw away anything received but not read yet."""

    def set_timeout(self, seconds: float) -> None:
        self.timeout = float(seconds)

    def write(self, text: str) -> None:
        self.write_raw((text + self.write_term).encode(self.encoding))

    def read(self) -> str:
        data = self.read_until(self.read_term.encode(self.encoding))
        return data.decode(self.encoding).rstrip("\r\n")

    def query(self, text: str) -> str:
        self.write(text)
        return self.read()


class SimTransport(Transport):
    """Base for simulators of text instruments: implement :meth:`handle`.

    ``handle`` gets one command line and returns the reply: a ``str`` (sent with the
    read terminator), ``bytes`` (sent exactly as given) or ``None`` (no reply).
    """

    scheme = "sim"

    def __init__(self, **options):
        super().__init__(**options)
        self._out = bytearray()

    def handle(self, command: str) -> str | bytes | None:
        raise NotImplementedError

    def reply(self, data: str | bytes | None) -> None:
        if data is None:
            return
        if isinstance(data, str):
            data = data.encode(self.encoding) + self.read_term.encode(self.encoding)
        self._out += data

    def write_raw(self, data: bytes) -> None:
        for line in data.decode(self.encoding).splitlines():
            if line.strip():
                self.reply(self.handle(line.strip()))

    def read_raw(self, size: int) -> bytes:
        if len(self._out) < size:
            raise TimeoutError("simulated device has nothing to send")
        data = bytes(self._out[:size])
        del self._out[:size]
        return data

    def read_until(self, term: bytes) -> bytes:
        end = self._out.find(term)
        if end < 0:
            raise TimeoutError("simulated device has nothing to send")
        return self.read_raw(end + len(term))

    def read_some(self, size: int) -> bytes:
        return self.read_raw(min(size, len(self._out)))

    def clear_input(self) -> None:
        self._out.clear()


class RawSimTransport(SimTransport):
    """Base for simulators of binary instruments: implement :meth:`handle_bytes`.

    It gets everything written in one go (one frame) and returns the reply bytes or None.
    """

    def handle_bytes(self, data: bytes) -> bytes | None:
        raise NotImplementedError

    def write_raw(self, data: bytes) -> None:
        self.reply(self.handle_bytes(bytes(data)))


class SerialTransport(Transport):
    scheme = "serial"

    def __init__(self, port: str, *, baud=9600, bytesize=8, parity="N", stopbits=1, rtscts=0,
                 **options):
        super().__init__(**options)
        self.port = port
        stopbits = float(stopbits)
        self.serial_options = dict(
            baudrate=int(baud), bytesize=int(bytesize), parity=str(parity).upper()[:1],
            stopbits=int(stopbits) if stopbits.is_integer() else stopbits,
            rtscts=bool(int(rtscts)),
        )
        self._serial = None

    def open(self) -> None:
        import serial

        try:
            self._serial = serial.Serial(self.port, timeout=self.timeout, **self.serial_options)
        except serial.SerialException as exc:
            raise TransportError(f"cannot open serial port {self.port}: {exc}") from exc

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def write_raw(self, data: bytes) -> None:
        self._serial.write(data)

    def read_raw(self, size: int) -> bytes:
        data = self._serial.read(size)
        if len(data) < size:
            raise TimeoutError(f"timeout reading from {self.port}")
        return data

    def read_some(self, size: int) -> bytes:
        return self._serial.read(size)

    def clear_input(self) -> None:
        self._serial.reset_input_buffer()

    def set_timeout(self, seconds: float) -> None:
        super().set_timeout(seconds)
        if self._serial is not None:
            self._serial.timeout = self.timeout

    def read_until(self, term: bytes) -> bytes:
        data = self._serial.read_until(term)
        if not data.endswith(term):
            raise TimeoutError(f"timeout reading from {self.port}")
        return data


def prologix_escape(data: bytes) -> bytes:
    """Escape the bytes a Prologix-style adapter would otherwise interpret."""
    out = bytearray()
    for byte in data:
        if byte in (0x0A, 0x0D, 0x1B, 0x2B):  # LF, CR, ESC, '+'
            out.append(0x1B)
        out.append(byte)
    return bytes(out)


class PrologixTransport(SerialTransport):
    """GPIB through a Prologix GPIB-USB or AR488 adapter (a plain serial port)."""

    scheme = "prologix"

    def __init__(self, port: str, address, *, baud=115200, eos=0, **options):
        super().__init__(port, baud=baud, **options)
        self.address = str(address)
        self.eos = int(eos)  # what the adapter appends on GPIB: 0=CR+LF 1=CR 2=LF 3=none

    def _adapter(self, text: str) -> None:
        self._serial.write((text + "\n").encode("ascii"))

    def open(self) -> None:
        super().open()
        for line in ("++mode 1", "++auto 0", "++eoi 1", f"++eos {self.eos}",
                     "++eot_enable 0", f"++addr {self.address}"):
            self._adapter(line)

    def write(self, text: str) -> None:
        self.write_raw(text.encode(self.encoding))

    def write_raw(self, data: bytes) -> None:
        self._serial.write(prologix_escape(data) + b"\n")

    def read_until(self, term: bytes) -> bytes:
        self._adapter("++read eoi")
        return super().read_until(term)

    def read_raw(self, size: int) -> bytes:
        """Read a binary reply of known length (the instrument ends it with EOI)."""
        self._adapter("++read eoi")
        return super().read_raw(size)

    def read_some(self, size: int) -> bytes:
        self._adapter("++read eoi")
        return super().read_some(size)


class TcpTransport(Transport):
    scheme = "tcp"

    def __init__(self, host: str, port, **options):
        super().__init__(**options)
        self.host, self.port = host, int(port)
        self._socket = None
        self._buffer = b""

    def open(self) -> None:
        try:
            self._socket = socket.create_connection((self.host, self.port), self.timeout)
        except OSError as exc:
            raise TransportError(f"cannot connect to {self.host}:{self.port}: {exc}") from exc

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def write_raw(self, data: bytes) -> None:
        self._socket.sendall(data)

    def _fill(self) -> None:
        chunk = self._socket.recv(4096)  # raises TimeoutError on timeout
        if not chunk:
            raise TransportError(f"{self.host}:{self.port} closed the connection")
        self._buffer += chunk

    def read_raw(self, size: int) -> bytes:
        while len(self._buffer) < size:
            self._fill()
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def read_some(self, size: int) -> bytes:
        if not self._buffer:
            try:
                self._fill()
            except TimeoutError:
                return b""
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def clear_input(self) -> None:
        self._buffer = b""
        self._socket.settimeout(0)
        try:
            while self._socket.recv(4096):
                pass
        except (BlockingIOError, OSError):
            pass
        finally:
            self._socket.settimeout(self.timeout)

    def set_timeout(self, seconds: float) -> None:
        super().set_timeout(seconds)
        if self._socket is not None:
            self._socket.settimeout(self.timeout)

    def read_until(self, term: bytes) -> bytes:
        while term not in self._buffer:
            self._fill()
        end = self._buffer.index(term) + len(term)
        data, self._buffer = self._buffer[:end], self._buffer[end:]
        return data


class VisaTransport(Transport):
    """Any VISA resource. Vendor GPIB adapters (NI, Keysight) work through this."""

    scheme = "visa"

    def __init__(self, resource: str, *, backend="", **options):
        super().__init__(**options)
        self.resource, self.backend = resource, backend
        self._instrument = None

    def open(self) -> None:
        try:
            import pyvisa
        except ImportError as exc:
            raise TransportError(
                'VISA support is not installed. Install it with: pip install "patchbay[visa]"'
            ) from exc
        backends = [self.backend] if self.backend else ["", "@py"]
        error = None
        for backend in backends:
            try:
                manager = pyvisa.ResourceManager(backend)
                self._instrument = manager.open_resource(self.resource)
                break
            except Exception as exc:  # no vendor VISA, or resource not found
                error = exc
        if self._instrument is None:
            raise TransportError(f"cannot open VISA resource {self.resource}: {error}")
        self._instrument.timeout = self.timeout * 1000
        self._instrument.read_termination = None
        self._instrument.write_termination = None

    def close(self) -> None:
        if self._instrument is not None:
            self._instrument.close()
            self._instrument = None

    def write_raw(self, data: bytes) -> None:
        self._instrument.write_raw(data)

    def read_raw(self, size: int) -> bytes:
        return self._instrument.read_bytes(size)

    def read_some(self, size: int) -> bytes:
        try:
            return self._instrument.read_bytes(size)
        except Exception:  # pyvisa reports a timeout as VisaIOError
            return b""

    def clear_input(self) -> None:
        try:
            self._instrument.clear()
        except Exception:
            pass

    def set_timeout(self, seconds: float) -> None:
        super().set_timeout(seconds)
        if self._instrument is not None:
            self._instrument.timeout = self.timeout * 1000

    def read_until(self, term: bytes) -> bytes:
        return self._instrument.read_raw()


def parse_resource(resource: str) -> tuple[str, str, dict[str, str]]:
    """Split ``scheme://target?key=value`` into its parts."""
    scheme, sep, rest = resource.partition("://")
    if not sep or not scheme:
        raise TransportError(
            f"invalid resource '{resource}' (expected e.g. serial://COM3 or sim://)"
        )
    target, _, query = rest.partition("?")
    return scheme.lower(), target, dict(parse_qsl(query, keep_blank_values=True))


def open_transport(resource: str, simulator: type[SimTransport] | None = None) -> Transport:
    """Create and open the transport a resource string describes."""
    scheme, target, options = parse_resource(resource)
    try:
        if scheme == "sim":
            if simulator is None:
                raise TransportError("this device type has no simulator")
            transport = simulator(**options)
        elif scheme == "serial":
            transport = SerialTransport(target, **options)
        elif scheme == "prologix":
            port, _, address = target.rpartition("/")
            if not port or not address:
                raise TransportError("prologix resource must look like prologix://COM4/5")
            transport = PrologixTransport(port, address, **options)
        elif scheme == "tcp":
            host, _, port = target.rpartition(":")
            if not host or not port:
                raise TransportError("tcp resource must look like tcp://host:port")
            transport = TcpTransport(host, port, **options)
        elif scheme == "visa":
            transport = VisaTransport(target, **options)
        else:
            raise TransportError(f"unknown connection type '{scheme}://'")
    except (TypeError, ValueError) as exc:
        raise TransportError(f"invalid resource '{resource}': {exc}") from exc
    transport.open()
    return transport
