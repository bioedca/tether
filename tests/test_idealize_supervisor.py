# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidecar supervision unit tests (PRD §7.11 FR-BATCH PR7-B; ADR-0031).

Covers the three supervision pieces headlessly — no real sidecar env (CI has none):

* :func:`tether.idealize.supervisor.supervise_idealize` — auto-restart *transient*
  sidecar failures up to N, never a deterministic (sidecar-reported) fit error, and
  :class:`RestartsExhausted` once the budget is spent.
* :func:`tether.idealize.supervisor.probe_sidecar` — the startup liveness probe never
  raises; every failure mode maps to ``available=False`` with a detail.
* :class:`tether.idealize.driver.SidecarError` ``transient`` classification in
  ``run_vbfret`` (crash/timeout = transient; a reported ``ok=False`` status = terminal),
  plus the ``_sidecar_runner --probe`` dispatch.
"""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from tether.idealize._sidecar_runner import STATUS_PREFIX  # noqa: E402
from tether.idealize._sidecar_runner import main as sidecar_main  # noqa: E402
from tether.idealize.driver import (  # noqa: E402
    SIDECAR_ENV_VAR,
    SidecarError,
    StateModel,
    resolve_sidecar_python,
)
from tether.idealize.supervisor import (  # noqa: E402
    DEFAULT_MAX_RESTARTS,
    ProbeResult,
    RestartsExhausted,
    SidecarSupervision,
    probe_sidecar,
    supervise_idealize,
)

# --- SidecarSupervision validation -------------------------------------------


def test_defaults_and_max_attempts() -> None:
    sup = SidecarSupervision()
    assert sup.max_restarts == DEFAULT_MAX_RESTARTS == 3
    assert sup.max_attempts == 4  # restarts + 1
    assert sup.defer_if_unavailable is True


@pytest.mark.parametrize("bad", [-1, -5])
def test_negative_max_restarts_rejected(bad: int) -> None:
    with pytest.raises(ValueError, match="max_restarts must be >= 0"):
        SidecarSupervision(max_restarts=bad)


@pytest.mark.parametrize("field", ["timeout", "probe_timeout"])
def test_nonpositive_timeouts_rejected(field: str) -> None:
    with pytest.raises(ValueError, match=f"{field} must be > 0 or None"):
        SidecarSupervision(**{field: 0})
    # None is allowed (wait indefinitely).
    SidecarSupervision(**{field: None})


# --- supervise_idealize ------------------------------------------------------


class _Runner:
    """A runner that raises the queued exceptions in turn, then returns ``result``."""

    def __init__(self, *, raises: list[BaseException] | None = None, result: object = "ok") -> None:
        self._raises = list(raises or [])
        self._result = result
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        if self._raises:
            raise self._raises.pop(0)
        return self._result


def test_success_first_try_no_restart() -> None:
    runner = _Runner(result="model")
    restarts: list[int] = []
    out = supervise_idealize(
        runner,
        "proj",
        supervision=SidecarSupervision(max_restarts=3),
        on_restart=lambda n, exc: restarts.append(n),
    )
    assert out == "model"
    assert runner.calls == 1
    assert restarts == []


def test_transient_failures_then_success() -> None:
    # Two transient crashes, then success on the third attempt (within N=3 restarts).
    runner = _Runner(
        raises=[SidecarError("crash", transient=True), SidecarError("crash", transient=True)],
        result="model",
    )
    restarts: list[int] = []
    out = supervise_idealize(
        runner,
        supervision=SidecarSupervision(max_restarts=3),
        on_restart=lambda n, exc: restarts.append(n),
    )
    assert out == "model"
    assert runner.calls == 3
    assert restarts == [1, 2]  # one restart notice per re-launch, 1-based


def test_deterministic_error_not_retried() -> None:
    runner = _Runner(raises=[SidecarError("bad fit", transient=False)])
    restarts: list[int] = []
    with pytest.raises(SidecarError, match="bad fit") as ei:
        supervise_idealize(
            runner,
            supervision=SidecarSupervision(max_restarts=3),
            on_restart=lambda n, exc: restarts.append(n),
        )
    assert not isinstance(ei.value, RestartsExhausted)  # raised as-is, not wrapped
    assert runner.calls == 1
    assert restarts == []


def test_all_transient_exhausts_budget() -> None:
    runner = _Runner(raises=[SidecarError(f"crash {i}", transient=True) for i in range(4)])
    restarts: list[int] = []
    with pytest.raises(RestartsExhausted) as ei:
        supervise_idealize(
            runner,
            supervision=SidecarSupervision(max_restarts=3),
            on_restart=lambda n, exc: restarts.append(n),
        )
    assert runner.calls == 4  # 1 initial + 3 restarts
    assert restarts == [1, 2, 3]
    assert isinstance(ei.value, SidecarError)  # batch's except-Exception still catches it
    assert "after 3 restart(s)" in str(ei.value)
    assert isinstance(ei.value.__cause__, SidecarError)  # last error chained


def test_zero_restarts_single_attempt() -> None:
    runner = _Runner(raises=[SidecarError("crash", transient=True)])
    with pytest.raises(RestartsExhausted, match="after 0 restart"):
        supervise_idealize(runner, supervision=SidecarSupervision(max_restarts=0))
    assert runner.calls == 1


# --- probe_sidecar -----------------------------------------------------------


def _ok_run(cmd, env, timeout):  # noqa: ANN001
    return SimpleNamespace(
        returncode=0,
        stdout=STATUS_PREFIX + json.dumps({"ok": True, "probe": True, "detail": "ready"}) + "\n",
        stderr="",
    )


def test_probe_unavailable_when_interpreter_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SIDECAR_ENV_VAR, raising=False)
    result = probe_sidecar(None, _run=_ok_run)  # _run must not even be reached
    assert isinstance(result, ProbeResult)
    assert result.available is False
    assert "no sidecar interpreter" in result.detail


def test_probe_available_on_ok_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)  # a real, existing interpreter
    result = probe_sidecar(_run=_ok_run)
    assert result.available is True
    assert result.detail == "ready"


def test_probe_unavailable_on_reported_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)

    def _bad(cmd, env, timeout):  # noqa: ANN001
        return SimpleNamespace(
            returncode=1,
            stdout=STATUS_PREFIX + json.dumps({"ok": False, "error": "no tmaven"}) + "\n",
            stderr="Traceback…",
        )

    result = probe_sidecar(_run=_bad)
    assert result.available is False
    assert "no tmaven" in result.detail


def test_probe_unavailable_on_crash_without_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)

    def _crash(cmd, env, timeout):  # noqa: ANN001
        return SimpleNamespace(returncode=139, stdout="", stderr="segfault")

    result = probe_sidecar(_run=_crash)
    assert result.available is False
    assert "exit 139" in result.detail


def test_probe_unavailable_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)

    def _timeout(cmd, env, timeout):  # noqa: ANN001
        raise subprocess.TimeoutExpired(cmd, timeout)

    result = probe_sidecar(timeout=5, _run=_timeout)
    assert result.available is False
    assert "timed out after 5s" in result.detail


def test_probe_unavailable_on_launch_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)

    def _oserror(cmd, env, timeout):  # noqa: ANN001
        raise OSError("exec format error")

    result = probe_sidecar(_run=_oserror)
    assert result.available is False
    assert "could not launch" in result.detail


# --- SidecarError.transient classification in run_vbfret ---------------------


@pytest.fixture()
def _smd(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A touched SMD path + a resolvable sidecar interpreter (no real fit is run)."""
    smd = tmp_path / "in.smd.hdf5"
    smd.write_bytes(b"")  # exists() is all run_vbfret checks before launching
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)
    return smd


