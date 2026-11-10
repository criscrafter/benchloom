"""Entry point of the process the desktop app runs a test in.

Kept free of Qt imports so the process starts quickly. Messages on the queue:
``("print", text)``, ``("row", dict)``, ``("panel", name, value)``,
``("check", dict)``, ``("waveform", name, traces)`` and
``("done", status, error, run_path)``. The app sends ``(name, value)`` pairs on
``control_queue`` whenever the user changes a front panel control.
"""

from __future__ import annotations

import threading

from .engine import run_project


def _follow_controls(control_queue, values: dict) -> None:
    while True:
        try:
            name, value = control_queue.get()
        except (EOFError, OSError, ValueError):
            return
        values[name] = value


def run_worker(project_path: str, queue, stop_event, control_queue=None,
               panel_values: dict | None = None) -> None:
    values = dict(panel_values or {})
    if control_queue is not None:
        threading.Thread(target=_follow_controls, args=(control_queue, values),
                         daemon=True).start()
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
        )
        queue.put(("done", result.status, result.error or "", str(result.path)))
    except Exception as exc:  # failed before the run started
        queue.put(("done", "error", str(exc), ""))
