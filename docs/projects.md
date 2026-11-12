# Projects

A project is a folder. It holds:

| File | Holds |
|---|---|
| `benchloom.toml` | the project: its name, mode, script and instruments |
| `main.lua` or `main.graph.json` | the test: a Lua script, or the nodes of a visual test |
| `panel.json` | the [front panel](front-panel.md), if the test has one |
| `plugins/` | optional: plugins that travel with the project ([see below](#plugins-in-a-project)) |
| `runs/` | one folder per run ([what is in it](getting-started.md#what-a-run-leaves-behind)) |

A project can be copied, zipped or put under version control like any folder;
leave `runs/` out if you do not want the results to travel with it.

## The project file

```toml
[project]
name = "my-test"
mode = "lua"          # or "visual"; fixed when the project is created
script = "main.lua"

[devices.psu]         # the name the test uses for this instrument
device = "demo.psu"   # the device type: plugin id and device id
resource = "sim://"   # where it is connected

[devices.eload]
device = "kunkin.kp184"
resource = "serial://COM5?baud=9600"
address = 1           # a setting the device type declares
leave_output_on = false

[plot]                # optional: how the plot starts out
x = "set_V"           # the column for the X axis
log_x = false         # a log scale for X, for frequency sweeps
right = ["I"]         # columns on the right-hand scale
hide = ["time_s"]     # columns not drawn
columns = ["set_V", "V", "I"]  # the order of the logged columns
```

The app writes this file; editing it by hand works too. A device name must be a
valid Lua name (letters, digits and underscores, not starting with a digit) and
must not hide something a script needs, such as `print` or `panel`.

`benchloom devices` lists the device types, and `benchloom describe demo.psu`
the commands of one.

## Resources

The resource says how Benchloom reaches the instrument.

| Resource | Connection |
|---|---|
| `sim://` | the plugin's simulator: no hardware |
| `serial://COM3?baud=9600` | a serial port (`serial:///dev/ttyUSB0` on Linux) |
| `tcp://192.168.1.20:5025` | a raw network socket (LAN instruments, port 5025 for most SCPI ones) |
| `prologix://COM4/5` | GPIB address 5 through a Prologix or AR488 USB adapter |
| `usbgpib://` | the xyphro UsbGpib adapter, without a vendor VISA; `usbgpib://<serial>` picks one of several |
| `usbtmc://03EB:2065` | a USBTMC instrument by USB vendor and product id; `/<serial>` picks one |
| `usb://049F:505A` | a USB instrument with its own protocol, such as the Hantek scope |
| `visa://GPIB0::5::INSTR` | anything VISA can open, including vendor GPIB adapters |

Options go after a `?`, joined with `&`:

| Option | For | Meaning |
|---|---|---|
| `timeout` | all | seconds to wait for a reply (default 2) |
| `term` | text instruments | line ending in both directions, such as `term=\r\n` |
| `write_term`, `read_term` | text instruments | the line ending of one direction |
| `baud`, `bytesize`, `parity`, `stopbits` | serial | port settings; `parity` is N, E or O |
| `rtscts=1` | serial | hardware handshake |

For example `serial://COM3?baud=115200&term=\r\n&timeout=5`.

`usb`, `usbtmc` and `usbgpib` need the `usb` extra, and on Windows the device
must use the WinUSB driver, which [Zadig](https://zadig.akeo.ie/) installs.
`visa` needs the `visa` extra; it uses a vendor VISA (NI-VISA, Keysight IO
Libraries) when one is installed, and pyvisa-py otherwise.

## Device settings

Some device types declare settings, such as a bus address; the Add device
dialog shows them as fields, and they are stored in the device entry. Two
are common:

- `leave_output_on = true`: sources and loads normally switch their output
  off when a test ends or is stopped. This keeps it on, for example for a
  supply that powers the rest of the bench.
- `check_errors = false`: SCPI drivers ask the instrument for errors after
  each setting, so a rejected command fails at the line that sent it. This
  turns that off, for instruments with an unusual error queue.

[Instruments](INSTRUMENTS.md) lists the settings of each instrument.

## The plot

The plot draws every logged column live, against the column chosen as X axis
(time by default). **Right axis** puts columns of very different size on a
second scale, such as 230 V and 10 W; **Columns** hides curves; **Log X** puts
the X axis on a log scale. The `[plot]` section sets how the plot starts out
when the project is opened; changes made in the plot are remembered per
project on this computer. The report's chart follows the `[plot]` section.

`columns` sets the order of the logged columns in `data.csv`, the plot, the
**Data** tab and the report: the ones listed first, in that order, then any
others by name. A Lua table has no order of its own, so without it the columns
are sorted by name. A visual test keeps the order of its **Log row** columns
by itself.

## Plugins in a project

A project can carry its own plugins in a `plugins/` folder, for an instrument
only it uses, or a changed copy of a driver: a plugin there replaces an
installed or built-in one with the same id.

Those plugins are Python code that arrives with the project, so they are
loaded only after you allow it for that project: the app asks when the project
is opened, and on the command line `benchloom run my-test --trust-plugins`
does it once. Until then the project runs without them.

## Projects from before the rename

Benchloom was called Patchbay during its development. A project with a
`patchbay.toml` opens and runs as before.
