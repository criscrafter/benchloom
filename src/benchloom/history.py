# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""A project's past runs, read back from its ``runs/`` folder.

Each run folder holds ``run.json`` (status, verdict, checks, start and duration)
and ``data.csv``. Folders without a readable ``run.json`` are listed too: a run
in progress, or one whose process was killed.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class RunInfo:
    path: Path
    started: datetime | None = None
    duration_s: float | None = None
    status: str = "unknown"  # ok, error, stopped; unknown: no run.json (yet)
    result: str = "none"  # pass, fail, none
    rows: int = 0
    passed: int = 0
    failed: int = 0
    error: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def has_report(self) -> bool:
        return (self.path / "report.html").is_file()


def read_run(folder: Path) -> RunInfo:
    info = RunInfo(Path(folder))
    try:
        meta = json.loads((info.path / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = None
    if not isinstance(meta, dict):
        try:  # the folder name is the start time: 2026-11-12_013920 or ..._2
            info.started = datetime.strptime(info.path.name[:17], "%Y-%m-%d_%H%M%S")
        except ValueError:
            pass
        return info
    try:
        info.started = datetime.fromisoformat(str(meta.get("started")))
    except ValueError:
        pass
    duration = meta.get("duration_s")
    info.duration_s = float(duration) if isinstance(duration, (int, float)) else None
    info.status = str(meta.get("status") or "unknown")
    info.result = str(meta.get("result") or "none")
    info.rows = int(meta.get("rows") or 0)
    checks = meta.get("checks") if isinstance(meta.get("checks"), dict) else {}
    info.passed, info.failed = int(checks.get("passed", 0)), int(checks.get("failed", 0))
    info.error = str(meta.get("error") or "")
    return info


def list_runs(project_dir: str | Path) -> list[RunInfo]:
    """The project's runs, newest first."""
    runs_dir = Path(project_dir) / "runs"
    if not runs_dir.is_dir():
        return []
    runs = [read_run(folder) for folder in runs_dir.iterdir() if folder.is_dir()]
    return sorted(runs, key=lambda r: (r.started or datetime.min, r.name), reverse=True)


def _value(text: str) -> Any:
    if text == "":
        return None
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return {"True": True, "False": False}.get(text, text)


def read_rows(run_dir: str | Path) -> list[dict[str, Any]]:
    """The rows of a run's data.csv, with numbers as numbers; empty cells left out."""
    path = Path(run_dir) / "data.csv"
    try:
        with open(path, newline="", encoding="utf-8") as file:
            return [{k: _value(v) for k, v in row.items() if v not in ("", None)}
                    for row in csv.DictReader(file)]
    except OSError:
        return []
