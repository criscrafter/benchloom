# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Front panel: the controls and indicators of a project, and their live values.

The layout is stored in the project's ``panel.json``. A test reaches the widgets
by name: ``panel.target_voltage`` reads a control, ``panel.output_ok = true``
sets an indicator. A chart takes a number, or a table of numbers for several
curves (``panel.chart1 = { V = v, I = i }``); a table widget takes one row per
assignment (``panel.results = { step = 1, V = v }``) and is emptied with nil. This module has no Qt in it, so tests also run headless,
where controls keep their saved values.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

PANEL_FILE = "panel.json"
PANEL_VERSION = 1

# type -> (title, default width, default height)
WIDGET_TYPES: dict[str, tuple[str, int, int]] = {
    "number": ("Number input", 170, 64),
    "switch": ("Switch", 120, 64),
    "button": ("Button", 130, 40),
    "text": ("Text input", 210, 64),
    "display": ("Value display", 190, 80),
    "led": ("Indicator light", 120, 64),
    "bar": ("Bar", 230, 64),
    "chart": ("Chart", 380, 230),
    "gauge": ("Gauge", 200, 170),
    "table": ("Table", 380, 200),
    "label": ("Label", 170, 30),
}
CONTROLS = ("number", "switch", "button", "text")  # the user sets these
INDICATORS = ("display", "led", "bar", "gauge", "chart", "table")  # the test sets these
VALUE_TYPES = {"number": "number", "switch": "boolean", "button": "boolean", "text": "string",
               "led": "boolean", "bar": "number", "gauge": "number", "chart": "any",
               "table": "any"}
STREAMS = ("chart", "table")  # keep what the test sends instead of one current value
TABLE_ROWS = 10000  # a table widget shows at most the newest this many rows


class PanelError(Exception):
    pass


@dataclass
class Widget:
    type: str
    name: str
    label: str = ""
    x: int = 10
    y: int = 10
    w: int = 160
    h: int = 64
    unit: str = ""
    min: float = 0.0
    max: float = 100.0
    value: Any = None  # saved value of a control
    decimals: int | None = None  # digits after the decimal point; None: automatic
    x_value: str = ""  # chart: the entry of a table that is the X value instead of time
    log_x: bool = False  # chart: X axis on a log scale
    columns: list[str] | None = None  # table: these columns first, in this order

    @property
    def is_control(self) -> bool:
        return self.type in CONTROLS

    @property
    def has_value(self) -> bool:
        return self.type != "label"

    def initial(self) -> Any:
        if self.type == "number":
            return float(self.value) if isinstance(self.value, (int, float)) else float(self.min)
        if self.type in ("switch", "button"):
            return bool(self.value) if self.type == "switch" else False
        if self.type == "text":
            return "" if self.value is None else str(self.value)
        return None


def format_value(value: float, decimals: int | None = None, point: str = ".") -> str:
    """A number as people read it: fixed decimals, or about four significant digits.

    Automatic: trailing zeros go (50, not 50.000), and very large or small numbers
    use an exponent (1.5e-06).
    """
    if decimals is not None:
        text = f"{value:.{max(0, int(decimals))}f}"
    elif value == 0 or not math.isfinite(value):
        text = f"{value:g}"
    else:
        magnitude = math.floor(math.log10(abs(value)))
        if -3 <= magnitude < 7:
            text = f"{value:.{max(0, 3 - magnitude)}f}"
            if "." in text:
                text = text.rstrip("0").rstrip(".")
        else:
            text = f"{value:.4g}"
    if text.startswith("-") and float(text) == 0:
        text = text[1:]  # no "-0" from a tiny negative value
    return text.replace(".", point)


class Panel:
    def __init__(self, path: Path | None = None, widgets: list[Widget] | None = None):
        self.path = path
        self.widgets: list[Widget] = widgets or []

    @classmethod
    def load(cls, project_path: str | Path) -> "Panel":
        path = Path(project_path) / PANEL_FILE
        panel = cls(path)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                known = set(Widget.__dataclass_fields__)
                for entry in data.get("widgets", []):
                    if entry.get("type") in WIDGET_TYPES and entry.get("name"):
                        panel.widgets.append(
                            Widget(**{k: v for k, v in entry.items() if k in known}))
            except (ValueError, TypeError, AttributeError) as exc:
                raise PanelError(f"{path}: {exc}") from exc
        return panel

    def save(self) -> None:
        if self.path is None:
            return
        data = {"version": PANEL_VERSION, "widgets": [asdict(w) for w in self.widgets]}
        self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    def get(self, name: str) -> Widget | None:
        return next((w for w in self.widgets if w.name == name), None)

    def new_name(self, kind: str) -> str:
        n = 1
        while self.get(f"{kind}{n}"):
            n += 1
        return f"{kind}{n}"

    def add(self, kind: str) -> Widget:
        title, width, height = WIDGET_TYPES[kind]
        name = self.new_name(kind)
        bottom = max((w.y + w.h for w in self.widgets), default=0)
        widget = Widget(kind, name, title if kind in ("button", "label") else name,
                        10, bottom + 10, width, height)
        self.widgets.append(widget)
        return widget

    def control_values(self) -> dict[str, Any]:
        return {w.name: w.initial() for w in self.widgets if w.is_control}


