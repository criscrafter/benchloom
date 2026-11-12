# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Command line: ``benchloom gui | new | run | devices | describe | plugins | usbgpib``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import NOTICE
from .engine import run_project
from .plugin import PluginError, PluginRegistry
from .project import ProjectError, create_project, templates


def _new(args) -> int:
    project = create_project(args.path, args.mode, args.template)
    print(f"Created {project.mode} project in {project.path}")
    print(f"Run it with: benchloom run {args.path}")
    return 0


def _run(args) -> int:
    result = run_project(args.path, trust_plugins=args.trust_plugins)
    print(f"\n{len(result.rows)} row(s) logged. Run saved to {result.path}")
    if result.status == "ok" and result.verdict != "none":
        passed = sum(1 for c in result.checks or [] if c.get("passed"))
        print(f"{result.verdict.upper()}: {passed} of {len(result.checks or [])} checks passed")
    failed = result.shutdown_failed
    if failed:
        print(f"WARNING: shutting down {', '.join(failed)} failed; outputs may still be on.")
    # for scripts and CI: 1 when a check failed, as when the test failed or was stopped,
    # or a device could not be shut down
    return 0 if result.status == "ok" and result.verdict != "fail" and not failed else 1


def _devices(args) -> int:
    # --project names the project on purpose, which is the permission to use its plugins
    registry = PluginRegistry.default(Path(args.project) if args.project else None,
                                      trust_project=True)
    for plugin in registry.plugins.values():
        print(f"{plugin.name} ({plugin.id} {plugin.version})")
        for device in plugin.devices:
            print(f"  {device.id:<18} {device.name}")
    for error in registry.errors:
        print(f"warning: {error}", file=sys.stderr)
    return 0


def _search(args, project) -> int:
    from .plugin_index import fetch_index, index_location, status

    entries = fetch_index()
    found = PluginRegistry.default(project, trust_project=True).found
    words = (args.target or "").lower().split()
    shown = 0
    for entry in entries:
        text = " ".join([entry.id, entry.name, entry.description, *entry.devices]).lower()
        if all(word in text for word in words):
            mark = {"new": "", "installed": "installed", "update": "update available",
                    "built-in": "built in"}[status(entry, found)]
            print(f"{entry.id:<14} {entry.version:<8} {entry.name}  {mark}".rstrip())
            for device in entry.devices:
                print(f"{'':<24}{device}")
            shown += 1
    if not shown:
        print(f"No plugins found in {index_location()}" + (" for that search" if words else ""))
    return 0


def _install_listed(args, project) -> int:
    from . import plugin_install
    from .plugin_index import fetch_index, install

    entry = next((e for e in fetch_index() if e.id == args.target), None)
    if entry is None:
        raise PluginError(f"'{args.target}' is neither a folder or .zip nor a listed plugin "
                          "(see: benchloom plugins search)")
    if not entry.compatible:
        raise PluginError(f"{entry.name} {entry.version} needs Benchloom {entry.benchloom} "
                          "or newer")
    print(plugin_install.TRUST_WARNING)
    replace = args.replace or plugin_install.installed_folder(entry.id) is not None
    info = install(entry, replace=replace)
    print(f"Installed {info.name} {info.version} ({info.id}) in {info.path}")
    return 0


def _plugins(args) -> int:
    from . import plugin_install
    from .config import AppConfig
    from .plugin import SOURCES

    project = Path(args.project) if args.project else None
    if args.action == "search":
        return _search(args, project)
    if args.action == "entry":
        if not args.target or not args.url:
            raise PluginError("'plugins entry' needs a .zip and --url (where it will be)")
        import json

        from .plugin_index import make_entry
        print(json.dumps(make_entry(Path(args.target), args.url), indent=2))
        return 0
    if args.action == "list":
        registry = PluginRegistry.default(project, trust_project=True)
        for info in registry.found:
            status = registry.status(info)
            mark = {"enabled": "on ", "disabled": "off", "replaced": "---"}[status]
            print(f"{mark} {info.id:<12} {info.version:<8} "
                  f"{SOURCES.get(info.source, info.source):<14} {info.path}")
        for folder, error in registry.broken:
            print(f"err {folder}: {error}")
        return 0
    if not args.target:
        raise PluginError(f"'plugins {args.action}' needs a {'path' if args.action == 'install' else 'plugin id'}")
    if args.action == "install":
        if not Path(args.target).exists():
            return _install_listed(args, project)
        print(plugin_install.TRUST_WARNING)
        info = plugin_install.install(Path(args.target), replace=args.replace)
        print(f"Installed {info.name} {info.version} ({info.id}) in {info.path}")
        return 0
    registry = PluginRegistry.default(project, trust_project=True)
    matches = [info for info in registry.found if info.id == args.target]
    if not matches:
        raise PluginError(f"no plugin with id '{args.target}' (see: benchloom plugins)")
    if args.action == "remove":
        installed = [info for info in matches if plugin_install.is_removable(info)]
        if not installed:
            raise PluginError(f"'{args.target}' is not an installed plugin")
        plugin_install.remove(installed[0])
        print(f"Removed {installed[0].path}")
        return 0
    config = AppConfig.load()
    config.set_enabled(args.target, args.action == "enable")
    config.save()
    print(f"Plugin '{args.target}' switched {'on' if args.action == 'enable' else 'off'}")
    return 0


