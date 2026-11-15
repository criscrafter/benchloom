# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The Benchloom desktop app (Qt)."""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

ICON = Path(__file__).resolve().parent / "benchloom.png"


def _keep_none_alive() -> None:
    """Work around a refcount bug in PySide6 on Python 3.11.

    PySide6 gives back some references to None that it never took, so None's
    reference count drains while the app runs. When it reaches zero, Python aborts
    ("Fatal Python error: none_dealloc"); in the tests this happened after a few
    hundred windows and theme switches. Python 3.12 and newer keep None alive for
    good, so there only 3.11 needs a count too large ever to drain.
    """
    if sys.version_info >= (3, 12) or sys.implementation.name != "cpython":
        return
    import ctypes

    # ob_refcnt, a Py_ssize_t, is the first field of every CPython object
    ctypes.c_ssize_t.from_address(id(None)).value += 1 << 40


_keep_none_alive()


def app_icon():
    """The window icon in several sizes. X11 gets an icon in one request, which a
    256 px image alone overflows: Qt then leaves the window without any icon."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon, QPixmap

    image, icon = QPixmap(str(ICON)), QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation))
    return icon


def main(path: str | None = None) -> int:
    multiprocessing.freeze_support()  # needed once the app is packaged as an .exe
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print('The desktop app needs Qt. Install it with: pip install "benchloom[gui]"',
              file=sys.stderr)
        return 2
    from .main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Benchloom")
    # Linux: the icon of windows and the taskbar, and on Wayland the link to the menu
    # entry 'benchloom desktop-entry' writes (benchloom.desktop)
    app.setDesktopFileName("benchloom")
    app.setWindowIcon(app_icon())
    window = MainWindow()
    window.show()
    if path:
        window.open_project(path)
    elif window.prefs.get("reopen_last") and window._recent():
        window.open_project(window._recent()[0])
    window.check_for_updates()  # in the background, at every start
    return app.exec()
