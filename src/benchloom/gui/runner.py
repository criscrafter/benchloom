# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Runs a project in a separate process and reports back through Qt signals.

A separate process keeps the window responsive and means a hung driver can
always be stopped.
"""

from __future__ import annotations

import multiprocessing
import queue

from PySide6.QtCore import QObject, QTimer, Signal

from ..worker import run_worker

FORCE_STOP_AFTER_MS = 3000


class RunController(QObject):
    printed = Signal(str)
    row = Signal(object)
    panel = Signal(str, object)  # the test set a front panel widget
    check = Signal(object)  # a pass/fail check, as a dict
    waveform = Signal(str, object)  # a saved waveform: name, {trace: (times, volts)}
    finished = Signal(str, str, str)  # status, error, run folder

    def __init__(self, parent=None):
        super().__init__(parent)
        self._context = multiprocessing.get_context("spawn")
        self._process = None
        self._queue = None
        self._controls = None
        self._stop = None
        self._dead_polls = 0
        self._poll_timer = QTimer(self, interval=30)
        self._poll_timer.timeout.connect(self._poll)
        self._kill_timer = QTimer(self, singleShot=True, interval=FORCE_STOP_AFTER_MS)
        self._kill_timer.timeout.connect(self._force_stop)

    @property
    def running(self) -> bool:
        return self._process is not None

    def start(self, project_path, panel_values: dict | None = None) -> None:
        if self.running:
            return
        self._queue = self._context.Queue()
        self._controls = self._context.Queue()
        self._stop = self._context.Event()
        self._dead_polls = 0
        self._process = self._context.Process(
            target=run_worker,
            args=(str(project_path), self._queue, self._stop, self._controls, panel_values),
            daemon=True,
        )
        self._process.start()
        self._poll_timer.start()

    def send_control(self, name: str, value) -> None:
        """Tell the running test that a front panel control changed."""
        if self.running:
            self._controls.put((name, value))

    def stop(self) -> None:
        """Ask the script to stop; force it if it has not stopped after a few seconds."""
        if self.running and not self._kill_timer.isActive():
            self._stop.set()
            self._kill_timer.start()

    def _poll(self) -> None:
        for _ in range(500):
            try:
                message = self._queue.get_nowait()
            except queue.Empty:
                break
            if message[0] == "print":
                self.printed.emit(message[1])
            elif message[0] == "row":
                self.row.emit(message[1])
            elif message[0] == "panel":
                self.panel.emit(message[1], message[2])
            elif message[0] == "check":
                self.check.emit(message[1])
            elif message[0] == "waveform":
                self.waveform.emit(message[1], message[2])
            elif message[0] == "done":
                self._end(*message[1:])
                return
        else:
            return  # more may be waiting; check again before looking at the process
        if not self._process.is_alive():
            self._dead_polls += 1
            if self._dead_polls >= 3:
                self._end("error", "The run ended unexpectedly.", "")

    def _force_stop(self) -> None:
        if self.running:
            self._process.terminate()
            self.printed.emit("Stopped (forced; devices were not shut down cleanly).")
            self._end("stopped", "", "")

    def _end(self, status: str, error: str, path: str) -> None:
        self._poll_timer.stop()
        self._kill_timer.stop()
        self._process.join(1)
        self._process = None
        self._controls.cancel_join_thread()  # unsent control changes no longer matter
        self._controls.close()
        self.finished.emit(status, error, path)
