"""A simulated bench: lets simulators in one run see each other's outputs.

A simulated signal source (such as the FY6900 simulator) registers itself here,
and a simulated measuring instrument (such as the Hantek simulator with a
``dut`` option) looks up what that source puts out. That way a project wired
like the real bench, generator into a circuit into a scope, also runs on
simulators. Sources are held weakly, so a closed simulator drops out.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass


@dataclass
class Signal:
    """A sine on a source's output: frequency, peak amplitude and DC offset."""

    hertz: float
    peak: float
    offset: float = 0.0


_sources: "weakref.WeakValueDictionary[str, object]" = weakref.WeakValueDictionary()


def add_source(name: str, source) -> None:
    """Register ``source``; it must have ``bench_signal(output) -> Signal | None``."""
    _sources[name] = source


def signal(output: str = "ch1") -> Signal | None:
    """The signal on ``output`` of the most recently registered source, if any."""
    sources = list(_sources.values())
    return sources[-1].bench_signal(output) if sources else None
