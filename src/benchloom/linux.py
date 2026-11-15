# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Linux desktop integration: a menu entry with icon, and udev rules for USB instruments.

Both are per user or printed for the user to install, so nothing here needs root:

    benchloom desktop-entry           # Benchloom in the app menu, with its icon
    benchloom udev-rules | sudo tee /etc/udev/rules.d/70-benchloom.rules
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_ID = "benchloom"  # the desktop file's name; the app sets it as its desktop file name
ICON = Path(__file__).resolve().parent / "gui" / "benchloom.png"  # = gui.ICON, without Qt
RULES_NAME = "70-benchloom.rules"  # before 73-seat-late.rules, which applies uaccess


def data_home() -> Path:
    """$XDG_DATA_HOME, where per-user menu entries and icons go."""
    value = os.environ.get("XDG_DATA_HOME", "")
    return Path(value) if os.path.isabs(value) else Path.home() / ".local" / "share"


def desktop_file() -> Path:
    return data_home() / "applications" / f"{APP_ID}.desktop"


def icon_file() -> Path:
    return data_home() / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png"


def app_command() -> list[str]:
    """How to start the desktop app from a menu: the packaged app, the benchloom-gui
    script next to this Python, or this Python with ``-m benchloom gui``."""
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve().parent / "Benchloom")]
    script = Path(sys.executable).parent / "benchloom-gui"
    if script.is_file():
        return [str(script)]
    return [sys.executable, "-m", "benchloom", "gui"]


def _quote(arg: str) -> str:
    """One Exec argument, quoted as the Desktop Entry Specification asks."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`='):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'.replace("\\", "\\\\")  # the value itself is an escaped string


def desktop_entry(command: list[str]) -> str:
    exec_line = " ".join(_quote(arg) for arg in command).replace("%", "%%")
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.4",
        "Name=Benchloom",
        "GenericName=Lab automation",
        "Comment=Control lab instruments and run tests as node graphs or Lua scripts",
        f"Exec={exec_line}",
        f"Icon={APP_ID}",
        "Terminal=false",
        "Categories=Science;Electronics;Engineering;",
        "Keywords=lab;instrument;test;measurement;SCPI;GPIB;oscilloscope;power supply;",
        "StartupWMClass=Benchloom",
        "StartupNotify=true",
        "",
    ])


def install_desktop_entry() -> tuple[Path, Path]:
    """Add Benchloom to the user's app menu (or update the entry). Returns the files."""
    icon = icon_file()
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ICON, icon)
    entry = desktop_file()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(desktop_entry(app_command()), encoding="utf-8")
    entry.chmod(0o755)  # some desktops only start entries that are executable
    return entry, icon


def remove_desktop_entry() -> list[Path]:
    removed = []
    for path in (desktop_file(), icon_file()):
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


def usb_ids(registry) -> list[tuple[int, int, str]]:
    """(vendor id, product id, what it is) of the USB devices plugins name in their
    default resource (``usb://`` and ``usbtmc://``), and the xyphro UsbGpib adapter."""
    from .transports import USBGPIB_IDS

    found = {USBGPIB_IDS: "xyphro UsbGpib adapter"}
    for plugin in registry.plugins.values():
        for device in plugin.devices:
            scheme, _, target = device.default_resource.partition("://")
            if scheme.lower() not in ("usb", "usbtmc"):
                continue
            vid, _, pid = target.split("/")[0].split("?")[0].partition(":")
            try:
                ids = (int(vid, 16), int(pid, 16))
            except ValueError:
                continue
            found.setdefault(ids, f"{device.name} ({device.id})")
    return sorted((vid, pid, name) for (vid, pid), name in found.items())


def udev_rules(registry, group: str | None = None) -> str:
    """udev rules that let the logged-in user open Benchloom's USB instruments; with
    ``group``, members of that group too (for test PCs used over SSH)."""
    access = 'TAG+="uaccess"' + (f', GROUP="{group}", MODE="0660"' if group else "")
    lines = [
        "# Benchloom: USB instruments without root. Written by 'benchloom udev-rules'.",
        f"# Install: benchloom udev-rules | sudo tee /etc/udev/rules.d/{RULES_NAME}",
        "#          sudo udevadm control --reload-rules && sudo udevadm trigger",
        "# then plug the instrument in again.",
        "",
    ]
    for vid, pid, name in usb_ids(registry):
        lines += [f"# {name}",
                  f'SUBSYSTEM=="usb", ATTR{{idVendor}}=="{vid:04x}", '
                  f'ATTR{{idProduct}}=="{pid:04x}", {access}']
    lines += ["# Any USBTMC instrument (USB Test & Measurement Class, as VISA's USB0::...)",
              'SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", '
              f'ENV{{ID_USB_INTERFACES}}=="*:fe03??:*", {access}', ""]
    return "\n".join(lines)
