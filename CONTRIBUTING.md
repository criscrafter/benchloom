# Contributing to Benchloom

Thank you for helping. Bug reports, instrument drivers, fixes and documentation
are all welcome.

## Reporting a bug or asking for an instrument

Open an issue and pick a template. For a bug, say what you did, what you
expected and what happened, and attach the run folder's `console.log` (and
`run.json`) if a test run went wrong. For an instrument, name the model, how it
connects (serial, USB, GPIB, LAN) and link its programming manual if you have
it.

## Setting up

```
git clone https://github.com/criscrafter/benchloom
cd benchloom
python -m venv .venv
.venv\Scripts\pip install -e ".[gui,dev]"      # Linux/Mac: .venv/bin/pip
.venv\Scripts\pytest
```

Python 3.11 or newer. The tests use the instruments' simulators, so no hardware
is needed; the desktop app is tested offscreen.

## Building the Windows download

```
.venv\Scripts\pip install -e ".[gui,usb]" pyinstaller
.venv\Scripts\python packaging\build_windows.py
```

This makes `build/Benchloom-<version>-windows-x64.zip` and runs a self-test of
the packaged app first. Pushing a version tag (`v1.2.3`) builds it on GitHub and
attaches it to the release.

## Writing an instrument plugin

A plugin is a folder with a `plugin.toml` and a Python driver. The README
section "A plugin" shows the format, and `src/benchloom/plugins/demo` is the
reference example. For a driver to be merged it needs:

- **A simulator** that answers like the real instrument (`simulator = ...` in
  `plugin.toml`), so the driver is tested without hardware, and tests in
  `tests/` that use it.
- **Output safety:** sources and loads switch their output off in `close()`
  unless the device entry says `leave_output_on = true`.
- **Honest notes** in `docs/INSTRUMENTS.md`: what was tried on real hardware
  (model, firmware, connection) and what was only written from the manual.
- **Checked settings:** when an instrument silently clamps or ignores a value,
  read it back and raise a `CommandError` that says what happened.

## Code style

- Readable over clever; comments say why, not what.
- Error messages tell the user what to do ("is the channel on and the scope
  running?"), in plain words.
- New files start with the licence header:
  ```
  # SPDX-License-Identifier: AGPL-3.0-or-later
  # Copyright (C) 2026 <your name>
  ```
- Run `pytest` before you open a pull request. CI runs it on Windows and Linux.

## The contributor agreement

Benchloom is open source under the AGPL-3.0, and the maintainer also offers
commercial licences to companies that cannot use the AGPL. That only works if
the maintainer may license every part of Benchloom, including contributions,
on both terms.

So every pull request needs agreement to the **[Contributor License
Agreement](CLA.md)**: you keep the copyright of your contribution and grant a
licence to use it, including under other licences. Tick the box in the pull
request template to agree. Your contribution stays available under the AGPL in
any case.

Small fixes of typos or wording need no agreement.
