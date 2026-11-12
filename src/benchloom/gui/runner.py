# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Runs a project in a separate process and reports back through Qt signals.

A separate process keeps the window responsive and means a hung driver can
always be stopped.

Stopping has three steps, each a fallback for the one before:

1. The script is asked to stop; it ends and the run shuts its devices down.
2. A script that has not ended after ``STOP_TIMEOUT_S`` is left behind: the run's
   process shuts the devices down without it and then ends itself.
3. A process that does neither is killed. Only then may outputs stay on.
"""

from __future__ import annotations

import multiprocessing
import queue

from PySide6.QtCore import QObject, QTimer, Signal

from ..worker import run_worker

STOP_TIMEOUT_S = 3.0  # what a script gets to stop, before the devices are shut down anyway
FORCE_STOP_AFTER_MS = 5000  # no shutdown has begun by then: the process is hung, kill it
# What shutting the devices down may take, before the kill. The run announces the
# shutdown (the "shutdown" message) before it waits for a device command in progress,
# so this covers that wait (engine.STOP_COMMAND_WAIT_S) as well.
SHUTDOWN_TIMEOUT_MS = 30000
# The run announces its shutdown STOP_TIMEOUT_S after Stop; the kill must come later.
assert FORCE_STOP_AFTER_MS > STOP_TIMEOUT_S * 1000


class RunController(QObject):
    printed = Signal(str)
    row = Signal(object)
    panel = Signal(str, object)  # the test set a front panel widget
    check = Signal(object)  # a pass/fail check, as a dict
    waveform = Signal(str, object)  # a saved waveform: name, {trace: (times, volts)}
    node = Signal(str)  # the node of a visual test that is running now
    line = Signal(int)  # the script line a Lua test is at
    finished = Signal(str, str, str)  # status, error, run folder

    def __init__(self, parent=None):
        super().__init__(parent)
        self._context = multiprocessing.get_context("spawn")
        self._process = None
        self._queue = None
        self._controls = None
        self._stop = None
        self._dead_polls = 0
        self._shutting_down = False
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
        self._shutting_down = False
        self._process = self._context.Process(
            target=run_worker,
            args=(str(project_path), self._queue, self._stop, self._controls, panel_values,
                  STOP_TIMEOUT_S),
            daemon=True,
        )
        self._process.start()
        self._poll_timer.start()

    def send_control(self, name: str, value) -> None:
        """Tell the running test that a front panel control changed."""
        if self.running:
            self._controls.put((name, value))

    def stop(self) -> None:
        """Ask the script to stop. The run shuts its devices down, with or without the
        script; only a process that does not even do that is killed."""
        if self.running and not self._kill_timer.isActive():
            self._stop.set()
            self._kill_timer.start(SHUTDOWN_TIMEOUT_MS if self._shutting_down
                                   else FORCE_STOP_AFTER_MS)

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
            elif message[0] == "node":
                self.node.emit(message[1] or "")
            elif message[0] == "line":
                self.line.emit(int(message[1] or 0))
            elif message[0] == "shutdown":
                # the devices are being switched off: give that time, do not kill it
                self._shutting_down = True
                if self._kill_timer.isActive():
                    self._kill_timer.start(SHUTDOWN_TIMEOUT_MS)
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
        if self._process.is_alive():  # reported its end but did not go
            self._process.terminate()
        self._process = None
        self._controls.cancel_join_thread()  # unsent control changes no longer matter
        self._controls.close()
        self.finished.emit(status, error, path)