def valid_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name))


class PanelState:
    """The panel as a running test sees it.

    ``values`` holds the controls' current values and may be updated from another
    thread while the test runs. ``on_change`` is told about everything the test sets.
    """

    def __init__(self, panel: Panel, values: dict[str, Any] | None = None,
                 on_change: Callable[[str, Any], None] | None = None):
        self.panel = panel
        self.values = panel.control_values()
        self.values.update(values or {})
        self.on_change = on_change

    def _widget(self, name: Any) -> Widget:
        widget = self.panel.get(name) if isinstance(name, str) else None
        if widget is None or not widget.has_value:
            raise PanelError(f"there is no front panel widget named '{name}'")
        return widget

    def get(self, name: str) -> Any:
        widget = self._widget(name)
        if widget.type == "button":  # true once per click
            pressed = bool(self.values.get(name))
            self.values[name] = False
            return pressed
        return self.values.get(name)

    def set(self, name: str, value: Any) -> None:
        widget = self._widget(name)
        kind = VALUE_TYPES.get(widget.type)
        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PanelError(f"panel.{name} needs a number, got {value!r}")
            value = float(value)
        elif kind == "boolean":
            value = bool(value)
        elif kind == "string":
            value = "" if value is None else str(value)
        elif widget.type == "chart":
            value = _chart_value(name, widget, value)
        elif widget.type == "table":
            value = _table_value(name, value)
        elif not isinstance(value, (int, float, str, bool, type(None))):
            raise PanelError(f"panel.{name} cannot show {value!r}")
        if widget.type not in STREAMS:
            self.values[name] = value
        if self.on_change is not None:
            self.on_change(name, value)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _chart_value(name: str, widget: Widget, value: Any) -> float | dict[str, float]:
    """A number (one curve), or curve name -> number (several curves at once)."""
    if _is_number(value):
        return float(value)
    if isinstance(value, dict) and value and all(_is_number(v) for v in value.values()):
        if widget.x_value and widget.x_value not in value:
            raise PanelError(f"panel.{name} needs '{widget.x_value}' in the table, "
                             "the chart's X value")
        return {str(k): float(v) for k, v in value.items()}
    raise PanelError(f"panel.{name} needs a number or a table of numbers, "
                     f"such as {{ V = 5, I = 0.2 }}, got {value!r}")


def _table_value(name: str, value: Any) -> dict[str, Any] | None:
    """One row to add (column -> number, text or boolean), or None to empty the table."""
    if value is None:
        return None
    if isinstance(value, dict) and value and all(
            isinstance(v, (int, float, str, bool)) for v in value.values()):
        return {str(k): v for k, v in value.items()}
    raise PanelError(f"panel.{name} needs a row such as {{ step = 1, V = 5 }}, "
                     f"or nil to empty it, got {value!r}")


def rename_in_graph(graph: dict, old: str, new: str) -> int:
    """Point a visual test's panel nodes at a renamed widget. Returns how many changed."""
    changed = 0
    for node in graph.get("nodes", []):
        if node.get("type") in ("panel.get", "panel.set") and node.get("widget") == old:
            node["widget"] = new
            changed += 1
    return changed


def rename_in_lua(code: str, old: str, new: str) -> tuple[str, int]:
    """Rename ``panel.old`` and ``panel["old"]`` in a script. Returns the code and how
    many places changed; ``panel.older`` or a variable named ``old`` stay as they are."""
    dotted = re.compile(rf"\bpanel(\s*\.\s*){re.escape(old)}\b")
    indexed = re.compile(rf"\bpanel(\s*\[\s*)([\"']){re.escape(old)}\2(\s*\])")
    code, a = dotted.subn(lambda m: f"panel{m.group(1)}{new}", code)
    code, b = indexed.subn(lambda m: f"panel{m.group(1)}{m.group(2)}{new}{m.group(2)}"
                                     f"{m.group(3)}", code)
    return code, a + b
