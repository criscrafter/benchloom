# Third-party notices

Benchloom itself is licensed under the GNU Affero General Public License 3.0 or
later (see `LICENSE`). It contains or uses the following works by others.

## Contained in this repository

### hantek-dso5000p-linux (protocol layout)

`src/benchloom/plugins/hantek/protocol.inf`, the layout of the Hantek DSO5000P
settings block, is copied from
[kooscode/hantek-dso5000p-linux](https://github.com/kooscode/hantek-dso5000p-linux).
The Hantek driver also follows that project's protocol notes; no code was copied.

```
MIT License

Copyright (c) 2026 kooscode

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Instrument protocol sources

The Feeltech FY6900 driver was written from the official FY6600 serial protocol
document and from notes in [mattwach/fygen](https://github.com/mattwach/fygen),
[katzenjens/fy6900commander](https://github.com/katzenjens/fy6900commander) and
[tspspi/pyfy6900](https://github.com/tspspi/pyfy6900), all checked on a real
generator. No code was copied from them. The other drivers follow the makers'
manuals.

## Installed alongside (not part of this repository)

These are installed by pip as dependencies. Each keeps its own licence; all
are compatible with the AGPL.

| Package | Used for | Licence |
|---|---|---|
| [lupa](https://github.com/scoder/lupa) (includes Lua 5.4) | running Lua tests | MIT |
| [pyserial](https://github.com/pyserial/pyserial) | serial ports | BSD-3-Clause |
| [PySide6](https://doc.qt.io/qtforpython-6/) (Qt) | the desktop app (`[gui]`) | LGPL-3.0 |
| [pyqtgraph](https://github.com/pyqtgraph/pyqtgraph) | plots (`[gui]`) | MIT |
| [NumPy](https://numpy.org) | needed by pyqtgraph | BSD-3-Clause |
| [PyVISA](https://github.com/pyvisa/pyvisa), [PyVISA-py](https://github.com/pyvisa/pyvisa-py) | VISA instruments (`[visa]`) | MIT |
| [python-usbtmc](https://github.com/python-ivi/python-usbtmc) | USBTMC instruments (`[usb]`) | MIT |
| [PyUSB](https://github.com/pyusb/pyusb) | USB instruments (`[usb]`) | BSD-3-Clause |
| [libusb-package](https://github.com/pyocd/libusb-package) (includes libusb) | the libusb library (`[usb]`) | Apache-2.0 (libusb: LGPL-2.1) |

A packaged build of Benchloom that includes these libraries must ship their
licence texts with it, and must let users replace the LGPL libraries (Qt,
libusb), for example by keeping them as separate files.
