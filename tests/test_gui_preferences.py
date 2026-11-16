# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The per-user preferences file the desktop application remembers the session in (#628).

Qt-free tests of [`tether.gui.preferences`][] (ADR-0122): where the file lives, that every key
reads on its own and a bad key reads as its default, that the one writer is atomic and keeps
keys it does not know, and that the update-check consent file of ADR-0095 is never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tether.gui import app_settings, preferences
from tether.gui.preferences import (
    PREFERENCES_ENV,
    PREFERENCES_FILENAME,
    RECENT_PROJECTS_LIMIT,
    RESUME_LIMIT,
    add_recent_project,
    clear_layout,
    curator_name,
    effective_curator_name,
    preferences_path,
    project_identity,
    recent_projects,
    record_layout,
    record_resume,
    remove_recent_project,
    resume_molecule,
    set_curator_name,
    stored_layout,
    validate_curator_name,
)


@pytest.fixture
def prefs(tmp_path: Path) -> Path:
    return tmp_path / "cfg" / PREFERENCES_FILENAME


def test_the_file_sits_beside_settings_json_unless_the_variable_names_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(PREFERENCES_ENV, raising=False)
    assert preferences_path() == app_settings.settings_path().with_name(PREFERENCES_FILENAME)
    monkeypatch.setenv(PREFERENCES_ENV, str(tmp_path / "mine.json"))
    assert preferences_path() == tmp_path / "mine.json"
    # A relative value is ignored, as a relative XDG_CONFIG_HOME is.
    monkeypatch.setenv(PREFERENCES_ENV, "relative.json")
    assert preferences_path() == app_settings.settings_path().with_name(PREFERENCES_FILENAME)


def test_a_missing_file_reads_as_defaults_and_reading_creates_nothing(prefs: Path) -> None:
    assert recent_projects(prefs) == []
    assert resume_molecule(prefs.parent / "a.tether", prefs) is None
    assert stored_layout(prefs) is None
    assert curator_name(prefs) is None
    assert not prefs.parent.exists()


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"{not json",
        b"\xff\xfe",
        b"[]",
        b'{"schema": 2, "curator_name": "ana"}',
        b'{"schema": true, "curator_name": "ana"}',
        b'{"curator_name": "ana"}',
        b"[" * 100_000 + b"]" * 100_000,
    ],
    # Short ids: pytest puts the id in PYTEST_CURRENT_TEST, and Windows refuses an
    # environment variable over 32767 characters.
    ids=["empty", "torn", "not-utf8", "list", "schema-2", "schema-true", "no-schema", "nested"],
)
def test_a_foreign_or_damaged_file_reads_as_empty(prefs: Path, content: bytes) -> None:
    prefs.parent.mkdir(parents=True)
    prefs.write_bytes(content)
    assert curator_name(prefs) is None
    assert recent_projects(prefs) == []


def test_each_key_reads_on_its_own(prefs: Path, tmp_path: Path) -> None:
    good = str(tmp_path / "good.tether")
    prefs.parent.mkdir(parents=True)
    prefs.write_text(
        json.dumps(
            {
                "schema": 1,
                "recent_projects": [good, "relative.tether", 7, good],
                "resume": {"k": 3},
                "layout": {"geometry": "not base64!", "state": "AA=="},
                "curator_name": "Ana López",
            }
        ),
        encoding="utf-8",
    )
    assert recent_projects(prefs) == [Path(good)]
    assert resume_molecule("k", prefs) is None
    assert stored_layout(prefs) is None
    assert curator_name(prefs) == "Ana López"


def test_recent_projects_are_newest_first_without_duplicates_and_capped(
    prefs: Path, tmp_path: Path
) -> None:
    paths = [tmp_path / f"p{i}.tether" for i in range(RECENT_PROJECTS_LIMIT + 3)]
    for path in paths:
        add_recent_project(path, prefs)
    add_recent_project(paths[-3], prefs)
    got = recent_projects(prefs)
    assert len(got) == RECENT_PROJECTS_LIMIT
    assert got[0] == paths[-3]
    assert got[1:3] == [paths[-1], paths[-2]]
    assert len(set(got)) == len(got)
    assert remove_recent_project(paths[-1], prefs)[:2] == [paths[-3], paths[-2]]


