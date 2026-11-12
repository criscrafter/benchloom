# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""The bar that says a newer Benchloom is out, and the check behind it.

The check runs in a background thread, so a slow or missing network never holds up
the app; its answer comes back to the window as a Qt signal.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy

from .. import __version__
from ..updates import Release, UpdateError, how_to_update, latest_release


class UpdateChecker(QObject):
    """Asks GitHub in a thread; ``done`` brings the Release, or the error text."""

    done = Signal(object, str)  # Release or None, error text

    def __init__(self, parent=None, fetch: Callable[[], Release] | None = None):
        super().__init__(parent)
        self._fetch = fetch or latest_release
        self.busy = False

    def start(self) -> bool:
        if self.busy:
            return False
        self.busy = True
        threading.Thread(target=self._work, name="benchloom-update-check",
                         daemon=True).start()
        return True

    def _work(self) -> None:
        try:
            release, error = self._fetch(), ""
        except UpdateError as exc:
            release, error = None, str(exc)
        except Exception as exc:  # a check must never take the app down
            release, error = None, f"the check failed: {exc}"
        self.busy = False
        try:
            self.done.emit(release, error)
        except RuntimeError:  # the window closed meanwhile
            pass


class UpdateBar(QFrame):
    """Says a newer version is available. It sits in a row of its own under the
    toolbar, which ``shown`` opens and closes."""

    skipped = Signal(str)  # the version not to mention again
    shown = Signal(bool)  # for the toolbar row the bar sits in

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("UpdateBar")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("#UpdateBar { background: rgba(37, 99, 235, 40); "
                           "border: 1px solid rgba(37, 99, 235, 120); border-radius: 4px; }")
        self.release: Release | None = None
        self.label = QLabel()
        self.open_button = QPushButton("What's new and download")
        self.open_button.clicked.connect(self.open_release)
        self.skip_button = QPushButton("Skip this version")
        self.skip_button.clicked.connect(self.skip)
        self.later_button = QPushButton("Later")
        self.later_button.setToolTip("Hide this until Benchloom starts again")
        self.later_button.clicked.connect(self.close_bar)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 6, 6)
        row.addWidget(self.label, 1)
        row.addWidget(self.open_button)
        row.addWidget(self.skip_button)
        row.addWidget(self.later_button)
        self.is_open = False

    def close_bar(self) -> None:
        self.is_open = False
        self.shown.emit(False)

    def show_release(self, release: Release) -> None:
        self.release = release
        self.label.setText(f"<b>Benchloom {release.version} is available</b> "
                           f"(you have {__version__}). {how_to_update()}")
        self.is_open = True
        self.shown.emit(True)

    def open_release(self) -> None:
        if self.release:
            QDesktopServices.openUrl(QUrl(self.release.url))

    def skip(self) -> None:
        if self.release:
            self.skipped.emit(self.release.version)
        self.close_bar()
