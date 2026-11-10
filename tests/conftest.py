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
