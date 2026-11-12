# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
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

    def device_clear(self) -> None:
        """Send the GPIB/USBTMC device clear, which aborts a measurement in progress.

        Transports without such a signal (serial, TCP) do nothing.
        """

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
        self.lines: dict[str, bool] = {}  # DTR/RTS levels set with set_lines
        self._serial = None

    def open(self) -> None:
        import serial

        try:
            self._serial = serial.Serial(self.port, timeout=self.timeout, **self.serial_options)
        except serial.SerialException as exc:
            # Some USB serial drivers (seen with the CH340 on Windows) refuse to apply
            # settings identical to the ones the port was last closed with. Opening
            # at another baud rate and then switching works around it.
            try:
                options = dict(self.serial_options, baudrate=self._other_baud())
                self._serial = serial.Serial(self.port, timeout=self.timeout, **options)
                self._serial.baudrate = self.serial_options["baudrate"]
            except serial.SerialException:
                self.close()
                raise TransportError(f"cannot open serial port {self.port}: {exc}") from exc
        if self.lines:
            self.set_lines()

    def configure(self, *, baud=None, bytesize=None, parity=None, stopbits=None) -> None:
        """Change the port settings, also while open. For instruments whose serial
        settings are fixed, so the driver can set them whatever the resource says."""
        changes = {"baudrate": baud, "bytesize": bytesize,
                   "parity": None if parity is None else str(parity).upper()[:1],
                   "stopbits": stopbits}
        changed = False
        for key, value in changes.items():
            if value is not None and self.serial_options.get(key) != value:
                self.serial_options[key] = value
                changed = True
        if changed and self._serial is not None:
            import serial

            try:
                # all at once: one reconfiguration of the port instead of one per setting
                self._serial.apply_settings({k: v for k, v in self.serial_options.items()
                                             if k != "rtscts"})
            except serial.SerialException:
                # the CH340 quirk described in open(): change the baud rate away and back
                self._reconfigure()

    def set_lines(self, *, dtr: bool | None = None, rts: bool | None = None) -> None:
        """Set the DTR and RTS output lines, also while open (kept for the next open).

        For cables that take their power from these lines, such as the optical
        receivers of handheld multimeters."""
        if dtr is not None:
            self.lines["dtr"] = bool(dtr)
        if rts is not None:
            self.lines["rts"] = bool(rts)
        if self._serial is not None:
            for name, value in self.lines.items():
                setattr(self._serial, name, value)

    def _other_baud(self) -> int:
        return 19200 if self.serial_options["baudrate"] != 19200 else 9600

    def _reconfigure(self) -> None:
        """Re-apply the port settings by changing the baud rate away and back."""
        self._serial.baudrate = self._other_baud()
        self._serial.baudrate = self.serial_options["baudrate"]

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
            import serial

            try:
                self._serial.timeout = self.timeout
            except serial.SerialException:
                # The timeout is stored before the driver rejects the settings.
                self._reconfigure()

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

    def device_clear(self) -> None:
        self._adapter("++clr")

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
                'VISA support is not installed. Install it with: pip install "benchloom[visa]"'
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

    def device_clear(self) -> None:
        self._instrument.clear()

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


USBGPIB_IDS = (0x03EB, 0x2065)  # xyphro UsbGpib adapter


