# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Qt-free core of the window's background tasks (#343).

Nothing here creates a ``QApplication``: the core owns the task state and is tested on its own.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tether.gui.tasks import (
    BackgroundTask,
    CancelToken,
    TaskCancelled,
    TaskRunner,
    TaskState,
)

TASKS = Path(__file__).resolve().parents[1] / "src" / "tether" / "gui" / "tasks.py"
QT_TOP = {"PySide6", "PySide2", "PyQt6", "PyQt5", "qtpy", "pyqtgraph", "shiboken6"}


@pytest.fixture
def runner():
    r = TaskRunner()
    yield r
    r.shutdown()


def test_the_core_imports_no_qt_module() -> None:
    tree = ast.parse(TASKS.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & QT_TOP
    assert "tether" not in imported  # the core depends on nothing else in Tether


def test_importing_the_core_loads_no_qt_binding() -> None:
    code = (
        "import sys, tether.gui.tasks\n"
        f"print(sorted(m for m in sys.modules if m.split('.')[0] in {sorted(QT_TOP)!r}))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert out.stdout.strip() == "[]"


def test_the_state_names_are_the_job_coordinator_names() -> None:
    # #895 defines the stored job states; these values must stay identical so a stored
    # job can back a task with no change for callers.
    assert [s.value for s in TaskState] == [
        "queued",
        "running",
        "done",
        "failed",
        "cancelled",
        "interrupted",
    ]
    jobs = pytest.importorskip("tether.project.jobs", reason="the job coordinator (#895)")
    assert {s.value for s in TaskState} == {s.value for s in jobs.JobState}


def test_a_task_runs_then_reports_done_with_its_value_and_context(runner) -> None:
    started = threading.Event()
    gate = threading.Event()
    worker: list[str] = []

    def work(x):
        worker.append(threading.current_thread().name)
        started.set()
        gate.wait(timeout=5)
        return x + 1

    task = runner.submit(work, 41, context="m0")
    assert task is not None and runner.busy and runner.active is task
    assert started.wait(timeout=5)
    assert task.state is TaskState.RUNNING
    assert runner.take_finished() is None  # still running: nothing to take, still busy
    assert runner.busy
    gate.set()
    task.future.result(timeout=5)
    assert task.state is TaskState.DONE
    outcome = runner.take_finished()
    assert outcome is not None
    assert (outcome.context, outcome.state, outcome.value, outcome.error) == (
        "m0",
        TaskState.DONE,
        42,
        None,
    )
    assert not runner.busy and runner.active is None
    assert worker and worker[0] != threading.current_thread().name


def test_a_raising_task_reports_failed_with_its_error(runner) -> None:
    def boom():
        raise RuntimeError("sidecar exploded")

    task = runner.submit(boom, context="k")
    task.future.exception(timeout=5)
    assert task.state is TaskState.FAILED
    outcome = runner.take_finished()
    assert outcome.state is TaskState.FAILED
    assert isinstance(outcome.error, RuntimeError) and "sidecar exploded" in str(outcome.error)
    assert outcome.value is None


def test_one_task_at_a_time_a_second_submit_is_refused(runner) -> None:
    gate = threading.Event()
    first = runner.submit(gate.wait, 5)
    assert runner.submit(lambda: None) is None
    assert runner.active is first
    gate.set()
    first.future.result(timeout=5)
    # Finished but not taken: still the active task, still refusing.
    assert runner.submit(lambda: None) is None
    runner.take_finished()
    second = runner.submit(lambda: "again")
    assert second is not None and second.future.result(timeout=5) == "again"


def test_the_runner_has_exactly_one_worker_thread(runner) -> None:
    assert runner._executor._max_workers == 1


def test_a_task_that_is_not_cancellable_refuses_a_cancel(runner) -> None:
    gate = threading.Event()
    task = runner.submit(gate.wait, 5)
    assert not task.cancellable
    assert task.cancel() is False
    assert not task.cancel_requested
    gate.set()
    task.future.result(timeout=5)
    assert task.state is TaskState.DONE


def test_a_running_cancellable_task_stops_as_cancelled(runner) -> None:
    started = threading.Event()

    def work(*, cancel: CancelToken):
        started.set()
        while True:
            cancel.raise_if_cancelled()
            threading.Event().wait(0.005)

    task = runner.submit(work, cancellable=True, context="c")
    assert task.cancellable
    assert started.wait(timeout=5)
    assert task.cancel() is True
    assert task.cancel_requested
    task.future.exception(timeout=5)
    assert task.state is TaskState.CANCELLED
    outcome = runner.take_finished()
    assert (outcome.context, outcome.state, outcome.error) == ("c", TaskState.CANCELLED, None)


def test_a_queued_cancellable_task_is_removed_before_it_starts() -> None:
    # A raw task over a future that never started: the queued state and its cancel.
    from concurrent.futures import Future

    future: Future = Future()
    task = BackgroundTask(future, "q", CancelToken())
    assert task.state is TaskState.QUEUED
    assert task.cancel() is True
    assert task.state is TaskState.CANCELLED
    assert task.outcome().state is TaskState.CANCELLED


def test_a_finished_task_refuses_a_cancel(runner) -> None:
    task = runner.submit(lambda *, cancel: 1, cancellable=True)
    task.future.result(timeout=5)
    assert task.cancel() is False
    assert task.state is TaskState.DONE


def test_the_outcome_of_an_unfinished_task_is_an_error() -> None:
    from concurrent.futures import Future

    task = BackgroundTask(Future(), None, None)
    with pytest.raises(RuntimeError, match="queued"):
        task.outcome()


def test_abandon_forgets_the_task_and_its_future_still_settles(runner) -> None:
    gate = threading.Event()
    task = runner.submit(gate.wait, 5)
    assert runner.abandon() is task
    assert not runner.busy and runner.take_finished() is None
    assert not task.future.done()
    gate.set()
    task.future.result(timeout=5)
    assert task.state is TaskState.DONE
    assert runner.abandon() is None


def test_the_cancel_token_raises_only_after_a_cancel() -> None:
    token = CancelToken()
    token.raise_if_cancelled()
    assert not token.cancelled
    token.cancel()
    assert token.cancelled
    with pytest.raises(TaskCancelled):
        token.raise_if_cancelled()
