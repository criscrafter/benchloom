"""Entry point of the process the desktop app runs a test in.

Kept free of Qt imports so the process starts quickly. Messages on the queue:
``("print", text)``, ``("row", dict)``, ``("done", status, error, run_path)``.
"""

from __future__ import annotations

from .engine import run_project


def run_worker(project_path: str, queue, stop_event) -> None:
    try:
        result = run_project(
            project_path,
            on_print=lambda text: queue.put(("print", text)),
            on_row=lambda row: queue.put(("row", row)),
            stop_event=stop_event,
        )
        queue.put(("done", result.status, result.error or "", str(result.path)))
    except Exception as exc:  # failed before the run started
        queue.put(("done", "error", str(exc), ""))
