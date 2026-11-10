# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The Benchloom desktop app (Qt)."""

from __future__ import annotations

import multiprocessing
import sys


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
    window = MainWindow()
    window.show()
    if path:
        window.open_project(path)
    elif window.prefs.get("reopen_last") and window._recent():
        window.open_project(window._recent()[0])
    return app.exec()
