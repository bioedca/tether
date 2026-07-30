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
import re
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


def test_the_install_is_two_commands_hashed_first() -> None:
    """It cannot be one command, and the order is load-bearing.

    pip's hash-checking mode is **all-or-nothing**: give a hash for any requirement and
    every requirement in the same invocation needs one — and the tMAVEN spec is a git URL
    with no hash to give. Splitting is what lets the setuptools half be hash-enforced at
    all, instead of dropping the guarantee to keep a single command.

    setuptools must land **first**, because ``--no-build-isolation`` means tMAVEN builds
    against whatever setuptools the env already has. Installed second, tMAVEN would build
    against the lock's 82.x — the release that removed ``pkg_resources``, which is the
    entire defect this pin exists to prevent (#212).
    """
    pinned, rest = setup.build_pip_cmds("py", tmaven_spec="spec", with_pytest=True)

    assert pinned == [
        "py",
        "-m",
        "pip",
        "install",
        "--require-hashes",
        "--only-binary=:all:",
        "-r",
        str(setup.SETUPTOOLS_REQUIREMENTS),
    ]
    assert "spec" not in pinned, "a git URL in the hashed command would break hash mode"
    assert rest == ["py", "-m", "pip", "install", "--no-build-isolation", "pytest", "spec"]


def test_the_tmaven_command_drops_only_pytest() -> None:
    with_pytest = setup.build_pip_cmds("py", tmaven_spec="spec", with_pytest=True)[1]
    without = setup.build_pip_cmds("py", tmaven_spec="spec", with_pytest=False)[1]
    assert "pytest" in with_pytest and "pytest" not in without
    assert without == [t for t in with_pytest if t != "pytest"]
    assert without[-1] == "spec", "the tmaven spec is always last"


# --- #218: one source of truth for the compatibility wheel -------------------


def test_the_requirement_is_pinned_and_hashed() -> None:
    """An exact version with a sha256 — the whole point of #218.

    `pip download "setuptools<81"` floated: three OS runners resolved independently, so one
    release could bundle different builds across platforms and a rebuild of the same tag
    could bundle a different one again. The tagged commit is now sufficient to determine
    what shipped, with no network-only resolution record.
    """
    version, digest = setup.setuptools_requirement()
    assert version == "80.9.0"
    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
    # Verified against PyPI on 2026-07-30 and matching the groomed decision on #218.
    assert digest == "062d34222ad13e0cc312a4c02d73f059e86a4acbfbdea8f8f76b28c99f306922"
    assert setup.setuptools_wheel_name() == f"setuptools-{version}-py3-none-any.whl"


