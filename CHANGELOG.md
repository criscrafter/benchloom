# Changelog

## Unreleased

- **On PyPI:** `pip install "benchloom[gui,usb]"` installs Benchloom; every
  release is uploaded there automatically.

## 0.1.2 – Lua sandbox

- **Lua sandbox:** scripts no longer reach files, other programs or other code
  on the computer (`io`, `os.execute` and the rest of `os` except `time`,
  `clock`, `date` and `difftime`, `require`, `dofile`, `loadfile`, `package`,
  `debug`, `collectgarbage`, `string.dump`; `load` takes text only), and use at
  most 1 GB of memory. Scripts that read or wrote files themselves need
  changing.
- CONTRIBUTING: the build command for the Windows download was garbled.

## 0.1.1 – Windows download

- **Windows download:** `Benchloom-0.1.1-windows-x64.zip` on the releases page.
  Unzip and start `Benchloom.exe`; no Python needed. `benchloom-cli.exe` is the
  command line.
- The app has an About dialog and `benchloom --version` with the licence and
  warranty notices.
- Waveform PNGs also work in the packaged app, and show text when Qt runs
  offscreen on Windows.
- CI runs on Windows and Linux; the Linux test run no longer fails after the
  tests have passed (a PySide6 shutdown fault).

## 0.1.0 – first public release

Benchloom was developed as "Patchbay" until this release. Projects with a
`patchbay.toml`, the `~/.patchbay` folder and the desktop app's settings from
that time keep working.

- **Tests** as Lua scripts or as visual graphs (nodes and wires, compiled to
  Lua), run in a separate process that can always be stopped.
- **Desktop app:** project wizard with templates, device manager with serial
  port list and connection test, Lua editor, node editor (undo/redo,
  copy/paste), live plot (right-hand scale, log X, column choice), front panel
  with controls and indicators, waveform viewer, plugin manager, settings,
  light and dark theme.
- **Run folders** with `data.csv`, console log, `run.json`, a copy of the
  script, pass/fail checks (`check`), saved waveforms (`save_waveform`, as CSV,
  SVG and PNG) and `report.html` with the verdict, data chart and waveforms.
- **Instruments:** Kunkin KP184 electronic load (RS-232 and RS-485), Chroma 6404
  AC source, Hantek DSO5102P oscilloscope (USB, including gain/phase
  measurement), Feeltech FY6900 function generator (signal, modulation, sweep,
  counter), HP 6655A, HP 3478A and HP 53131A (GPIB, from their manuals), a
  generic SCPI driver and an Arduino line protocol. Each has a simulator.
- **Connections:** serial, TCP, Prologix and AR488 GPIB adapters, VISA, USB
  bulk and USBTMC (including the xyphro UsbGpib adapter) without a vendor VISA.
- **Templates:** a power supply example and a Bode plot (FY6900 and DSO5102P)
  that finds the -3 dB points and can check them against limits.
- **Plugins:** installable from a folder or a .zip, can be switched off,
  `benchloom plugins` on the command line.
