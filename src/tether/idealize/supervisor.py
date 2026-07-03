# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidecar supervision for the batch idealize stage (PRD §7.11 FR-BATCH; ADR-0031).

The batch runner (:mod:`tether.project.batch`, M3 PR7-A) drives the tMAVEN
idealization sidecar as **one short-lived subprocess per movie**
(:func:`tether.idealize.run_vbfret`). This module adds the FR-BATCH
"sidecar supervision" paragraph on top of that model:

* **Startup liveness** — :func:`probe_sidecar` launches the sidecar interpreter on
  the ``--probe`` fast-path of :mod:`tether.idealize._sidecar_runner` (which imports
  and instantiates ``tmaven.maven.maven_class`` and exits), so a *merely present but
  broken* sidecar env (``tmaven`` missing/unimportable, or no interpreter configured
  at all) is detected **before** the batch commits to idealizing. The runner uses this
  to enter **idealization-deferred mode**: extract + correct still run and checkpoint
  for every movie, and idealization is queued for a later run with a working sidecar
  (the per-stage checkpoint of ADR-0030 makes the resume re-run only the deferred
  stage).
* **Auto-restart** — :func:`supervise_idealize` re-launches the sidecar up to
  ``max_restarts`` times (default **N = 3**, §11.2) on a **transient** failure (the
  process crashed or timed out — a liveness failure a fresh process can recover). A
  sidecar that *cleanly reported* a fit error (``SidecarError.transient`` is ``False``)
  is **not** retried: re-running the same data would only fail again. When every
  restart is spent the last error is re-raised as :class:`RestartsExhausted`, and the
  batch fails **only that movie's** idealization (extract + correct stay checkpointed).

Every piece is dependency-injectable (``_run`` on the probe, the runner passed to
:func:`supervise_idealize`) so the supervision policy is tested headlessly, without a
real sidecar env — CI has none (PRD §4.3).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tether.idealize.driver import (
    _RUNNER,
    SidecarError,
    _parse_status,
    _tail,
    resolve_sidecar_python,
)

if TYPE_CHECKING:
    from os import PathLike

__all__ = [
    "DEFAULT_MAX_RESTARTS",
    "DEFAULT_SIDECAR_TIMEOUT",
    "DEFAULT_PROBE_TIMEOUT",
    "SidecarSupervision",
    "ProbeResult",
    "RestartsExhausted",
    "probe_sidecar",
    "supervise_idealize",
]

#: Default auto-restart budget per movie's idealization (§11.2 "Batch sidecar
#: supervision"). ``N`` restarts means up to ``N + 1`` total attempts.
DEFAULT_MAX_RESTARTS = 3
#: Default per-idealization-call timeout, inherited from :func:`run_vbfret` (§11.2).
DEFAULT_SIDECAR_TIMEOUT = 1800.0
#: Default timeout for the one-shot startup liveness probe (import + instantiate
#: ``maven_class`` — far cheaper than a fit, but a cold ``tmaven``/Numba import is
#: not instant, so the window is generous).
DEFAULT_PROBE_TIMEOUT = 120.0


class RestartsExhausted(SidecarError):
    """Every supervised restart of the sidecar idealization failed.

    A :class:`~tether.idealize.driver.SidecarError` subclass, so the batch runner's
    existing ``except Exception`` isolation records it as a failed idealize stage for
    that movie without any new handling.
    """


