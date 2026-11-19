# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether status`` and :func:`tether.project.readiness.read_project_readiness` (#896).

The acceptance criteria of #896, one test or more each:

* one Qt-free function returns the readiness record, and ``tether status`` calls it (the
  application's call is in ``tests/test_shell_readiness.py``);
* the command opens the project read-only and takes no write lock, as ``tether info`` does;
* the text names each stage, its state, each stale item with its reason, and the next step;
* ``--json`` has a schema version, and its keys are the ones ``docs/cli.md`` documents;
* ``--require STAGE`` exits 3 when the stage is not ready;
* a stage that a later issue adds reads "not available in this version";
* a new project, a project with a stale fit and a project with an interrupted job.

Plus the item moved from #925: the correction state of G03 is shown by its name. The stores
come from the shared test-only builder ``tests/_analysis_store.py``; nothing here is a
scientific oracle.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")

import h5py  # noqa: E402

from _analysis_store import MEANS, build_store_with_model, reg_map  # noqa: E402
from tether import cli  # noqa: E402
from tether.cli import main  # noqa: E402
from tether.imaging.calibrate import write_calibration  # noqa: E402
from tether.project import Project, jobs, lock  # noqa: E402
from tether.project import correction_states as cs  # noqa: E402
from tether.project import readiness as rd  # noqa: E402
from tether.services import ExtractRequest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "cli.md"
SOURCE = ROOT / "src" / "tether" / "project" / "readiness.py"
_FIXTURES = Path(__file__).parent / "fixtures"
_QT = ("PySide6", "PySide2", "PyQt6", "PyQt5", "shiboken6", "qtpy", "pyqtgraph", "napari")

_STATES = np.array([[0, 0, 1, 1, 2], [1, 1, 2, 2, 0], [0, 1, 1, 0, 0], [2, 2, 2, 1, 1]])


def _store(tmp_path: Path, *, stale: bool = False, mapped: bool = True) -> Path:
    """Four molecules, one movie with a map, one fit; the last molecule stale when asked."""
    project, _keys = build_store_with_model(
        tmp_path, _STATES, MEANS, stale=[False, False, False, stale]
    )
    if mapped:  # as tether extract stores it: /calibration/<id>, named by the movie row
        write_calibration(project.path, reg_map(), calibration_id="cal-1")
        with h5py.File(project.path, "r+") as f:
            movies = f["movies"]["table"]
            row = movies[0]
            row["calibration_id"] = "cal-1"
            movies[0] = row
    return project.path


def _states(r: rd.ProjectReadiness) -> dict[str, str]:
    return {s.key: s.state.value for s in r.stages}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- one Qt-free function ------------------------------------------------------------------


def test_the_readiness_module_imports_no_qt() -> None:
    names: set[str] = set()
    for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    assert not {n for n in names if n.split(".")[0] in _QT}


def test_the_command_prints_what_the_function_returns(tmp_path: Path, monkeypatch, capsys) -> None:
    path = _store(tmp_path)
    calls: list[str] = []
    real = rd.read_project_readiness

    def spy(p):
        calls.append(str(p))
        return real(p)

    monkeypatch.setattr(rd, "read_project_readiness", spy)
    assert main(["status", "--json", str(path)]) == 0
    assert calls == [str(path)]
    document = json.loads(capsys.readouterr().out)
    expected = real(path).to_dict()
    assert {k: document[k] for k in expected} == expected


def test_the_command_reads_through_the_status_service(tmp_path: Path, monkeypatch) -> None:
    from tether.services import status

    seen: list[str] = []
    real = status.project_status
    monkeypatch.setattr(status, "project_status", lambda p: seen.append(str(p)) or real(p))
    path = _store(tmp_path)
    assert main(["status", str(path)]) == 0
    assert seen == [str(path)]


def test_the_require_choices_are_the_stages() -> None:
    assert tuple(key for key, *_ in rd.STAGES) == cli._STATUS_STAGES
    assert [s for s, _ in rd.SECTIONS] == ["prepare", "curate", "analyze", "deposit"]


# --- the three project states of the issue -----------------------------------------------


def test_a_new_project(tmp_path: Path, capsys) -> None:
    path = tmp_path / "new.tether"
    Project.create(path)
    r = rd.read_project_readiness(path)
    assert _states(r) == {
        "sources": "not_started",
        "map": "not_started",
        "traces": "not_started",
        "curation": "not_started",
        "corrections": "not_started",
        "analysis_sets": "not_available",
        "fits": "not_started",
        "results": "not_available",
        "deposit": "not_available",
    }
    assert r.stale == r.jobs == ()
    assert (r.next_step.stage, r.next_step.section) == ("sources", "prepare")

    assert main(["status", str(path)]) == 0
    out = capsys.readouterr().out
    assert "  Prepare\n    sources and geometry: not started (no movie)\n" in out
    assert "    corrections: not started (correction state APPARENT_REVIEW)\n" in out
    assert "  stale: none\n  jobs: none\n" in out
    assert out.rstrip().endswith("next step: " + r.next_step.action)


def test_a_project_with_a_stale_fit(tmp_path: Path, capsys) -> None:
    path = _store(tmp_path, stale=True)
    r = rd.read_project_readiness(path)
    states = _states(r)
    assert states["sources"] == states["map"] == states["traces"] == "ready"
    assert states["fits"] == "stale"
    assert r.stale == (
        rd.StaleItem(
            "fits",
            "fit vbconhmm",
            "1 of 4 molecules changed since the fit (their traces, window or correction factors)",
        ),
    )
    # A stale result comes before a stage that is only not started yet.
    assert r.next_step.stage == "fits"
    assert "--stale-only" in r.next_step.action

    assert main(["status", str(path)]) == 0
    out = capsys.readouterr().out
    assert "    fits: stale (1 model)\n" in out
    assert "  stale: 1\n    fit vbconhmm: 1 of 4 molecules changed since the fit" in out

    (tmp_path / "fresh").mkdir()
    fresh = rd.read_project_readiness(_store(tmp_path / "fresh"))
    assert fresh.stage("fits").state is rd.StageState.READY
    assert fresh.stale == ()


def test_a_project_with_an_interrupted_job(tmp_path: Path, capsys) -> None:
    path = _store(tmp_path)
    project = Project(path)
    project.acquire_lock()
    job = jobs.submit_job(project, ExtractRequest(movie="m.tif", output="m.tether"), ("a", "b"))

    def work(part: str, _cancel: jobs.CancelToken) -> None:
        if part == "b":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        jobs.run_job(project, job.job_id, work)
    project.release_lock()

    r = rd.read_project_readiness(path)
    assert r.jobs == (rd.JobStatus(job.job_id, "extract", "traces", "interrupted", 2, 1, ""),)
    assert r.next_step == rd.NextStep(
        "traces",
        "prepare",
        f"retry the interrupted extract job {job.job_id[:8]} (1 of 2 parts are complete)",
    )
    assert main(["status", str(path)]) == 0
    out = capsys.readouterr().out
    assert f"  jobs: 1\n    {job.job_id[:8]} extract: interrupted, 1 of 2 parts complete\n" in out
    assert out.rstrip().endswith(r.next_step.action)


def test_a_later_finished_job_of_the_command_clears_the_retry(tmp_path: Path) -> None:
    jobs_ = (
        rd.JobStatus("a" * 32, "extract", "traces", "failed", 2, 1, "boom"),
        rd.JobStatus("b" * 32, "extract", "traces", "done", 2, 2, ""),
    )
    assert rd._jobs_to_retry(jobs_) == []
    assert rd._jobs_to_retry(jobs_[:1]) == [jobs_[0]]


# --- stale marks, the correction state, an analysis-only import ----------------------------


def test_a_movie_without_a_map_is_named(tmp_path: Path) -> None:
    r = rd.read_project_readiness(_store(tmp_path, mapped=False))
    assert r.stage("map").state is rd.StageState.IN_PROGRESS
    assert r.stage("map").detail == "1 of 1 movies have no map"


def test_a_stale_mark_names_the_record_and_the_reason(tmp_path: Path, capsys) -> None:
    path = _store(tmp_path)
    project = Project(path)
    with project.write_lock():
        jobs.record_inputs(project, "trace_generation", "g1", {("map", "m1"): "v1"})
        jobs.set_stale_marks(project, "map", "m1", "v2")
    r = rd.read_project_readiness(path)
    assert r.stage("traces").state is rd.StageState.STALE
    assert r.stale[0] == rd.StaleItem("traces", "Trace generation g1", "Map edit (Map m1 changed)")
    assert r.next_step.stage == "traces"
    assert main(["status", str(path)]) == 0
    assert "    Trace generation g1: Map edit (Map m1 changed)\n" in capsys.readouterr().out


def test_the_correction_state_is_shown_by_its_name(tmp_path: Path, capsys) -> None:
    path = _store(tmp_path)
    project = Project(path)
    with project.write_lock():
        cs.send_event(project, cs.CorrectionEvent.FINISH_INITIAL_CURATION, cs.Actor.person("t"))
    r = rd.read_project_readiness(path)
    assert r.correction_state == "BLEEDTHROUGH_REQUIRED"
    assert r.stage("corrections").state is rd.StageState.IN_PROGRESS
    # The initial curation was finished, though some molecules are undecided.
    assert r.stage("curation").state is rd.StageState.READY
    assert r.next_step.stage == "corrections"
    assert main(["status", str(path)]) == 0
    out = capsys.readouterr().out
    assert "    corrections: in progress (correction state BLEEDTHROUGH_REQUIRED)\n" in out


def test_every_correction_state_has_a_next_action() -> None:
    assert set(rd._CORRECTION_ACTIONS) == {s.value for s in cs.CorrectionState}
    assert [s for s, a in rd._CORRECTION_ACTIONS.items() if not a] == ["ANALYSIS_READY"]


def test_an_analysis_only_import_starts_at_curation(tmp_path: Path) -> None:
    from tether.idealize import read_smd
    from tether.project.analysis_import import import_analysis_only_project

    out = tmp_path / "ao.tether"
    smd = read_smd(_FIXTURES / "smd_4mol.hdf5")
    import_analysis_only_project(out, source=smd, source_name="smd_4mol.hdf5")
    r = rd.read_project_readiness(out)
    assert r.stage("sources").state is r.stage("map").state is rd.StageState.NOT_NEEDED
    assert r.stage("traces").state is rd.StageState.READY
    assert r.next_step.section == "curate"


# --- stages a later issue adds ------------------------------------------------------------


def test_a_stage_that_has_not_landed_reads_not_available_in_this_version(
    tmp_path: Path, capsys
) -> None:
    path = _store(tmp_path)
    r = rd.read_project_readiness(path)
    later = {s.key: s.issue for s in r.stages if s.state is rd.StageState.NOT_AVAILABLE}
    assert later == {"analysis_sets": 934, "results": 943, "deposit": 956}
    assert rd.StageState.NOT_AVAILABLE.text == "not available in this version"
    assert main(["status", str(path)]) == 0
    out = capsys.readouterr().out
    assert "    analysis sets: not available in this version (#934)\n" in out
    assert "    results: not available in this version (#943)\n" in out
    assert "    deposit: not available in this version (#956)\n" in out
    # The next step never points at a stage that is not available.
    assert r.next_step.stage not in later


# --- --require ----------------------------------------------------------------------------


def test_require_exits_3_when_a_stage_is_not_ready(tmp_path: Path, capsys) -> None:
    path = _store(tmp_path, stale=True)
    assert main(["status", "--require", "traces", str(path)]) == 0
    assert main(["status", "--require", "traces", "--require", "fits", str(path)]) == 3
    assert "tether status: fits is stale" in capsys.readouterr().err
    assert main(["status", "--json", "--require", "deposit", str(path)]) == 3
    captured = capsys.readouterr()
    assert json.loads(captured.out)["format"] == "tether-status"
    assert "tether status: deposit is not available in this version" in captured.err


def test_require_refuses_a_name_that_is_not_a_stage(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["status", "--require", "nope", str(tmp_path / "x.tether")])
    assert excinfo.value.code == 2


# --- read-only, no lock -------------------------------------------------------------------


def test_status_changes_nothing_and_takes_no_lock(tmp_path: Path, monkeypatch) -> None:
    path = _store(tmp_path, stale=True)
    foreign = Project(path, identity=lock.LockIdentity("other-host", "someone", 4242))
    foreign.acquire_lock()
    lock_file = lock.lock_path(path)
    listing = sorted(p.name for p in tmp_path.iterdir())
    before = (_sha256(path), path.stat().st_mtime_ns, _sha256(lock_file))

    def refuse(*args, **kwargs):
        raise AssertionError("tether status reached a lock writer")

    for name in ("acquire", "assert_writable", "refresh", "steal_lock", "release"):
        monkeypatch.setattr(lock, name, refuse)
    assert main(["status", str(path)]) == 0
    assert main(["status", "--json", str(path)]) == 0

    assert (_sha256(path), path.stat().st_mtime_ns, _sha256(lock_file)) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == listing


def test_a_file_that_is_not_a_project_exits_1_with_one_line(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.tether"
    bad.write_bytes(b"not hdf5")
    assert main(["status", str(bad)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1
    assert captured.err.startswith("tether status: ")
    assert main(["status", str(tmp_path / "gone.tether")]) == 1


# --- the JSON layout is the documented one -----------------------------------------------


def _key_paths(value: object, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            paths |= _key_paths(child, path)
    elif isinstance(value, list):
        for child in value:
            paths |= _key_paths(child, f"{prefix}[]")
    return paths


def _documented_json_keys() -> set[str]:
    text = PAGE.read_text(encoding="utf-8")
    start = text.index("### Status JSON layout")
    section = text[start : text.index("\n## ", start)]
    return set(re.findall(r"^\| `([a-z_\[\].]+)` \|", section, re.MULTILINE))


def test_json_output_has_a_version_and_the_documented_keys(tmp_path: Path, capsys) -> None:
    path = _store(tmp_path, stale=True)
    project = Project(path)
    with project.write_lock():
        jobs.submit_job(project, ExtractRequest(movie="m.tif", output="m.tether"), ("a",))
    assert main(["status", "--json", str(path)]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["format"] == "tether-status"
    assert document["format_version"] == rd.READINESS_FORMAT_VERSION == 1
    actual = _key_paths(document)
    documented = _documented_json_keys()
    assert actual - documented == set(), "keys printed but not documented in docs/cli.md"
    assert documented - actual == set(), "keys documented in docs/cli.md but never printed"
    states = {s["state"] for s in document["stages"]}
    assert states <= {s.value for s in rd.StageState}
