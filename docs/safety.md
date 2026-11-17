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

## Finding devices

**Find…** (and `benchloom discover`) first only lists ports and USB ids: that
sends nothing and opens nothing. Only ports you tick are asked, and asking
**does** send to them. What that can do:

- **Opening a serial port resets many Arduino boards**, including an AR488 on
  an Arduino Nano. Discovery never uses 1200 baud, which would start the
  bootloader of a Leonardo or Pro Micro.
- **Anything on a ticked port gets the queries:** `++ver` at 115200 baud, then
  the queries plugins declare (today the Feeltech's `UMO` at 115200 baud and a
  Modbus read of address 1 at 9600 baud for the Kunkin KP184). Every device on
  an RS-485 bus hears them; the Modbus read changes nothing on a Modbus device
  that has address 1, and devices at other addresses ignore it. Tick only ports with
  instruments on them. A 3D printer, CNC controller or anything else that moves
  or heats things up does not belong on a port you ask.
- **On a GPIB adapter** nothing else is sent to the adapter port itself.
  `++fndl` finds the instruments by handshake alone. Each one is then addressed
  to talk without a command: old HP instruments (3478A, 5334, 5384A) send a
  reading and are left at that. An SCPI instrument that was not asked
  anything may put a "query unterminated" error (-420) in its error queue.
- **Silent instruments are asked `*IDN?`**, unless you untick that (or use
  `--no-idn`). Instruments older than IEEE 488.2 do not know it: the HP 5334 and
  5384A counters stop measuring after an unknown command, so one that does not
  answer gets a device clear, which ends that state. The clear also aborts a
  measurement in progress. Other old instruments may show an error until you
  press a key or clear them.
- **The AR488's settings** (address, auto, EOI, read timeout) are put back as
  they were; they are only kept until it is unplugged anyway.
- **Bluetooth serial ports** are never asked, nor are the ports the open
  project's devices use. A GPIB adapter the project uses is the exception,
  since more instruments can share its bus: it is still scanned, but the
  addresses of the project's instruments are left alone. Find… is not
  available while a test runs. On Linux
  ports are opened exclusively, so one another program uses is left alone.
- **Networks are never scanned.** Scanning addresses on a network can reach
  other people's equipment, and lab instruments on a network have no protection
  of their own.

Discovery switches no outputs and changes no settings beyond what is listed
here. It only suggests a device type: **Test connection** in the Add device
dialog confirms it.

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