class UsbTmcTransport(Transport):
    """A USBTMC instrument or USB-GPIB adapter, without a vendor VISA.

    Resource: ``usbtmc://03EB:2065`` (the first such device) or
    ``usbtmc://03EB:2065/<serial>``; ``usbgpib://`` is short for the xyphro UsbGpib
    adapter (``usbgpib://`` or ``usbgpib://<serial>``). Needs pyusb and a libusb
    backend (``pip install "benchloom[usb]"``); on Windows the device must use the
    WinUSB driver, which Zadig installs. With a vendor VISA installed instead, use
    ``visa://USB0::0x03EB::0x2065::<serial>::INSTR``.

    One USBTMC message is one reply, so :meth:`read` needs no termination
    character; the UsbGpib adapter ends a GPIB reply on EOI.
    """

    scheme = "usbtmc"

    def __init__(self, target: str, **options):
        super().__init__(**options)
        ids, _, self.serial = target.partition("/")
        vid, _, pid = ids.partition(":")
        try:
            self.vid, self.pid = int(vid, 16), int(pid, 16)
        except ValueError:
            raise TransportError("usbtmc resource must look like usbtmc://03EB:2065 or "
                                 "usbtmc://03EB:2065/<serial>") from None
        self._instrument = None
        self._buffer = bytearray()

    @property
    def name(self) -> str:
        return f"USB device {self.vid:04X}:{self.pid:04X}"

    def open(self) -> None:
        try:
            import usb.core
            import usbtmc
        except ImportError as exc:
            raise TransportError(
                'USB instrument support is not installed. Install it with: '
                'pip install "benchloom[usb]"') from exc
        backend = libusb_backend()
        if backend is None:
            raise TransportError("libusb was not found; install it with: pip install libusb-package")
        devices = list(usb.core.find(find_all=True, idVendor=self.vid, idProduct=self.pid,
                                     backend=backend))
        if not devices:
            hint = (" A UsbGpib adapter only appears while a powered instrument is connected "
                    "to it." if (self.vid, self.pid) == USBGPIB_IDS else "")
            raise TransportError(f"{self.name} not found.{hint}")
        chosen = None
        for device in devices:
            try:
                serial = device.serial_number or ""
            except (ValueError, usb.core.USBError, NotImplementedError):
                raise TransportError(
                    f"{self.name} is connected but cannot be opened. On Windows it needs the "
                    "WinUSB driver (install it once with Zadig), or use a VISA such as R&S "
                    "VISA with a visa:// resource.") from None
            if not self.serial or serial == self.serial:
                chosen = device
                break
        if chosen is None:
            raise TransportError(f"{self.name} with serial number {self.serial} not found")
        try:
            self._instrument = usbtmc.Instrument(chosen)
            self._instrument.timeout = self.timeout
            self._instrument.open()
        except Exception as exc:
            self._instrument = None
            raise TransportError(f"cannot open {self.name}: {exc}") from exc

    def close(self) -> None:
        if self._instrument is not None:
            try:
                self._instrument.close()
            finally:
                self._instrument = None

    def set_timeout(self, seconds: float) -> None:
        super().set_timeout(seconds)
        if self._instrument is not None:
            self._instrument.timeout = self.timeout

    def write_raw(self, data: bytes) -> None:
        self._instrument.write_raw(bytes(data))

    def _message(self) -> bytes:
        try:
            return self._instrument.read_raw()
        except Exception as exc:  # pyusb reports a timeout as USBTimeoutError
            text = str(exc).lower()
            if "timeout" in text or "timed out" in text:
                raise TimeoutError(f"timeout reading from {self.name}") from exc
            raise

    def read(self) -> str:
        data = bytes(self._buffer) or self._message()
        self._buffer.clear()
        return data.decode(self.encoding).rstrip("\r\n")

    def read_raw(self, size: int) -> bytes:
        while len(self._buffer) < size:
            self._buffer += self._message()
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def read_some(self, size: int) -> bytes:
        try:
            return self.read_raw(size)
        except TimeoutError:
            data = bytes(self._buffer)
            self._buffer.clear()
            return data

    def read_until(self, term: bytes) -> bytes:
        while term not in self._buffer:
            self._buffer += self._message()
        end = self._buffer.index(term) + len(term)
        data = bytes(self._buffer[:end])
        del self._buffer[:end]
        return data

    def clear_input(self) -> None:
        self._buffer.clear()

    def device_clear(self) -> None:
        self._buffer.clear()
        self._instrument.clear()

    def adapter_command(self, text: str) -> str | None:
        """Send one of the UsbGpib adapter's own '!' commands; return its reply if any.

        The adapter takes them after a USBTMC indicator-pulse request, for example
        ``!autoid off`` for instruments that do not understand ``*IDN?``.
        """
        self._instrument.pulse()
        self._instrument.write(text)
        return self._instrument.read().strip() if text.endswith("?") else None


