# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether info`` and :func:`tether.project.summary.read_project_summary` (#655).

The acceptance criteria of #655, one test each where they can be tested apart:

* the counts printed equal the counts in the store (``/molecules`` read independently);
* the project file and its lock file are byte-for-byte unchanged after the command;
* the command works while another process holds the project's lock;
* ``--json`` output parses and has the keys ``docs/cli.md`` documents;
* ``tether --help`` lists the command (the page section is pinned by
  ``tests/test_cli_reference.py``);
* the summary function needs no write lock, so ``tether status`` (#896) can share it.

Plus the issue's other promises: no trace arrays are read unless ``--stale`` asks, a
non-project or a newer schema gives one message and exit 1, and a corrupt lock is
reported rather than raised. The stores are built with the shared test-only builder
``tests/_analysis_store.py``; nothing here is a scientific oracle.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import textwrap
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model, to_str  # noqa: E402
from tether.cli import build_parser, main  # noqa: E402
from tether.imaging.extract import read_molecules  # noqa: E402
from tether.io.schema import SCHEMA_VERSION  # noqa: E402
from tether.project import Project, lock  # noqa: E402
from tether.project.labels import CurationLabel  # noqa: E402
from tether.project.summary import (  # noqa: E402
    SUMMARY_FORMAT_VERSION,
    ProjectSummaryError,
    read_project_summary,
)

PAGE = Path(__file__).resolve().parents[1] / "docs" / "cli.md"

#: Six molecules, five frames; three states (MEANS). Row 5 is forced stale.
_STATES = np.array(
    [
        [0, 0, 1, 1, 2],
        [1, 1, 2, 2, 0],
        [0, 1, 1, 0, 0],
        [2, 2, 2, 1, 1],
        [0, 0, 0, 0, 1],
        [1, 0, 1, 0, 1],
    ]
)
_LABELS = [
    CurationLabel.ACCEPT,
    CurationLabel.ACCEPT,
    CurationLabel.REJECT,
    CurationLabel.UNCURATED,
    CurationLabel.ACCEPT,
    CurationLabel.UNCURATED,
]
_CATEGORIES = ["static", "dynamic", "", "dynamic", "", "static"]
_METHODS = ["corrected", "corrected", "apparent-E (corrections unavailable)", "", "manual", ""]


def _rich_store(tmp_path: Path) -> Path:
    """A store with two movies, mixed curation, categories, corrections and one stale row."""
    project, _keys = build_store_with_model(
        tmp_path,
        _STATES,
        MEANS,
        stale=[False, False, False, False, False, True],
    )
    path = project.path
    project.sync_conditions()
    with h5py.File(path, "r+") as f:
        movies = f["movies"]["table"]
        first = movies[0]
        first["uri"] = "C:\\data\\day1\\movie_010.tif"
        first["frame_time"] = 0.1
        second = first.copy()
        second["movie_id"] = "mov-2"
        second["uri"] = "/data/day2/movie_011.tif"
        second["n_frames"] = 7
        second["frame_time"] = 0.0  # not recorded
        movies.resize((2,))
        movies[:] = np.array([first, second], dtype=movies.dtype)

        table = f["molecules"]["table"][:]
        for i in range(table.shape[0]):
            table["curation_label"][i] = int(_LABELS[i])
            table["category"][i] = _CATEGORIES[i]
            table["correction_method"][i] = _METHODS[i]
            if i >= 4:
                table["movie_id"][i] = "mov-2"
        f["molecules"]["table"][:] = table
        f["settings"].create_group("correction")
        f["models"].create_group("ranker-ref")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- the counts equal the store ----------------------------------------------------------


def test_the_summary_counts_equal_the_store(tmp_path: Path) -> None:
    path = _rich_store(tmp_path)
    molecules = read_molecules(path)  # the independent read
    labels = [int(v) for v in molecules["curation_label"]]
    movie_ids = [to_str(v) for v in molecules["movie_id"]]
    categories = Counter(to_str(v) for v in molecules["category"])
    methods = Counter(to_str(v) for v in molecules["correction_method"])
    conditions = Counter(to_str(v) for v in molecules["condition_id"])

    summary = read_project_summary(path)

    assert summary.schema_version == SCHEMA_VERSION
    assert summary.n_molecules == molecules.shape[0] == 6
    assert summary.curation.accepted == labels.count(int(CurationLabel.ACCEPT)) == 3
    assert summary.curation.rejected == labels.count(int(CurationLabel.REJECT)) == 1
    assert summary.curation.undecided == labels.count(int(CurationLabel.UNCURATED)) == 2

    by_movie = {m.movie_id: m for m in summary.movies}
    assert sorted(by_movie) == ["mov-1", "mov-2"]
    for movie_id, movie in by_movie.items():
        own = [lab for lab, mid in zip(labels, movie_ids, strict=True) if mid == movie_id]
        assert movie.n_molecules == len(own)
        assert movie.curation.accepted == own.count(int(CurationLabel.ACCEPT))
        assert movie.curation.rejected == own.count(int(CurationLabel.REJECT))
        assert movie.curation.undecided == own.count(int(CurationLabel.UNCURATED))
    assert by_movie["mov-1"].name == "movie_010.tif"  # a Windows uri, read on any OS
    assert by_movie["mov-1"].frame_time_s == pytest.approx(0.1)
    assert by_movie["mov-2"].name == "movie_011.tif"
    assert by_movie["mov-2"].n_frames == 7
    assert by_movie["mov-2"].frame_time_s is None

    assert summary.categories == {k: v for k, v in categories.items() if k}
    assert summary.uncategorized == categories[""] == 2
    assert summary.corrections.ran is True
    assert summary.corrections.methods == dict(methods)

    assert [(c.condition_id, c.n_molecules) for c in summary.conditions] == list(conditions.items())
    assert all(c.materialized for c in summary.conditions)
    assert summary.conditions[0].key["construct_variant"]  # the synced key fields

    (model,) = summary.idealizations
    stored = Project.open(path).read_idealization("vbconhmm")
    assert (model.name, model.method, model.n_states, model.n_molecules) == (
        "vbconhmm",
        stored.model_type,
        stored.nstates,
        stored.n_molecules,
    )
    assert model.stale is None  # not checked without check_stale
    assert summary.models == ["ranker-ref"]
    assert summary.lock.held is False


def test_check_stale_counts_what_the_staleness_recompute_reports(tmp_path: Path) -> None:
    path = _rich_store(tmp_path)
    # Row 5's hash was forced stale; the corrections stamped above re-stale others too.
    expected = len(Project.open(path).stale_idealization_keys("vbconhmm"))
    assert expected >= 1
    (model,) = read_project_summary(path, check_stale=True).idealizations
    assert model.stale == expected


def test_the_stale_count_counts_rows_not_shared_keys(tmp_path: Path) -> None:
    """Two stale molecules that share a quantized ``molecule_key`` (§7.10) count as two."""
    project, keys = build_store_with_model(tmp_path, _STATES[:3], MEANS, stale=[True, True, False])
    shared = keys[0]
    with h5py.File(project.path, "r+") as f:
        table = f["molecules"]["table"][:]
        table["molecule_key"][1] = shared
        f["molecules"]["table"][:] = table
        f["idealization"]["vbconhmm"]["molecule_key"][1] = shared
    assert project.stale_idealization_keys("vbconhmm") == [shared]  # one key ...
    (model,) = read_project_summary(project.path, check_stale=True).idealizations
    assert model.stale == 2  # ... two stale molecules


def test_the_text_output_prints_the_counts(tmp_path: Path, capsys) -> None:
    path = _rich_store(tmp_path)
    assert main(["info", str(path)]) == 0
    out = capsys.readouterr().out
    assert "  molecules: 6\n" in out
    assert "  curation: 3 accepted, 1 rejected, 2 undecided\n" in out
    assert "movie_010.tif: 5 frames, 0.1 s/frame, 4 molecules" in out
    assert "movie_011.tif: 7 frames, frame time not recorded, 2 molecules" in out
    assert "categories: dynamic 2, static 2; 2 uncategorized" in out
    assert "corrections: ran (none 2, apparent-E (corrections unavailable) 1, " in out
    assert "vbconhmm: method vbconhmm, 3 states, 6 molecules, stale not checked" in out
    assert "ranker model: ranker-ref" in out
    assert "lock: not held" in out


def test_a_new_project_reads_as_empty(tmp_path: Path, capsys) -> None:
    path = tmp_path / "new.tether"
    Project.create(path)
    summary = read_project_summary(path)
    assert summary.n_molecules == 0
    assert summary.movies == summary.conditions == summary.idealizations == []
    assert summary.corrections.ran is False
    assert main(["info", str(path)]) == 0
    assert "corrections: not run" in capsys.readouterr().out


# --- read-only: nothing changes, no lock, no trace or movie reads ------------------------


def test_the_project_and_lock_files_are_byte_for_byte_unchanged(tmp_path: Path) -> None:
    path = _rich_store(tmp_path)
    foreign = Project(path, identity=lock.LockIdentity("other-host", "someone", 4242))
    foreign.acquire_lock()
    lock_file = lock.lock_path(path)
    listing = sorted(p.name for p in tmp_path.iterdir())
    before = (_sha256(path), path.stat().st_mtime_ns, _sha256(lock_file))

    assert main(["info", "--json", str(path)]) == 0
    assert main(["info", "--stale", str(path)]) == 0

    after = (_sha256(path), path.stat().st_mtime_ns, _sha256(lock_file))
    assert after == before
    # No new sidecar, guard or reservation file appeared beside the project either.
    assert sorted(p.name for p in tmp_path.iterdir()) == listing


_HOLDER = textwrap.dedent(
    """
    import sys
    from tether.project import Project

    Project(sys.argv[1]).acquire_lock()
    print("held", flush=True)
    sys.stdin.readline()
    """
)


def test_info_works_while_another_process_holds_the_lock(tmp_path: Path, capsys) -> None:
    path = _rich_store(tmp_path)
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        assert main(["info", "--json", str(path)]) == 0
        document = json.loads(capsys.readouterr().out)
        lock_state = document["projects"][0]["lock"]
        assert lock_state["held"] is True
        assert lock_state["pid"] == holder.pid
        assert lock_state["stale"] is False
        assert document["projects"][0]["n_molecules"] == 6
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.close()
        holder.wait(timeout=30)


def test_the_summary_needs_no_write_lock(tmp_path: Path, monkeypatch) -> None:
    """The shared reader for #896 never reaches a writer or the lock acquirer."""
    path = _rich_store(tmp_path)

    def refuse(*args, **kwargs):
        raise AssertionError("read_project_summary reached a lock writer")

    for name in ("acquire", "assert_writable", "refresh", "steal_lock", "release"):
        monkeypatch.setattr(lock, name, refuse)
    assert read_project_summary(path).n_molecules == 6


def test_no_trace_array_is_read_unless_stale_is_asked(tmp_path: Path, monkeypatch) -> None:
    path = _rich_store(tmp_path)
    read: list[str] = []
    real = h5py.Dataset.__getitem__

    def recording(self, key, **kwargs):
        read.append(self.name)
        return real(self, key, **kwargs)

    monkeypatch.setattr(h5py.Dataset, "__getitem__", recording)
    read_project_summary(path)
    bulky = [n for n in read if n.startswith(("/traces", "/patches", "/idealization"))]
    assert not bulky, f"read_project_summary read bulk arrays: {sorted(set(bulky))}"
    assert "/molecules/table" in read

    read.clear()
    read_project_summary(path, check_stale=True)
    assert any(n.startswith("/traces") for n in read), "--stale must read the trace layers"


def test_no_movie_file_is_opened(tmp_path: Path, monkeypatch) -> None:
    import tether.io.movie as movie

    def refuse(*args, **kwargs):
        raise AssertionError("tether info opened a movie")

    monkeypatch.setattr(movie, "open_movie", refuse)
    assert read_project_summary(_rich_store(tmp_path)).movies


def test_a_corrupt_lock_is_reported_not_raised(tmp_path: Path, capsys) -> None:
    path = _rich_store(tmp_path)
    lock.lock_path(path).write_text("{not json", encoding="utf-8")
    assert read_project_summary(path).lock.corrupt is True
    assert main(["info", str(path)]) == 0
    assert "lock: present but unreadable" in capsys.readouterr().out


# --- errors and exit codes ---------------------------------------------------------------


def test_a_file_that_is_not_a_project_exits_1_with_one_message(tmp_path: Path, capsys) -> None:
    stray = tmp_path / "notes.tether"
    stray.write_text("not hdf5", encoding="utf-8")
    assert main(["info", str(stray)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    (line,) = captured.err.strip().splitlines()
    assert line.startswith("tether info: ") and "not a readable .tether" in line


def test_a_newer_schema_exits_1_naming_the_version(tmp_path: Path, capsys) -> None:
    path = tmp_path / "future.tether"
    Project.create(path)
    with h5py.File(path, "r+") as f:
        f.attrs["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(ProjectSummaryError, match="newer than this app"):
        read_project_summary(path)
    assert main(["info", str(path)]) == 1
    assert f"schema_version {SCHEMA_VERSION + 1} is newer" in capsys.readouterr().err


def test_one_bad_project_still_prints_the_good_one(tmp_path: Path, capsys) -> None:
    good = _rich_store(tmp_path)
    missing = tmp_path / "missing.tether"
    assert main(["info", "--json", str(good), str(missing)]) == 1
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert [p["path"] for p in document["projects"]] == [str(good), str(missing)]
    assert document["projects"][0]["n_molecules"] == 6
    assert "no such file" in document["projects"][1]["error"]
    assert "no such file" in captured.err


def test_no_project_argument_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["info"])
    assert excinfo.value.code == 2


def test_help_lists_info() -> None:
    assert re.search(r"^\s+info\s", build_parser().format_help(), re.MULTILINE)


# --- the JSON layout is the documented one -----------------------------------------------

#: Values that are maps keyed by data (category names, method names, key fields), so the
#: page documents the map and not each of its keys.
_MAP_KEYS = frozenset({"categories", "corrections.methods", "conditions[].key"})


def _key_paths(value: object, prefix: str = "") -> set[str]:
    """Every key path in a JSON value; list items are spelled ``[]``."""
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            if path.split("projects[].", 1)[-1] not in _MAP_KEYS:
                paths |= _key_paths(child, path)
    elif isinstance(value, list):
        for child in value:
            paths |= _key_paths(child, f"{prefix}[]")
    return paths


def _documented_json_keys() -> set[str]:
    text = PAGE.read_text(encoding="utf-8")
    start = text.index("### JSON layout")
    section = text[start : text.index("\n## ", start)]
    return set(re.findall(r"^\| `([a-z_\[\].]+)` \|", section, re.MULTILINE))


def test_json_output_parses_and_has_the_documented_keys(tmp_path: Path, capsys) -> None:
    good = _rich_store(tmp_path)
    holder = Project(good, identity=lock.LockIdentity("other-host", "someone", 4242))
    holder.acquire_lock()
    assert main(["info", "--json", "--stale", str(good), str(tmp_path / "gone.tether")]) == 1
    document = json.loads(capsys.readouterr().out)
    assert document["format"] == "tether-info"
    assert document["format_version"] == SUMMARY_FORMAT_VERSION

    actual = _key_paths(document)
    documented = _documented_json_keys()
    assert actual - documented == set(), "keys printed but not documented in docs/cli.md"
    assert documented - actual == set(), "keys documented in docs/cli.md but never printed"
