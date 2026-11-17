# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The job and dependency coordinator (#895, ADR-0126) and its stale marks (ADR-0103)."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import h5py
import pytest

from tether.io import schema
from tether.project import dependency_graph as graph
from tether.project import jobs
from tether.project.core import Project
from tether.project.lock import LockedError, LockIdentity
from tether.services import ExtractRequest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
JOBS_SOURCE = SRC / "tether" / "project" / "jobs.py"
FORMAT_PAGE = ROOT / "docs" / "reference" / "tether-format.md"
_QT = ("PySide6", "PySide2", "PyQt6", "PyQt5", "shiboken6", "qtpy", "pyqtgraph", "napari")
REQUEST = ExtractRequest(movie="m.tif", output="m.tether")
PARTS = ("a", "b", "c", "d", "e")


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.create(tmp_path / "p.tether")
    proj.acquire_lock()
    yield proj
    proj.release_lock()


def _write_part(path: Path, part: str) -> None:
    with h5py.File(path, "r+") as f:
        f["settings"].create_group(f"test_part_{part}")


def _parts_in_store(path: Path) -> set[str]:
    with h5py.File(path, "r") as f:
        return {k.removeprefix("test_part_") for k in f["settings"] if k.startswith("test_part_")}


# --- Qt-free --------------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            names.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            names.add(n.module)
    return names


def test_the_coordinator_source_imports_no_qt_and_no_gui_module() -> None:
    bad = {m for m in _imports(JOBS_SOURCE) if m.split(".")[0] in _QT or m.startswith("tether.gui")}
    assert bad == set()


