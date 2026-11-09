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
| Timing | No hard timing needs now. The engine stays separate from the UI so a tighter runner can replace it later. |

## Open design questions

- **Hantek DSO 5102P** uses a proprietary USB protocol and needs a WinUSB
  driver swap on Windows; this conflicts with the fast-start goal and needs a
  guided setup step.

## Phases

1. **Engine** (done): plugin loader, transports, Lua runner, simulated
   device, run folders, command line.
2. **Desktop shell** (done): project wizard, device manager with serial port
   list and connection test, Lua editor, run/stop in a separate process,
   console, live plot. Still to do here: automatic device discovery, a data
   table, project templates.
3. **Node editor** (done): nodes generated from plugin commands, flow nodes
   (for, while, if), values (math, compare, logic, variables), logging, graph
   to Lua compiler, errors marked on the node. Still to do here: undo/redo,
   copy/paste, highlighting the running node, comments, sub-graphs.
4. **Front panel designer**: widgets bound to script values and graph pins.
5. **Device plugins**, each with a simulator:
   Arduino line protocol (generic, done) · Kunkin KP184 (Modbus/serial) ·
   HP 6655A · HP 53131A · HP 3478A (pre-SCPI) · Chroma 6404 · Hantek DSO 5102P
6. **Packaging**: portable Windows build, then Linux, then Mac. Plugin install
   from a folder or a registry.

## Not verified yet

- The serial, TCP, Prologix and VISA transports have not been run against
  real hardware. Only the simulator path is covered by tests.
- The desktop app is tested on Windows only, and its dialogs (new project,
  add device) are covered only partly by automated tests.
- The node editor is tested through its functions (add, wire, delete, run),
  not by real mouse input.
