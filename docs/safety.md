# Safety and security

## Working with real instruments

Benchloom switches real power supplies, loads and generators. A wrong script
can apply a wrong voltage or current to your circuit.

- **Set limits on the instruments themselves:** current limits, over-voltage
  protection, the load's maximum power. They work even when software does not.
- **Try new tests on the simulators first** (`sim://`), then on real hardware
  with a harmless load.
- **Stay near the bench** the first time a test runs, and keep **Stop** in
  reach.

When a test ends, fails or is stopped, the drivers of sources and loads
switch their outputs off, unless a device entry says `leave_output_on = true`.
A script that does not react to Stop within three seconds is left behind and
its devices are shut down without it. Only a run whose process hangs
completely is killed as it is, and then outputs may stay on. None of this
replaces a hardware limit.

## What runs on your computer

- **Lua scripts** run in a sandbox: no files, no other programs, no loading of
  other code or compiled Lua, and at most 1 GB of memory. They can command
  every instrument in their project, so read a project from someone else
  before running it on real hardware.
- **Plugins are Python code** and run with your rights, in the app and in
  test runs. Install plugins only from people you trust. Nothing in a plugin
  runs while it is installed. Plugins from the
  [plugin registry](plugin-registry.md) are checked against the checksum in
  the list, which proves you get the listed file, not that it is safe.
- **A project's own `plugins/` folder** is Python code that arrives with the
  project; it loads only after you allowed it for that project.
- **Network instruments** (`tcp://`, VISA over LAN) usually have no protection
  at all. Keep them on a lab network.

## Reporting a security problem

Please report security problems privately: on GitHub, open the repository's
**Security** tab and choose **Report a vulnerability**. Do not open a public
issue for them. A way out of the Lua sandbox counts as one. See
[SECURITY.md](https://github.com/criscrafter/benchloom/blob/main/SECURITY.md).
