# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The background tasks of the window: a core that imports no Qt module (#343).

A long action of the window, such as the one-click idealize, runs on one worker thread so the
window stays live. This module holds the state of that work. The Qt adapter
[`TaskService`][tether.gui.task_service.TaskService] polls it from a main-thread timer and
applies each result on the main thread.

* A [`TaskRunner`][] runs **one task at a time** on one worker thread
  (``ThreadPoolExecutor(max_workers=1)``). A second submit while a task is active is refused.
* A [`BackgroundTask`][] reports its [`TaskState`][]. The state is read from the task's
  ``Future`` each time, so the core stores no second copy of it.
* Cancellation is part of the core. A task submitted as cancellable gets a [`CancelToken`][];
  its work stops by raising [`TaskCancelled`][]. A task that is not cancellable refuses a
  cancel, and the one-click idealize is not cancellable.

The state names are those of the stored jobs of the job coordinator (#895). This module stores
nothing in a project and keeps no record that outlives the process. A stored job can therefore
back a task later with no change for the callers of [`TaskRunner`][].
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Future

__all__ = [
    "BackgroundTask",
    "CancelToken",
    "TaskCancelled",
    "TaskOutcome",
    "TaskRunner",
    "TaskState",
]


class TaskState(StrEnum):
    """The state of a background task.

    The values are those of the job states of the job coordinator (#895), so one name means
    one state in both places.
    """

    #: Submitted and not started.
    QUEUED = "queued"
    #: The worker thread runs the task now.
    RUNNING = "running"
    #: The task returned a value.
    DONE = "done"
    #: The task raised an error.
    FAILED = "failed"
    #: The task stopped on a cancel, or a cancel removed it before it started.
    CANCELLED = "cancelled"
    #: A stored job whose owner stopped. An in-process task never reads as this state.
    INTERRUPTED = "interrupted"


class TaskCancelled(Exception):  # noqa: N818 - a signal, not an error
    """Raised by the work of a cancellable task to stop it as cancelled."""


class CancelToken:
    """A thread-safe flag that asks a running task to stop.

    The work of a cancellable task receives the token as its ``cancel`` keyword argument and
    reads it between its steps.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Ask the task to stop."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """Whether a cancel was asked for."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise [`TaskCancelled`][] when a cancel was asked for.

        Raises
        ------
        TaskCancelled
            When [`cancel`][tether.gui.tasks.CancelToken.cancel] was called.
        """
        if self._event.is_set():
            raise TaskCancelled


@dataclass(frozen=True)
class TaskOutcome:
    """The end of one task, as [`TaskRunner.take_finished`][] returns it.

    Attributes
    ----------
    context : object
        The value given as ``context`` at submit, for example the key of the molecule.
    state : TaskState
        [`TaskState.DONE`][], [`TaskState.FAILED`][] or [`TaskState.CANCELLED`][].
    value : object
        The return value of a task that is done, else ``None``.
    error : BaseException or None
        The error of a task that failed, else ``None``.
    """

    context: Any
    state: TaskState
    value: Any = None
    error: BaseException | None = None


class BackgroundTask:
    """One task that a [`TaskRunner`][] submitted.

    Parameters
    ----------
    future : concurrent.futures.Future
        The future of the work on the worker thread.
    context : object
        A value that the caller uses to identify the task.
    token : CancelToken or None
        The cancel token of a cancellable task, else ``None``.
    """

    def __init__(self, future: Future[Any], context: Any, token: CancelToken | None) -> None:
        self._future = future
        self._context = context
        self._token = token

    @property
    def future(self) -> Future[Any]:
        """The future of the work. It settles when the work stops on the worker thread."""
        return self._future

    @property
    def context(self) -> Any:
        """The value given as ``context`` at submit."""
        return self._context

    @property
    def cancellable(self) -> bool:
        """Whether [`cancel`][tether.gui.tasks.BackgroundTask.cancel] can stop the task."""
        return self._token is not None

    @property
    def cancel_requested(self) -> bool:
        """Whether a cancel was asked for and accepted."""
        return self._token is not None and self._token.cancelled

    @property
    def done(self) -> bool:
        """Whether the work stopped, for any reason."""
        return self._future.done()

    @property
    def state(self) -> TaskState:
        """The state of the task, read from its future now."""
        future = self._future
        if future.cancelled():
            return TaskState.CANCELLED
        if not future.done():
            return TaskState.RUNNING if future.running() else TaskState.QUEUED
        error = future.exception()
        if error is None:
            return TaskState.DONE
        if isinstance(error, TaskCancelled):
            return TaskState.CANCELLED
        return TaskState.FAILED

    def cancel(self) -> bool:
        """Ask the task to stop.

        A task that has not started is removed from the queue. A running task stops when its
        work next reads its token.

        Returns
        -------
        bool
            ``True`` when the task is cancellable and not yet finished, else ``False``. A task
            that is not cancellable is left as it is.
        """
        if self._token is None or self._future.done():
            return False
        self._token.cancel()
        self._future.cancel()
        return True

    def outcome(self) -> TaskOutcome:
        """Return the outcome of the task.

        Returns
        -------
        TaskOutcome
            The state, the return value and the error of the finished task.

        Raises
        ------
        RuntimeError
            When the work has not stopped.
        """
        state = self.state
        if state is TaskState.DONE:
            return TaskOutcome(self._context, state, value=self._future.result())
        if state is TaskState.FAILED:
            return TaskOutcome(self._context, state, error=self._future.exception())
        if state is TaskState.CANCELLED:
            return TaskOutcome(self._context, state)
        raise RuntimeError(f"the task is {state.value}, not finished")


class TaskRunner:
    """Run one background task at a time on one worker thread.

    The runner holds the active task until a caller takes its outcome with
    [`take_finished`][tether.gui.tasks.TaskRunner.take_finished] or forgets it with
    [`abandon`][tether.gui.tasks.TaskRunner.abandon]. Both are called from one thread, the
    thread that submits.

    Examples
    --------
    >>> from tether.gui.tasks import TaskRunner, TaskState
    >>> runner = TaskRunner()
    >>> task = runner.submit(lambda x: x * 2, 21, context="m0")
    >>> _ = task.future.result(timeout=5)
    >>> outcome = runner.take_finished()
    >>> (outcome.context, outcome.state is TaskState.DONE, outcome.value, runner.busy)
    ('m0', True, 42, False)
    >>> runner.shutdown()
    """

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._active: BackgroundTask | None = None

    @property
    def active(self) -> BackgroundTask | None:
        """The task that was submitted and not yet taken or abandoned, else ``None``."""
        return self._active

    @property
    def busy(self) -> bool:
        """Whether a task is active. An active task can be finished and not yet taken."""
        return self._active is not None

    def submit(
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        context: Any = None,
        cancellable: bool = False,
    ) -> BackgroundTask | None:
        """Start ``fn(*args)`` on the worker thread.

        Parameters
        ----------
        fn : callable
            The work. A cancellable task is called as ``fn(*args, cancel=token)``.
        *args : object
            The positional arguments of ``fn``.
        context : object, optional
            A value that identifies the task, returned on its outcome. The default is
            ``None``.
        cancellable : bool, optional
            Whether the task gets a [`CancelToken`][]. The default is ``False``.

        Returns
        -------
        BackgroundTask or None
            The new active task, or ``None`` when a task is already active.
        """
        if self._active is not None:
            return None
        token = CancelToken() if cancellable else None
        call = partial(fn, *args, cancel=token) if token is not None else partial(fn, *args)
        self._active = BackgroundTask(self._executor.submit(call), context, token)
        return self._active

    def take_finished(self) -> TaskOutcome | None:
        """Return the outcome of the active task when it has finished, and clear it.

        Returns
        -------
        TaskOutcome or None
            The outcome, or ``None`` when no task is active or the active task still runs.
            The runner is not busy after an outcome is returned.
        """
        task = self._active
        if task is None or not task.done:
            return None
        self._active = None
        return task.outcome()

    def abandon(self) -> BackgroundTask | None:
        """Forget the active task without waiting for it.

        The work is not stopped. Its future still settles when the work stops, so a caller
        that must wait for the work, such as a lock release, keeps the returned task.

        Returns
        -------
        BackgroundTask or None
            The task that was active, else ``None``.
        """
        task, self._active = self._active, None
        return task

    def shutdown(self) -> None:
        """Stop the worker thread after its work, without waiting for it."""
        self._executor.shutdown(wait=False)
