# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""``report.html``: one self-contained page per run, to read, print or pass on.

It shows the verdict of the checks, the checks themselves, a chart of the logged
data (drawn as the project's plot is set up), the saved waveforms, the devices,
the data table and the console output. Images are inline SVG, so the page needs
nothing next to it.
"""

from __future__ import annotations

import math
from html import escape
from typing import TYPE_CHECKING, Any

from .svgplot import Series, chart

if TYPE_CHECKING:
    from .datalog import RunLog

MAX_TABLE_ROWS = 500

STYLE = """
body { font-family: Segoe UI, Helvetica, Arial, sans-serif; color: #111827; margin: 24px auto;
       max-width: 960px; padding: 0 16px; line-height: 1.4; }
h1 { font-size: 22px; margin-bottom: 4px; }
h2 { font-size: 17px; margin-top: 28px; border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }
.meta { color: #4b5563; }
.verdict { display: inline-block; padding: 6px 14px; border-radius: 6px; font-weight: 700;
           font-size: 18px; margin: 12px 0; }
.pass { background: #dcfce7; color: #166534; }
.fail { background: #fee2e2; color: #991b1b; }
.none, .stopped, .error { background: #f3f4f6; color: #374151; }
.error { background: #fef3c7; color: #92400e; }
.shutdown-failed { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5;
                   border-radius: 6px; padding: 8px 12px; margin: 0 0 12px; }
.shutdown-failed ul { margin: 4px 0 0; }
table { border-collapse: collapse; font-size: 13px; margin: 8px 0; }
th, td { border: 1px solid #e5e7eb; padding: 3px 8px; text-align: left; }
th { background: #f9fafb; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.pass { color: #166534; font-weight: 600; background: none; }
td.fail { color: #991b1b; font-weight: 600; background: none; }
figure { margin: 12px 0 20px; }
svg { max-width: 100%; height: auto; border: 1px solid #e5e7eb; }
pre { background: #f9fafb; border: 1px solid #e5e7eb; padding: 8px; font-size: 12px;
      white-space: pre-wrap; }
@media print { h2 { break-after: avoid; } figure { break-inside: avoid; } }
"""


def _number(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6g}" if math.isfinite(value) else str(value)
    return escape(str(value))


def _data_chart(rows: list[dict], plot: dict[str, Any]) -> str:
    numeric = [k for k in dict.fromkeys(k for r in rows for k in r)
               if any(isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)
                      for r in rows)]
    x = plot.get("x") if plot.get("x") in numeric else "time_s"
    hidden = set(plot.get("hide", []) if not isinstance(plot.get("hide"), str) else [plot["hide"]])
    right = set(plot.get("right", []) if not isinstance(plot.get("right"), str) else [plot["right"]])
    columns = [k for k in numeric if k != x and k not in hidden]
    if x not in numeric or not columns:
        return ""
    series = [Series(k, [r.get(x) for r in rows], [r.get(k) for r in rows], right=k in right)
              for k in columns]
    return chart(series, x_label=x, y_label=", ".join(k for k in columns if k not in right),
                 y2_label=", ".join(k for k in columns if k in right),
                 log_x=bool(plot.get("log_x")))


def build_report(run: "RunLog", meta: dict[str, Any], plot: dict[str, Any]) -> str:
    project = meta.get("project", "")
    verdict = meta.get("result", "none")
    status = meta.get("status", "")
    if status != "ok":
        badge, css = {"stopped": "STOPPED", "error": "ERROR"}.get(status, status.upper()), status
    else:
        badge, css = {"pass": "PASS", "fail": "FAIL", "none": "FINISHED"}[verdict], verdict
    checks = meta.get("checks", {})
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{escape(project)} – {escape(meta.get('started', ''))}</title>",
        f"<style>{STYLE}</style></head><body>",
        f"<h1>{escape(project)}</h1>",
        f"<div class='meta'>Run {escape(run.path.name)} · started "
        f"{escape(meta.get('started', '').replace('T', ' '))} · "
        f"{meta.get('duration_s', 0):.1f} s · script {escape(meta.get('script', ''))} · "
        f"Benchloom {escape(str(meta.get('benchloom', '')))}</div>",
        f"<div class='verdict {css}'>{badge}</div>",
    ]
    failed = {name: how for name, how in (meta.get("shutdown") or {}).items() if how != "done"}
    if failed:
        items = "".join(f"<li><b>{escape(name)}</b>: {escape(how.removeprefix('failed: '))}</li>"
                        for name, how in failed.items())
        parts.append("<div class='shutdown-failed'><b>Not every device could be shut down; "
                     f"check that their outputs are off.</b><ul>{items}</ul></div>")
    summary = []
    if run.checks:
        summary.append(f"{checks.get('passed', 0)} of {len(run.checks)} checks passed")
    summary.append(f"{len(run.rows)} rows logged")
    if run.waveforms:
        summary.append(f"{len(run.waveforms)} waveforms saved")
    parts.append(f"<p>{escape(', '.join(summary))}.</p>")
    if meta.get("error"):
        parts.append(f"<pre>{escape(meta['error'])}</pre>")

    if run.checks:
        parts.append("<h2>Checks</h2><table><tr><th>Check</th><th>Value</th><th>Limits</th>"
                     "<th>Result</th><th>Time (s)</th></tr>")
        for c in run.checks:
            unit = f" {escape(c.unit)}" if c.unit else ""
            result = "pass" if c.passed else "fail"
            parts.append(f"<tr><td>{escape(c.name)}</td><td class='num'>{_number(c.value)}{unit}"
                         f"</td><td>{escape(c.limits())}{unit}</td><td class='{result}'>"
                         f"{result.upper()}</td><td class='num'>{c.time_s:g}</td></tr>")
        parts.append("</table>")

    if run.rows:
        svg = _data_chart(run.rows, plot)
        if svg:
            parts.append(f"<h2>Data</h2><figure>{svg}</figure>")

    if run.waveforms:
        parts.append("<h2>Waveforms</h2>")
        for w in run.waveforms:
            svg = (run.path / f"{w.file}.svg").read_text(encoding="utf-8")
            parts.append(f"<figure>{svg}<figcaption>{escape(w.file)}.csv · at "
                         f"{w.time_s:g} s</figcaption></figure>")

    devices = meta.get("devices", {})
    if devices:
        parts.append("<h2>Devices</h2><table><tr><th>Name</th><th>Device</th>"
                     "<th>Connection</th></tr>")
        parts += [f"<tr><td>{escape(n)}</td><td>{escape(d.get('device', ''))}</td>"
                  f"<td>{escape(d.get('resource', ''))}</td></tr>" for n, d in devices.items()]
        parts.append("</table>")

    if run.rows:
        columns = list(dict.fromkeys(k for r in run.rows for k in r))
        shown = run.rows[:MAX_TABLE_ROWS]
        parts.append("<h2>Logged rows</h2><table><tr>"
                     + "".join(f"<th>{escape(c)}</th>" for c in columns) + "</tr>")
        for r in shown:
            parts.append("<tr>" + "".join(
                f"<td class='num'>{_number(r[c])}</td>" if c in r else "<td></td>"
                for c in columns) + "</tr>")
        parts.append("</table>")
        if len(run.rows) > len(shown):
            parts.append(f"<p>The first {len(shown)} of {len(run.rows)} rows; all are in "
                         "data.csv.</p>")

    console = (run.path / "console.log").read_text(encoding="utf-8", errors="replace")
    if console.strip():
        parts.append(f"<h2>Console</h2><pre>{escape(console)}</pre>")
    parts.append("</body></html>")
    return "\n".join(parts)


def write_report(run: "RunLog", meta: dict[str, Any], plot: dict[str, Any]) -> None:
    (run.path / "report.html").write_text(build_report(run, meta, plot), encoding="utf-8")