def test_run_vbfret_timeout_is_transient(_smd, monkeypatch: pytest.MonkeyPatch) -> None:
    from tether.idealize import driver

    def _timeout(*a, **k):  # noqa: ANN001, ANN002, ANN003
        raise subprocess.TimeoutExpired("cmd", 1.0)

    monkeypatch.setattr(driver.subprocess, "run", _timeout)
    with pytest.raises(SidecarError) as ei:
        driver.run_vbfret(_smd, timeout=1.0)
    assert ei.value.transient is True
    assert "timed out" in str(ei.value)


def test_run_vbfret_crash_without_status_is_transient(
    _smd, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tether.idealize import driver

    def _crash(*a, **k):  # noqa: ANN001, ANN002, ANN003
        return SimpleNamespace(returncode=1, stdout="tmaven noise\n", stderr="boom")

    monkeypatch.setattr(driver.subprocess, "run", _crash)
    with pytest.raises(SidecarError) as ei:
        driver.run_vbfret(_smd)
    assert ei.value.transient is True


def test_run_vbfret_reported_error_is_terminal(_smd, monkeypatch: pytest.MonkeyPatch) -> None:
    from tether.idealize import driver

    def _reported(*a, **k):  # noqa: ANN001, ANN002, ANN003
        return SimpleNamespace(
            returncode=1,
            stdout=STATUS_PREFIX + json.dumps({"ok": False, "error": "0 usable traces"}) + "\n",
            stderr="",
        )

    monkeypatch.setattr(driver.subprocess, "run", _reported)
    with pytest.raises(SidecarError) as ei:
        driver.run_vbfret(_smd)
    assert ei.value.transient is False  # sidecar reported it — a restart cannot help
    assert "0 usable traces" in str(ei.value)


def test_run_vbfret_reported_error_without_detail_message(
    _smd, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A cleanly-reported ok=False with an EMPTY error string is still terminal, and the
    # message must not claim "no status emitted" (a status WAS emitted).
    from tether.idealize import driver

    def _reported_empty(*a, **k):  # noqa: ANN001, ANN002, ANN003
        return SimpleNamespace(
            returncode=1,
            stdout=STATUS_PREFIX + json.dumps({"ok": False, "error": ""}) + "\n",
            stderr="",
        )

    monkeypatch.setattr(driver.subprocess, "run", _reported_empty)
    with pytest.raises(SidecarError) as ei:
        driver.run_vbfret(_smd)
    assert ei.value.transient is False
    assert "no status emitted" not in str(ei.value)
    assert "reported failure without detail" in str(ei.value)


def _fake_model() -> StateModel:
    return StateModel(model_type="vbconhmm", nstates=2, means=np.array([0.3, 0.7]), idealized=None)


def test_run_vbfret_ok_status_nonzero_exit_salvages_model(
    _smd, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The sidecar reported ok=True (model written) then crashed on teardown (exit != 0):
    # the completed model must be salvaged, not discarded to a restart.
    from tether.idealize import driver

    def _ok_then_crash(*a, **k):  # noqa: ANN001, ANN002, ANN003
        return SimpleNamespace(
            returncode=139,
            stdout=STATUS_PREFIX + json.dumps({"ok": True, "nstates": 2}) + "\n",
            stderr="segfault at exit",
        )

    monkeypatch.setattr(driver.subprocess, "run", _ok_then_crash)
    monkeypatch.setattr(driver, "read_model", lambda *a, **k: _fake_model())
    monkeypatch.setattr(driver, "read_smd", lambda *a, **k: SimpleNamespace(molecule_keys=["m1"]))

    result = driver.run_vbfret(_smd)  # must NOT raise
    assert result.model.nstates == 2
    assert result.status == {"ok": True, "nstates": 2}


def test_run_vbfret_ok_status_nonzero_exit_unreadable_model_is_transient(
    _smd, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ok=True + non-zero exit but the on-disk model is unreadable → a real process
    # failure worth a supervised restart (transient), not a silent success.
    from tether.idealize import driver

    def _ok_then_crash(*a, **k):  # noqa: ANN001, ANN002, ANN003
        return SimpleNamespace(
            returncode=139,
            stdout=STATUS_PREFIX + json.dumps({"ok": True}) + "\n",
            stderr="",
        )

    def _unreadable(*a, **k):  # noqa: ANN001, ANN002, ANN003
        raise ValueError("model group has no 'mean'")

    monkeypatch.setattr(driver.subprocess, "run", _ok_then_crash)
    monkeypatch.setattr(driver, "read_model", _unreadable)
    with pytest.raises(SidecarError) as ei:
        driver.run_vbfret(_smd)
    assert ei.value.transient is True
    assert "no readable model" in str(ei.value)


# --- resolve_sidecar_python config errors are terminal (not transient) -------


def test_resolve_sidecar_python_unset_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SIDECAR_ENV_VAR, raising=False)
    with pytest.raises(SidecarError) as ei:
        resolve_sidecar_python(None)
    assert ei.value.transient is False  # no restart can conjure an interpreter


def test_resolve_sidecar_python_missing_path_is_terminal(tmp_path) -> None:
    missing = tmp_path / "nope" / "python.exe"
    with pytest.raises(SidecarError) as ei:
        resolve_sidecar_python(missing)
    assert ei.value.transient is False


def test_probe_ignores_non_dict_status(monkeypatch: pytest.MonkeyPatch) -> None:
    # A stray STATUS_PREFIX line with a non-object JSON payload must not make
    # probe_sidecar raise (its "never raises" contract) — it reads as unavailable.
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)

    def _non_dict(cmd, env, timeout):  # noqa: ANN001
        return SimpleNamespace(returncode=0, stdout=STATUS_PREFIX + "42\n", stderr="")

    result = probe_sidecar(_run=_non_dict)
    assert result.available is False


# --- _sidecar_runner --probe dispatch ----------------------------------------


def test_sidecar_runner_probe_reports_missing_tmaven(capsys: pytest.CaptureFixture[str]) -> None:
    # tmaven is absent from the base env, so the probe reports ok=False (not a crash):
    # this exercises the --probe dispatch + JSON error reporting without a sidecar env.
    rc = sidecar_main(["_sidecar_runner.py", "--probe"])
    out = capsys.readouterr().out
    assert rc == 1
    line = next(x for x in out.splitlines() if x.startswith(STATUS_PREFIX))
    status = json.loads(line[len(STATUS_PREFIX) :])
    assert status["ok"] is False
    assert status["probe"] is True
    assert "error" in status
