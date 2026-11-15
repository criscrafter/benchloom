Benchloom for Linux
===================

Open-source lab automation: control power supplies, loads, scopes and
generators, and build tests as node graphs or Lua scripts.

Starting
--------
Unpack the archive anywhere in your home folder and start Benchloom:

    tar xzf Benchloom-*-linux-x86_64.tar.gz
    Benchloom/Benchloom

Nothing is installed and no root rights are needed. To add Benchloom to the
app menu, with its icon, run once:

    Benchloom/benchloom-cli desktop-entry

Run it again after moving the folder. To remove Benchloom, run
"benchloom-cli desktop-entry --remove" and delete the folder.

Choose "New project" and run it with F5: new projects start with simulated
instruments, so they work without hardware.

Command line: benchloom-cli (for example "Benchloom/benchloom-cli --help").

Needs a 64-bit Linux with glibc 2.35 or newer (Ubuntu 22.04, Debian 12,
Fedora 36 or newer) and an X11 or Wayland desktop. If the app does not start
on X11 and says it could not load the Qt platform plugin "xcb", install
libxcb-cursor0 (Debian, Ubuntu) or xcb-util-cursor (Fedora, Arch).

Serial instruments
------------------
USB serial adapters show up as /dev/ttyUSB0, /dev/ttyACM0 and so on. Your user
needs to be in their group, usually "dialout" ("uucp" on Arch):

    sudo usermod -aG dialout $USER

then log out and in again. On Ubuntu, the brltty package (Braille displays)
can take over CH340 adapters, so that /dev/ttyUSB0 vanishes right after
plugging in; remove brltty if you do not use a Braille display.

USB instruments
---------------
The Hantek DSO5102P, USBTMC instruments and USB-GPIB adapters such as the
xyphro UsbGpib need access to the USB device. Install Benchloom's udev rules
once:

    Benchloom/benchloom-cli udev-rules | sudo tee /etc/udev/rules.d/70-benchloom.rules
    sudo udevadm control --reload-rules && sudo udevadm trigger

and plug the instrument in again. No other driver is needed.

Safety
------
Benchloom switches real power supplies, loads and generators. A wrong test
can apply a wrong voltage or current to your circuit. Set current limits on
the instruments themselves and try new tests on the simulators first.

Licence and warranty
--------------------
Copyright (C) 2026 Friedrich-Christer Rosenthal.
Benchloom is free software under the GNU Affero General Public License 3.0 or
later (LICENSE.txt). It is provided WITHOUT ANY WARRANTY. Other software
included in this folder is listed in THIRD_PARTY_NOTICES.md; the licence files
of the included libraries are in the _internal folder (*.dist-info).

Source code, documentation and help:
https://github.com/criscrafter/benchloom
