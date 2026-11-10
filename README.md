# Patchbay

Open lab automation. Connect instruments through plugins, then build a test
either by wiring nodes together or by writing a Lua script.

The goal is a short path from "I need to automate this" to a running setup:
one download, no licence, no vendor runtime.

**Status: early prototype.** The engine, command line and a first desktop app
(node editor, Lua editor, device manager, front panel, live plot) work. See
[docs/PLAN.md](docs/PLAN.md) for what is missing.

![A visual test in Patchbay after running the example sweep](docs/images/visual.png)

## Try it

```
python -m venv .venv
.venv\Scripts\pip install -e ".[gui,dev]"    # Linux/Mac: .venv/bin/pip
.venv\Scripts\patchbay gui
```

Choose **New project**, pick **Visual** or **Lua script**, then press **Run**
(F5). The project starts with a simulated power supply and a voltage sweep, so
it runs without hardware. Add real instruments in the Devices panel;
double-click a command there to add it to the test.

Each run gets a folder under the project's `runs/` holding `data.csv`,
`console.log`, `run.json` and a copy of the script.

The same works without the app:

```
patchbay new my-test
patchbay run my-test
patchbay devices             # device types the installed plugins provide
patchbay describe demo.psu   # commands of one device type
```

## A project

`patchbay.toml` lists the instruments; the entry name is the name in the test.

```toml
[project]
name = "my-test"
mode = "lua"          # or "visual"
script = "main.lua"

[devices.psu]
device = "demo.psu"
resource = "sim://"
```

| Resource | Connection |
|---|---|
| `sim://` | the plugin's simulator |
| `serial://COM3?baud=9600` | serial port (`serial:///dev/ttyUSB0` on Linux) |
| `tcp://192.168.1.20:5025` | raw socket |
| `prologix://COM4/5` | GPIB address 5 through a Prologix or AR488 adapter |
| `visa://GPIB0::5::INSTR` | any VISA resource, including vendor GPIB adapters (`pip install "patchbay[visa]"`) |

## A visual test

Nodes are connected by two kinds of wire:

- **Thick wires** say what runs next. Follow them from the Start node.
- **Dashed blue wires** carry values from an output to an input.

Inputs without a wire are typed into the node. Right-click the canvas to add
nodes (loops, conditions, math, logging, and every command of the project's
devices); drag from pin to pin to wire; select and press Delete to remove.

Nodes without thick-wire pins, such as a measurement, run each time a node
that uses their value runs. To measure at a fixed point instead and keep the
value, right-click the node and choose **Run in sequence**.

A visual test is compiled to Lua and run by the same engine as a script.
**View as Lua** shows the generated code; each run folder keeps it as
`graph.lua`.

## A script

```lua
psu:set_voltage(5)
psu:set_output(true)
sleep(0.5)
log { V = psu:measure_voltage(), I = psu:measure_current() }
```

Besides the devices, a script has `sleep(seconds)`, `log{...}` (one row in
`data.csv`), `time()` (seconds since start), `print(...)` and `panel` (the
front panel, below).

## A front panel

![A front panel while its test runs](docs/images/panel.png)

The Front panel tab holds controls you operate while a test runs and
indicators the test updates. Tick **Edit layout**, use **Add**, then drag
widgets into place; double-click one for its name, caption, unit and range.

| Widget | Kind | In a script |
|---|---|---|
| Number input, switch, text input | control | `panel.target` reads the current value |
| Button | control | `panel.stop` is `true` once after each click |
| Value display, indicator light, bar | indicator | `panel.voltage = v` shows a value |
| Chart | indicator | `panel.trend = v` adds a point against time |

```lua
while not panel.stop do
    psu:set_voltage(panel.target)
    panel.voltage = psu:measure_voltage()
    sleep(0.1)
end
```

In a visual test the same widgets appear as nodes under **Front panel** in the
right-click menu. The layout is saved in the project's `panel.json`. Run from
the command line, controls have the values they had when last run in the app.

## Instruments

| Instrument | Device type | Connection |
|---|---|---|
| Kunkin KP184 electronic load | `kunkin.kp184` | serial (RS-232/RS-485) |
| HP 6655A power supply | `hp.6655a` | GPIB |
| HP 3478A multimeter | `hp.3478a` | GPIB |
| Chroma 6404 AC source | `chroma.6404` | GPIB or serial |
| Any SCPI instrument | `generic.scpi` | serial, network, GPIB, VISA |
| Arduino and other line-based serial devices | `generic.serial` | serial, network |

Each comes with a simulator. The drivers have not been tried on real hardware
yet; [docs/INSTRUMENTS.md](docs/INSTRUMENTS.md) has connection settings, options
and the documentation each driver is based on. Sources and loads switch their
output off when a test ends, unless the device entry says
`leave_output_on = true`.

## A plugin

A plugin is a folder with a `plugin.toml` and Python files. Put it in
`~/.patchbay/plugins/` or in a project's `plugins/` folder.
[src/patchbay/plugins/demo](src/patchbay/plugins/demo) is the reference example.

```python
from patchbay import Param, ScpiDevice, command

class MyPsu(ScpiDevice):
    @command("Set the output voltage", params={"volts": Param(unit="V", min=0, max=20)})
    def set_voltage(self, volts: float) -> None:
        self.transport.write(f"VOLT {volts}")

    @command("Measure the output voltage", unit="V")
    def measure_voltage(self) -> float:
        return float(self.transport.query("MEAS:VOLT?"))
```

Types, units and ranges declared here are checked on every call, and are what
the node editor and device panels will be generated from. A plugin can ship a
simulator (a `SimTransport` subclass that answers the instrument's commands) so
tests can be written without the hardware.

Plugins are ordinary Python and run with full access to the computer; only
install ones you trust.

## Tests

```
.venv\Scripts\pytest
```
