# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless unit tests for the tMAVEN SMD open-check (issue #13, PRD §7.4).

Covers :func:`tether.idealize.check_smd_opens` and the ``_sidecar_runner --load-check``
dispatch *without a sidecar env* (CI has none) by injecting the subprocess launcher via
the ``_run`` seam — the same pattern as the ``probe_sidecar`` tests. The live end-to-end
open (a Tether SMD actually loaded by tMAVEN's own loader) is exercised by the
``@pytest.mark.sidecar`` suite ``test_handoff_sidecar.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from tether.idealize._sidecar_runner import STATUS_PREFIX  # noqa: E402
from tether.idealize._sidecar_runner import main as sidecar_main  # noqa: E402
from tether.idealize.driver import (  # noqa: E402
    SIDECAR_ENV_VAR,
    SidecarError,
    SMDOpenCheck,
    check_smd_opens,
)

_OK_STATUS = {
    "ok": True,
    "load_check": True,
    "nmol": 3,
    "nt": 40,
    "ncolors": 2,
    "raw_shape": [3, 40, 2],
    "raw_sum": 1234.5,
    "pre_list": [0, 3, 5],
    "post_list": [40, 38, 39],
    "classes": [0, 0, 0],
}


def _smd_file(tmp_path):
    """A real (but arbitrary) file so the existence guard passes; content is unused."""
    p = tmp_path / "handoff.hdf5"
    p.write_bytes(b"\x89HDF\r\n\x1a\n")  # HDF5 magic — never actually parsed here
    return p


def _ok_run(cmd, env, timeout):  # noqa: ANN001
    return SimpleNamespace(
        returncode=0, stdout=STATUS_PREFIX + json.dumps(_OK_STATUS) + "\n", stderr=""
    )


def test_check_smd_opens_unset_interpreter(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SIDECAR_ENV_VAR, raising=False)
    with pytest.raises(SidecarError, match="no sidecar interpreter"):
        check_smd_opens(_smd_file(tmp_path), _run=_ok_run)  # _run never reached


def test_check_smd_opens_missing_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)
    with pytest.raises(FileNotFoundError):
        check_smd_opens(tmp_path / "nope.hdf5", _run=_ok_run)


def test_check_smd_opens_parses_status(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)
    check = check_smd_opens(_smd_file(tmp_path), _run=_ok_run)
    assert isinstance(check, SMDOpenCheck)
    assert check.n_molecules == 3
    assert check.n_frames == 40
    assert check.n_channels == 2
    assert check.raw_shape == (3, 40, 2)
    assert check.raw_sum == 1234.5
    assert np.array_equal(check.pre_list, [0, 3, 5])
    assert np.array_equal(check.post_list, [40, 38, 39])
    assert np.array_equal(check.classes, [0, 0, 0])


def test_check_smd_opens_passes_group_to_runner(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)
    seen = {}

    def _capture(cmd, env, timeout):  # noqa: ANN001
        seen["cmd"] = cmd
        return _ok_run(cmd, env, timeout)

    check_smd_opens(_smd_file(tmp_path), group="other", _run=_capture)
    assert "--load-check" in seen["cmd"]
    assert seen["cmd"][-1] == "other"  # group is the last argv token


def test_check_smd_opens_reported_error_is_terminal(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)

    def _bad(cmd, env, timeout):  # noqa: ANN001
        return SimpleNamespace(
            returncode=1,
            stdout=STATUS_PREFIX + json.dumps({"ok": False, "error": "not smd format"}) + "\n",
            stderr="Traceback...",
        )

    with pytest.raises(SidecarError, match="not smd format") as exc:
        check_smd_opens(_smd_file(tmp_path), _run=_bad)
    assert exc.value.transient is False  # a clean tMAVEN-reported open failure is deterministic


def test_check_smd_opens_crash_without_status_is_transient(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)

    def _crash(cmd, env, timeout):  # noqa: ANN001
        return SimpleNamespace(returncode=139, stdout="", stderr="segfault")

    with pytest.raises(SidecarError) as exc:
        check_smd_opens(_smd_file(tmp_path), _run=_crash)
    assert exc.value.transient is True  # a crash before any status is a process-level failure


def test_check_smd_opens_timeout_is_transient(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)

    def _timeout(cmd, env, timeout):  # noqa: ANN001
        raise subprocess.TimeoutExpired(cmd, timeout)

    with pytest.raises(SidecarError, match="timed out after 5s") as exc:
        check_smd_opens(_smd_file(tmp_path), timeout=5, _run=_timeout)
    assert exc.value.transient is True


def test_check_smd_opens_launch_oserror_is_transient(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SIDECAR_ENV_VAR, sys.executable)

    def _oserror(cmd, env, timeout):  # noqa: ANN001
        raise OSError("exec format error")

    with pytest.raises(SidecarError, match="could not launch") as exc:
        check_smd_opens(_smd_file(tmp_path), _run=_oserror)
    assert exc.value.transient is True


# --- _sidecar_runner --load-check dispatch -----------------------------------


def test_sidecar_runner_load_check_bad_argc() -> None:
    # Wrong argument count is a usage error (exit 2) before any tmaven import.
    assert sidecar_main(["_sidecar_runner.py", "--load-check", "only-one-arg"]) == 2


def test_sidecar_runner_load_check_reports_missing_tmaven(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    # tmaven is absent from the base env, so --load-check reports ok=False (not a crash):
    # exercises the dispatch + JSON error reporting without a sidecar env.
    smd = _smd_file(tmp_path)
    rc = sidecar_main(["_sidecar_runner.py", "--load-check", str(smd), "dataset"])
    out = capsys.readouterr().out
    assert rc == 1
    line = next(x for x in out.splitlines() if x.startswith(STATUS_PREFIX))
    status = json.loads(line[len(STATUS_PREFIX) :])
    assert status["ok"] is False
    assert status["load_check"] is True
    assert "error" in status
