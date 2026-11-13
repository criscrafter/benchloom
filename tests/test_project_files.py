# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Empty projects, the saved_with stamp, renaming and moving projects."""
import json
import os

import pytest

from benchloom import __version__
from benchloom.engine import run_project
from benchloom.project import (
    LEGACY_PROJECT_FILE, PROJECT_FILE, Project, ProjectError, create_project,
    duplicate_project, last_saved,
    move_project, rename_project, stamp_version,
)


def test_empty_lua_project_has_no_devices_and_runs(tmp_path):
    project = create_project(tmp_path / "blank", "lua", template="empty")
    assert project.mode == "lua" and project.devices == {}
    assert "psu" not in (project.path / "main.lua").read_text(encoding="utf-8")
    assert project.saved_with == __version__
    assert run_project(project.path).status == "ok"


def test_empty_visual_project_has_only_a_start_node(tmp_path):
    project = create_project(tmp_path / "blank", "visual", template="empty")
    graph = json.loads((project.path / project.script).read_text(encoding="utf-8"))
    assert [node["type"] for node in graph["nodes"]] == ["core.start"]
    assert graph["wires"] == [] and project.devices == {}


def test_every_new_project_notes_the_version(tmp_path):
    assert create_project(tmp_path / "a").saved_with == __version__
    assert create_project(tmp_path / "b", template="bode").saved_with == __version__


def test_stamp_version_keeps_the_rest_of_the_file(tmp_path):
    folder = tmp_path / "old"
    folder.mkdir()
    file = folder / LEGACY_PROJECT_FILE
    file.write_text('# my notes\n[project]\nname = "old"\nsaved_with = "0.0.1"  # x\n\n'
                    '[devices.psu]\ndevice = "demo.psu"\n', encoding="utf-8")
    stamp_version(folder)
    text = file.read_text(encoding="utf-8")
    assert "# my notes" in text and f'saved_with = "{__version__}"' in text
    assert text.count("saved_with") == 1
    project = Project.load(folder)
    assert project.saved_with == __version__ and "psu" in project.devices

    # missing: added at the end of [project], not inside [devices]
    file.write_text('[project]\nname = "old"\n\n[devices.psu]\ndevice = "demo.psu"\n',
                    encoding="utf-8")
    stamp_version(folder)
    assert Project.load(folder).saved_with == __version__
    assert Project.load(folder).devices["psu"].settings == {}


def test_project_without_a_stamp_is_unknown(tmp_path):
    folder = tmp_path / "older"
    folder.mkdir()
    (folder / PROJECT_FILE).write_text('[project]\nname = "older"\n', encoding="utf-8")
    assert Project.load(folder).saved_with == ""


def test_last_saved_is_the_newest_file(tmp_path):
    project = create_project(tmp_path / "p")
    os.utime(project.path / PROJECT_FILE, (1000, 1000))
    os.utime(project.path / "main.lua", (5000, 5000))
    assert last_saved(project.path) == 5000
    assert last_saved(tmp_path / "missing") is None


def test_rename_project_renames_folder_and_name(tmp_path):
    project = create_project(tmp_path / "first")
    (project.path / "notes.txt").write_text("kept", encoding="utf-8")
    new = rename_project(project.path, "second")
    assert new == tmp_path / "second" and not (tmp_path / "first").exists()
    assert Project.load(new).name == "second"
    assert (new / "notes.txt").read_text(encoding="utf-8") == "kept"
    assert "# One entry per instrument" in (new / PROJECT_FILE).read_text(encoding="utf-8")


def test_rename_project_refuses_bad_names_and_taken_folders(tmp_path):
    project = create_project(tmp_path / "first")
    create_project(tmp_path / "taken")
    for bad in ("", "a/b", "x:y", ".."):
        with pytest.raises(ProjectError, match="special characters"):
            rename_project(project.path, bad)
    with pytest.raises(ProjectError, match="already exists"):
        rename_project(project.path, "taken")
    assert Project.load(project.path).name == "first"


def test_rename_project_only_the_case(tmp_path):
    project = create_project(tmp_path / "bench")
    new = rename_project(project.path, "Bench")
    assert new.name == "Bench" and Project.load(new).name == "Bench"


def test_duplicate_project_leaves_the_runs_behind(tmp_path):
    project = create_project(tmp_path / "orig")
    (project.path / "runs" / "2026-11-13_120000").mkdir(parents=True)
    (project.path / "data").mkdir()
    (project.path / "data" / "cal.csv").write_text("1,2", encoding="utf-8")
    copy = duplicate_project(project.path, "orig copy")
    assert copy == tmp_path / "orig copy" and Project.load(copy).name == "orig copy"
    assert not (copy / "runs").exists() and (copy / "data" / "cal.csv").is_file()
    assert (project.path / "runs").is_dir() and Project.load(project.path).name == "orig"
    with pytest.raises(ProjectError, match="already exists"):
        duplicate_project(project.path, "orig copy")
    with pytest.raises(ProjectError, match="special characters"):
        duplicate_project(project.path, "a/b")


def test_move_project(tmp_path):
    project = create_project(tmp_path / "p")
    new = move_project(project.path, tmp_path / "archive" / "2026")
    assert new == tmp_path / "archive" / "2026" / "p" and not project.path.exists()
    assert Project.load(new).name == "p"
    assert move_project(new, new.parent) == new  # already there

    create_project(tmp_path / "other" / "p")
    with pytest.raises(ProjectError, match="already exists"):
        move_project(new, tmp_path / "other")
    with pytest.raises(ProjectError, match="its own folder"):
        move_project(new, new / "sub")
