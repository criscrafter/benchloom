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
- **Projects** contain Lua scripts. Since 0.1.2 they run in a sandbox: no
  files, no other programs, no loading of other code or Lua bytecode, and at
  most 1 GB of memory. A way out of that sandbox is a security problem; please
  report it as above. A script can still command every instrument the project
  lists, so it can switch outputs on: read a project from someone else before
  running it on real hardware. A project's own `plugins/` folder is Python and
  is not sandboxed; treat it like any plugin. It is not loaded until you allow
  it for that project (the app asks when the project is opened; `benchloom run
  --trust-plugins` on the command line), and until then it cannot replace a
  built-in plugin either. The permission is kept for the project folder, so
  it also covers later changes to those plugins.
- **Instruments** reached over the network (`tcp://`, `visa://` over LAN) are
  usually not protected at all. Keep them on a lab network.
