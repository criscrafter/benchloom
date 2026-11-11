# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
# PyInstaller recipe for the Windows download: python packaging/build_windows.py
#
# A folder build (not one file): it starts faster, and the Qt and libusb libraries
# stay separate files that users can replace, as their LGPL licence asks.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src" / "benchloom"

# Plugins and templates are read from disk at run time, so they go in as files.
datas = [(str(SRC / "plugins"), "benchloom/plugins"),
         (str(SRC / "templates"), "benchloom/templates")]
binaries = []
hiddenimports = collect_submodules("benchloom") + [
    "random", "serial", "serial.tools.list_ports", "usb.core", "usb.util",
    "usb.backend.libusb1", "usbtmc",
]
d, b, h = collect_all("libusb_package")  # the libusb DLL for USB instruments
datas += d
binaries += b
hiddenimports += h
# Licence files of everything shipped (their dist-info folders)
for package in ("PySide6", "shiboken6", "pyqtgraph", "numpy", "lupa", "pyserial", "pyusb",
                "python-usbtmc", "libusb-package"):
    datas += copy_metadata(package)

excludes = [
    "tkinter", "matplotlib", "IPython", "pytest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSql", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtSpatialAudio", "PySide6.QtRemoteObjects",
]


def analysis(script):
    return Analysis([str(Path(SPECPATH) / script)], pathex=[str(ROOT / "src")],
                    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
                    excludes=excludes, noarchive=False)


icon = str(Path(SPECPATH) / "benchloom.ico")
app = analysis("app.py")
cli = analysis("cli.py")
app_exe = EXE(PYZ(app.pure), app.scripts, [], exclude_binaries=True, name="Benchloom",
              console=False, icon=icon, version=str(Path(SPECPATH) / "version_info.txt"))
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="benchloom-cli",
              console=True, icon=icon, version=str(Path(SPECPATH) / "version_info.txt"))
COLLECT(app_exe, app.binaries, app.datas, cli_exe, cli.binaries, cli.datas,
        name="Benchloom")