class UsbBulkTransport(Transport):
    """A USB device with its own bulk protocol, through libusb (``usb://049F:505A``).

    Writes go out as one bulk transfer each; reads return what one or more bulk
    transfers bring. On Windows the device needs the WinUSB driver (Zadig). Needs
    pyusb and libusb-package (``pip install "benchloom[usb]"``).
    """

    scheme = "usb"

    def __init__(self, target: str, interface="0", **options):
        super().__init__(**options)
        vid, _, pid = target.partition(":")
        try:
            self.vid, self.pid = int(vid, 16), int(pid, 16)
        except ValueError:
            raise TransportError("usb resource must look like usb://049F:505A") from None
        self.interface = int(interface)
        self._device = None
        self._ep_in = self._ep_out = None
        self._buffer = bytearray()

    @property
    def name(self) -> str:
        return f"USB device {self.vid:04X}:{self.pid:04X}"

    def open(self) -> None:
        try:
            import usb.core
            import usb.util
        except ImportError as exc:
            raise TransportError('USB support is not installed. Install it with: '
                                 'pip install "benchloom[usb]"') from exc
        backend = libusb_backend()
        if backend is None:
            raise TransportError("libusb was not found; install it with: pip install libusb-package")
        device = usb.core.find(idVendor=self.vid, idProduct=self.pid, backend=backend)
        if device is None:
            raise TransportError(f"{self.name} not found")
        try:
            try:
                if device.is_kernel_driver_active(self.interface):
                    device.detach_kernel_driver(self.interface)
            except (NotImplementedError, usb.core.USBError):
                pass  # Windows has no kernel drivers to detach
            try:
                config = device.get_active_configuration()
            except usb.core.USBError:
                config = None
            if config is None:  # configuring an already configured device may reset it
                device.set_configuration()
                config = device.get_active_configuration()
            usb.util.claim_interface(device, self.interface)
        except (usb.core.USBError, NotImplementedError, ValueError) as exc:
            raise TransportError(
                f"{self.name} is connected but cannot be opened ({exc}). On Windows it needs "
                "the WinUSB driver: install it once with Zadig.") from None
        iface = config[(self.interface, 0)]
        for ep in iface:
            if usb.util.endpoint_type(ep.bmAttributes) != usb.util.ENDPOINT_TYPE_BULK:
                continue
            if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                self._ep_in = self._ep_in or ep
            else:
                self._ep_out = self._ep_out or ep
        if self._ep_in is None or self._ep_out is None:
            raise TransportError(f"{self.name} has no bulk endpoints on interface {self.interface}")
        self._device = device

    def close(self) -> None:
        if self._device is not None:
            import usb.util

            try:
                usb.util.release_interface(self._device, self.interface)
            except Exception:
                pass
            usb.util.dispose_resources(self._device)
            self._device = None

    def write_raw(self, data: bytes) -> None:
        written = self._ep_out.write(bytes(data), int(self.timeout * 1000))
        if written != len(data):
            raise TransportError(f"short write to {self.name}: {written} of {len(data)} bytes")

    def _transfer(self, timeout: float) -> bytes:
        import usb.core

        try:
            return bytes(self._ep_in.read(65536, max(1, int(timeout * 1000))))
        except usb.core.USBTimeoutError:
            return b""
        except usb.core.USBError as exc:
            if "timed out" in str(exc).lower():
                return b""
            raise

    def read_some(self, size: int) -> bytes:
        if not self._buffer:
            self._buffer += self._transfer(self.timeout)
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def read_raw(self, size: int) -> bytes:
        import time

        deadline = time.monotonic() + self.timeout
        while len(self._buffer) < size:
            left = deadline - time.monotonic()
            chunk = self._transfer(left) if left > 0 else b""
            if not chunk:
                raise TimeoutError(f"timeout reading from {self.name}")
            self._buffer += chunk
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def clear_input(self) -> None:
        self._buffer.clear()
        while self._transfer(0.05):
            pass


def find_usbtmc(vid: int | None = None, pid: int | None = None) -> list[tuple[int, int, str]]:
    """USBTMC devices libusb can see, as (vendor id, product id, serial number)."""
    try:
        import usb.core
    except ImportError:
        return []
    backend = libusb_backend()
    if backend is None:
        return []
    found = []
    for device in usb.core.find(find_all=True, backend=backend):
        if (vid is not None and device.idVendor != vid) or (pid is not None and device.idProduct != pid):
            continue
        if not _is_usbtmc(device):
            continue
        try:
            serial = device.serial_number or ""
        except (ValueError, usb.core.USBError, NotImplementedError):
            serial = ""  # no WinUSB driver bound, or no permission
        found.append((device.idVendor, device.idProduct, serial))
    return found


def libusb_backend():
    """A libusb backend for pyusb, or None; prefers the library libusb-package ships."""
    try:
        import libusb_package

        return libusb_package.get_libusb1_backend()
    except ImportError:
        import usb.backend.libusb1

        return usb.backend.libusb1.get_backend()


def _is_usbtmc(device) -> bool:
    try:
        return any(iface.bInterfaceClass == 0xFE and iface.bInterfaceSubClass == 0x03
                   for cfg in device for iface in cfg)
    except Exception:
        return False


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
        elif scheme == "usb":
            transport = UsbBulkTransport(target, **options)
        elif scheme == "usbtmc":
            transport = UsbTmcTransport(target, **options)
        elif scheme == "usbgpib":
            ids = f"{USBGPIB_IDS[0]:04X}:{USBGPIB_IDS[1]:04X}"
            transport = UsbTmcTransport(f"{ids}/{target}" if target else ids, **options)
        else:
            raise TransportError(f"unknown connection type '{scheme}://'")
    except (TypeError, ValueError) as exc:
        raise TransportError(f"invalid resource '{resource}': {exc}") from exc
    transport.open()
    return transport
