# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
import pytest


@pytest.fixture(autouse=True)
def benchloom_home(tmp_path_factory, monkeypatch):
    """Each test gets its own ~/.benchloom, so tests never touch the user's plugins or
    config. Runs started by a test inherit it through the environment."""
    home = tmp_path_factory.mktemp("benchloom_home")
    monkeypatch.setenv("BENCHLOOM_HOME", str(home))
    return home


# PySide6 can crash while Python shuts down after the tests ("none_dealloc", seen on
# Linux with Python 3.11), turning a passed run into exit code 134. Once pytest has
# reported its result, end the process straight away, keeping the exit status. On
# Windows os._exit still unloads Qt's DLLs, which crash on the windows and widgets
# the tests leave behind (an access violation, -1073741819), so the process is
# ended there without unloading anything.
_exit_status = None


def pytest_sessionfinish(session, exitstatus):
    global _exit_status
    _exit_status = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    import os
    import sys

    if _exit_status is not None and "PySide6" in sys.modules:
        sys.stdout.flush()
        sys.stderr.flush()
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32")
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
            kernel32.TerminateProcess(kernel32.GetCurrentProcess(), _exit_status)
        os._exit(_exit_status)
