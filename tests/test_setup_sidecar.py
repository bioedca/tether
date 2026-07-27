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
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "setup_sidecar.py"
_SIDECAR_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "sidecar.yml"
_SIDECAR_MEASURE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "sidecar-measure.yml"
_HANDOFF_DOC = _REPO_ROOT / "docs" / "idealize" / "standalone-tmaven-handoff.md"


def _load_script():
    spec = importlib.util.spec_from_file_location("tether_setup_sidecar", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = _load_script()


def _workflow_tmaven_spec(workflow: Path) -> str:
    for raw in workflow.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith("TMAVEN_SPEC:"):
            return stripped.split(":", 1)[1].strip().strip('"')
    raise AssertionError(f"{workflow.name} has no TMAVEN_SPEC env")


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
        "--force-reinstall",
        "--no-cache-dir",
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
    active_lines = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for pin in (
        "pytest==9.1.1",
        "iniconfig==2.3.0",
        "pluggy==1.6.0",
        "pygments==2.20.0",
        'colorama==0.4.6; sys_platform == "win32"',
    ):
        assert any(pin in line for line in active_lines)
    assert not any(line.startswith("packaging==") for line in active_lines)
    assert sum(line.count("--hash=sha256:") for line in active_lines) == 5


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


def test_default_tmaven_spec_matches_sidecar_workflows() -> None:
    # Both workflows advertise $TMAVEN_SPEC, so they must use the exact pin that the
    # guided setup accepts and defaults to.
    for workflow in (_SIDECAR_WORKFLOW, _SIDECAR_MEASURE_WORKFLOW):
        assert _workflow_tmaven_spec(workflow) == setup.DEFAULT_TMAVEN_SPEC
    assert setup.DEFAULT_TMAVEN_COMMIT == "10f4230b6d13c6d2ad67b05d801696b4a40eff4a"
    assert setup.DEFAULT_TMAVEN_COMMIT.startswith(setup.DEFAULT_TMAVEN_SPEC.rsplit("@", 1)[1])


def test_tmaven_specs_resolve_only_the_default_pin_or_a_full_commit() -> None:
    custom_commit = "a" * 40
    assert (
        setup._require_immutable_tmaven_commit(setup.DEFAULT_TMAVEN_SPEC)
        == setup.DEFAULT_TMAVEN_COMMIT
    )
    assert (
        setup._require_immutable_tmaven_commit(
            f"git+https://example.test/tmaven.git@{custom_commit}"
        )
        == custom_commit
    )
    assert setup._expected_tmaven_commit("git+https://example.test/tmaven.git@main") is None
    assert setup._expected_tmaven_commit("git+https://example.test/tmaven.git@" + "b" * 39) is None


@pytest.mark.parametrize("revision", ["main", "v1.2.3", "deadbeef", "b" * 39])
def test_mutable_tmaven_spec_fails_before_any_sidecar_subprocess(
    revision: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sidecar_python = tmp_path / "python"
    sidecar_python.touch()

    def _boom(*_args, **_kwargs):
        raise AssertionError("an unsupported tMAVEN spec must fail before any subprocess")

    monkeypatch.setattr(setup, "inspect_sidecar_build_state", _boom)
    monkeypatch.setattr(setup, "_run", _boom)

    assert (
        setup.main(
            [
                "--python",
                str(sidecar_python),
                "--tmaven-spec",
                f"git+https://example.test/tmaven.git@{revision}",
                "--no-probe",
            ]
        )
        == 1
    )
    error = capsys.readouterr().err
    assert "unsupported --tmaven-spec" in error
    assert "rejected before the sidecar environment is changed" in error


def test_locked_build_setuptools_version_comes_from_the_unified_lock() -> None:
    assert setup.load_locked_setuptools_version(setup.DEFAULT_LOCK) == "82.0.1"


def test_build_state_probe_reads_the_imported_backend_and_wheel_generator() -> None:
    import setuptools

    state = setup.inspect_sidecar_build_state(sys.executable)

    assert state["setuptools_version"] == setuptools.__version__
    assert "tmaven_direct_url" in state
    assert "tmaven_wheel_generator" in state


def test_build_state_probe_timeout_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _timeout(cmd: list[str], **kwargs) -> None:
        assert kwargs["timeout"] == 120.0
        raise setup.subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(setup.subprocess, "run", _timeout)

    with pytest.raises(
        setup.SetupError,
        match=r"target sidecar build-state inspection timed out after 120\.0s",
    ) as exc_info:
        setup.inspect_sidecar_build_state("python")

    assert isinstance(exc_info.value.__cause__, setup.subprocess.TimeoutExpired)


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


def test_sidecar_yml_exercises_the_runtime_overlay_rerun_path() -> None:
    workflow = _SIDECAR_WORKFLOW.read_text(encoding="utf-8")
    rerun_step = workflow.split(
        "- name: Verify a guided setup rerun reuses locked-builder tMAVEN", 1
    )[1].split("- name:", 1)[0]
    assert workflow.count("python scripts/setup_sidecar.py") == 2
    assert rerun_step.index("set -euo pipefail") < rerun_step.index(
        "python scripts/setup_sidecar.py"
    )
    assert 'grep -F "Reusing exact installed tMAVEN commit"' in rerun_step
    assert 'grep -Fq "Installing pinned tMAVEN with the locked setuptools"' in rerun_step
    assert "rerun attempted to rebuild tMAVEN under the runtime setuptools overlay" in rerun_step


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
    state_check_at = output.index("Inspecting target build state")
    tmaven_at = output.index(setup.DEFAULT_TMAVEN_SPEC)
    test_tools_at = output.index(str(setup.TEST_TOOLS_REQUIREMENTS))
    compatibility_at = output.index(str(setup.SETUPTOOLS_REQUIREMENTS))
    assert state_check_at < tmaven_at < test_tools_at < compatibility_at, (
        "the older compatibility wheel must be installed only after tMAVEN is built"
    )
    assert "unsafe existing states fail instead of building tMAVEN" in output
    assert (
        f"--force-reinstall --no-cache-dir --no-build-isolation --no-deps "
        f"{setup.DEFAULT_TMAVEN_SPEC}" in output
    )
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

    states = iter(
        [
            {
                "setuptools_version": "82.0.1",
                "tmaven_direct_url": None,
                "tmaven_wheel_generator": None,
            },
            {
                "setuptools_version": "82.0.1",
                "tmaven_direct_url": {
                    "url": "https://github.com/GonzalezBiophysicsLab/tmaven.git",
                    "vcs_info": {
                        "vcs": "git",
                        "requested_revision": "10f4230",
                        "commit_id": "10f4230b6d13c6d2ad67b05d801696b4a40eff4a",
                    },
                },
                "tmaven_wheel_generator": "setuptools (82.0.1)",
            },
        ]
    )
    monkeypatch.setattr(setup, "inspect_sidecar_build_state", lambda _python: next(states))
    monkeypatch.setattr(setup, "_run", _record)
    rc = setup.main(["--python", str(sidecar_python), "--with-pytest", "--no-probe"])

    assert rc == 0
    assert commands == [
        setup.build_tmaven_pip_cmd(str(sidecar_python), tmaven_spec=setup.DEFAULT_TMAVEN_SPEC),
        setup.build_test_tools_pip_cmd(str(sidecar_python)),
        setup.build_setuptools_pip_cmd(str(sidecar_python)),
    ]


def test_recovered_locked_builder_force_rebuilds_an_existing_unsafe_tmaven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar_python = tmp_path / "python"
    sidecar_python.touch()
    commands: list[list[str]] = []
    states = iter(
        [
            {
                "setuptools_version": "82.0.1",
                "tmaven_direct_url": {
                    "url": "https://github.com/GonzalezBiophysicsLab/tmaven.git",
                    "vcs_info": {
                        "vcs": "git",
                        "requested_revision": "10f4230",
                        "commit_id": "10f4230b6d13c6d2ad67b05d801696b4a40eff4a",
                    },
                },
                "tmaven_wheel_generator": "setuptools (80.9.0)",
            },
            {
                "setuptools_version": "82.0.1",
                "tmaven_direct_url": {
                    "url": "https://github.com/GonzalezBiophysicsLab/tmaven.git",
                    "vcs_info": {
                        "vcs": "git",
                        "requested_revision": "10f4230",
                        "commit_id": "10f4230b6d13c6d2ad67b05d801696b4a40eff4a",
                    },
                },
                "tmaven_wheel_generator": "setuptools (82.0.1)",
            },
        ]
    )
    monkeypatch.setattr(setup, "inspect_sidecar_build_state", lambda _python: next(states))
    monkeypatch.setattr(
        setup, "_run", lambda cmd, *, dry_run: commands.append(cmd) if not dry_run else None
    )

    assert setup.main(["--python", str(sidecar_python), "--no-probe"]) == 0
    tmaven_cmd = commands[0]
    assert tmaven_cmd == setup.build_tmaven_pip_cmd(
        str(sidecar_python), tmaven_spec=setup.DEFAULT_TMAVEN_SPEC
    )
    assert "--force-reinstall" in tmaven_cmd
    assert "--no-cache-dir" in tmaven_cmd
    assert tmaven_cmd.index("--force-reinstall") < tmaven_cmd.index("--no-build-isolation")
    assert tmaven_cmd.index("--no-cache-dir") < tmaven_cmd.index("--no-build-isolation")
    assert commands[1] == setup.build_setuptools_pip_cmd(str(sidecar_python))


def test_rebuild_fails_before_runtime_overlay_when_generator_is_not_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sidecar_python = tmp_path / "python"
    sidecar_python.touch()
    commands: list[list[str]] = []
    states = iter(
        [
            {
                "setuptools_version": "82.0.1",
                "tmaven_direct_url": None,
                "tmaven_wheel_generator": None,
            },
            {
                "setuptools_version": "82.0.1",
                "tmaven_direct_url": {
                    "url": "https://github.com/GonzalezBiophysicsLab/tmaven.git",
                    "vcs_info": {
                        "vcs": "git",
                        "requested_revision": "10f4230",
                        "commit_id": "10f4230b6d13c6d2ad67b05d801696b4a40eff4a",
                    },
                },
                "tmaven_wheel_generator": "setuptools (80.9.0)",
            },
        ]
    )
    monkeypatch.setattr(setup, "inspect_sidecar_build_state", lambda _python: next(states))
    monkeypatch.setattr(
        setup, "_run", lambda cmd, *, dry_run: commands.append(cmd) if not dry_run else None
    )

    assert setup.main(["--python", str(sidecar_python), "--no-probe"]) == 1
    assert commands == [
        setup.build_tmaven_pip_cmd(str(sidecar_python), tmaven_spec=setup.DEFAULT_TMAVEN_SPEC)
    ]
    error = capsys.readouterr().err
    assert "rebuilt tMAVEN failed provenance verification" in error
    assert "setuptools (80.9.0)" in error
    assert "WHEEL Generator: setuptools (82.0.1)" in error


def test_rerun_reuses_only_the_exact_installed_tmaven_before_runtime_reinstall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sidecar_python = tmp_path / "python"
    sidecar_python.touch()
    commands: list[list[str]] = []
    monkeypatch.setattr(
        setup,
        "inspect_sidecar_build_state",
        lambda _python: {
            "setuptools_version": "80.9.0",
            "tmaven_direct_url": {
                "url": "https://github.com/GonzalezBiophysicsLab/tmaven.git",
                "vcs_info": {
                    "vcs": "git",
                    "requested_revision": "10f4230",
                    "commit_id": "10f4230b6d13c6d2ad67b05d801696b4a40eff4a",
                },
            },
            "tmaven_wheel_generator": "setuptools (82.0.1)",
        },
    )
    monkeypatch.setattr(
        setup, "_run", lambda cmd, *, dry_run: commands.append(cmd) if not dry_run else None
    )

    rc = setup.main(["--python", str(sidecar_python), "--with-pytest", "--no-probe"])

    assert rc == 0
    assert commands == [
        setup.build_test_tools_pip_cmd(str(sidecar_python)),
        setup.build_setuptools_pip_cmd(str(sidecar_python)),
    ]
    output = capsys.readouterr().out
    assert "Reusing exact installed tMAVEN commit" in output
    assert "10f4230b6d13c6d2ad67b05d801696b4a40eff4a" in output


def test_rerun_with_runtime_setuptools_and_unverified_tmaven_fails_before_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sidecar_python = tmp_path / "python"
    sidecar_python.touch()

    monkeypatch.setattr(
        setup,
        "inspect_sidecar_build_state",
        lambda _python: {
            "setuptools_version": "80.9.0",
            "tmaven_direct_url": {
                "url": "https://github.com/GonzalezBiophysicsLab/tmaven.git",
                "vcs_info": {
                    "vcs": "git",
                    "requested_revision": "different",
                    "commit_id": "0" * 40,
                },
            },
            "tmaven_wheel_generator": "setuptools (80.9.0)",
        },
    )

    def _boom(*_args, **_kwargs):
        raise AssertionError("tMAVEN must never build under runtime setuptools 80.9.0")

    monkeypatch.setattr(setup, "_run", _boom)

    rc = setup.main(["--python", str(sidecar_python), "--no-probe"])

    assert rc == 1
    error = capsys.readouterr().err
    assert "refusing to build tMAVEN" in error
    assert "setuptools 80.9.0" in error
    assert "locked build version is 82.0.1" in error
    assert "genuinely fresh sidecar environment under a new --env-name" in error


def test_matching_tmaven_commit_built_by_runtime_setuptools_is_not_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sidecar_python = tmp_path / "python"
    sidecar_python.touch()
    monkeypatch.setattr(
        setup,
        "inspect_sidecar_build_state",
        lambda _python: {
            "setuptools_version": "80.9.0",
            "tmaven_direct_url": {
                "url": "https://github.com/GonzalezBiophysicsLab/tmaven.git",
                "vcs_info": {
                    "vcs": "git",
                    "requested_revision": "10f4230",
                    "commit_id": "10f4230b6d13c6d2ad67b05d801696b4a40eff4a",
                },
            },
            "tmaven_wheel_generator": "setuptools (80.9.0)",
        },
    )

    def _boom(*_args, **_kwargs):
        raise AssertionError("a stale tMAVEN build must not be reused or rebuilt")

    monkeypatch.setattr(setup, "_run", _boom)

    assert setup.main(["--python", str(sidecar_python), "--no-probe"]) == 1
    error = capsys.readouterr().err
    assert "setuptools (80.9.0)" in error
    assert "expected setuptools (82.0.1)" in error
    assert "genuinely fresh sidecar environment under a new --env-name" in error


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


def test_skip_install_still_probes_an_already_populated_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar_python = tmp_path / "python"
    sidecar_python.touch()
    probes: list[str] = []

    def _boom(*_args, **_kwargs):
        raise AssertionError("--skip-install must not run any install command")

    def _probe(python: str) -> dict:
        probes.append(python)
        return {"ok": True, "detail": "already populated"}

    monkeypatch.setattr(setup, "_run", _boom)
    monkeypatch.setattr(setup, "run_probe", _probe)

    assert setup.main(["--python", str(sidecar_python), "--skip-install"]) == 0
    assert probes == [str(sidecar_python)]


def test_skip_install_help_names_every_skipped_pip_layer() -> None:
    help_text = " ".join(setup.build_parser().format_help().split())
    assert (
        "skip the tMAVEN, optional pytest test-tool, and compatibility-wheel installs" in help_text
    )
    assert "use only with an already-populated sidecar environment" in help_text
    assert "probe still runs unless --no-probe" in help_text


def test_skip_install_docs_require_an_already_populated_environment() -> None:
    skip_row = next(
        line
        for line in _HANDOFF_DOC.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `--skip-install` |")
    )
    assert "already-populated sidecar environment" in skip_row
    assert "probe still runs unless `--no-probe`" in skip_row
    assert "only create the env / probe" not in skip_row


def test_existing_interpreter_docs_require_source_and_builder_provenance() -> None:
    handoff = _HANDOFF_DOC.read_text(encoding="utf-8")
    normalized = " ".join(handoff.split())
    python_row = next(
        line for line in handoff.splitlines() if line.startswith("| `--python PATH` |")
    )
    assert "exact Git-commit" in python_row
    assert "locked `WHEEL`-generator provenance" in python_row
    assert "Commit identity alone does not prove which backend built" in normalized
    assert "genuinely fresh environment under a new `--env-name`" in normalized
    assert "instead of reinstalling into the same named env" in normalized
    tmaven_row = next(
        line for line in handoff.splitlines() if line.startswith("| `--tmaven-spec SPEC` |")
    )
    assert "default pinned short spec" in tmaven_row
    assert "`git+URL@<full 40/64-hex commit>` only" in tmaven_row
    assert "Tags, branches, and abbreviated custom commits are rejected" in tmaven_row

    help_text = " ".join(setup.build_parser().format_help().split())
    assert "tMAVEN builds only with the lock's setuptools" in help_text
    assert "exact installed Git provenance is required" in help_text
    assert "default pinned short spec" in help_text
    assert "full 40/64-hex commit" in help_text
    assert "tags and branches are rejected" in help_text