def test_a_requirements_file_that_is_not_one_pinned_hashed_line_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail loudly. Silently accepting a floating or unhashed line restores the defect.

    Each rejected form is one that would otherwise "work": an unhashed pin still installs,
    a bound still resolves, and two requirements still succeed — while none of the three
    determines what shipped.
    """
    for content in (
        "setuptools==80.9.0\n",
        "setuptools<81 --hash=sha256:" + "a" * 64 + "\n",
        "setuptools==80.9.0 --hash=sha256:" + "a" * 63 + "\n",
        "setuptools==80.9.0 --hash=sha256:" + "a" * 64 + "\nwheel==0.45.0\n",
        "",
    ):
        path = tmp_path / "req.txt"
        path.write_text(content, encoding="utf-8")
        monkeypatch.setattr(setup, "SETUPTOOLS_REQUIREMENTS", path)
        with pytest.raises(setup.SetupError, match="pinned, sha256-hashed"):
            setup.setuptools_requirement()


def test_comments_and_continuations_do_not_change_the_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The committed file is mostly rationale, and pip's own style wraps with a backslash."""
    path = tmp_path / "req.txt"
    path.write_text(
        "# why this exists\n#\n# more prose\n"
        "setuptools==80.9.0 \\\n    --hash=sha256:" + "b" * 64 + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(setup, "SETUPTOOLS_REQUIREMENTS", path)
    assert setup.setuptools_requirement() == ("80.9.0", "b" * 64)


def test_no_consumer_restates_the_version_or_the_bound() -> None:
    """The third defect in #218: `<81` was hard-coded in two workflows and this script.

    A bump had to find all three. Now every consumer reads the file, so this asserts the
    absence of a second copy rather than the presence of a first — the failure mode is a
    consumer that quietly stops reading and hard-codes again.
    """
    _version, digest = setup.setuptools_requirement()
    # FIVE consumers, not the three #218 names and not the four the first version of this
    # test found. `packaging/README.md` carries a runnable local build recipe, and CodeRabbit
    # caught it still creating exactly the unreproducible artifact this change removes. A
    # documented command is a consumer: it is copied and run.
    consumers = {
        "scripts/setup_sidecar.py": _SCRIPT,
        "packaging.yml": _REPO_ROOT / ".github" / "workflows" / "packaging.yml",
        "release.yml": _REPO_ROOT / ".github" / "workflows" / "release.yml",
        "packaging/README.md": _REPO_ROOT / "packaging" / "README.md",
    }
    # A LITERAL requirement: `setuptools`, a comparator, then a digit. Narrowed twice while
    # writing it, and both narrowings are the point rather than concessions.
    #
    # Not the version NUMBER: `setup_sidecar.py`'s docstring explains that setuptools
    # deprecated `pkg_resources` by 80.9.0 and removed it in 82.0.0 — deprecation history a
    # reader needs, not a pin a bump has to find.
    #
    # And not every `setuptools<comparator>`: the same module contains the regex that PARSES
    # the requirements file, and an f-string that echoes the version it just parsed. Both
    # read the single source; neither restates it. Requiring a literal digit next admits
    # `setuptools=={version}` and `setuptools==(?P<version>...)` while still rejecting
    # `setuptools<81` and `setuptools==80.9.0`, which is exactly #218's third defect.
    requirement = re.compile(r"setuptools\s*(==|<=|>=|<|>|~=)\s*[0-9]")
    for name, path in consumers.items():
        text = path.read_text(encoding="utf-8")
        offending = [
            line
            for line in text.splitlines()
            if requirement.search(line) and not line.lstrip().startswith("#")
        ]
        assert not offending, f"{name} restates the pin instead of reading the file: {offending}"
        assert digest not in text, f"{name} restates the digest"


def test_both_workflows_download_with_hash_enforcement() -> None:
    """The download must be hash-checked and staged by exact name, in BOTH workflows.

    They drifted apart trivially before — the same unpinned command was pasted into each —
    so the assertion covers both rather than a representative one.
    """
    for name in ("packaging.yml", "release.yml"):
        text = (_REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "--require-hashes" in text, f"{name} downloads without hash enforcement"
        assert "-r packaging/setuptools-compatibility.txt" in text, f"{name} ignores the source"
        assert "--print-setuptools-wheel" in text, f"{name} does not stage the exact name"
        assert "--verify-setuptools-wheel" in text, f"{name} does not re-verify the bytes"
        assert "ls packaging/staging/setuptools-*.whl" not in text, (
            f"{name} still globs for the wheel, which stages whatever happens to be present"
        )


def test_the_removal_trigger_is_recorded() -> None:
    """A temporary exception with no stated end becomes permanent by inattention.

    #218 asks for the trigger explicitly, and it is a fact about tMAVEN rather than about
    setuptools: the pin goes away when tMAVEN stops importing `pkg_resources`.
    """
    text = setup.SETUPTOOLS_REQUIREMENTS.read_text(encoding="utf-8")
    assert "REMOVAL TRIGGER" in text
    assert "pkg_resources" in text
    assert "82.0.0" in text, "the removal release is what makes the pin load-bearing"


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


def test_no_workflow_run_block_contains_a_literal_escape() -> None:
    """A literal `\\n` inside a `run:` block is passed to the shell as the argument `n`.

    CodeRabbit's finding on #306, and it reached a pull request: a generated `release.yml`
    step read

        python -m pip install --require-hashes --only-binary=:all: \\n            -r ...

    so bash would have handed pip an argument `n` and the install would have failed - at
    RELEASE time, because neither packaging workflow runs on a pull request. Nothing else in
    the repository would have caught it: the YAML parses, `actionlint` sees a valid string,
    and the step is never executed by CI.
    """
    # The signature is a FAILED LINE CONTINUATION, not the two characters on their own: a
    # literal `\n` followed by the indentation of the continuation that should have been on
    # the next line. `printf '### %s\n%s\n\n'` is a correct and common use — its `\n` is
    # followed by `%` or a closing quote — and flagging it would make this test a nuisance
    # that gets deleted rather than a guard.
    #
    # What it cannot see, stated rather than implied: a literal `\n` inside a quoted string
    # that is genuinely wrong. Distinguishing those needs a shell parser; this catches the
    # generated-file defect that actually occurred.
    failed_continuation = re.compile(r"\\n\s{2,}\S")
    for name in ("packaging.yml", "release.yml"):
        text = (_REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        offending = [
            line
            for line in text.splitlines()
            if failed_continuation.search(line) and not line.lstrip().startswith("#")
        ]
        assert not offending, (
            f"{name} has a literal backslash-n where a line continuation belongs; the shell "
            f"reads it as the argument 'n': {offending}"
        )
