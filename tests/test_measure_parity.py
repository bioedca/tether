# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for the parity-measurement build-provenance probe.

The measurement script is a development tool rather than an importable package
module, so these tests load it from its checked-in path.  No sidecar is needed:
the short subprocess probe and the expensive cross-seed measurement are both
replaced with deterministic base-environment fakes.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tether.idealize.parity import SpreadSummary

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "measure_parity.py"
_SIDECAR_PYTHON = r"C:\private workstation\sidecar\python.exe"
_ESCAPED_SIDECAR_PYTHON = _SIDECAR_PYTHON.replace("\\", "\\\\")
_UNRELATED_PRIVATE_PATH = r"C:\Users\alice\.ssh\private_key"
_CREDENTIAL_SHAPED_URL = "https://build-user:" + "synthetic-password@" + "example.invalid/probe"
_CREDENTIAL_SHAPED_VALUE = "TETHER_TEST_" + "TOKEN=synthetic-only"
_UNTRUSTED_DIAGNOSTICS = (
    f"path={_UNRELATED_PRIVATE_PATH}; url={_CREDENTIAL_SHAPED_URL}; "
    f"credential={_CREDENTIAL_SHAPED_VALUE}"
)
_TMAVEN_COMMIT = "10f4230b6d13c6d2ad67b05d801696b4a40eff4a"


def _load_script():
    spec = importlib.util.spec_from_file_location("tether_measure_parity", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


measure = _load_script()


def _completed_probe(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        [_SIDECAR_PYTHON, "-c", measure._BUILD_PROBE],
        returncode=0,
        stdout=stdout,
        stderr="",
    )


def _assert_path_absent(value: object) -> None:
    serialized = value if isinstance(value, str) else json.dumps(value)
    assert _SIDECAR_PYTHON not in serialized
    assert _ESCAPED_SIDECAR_PYTHON not in serialized
    assert _UNRELATED_PRIVATE_PATH not in serialized
    assert _UNRELATED_PRIVATE_PATH.replace("\\", "\\\\") not in serialized
    assert _CREDENTIAL_SHAPED_URL not in serialized
    assert _CREDENTIAL_SHAPED_VALUE not in serialized


def _assert_unrecorded(result: dict[str, str]) -> None:
    assert result["sidecar_python_version"] == "unrecorded"
    assert result["tmaven_commit"] == "unrecorded"
    assert result["build_provenance"].strip()
    assert result["build_probe_error"].strip()
    _assert_path_absent(result)


def test_probe_sidecar_build_records_version_commit_and_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command, **kwargs):
        assert command == [_SIDECAR_PYTHON, "-c", measure._BUILD_PROBE]
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "timeout": 120.0,
            "check": True,
        }
        return _completed_probe(
            json.dumps(
                {
                    "sidecar_python_version": "3.12.13",
                    "tmaven_commit": _TMAVEN_COMMIT,
                }
            )
            + "\n"
        )

    monkeypatch.setattr(measure.subprocess, "run", fake_run)

    result = measure.probe_sidecar_build(_SIDECAR_PYTHON)

    assert result["sidecar_python_version"] == "3.12.13"
    assert result["tmaven_commit"] == _TMAVEN_COMMIT
    assert result["build_provenance"].strip()
    assert "platform.python_version()" in result["build_provenance"]
    assert "PEP 610" in result["build_provenance"]
    _assert_path_absent(result)


def test_probe_sidecar_build_preserves_version_when_commit_is_unrecorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        measure.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed_probe(
            json.dumps(
                {
                    "sidecar_python_version": "3.12.13",
                    "tmaven_commit": "unrecorded",
                    "tmaven_commit_probe_error": _UNTRUSTED_DIAGNOSTICS,
                }
            )
        ),
    )

    result = measure.probe_sidecar_build(_SIDECAR_PYTHON)

    assert result["sidecar_python_version"] == "3.12.13"
    assert result["tmaven_commit"] == "unrecorded"
    assert result["build_provenance"].strip()
    _assert_path_absent(result)


def test_probe_sidecar_build_unset_interpreter_is_unrecorded() -> None:
    _assert_unrecorded(measure.probe_sidecar_build(None))


@pytest.mark.parametrize(
    ("exception", "expected_error"),
    [
        (
            subprocess.CalledProcessError(
                7,
                [_SIDECAR_PYTHON, "-c", measure._BUILD_PROBE],
                stderr=(
                    f"raw={_SIDECAR_PYTHON}; escaped={_ESCAPED_SIDECAR_PYTHON}; "
                    f"{_UNTRUSTED_DIAGNOSTICS}"
                ),
            ),
            "CalledProcessError: exit 7",
        ),
        (
            subprocess.TimeoutExpired(
                [_SIDECAR_PYTHON, "-c", measure._BUILD_PROBE],
                120.0,
                stderr=(
                    f"raw={_SIDECAR_PYTHON}; escaped={_ESCAPED_SIDECAR_PYTHON}; "
                    f"{_UNTRUSTED_DIAGNOSTICS}"
                ),
            ),
            "TimeoutExpired: timed out after 120.0s",
        ),
    ],
)
def test_sanitized_probe_error_omits_raw_and_escaped_interpreter_path(
    exception: BaseException,
    expected_error: str,
) -> None:
    error = measure._sanitized_probe_error(exception, _SIDECAR_PYTHON)

    assert error == expected_error
    _assert_path_absent(error)


