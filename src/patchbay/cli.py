"""Command line: ``patchbay gui | new | run | devices | describe``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .engine import run_project
from .plugin import PluginError, PluginRegistry
from .project import ProjectError, create_project


def _new(args) -> int:
    project = create_project(args.path, args.mode)
    print(f"Created {project.mode} project in {project.path}")
    print(f"Run it with: patchbay run {args.path}")
    return 0


def _run(args) -> int:
    result = run_project(args.path)
    print(f"\n{len(result.rows)} row(s) logged. Run saved to {result.path}")
    return 0 if result.status == "ok" else 1


def _devices(args) -> int:
    registry = PluginRegistry.default(Path(args.project) if args.project else None)
    for plugin in registry.plugins.values():
        print(f"{plugin.name} ({plugin.id} {plugin.version})")
        for device in plugin.devices:
            print(f"  {device.id:<18} {device.name}")
    for error in registry.errors:
        print(f"warning: {error}", file=sys.stderr)
    return 0


def _describe(args) -> int:
    registry = PluginRegistry.default(Path(args.project) if args.project else None)
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


def _gui(args) -> int:
    from .gui import main as gui_main

    return gui_main(args.path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patchbay", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="create a project with a working example")
    new.add_argument("path")
    new.add_argument("--mode", choices=("lua", "visual"), default="lua")
    new.set_defaults(func=_new)

    run = sub.add_parser("run", help="run a project's test")
    run.add_argument("path", nargs="?", default=".")
    run.set_defaults(func=_run)

    devices = sub.add_parser("devices", help="list the device types plugins provide")
    devices.add_argument("--project", help="also include this project's plugins folder")
    devices.set_defaults(func=_devices)

    describe = sub.add_parser("describe", help="show the commands of a device type")
    describe.add_argument("device", help="device type, e.g. demo.psu")
    describe.add_argument("--project", help="also include this project's plugins folder")
    describe.set_defaults(func=_describe)

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
