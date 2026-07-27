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


def test_tmaven_install_is_tmaven_only_and_cannot_resolve_dependencies() -> None:
    """The git source is installed without resolving or mutating the locked stack.

    pip's ``--require-hashes`` correctly rejects an unhashed VCS requirement, so tMAVEN
    cannot share either hashed requirements command. ``--no-deps`` keeps its undeclared
    dependency graph from changing the sidecar lock, and keeping this command first means
    the older setuptools wheel is not the build backend for tMAVEN.
    """
    assert setup.build_tmaven_pip_cmd("py", tmaven_spec="spec") == [
        "py",
        "-m",
        "pip",
        "install",
        "--no-build-isolation",
        "--no-deps",
        "spec",
    ]


def test_pytest_install_uses_a_separate_hash_locked_binary_source() -> None:
    cmd = setup.build_test_tools_pip_cmd("py")
    assert cmd == [
        "py",
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--only-binary=:all:",
        "--no-deps",
        "--require-hashes",
        "-r",
        str(setup.TEST_TOOLS_REQUIREMENTS),
    ]
    assert setup.TEST_TOOLS_REQUIREMENTS == _REPO_ROOT / "sidecar" / "pytest-requirements.txt"
    assert setup.TEST_TOOLS_REQUIREMENTS.exists()

    requirements = setup.TEST_TOOLS_REQUIREMENTS.read_text(encoding="utf-8")
    for pin in (
        "pytest==9.1.1",
        "iniconfig==2.3.0",
        "pluggy==1.6.0",
        "pygments==2.20.0",
        'colorama==0.4.6; sys_platform == "win32"',
    ):
        assert pin in requirements
    active_lines = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(line.startswith("packaging==") for line in active_lines)
    assert requirements.count("--hash=sha256:") == 5


def test_setuptools_install_uses_the_single_hash_locked_binary_source() -> None:
    cmd = setup.build_setuptools_pip_cmd("py")
    assert cmd == [
        "py",
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--only-binary=:all:",
        "--no-deps",
        "--require-hashes",
        "-r",
        str(setup.SETUPTOOLS_REQUIREMENTS),
    ]
    assert setup.SETUPTOOLS_REQUIREMENTS.name == "setuptools-compatibility.txt"
    assert setup.SETUPTOOLS_REQUIREMENTS.exists()


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


def test_main_dry_run_python_mode_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(*a, **k):
        raise AssertionError("subprocess must not run under --dry-run")

    monkeypatch.setattr(setup.subprocess, "run", _boom)
    rc = setup.main(["--dry-run", "--python", "/does/not/matter", "--with-pytest"])
    assert rc == 0
    output = capsys.readouterr().out
    tmaven_at = output.index(setup.DEFAULT_TMAVEN_SPEC)
    test_tools_at = output.index(str(setup.TEST_TOOLS_REQUIREMENTS))
    compatibility_at = output.index(str(setup.SETUPTOOLS_REQUIREMENTS))
    assert tmaven_at < test_tools_at < compatibility_at, (
        "the older compatibility wheel must be installed only after tMAVEN is built"
    )
    assert f"--no-build-isolation --no-deps {setup.DEFAULT_TMAVEN_SPEC}" in output
    assert (
        f"--only-binary=:all: --no-deps --require-hashes "
        f"-r {setup.TEST_TOOLS_REQUIREMENTS}" in output
    )


def test_main_runs_three_install_commands_in_dependency_safe_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sidecar_python = tmp_path / "python"
    sidecar_python.touch()
    commands: list[list[str]] = []

    def _record(cmd: list[str], *, dry_run: bool) -> None:
        assert not dry_run
        commands.append(cmd)

    monkeypatch.setattr(setup, "_run", _record)
    rc = setup.main(
        ["--python", str(sidecar_python), "--with-pytest", "--no-probe", "--tmaven-spec", "spec"]
    )

    assert rc == 0
    assert commands == [
        setup.build_tmaven_pip_cmd(str(sidecar_python), tmaven_spec="spec"),
        setup.build_test_tools_pip_cmd(str(sidecar_python)),
        setup.build_setuptools_pip_cmd(str(sidecar_python)),
    ]


def test_skip_install_skips_every_pip_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sidecar_python = tmp_path / "python"
    sidecar_python.touch()

    def _boom(*_args, **_kwargs):
        raise AssertionError("--skip-install must not run any install command")

    monkeypatch.setattr(setup, "_run", _boom)
    rc = setup.main(
        ["--python", str(sidecar_python), "--with-pytest", "--skip-install", "--no-probe"]
    )

    assert rc == 0
    assert (
        "Skipping tMAVEN + optional pytest test tools + compatibility-wheel installs"
        in capsys.readouterr().out
    )


def test_skip_install_help_names_every_skipped_pip_layer() -> None:
    help_text = " ".join(setup.build_parser().format_help().split())
    assert (
        "skip the tMAVEN, optional pytest test-tool, and compatibility-wheel installs" in help_text
    )
