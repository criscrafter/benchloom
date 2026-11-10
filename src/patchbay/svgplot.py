"""Charts as SVG text: waveforms and logged data, for run folders and reports.

Pure Python, so a run can draw its images without Qt. Values that are not
finite (an infinite gain, say) are left out of the lines.
"""

from __future__ import annotations

import math
from html import escape
from typing import Sequence

COLORS = ["#2563eb", "#ea580c", "#059669", "#dc2626", "#7c3aed", "#ca8a04", "#0891b2", "#db2777"]
FONT = "Segoe UI, Helvetica, Arial, sans-serif"
PREFIXES = [(1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"), (1.0, ""), (1e3, "k"), (1e6, "M"),
            (1e9, "G")]


def si(value: float, unit: str = "", digits: int = 4) -> str:
    """1.5e-06 s -> '1.5 µs'."""
    if value == 0 or not math.isfinite(value):
        return f"{value:g} {unit}".strip()
    scale, prefix = PREFIXES[0]
    for s, p in PREFIXES:
        if abs(value) >= s * 0.9995:
            scale, prefix = s, p
    return f"{value / scale:.{digits}g} {prefix}{unit}".strip()


def nice_ticks(low: float, high: float, count: int = 6) -> list[float]:
    """Round tick positions covering low..high."""
    if not (math.isfinite(low) and math.isfinite(high)):
        return [0.0]
    if high <= low:
        high = low + (abs(low) or 1.0)
    raw = (high - low) / max(count - 1, 1)
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw * 0.999)
    start = math.floor(low / step + 1e-9) * step
    ticks, value = [], start
    while value <= high + step * 1e-6:
        ticks.append(0.0 if abs(value) < step * 1e-9 else value)
        value = start + len(ticks) * step
    if ticks[-1] < high - step * 1e-6:
        ticks.append(ticks[-1] + step)
    return ticks


def _label(value: float, step: float) -> str:
    decimals = max(0, -math.floor(math.log10(step) + 1e-9)) if step > 0 else 0
    return f"{value:.{min(decimals, 9)}f}"


class Series:
    def __init__(self, name: str, xs: Sequence[float], ys: Sequence[float],
                 right: bool = False, color: str | None = None):
        self.name, self.right, self.color = name, right, color
        self.points = [(float(x), float(y)) for x, y in zip(xs, ys)
                       if isinstance(x, (int, float)) and isinstance(y, (int, float))
                       and math.isfinite(x) and math.isfinite(y)]


