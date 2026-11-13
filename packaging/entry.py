# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""What the packaged programs run: Benchloom.exe (the app) and benchloom-cli.exe.

Both also serve as Benchloom's helper processes, because a packaged app has no
``python -m``: test runs (multiprocessing), the PNG helper (``--benchloom-svgpng``)
and a self-test of the package (``--selftest``).
"""

from __future__ import annotations

import multiprocessing
import sys


def main(gui: bool) -> int:
    multiprocessing.freeze_support()  # a test run's process starts here and stops here
    from benchloom.datalog import SVGPNG_ARG

    args = sys.argv[1:]
    if args[:1] == [SVGPNG_ARG]:
        from benchloom.svgpng import main as svgpng

        return svgpng(args[1:])
    if args[:1] == ["--selftest"]:
        return selftest()
    if gui:
        from benchloom.gui import main as app

        return app(args[0] if args else None)
    from benchloom.cli import main as cli

    return cli(args)


def selftest() -> int:
    """Run the Bode template on simulators the way the app does, in a separate process,
    and check what it leaves behind. Prints the result; exit code 0 means it works."""
    import json
    import os
    import queue
    import tempfile
    from pathlib import Path

    from benchloom import __version__
    from benchloom.project import create_project
    from benchloom.worker import run_worker

    folder = Path(tempfile.mkdtemp(prefix="benchloom-selftest-"))
    os.environ.setdefault("BENCHLOOM_HOME", str(folder / "home"))
    project = create_project(folder / "bode", template="bode")
    context = multiprocessing.get_context("spawn")
    messages, stop = context.Queue(), context.Event()
    values = {"start_hz": 10.0, "stop_hz": 1000.0, "points_per_decade": 2.0,
              "check_limits": True, "limit_low_hz": 140.0, "limit_high_hz": 160.0}
    process = context.Process(target=run_worker,
                              args=(str(project.path), messages, stop, None, values))
    process.start()
    done = None
    while done is None:
        try:
            message = messages.get(timeout=120)
        except queue.Empty:
            break
        if message[0] == "done":
            done = message
    process.join(10)
    problems = []
    if done is None or done[1] != "ok":
        problems.append(f"the run did not finish: {done}")
    else:
        run = Path(done[3])
        meta = json.loads((run / "run.json").read_text(encoding="utf-8"))
        if meta.get("result") != "pass":
            problems.append(f"verdict {meta.get('result')}, expected pass")
        pngs = list((run / "waveforms").glob("*.png"))
        if len(pngs) != 5:
            problems.append(f"{len(pngs)} waveform PNGs, expected 5")
        if "PASS" not in (run / "report.html").read_text(encoding="utf-8"):
            problems.append("the report has no PASS")
    try:  # visa:// works: pyvisa and its pure-Python backend are in the package
        import pyvisa

        manager = pyvisa.ResourceManager("@py")  # a vendor VISA is used when installed
        print(f"VISA: pyvisa {pyvisa.__version__}, {manager.visalib}")
        manager.close()
    except Exception as exc:  # noqa: BLE001 - report anything
        problems.append(f"VISA support is missing: {exc!r}")
    try:  # the app's windows can be built
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        from benchloom.gui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(QSettings(str(folder / "settings.ini"),
                                      QSettings.Format.IniFormat))
        window.open_project(project.path)
        window.close()
        del app
    except Exception as exc:  # noqa: BLE001 - report anything
        problems.append(f"the app could not be built: {exc!r}")
    for problem in problems:
        print("FAIL", problem)
    if not problems:
        print(f"Benchloom {__version__} self-test passed ({folder})")
    return 1 if problems else 0
