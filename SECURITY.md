# Security

## Reporting a problem

Please report security problems privately: on GitHub, open the repository's
**Security** tab and choose **Report a vulnerability**. Do not open a public
issue for them. You will get an answer within a week.

## What to know when using Benchloom

- **Plugins are Python code** and run with your user's rights, both in the
  desktop app and in test runs. Install plugins only from people you trust;
  the plugin manager says so before installing. Nothing in a plugin runs while
  it is being installed.
- **Projects are programs.** Their Lua scripts can read and write files and
  start programs (Lua's `io` and `os` libraries), and they can command every
  instrument the project lists, so they can switch outputs on. Open and run a
  project from someone else only if you trust it, and read its script before
  running it on real hardware.
- **Instruments** reached over the network (`tcp://`, `visa://` over LAN) are
  usually not protected at all. Keep them on a lab network.
