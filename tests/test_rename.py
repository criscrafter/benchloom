# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Projects, folders and settings from before the rename to Benchloom keep working."""
from pathlib import Path

from benchloom import config
from benchloom.engine import run_project
from benchloom.project import LEGACY_PROJECT_FILE, PROJECT_FILE, Project, create_project, is_project


def test_a_patchbay_project_still_opens_runs_and_saves(tmp_path):
    project = create_project(tmp_path / "old")
    (project.path / PROJECT_FILE).rename(project.path / LEGACY_PROJECT_FILE)
    assert is_project(project.path)
    old = Project.load(project.path)
    old.name = "renamed"
    old.save()
    assert not (project.path / PROJECT_FILE).exists()  # it keeps its patchbay.toml
    assert Project.load(project.path).name == "renamed"
    assert run_project(project.path, on_print=lambda _: None).status == "ok"


def test_the_old_home_folder_is_used_until_there_is_a_new_one(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCHLOOM_HOME", raising=False)
    monkeypatch.delenv("PATCHBAY_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert config.home_dir() == tmp_path / ".benchloom"
    (tmp_path / ".patchbay").mkdir()
    assert config.home_dir() == tmp_path / ".patchbay"
    (tmp_path / ".benchloom").mkdir()
    assert config.home_dir() == tmp_path / ".benchloom"
    monkeypatch.setenv("PATCHBAY_HOME", str(tmp_path / "x"))
    assert config.home_dir() == tmp_path / "x"