@dataclass(frozen=True)
class SidecarSupervision:
    """Supervision policy for the batch idealize stage (PRD §7.11; §11.2).

    Parameters
    ----------
    max_restarts:
        Auto-restarts per movie's idealization on a *transient* sidecar failure
        (default :data:`DEFAULT_MAX_RESTARTS`). ``0`` disables restarts (a single
        attempt). A negative value is rejected.
    timeout:
        Per-idealization-call timeout in seconds, forwarded to the idealize runner
        (default :data:`DEFAULT_SIDECAR_TIMEOUT`). ``None`` waits indefinitely.
    probe_timeout:
        Timeout in seconds for the startup liveness probe (default
        :data:`DEFAULT_PROBE_TIMEOUT`). ``None`` waits indefinitely.
    defer_if_unavailable:
        When ``True`` (default), a failed startup liveness probe defers idealization
        for the whole run instead of failing every movie's idealize stage.
    sidecar_python:
        The sidecar interpreter (falls back to ``$TETHER_SIDECAR_PYTHON``). Owned here
        so the probe and every idealize call use the **same** env.
    """

    max_restarts: int = DEFAULT_MAX_RESTARTS
    timeout: float | None = DEFAULT_SIDECAR_TIMEOUT
    probe_timeout: float | None = DEFAULT_PROBE_TIMEOUT
    defer_if_unavailable: bool = True
    sidecar_python: str | PathLike[str] | None = None

    def __post_init__(self) -> None:
        if self.max_restarts < 0:
            raise ValueError(f"max_restarts must be >= 0, got {self.max_restarts}")
        for name in ("timeout", "probe_timeout"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be > 0 or None, got {value}")

    @property
    def max_attempts(self) -> int:
        """Total idealization attempts per movie (``max_restarts + 1``)."""
        return self.max_restarts + 1


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a startup sidecar liveness probe."""

    available: bool
    detail: str


def _default_probe_run(
    cmd: list[str], env: dict[str, str], timeout: float | None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - cmd is a resolved interpreter + our runner
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def probe_sidecar(
    sidecar_python: str | PathLike[str] | None = None,
    *,
    timeout: float | None = DEFAULT_PROBE_TIMEOUT,
    _run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> ProbeResult:
    """Check whether the sidecar env can build the tMAVEN driver (liveness).

    Resolves the sidecar interpreter, then launches
    :mod:`tether.idealize._sidecar_runner` in ``--probe`` mode (import + instantiate
    ``maven_class``, no fit). Never raises: a missing interpreter, a launch error, a
    timeout, or a non-``ok`` probe status all return :class:`ProbeResult`
    ``available=False`` with an actionable ``detail``.

    ``_run`` is an injectable subprocess launcher (test seam); it defaults to
    :func:`subprocess.run`.
    """
    try:
        py = resolve_sidecar_python(sidecar_python)
    except SidecarError as exc:
        return ProbeResult(available=False, detail=str(exc))

    cmd = [str(py), str(_RUNNER), "--probe"]
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("NAPARI_ASYNC", "0")

    runner = _run if _run is not None else _default_probe_run
    try:
        proc = runner(cmd, env, timeout)
    except subprocess.TimeoutExpired:
        return ProbeResult(
            available=False, detail=f"sidecar liveness probe timed out after {timeout}s"
        )
    except OSError as exc:  # interpreter vanished / not executable between resolve + launch
        return ProbeResult(
            available=False, detail=f"sidecar liveness probe could not launch: {exc}"
        )

    status = _parse_status(proc.stdout or "")
    if proc.returncode == 0 and status is not None and status.get("ok"):
        return ProbeResult(available=True, detail=str(status.get("detail") or "sidecar ready"))

    detail = status.get("error") if status is not None else None
    message = detail or f"sidecar liveness probe failed (exit {proc.returncode})"
    tail = _tail(proc.stderr or "")
    if tail:
        message += f"\n--- stderr (tail) ---\n{tail}"
    return ProbeResult(available=False, detail=message)


def supervise_idealize(
    runner: Callable[..., Any],
    /,
    *args: Any,
    supervision: SidecarSupervision,
    on_restart: Callable[[int, SidecarError], None] | None = None,
    **kwargs: Any,
) -> Any:
    """Call ``runner(*args, **kwargs)``, auto-restarting on transient sidecar failure.

    Retries a :class:`~tether.idealize.driver.SidecarError` whose ``transient`` flag is
    truthy (a crash or timeout — the process failed, a fresh one may succeed) up to
    ``supervision.max_restarts`` times. A **non-transient** ``SidecarError`` (the
    sidecar cleanly reported a fit error) is re-raised immediately — restarting cannot
    help. When the restart budget is exhausted the last error is re-raised as
    :class:`RestartsExhausted`.

    ``on_restart(restart_number, error)`` is invoked once per restart *before*
    re-launching (``restart_number`` is 1-based), so the caller can log the retry.
    """
    last: SidecarError | None = None
    for attempt in range(supervision.max_attempts):
        try:
            return runner(*args, **kwargs)
        except SidecarError as exc:
            last = exc
            if not getattr(exc, "transient", True):
                raise  # deterministic fit failure — a restart would only repeat it
            if attempt + 1 >= supervision.max_attempts:
                break  # budget spent; fall through to RestartsExhausted
            if on_restart is not None:
                on_restart(attempt + 1, exc)
    raise RestartsExhausted(
        f"sidecar idealization failed after {supervision.max_restarts} restart(s); "
        f"last error: {last}"
    ) from last