def _describe(args) -> int:
    # Describing a device type imports its driver, so a project's own plugins load only
    # with the same consent as for a run: allowed before, or --trust-plugins now.
    project = Path(args.project) if args.project else None
    if project is not None and args.trust_plugins:
        from .config import AppConfig

        config = AppConfig.load()
        if not config.trusts(project):
            config.set_trusted(project)
            config.save()
    registry = PluginRegistry.default(project)
    spec = registry.device(args.device)
    print(f"{spec.name} ({spec.id})")
    if spec.description:
        print(spec.description)
    print(f"Connections: {', '.join(spec.transports) or 'any'}\n")
    for info in spec.load_driver().commands().values():
        params = []
        for p in info.params:
            text = p.name
            if p.unit:
                text += f" [{p.unit}]"
            if p.min is not None or p.max is not None:
                low = "" if p.min is None else f"{p.min:g}"
                high = "" if p.max is None else f"{p.max:g}"
                text += f" {low}..{high}"
            params.append(text if p.required else f"[{text}]")
        returns = ""
        if info.returns not in ("nil", "any"):
            returns = f" -> {info.returns}" + (f" [{info.unit}]" if info.unit else "")
        print(f"  {info.name}({', '.join(params)}){returns}")
        if info.doc:
            print(f"      {info.doc}")
    return 0


def _usbgpib(args) -> int:
    from .transports import TransportError, find_usbtmc, open_transport

    if not args.commands:
        found = find_usbtmc(0x03EB, 0x2065)
        for _, _, serial in found:
            print(f"usbgpib://{serial}" if serial else "usbgpib:// (serial number not readable)")
        if not found:
            print("no UsbGpib adapter found (it only appears while a powered instrument "
                  "is connected to it)")
        return 0
    try:
        transport = open_transport(f"usbgpib://{args.serial or ''}")
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        for text in args.commands:
            reply = transport.adapter_command(text)
            print(f"{text} -> {reply}" if reply is not None else f"{text}: sent")
    finally:
        transport.close()
    return 0


def _gui(args) -> int:
    from .gui import main as gui_main

    return gui_main(args.path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchloom", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="Benchloom comes with ABSOLUTELY NO WARRANTY; "
                                            "see 'benchloom --version' for details.")
    parser.add_argument("--version", action="version", version=NOTICE)
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="create a project with a working example")
    new.add_argument("path")
    new.add_argument("--mode", choices=("lua", "visual"), default="lua")
    new.add_argument("--template", choices=[t.id for t in templates()], default="example",
                     help="what the project starts with: " + "; ".join(
                         f"{t.id} = {t.title}" for t in templates()))
    new.set_defaults(func=_new)

    run = sub.add_parser("run", help="run a project's test")
    run.add_argument("path", nargs="?", default=".")
    run.add_argument("--trust-plugins", action="store_true",
                     help="allow the project's own plugins folder (Python code) to load, "
                          "now and in later runs")
    run.set_defaults(func=_run)

    devices = sub.add_parser("devices", help="list the device types plugins provide")
    devices.add_argument("--project", help="also include this project's plugins folder")
    devices.set_defaults(func=_devices)

    describe = sub.add_parser("describe", help="show the commands of a device type")
    describe.add_argument("device", help="device type, e.g. demo.psu")
    describe.add_argument("--project", help="also include this project's plugins folder, "
                                            "if it was allowed to load")
    describe.add_argument("--trust-plugins", action="store_true",
                          help="allow the project's own plugins folder (Python code) to "
                               "load, now and in later runs")
    describe.set_defaults(func=_describe)

    plugins = sub.add_parser(
        "plugins", help="list, find, install, remove, or switch plugins on and off",
        description="list: every plugin found (on, off, or --- when replaced by a plugin "
                    "with the same id). search [WORDS]: plugins in the online list. "
                    "install PATH|ID: copy a plugin folder or .zip into the user plugin "
                    "folder, or download a listed plugin. remove/enable/disable ID. "
                    "entry ZIP --url URL: the list entry for a plugin you publish.")
    plugins.add_argument("action", nargs="?", default="list",
                         choices=("list", "search", "install", "remove", "enable", "disable",
                                  "entry"))
    plugins.add_argument("target", nargs="?",
                         help="a path or listed id for install, words for search, a .zip "
                              "for entry, else a plugin id")
    plugins.add_argument("--url", help="entry: where the .zip will be downloaded from")
    plugins.add_argument("--replace", action="store_true",
                         help="install over a plugin with the same id")
    plugins.add_argument("--project", help="also include this project's plugins folder")
    plugins.set_defaults(func=_plugins)

    usbgpib = sub.add_parser(
        "usbgpib", help="list xyphro UsbGpib adapters, or send them their own '!' commands",
        description="Without commands: list the adapters. With commands: send them to the "
                    "adapter, e.g. benchloom usbgpib \"!autoid off\" for instruments that "
                    "do not understand *IDN? (HP 3478A), or \"!ver?\".")
    usbgpib.add_argument("commands", nargs="*")
    usbgpib.add_argument("--serial", help="the adapter's serial number, if there are several")
    usbgpib.set_defaults(func=_usbgpib)

    gui = sub.add_parser("gui", help="open the desktop app")
    gui.add_argument("path", nargs="?", help="project folder to open")
    gui.set_defaults(func=_gui)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ProjectError, PluginError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