def test_importing_the_coordinator_loads_no_qt_module() -> None:
    code = (
        "import sys, tether.project.jobs\n"
        f"qt = {_QT!r}\n"
        "print([m for m in sys.modules if m.split('.')[0] in qt or m.startswith('tether.gui')])"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert out.stdout.strip() == "[]"


# --- the job record -------------------------------------------------------------------------


def test_a_job_record_is_additive_data_with_every_field(project: Project) -> None:
    with h5py.File(project.path, "r") as f:
        assert "jobs" not in f["settings"]
    job = jobs.submit_job(project, REQUEST, PARTS, inputs={("trace_generation", "1"): "g1"})
    assert job.state is jobs.JobState.QUEUED
    assert (job.command, job.parts, job.completed) == ("extract", PARTS, ())
    assert json.loads(job.request)["fields"]["movie"] == "m.tif"
    assert job.inputs == {"trace_generation:1": "g1"}
    assert job.created_utc and not job.started_utc and not job.ended_utc and not job.error
    with h5py.File(project.path, "r") as f:
        names = set(f["settings"]["jobs"].dtype.names)
        assert {"job_id", "request", "state", "completed", "inputs", "error"} <= names
        manifest = schema.introspect(f)
    golden = json.loads((ROOT / "schema" / "schema_frozen.json").read_text(encoding="utf-8"))
    assert schema.diff_manifest(golden, manifest) == []


def test_a_job_refuses_a_bad_part_list_or_an_unknown_input_node(project: Project) -> None:
    with pytest.raises(ValueError, match="part"):
        jobs.submit_job(project, REQUEST, ("a", "a"))
    with pytest.raises(KeyError):
        jobs.submit_job(project, REQUEST, PARTS, inputs={("no_such_node", "1"): "x"})


def test_a_job_runs_its_parts_in_order_and_ends_done(project: Project) -> None:
    job = jobs.submit_job(project, REQUEST, PARTS)
    seen: list[tuple[int, int]] = []
    done = jobs.run_job(
        project,
        job.job_id,
        lambda part, _c: _write_part(project.path, part),
        progress=lambda n, m: seen.append((n, m)),
    )
    assert (done.state, done.completed, done.remaining) == (jobs.JobState.DONE, PARTS, ())
    assert seen == [(i, 5) for i in range(1, 6)]
    assert done.owner["nonce"] == project._held_lock.nonce
    with pytest.raises(jobs.JobError, match="only a queued job"):
        jobs.run_job(project, job.job_id, lambda *_: None)


# --- cancel, crash, retry -------------------------------------------------------------------


@pytest.mark.parametrize("inside_part", [True, False])
def test_a_cancel_in_the_middle_keeps_the_store_valid_and_the_complete_parts(
    project: Project, inside_part: bool
) -> None:
    job = jobs.submit_job(project, REQUEST, PARTS)
    token = jobs.CancelToken()

    def work(part: str, cancel: jobs.CancelToken) -> None:
        if part == "c":
            cancel.cancel()
            if inside_part:
                cancel.raise_if_cancelled()  # stops before part c writes anything
        _write_part(project.path, part)

    out = jobs.run_job(project, job.job_id, work, cancel=token)
    expected = ("a", "b") if inside_part else ("a", "b", "c")
    assert (out.state, out.completed) == (jobs.JobState.CANCELLED, expected)
    schema.assert_is_compatible_project(project.path)
    Project.open(project.path)
    assert _parts_in_store(project.path) == set(expected)


def _log(log: Path, part: str) -> None:
    with log.open("a") as fh:
        fh.write(part + "\n")


_CRASHING_CHILD = """
import os, sys
from pathlib import Path
from tether.project import jobs
from tether.project.core import Project

path, job_id, log = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
project = Project.open(path)
project.acquire_lock()

def work(part, cancel):
    with log.open("a") as fh:
        fh.write(part + "\\n")
    if part == "c":
        os._exit(3)  # the process stops in the middle of part c

jobs.run_job(project, job_id, work)
"""


def test_a_process_that_stops_mid_job_leaves_it_interrupted_and_retry_finishes_it(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path / "p.tether")
    with project.write_lock():
        job = jobs.submit_job(project, REQUEST, PARTS)
    log = tmp_path / "calls.txt"
    child = subprocess.run(
        [sys.executable, "-c", _CRASHING_CHILD, str(project.path), job.job_id, str(log)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert child.returncode == 3, child.stderr

    seen = jobs.read_job(project.path, job.job_id)  # the next open: a read, no lock
    assert (seen.stored_state, seen.state) == (jobs.JobState.RUNNING, jobs.JobState.INTERRUPTED)
    assert seen.completed == ("a", "b")

    project.steal_lock()  # the crashed session's lock is left behind
    try:
        assert jobs.reconcile_jobs(project) == [job.job_id]
        assert jobs.read_job(project.path, job.job_id).stored_state is jobs.JobState.INTERRUPTED
        done = jobs.retry_job(
            project,
            job.job_id,
            lambda part, _c: _log(log, part),
        )
    finally:
        project.release_lock()
    assert (done.state, done.completed) == (jobs.JobState.DONE, PARTS)
    assert log.read_text().split() == ["a", "b", "c", "c", "d", "e"]


def test_a_retry_of_n_complete_parts_of_m_does_not_do_the_n_parts_again(
    project: Project,
) -> None:
    job = jobs.submit_job(project, REQUEST, PARTS)
    calls: list[str] = []

    def failing(part: str, _c: jobs.CancelToken) -> None:
        calls.append(part)
        if part == "d":
            raise RuntimeError("movie d is unreadable")

    failed = jobs.run_job(project, job.job_id, failing)
    assert (failed.state, failed.completed) == (jobs.JobState.FAILED, ("a", "b", "c"))
    assert failed.error == "RuntimeError: movie d is unreadable"
    calls.clear()
    done = jobs.retry_job(project, job.job_id, lambda part, _c: calls.append(part))
    assert calls == ["d", "e"]
    assert (done.state, done.completed, done.error) == (jobs.JobState.DONE, PARTS, "")
    with pytest.raises(jobs.JobError, match="retried"):
        jobs.retry_job(project, job.job_id, lambda *_: None)


def test_an_interrupt_stores_interrupted_and_is_raised_again(project: Project) -> None:
    job = jobs.submit_job(project, REQUEST, PARTS)

    def work(part: str, _c: jobs.CancelToken) -> None:
        if part == "b":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        jobs.run_job(project, job.job_id, work)
    seen = jobs.read_job(project.path, job.job_id)
    assert (seen.stored_state, seen.completed) == (jobs.JobState.INTERRUPTED, ("a",))


# --- the write lock (PRD §7.10) -------------------------------------------------------------


_READER = """
import json, sys
from tether.project import jobs
job = jobs.read_job(sys.argv[1], sys.argv[2])
print(json.dumps([job.state.value, list(job.completed)]))
"""


def test_a_reader_in_another_process_never_blocks_on_a_running_job(project: Project) -> None:
    job = jobs.submit_job(project, REQUEST, PARTS)
    reads: list[list[object]] = []

    def work(part: str, _c: jobs.CancelToken) -> None:
        if part == "c":
            out = subprocess.run(
                [sys.executable, "-c", _READER, str(project.path), job.job_id],
                capture_output=True,
                text=True,
                cwd=ROOT,
                timeout=60,
                check=True,
            )
            reads.append(json.loads(out.stdout))
        _write_part(project.path, part)

    jobs.run_job(project, job.job_id, work)
    # The other process sees the job running (its owner is a live process here).
    assert reads == [["running", ["a", "b"]]]


def test_a_job_needs_the_writer_lock_and_stops_when_it_is_stolen(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "p.tether")
    with project.write_lock():
        job = jobs.submit_job(project, REQUEST, PARTS)
    with pytest.raises(LockedError):
        jobs.run_job(project, job.job_id, lambda *_: None)

    project.acquire_lock()
    thief = Project(project.path, identity=LockIdentity("other-host", "someone", 4242))

    def work(part: str, _c: jobs.CancelToken) -> None:
        if part == "b":
            thief.steal_lock()

    with pytest.raises(LockedError):
        jobs.run_job(project, job.job_id, work)
    seen = jobs.read_job(project.path, job.job_id)
    assert seen.completed == ("a",)  # nothing was written after the steal
    assert seen.state is jobs.JobState.INTERRUPTED  # the lock no longer carries its nonce
    with pytest.raises(LockedError):
        jobs.set_stale_marks(project, "map", "m1", "new")


_HOLD_OPEN = """
import sys, time
from pathlib import Path
import h5py
with h5py.File(sys.argv[1], "r"):
    Path(sys.argv[2]).write_text("open")
    time.sleep(float(sys.argv[3]))
"""


def test_a_checkpoint_write_waits_out_a_reader_that_holds_the_file(
    project: Project, tmp_path: Path
) -> None:
    job = jobs.submit_job(project, REQUEST, PARTS)
    ready = tmp_path / "ready"
    reader = subprocess.Popen(
        [sys.executable, "-c", _HOLD_OPEN, str(project.path), str(ready), "0.5"], cwd=ROOT
    )
    try:
        for _ in range(400):
            if ready.exists() or reader.poll() is not None:
                break
            time.sleep(0.025)
        assert ready.exists()
        # The reader holds the file while the job starts and writes its first checkpoint.
        done = jobs.run_job(project, job.job_id, lambda *_: None)
    finally:
        reader.wait(timeout=60)
    assert (done.state, done.completed, done.error) == (jobs.JobState.DONE, PARTS, "")


def test_two_starts_of_one_job_in_one_process_never_both_run(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = jobs.submit_job(project, REQUEST, PARTS)

    def fail_at_c(part: str, _c: jobs.CancelToken) -> None:
        if part == "c":
            raise RuntimeError("disk full")

    assert jobs.run_job(project, job.job_id, fail_at_c).state is jobs.JobState.FAILED

    # Hold the first retry between its state check and its first write.
    real_write = jobs._write_job
    at_start, go_on = threading.Event(), threading.Event()

    def held_write(path: Path, job_id: str, check: object = None, **fields: str) -> None:
        if fields.get("state") == "running" and not at_start.is_set():
            at_start.set()
            assert go_on.wait(30)
        real_write(path, job_id, check, **fields)

    monkeypatch.setattr(jobs, "_write_job", held_write)
    ran: list[str] = []
    first: dict[str, jobs.JobRecord] = {}

    def retry() -> None:
        first["job"] = jobs.retry_job(project, job.job_id, lambda part, _c: ran.append(part))

    thread = threading.Thread(target=retry)
    thread.start()
    try:
        assert at_start.wait(30)
        with pytest.raises(jobs.JobError, match="already running"):
            jobs.retry_job(project, job.job_id, lambda part, _c: ran.append(part))
        with pytest.raises(jobs.JobError, match="already running"):
            jobs.run_job(project, job.job_id, lambda part, _c: ran.append(part))
    finally:
        go_on.set()
        thread.join(30)
    assert ran == ["c", "d", "e"]
    assert (first["job"].state, first["job"].completed) == (jobs.JobState.DONE, PARTS)


def test_a_steal_while_a_write_waits_for_the_file_stops_the_write(
    project: Project, tmp_path: Path
) -> None:
    job = jobs.submit_job(project, REQUEST, PARTS)
    ready = tmp_path / "ready"
    reader = subprocess.Popen(
        [sys.executable, "-c", _HOLD_OPEN, str(project.path), str(ready), "0.6"], cwd=ROOT
    )
    thief = Project(project.path, identity=LockIdentity("other-host", "someone", 4242))
    try:
        for _ in range(400):
            if ready.exists() or reader.poll() is not None:
                break
            time.sleep(0.025)
        assert ready.exists()
        # The first write passes its lock check, then waits for the reader; the lock is
        # taken over while it waits.
        stealer = threading.Timer(0.2, thief.steal_lock)
        stealer.start()
        with pytest.raises(LockedError):
            jobs.run_job(project, job.job_id, lambda *_: None)
        stealer.join(30)
    finally:
        reader.wait(timeout=60)
    seen = jobs.read_job(project.path, job.job_id)
    assert (seen.stored_state, seen.completed) == (jobs.JobState.QUEUED, ())


def test_a_reconcile_never_stores_interrupted_over_a_run_that_started_after_it_looked(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = jobs.submit_job(project, REQUEST, PARTS)
    owner = json.dumps({"nonce": project._held_lock.nonce, "pid": os.getpid()})
    # A run in this process that died without storing its end: running, and not active.
    jobs._write_job(project.path, job.job_id, state="running", owner=owner, started_utc="t0")
    assert jobs.read_job(project.path, job.job_id).state is jobs.JobState.INTERRUPTED

    real_read_jobs = jobs.read_jobs
    looked, go_on = threading.Event(), threading.Event()

    def paused_read_jobs(path: str | Path) -> list[jobs.JobRecord]:
        out = real_read_jobs(path)
        if threading.current_thread().name == "late-reconcile" and not looked.is_set():
            looked.set()
            assert go_on.wait(30)
        return out

    monkeypatch.setattr(jobs, "read_jobs", paused_read_jobs)
    late = threading.Thread(target=jobs.reconcile_jobs, args=(project,), name="late-reconcile")
    late.start()
    assert looked.wait(30)

    in_part, finish = threading.Event(), threading.Event()

    def work(part: str, _c: jobs.CancelToken) -> None:
        if part == "a":
            in_part.set()
            assert finish.wait(30)

    retried: dict[str, jobs.JobRecord] = {}
    retry = threading.Thread(
        target=lambda: retried.update(job=jobs.retry_job(project, job.job_id, work))
    )
    retry.start()
    try:
        assert in_part.wait(30)
        go_on.set()
        late.join(30)
        # The late reconcile saw the dead run; the retry that started since is not touched.
        assert jobs.read_job(project.path, job.job_id).stored_state is jobs.JobState.RUNNING
    finally:
        go_on.set()
        finish.set()
        retry.join(30)
    assert retried["job"].state is jobs.JobState.DONE


# --- the stale marks (ADR-0103) -------------------------------------------------------------


def _populate_others(project: Project) -> None:
    """One record of every node with an upstream, made from instances named ``other``."""
    for e in graph.EDGES:
        jobs.record_inputs(project, e.downstream, f"x{e.number}", {(e.upstream, "other"): "v1"})


@pytest.mark.parametrize("edge", graph.EDGES, ids=lambda e: f"edge{e.number}")
def test_each_edge_event_marks_the_downstream_record_and_no_other(
    project: Project, edge: graph.Edge
) -> None:
    _populate_others(project)
    jobs.record_inputs(project, edge.downstream, "d", {(edge.upstream, "u"): "v1"})
    guards = {edge.number} if edge.guarded else set()

    assert jobs.set_stale_marks(project, edge.upstream, "u", "v1", guards_hold=guards) == []
    if edge.guarded:  # the owner says the guard does not hold: nothing is marked
        assert jobs.set_stale_marks(project, edge.upstream, "u", "v2") == []
    marks = jobs.set_stale_marks(project, edge.upstream, "u", "v2", guards_hold=guards)

    assert [(m.node, m.instance, m.edge) for m in marks] == [(edge.downstream, "d", edge.number)]
    assert marks[0].event == edge.event
    stored = jobs.read_stale_marks(project.path)
    assert [(m.node, m.instance) for m in stored] == [(edge.downstream, "d")]


@pytest.mark.parametrize("node", graph.NODES, ids=lambda n: n.key)
def test_the_marks_of_an_edit_reach_exactly_the_nodes_marked_by_names(
    project: Project, node: graph.Node
) -> None:
    for n in graph.NODES:
        ups = {(e.upstream, "i"): "v1" for e in graph.EDGES if e.downstream == n.key}
        if ups:
            jobs.record_inputs(project, n.key, "i", ups)
    every_guard = {e.number for e in graph.EDGES if e.guarded}
    marks = jobs.set_stale_marks(project, node.key, "i", "v2", guards_hold=every_guard)
    assert {m.node for m in marks} == graph.marked_by(node.key)
    assert len(marks) == len({m.node for m in marks})  # a record is marked once
    assert jobs.set_stale_marks(project, node.key, "i", "v3", guards_hold=every_guard) == []


def test_a_label_edit_marks_the_factor_version_but_no_fit(project: Project) -> None:
    jobs.record_inputs(project, "factor_version", "f1", {("curation_labels", "L"): "h1"})
    jobs.record_inputs(project, "correction_version", "c1", {("factor_version", "f1"): "1"})
    jobs.record_inputs(project, "fit", "fit1", {("correction_version", "c1"): "1"})
    marks = jobs.set_stale_marks(project, "curation_labels", "L", "h2", guards_hold={11})
    assert [(m.node, m.edge) for m in marks] == [("factor_version", 11), ("correction_version", 19)]


def test_recorded_inputs_need_an_edge_and_are_recorded_once(project: Project) -> None:
    with pytest.raises(ValueError, match="no edge"):
        jobs.record_inputs(project, "fit", "f", {("map", "m"): "x"})
    jobs.record_inputs(project, "fit", "f", {("trace_generation", "1"): "x"})
    with pytest.raises(ValueError, match="already recorded"):
        jobs.record_inputs(project, "fit", "f", {("trace_generation", "1"): "y"})
    assert [r.identity for r in jobs.read_recorded_inputs(project.path)] == ["x"]


# --- the reference page ---------------------------------------------------------------------


def test_the_reference_page_documents_every_job_state_and_table() -> None:
    page = FORMAT_PAGE.read_text(encoding="utf-8")
    section = page.split("`/settings/jobs`", 1)[1]
    for state in jobs.JobState:
        assert f"`{state.value}`" in section, state
    for table in ("`/settings/recorded_inputs`", "`/settings/stale_marks`"):
        assert table in page
