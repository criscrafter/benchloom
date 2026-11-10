# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Front panel: the controls and indicators of a project, and their live values.

The layout is stored in the project's ``panel.json``. A test reaches the widgets
by name: ``panel.target_voltage`` reads a control, ``panel.output_ok = true``
sets an indicator. This module has no Qt in it, so tests also run headless,
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
    "label": ("Label", 170, 30),
}
CONTROLS = ("number", "switch", "button", "text")  # the user sets these
INDICATORS = ("display", "led", "bar", "chart")  # the test sets these
VALUE_TYPES = {"number": "number", "switch": "boolean", "button": "boolean", "text": "string",
               "led": "boolean", "bar": "number", "chart": "number"}


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
        elif not isinstance(value, (int, float, str, bool, type(None))):
            raise PanelError(f"panel.{name} cannot show {value!r}")
        if widget.type != "chart":
            self.values[name] = value
        if self.on_change is not None:
            self.on_change(name, value)
