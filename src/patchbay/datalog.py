"""Run folders: every run keeps its data, console output and script together."""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class RunLog:
    def __init__(self, runs_dir: Path):
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
        self._columns = ["time_s"]
        self._console = open(self.path / "console.log", "w", encoding="utf-8")

    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    def console(self, text: str) -> None:
        self._console.write(text + "\n")
        self._console.flush()

    def log(self, values: dict[str, Any]) -> None:
        """Append one row to data.csv; new keys become new columns."""
        row = {"time_s": round(self.elapsed(), 3), **values}
        self.rows.append(row)
        new = [key for key in row if key not in self._columns]
        file = self.path / "data.csv"
        if new or len(self.rows) == 1:
            self._columns += sorted(new)
            with open(file, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, self._columns, restval="")
                writer.writeheader()
                writer.writerows(self.rows)
        else:
            with open(file, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, self._columns, restval="").writerow(row)

    def finish(self, status: str, error: str | None, info: dict[str, Any]) -> None:
        self._console.close()
        meta = {
            **info,
            "started": self.started.isoformat(timespec="seconds"),
            "duration_s": round(self.elapsed(), 3),
            "status": status,
            "error": error,
            "rows": len(self.rows),
        }
        (self.path / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
