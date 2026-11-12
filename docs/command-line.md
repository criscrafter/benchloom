# Command line

Everything the app does with a test also works without it: on a test PC
without a screen, in a script, or from a scheduled task. With the Windows
download, use `benchloom-cli.exe` in the same folder; after `pip install`, the
command is `benchloom`.

```
benchloom new my-test                    # a Lua project with the example
benchloom new my-graph --mode visual     # a visual project
benchloom new my-filter --template bode  # from a template
benchloom run my-test                    # run it; the run folder is printed
benchloom gui                            # the desktop app
```

`benchloom run` prints what the test prints, then the run folder. Its exit code
says how the run went, for use in scripts:

| Exit code | Meaning |
|---|---|
| 0 | the test ran to its end |
| 1 | the test failed with an error, or was stopped |
| 2 | Benchloom could not start the run (no such project, a plugin error) |

The exit code does not include the checks: a test that ends normally gives 0
even when a check failed. The verdict is the `result` in the run's
`run.json` (`pass`, `fail` or `none`), and each check printed a `PASS` or
`FAIL` line. `benchloom gui my-test` opens a project in the app.

Stop a run with Ctrl+C; the outputs are switched off as after a run in the
app. `--trust-plugins` allows the project's own `plugins/` folder to load, now
and in later runs ([why](projects.md#plugins-in-a-project)).

## Instruments

```
benchloom devices                    # the device types the plugins provide
benchloom describe demo.psu          # the commands of one, with arguments and units
benchloom usbgpib                    # xyphro UsbGpib adapters connected
benchloom usbgpib "!ver?"            # a command to the adapter itself
```

`--project <folder>` on `devices` and `describe` includes that project's own
plugins.

## Plugins

```
benchloom plugins                         # every plugin found, on or off
benchloom plugins search rigol            # the online plugin list
benchloom plugins install rigol           # a listed plugin, by id
benchloom plugins install my-plugin.zip   # or a folder; --replace to update
benchloom plugins disable hp              # switch off; enable switches on
benchloom plugins remove acme             # an installed plugin
benchloom plugins entry my.zip --url URL  # the list entry for a plugin you publish
```

See [Writing a plugin](writing-plugins.md) and
[The plugin registry](plugin-registry.md).

## Settings and folders

Benchloom keeps its settings in `~/.benchloom/`: installed plugins in
`plugins/`, and in `config.json` the plugins switched off, extra plugin
folders, the projects allowed to load their own plugins, and `plugin_index`,
another plugin list to use. Set the environment variable `BENCHLOOM_HOME` to
use another folder.

`benchloom --version` shows the version, the copyright and the licence notice.