def chart(series: list[Series], *, title: str = "", x_label: str = "", y_label: str = "",
          y2_label: str = "", log_x: bool = False, width: int = 900, height: int = 420,
          notes: Sequence[str] = ()) -> str:
    """A line chart: series on a left scale and, if any are marked ``right``, a right one."""
    left = [s for s in series if not s.right and s.points]
    right = [s for s in series if s.right and s.points]
    for i, s in enumerate(series):
        s.color = s.color or COLORS[i % len(COLORS)]
    if log_x:
        for s in series:
            s.points = [(x, y) for x, y in s.points if x > 0]
    xs = [x for s in series for x, _ in s.points]
    top = 34 if title else 14
    margin_l, margin_r = 70, 70 if right else 24
    legend_h = 22 * ((len([s for s in series if s.points]) + 2) // 3) + 8 * bool(notes) \
        + 18 * len(notes)
    plot_w = width - margin_l - margin_r
    plot_h = height - top - 52 - legend_h
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" font-family="{FONT}" font-size="12">',
           f'<rect width="{width}" height="{height}" fill="#ffffff"/>']
    if title:
        out.append(f'<text x="{margin_l}" y="22" font-size="15" font-weight="600" '
                   f'fill="#111827">{escape(title)}</text>')
    if not xs:
        out.append(f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" '
                   f'fill="#6b7280">No data</text></svg>')
        return "\n".join(out)

    # x scale
    x_lo, x_hi = min(xs), max(xs)
    if log_x:
        lo_d, hi_d = math.floor(math.log10(x_lo)), math.ceil(math.log10(x_hi))
        hi_d = max(hi_d, lo_d + 1)
        x_ticks = [10.0 ** d for d in range(lo_d, hi_d + 1)]
        fx_lo, fx_hi = math.log10(x_ticks[0]), math.log10(x_ticks[-1])

        def px(x: float) -> float:
            return margin_l + (math.log10(x) - fx_lo) / (fx_hi - fx_lo) * plot_w
    else:
        x_ticks = nice_ticks(x_lo, x_hi)

        def px(x: float) -> float:
            return margin_l + (x - x_ticks[0]) / ((x_ticks[-1] - x_ticks[0]) or 1) * plot_w

    def y_scale(group):
        ys = [y for s in group for _, y in s.points]
        ticks = nice_ticks(min(ys), max(ys)) if ys else [0.0, 1.0]
        span = (ticks[-1] - ticks[0]) or 1.0
        return ticks, lambda y: top + plot_h - (y - ticks[0]) / span * plot_h

    y_ticks, py = y_scale(left or right)
    y2_ticks, py2 = y_scale(right) if right and left else (y_ticks, py)

    # grid and axes
    out.append(f'<rect x="{margin_l}" y="{top}" width="{plot_w}" height="{plot_h}" '
               f'fill="#fafafa" stroke="#9ca3af"/>')
    if log_x:
        for d in range(len(x_ticks) - 1):
            for m in range(2, 10):
                x = px(x_ticks[d] * m)
                out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" '
                           f'stroke="#eef0f3"/>')
    for t in x_ticks:
        x = px(t)
        out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" '
                   f'stroke="#e5e7eb"/>')
        text = si(t, digits=3) if log_x else _label(t, x_ticks[1] - x_ticks[0])
        out.append(f'<text x="{x:.1f}" y="{top + plot_h + 16}" text-anchor="middle" '
                   f'fill="#374151">{escape(text)}</text>')
    step = y_ticks[1] - y_ticks[0] if len(y_ticks) > 1 else 1
    for t in y_ticks:
        y = py(t)
        out.append(f'<line x1="{margin_l}" y1="{y:.1f}" x2="{margin_l + plot_w}" y2="{y:.1f}" '
                   f'stroke="#e5e7eb"/>')
        out.append(f'<text x="{margin_l - 6}" y="{y + 4:.1f}" text-anchor="end" '
                   f'fill="#374151">{_label(t, step)}</text>')
    if right and left:
        step2 = y2_ticks[1] - y2_ticks[0] if len(y2_ticks) > 1 else 1
        for t in y2_ticks:
            out.append(f'<text x="{margin_l + plot_w + 6}" y="{py2(t) + 4:.1f}" '
                       f'fill="#374151">{_label(t, step2)}</text>')
    if x_label:
        out.append(f'<text x="{margin_l + plot_w / 2}" y="{top + plot_h + 34}" '
                   f'text-anchor="middle" fill="#111827">{escape(x_label)}</text>')
    if y_label:
        cy = top + plot_h / 2
        out.append(f'<text transform="translate(16 {cy}) rotate(-90)" text-anchor="middle" '
                   f'fill="#111827">{escape(y_label)}</text>')
    if y2_label and right and left:
        cy = top + plot_h / 2
        out.append(f'<text transform="translate({width - 12} {cy}) rotate(90)" '
                   f'text-anchor="middle" fill="#111827">{escape(y2_label)}</text>')

    # lines
    # No clip path: Qt's SVG renderer does not know it (it drew the clip rectangle
    # black), and the scales cover all the data anyway.
    out.append('<g fill="none">')
    for s in left + right:
        scale = py2 if s.right and left else py
        points = " ".join(f"{px(x):.1f},{scale(y):.1f}" for x, y in s.points)
        dots = len(s.points) <= 60
        out.append(f'<polyline points="{points}" stroke="{s.color}" stroke-width="1.6"/>')
        if dots:
            out += [f'<circle cx="{px(x):.1f}" cy="{scale(y):.1f}" r="2.5" fill="{s.color}"/>'
                    for x, y in s.points]
    out.append("</g>")

    # legend and notes
    y = top + plot_h + 52
    for i, s in enumerate([s for s in series if s.points]):
        x = margin_l + (i % 3) * (plot_w / 3)
        row = y + (i // 3) * 22
        name = s.name + (" (right)" if s.right and left else "")
        out.append(f'<rect x="{x:.1f}" y="{row - 9}" width="14" height="4" fill="{s.color}"/>'
                   f'<text x="{x + 20:.1f}" y="{row - 3}" fill="#111827">{escape(name)}</text>')
    y += 22 * ((len([s for s in series if s.points]) + 2) // 3) + 4
    for note in notes:
        out.append(f'<text x="{margin_l}" y="{y}" fill="#4b5563">{escape(note)}</text>')
        y += 18
    out.append("</svg>")
    return "\n".join(out)


def time_unit(times: Sequence[float]) -> tuple[float, str]:
    """The scale and unit a time axis reads best in."""
    span = (max(times) - min(times)) if times else 1.0
    for scale, unit in ((1e-9, "ns"), (1e-6, "µs"), (1e-3, "ms")):
        if span < scale * 2000:
            return scale, unit
    return 1.0, "s"


def describe(times: Sequence[float], volts: Sequence[float]) -> str:
    """'4.02 Vpp, mean 12 mV, 1 kHz': what a scope's measure menu would show."""
    if not volts:
        return "no samples"
    vpp = max(volts) - min(volts)
    mean = sum(volts) / len(volts)
    if abs(mean) < vpp * 1e-4:
        mean = 0.0  # rounding noise, not an offset
    parts = [f"{si(vpp, 'Vpp', 3)}", f"mean {si(mean, 'V', 3)}"]
    level, edges, armed = (max(volts) + min(volts)) / 2, [], False
    hyst = vpp * 0.1
    for i in range(len(volts) - 1):
        if volts[i] < level - hyst:
            armed = True
        if armed and volts[i] < level <= volts[i + 1]:
            share = (level - volts[i]) / (volts[i + 1] - volts[i])  # between two samples
            edges.append(times[i] + share * (times[i + 1] - times[i]))
            armed = False
    if len(edges) >= 2 and vpp > 0:
        parts.append(si((len(edges) - 1) / (edges[-1] - edges[0]), "Hz", 4))
    return ", ".join(parts)


def waveform_svg(traces: dict[str, tuple[Sequence[float], Sequence[float]]],
                 title: str = "", width: int = 900, height: int = 420) -> str:
    """Scope traces (time in s, volts) on one volts scale, with Vpp, mean and frequency."""
    all_times = [t for times, _ in traces.values() for t in times]
    scale, unit = time_unit(all_times)
    series = [Series(name, [t / scale for t in times], volts)
              for name, (times, volts) in traces.items()]
    notes = [f"{name}: {describe(times, volts)}" for name, (times, volts) in traces.items()]
    return chart(series, title=title, x_label=f"Time ({unit})", y_label="Voltage (V)",
                 width=width, height=height, notes=notes)
