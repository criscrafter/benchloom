# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Entry point of the process the desktop app runs a test in.

Kept free of Qt imports so the process starts quickly. Messages on the queue:
``("print", text)``, ``("row", dict)``, ``("panel", name, value)``,
``("check", dict)``, ``("waveform", name, traces)``, ``("node", id)`` for the node of
a visual test that is running now (a few times a second at most), ``("shutdown",)``
when the devices start to be shut down, and ``("done", status, error, run_path)``.
The app sends ``(name, value)`` pairs on ``control_queue`` whenever the user changes
a front panel control.
"""

from __future__ import annotations

import os
import threading

NODE_REPORT_S = 0.05  # how often the running node is reported, when it changed

from .engine import run_project


def _follow_controls(control_queue, values: dict) -> None:
    while True:
        try:
            name, value = control_queue.get()
        except (EOFError, OSError, ValueError):
            return
        values[name] = value


class _NodeReporter:
    """Reports the running node now and then: a loop may start thousands of nodes a
    second, and the window only needs to show the latest."""

    def __init__(self, queue):
        self.queue = queue
        self.current: str | None = None
        self.done = threading.Event()
        threading.Thread(target=self._report, daemon=True).start()

    def __call__(self, node_id: str) -> None:
        self.current = node_id

    def _report(self) -> None:
        sent = None
        while not self.done.wait(NODE_REPORT_S):
            current = self.current
            if current != sent:
                self.queue.put(("node", current))
                sent = current


def run_worker(project_path: str, queue, stop_event, control_queue=None,
               panel_values: dict | None = None, stop_timeout: float | None = None) -> None:
    """``stop_timeout``: seconds a script gets to end after ``stop_event`` is set. After
    that the devices are shut down without it, the run is reported as stopped and this
    process ends itself, since the script may never return."""
    values = dict(panel_values or {})
    reporting = threading.Lock()  # "done" goes out once, whoever gets there first

    def report(status: str, error: str, path: str) -> bool:
        if not reporting.acquire(blocking=False):
            return False
        queue.put(("done", status, error, path))
        return True

    def abandoned(result) -> None:
        if report(result.status, result.error or "", str(result.path)):
            queue.close()
            queue.join_thread()  # the message is on its way before the process goes
            os._exit(0)

    if control_queue is not None:
        threading.Thread(target=_follow_controls, args=(control_queue, values),
                         daemon=True).start()
    nodes = _NodeReporter(queue)
    try:
        result = run_project(
            project_path,
            on_print=lambda text: queue.put(("print", text)),
            on_row=lambda row: queue.put(("row", row)),
            stop_event=stop_event,
            panel_values=values,
            on_panel=lambda name, value: queue.put(("panel", name, value)),
            on_check=lambda record: queue.put(("check", record)),
            on_waveform=lambda name, traces: queue.put(("waveform", name, traces)),
            stop_timeout=stop_timeout,
            on_shutdown=lambda: queue.put(("shutdown",)),
            on_abandoned=abandoned,
            on_node=nodes,
        )
        nodes.done.set()  # nothing about nodes after "done"
        report(result.status, result.error or "", str(result.path))
    except Exception as exc:  # failed before the run started
        nodes.done.set()
        report("error", str(exc), "")
