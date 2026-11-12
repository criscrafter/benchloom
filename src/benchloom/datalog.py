# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Run folders: every run keeps its data, console output and script together.

A run folder holds ``data.csv`` (the logged rows), ``console.log``, ``run.json``
(how the run went), a copy of the script, ``checks.csv`` (pass/fail checks, when
the test made any), ``waveforms/`` (saved scope traces as CSV, SVG and PNG) and
``report.html``, which shows all of it on one page.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .svgplot import waveform_svg

# The packaged app's programs start the PNG helper when given this first argument.
SVGPNG_ARG = "--benchloom-svgpng"


@dataclass
class Check:
    name: str
    value: Any
    low: float | None
    high: float | None
    unit: str
    passed: bool
    time_s: float

    def limits(self) -> str:
        if self.low is not None and self.high is not None:
            return f"{self.low:g} to {self.high:g}"
        if self.low is not None:
            return f"at least {self.low:g}"
        if self.high is not None:
            return f"at most {self.high:g}"
        return "true"


@dataclass
class Waveform:
    name: str
    file: str  # path relative to the run folder, without extension
    traces: dict[str, tuple[list, list]]
    time_s: float


def evaluate(value: Any, low: float | None, high: float | None) -> bool:
    """Whether a check passes: a number within its limits, or true without limits."""
    if low is None and high is None:
        return value is True or (isinstance(value, (int, float)) and not isinstance(value, bool)
                                 and math.isfinite(value) and value != 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        return False
    return (low is None or value >= low) and (high is None or value <= high)


class RunLog:
    """One run's folder and files.

    ``columns`` lists logged columns in the order to keep them: in data.csv, the
    rows handed on (so the plot and the Data tab), and the report. Other columns
    follow, in alphabetical order. A Lua table has no order of its own.
    """

    def __init__(self, runs_dir: Path, columns: list[str] | None = None):
        self.order = [str(c) for c in columns or [] if str(c) != "time_s"]
        self.started = datetime.now()
        self._t0 = time.monotonic()
        stamp = self.started.strftime("%Y-%m-%d_%H%M%S")
        self.path = runs_dir / stamp
        n = 2
        while self.path.exists():
            self.path = runs_dir / f"{stamp}_{n}"
            n += 1
        self.path.mkdir(parents=True)
        self.rows: list[dict[str, Any]] = []
        self.checks: list[Check] = []
        self.waveforms: list[Waveform] = []
        self._columns = ["time_s"]
        self._console = open(self.path / "console.log", "w", encoding="utf-8")

    def _rank(self, key: str) -> tuple:
        """Sort key: the columns asked for, in their order, then the others by name."""
        return (self.order.index(key), "") if key in self.order else (len(self.order), key)

    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    def console(self, text: str) -> None:
        self._console.write(text + "\n")
        self._console.flush()

    def log(self, values: dict[str, Any]) -> dict[str, Any]:
        """Append one row to data.csv; new keys become new columns. Returns the row."""
        row = {"time_s": round(self.elapsed(), 3),
               **{k: values[k] for k in sorted(values, key=self._rank)}}
        self.rows.append(row)
        new = [key for key in row if key not in self._columns]
        file = self.path / "data.csv"
        if new or len(self.rows) == 1:
            self._columns += sorted(new, key=self._rank)
            with open(file, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, self._columns, restval="")
                writer.writeheader()
                writer.writerows(self.rows)
        else:
            with open(file, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, self._columns, restval="").writerow(row)
        return row

    # --- checks -------------------------------------------------------------
    def check(self, name: str, value: Any, low: float | None = None, high: float | None = None,
              unit: str = "") -> Check:
        """Record a pass/fail check and append it to checks.csv."""
        result = Check(str(name), value, low, high, unit or "", evaluate(value, low, high),
                       round(self.elapsed(), 3))
        self.checks.append(result)
        file = self.path / "checks.csv"
        new = not file.exists()
        with open(file, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new:
                writer.writerow(["time_s", "name", "value", "low", "high", "unit", "result"])
            writer.writerow([result.time_s, result.name, result.value,
                             "" if low is None else low, "" if high is None else high,
                             result.unit, "pass" if result.passed else "fail"])
        return result

    def verdict(self) -> str:
        """'pass', 'fail', or 'none' when the test made no checks."""
        if not self.checks:
            return "none"
        return "pass" if all(c.passed for c in self.checks) else "fail"

    # --- waveforms ------------------------------------------------------------
    def save_waveform(self, name: str, traces: dict[str, tuple[Sequence, Sequence]]) -> Waveform:
        """Save scope traces (name -> (times, volts)) as CSV and SVG under waveforms/."""
        folder = self.path / "waveforms"
        folder.mkdir(exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name)).strip("_") or "waveform"
        stem = f"{len(self.waveforms) + 1:03d}_{slug}"[:80]
        traces = {str(k): (list(t), list(v)) for k, (t, v) in traces.items()}
        names = list(traces)
        shared = len({tuple(t) for t, _ in traces.values()}) == 1
        with open(folder / f"{stem}.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if shared:
                times = traces[names[0]][0]
                writer.writerow(["time_s"] + [f"{n}_V" for n in names])
                writer.writerows([t] + [traces[n][1][i] for n in names]
                                 for i, t in enumerate(times))
            else:
                writer.writerow([c for n in names for c in (f"{n}_time_s", f"{n}_V")])
                longest = max(len(t) for t, _ in traces.values())
                for i in range(longest):
                    writer.writerow([x for n in names for x in (
                        (traces[n][0][i], traces[n][1][i]) if i < len(traces[n][0]) else ("", ""))])
        (folder / f"{stem}.svg").write_text(waveform_svg(traces, str(name)), encoding="utf-8")
        waveform = Waveform(str(name), f"waveforms/{stem}", traces, round(self.elapsed(), 3))
        self.waveforms.append(waveform)
        return waveform

    def _render_pngs(self) -> None:
        """PNG copies of the waveform images, drawn by Qt in a helper process, when the
        desktop app is installed. The run itself never loads Qt."""
        frozen = getattr(sys, "frozen", False)  # the packaged app: no python -m there
        if not self.waveforms or (not frozen and importlib.util.find_spec("PySide6") is None):
            return
        files = [str(self.path / f"{w.file}.svg") for w in self.waveforms]
        helper = [sys.executable, SVGPNG_ARG] if frozen else [sys.executable, "-m",
                                                              "benchloom.svgpng"]
        try:
            subprocess.run(helper + files, timeout=60 + 2 * len(files), capture_output=True,
                           check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass  # the SVG images stay

    # --- end of the run -------------------------------------------------------
    def finish(self, status: str, error: str | None, info: dict[str, Any],
               plot: dict[str, Any] | None = None) -> None:
        self._console.close()
        passed = sum(c.passed for c in self.checks)
        meta = {
            **info,
            "started": self.started.isoformat(timespec="seconds"),
            "duration_s": round(self.elapsed(), 3),
            "status": status,
            "error": error,
            "rows": len(self.rows),
            "result": self.verdict(),
            "checks": {"passed": passed, "failed": len(self.checks) - passed},
            "waveforms": [w.file for w in self.waveforms],
        }
        (self.path / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self._render_pngs()
        from .report import write_report

        try:
            write_report(self, meta, plot or {})
        except Exception as exc:  # a report must never turn a finished run into a crash
            (self.path / "report.html").write_text(
                f"<p>The report could not be made: {exc!r}</p>", encoding="utf-8")

    def check_records(self) -> list[dict[str, Any]]:
        return [asdict(c) for c in self.checks]
