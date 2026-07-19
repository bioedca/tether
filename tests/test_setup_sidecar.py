# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit + contract tests for the guided sidecar-setup script (issue #13, PRD §4.3).

``scripts/setup_sidecar.py`` is the single documented way to turn a checkout into a
working ``$TETHER_SIDECAR_PYTHON``. These tests pin its command construction and, most
importantly, keep it in lockstep with the two other places the same recipe lives:
``.github/workflows/sidecar.yml`` (the live parity job) and
``tether.idealize._sidecar_runner`` (the probe status protocol). If those drift apart a
guided setup would silently install a *different* sidecar than CI validates.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "setup_sidecar.py"
_SIDECAR_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "sidecar.yml"


def _load_script():
    spec = importlib.util.spec_from_file_location("tether_setup_sidecar", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = _load_script()


def _workflow_tmaven_spec() -> str:
    for raw in _SIDECAR_WORKFLOW.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith("TMAVEN_SPEC:"):
            return stripped.split(":", 1)[1].strip().strip('"')
    raise AssertionError("sidecar.yml has no TMAVEN_SPEC env")


# --- command construction ----------------------------------------------------


def test_build_pip_cmd_is_the_exact_recipe() -> None:
    """The install command is exactly ``pip install --no-build-isolation [pytest] <pins>``.

    Pins the shape of the one command that installs the non-lock deps into the sidecar,
    the recipe the live parity job used to inline before delegating to this script.
    """
    assert setup.build_pip_cmd("py", tmaven_spec="spec", with_pytest=True) == [
        "py",
        "-m",
        "pip",
        "install",
        "--no-build-isolation",
        "pytest",
        setup.SETUPTOOLS_PIN,
        "spec",
    ]


def test_build_pip_cmd_without_pytest_drops_only_pytest() -> None:
    with_pytest = setup.build_pip_cmd("py", tmaven_spec="spec", with_pytest=True)
    without = setup.build_pip_cmd("py", tmaven_spec="spec", with_pytest=False)
    assert "pytest" in with_pytest and "pytest" not in without
    assert without == [t for t in with_pytest if t != "pytest"]
    # setuptools pin + tmaven spec are always last, in that order.
    assert without[-2:] == [setup.SETUPTOOLS_PIN, "spec"]


# --- lockstep contracts ------------------------------------------------------


def test_default_tmaven_spec_matches_sidecar_yml() -> None:
    # The script reads $TMAVEN_SPEC (set in sidecar.yml) but must default to the SAME pin,
    # so a fresh developer setup installs the tMAVEN the live parity job validates.
    assert _workflow_tmaven_spec() == setup.DEFAULT_TMAVEN_SPEC


def test_sidecar_yml_installs_via_setup_script() -> None:
    """The live parity job installs the sidecar via this script, not an inline pip line.

    Guards the CI leg of issue #13: the setup script is exercised on every live sidecar
    run. If the workflow ever reverts to a raw ``pip install`` the script would stop being
    tested in CI — this fails so the choice must be deliberate.
    """
    workflow = _SIDECAR_WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/setup_sidecar.py" in workflow
    assert "--with-pytest" in workflow
    # It targets the already-restored env (no env creation on the runner).
    assert "--python" in workflow


def test_status_prefix_matches_runner() -> None:
    from tether.idealize._sidecar_runner import STATUS_PREFIX

    assert setup.STATUS_PREFIX == STATUS_PREFIX


def test_paths_point_at_real_repo_files() -> None:
    # The lock the guided setup builds from and the runner it probes both exist.
    assert setup.DEFAULT_LOCK.name == "conda-lock.yml"
    assert setup.DEFAULT_LOCK.exists()
    assert setup._SIDECAR_RUNNER.exists()


# --- front-end + env-create construction -------------------------------------


def test_detect_conda_frontend_prefers_explicit() -> None:
    assert setup.detect_conda_frontend("micromamba") == "micromamba"


def test_detect_conda_frontend_raises_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup.shutil, "which", lambda _name: None)
    with pytest.raises(setup.SetupError, match="no conda front-end"):
        setup.detect_conda_frontend(None)


def test_env_create_prefers_conda_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup.shutil, "which", lambda name: f"/usr/bin/{name}")
    cmd = setup.build_env_create_cmd("micromamba", "tether-sidecar", setup.DEFAULT_LOCK)
    assert cmd[:2] == ["conda-lock", "install"]
    assert "--conda" in cmd and "micromamba" in cmd
    assert cmd[-1] == str(setup.DEFAULT_LOCK)


def test_env_create_falls_back_to_micromamba(monkeypatch: pytest.MonkeyPatch) -> None:
    # conda-lock absent -> micromamba/mamba create -f the lock file directly.
    monkeypatch.setattr(setup.shutil, "which", lambda name: None if name == "conda-lock" else name)
    cmd = setup.build_env_create_cmd("micromamba", "tether-sidecar", setup.DEFAULT_LOCK)
    assert cmd == [
        "micromamba",
        "create",
        "-y",
        "-n",
        "tether-sidecar",
        "-f",
        str(setup.DEFAULT_LOCK),
    ]


def test_env_create_rejects_plain_conda_without_conda_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup.shutil, "which", lambda _name: None)
    with pytest.raises(setup.SetupError, match="cannot install a unified conda-lock"):
        setup.build_env_create_cmd("conda", "tether-sidecar", setup.DEFAULT_LOCK)


# --- launch failures become clean SetupErrors (not raw tracebacks) -----------


def test_run_wraps_oserror_as_setup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a, **_k):
        raise OSError("not launchable")

    monkeypatch.setattr(setup.subprocess, "run", _boom)
    with pytest.raises(setup.SetupError, match="could not launch"):
        setup._run(["nope"], dry_run=False)


def test_resolve_env_python_wraps_oserror_as_setup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a, **_k):
        raise OSError("no such front-end")

    monkeypatch.setattr(setup.subprocess, "run", _boom)
    with pytest.raises(setup.SetupError, match="could not launch conda front-end"):
        setup.resolve_env_python("bogus-frontend", "tether-sidecar")


# --- status parsing + export line --------------------------------------------


def test_parse_status_last_object_wins_and_ignores_non_dict() -> None:
    import json

    stdout = "\n".join(
        [
            "noise",
            setup.STATUS_PREFIX + "42",  # non-object payload: ignored
            setup.STATUS_PREFIX + json.dumps({"ok": True, "detail": "first"}),
            setup.STATUS_PREFIX + json.dumps({"ok": True, "detail": "last"}),
        ]
    )
    assert setup._parse_status(stdout) == {"ok": True, "detail": "last"}
    assert setup._parse_status("nothing here") is None


def test_export_line_is_platform_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup.os, "name", "posix")
    assert setup._export_line("/env/bin/python").startswith("export TETHER_SIDECAR_PYTHON=")
    monkeypatch.setattr(setup.os, "name", "nt")
    assert setup._export_line("C:/env/python.exe").startswith("$env:TETHER_SIDECAR_PYTHON =")


# --- main() dry-run does not execute anything --------------------------------


def test_main_dry_run_python_mode_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a, **k):
        raise AssertionError("subprocess must not run under --dry-run")

    monkeypatch.setattr(setup.subprocess, "run", _boom)
    rc = setup.main(["--dry-run", "--python", "/does/not/matter", "--with-pytest"])
    assert rc == 0
