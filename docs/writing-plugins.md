# Writing a plugin

A plugin teaches Benchloom an instrument. It is a folder with a `plugin.toml`
and Python files: a **driver** that talks to the instrument, and usually a
**simulator** that answers like it, so tests can be written without hardware.
From the driver's commands, Benchloom makes the Lua functions, the nodes of
the node editor and the entries in the Devices panel.

The built-in [demo plugin](https://github.com/criscrafter/benchloom/tree/main/src/benchloom/plugins/demo)
is the reference example, and the other built-in plugins show real
instruments: SCPI over GPIB, Modbus over RS-485, a binary USB protocol.

## A first plugin

```
acme/
├── plugin.toml
├── driver.py
└── simulator.py
```

**plugin.toml**

```toml
[plugin]
id = "acme"                     # unique; device types become acme.<device id>
name = "ACME instruments"
version = "1.0.0"
description = "ACME bench supplies."

[[device]]
id = "ps3005"
name = "ACME PS-3005 power supply"
description = "30 V / 5 A bench supply, SCPI over USB serial."
driver = "driver:Ps3005"        # module:class, relative to the plugin folder
simulator = "simulator:Ps3005Sim"
transports = ["serial", "sim"]
default_name = "psu"            # suggested name in a project
default_resource = "serial://COM3?baud=9600"
```

**driver.py**

```python
from benchloom import Param, ScpiDevice, command


class Ps3005(ScpiDevice):
    @command("Set the output voltage", params={"volts": Param(unit="V", min=0, max=30)})
    def set_voltage(self, volts: float) -> None:
        self.send(f"VOLT {volts:.3f}")

    @command("Switch the output on or off")
    def set_output(self, on: bool) -> None:
        self.send("OUTP ON" if on else "OUTP OFF")

    @command("Measure the output voltage", unit="V")
    def measure_voltage(self) -> float:
        return self.ask_number("MEAS:VOLT?")

    def close(self) -> None:
        if not self.leave_output_on():
            self.send("OUTP OFF")   # a source switches its output off after a test
```

**simulator.py**

```python
from benchloom.scpi import ScpiSimulator, sim_bool, sim_number


class Ps3005Sim(ScpiSimulator):
    identity = "ACME,PS-3005,SIM,1.0"

    def reset(self) -> None:
        self.volts, self.on = 0.0, False

    def routes(self):
        return [
            (r"VOLT", self._set_volts),
            (r"OUTP", self._set_output),
            (r"MEAS:VOLT\?", lambda arg: f"{self.volts if self.on else 0.0:.3f}"),
        ]

    def _set_volts(self, arg):
        self.volts = sim_number(arg, 0, 30)

    def _set_output(self, arg):
        self.on = sim_bool(arg)
```

Put the `acme` folder into Benchloom with **Plugins → Install from folder…**
(or `benchloom plugins install acme`), add the device with resource `sim://`,
and a script can use it:

```lua
psu:set_voltage(5)
psu:set_output(true)
print(psu:measure_voltage())
```

## The manifest

| Key | Under | Meaning |
|---|---|---|
| `id` | `[plugin]` | the plugin's id, a short lower-case name; must be unique |
| `name`, `version`, `description` | `[plugin]` | shown in the plugin manager |
| `id` | `[[device]]` | the device's id; the device type is `<plugin id>.<device id>` |
| `name`, `description` | `[[device]]` | shown in the Add device dialog |
| `driver` | `[[device]]` | `module:Class` of the driver |
| `simulator` | `[[device]]` | `module:Class` of the simulator, used for `sim://` |
| `transports` | `[[device]]` | the connections it supports, such as `["serial", "tcp", "visa", "sim"]` |
| `default_name`, `default_resource` | `[[device]]` | offered when the device is added |

### Settings

A device can declare settings, such as a bus address. The Add device dialog
shows them as fields, they are saved in the project's device entry, and the
driver reads them from `self.settings`:

```toml
[[device.setting]]
name = "address"
label = "Modbus address"
type = "int"          # int, float, bool or text
default = 1
min = 1
max = 247
help = "Set on the instrument."
```

```python
address = self.settings.get("address", 1)
```

## Commands

A driver is a class derived from `Device`, or from `ScpiDevice` for SCPI
instruments. Every method marked with `@command` becomes a command:

```python
@command("Set the current limit", params={"amps": Param(unit="A", min=0, max=5)})
def set_current_limit(self, amps: float) -> None: ...

@command("Measure the output current", unit="A")   # unit of the result
def measure_current(self) -> float: ...

@command("Select the input", params={"source": Param(choices=("front", "rear"))})
def set_input(self, source: str = "front") -> None: ...   # optional, with a default
```

- **Types** come from the annotations: `float` (a number), `int`, `bool`,
  `str`, `list`. Benchloom checks and converts every argument before the method
  runs, so a script passing text for a number, or 40 V to a 30 V supply, stops
  with a clear error at its own line, and nothing is sent.
- **`Param`** adds `unit`, `min`, `max`, `choices` and `doc` to an argument.
- **Results** can be numbers, texts, booleans, lists and dicts; lists and dicts
  become Lua tables. A scope's waveform is a dict
  `{"time": [...], "volts": [...]}`, which `save_waveform` understands.
- The first line of the docstring is used when the decorator has no text.

### Talking to the instrument

`self.transport` is the open connection:

| Method | Does |
|---|---|
| `write(text)` | sends a line, with the write terminator |
| `read()` | reads a line |
| `query(text)` | writes, then reads the reply |
| `write_raw(data)`, `read_raw(size)`, `read_until(term)` | bytes, for binary protocols |
| `clear_input()` | drops anything unread |
| `set_timeout(seconds)` | for one slow command |

`ScpiDevice` adds `send(text)` (write, then ask `SYST:ERR?` so a rejected
command fails at once), `ask(text)` and `ask_number(text)`, and gives every
SCPI driver the commands `idn`, `reset`, `clear`, `get_error`, `write`,
`query` and `query_number`.

### Life of a device

1. The transport is opened, then the driver is created with it and its
   settings, and `connect()` is called: the place to check the instrument
   answers or to set a known state.
2. The test calls commands.
3. When the test ends, also by an error or Stop, `close()` is called. **A
   driver of anything with an output switches it off here**, unless
   `self.leave_output_on()` is true (the project asked for it).

`identify()` returns a short text for **Test connection**; by default it calls
`idn` when the driver has it.

## Simulators

A simulator is a transport that answers like the instrument. With it, tests
of your driver need no hardware, and users can write a test before the
instrument arrives.

- `ScpiSimulator` (from `benchloom.scpi`): list `(pattern, handler)` pairs in
  `routes()`. `*IDN?`, `*RST`, `*CLS` and `SYST:ERR?` work out of the box, and
  unknown commands or bad values are queued as SCPI errors, as on a real
  instrument. `sim_number(arg, low, high)` and `sim_bool(arg)` check
  arguments that way.
- `SimTransport`: implement `handle(line)` for any line-based protocol; return
  the reply text, `None` for no reply, or `bytes` sent as they are.
- `RawSimTransport`: implement `handle_bytes(data)` for binary protocols.

A `sim://` resource can pass options to the simulator: `sim://?load_ohms=5`
creates it with `load_ohms="5"`, as text. Take your options out before the
rest goes to the base class, which refuses options it does not know:

```python
def __init__(self, load_ohms="10", **options):
    super().__init__(**options)
    self.load_ohms = float(load_ohms)
```

## Testing

```python
from pathlib import Path

import pytest

from benchloom.engine import open_device
from benchloom.plugin import PluginRegistry
from benchloom.project import DeviceConfig

PLUGINS = PluginRegistry([Path(__file__).parent.parent])  # the folder holding acme/


def test_set_and_measure():
    psu = open_device(PLUGINS, DeviceConfig("psu", "acme.ps3005", "sim://"))
    psu.call("set_voltage", 5)
    psu.call("set_output", True)
    assert psu.call("measure_voltage") == pytest.approx(5)
    with pytest.raises(Exception, match="maximum"):
        psu.call("set_voltage", 40)
```

`call` runs a command the way a script does, with the same checks.

!!! tip "Try it on real hardware"
    A simulator written from the same manual as the driver will agree with
    it even where both misread the manual. Before sharing a plugin, run it on
    the instrument once, and say in its description what was tried.

## Sharing it

- **With a project:** put the plugin folder into the project's `plugins/`
  folder. It travels with the project; others are asked before it loads.
- **As a .zip:** zip the folder; others use **Install from .zip…**.
- **In the plugin registry:** others find it under **Browse online**. See
  [The plugin registry](plugin-registry.md).
- **In Benchloom itself:** for widely used instruments, a pull request is
  welcome; see [CONTRIBUTING.md](https://github.com/criscrafter/benchloom/blob/main/CONTRIBUTING.md).

A plugin is Python code and runs with the user's rights. Benchloom does not
sandbox plugins the way it does Lua scripts.
