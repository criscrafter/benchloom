Benchloom for Windows
=====================

Open-source lab automation: control power supplies, loads, scopes and
generators, and build tests as node graphs or Lua scripts.

Starting
--------
Unzip the whole folder anywhere (for example into Documents) and start
Benchloom.exe. Nothing is installed and no administrator rights are needed.
To remove Benchloom, delete the folder.

Windows may warn that the app is from an unknown publisher, because the
program is not signed. Choose "More info" and then "Run anyway".

Choose "New project" and run it with F5: new projects start with simulated
instruments, so they work without hardware.

Command line: benchloom-cli.exe (for example "benchloom-cli.exe --help" in a
command prompt in this folder).

USB instruments
---------------
Instruments with a USB serial port (Kunkin KP184 adapters, Feeltech FY6900)
work right away. The Hantek DSO5102P and USB-GPIB adapters without a VISA need
the WinUSB driver once; see the documentation online.

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
