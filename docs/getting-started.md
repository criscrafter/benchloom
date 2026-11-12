# Getting started

## Install

=== "Windows download"

    Download `Benchloom-…-windows-x64.zip` from the
    [latest release](https://github.com/criscrafter/benchloom/releases/latest),
    unzip it anywhere and start `Benchloom.exe`. No installation, no Python and
    no administrator rights are needed.

    Windows may warn about an unknown publisher, because the program is not
    signed: choose **More info**, then **Run anyway**.

    The folder also holds `benchloom-cli.exe`, the [command line](command-line.md).

=== "pip"

    With Python 3.11 or newer, on Windows or Linux:

    ```
    pip install "benchloom[gui,usb]"
    benchloom gui
    ```

    The extras add parts you may not need:

    | Extra | Adds |
    |---|---|
    | `gui` | the desktop app (without it, tests run from the command line only) |
    | `usb` | USB instruments such as the Hantek scope, and USB-GPIB adapters |
    | `visa` | instruments through VISA, including vendor GPIB adapters |

=== "From source"

    Needed to change Benchloom itself:

    ```
    git clone https://github.com/criscrafter/benchloom
    cd benchloom
    python -m venv .venv
    .venv\Scripts\pip install -e ".[gui,usb,dev]"    # Linux/Mac: .venv/bin/pip
    .venv\Scripts\benchloom gui
    ```

    `.venv\Scripts\pytest` runs the tests; they use the simulators, so they
    need no hardware. [CONTRIBUTING.md](https://github.com/criscrafter/benchloom/blob/main/CONTRIBUTING.md)
    has the rest.

## Your first test

1. Choose **New project**, give it a name, and pick **Lua script** or
   **Visual**. A Lua project is a text script; a visual one is built from
   nodes. The choice is fixed per project.
2. Leave **Start from** on **Example**. The project starts with a simulated
   power supply and a voltage sweep, so it runs without hardware.
3. Press **Run** (F5). The sweep sets 1 V to 10 V, logs voltage and current,
   and checks the current at 10 V. The **Plot** draws the rows as they arrive,
   the **Data** tab lists them, and the status bar shows the verdict:
   `PASS · Finished · 10 row(s) logged · 1 of 1 checks passed`.
4. Press **Report** to open the run's report in your browser.

The example script:

```lua
-- Sweep the supply from 1 V to 10 V and record voltage and current.
print("Connected to " .. psu:idn())

psu:set_current_limit(2)
psu:set_output(true)

for volts = 1, 10 do
    psu:set_voltage(volts)
    sleep(0.1)
    log { set_V = volts, V = psu:measure_voltage(), I = psu:measure_current() }
end

-- A pass/fail check: the run passes only if every check does (see report.html).
check("Current at 10 V", psu:measure_current(), 0.9, 1.1, "A")

psu:set_output(false)
print("Sweep finished")
```

`psu` is the device the project lists; [Lua scripts](lua.md) describes
everything else a script can use.

## Real instruments

In the **Devices** panel, **Add…** opens the list of device types the plugins
provide. Pick one, give it a name (the name the test uses, such as `psu`) and
a resource: where the instrument is connected, such as `serial://COM3` or
`prologix://COM4/5`. **Test connection** asks the instrument who it is.
[Projects](projects.md#resources) lists every kind of resource, and
[Instruments](INSTRUMENTS.md) the settings each instrument needs.

Double-click a command under a device to add it to the test: a line in a
script, a node in a visual test.

!!! tip
    Keep the simulator (`sim://`) while you write a test, and switch to the
    real resource when it works. Most drivers come with a simulator that
    behaves like the instrument, including its errors.

## Start from a template

**Start from** offers ready-made projects:

- **Bode plot:** gain and phase of a circuit against frequency, with a
  Feeltech FY6900 generator and a Hantek DSO5102P scope. Wire generator CH1 to
  the circuit's input and to scope CH1, and the circuit's output to scope CH2.
  Set the sweep on the front panel; the plot shows gain and phase on a log
  frequency axis, and the script measures the -3 dB points. The input and
  output waveform of each point are saved as images, and the -3 dB point can be
  checked against limits. It starts on simulators with a simulated RC
  low-pass; set the two resources for real hardware.

## What a run leaves behind

Each run gets a folder under the project's `runs/`, named by date and time:

| File | Holds |
|---|---|
| `data.csv` | the rows the test logged, with `time_s` (seconds since the start) |
| `console.log` | everything the test printed, and errors |
| `run.json` | status, verdict, check counts, Benchloom version, devices and how each one's shutdown went |
| `checks.csv` | each pass/fail check: name, value, limits, unit, result |
| `waveforms/` | saved waveforms as CSV, SVG and PNG |
| `report.html` | one page with the verdict, checks, a chart, the waveforms, the devices, the data and the console |
| `main.lua`, or `main.graph.json` and `graph.lua` | the test exactly as it ran; a visual test also as the Lua it became |

**Show run folder** in the toolbar opens the folder of the last run.

## Past runs

The **Runs** tab, next to the console, lists the project's runs, newest first,
with their verdict, row and check counts and duration. Double-click one for its
report. **Show** loads its data into the plot and the Data tab, and its console.
**Compare** draws its curves dashed next to the current ones, also while the
next run is being measured, so a change to the circuit or the test shows
right away; **Clear comparison** removes them. **Delete** moves a run to the
recycle bin.

## Next

- [Projects](projects.md): the project file, resources and plot settings.
- [Lua scripts](lua.md) or [Visual tests](visual-tests.md).
- [Front panel](front-panel.md): controls you use while a test runs.