def test_sanitized_probe_error_omits_untrusted_generic_exception_detail() -> None:
    error = measure._sanitized_probe_error(
        ValueError(f"invalid probe output: {_UNTRUSTED_DIAGNOSTICS}"),
        _SIDECAR_PYTHON,
    )

    assert error == "ValueError: invalid build probe output"
    _assert_path_absent(error)


@pytest.mark.parametrize(
    "exception",
    [
        subprocess.CalledProcessError(
            7,
            [_SIDECAR_PYTHON, "-c", measure._BUILD_PROBE],
            stderr=f"failed under {_ESCAPED_SIDECAR_PYTHON}; {_UNTRUSTED_DIAGNOSTICS}",
        ),
        subprocess.TimeoutExpired(
            [_SIDECAR_PYTHON, "-c", measure._BUILD_PROBE],
            120.0,
            stderr=f"timed out under {_SIDECAR_PYTHON}; {_UNTRUSTED_DIAGNOSTICS}",
        ),
    ],
)
def test_probe_sidecar_build_subprocess_failures_are_unrecorded(
    monkeypatch: pytest.MonkeyPatch,
    exception: BaseException,
) -> None:
    def fail_probe(*_args, **_kwargs):
        raise exception

    monkeypatch.setattr(measure.subprocess, "run", fail_probe)

    _assert_unrecorded(measure.probe_sidecar_build(_SIDECAR_PYTHON))


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "not JSON",
        json.dumps(
            {
                "sidecar_python_version": "3.12.13",
                "tmaven_commit": "not-a-40-hex-commit",
            }
        ),
        json.dumps(
            {
                "sidecar_python_version": _SIDECAR_PYTHON,
                "tmaven_commit": _TMAVEN_COMMIT,
            }
        ),
        json.dumps(["not", "an", "object"]),
    ],
)
def test_probe_sidecar_build_invalid_or_empty_output_is_unrecorded(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    monkeypatch.setattr(
        measure.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed_probe(stdout),
    )

    _assert_unrecorded(measure.probe_sidecar_build(_SIDECAR_PYTHON))


def _configure_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    spread = {
        "state_count_fraction": SpreadSummary("state_count_fraction", "floor", [1.0]),
        "state_mean_abs_delta": SpreadSummary("state_mean_abs_delta", "ceiling", [0.0]),
        "viterbi_agreement": SpreadSummary("viterbi_agreement", "floor", [1.0]),
        "relative_elbo": SpreadSummary("relative_elbo", "ceiling", [0.0]),
    }

    def fake_measure_spread(*_args, **_kwargs):
        return spread, [object()]

    monkeypatch.setattr(measure, "measure_spread", fake_measure_spread)
    monkeypatch.setenv("TETHER_SIDECAR_PYTHON", _SIDECAR_PYTHON)
    out_path = tmp_path / "parity.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(_SCRIPT),
            "--n-runs",
            "1",
            "--fixtures",
            "smd_4mol",
            "--out",
            str(out_path),
            "--scratch",
            str(tmp_path / "scratch"),
        ],
    )
    return out_path


def test_main_serializes_build_provenance_without_interpreter_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out_path = _configure_main(monkeypatch, tmp_path)
    monkeypatch.setattr(
        measure.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed_probe(
            json.dumps(
                {
                    "sidecar_python_version": "3.12.13",
                    "tmaven_commit": _TMAVEN_COMMIT,
                }
            )
        ),
    )

    assert measure.main() == 0

    serialized = out_path.read_text(encoding="utf-8")
    artifact = json.loads(serialized)
    assert artifact["method"]["sidecar_python_version"] == "3.12.13"
    assert artifact["method"]["tmaven_commit"] == _TMAVEN_COMMIT
    assert artifact["method"]["build_provenance"].strip()
    _assert_path_absent(serialized)


def test_main_preserves_python_version_when_commit_is_unrecorded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out_path = _configure_main(monkeypatch, tmp_path)
    monkeypatch.setattr(
        measure.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed_probe(
            json.dumps(
                {
                    "sidecar_python_version": "3.12.13",
                    "tmaven_commit": "unrecorded",
                    "tmaven_commit_probe_error": _UNTRUSTED_DIAGNOSTICS,
                }
            )
        ),
    )

    assert measure.main() == 0

    serialized = out_path.read_text(encoding="utf-8")
    artifact = json.loads(serialized)
    assert artifact["method"]["sidecar_python_version"] == "3.12.13"
    assert artifact["method"]["tmaven_commit"] == "unrecorded"
    assert artifact["method"]["build_provenance"].strip()
    _assert_path_absent(serialized)


def test_main_serializes_failed_probe_without_untrusted_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out_path = _configure_main(monkeypatch, tmp_path)

    def fail_probe(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            7,
            [_SIDECAR_PYTHON, "-c", measure._BUILD_PROBE],
            stderr=_UNTRUSTED_DIAGNOSTICS,
        )

    monkeypatch.setattr(measure.subprocess, "run", fail_probe)

    assert measure.main() == 0

    serialized = out_path.read_text(encoding="utf-8")
    artifact = json.loads(serialized)
    _assert_unrecorded(artifact["method"])
    _assert_path_absent(serialized)
