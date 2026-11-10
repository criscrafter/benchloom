# Patchbay plan

## Goals

- **Fast start.** From download to a running setup in minutes: one portable
  download per OS, no licence, no account, no vendor runtime required.
- **Two ways to write a test**, chosen when a project is created: wires
  between nodes (visual) or a Lua script.
- **Plugins** for instruments, written in Python by anyone.
- **Front panels** with live plots, indicators and controls.
- Windows first; Linux and Mac supported.
- Open source. Licence to be chosen before the first public release.

## Decisions

| Topic | Decision |
|---|---|
| Application language | Python 3.11+ |
| UI | Qt (PySide6), native, no browser; pyqtgraph for plots |
| Test scripting | Lua 5.4, embedded with `lupa` |
| Visual tests | Node graph saved as JSON; the graph is the source of truth for visual projects |
| Wire semantics | Two wire kinds: run order ("exec") and values ("data"). Nodes without exec pins are evaluated on demand each time their value is used; a measurement can be switched to "run in sequence" to fix when it happens. |
| Running graphs | Graphs compile to Lua and run on the same engine as scripts; a line map ties runtime errors back to nodes |
| Project mode | `lua` or `visual`, fixed per project. No two-way sync between them. A one-way "export graph as Lua" is possible later. |
| Plugins | Folder with `plugin.toml` + Python driver; commands declared with `@command` carry types, units and ranges |
| Instrument I/O | Core provides transports (serial, TCP, Prologix/AR488 GPIB, VISA). Vendor VISA and GPIB adapters are optional, never required. |
| Simulation | Plugins ship wire-level simulators so driver code is exercised without hardware |
| Data | Every run gets a folder: `data.csv`, console log, script copy, metadata. HDF5 to be added when waveform capture arrives. |
| Front panel | Layout in `panel.json`; widgets addressed by name. Control changes are sent to the running test's process, indicator updates come back on the event queue. A button reads true once per click. |
| Output safety | Drivers of sources and loads switch their output off when a test ends (`Device.close`), unless the device entry says `leave_output_on = true`. |
| Project templates | Folders under `src/patchbay/templates`: a finished project plus `template.toml` (title, description, mode). Templates start on simulators; a simulated bench (`simbench`) lets a simulated scope see a simulated generator, so a wired-up test runs without hardware. A project's `[plot]` section sets how the plot starts out. |
| Plugin management | Plugin manager window and `patchbay plugins`: switch off, install from a folder or .zip into `~/.patchbay/plugins`, remove. Switched-off plugins and extra plugin folders live in `~/.patchbay/config.json` so runs and the command line see them too; desktop-only settings stay in QSettings. Nothing in a plugin runs at install time. |
| Device names | Must not shadow Lua keywords, Lua's standard globals or Patchbay's own (`print`, `load`, `panel`, …). |
| Timing | No hard timing needs now. The engine stays separate from the UI so a tighter runner can replace it later. |

## Open design questions

- **Hantek DSO 5102P** uses a proprietary USB protocol and needs a WinUSB
  driver swap on Windows (Zadig); this conflicts with the fast-start goal and
  needs a guided setup step. The same goes for the UsbGpib adapter without a
  VISA.

## Phases

1. **Engine** (done): plugin loader, transports, Lua runner, simulated
   device, run folders, command line.
2. **Desktop shell** (done): project wizard, device manager with serial port
   list and connection test, Lua editor, run/stop in a separate process,
   console, live plot (with a log X axis), plugin manager, Settings window,
   project templates (the example, and a Bode plot for an FY6900 and a
   DSO5102P). Still to do here: automatic device discovery, a data table, more
   templates.
3. **Node editor** (done): nodes generated from plugin commands, flow nodes
   (for, while, if), values (math, compare, logic, variables), logging, graph
   to Lua compiler, errors marked on the node. Still to do here: undo/redo,
   copy/paste, highlighting the running node, comments, sub-graphs.
4. **Front panel** (done): controls (number, switch, button, text) and
   indicators (value, light, bar, chart) laid out by drag and drop, live in both
   directions while a test runs, available as `panel.<name>` in Lua and as nodes.
   Still to do here: knobs, gauges and tables, multi-series charts, renaming a
   widget without breaking the nodes that use it, a full-window panel mode.
5. **Device plugins**, each with a simulator. Done: Arduino line protocol
   (generic) · Kunkin KP184 (Modbus/serial, RS-485) · HP 6655A · HP 3478A
   (pre-SCPI) · HP 53131A · Chroma 6404 · Hantek DSO 5102P (USB) · Feeltech
   FY6900. GPIB through the xyphro UsbGpib adapter (USBTMC) without a vendor
   VISA. Tried on hardware: KP184, 6404, DSO5102P and FY6900. See [INSTRUMENTS.md](INSTRUMENTS.md).
6. **Packaging**: portable Windows build, then Linux, then Mac. Plugin install
   from a registry (install from a folder or .zip is done).

## Not verified yet

- The serial transport has been used with a real KP184 (CH340 adapter, Windows)
  and a real FY6900 (115200 baud 8N2).
  The USB transport has been used with a real DSO5102P. The TCP, Prologix, VISA
  and USBTMC transports have not been run against real hardware.
- The KP184 driver has been tried on a real load, up to 6 A and 130 W, and the
  6404 driver on a real source with a 10 W lamp. The 6655A, 3478A and 53131A
  drivers are written from manuals and are each tested against a simulator
  written from the same sources, so a misread manual would not show up in the
  tests.
- The desktop app is tested on Windows only, and its dialogs (new project,
  add device) are covered only partly by automated tests.
- The node editor is tested through its functions (add, wire, delete, run),
  not by real mouse input.
