# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Qt adapter of the background tasks of the window (#343).

[`TaskService`][] runs the work of a long action on the one worker thread of a
[`TaskRunner`][tether.gui.tasks.TaskRunner], and keeps the window live while it runs:

* it sets the wait cursor and starts the busy indicator when a task starts;
* a ``QTimer`` on the main thread polls the task, so no Qt object is touched from the worker;
* when the task finishes, it stops the timer, restores the cursor, stops the indicator and
  calls ``on_finished`` with the [`TaskOutcome`][tether.gui.tasks.TaskOutcome], on the main
  thread.

The caller decides what the result means, for example that a result for a molecule that is no
longer selected is not drawn. A task that
[`TaskService.abandon`][tether.gui.task_service.TaskService.abandon] forgets keeps running. Its
future settles later, and a caller that holds a lock for the task waits on that future.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tether.gui.tasks import BackgroundTask, TaskOutcome, TaskRunner

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Future

__all__ = ["TaskService"]


class TaskService:
    """Run one background task at a time and apply its outcome on the main thread.

    Create it on the main thread, after the ``QApplication``.

    Parameters
    ----------
    poll_ms : int
        The interval of the main-thread poll, in milliseconds.
    indicator : object, optional
        The busy indicator, an object with ``start(name)`` and ``stop()`` such as
        [`BusyIndicator`][tether.gui.indicators.BusyIndicator]. The default is ``None``, no
        indicator.
    """

    def __init__(self, *, poll_ms: int, indicator: Any = None) -> None:
        self._poll_ms = poll_ms
        self._indicator = indicator
        self._runner = TaskRunner()
        self._timer: Any = None
        self._on_finished: Callable[[TaskOutcome], None] | None = None

    @property
    def busy(self) -> bool:
        """Whether a task is active: started, and its outcome not yet applied."""
        return self._runner.busy

    @property
    def active(self) -> BackgroundTask | None:
        """The active task, else ``None``."""
        return self._runner.active

    @property
    def future(self) -> Future[Any] | None:
        """The future of the active task, else ``None``."""
        task = self._runner.active
        return None if task is None else task.future

    @property
    def polling(self) -> bool:
        """Whether the main-thread poll timer runs."""
        return self._timer is not None

    def start(
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        context: Any = None,
        label: str | None = None,
        on_finished: Callable[[TaskOutcome], None],
        cancellable: bool = False,
    ) -> BackgroundTask | None:
        """Start ``fn(*args)`` on the worker thread, with the wait cursor and the indicator.

        Parameters
        ----------
        fn : callable
            The work. It runs on the worker thread and must not touch a Qt object.
        *args : object
            The positional arguments of ``fn``.
        context : object, optional
            A value that identifies the task, returned on its outcome.
        label : str, optional
            The name that the busy indicator shows. The default is ``None``, which leaves
            the indicator as it is.
        on_finished : callable
            Called on the main thread with the [`TaskOutcome`][tether.gui.tasks.TaskOutcome]
            when the task finishes. The service is not busy when it is called.
        cancellable : bool, optional
            Whether the task can be cancelled. The default is ``False``.

        Returns
        -------
        BackgroundTask or None
            The new task, or ``None`` when a task is already active.
        """
        from pyqtgraph.Qt import QtCore, QtWidgets

        task = self._runner.submit(fn, *args, context=context, cancellable=cancellable)
        if task is None:
            return None
        self._on_finished = on_finished
        if label is not None and self._indicator is not None:
            self._indicator.start(label)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        self._timer = QtCore.QTimer()
        self._timer.setInterval(self._poll_ms)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        return task

    def poll(self) -> None:
        """Apply the outcome of the active task when it has finished.

        The poll timer calls this on the main thread. It does nothing while the task runs.
        """
        outcome = self._runner.take_finished()
        if outcome is None:
            return
        on_finished = self._on_finished
        self._end()
        if on_finished is not None:
            on_finished(outcome)

    def abandon(self) -> BackgroundTask | None:
        """Forget the active task without waiting for it, and restore the cursor.

        The outcome of the task is not applied. The work keeps running on the worker thread
        until it stops; the returned task's future settles then.

        Returns
        -------
        BackgroundTask or None
            The task that was active, else ``None``.
        """
        task = self._runner.abandon()
        if task is not None:
            self._end()
        return task

    def shutdown(self) -> None:
        """Stop the worker thread after its work, without waiting for it."""
        self._runner.shutdown()

    def _end(self) -> None:
        # Undo exactly what start() did: one override cursor push, one indicator start.
        from pyqtgraph.Qt import QtWidgets

        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._on_finished = None
        if self._indicator is not None:
            self._indicator.stop()
        QtWidgets.QApplication.restoreOverrideCursor()