def test_a_relative_path_is_stored_absolute(
    prefs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert add_recent_project("here.tether", prefs) == [tmp_path / "here.tether"]


def test_the_resume_position_is_kept_per_project_and_forgotten_on_none(
    prefs: Path, tmp_path: Path
) -> None:
    a, b = tmp_path / "a.tether", tmp_path / "b.tether"
    record_resume(a, "mol-a", prefs)
    record_resume(b, "mol-b", prefs)
    assert resume_molecule(a, prefs) == "mol-a"
    assert resume_molecule(b, prefs) == "mol-b"
    record_resume(a, None, prefs)
    assert resume_molecule(a, prefs) is None
    assert resume_molecule(b, prefs) == "mol-b"


def test_only_the_last_resume_positions_are_kept(prefs: Path, tmp_path: Path) -> None:
    for i in range(RESUME_LIMIT + 5):
        record_resume(tmp_path / f"p{i}.tether", f"m{i}", prefs)
    assert resume_molecule(tmp_path / "p0.tether", prefs) is None
    assert resume_molecule(tmp_path / f"p{RESUME_LIMIT + 4}.tether", prefs) == (
        f"m{RESUME_LIMIT + 4}"
    )
    data = json.loads(prefs.read_text(encoding="utf-8"))
    assert len(data["resume"]) == RESUME_LIMIT


def test_reopening_a_project_keeps_its_resume_position_past_the_limit(
    prefs: Path, tmp_path: Path
) -> None:
    # The oldest position is dropped first, and closing a project again makes it the newest,
    # also once the file has been written and read back (no reordering on write).
    first = tmp_path / "first.tether"
    record_resume(first, "kept", prefs)
    for i in range(RESUME_LIMIT - 1):
        record_resume(tmp_path / f"p{i}.tether", f"m{i}", prefs)
    record_resume(first, "kept", prefs)
    record_resume(tmp_path / "newest.tether", "n", prefs)
    assert resume_molecule(first, prefs) == "kept"
    assert resume_molecule(tmp_path / "p0.tether", prefs) is None
    assert resume_molecule(tmp_path / "p1.tether", prefs) == "m1"


def test_two_spellings_of_one_project_share_one_identity(tmp_path: Path) -> None:
    target = tmp_path / "real.tether"
    target.write_bytes(b"")
    assert project_identity(tmp_path / "sub" / ".." / "real.tether") == project_identity(target)
    link = tmp_path / "link.tether"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    assert project_identity(link) == project_identity(target)


def test_layout_round_trips_and_clears(prefs: Path) -> None:
    record_layout(b"\x00geo\xff", b"\x01state", prefs, section_hidden=["b", "a", "a"])
    layout = stored_layout(prefs)
    assert layout is not None
    assert layout.geometry == b"\x00geo\xff"
    assert layout.state == b"\x01state"
    assert layout.section_hidden == ("a", "b")
    clear_layout(prefs)
    assert stored_layout(prefs) is None


def test_the_curator_name_defaults_to_the_login_and_can_be_set_and_unset(prefs: Path) -> None:
    from tether.project.labels import default_labeler

    assert effective_curator_name(prefs) == default_labeler()
    set_curator_name("  Ana López  ", prefs)
    assert curator_name(prefs) == "Ana López"
    assert effective_curator_name(prefs) == "Ana López"
    set_curator_name(None, prefs)
    assert curator_name(prefs) is None
    assert effective_curator_name(prefs) == default_labeler()


@pytest.mark.parametrize("bad", ["", "   ", "a\x00b", "line\nbreak", "x" * 101, 3])
def test_a_bad_curator_name_is_refused_and_nothing_is_written(prefs: Path, bad: object) -> None:
    with pytest.raises(ValueError):
        validate_curator_name(bad)
    with pytest.raises(ValueError):
        set_curator_name(bad, prefs)  # type: ignore[arg-type]
    assert not prefs.exists()


def test_a_write_keeps_keys_this_version_does_not_know(prefs: Path) -> None:
    prefs.parent.mkdir(parents=True)
    prefs.write_text(json.dumps({"schema": 1, "from_a_newer_version": [1, 2]}), encoding="utf-8")
    set_curator_name("ana", prefs)
    data = json.loads(prefs.read_text(encoding="utf-8"))
    assert data == {"schema": 1, "from_a_newer_version": [1, 2], "curator_name": "ana"}


def test_a_failed_write_leaves_the_old_file_and_no_temporary(
    prefs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_curator_name("before", prefs)
    before = prefs.read_bytes()

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(preferences.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        set_curator_name("after", prefs)
    assert prefs.read_bytes() == before
    assert sorted(p.name for p in prefs.parent.iterdir()) == [PREFERENCES_FILENAME]


def test_a_write_the_reader_would_discard_is_refused_and_the_old_file_kept(
    prefs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_curator_name("before", prefs)
    before = prefs.read_bytes()
    # Room for what is there now, not for one more long path.
    monkeypatch.setattr(preferences, "_MAX_BYTES", len(before) + 20)
    with pytest.raises(OSError, match="exceed"):
        add_recent_project(tmp_path / ("x" * 200 + ".tether"), prefs)
    assert prefs.read_bytes() == before
    assert curator_name(prefs) == "before"
    assert sorted(p.name for p in prefs.parent.iterdir()) == [PREFERENCES_FILENAME]


def test_the_consent_file_is_never_written(prefs: Path, tmp_path: Path) -> None:
    settings = prefs.with_name(app_settings.SETTINGS_FILENAME)
    add_recent_project(tmp_path / "a.tether", prefs)
    record_resume(tmp_path / "a.tether", "m", prefs)
    record_layout(b"g", b"s", prefs)
    set_curator_name("ana", prefs)
    assert not settings.exists()


def test_importing_the_module_loads_no_qt_numpy_or_hdf5() -> None:
    code = (
        "import sys\n"
        "import tether.gui.preferences\n"
        "heavy = {'PySide6', 'pyqtgraph', 'numpy', 'h5py'} & set(sys.modules)\n"
        "print(sorted(heavy))\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    )
    assert out.stdout.strip() == "[]"


def test_the_login_name_shown_is_the_name_the_project_writes() -> None:
    from tether.project.labels import default_labeler

    assert preferences.login_name() == default_labeler()
