# Changelog

## Unreleased

- **`benchloom run` exits with 1 when a check failed**, so scripts and CI
  see a FAIL; it also prints the verdict. Before, only errors and stops gave 1.
- Dialogs (devices, settings, plugins, new project, About) are deleted after
  use instead of staying in memory until the window closes.
- **Column order:** `columns = [...]` under `[plot]` in benchloom.toml sets the
  order of the logged columns in data.csv, the plot, the Data tab and the
  report. A visual test keeps the order of its Log row node by itself.

## 0.1.4 – Data table, panel widgets, plugin registry and docs

- **Data table:** a Data tab next to the plot lists every logged row as it
  arrives. It follows the newest row unless you scroll up, and Ctrl+C copies
  the selected cells with full precision, ready to paste into a spreadsheet.
- **Running node:** while a visual test runs, a green outline shows the node
  it is at, so a long loop or a slow instrument is easy to follow.
- **Front panel:** a gauge and a table widget, and charts with several
  curves (`panel.trend = { V = v, I = i }`), their X value taken from the
  table (for example a frequency) and a log X axis. Renaming a widget renames
  the nodes that use it, and offers to change the script.
- **Plugin registry:** Plugins → Browse online lists plugins others wrote
  (`registry/index.json` in this repository, added to by pull request) and
  installs or updates them; a download whose SHA-256 differs from the list is
  refused. On the command line: `benchloom plugins search`, `benchloom plugins
  install <id>`, and `benchloom plugins entry` to make the entry for your own.
- **Documentation site** at <https://criscrafter.github.io/benchloom/>, built
  from `docs/`: getting started, projects and resources, the Lua reference,
  visual tests, the front panel, the command line, instruments, writing and
  sharing plugins, and safety.
- The front panel's widget settings dialog no longer stays behind in memory
  after each edit.

## 0.1.3 – Safer stops, plugin consent and PyPI

- **Plugins in a project need your consent:** a project's own `plugins/` folder
  is Python code, and it used to be loaded as soon as the project was opened.
  Now the app asks first, per project, and `benchloom run` needs
  `--trust-plugins` once. Until then such a plugin does not replace a built-in
  one either.
- **Stopping switches outputs off more reliably:** when a script does not stop
  within 3 s (a loop that never checks, a slow command), the run now shuts its
  devices down without the script instead of being killed with the outputs on.
  Shutting down is no longer cut short after 3 s, and closing the window
  while a test runs waits for it too.
- The app no longer shows the commands of another project's plugin after
  switching between projects that carry a plugin with the same id.
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
