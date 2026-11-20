# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Qt adapter of the window's background tasks (#343): poll, cursor, indicator, abandon."""

from __future__ import annotations

import threading

import pytest

from tether.gui.tasks import TaskState


class _Indicator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def start(self, name: str) -> None:
        self.calls.append(("start", name))

    def stop(self) -> None:
        self.calls.append(("stop", None))


@pytest.fixture
def service(qapp):
    from tether.gui.task_service import TaskService

    indicator = _Indicator()
    svc = TaskService(poll_ms=5, indicator=indicator)
    svc.test_indicator = indicator
    yield svc
    svc.abandon()
    svc.shutdown()


def _override_cursor():
    from pyqtgraph.Qt import QtWidgets

    cursor = QtWidgets.QApplication.overrideCursor()
    return None if cursor is None else cursor.shape()


def test_a_task_runs_off_the_main_thread_and_finishes_on_it(service, qtbot) -> None:
    from pyqtgraph.Qt import QtCore

    assert _override_cursor() is None
    gate = threading.Event()
    seen: dict[str, object] = {}
    main = threading.current_thread()

    def work(x):
        seen["worker"] = threading.current_thread()
        gate.wait(timeout=5)
        return x * 2

    def finished(outcome):
        seen["callback"] = threading.current_thread()
        seen["busy_in_callback"] = service.busy
        seen["cursor_in_callback"] = _override_cursor()
        seen["outcome"] = outcome

    task = service.start(work, 21, context="m0", label="Idealizing m0", on_finished=finished)
    assert task is not None
    assert service.busy and service.polling and service.future is task.future
    assert _override_cursor() == QtCore.Qt.CursorShape.WaitCursor
    assert service.test_indicator.calls == [("start", "Idealizing m0")]

    gate.set()
    qtbot.waitUntil(lambda: "outcome" in seen, timeout=5000)
    assert seen["worker"] is not main
    assert seen["callback"] is main  # the result is applied on the main thread
    assert seen["busy_in_callback"] is False
    assert seen["cursor_in_callback"] is None  # restored before the callback runs
    outcome = seen["outcome"]
    assert (outcome.context, outcome.state, outcome.value) == ("m0", TaskState.DONE, 42)
    assert not service.busy and not service.polling and service.future is None
    assert service.test_indicator.calls[-1] == ("stop", None)


def test_a_failing_task_reaches_the_callback_as_failed(service, qtbot) -> None:
    got = []

    def boom():
        raise RuntimeError("sidecar exploded")

    service.start(boom, context="k", on_finished=got.append)
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    assert got[0].state is TaskState.FAILED and "sidecar exploded" in str(got[0].error)
    assert _override_cursor() is None


def test_a_second_start_while_busy_is_refused_and_changes_nothing(service, qtbot) -> None:
    gate = threading.Event()
    got = []
    first = service.start(gate.wait, 5, label="first", on_finished=got.append)
    assert service.start(lambda: None, label="second", on_finished=got.append) is None
    assert service.active is first
    assert service.test_indicator.calls == [("start", "first")]
    gate.set()
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    assert len(got) == 1
    # Exactly one cursor push was made, and it was popped.
    assert _override_cursor() is None


def test_abandon_restores_the_cursor_and_drops_the_result(service, qtbot) -> None:
    gate = threading.Event()
    got = []
    task = service.start(gate.wait, 5, label="fit", on_finished=got.append)
    assert service.abandon() is task
    assert not service.busy and not service.polling
    assert _override_cursor() is None
    assert service.test_indicator.calls[-1] == ("stop", None)
    gate.set()
    task.future.result(timeout=5)
    qtbot.wait(30)  # several poll intervals: no callback for an abandoned task
    service.poll()
    assert got == []
    assert service.abandon() is None


def test_abandon_with_no_task_leaves_the_cursor_alone(service) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.CrossCursor)
    try:
        assert service.abandon() is None
        assert _override_cursor() == QtCore.Qt.CursorShape.CrossCursor
        assert service.test_indicator.calls == []
    finally:
        QtWidgets.QApplication.restoreOverrideCursor()


def test_a_cancellable_task_is_cancelled_through_the_service(service, qtbot) -> None:
    started = threading.Event()
    got = []

    def work(*, cancel):
        started.set()
        while True:
            cancel.raise_if_cancelled()
            threading.Event().wait(0.005)

    task = service.start(work, cancellable=True, context="c", on_finished=got.append)
    assert started.wait(timeout=5)
    assert task.cancel() is True
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    assert got[0].state is TaskState.CANCELLED and got[0].context == "c"
