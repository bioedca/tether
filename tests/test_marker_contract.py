# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collection contract for the live ``@pytest.mark.sidecar`` suite (M0.5).

``sidecar.yml`` runs the live parity/round-trip suite in the *isolated* sidecar
env (``numpy<2`` / PyQt5, no base GUI/IO stack), so a repo-wide ``pytest -m
sidecar`` aborts at collection when it imports a base-only module (e.g.
``tests/test_movie_panel.py`` -> ``tifffile``). The job therefore scopes
collection with a ``tests/test_*sidecar*.py`` glob.

This module is the base-matrix guard that keeps that glob honest: every test
module that *actually* applies the ``sidecar`` marker must live in a file the
glob matches, so a new sidecar test can never silently escape the live job.
Detection is AST-based (not a text grep), so docstring/comment mentions of the
marker are ignored -- only real ``pytest.mark.sidecar`` references count.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import pytest
import yaml  # provided by the base conda-lock (a mkdocs dependency); see `_steps` below

from _sidecar_gate import REQUIRE_ENV as SIDECAR_REQUIRE_ENV
from _sidecar_gate import SIDECAR_ENV

# Must match the glob in .github/workflows/sidecar.yml's parity step
# (test_contract_glob_matches_workflow_glob asserts they stay in lockstep).
SIDECAR_FILE_GLOB = "test_*sidecar*.py"
TESTS_DIR = Path(__file__).parent
SIDECAR_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "sidecar.yml"


def _uses_sidecar_marker(source: str) -> bool:
    """True if *source* applies ``pytest.mark.sidecar`` in code (not in a string).

    Thin wrapper over the generalized :func:`_uses_marker` (defined below) for the sidecar
    marker: it matches the ``pytest.mark.sidecar`` attribute chain as real syntax (a
    ``pytestmark`` assignment or a decorator), so docstring/comment mentions are ignored.
    """
    return _uses_marker(source, "sidecar")


def test_marker_detector_distinguishes_marks_from_mentions():
    # Real applications of the marker.
    assert _uses_sidecar_marker("import pytest\npytestmark = pytest.mark.sidecar\n")
    assert _uses_sidecar_marker("@pytest.mark.sidecar\ndef test_x():\n    pass\n")
    assert _uses_sidecar_marker("pytestmark = [pytest.mark.slow, pytest.mark.sidecar]\n")
    # Mentions and lookalikes that must NOT count.
    assert not _uses_sidecar_marker('"""see ``@pytest.mark.sidecar`` for the live job"""\n')
    assert not _uses_sidecar_marker("# pytest.mark.sidecar in a comment\nx = 1\n")
    assert not _uses_sidecar_marker("import pytest\npytestmark = pytest.mark.large\n")
    assert not _uses_sidecar_marker("sidecar = 1\nmark = object()\n")


def test_sidecar_marked_modules_match_ci_glob():
    """Every sidecar-marked module is collected by the sidecar.yml glob.

    If this fails, either rename the offending file to match
    ``test_*sidecar*.py`` or widen the glob in ``.github/workflows/sidecar.yml``
    (and this contract) to keep the two in lockstep. Every file pytest collects is scanned,
    at any depth under ``tests/``: the job's glob is not recursive, so a marked module in a
    subdirectory is one it never runs (#750).
    """
    offenders = []
    detected = []
    for path in _collectable_test_files():
        if _uses_sidecar_marker(path.read_text(encoding="utf-8")):
            detected.append(path.name)
            if not _collected_by_tier_glob(path, SIDECAR_FILE_GLOB):
                offenders.append(path.relative_to(TESTS_DIR).as_posix())

    assert not offenders, (
        "sidecar-marked test modules must match the sidecar.yml glob "
        f"'{SIDECAR_FILE_GLOB}' so the live job collects them; offenders: "
        f"{offenders}"
    )
    # Anchor against a detector that silently matches nothing (vacuous pass):
    # the repo has at least the parity + driver sidecar suites.
    assert len(detected) >= 2, (
        f"expected to detect the known sidecar suites (parity + driver); detected only: {detected}"
    )


def test_contract_glob_matches_workflow_glob():
    """The contract's glob is exactly the one ``sidecar.yml`` collects with.

    Binds this guard to the real CI command so the two cannot drift: the parity
    step must invoke ``pytest -m sidecar`` against ``tests/<SIDECAR_FILE_GLOB>``
    and nothing else. (Comment mentions of other ``tests/*.py`` paths elsewhere
    in the workflow are ignored -- only the ``pytest`` command line is checked.)
    """
    workflow = SIDECAR_WORKFLOW.read_text(encoding="utf-8")
    pytest_lines = [
        ln
        for ln in workflow.splitlines()
        if "pytest -m sidecar" in ln and not ln.lstrip().startswith("#")
    ]
    assert pytest_lines, "sidecar.yml must run `pytest -m sidecar`"
    globs = re.findall(r"tests/(\S+\.py)", " ".join(pytest_lines))
    assert globs == [SIDECAR_FILE_GLOB], (
        f"sidecar.yml's `pytest -m sidecar` must collect exactly "
        f"'tests/{SIDECAR_FILE_GLOB}'; found: {globs}"
    )


def test_sidecar_workflow_uses_no_explicit_exit():
    """No step in ``sidecar.yml`` may call an explicit ``exit`` under ``bash -el {0}``.

    Every ``run:`` step in this workflow uses the login shell ``bash -el {0}``, where
    an explicit ``exit 0`` reports "Process completed with exit code 1" on the runner:
    the relevance/gate step's non-PR branch ran ``exit 0`` and failed *every* nightly
    ``schedule`` and manual ``workflow_dispatch`` run at the gate (steps 4-7 skipped, so
    the live parity fit — the drift-detection safety net — never ran), while the
    fall-through PR branch always passed. The fix removed the ``exit`` so both branches
    fall through to a single terminal ``echo "run=..." >> "$GITHUB_OUTPUT"``. This guard
    fails the moment a bare ``exit`` is reintroduced into any run script, so the footgun
    cannot silently return. (Comment lines mentioning ``exit`` are ignored.)
    """
    workflow = SIDECAR_WORKFLOW.read_text(encoding="utf-8")
    exit_lines = [
        ln
        for ln in workflow.splitlines()
        if re.match(r"\s*exit\b", ln) and not ln.lstrip().startswith("#")
    ]
    assert not exit_lines, (
        "sidecar.yml must not call an explicit `exit` in a `bash -el {0}` run step "
        f"(it reports exit 1 on the runner); offending lines: {exit_lines}"
    )


# --- The same collection contract for the M8 deep-classifier leg (ADR-0047) ---
# deep.yml runs the live torch train-smoke in the isolated `deep/` env (torch + numpy +
# scipy + h5py, no base GUI/IO stack), so a repo-wide `pytest -m deep` would abort at
# collection on an unrelated base-only import — exactly the sidecar situation. deep.yml
# therefore scopes collection with a `tests/test_*_deep.py` SUFFIX glob (not a `*deep*`
# substring glob, which would also sweep in the M7 Deep-LASI suite test_deeplasi*.py, the
# substrate tests test_*_deep_dataset.py, and test_deep_lock.py — none deep-marked). This guard
# keeps that glob honest so a new deep-marked test can never silently escape the live job.
DEEP_FILE_GLOB = "test_*_deep.py"
DEEP_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deep.yml"


def _mark_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """The names bound to the ``pytest`` module and to ``pytest.mark`` in *tree*.

    ``import pytest as pt``, ``from pytest import mark`` (or ``as m``) and ``m = pytest.mark``
    are all real ways to apply a marker, and a detector that knew only ``pytest.mark.<x>`` let
    ``@mark.sidecar`` escape both the contract and the job (#750, finding DCS-F3).
    """
    modules, marks = {"pytest"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.asname for a in node.names if a.name == "pytest" and a.asname}
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest" and not node.level:
            marks |= {a.asname or a.name for a in node.names if a.name == "mark"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_mark_namespace(node.value, modules, set()):
            marks |= {target.id for target in node.targets if isinstance(target, ast.Name)}
    return modules, marks


def _is_mark_namespace(node: ast.AST, modules: set[str], marks: set[str]) -> bool:
    """True if *node* is ``<pytest alias>.mark`` or a name bound to it."""
    if isinstance(node, ast.Name):
        return node.id in marks
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "mark"
        and isinstance(node.value, ast.Name)
        and node.value.id in modules
    )


def _applies_marker(node: ast.AST, marker: str, modules: set[str], marks: set[str]) -> bool:
    """True if the expression *node* contains ``<mark namespace>.<marker>`` anywhere."""
    return any(
        isinstance(sub, ast.Attribute)
        and sub.attr == marker
        and _is_mark_namespace(sub.value, modules, marks)
        for sub in ast.walk(node)
    )


def _uses_marker(source: str, marker: str) -> bool:
    """True if *source* applies ``pytest.mark.<marker>`` in code (not in a string/comment).

    The AST generalization of :func:`_uses_sidecar_marker`: matches the ``pytest.mark.<marker>``
    attribute chain as real syntax (a ``pytestmark`` assignment or a decorator), so docstring or
    comment mentions are ignored -- under any import spelling :func:`_mark_aliases` resolves.
    """
    tree = ast.parse(source)
    modules, marks = _mark_aliases(tree)
    return _applies_marker(tree, marker, modules, marks)


def test_deep_marker_detector_matches_the_sidecar_detector() -> None:
    # The generalized detector agrees with the hand-written sidecar one on sidecar sources.
    assert _uses_marker("import pytest\npytestmark = pytest.mark.deep\n", "deep")
    assert _uses_marker("@pytest.mark.deep\ndef test_x():\n    pass\n", "deep")
    assert not _uses_marker('"""see ``@pytest.mark.deep`` for the live job"""\n', "deep")
    assert not _uses_marker("import pytest\npytestmark = pytest.mark.sidecar\n", "deep")


def test_the_marker_detector_resolves_every_import_spelling() -> None:
    """``@mark.sidecar`` after ``from pytest import mark`` is a sidecar test too (DCS-F3 (c))."""
    assert _uses_marker("from pytest import mark\n@mark.sidecar\ndef test_x(): pass\n", "sidecar")
    assert _uses_marker("from pytest import mark as m\npytestmark = m.sidecar\n", "sidecar")
    assert _uses_marker("import pytest as pt\npytestmark = pt.mark.deep\n", "deep")
    assert _uses_marker("import pytest\nm = pytest.mark\npytestmark = m.deep\n", "deep")
    # A local name that merely happens to be called `mark` is not pytest's.
    assert not _uses_marker("mark = object()\nx = mark.sidecar\n", "sidecar")
    assert not _uses_marker("from pytest import mark\npytestmark = mark.large\n", "sidecar")


def test_deep_marked_modules_match_ci_glob() -> None:
    """Every ``@pytest.mark.deep`` module is collected by the deep.yml glob.

    If this fails, either rename the offending file to match ``test_*_deep.py`` or widen the glob
    in ``.github/workflows/deep.yml`` (and this contract) to keep the two in lockstep.
    """
    offenders = []
    detected = []
    for path in _collectable_test_files():
        if _uses_marker(path.read_text(encoding="utf-8"), "deep"):
            detected.append(path.name)
            if not _collected_by_tier_glob(path, DEEP_FILE_GLOB):
                offenders.append(path.relative_to(TESTS_DIR).as_posix())

    assert not offenders, (
        "deep-marked test modules must match the deep.yml glob "
        f"'{DEEP_FILE_GLOB}' so the live job collects them; offenders: {offenders}"
    )
    # Anchor against a detector that silently matches nothing: at least the train-smoke exists.
    assert detected, "expected to detect the deep train-smoke suite; detected none"


def test_deep_contract_glob_matches_workflow_glob() -> None:
    """The contract's glob is exactly the one ``deep.yml`` collects with (`pytest -m deep`)."""
    workflow = DEEP_WORKFLOW.read_text(encoding="utf-8")
    pytest_lines = [
        ln
        for ln in workflow.splitlines()
        if "pytest -m deep" in ln and not ln.lstrip().startswith("#")
    ]
    assert pytest_lines, "deep.yml must run `pytest -m deep`"
    globs = re.findall(r"tests/(\S+\.py)", " ".join(pytest_lines))
    assert globs == [DEEP_FILE_GLOB], (
        f"deep.yml's `pytest -m deep` must collect exactly 'tests/{DEEP_FILE_GLOB}'; found: {globs}"
    )


# --- What the tier jobs really run (#750) ---------------------------------------------------
# The glob contracts above bind a FILE PATTERN, and nothing bound the command around it. The
# test audit of 2026-11-11 (#695) kept every test green while the required `sidecar / parity`
# job lost its pytest line to a comment, gained `|| true`, `-k`, `--ignore-glob` or
# `--collect-only`, had its step set to `if: false`, or lost the interpreter export (then all
# ten live tests skip and pytest exits 0); the required 3-OS matrix, the deep smoke and the
# large tier were open the same way, and so were the path filters and schedules that decide
# when the tier jobs run at all. Everything below reads the PARSED workflow (`_job_steps`,
# `_is_live`, defined further down) and pins each command, so such an edit fails here first.
REPO_ROOT = TESTS_DIR.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOWS_DIR / "ci.yml"
LARGE_WORKFLOW = WORKFLOWS_DIR / "large-fixtures.yml"
LARGE_FIXTURES_DIR = TESTS_DIR / "fixtures" / "large"
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: The three platforms of the required matrix (`docs/validation.md`, "How to read the tiers").
REQUIRED_MATRIX_OS = ["ubuntu-latest", "macos-latest", "windows-latest"]
#: The runtime gate the live sidecar steps share; on a pull request it is the path filter.
SIDECAR_STEP_GATE = "steps.relevance.outputs.run == 'true'"
#: Tiers the required matrix may exclude although no workflow runs them -- a tier that runs
#: outside CI on purpose -- each mapped to the reason.
TIERS_WITH_NO_CI_JOB: dict[str, str] = {
    "workstation": (
        "needs private laboratory data; runs only from "
        "scripts/workstation/run_private_checks.py (ADR-0071)"
    ),
}
#: Files that match a tier glob but hold required-matrix tests on purpose: the sidecar setup
#: script's own tests, which `-m sidecar` deselects in the sidecar job.
TIER_GLOB_EXEMPT: dict[str, frozenset[str]] = {
    "sidecar": frozenset({"test_setup_sidecar.py"}),
    "deep": frozenset(),
}

#: A pytest invocation on one shell line, bare or through `python -m`.
_PYTEST_INVOCATION = re.compile(r"(?:^|[\s;&|(])(?:python3?\s+-m\s+)?pytest(?=\s|$)")
#: The one option a pinned pytest command may carry past its pin: a JUnit report to one path
#: (letters, digits and ``$_./-`` only, so no second command can hide in it). It changes neither
#: the tests that run nor the exit status; the skip budget of #726 reads it.
_JUNIT_REPORT = r'(?: --junitxml=(?:"[\w$./-]+"|[\w$./-]+))?'


def _runs_exactly(line: str, command: str) -> bool:
    """True if *line* is *command*, or *command* and a JUnit report, and nothing else."""
    return re.fullmatch(re.escape(command) + _JUNIT_REPORT, line) is not None


def _pytest_ini() -> dict:
    """``[tool.pytest.ini_options]`` from ``pyproject.toml``."""
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["tool"]["pytest"]["ini_options"]


def _registered_markers() -> set[str]:
    """The marker names ``pyproject.toml`` registers (the part of each entry before ``:``)."""
    return {entry.split(":", 1)[0].strip() for entry in _pytest_ini()["markers"]}


def _collectable_test_files() -> list[Path]:
    """Every file pytest collects under ``tests/``: its ``python_files`` patterns, at any depth.

    ``testpaths = ["tests"]`` recurses, so a module in a subdirectory is collected by a bare
    ``pytest``; the tier jobs' globs are not recursive and never see it (DCS-F3 (d)).
    """
    patterns = _pytest_ini().get("python_files", ["test_*.py", "*_test.py"])
    if isinstance(patterns, str):
        patterns = patterns.split()
    return sorted({path for pattern in patterns for path in TESTS_DIR.rglob(pattern)})


def _collected_by_tier_glob(path: Path, glob: str) -> bool:
    """True if the tier job's ``tests/<glob>`` (a shell glob, not recursive) collects *path*."""
    return path.parent == TESTS_DIR and fnmatch.fnmatch(path.name, glob)


def _command_lines(script: str) -> list[str]:
    """The lines a ``run:`` script executes: no blanks, no comments, continuations joined."""
    joined = re.sub(r"\\\n", " ", script)
    return [
        line.strip()
        for line in joined.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _pytest_command_lines(script: str) -> list[str]:
    return [line for line in _command_lines(script) if _PYTEST_INVOCATION.search(line)]


def _parsed_job(path: Path, job_id: str) -> dict:
    job = (yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs") or {}).get(job_id)
    assert isinstance(job, dict), f"{path.name} must keep a `{job_id}` job"
    return job


def _assert_live_and_failing(path: Path, label: str, block: dict) -> None:
    """A job or step that can neither be switched off by a constant nor swallow a failure."""
    assert _is_live(block), f"{path.name}: {label} is switched off by `if: {block.get('if')}`"
    assert block.get("continue-on-error") in (None, False), (
        f"{path.name}: {label} sets `continue-on-error`, so its failure would not fail the job"
    )


def _the_pytest_step(path: Path, job_id: str) -> dict:
    """The ONE live step of *job_id* that runs pytest, which may not swallow a failure."""
    _assert_live_and_failing(path, f"job `{job_id}`", _parsed_job(path, job_id))
    steps = [
        step
        for step in _job_steps(path.read_text(encoding="utf-8"), job_id)
        if isinstance(step.get("run"), str) and _pytest_command_lines(step["run"])
    ]
    assert len(steps) == 1, (
        f"{path.name}: job `{job_id}` must run pytest in exactly one step, on a line that is not "
        f"a comment; found {len(steps)}"
    )
    _assert_live_and_failing(path, f"step {steps[0].get('name')!r}", steps[0])
    return steps[0]


def _marker_expressions(command: str) -> list[str]:
    """The ``-m`` arguments of one pytest command line (``python -m pytest`` is not one)."""
    argv = shlex.split(command)
    return [
        argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "-m" and argv[i + 1] != "pytest"
    ]


def _markers_other_jobs_select() -> set[str]:
    """Each registered marker that a live pytest command OUTSIDE the required matrix selects.

    A command selects a tier with ``-m <marker>`` (``-m sidecar``, ``-m deep``, ``-m large``),
    which the pins of those commands keep exact. A command with no ``-m`` runs named files, not
    a tier, so it counts for none.
    """
    registered = _registered_markers()
    selected: set[str] = set()
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        jobs = yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs") or {}
        for job_id, job in jobs.items():
            if (path, job_id) == (CI_WORKFLOW, "test") or not _is_live(job):
                continue
            for step in job.get("steps") or []:
                if not isinstance(step, dict) or not _is_live(step):
                    continue
                for command in _pytest_command_lines(step.get("run") or ""):
                    selected |= set(_marker_expressions(command)) & registered
    return selected


def test_the_marker_argument_reader_skips_python_dash_m() -> None:
    assert _marker_expressions('pytest -v -m "not large and not deep" tests') == [
        "not large and not deep"
    ]
    assert _marker_expressions("python -m pytest -v tests/test_x.py -k y") == []
    assert _marker_expressions("python -m pytest -m sidecar -q") == ["sidecar"]


def test_the_pytest_step_finder_skips_comments_and_reads_continuations() -> None:
    script = "# pytest -m sidecar tests/x.py\necho hi\npytest -m deep \\\n  tests/y.py -q\n"
    assert _pytest_command_lines(script) == ["pytest -m deep    tests/y.py -q"]
    assert _pytest_command_lines('echo "pytest is mentioned"') == []
    assert _pytest_command_lines("python -m pytest -q") == ["python -m pytest -q"]


def test_a_pinned_command_may_add_only_a_junit_report() -> None:
    command = "pytest -m deep tests/test_*_deep.py -q"
    assert _runs_exactly(command, command)
    assert _runs_exactly(f'{command} --junitxml="$RUNNER_TEMP/pytest-junit.xml"', command)
    assert _runs_exactly(f"{command} --junitxml=report.xml", command)
    for tail in (" -k x", " --junitxml=a.xml -k x", " --junitxml=a.xml||true", " || true"):
        assert not _runs_exactly(command + tail, command), tail


def test_the_sidecar_parity_step_runs_the_whole_live_suite() -> None:
    """The required ``sidecar / parity`` step is live, unnarrowed and cannot pass with no fit.

    DCS-F1. Its script is exactly two commands: export the interpreter, then
    ``pytest -m sidecar tests/<glob> -q`` with no other option (a JUnit report aside) and nothing
    after it, so the step's exit status is pytest's. The step sets ``TETHER_REQUIRE_SIDECAR=1``,
    under which a missing interpreter fails the live tests instead of skipping them
    (``tests/_sidecar_gate.py``; the next test proves that half).
    """
    step = _the_pytest_step(SIDECAR_WORKFLOW, "parity")
    assert step.get("if") == SIDECAR_STEP_GATE, (
        f"the parity step must run whenever the relevance gate says so: if: {step.get('if')!r}"
    )
    assert (step.get("env") or {}).get(SIDECAR_REQUIRE_ENV) == "1", (
        f"the parity step must set {SIDECAR_REQUIRE_ENV}=1, or a lost interpreter skips every "
        "live test and the required check passes with zero fits"
    )
    lines = _command_lines(step["run"])
    assert len(lines) == 2 and lines[0].startswith(f"export {SIDECAR_ENV}="), (
        f"the parity script must export {SIDECAR_ENV} and then run pytest, and nothing else: "
        f"{lines}"
    )
    assert _runs_exactly(lines[1], f"pytest -m sidecar tests/{SIDECAR_FILE_GLOB} -q"), (
        "the parity step must run the whole sidecar tier, unnarrowed and with its exit status "
        f"intact: {lines[1]!r}"
    )


def test_the_deep_smoke_step_runs_the_whole_deep_suite() -> None:
    """``deep / smoke`` runs ``pytest -m deep tests/<glob> -q`` and nothing else (DCS-F1)."""
    step = _the_pytest_step(DEEP_WORKFLOW, "smoke")
    lines = _command_lines(step["run"])
    command = f"pytest -m deep tests/{DEEP_FILE_GLOB} -q"
    assert len(lines) == 1 and _runs_exactly(lines[0], command), step["run"]


def _sidecar_tier_files() -> list[Path]:
    return sorted(TESTS_DIR.glob(SIDECAR_FILE_GLOB))


def _run_sidecar_tier(**env_overrides: str) -> tuple[int, dict[str, int], str]:
    """Run the sidecar job's own pytest command here, with no sidecar interpreter."""
    env = {k: v for k, v in os.environ.items() if k not in (SIDECAR_ENV, SIDECAR_REQUIRE_ENV)}
    env.update(env_overrides)
    files = [str(path.relative_to(REPO_ROOT)) for path in _sidecar_tier_files()]
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q", "-m", "sidecar"]
        + files,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    counts = {kind: int(n) for n, kind in re.findall(r"(\d+) (\w+)", summary)}
    return proc.returncode, counts, proc.stdout + proc.stderr


def test_the_sidecar_tests_fail_rather_than_skip_where_the_job_requires_them() -> None:
    """With ``TETHER_REQUIRE_SIDECAR=1`` and no interpreter, every live test FAILS (DCS-F1).

    Without the variable the same command skips them all and exits 0 -- which is what the
    required job reported, green, when the interpreter export was deleted (mutant 5f). Here the
    job's own command runs with the variable set and no interpreter: every selected test must
    fail, each on the gate's message, and none may skip, pass or error.
    """
    code, counts, output = _run_sidecar_tier(**{SIDECAR_REQUIRE_ENV: "1"})
    assert code == 1 and set(counts) <= {"failed", "deselected"}, output
    live_modules = [
        path for path in _sidecar_tier_files() if _uses_marker(path.read_text("utf-8"), "sidecar")
    ]
    assert counts.get("failed", 0) >= len(live_modules) >= 2, output  # at least one per module
    message = f"{SIDECAR_REQUIRE_ENV}=1 but {SIDECAR_ENV} is unset or empty"
    assert output.count(message) >= counts["failed"], output


def test_the_required_matrix_runs_every_test_but_the_tier_markers() -> None:
    """The one command that gates every merge, pinned (DCS-F2).

    On each platform the ``test`` job runs ``pytest -v -m "not A and not B ..."`` over the whole
    tree -- no path argument, no option but a JUnit report, nothing after it -- and the
    markers it excludes are
    exactly the tiers another workflow selects with ``-m``. So a new exclusion (``and not gui``)
    or a typo (``not largge``, which excludes nothing) fails here, and so does a tier that the
    matrix stops excluding.
    """
    job = _parsed_job(CI_WORKFLOW, "test")
    assert (job.get("strategy") or {}).get("matrix", {}).get("os") == REQUIRED_MATRIX_OS, job
    step = _the_pytest_step(CI_WORKFLOW, "test")
    lines = _command_lines(step["run"])
    linux = r'xvfb-run -a pytest -v -m "([^"]+)"(' + _JUNIT_REPORT + ")"
    match = re.fullmatch(linux, lines[1]) if len(lines) > 1 else None
    assert match, f"the matrix step must keep its Linux/other two-line form: {lines}"
    expression, report = match.groups()
    assert lines == [
        'if [ "${RUNNER_OS}" = "Linux" ]; then',
        f'xvfb-run -a pytest -v -m "{expression}"{report}',
        "else",
        f'pytest -v -m "{expression}"{report}',
        "fi",
    ], f"the matrix script must be exactly the two pytest commands and nothing else: {lines}"
    terms = expression.split(" and ")
    assert all(re.fullmatch(r"not [\w-]+", term) for term in terms), (
        f"the matrix expression must be a plain list of `not <tier>` terms: {expression!r}"
    )
    excluded = {term.removeprefix("not ") for term in terms}
    homes = _markers_other_jobs_select() | set(TIERS_WITH_NO_CI_JOB)
    assert excluded <= _registered_markers(), (
        f"the matrix excludes markers pyproject.toml does not register (a typo excludes "
        f"nothing): {sorted(excluded - _registered_markers())}"
    )
    assert excluded == homes, (
        f"the matrix must exclude exactly the tiers another job runs; excluded and run nowhere: "
        f"{sorted(excluded - homes)}; run elsewhere but not excluded: {sorted(homes - excluded)}"
    )


def test_marker_typos_fail_collection() -> None:
    """``--strict-markers`` stays on, so ``@pytest.mark.sidcar`` errors (DCS-F3).

    Without it a misspelled tier marker is an ordinary unregistered mark: the test runs in the
    required matrix and ``-m sidecar`` deselects it. The contract cannot see a typo; pytest can.
    """
    assert "--strict-markers" in shlex.split(_pytest_ini()["addopts"])
    assert {"large", "sidecar", "deep", "gui"} <= _registered_markers()


def _unmarked_tests(source: str, marker: str) -> list[str]:
    """The tests in *source* that do not carry ``<marker>``, by name (module, class or own)."""
    tree = ast.parse(source)
    modules, marks = _mark_aliases(tree)

    def carries(nodes: list[ast.expr]) -> bool:
        return any(_applies_marker(node, marker, modules, marks) for node in nodes)

    def pytestmark(body: list[ast.stmt]) -> list[ast.expr]:
        values = []
        for stmt in body:
            if isinstance(stmt, ast.Assign):
                targets, value = stmt.targets, stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                targets, value = [stmt.target], stmt.value
            else:
                continue
            if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
                values.append(value)
        return values

    if carries(pytestmark(tree.body)):
        return []
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    missing = []
    for stmt in tree.body:
        if isinstance(stmt, functions) and stmt.name.startswith("test"):
            if not carries(stmt.decorator_list):
                missing.append(stmt.name)
        elif isinstance(stmt, ast.ClassDef) and stmt.name.startswith("Test"):
            if carries(stmt.decorator_list) or carries(pytestmark(stmt.body)):
                continue
            missing += [
                f"{stmt.name}.{method.name}"
                for method in stmt.body
                if isinstance(method, functions)
                and method.name.startswith("test")
                and not carries(method.decorator_list)
            ]
    return missing


def test_the_unmarked_test_finder_reads_module_class_and_own_marks() -> None:
    marked = (
        "import pytest\npytestmark = [pytest.mark.slow, pytest.mark.deep]\ndef test_a(): pass\n"
    )
    assert _unmarked_tests(marked, "deep") == []
    source = (
        "import pytest\n"
        "torch = pytest.importorskip('torch')\n"
        "@pytest.mark.deep\ndef test_a(): pass\n"
        "def test_b(): pass\n"
        "@pytest.mark.deep\nclass TestC:\n    def test_c(self): pass\n"
        "class TestD:\n    def test_d(self): pass\n"
        "def helper(): pass\n"
    )
    assert _unmarked_tests(source, "deep") == ["test_b", "TestD.test_d"]


@pytest.mark.parametrize(
    ("marker", "glob"), [("sidecar", SIDECAR_FILE_GLOB), ("deep", DEEP_FILE_GLOB)]
)
def test_every_test_in_a_tier_file_carries_the_tier_marker(marker: str, glob: str) -> None:
    """The reverse of the glob contract: what the tier job collects, its ``-m`` keeps (DCS-F3).

    A test in ``tests/<glob>`` without the marker is deselected by the tier job's ``-m <marker>``
    and, if it then skips in the required matrix for want of the tier's interpreter or torch, it
    runs nowhere. :data:`TIER_GLOB_EXEMPT` names the files that hold required-matrix tests on
    purpose.
    """
    files = sorted(TESTS_DIR.glob(glob))
    exempt = TIER_GLOB_EXEMPT[marker]
    assert exempt <= {path.name for path in files}, f"stale exemption: {sorted(exempt)}"
    offenders = {
        path.name: missing
        for path in files
        if path.name not in exempt
        and (missing := _unmarked_tests(path.read_text(encoding="utf-8"), marker))
    }
    assert not offenders, (
        f"tests in files the `{marker}` job collects must carry `pytest.mark.{marker}` (module "
        f"`pytestmark` or their own decorator), or the job deselects them: {offenders}"
    )


def _sidecar_relevance_pattern() -> re.Pattern[str]:
    """The ``grep -E`` pattern that decides whether a pull request runs the live fits."""
    steps = [
        step
        for step in _job_steps(SIDECAR_WORKFLOW.read_text(encoding="utf-8"), "parity")
        if step.get("id") == "relevance"
    ]
    assert len(steps) == 1, "the parity job must keep exactly one `relevance` step"
    _assert_live_and_failing(SIDECAR_WORKFLOW, "step `relevance`", steps[0])
    script = steps[0]["run"]
    patterns = re.findall(r"^\s*pattern='([^']+)'\s*$", script, re.M)
    assert len(patterns) == 1, f"the relevance step must set one `pattern='...'`: {patterns}"
    assert any('grep -qE "$pattern"' in line for line in _command_lines(script)), (
        "the relevance step must test the changed files against that pattern"
    )
    return re.compile(patterns[0])


def _local_imports(tree: ast.AST) -> set[str]:
    """The top-level module names *tree* imports (``import a.b`` gives ``a``)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


def _sidecar_inputs() -> set[str]:
    """Repository paths whose change can change a live sidecar result, as POSIX strings."""

    def files_under(directory: str) -> set[Path]:
        return {path for path in (REPO_ROOT / directory).rglob("*") if path.is_file()}

    inputs = files_under("src/tether/idealize") | files_under("sidecar")
    inputs |= files_under(".github/actions/setup-env")
    inputs |= {
        REPO_ROOT / "schema" / "parity_tolerance.json",
        REPO_ROOT / "scripts" / "setup_sidecar.py",
        SIDECAR_WORKFLOW,
    }
    inputs |= set(_sidecar_tier_files())
    fixtures = [path for path in (TESTS_DIR / "fixtures").rglob("*") if path.is_file()]
    named: set[Path] = set()
    for module in _sidecar_tier_files():
        source = module.read_text(encoding="utf-8")
        if not _uses_marker(source, "sidecar"):
            continue
        tree = ast.parse(source)
        strings = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        named |= {path for path in fixtures if path.name in strings}
        inputs |= {
            TESTS_DIR / f"{name}.py"
            for name in _local_imports(tree)
            if (TESTS_DIR / f"{name}.py").is_file()
        }
    # Anchor against a scan that finds nothing: the parity tests read the 4-molecule SMD.
    assert TESTS_DIR / "fixtures" / "smd_4mol.hdf5" in named, sorted(named)
    inputs |= named
    return {path.relative_to(REPO_ROOT).as_posix() for path in inputs}


def test_the_sidecar_path_filter_covers_every_input_of_the_live_fits() -> None:
    """A pull request that changes what the fits read runs them (DCS-F5).

    The pattern must match the idealization package, the sidecar lock, the setup script and
    action, the frozen tolerance, every sidecar test module, the helper modules they import and
    every committed fixture they name. Otherwise such a pull request gets the fast "not
    applicable" pass on the required check, and the fits first run after the merge.
    """
    pattern = _sidecar_relevance_pattern()
    unmatched = sorted(path for path in _sidecar_inputs() if not pattern.match(path))
    assert not unmatched, (
        f"sidecar.yml's relevance pattern misses inputs of the live fits: {unmatched}"
    )
    # Anchor against a pattern that matches everything: the gate must still gate.
    assert not pattern.match("README.md")


def _github_path_filter(glob: str) -> re.Pattern[str]:
    """A ``paths:`` filter as a regex: ``**`` crosses ``/``, ``*`` and ``?`` do not."""
    assert not re.search(r"[!\[\]+]", glob), f"path filter syntax not handled here: {glob!r}"
    parts = re.split(r"(\*\*|\*|\?)", glob)
    wildcards = {"**": ".*", "*": "[^/]*", "?": "[^/]"}
    return re.compile("".join(wildcards.get(part, re.escape(part)) for part in parts) + r"\Z")


def test_the_path_filter_translation_follows_github() -> None:
    assert _github_path_filter("src/tether/ml/**").match("src/tether/ml/deep/model.py")
    assert _github_path_filter("tests/test_*_deep.py").match("tests/test_x_deep.py")
    assert not _github_path_filter("tests/test_*_deep.py").match("tests/sub/test_x_deep.py")
    assert not _github_path_filter("deep/**").match("deeper/x")


def _workflow_triggers(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    triggers = document.get("on", document.get(True))  # YAML 1.1 reads a bare `on:` as True
    return triggers if isinstance(triggers, dict) else {}


def _deep_inputs() -> set[str]:
    """Repository paths whose change can change a deep train-smoke result."""
    inputs = {path for path in (REPO_ROOT / "deep").rglob("*") if path.is_file()}
    inputs |= {p for p in (REPO_ROOT / ".github/actions/setup-env").rglob("*") if p.is_file()}
    inputs |= {DEEP_WORKFLOW, PYPROJECT}
    modules = sorted(TESTS_DIR.glob(DEEP_FILE_GLOB))
    inputs |= set(modules)
    for module in modules:
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                if name.split(".")[0] != "tether":
                    continue
                base = REPO_ROOT / "src" / Path(*name.split("."))
                found = [p for p in (base.with_suffix(".py"), base / "__init__.py") if p.is_file()]
                assert found, f"{module.name} imports {name}, which is no file under src/"
                inputs |= set(found)
    return {path.relative_to(REPO_ROOT).as_posix() for path in inputs}


def test_the_deep_path_filter_covers_what_the_deep_tests_import() -> None:
    """``deep.yml`` runs on a pull request that changes the code its tests import (DCS-F5)."""
    paths = (_workflow_triggers(DEEP_WORKFLOW).get("pull_request") or {}).get("paths")
    assert isinstance(paths, list) and paths, "deep.yml must keep its pull_request paths filter"
    filters = [_github_path_filter(glob) for glob in paths]
    unmatched = sorted(p for p in _deep_inputs() if not any(f.match(p) for f in filters))
    assert not unmatched, f"deep.yml's paths filter misses inputs of the deep smoke: {unmatched}"


@pytest.mark.parametrize(
    "workflow", [SIDECAR_WORKFLOW, DEEP_WORKFLOW, LARGE_WORKFLOW], ids=lambda path: path.name
)
def test_the_tier_jobs_keep_their_schedule(workflow: Path) -> None:
    """The nightly or weekly run is each tier's drift net on ``main`` (DCS-F5, DCS-F6)."""
    schedule = _workflow_triggers(workflow).get("schedule")
    crons = [str(e["cron"]) for e in schedule or [] if isinstance(e, dict) and "cron" in e]
    assert crons and all(len(cron.split()) == 5 for cron in crons), (
        f"{workflow.name} must keep a `schedule` trigger with a cron entry: {schedule!r}"
    )


LFS_VERIFY_STEP = "Verify committed LFS fixtures are materialized"


def _large_verify_step() -> dict:
    steps = [
        step
        for step in _job_steps(LARGE_WORKFLOW.read_text(encoding="utf-8"), "large")
        if step.get("name") == LFS_VERIFY_STEP
    ]
    assert len(steps) == 1, f"large-fixtures.yml must keep one step named {LFS_VERIFY_STEP!r}"
    _assert_live_and_failing(LARGE_WORKFLOW, f"step {LFS_VERIFY_STEP!r}", steps[0])
    return steps[0]


def _large_verify_script() -> str:
    """The Python the verify step feeds to ``python - <<'PY'``."""
    match = re.search(r"^python - <<'PY'\n(.*?)^PY$", _large_verify_step()["run"], re.M | re.S)
    assert match, "the verify step must run its check as `python - <<'PY' ... PY`"
    return match.group(1)


def _lfs_tracked_large_fixtures() -> set[str]:
    rules = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert any(
        line.split()[:1] == ["tests/fixtures/large/**"] and "filter=lfs" in line.split()
        for line in rules
    ), ".gitattributes must keep tests/fixtures/large/** under Git LFS"
    return {
        path.relative_to(REPO_ROOT).as_posix()
        for path in LARGE_FIXTURES_DIR.rglob("*")
        if path.is_file()
    }


def test_the_large_tier_verifies_every_lfs_fixture_before_it_runs() -> None:
    """The weekly job fails on an unmaterialized fixture instead of skipping its tests (DCS-F6).

    The verify list must be every file under ``tests/fixtures/large/``: a file left off it
    (``kinsoft_sim.hdf5`` was, so the four oracle (c) tests skipped there) turns a failed LFS
    pull into a green run. Then the job runs ``pytest -v -m large`` and nothing else (a JUnit
    report aside).
    """
    required = set(re.findall(r'Path\("([^"]+)"\)', _large_verify_script()))
    tracked = _lfs_tracked_large_fixtures()
    assert required == tracked, (
        f"the verify list must be every LFS fixture; not verified: {sorted(tracked - required)}; "
        f"not a fixture: {sorted(required - tracked)}"
    )
    step = _the_pytest_step(LARGE_WORKFLOW, "large")
    lines = _command_lines(step["run"])
    assert len(lines) == 1 and _runs_exactly(lines[0], "xvfb-run -a pytest -v -m large"), lines
    order = [s.get("name") for s in _job_steps(LARGE_WORKFLOW.read_text(encoding="utf-8"), "large")]
    assert order.index(LFS_VERIFY_STEP) < order.index(step.get("name")), order


def test_the_large_verify_script_fails_on_a_pointer_or_a_missing_file(tmp_path: Path) -> None:
    """The verify step's own Python, run here: real bytes pass, a pointer or a gap fails."""
    script = _large_verify_script()
    required = sorted(set(re.findall(r'Path\("([^"]+)"\)', script)))

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        )

    for name in required:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"\x89HDF\r\n\x1a\n" + bytes(64))
    assert run().returncode == 0
    pointer = tmp_path / required[-1]
    named = pointer.name  # the script prints native paths, so match the name on every OS
    pointer.write_bytes(b"version https://git-lfs.github.com/spec/v1\noid sha256:00\nsize 1\n")
    result = run()
    assert result.returncode != 0 and named in result.stderr, result.stderr
    pointer.unlink()
    result = run()
    assert result.returncode != 0 and named in result.stderr, result.stderr


# --- The same contract for the non-required GPU leg (deep-gpu.yml, PR-2 / ADR-0047) ---
# deep-gpu.yml is the workflow_dispatch, self-hosted CUDA counterpart of deep.yml: it runs the
# SAME `pytest -m deep tests/test_*_deep.py` on a GPU box, so the GPU cases
# (tests/test_deep_gpu_deep.py) ride the same suffix glob. It is advisory / non-required BY
# CONSTRUCTION — dispatched manually and never on pull_request/push, so it can never report a
# gating status on a PR (§9 M8 "optional / CPU base app unaffected").
DEEP_GPU_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deep-gpu.yml"


def _on_block_child_keys(text: str) -> set[str]:
    """Immediate child keys of the top-level ``on:`` mapping (a small indent scan, no YAML dep).

    Mirrors the stdlib-only style of this module (ast/re, never a YAML import). Assumes the
    two-space block indentation the repo's workflows use; comment/blank lines are ignored.
    """
    keys: set[str] = set()
    in_on = False
    on_indent = 0
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if not in_on:
            if indent == 0 and stripped.split(":", 1)[0] == "on":
                in_on = True
                on_indent = indent
            continue
        if indent <= on_indent:  # dedented back to another top-level key: on: block ended
            break
        if indent == on_indent + 2:  # an immediate child of on:
            keys.add(stripped.split(":", 1)[0].strip())
    return keys


def test_on_block_parser_extracts_immediate_triggers() -> None:
    # Only the immediate children of on: count — nested input keys must not leak up.
    sample = (
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      push:\n"
        "        type: string\n"
        "jobs:\n"
        "  x: {}\n"
    )
    assert _on_block_child_keys(sample) == {"workflow_dispatch"}


def test_deep_gpu_contract_glob_matches_workflow_glob() -> None:
    """deep-gpu.yml collects exactly the deep suffix glob (identical to deep.yml)."""
    workflow = DEEP_GPU_WORKFLOW.read_text(encoding="utf-8")
    pytest_lines = [
        ln
        for ln in workflow.splitlines()
        if "pytest -m deep" in ln and not ln.lstrip().startswith("#")
    ]
    assert pytest_lines, "deep-gpu.yml must run `pytest -m deep`"
    globs = re.findall(r"tests/(\S+\.py)", " ".join(pytest_lines))
    assert globs == [DEEP_FILE_GLOB], (
        f"deep-gpu.yml's `pytest -m deep` must collect exactly "
        f"'tests/{DEEP_FILE_GLOB}'; found: {globs}"
    )


def test_deep_gpu_leg_is_non_required_by_construction() -> None:
    """The GPU leg is advisory: manual-dispatch only, never an auto (PR/push) trigger.

    A ``workflow_dispatch``-only workflow never reports a status on a pull request, so it
    structurally cannot be (or silently become) a required merge check — the ADR-0047
    "optional / CPU base app unaffected" invariant. Adding a ``pull_request:``/``push:``
    trigger fails this guard so the choice would have to be deliberate.
    """
    triggers = _on_block_child_keys(DEEP_GPU_WORKFLOW.read_text(encoding="utf-8"))
    assert "workflow_dispatch" in triggers, "deep-gpu.yml must be manually dispatchable"
    # pull_request / push report a PR or branch status; merge_group gates a merge queue.
    # None of these may appear — a workflow_dispatch-only leg can never be a required check.
    gating = {"pull_request", "push", "merge_group"}
    assert triggers.isdisjoint(gating), (
        "deep-gpu.yml must stay advisory (no PR/push/merge_group trigger); "
        f"on-triggers: {sorted(triggers)}"
    )


def test_deep_gpu_leg_targets_a_self_hosted_runner() -> None:
    """The GPU leg runs on a self-hosted runner — hosted runners have no CUDA GPU.

    Matches the exact ``- self-hosted`` runs-on list item, NOT the substring anywhere: the
    literal string "self-hosted" also appears in the ``runner_label`` input's description prose,
    so a substring check would stay green even if the runs-on mapping were switched to a hosted
    runner. This guard fails the moment the job stops targeting a self-hosted runner.
    """
    lines = DEEP_GPU_WORKFLOW.read_text(encoding="utf-8").splitlines()
    assert any(ln.strip() == "- self-hosted" for ln in lines), (
        "deep-gpu.yml must target a self-hosted (GPU) runner (a `- self-hosted` runs-on entry)"
    )


# --- The same contract for the non-required packaging leg (packaging.yml, M9 / ADR-0049) ---
# packaging.yml builds the constructor installer (base env + the isolated tMAVEN sidecar as an
# `extra_envs`) and runs the offline install-smoke on each of the 3 OSes. Building a full
# napari/PySide6 + sidecar installer is heavy, network-bound and per-OS, so — exactly like
# deep-gpu.yml — it is advisory / non-required BY CONSTRUCTION: workflow_dispatch only, never on
# pull_request/push, so it can never report a gating status on a PR. The §9 M9 "installers install
# clean" runtime clause is validated on THIS advisory leg, not the required matrix (ADR-0049). This
# guard keeps that shape honest and the smoke non-vacuous.
PACKAGING_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "packaging.yml"


def test_packaging_leg_is_non_required_by_construction() -> None:
    """The packaging leg is advisory: manual-dispatch only, never an auto (PR/push) trigger.

    A ``workflow_dispatch``-only workflow never reports a status on a pull request, so it
    structurally cannot be (or silently become) a required merge check — the ADR-0049 posture that
    keeps the required 3-OS matrix free of the heavy installer build. Adding a
    ``pull_request:``/``push:``/``merge_group:`` trigger fails this guard, so the choice would have
    to be deliberate.
    """
    triggers = _on_block_child_keys(PACKAGING_WORKFLOW.read_text(encoding="utf-8"))
    assert "workflow_dispatch" in triggers, "packaging.yml must be manually dispatchable"
    gating = {"pull_request", "push", "merge_group"}
    assert triggers.isdisjoint(gating), (
        "packaging.yml must stay advisory (no PR/push/merge_group trigger); "
        f"on-triggers: {sorted(triggers)}"
    )


def test_packaging_workflow_uses_no_explicit_exit() -> None:
    """No step in ``packaging.yml`` may call an explicit ``exit`` under ``bash -el {0}``.

    packaging.yml uses the same ``bash -el {0}`` login shell as sidecar.yml, where an explicit
    ``exit`` reports "Process completed with exit code 1" on the runner (the
    [[gha-bash-el-explicit-exit]] footgun). This guard fails the moment a bare ``exit`` is
    introduced into any run script. (Comment lines are ignored; the ``exit /b`` inside the batch
    ``packaging/scripts/post_install.bat`` is an installer script, not a workflow run step, so it
    is out of scope here.)
    """
    workflow = PACKAGING_WORKFLOW.read_text(encoding="utf-8")
    exit_lines = [
        ln
        for ln in workflow.splitlines()
        if re.match(r"\s*exit\b", ln) and not ln.lstrip().startswith("#")
    ]
    assert not exit_lines, (
        "packaging.yml must not call an explicit `exit` in a `bash -el {0}` run step "
        f"(it reports exit 1 on the runner); offending lines: {exit_lines}"
    )


def test_packaging_install_smoke_asserts_version_and_offline_sidecar() -> None:
    """The install-smoke asserts the launch surface AND the offline sidecar (ADR-0049/0051).

    ADR-0049's PR-1 acceptance: after an offline install, ``tether --version`` runs and the bundled
    sidecar interpreter imports tMAVEN/PyQt5. The probe sequence now lives in
    ``packaging/scripts/install_smoke.sh`` — the single source BOTH packaging.yml and release.yml
    invoke (issue #217; ``test_install_smoke_script_is_invoked_by_both_workflows`` is what binds
    the two workflows to it) — so this guard binds the SCRIPT to the contract, widened to the
    launch surface the smoke really executes and that nothing else guards: the three per-platform
    offline install modes, the PATH-shim launches, the console-less Windows GUI launcher, the
    Linux desktop entry, the ``numpy<2`` assertion and the bounded NSIS wait. Matched on
    NON-COMMENT lines only: ``_RUNNER`` and friends already live in explanatory comments, so a
    comment moved across with the code it documents would keep a whole-text check green after the
    command itself was deleted.
    """
    code = _install_smoke_code_lines()
    for needle in (
        "tether --version",
        "tether-gui --version",
        "import tmaven",
        "numpy.__version__",
        "-b -p",
        "-target CurrentUserHomeDirectory",
        "/S /D=",
        "seq 1 40",
        "Scripts/tether-gui.exe",
        "tether.desktop",
    ):
        assert needle in code, (
            f"packaging/scripts/install_smoke.sh must keep `{needle}` on a non-comment line — "
            "part of the probe set both workflows' install-smokes rely on (issue #217)"
        )


def test_packaging_install_smoke_exercises_the_pkg_resources_path() -> None:
    """The smoke must CONSTRUCT ``maven_class``, not merely import tMAVEN (issue #212).

    ``import tmaven`` passes on a sidecar whose setuptools no longer ships ``pkg_resources``
    (removed in setuptools 82.0.0; ``sidecar/conda-lock.yml`` resolves 82.0.1), because tMAVEN does
    that import inside ``maven_class.__init__`` — which is how a broken installer shipped past the
    old smoke. Two checks close it, and both need pinning or the next edit can silently drop them:

    * ``_sidecar_runner.py --probe`` really constructs ``maven_class`` (see
      ``src/tether/idealize/_sidecar_runner.py``), and is resolved from the INSTALLED app env via
      ``driver._RUNNER`` so a wheel that stopped shipping the runner also fails; and
    * an explicit ``import pkg_resources`` + ``setuptools.__version__`` bound, which names the cause
      instead of leaving an opaque probe failure.

    Asserted against ``packaging/scripts/install_smoke.sh`` — the single source both workflows
    invoke (issue #217) — on non-comment lines, since ``_RUNNER`` also lives in an explanatory
    comment the extraction carried across.
    """
    code = _install_smoke_code_lines()
    assert "--probe" in code and "_RUNNER" in code, (
        "install_smoke.sh must drive `_sidecar_runner.py --probe`, resolved from the "
        "installed app env via `tether.idealize.driver._RUNNER` — importing tMAVEN is not enough "
        "(issue #212)"
    )
    assert "import setuptools, pkg_resources" in code, (
        "install_smoke.sh must assert the bundled `setuptools<81` pin was applied to "
        "the sidecar env, so a regression names its cause (issue #212)"
    )


# --- The release pipeline (release.yml, M9 / ADR-0059) ---
# release.yml builds + publishes the installers on a signed `v*` tag. It runs ONLY on a tag
# push and manual dispatch — never on pull_request/branch-push/merge_group — so, like the
# advisory legs, it can never report a gating status on a PR (the heavy build must not sit
# in the required matrix). It also uses `bash -el {0}` in its build job, so the same
# explicit-exit footgun applies. These guards keep that shape honest.
#
# The installers are NOT OS-code-signed (ADR-0059 supersedes ADR-0050): SignPath Foundation
# declined enrollment and Apple Developer ID is out of budget, so the inert legs were removed
# rather than shipped dormant. The tag signature the `verify` job checks is a different
# mechanism and stays.
RELEASE_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def test_release_leg_never_gates_a_pr() -> None:
    """release.yml triggers only on a tag push + dispatch — never on a PR/branch/merge queue.

    A ``pull_request``/``merge_group`` trigger, or a branch-filtered ``push``, would report a
    status on a PR or branch and could (silently) become a required merge check. The heavy
    build pipeline must stay off that path (the ADR-0049/0059 posture). Adding any such
    trigger fails this guard, so the choice would have to be deliberate.
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    triggers = _on_block_child_keys(text)
    assert triggers == {"push", "workflow_dispatch"}, (
        f"release.yml must trigger only on tag `push` + `workflow_dispatch`; got {sorted(triggers)}"
    )
    # The push must be tag-filtered, never branch pushes (a branch push reports a status).
    assert re.search(r"\n  push:\n    tags:\n", text), (
        "release.yml's push trigger must be tag-filtered (`on: push: tags:`)"
    )
    assert "branches:" not in text, (
        "release.yml must not trigger on branch pushes (no `branches:` filter)"
    )


def test_release_workflow_uses_no_explicit_exit() -> None:
    """No step in ``release.yml`` may call an explicit ``exit`` under ``bash -el {0}``.

    Its ``build`` job uses the ``bash -el {0}`` login shell where an explicit ``exit``
    reports "exit code 1" on the runner (the [[gha-bash-el-explicit-exit]] footgun). Fail the
    moment a bare ``exit`` appears; failures use a falsy terminal command instead.
    """
    exit_lines = [
        ln
        for ln in RELEASE_WORKFLOW.read_text(encoding="utf-8").splitlines()
        if re.match(r"\s*exit\b", ln) and not ln.lstrip().startswith("#")
    ]
    assert not exit_lines, (
        "release.yml must not call an explicit `exit` in a `bash -el {0}` run step "
        f"(it reports exit 1 on the runner); offending lines: {exit_lines}"
    )


#: Every token that only appears in an OS-code-signing leg. ADR-0059 removed both legs, so
#: none of these may return without a superseding decision -- and a returning leg would be
#: *inert* again, which is the failure mode that is invisible in a green run.
_SIGNING_TOKENS = (
    "SIGNPATH",
    "signpath",
    "APPLE_SIGNING",
    "APPLE_CERT",
    "APPLE_NOTARY",
    "productsign",
    "notarytool",
    "stapler",
    "create-keychain",
    "UNSIGNED",
)


def test_the_release_pipeline_carries_no_code_signing_leg() -> None:
    """ADR-0059: the inert SignPath and Apple legs are removed, not shipped dormant.

    Both legs were written green-before-secrets, so neither ever executed: `v1.0.0-rc1`'s
    only annotation was the "SignPath not configured" warning. Inert CI is the specific
    thing a green run cannot tell you about, which is why the removal needs a guard rather
    than a commit message -- a re-added leg would be just as green and just as dead.

    This asserts the *shape* only. It takes no position on whether Tether should ever be
    code-signed; #244 tracks re-applying, and reversing this is an ADR, not an edit.
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    found = sorted({token for token in _SIGNING_TOKENS if token in text})
    assert not found, (
        "release.yml must carry no OS-code-signing leg (ADR-0059 removed them); found "
        f"{found}. Re-adding signing supersedes ADR-0059 and updates this guard with it."
    )


#: The action whose presence *is* ADR-0059's integrity anchor for the installers.
ATTEST_ACTION = "actions/attest-build-provenance@"


#: The job that publishes the Release, and therefore the only job whose attestation counts.
PUBLISH_JOB = "release"

#: `if:` values that disable a step unconditionally. A step gated on a runtime expression
#: (the real one is `needs.verify.outputs.publish == 'true'`) is live; one gated on a literal
#: false is dead, and dead is exactly the state ADR-0059 removed the signing legs for.
_STATICALLY_DISABLED = frozenset({"false", "${{ false }}", "${{false}}"})


def _job_steps(text: str, job_id: str) -> list[dict]:
    """The steps of ONE named job, from the parsed workflow.

    Scoping to a job matters: an `actions/attest-build-provenance` step in `build`, or in
    some future unrelated job, attests nothing about the published Release, and a guard that
    accepted one would go green while the publish path had none.
    """
    workflow = yaml.safe_load(text)
    job = (workflow.get("jobs") or {}).get(job_id)
    assert job is not None, f"the workflow must keep a `{job_id}` job"
    return [step for step in (job.get("steps") or []) if isinstance(step, dict)]


# An `if:` is a constant when GitHub can decide it before the run: `false`, but also
# `${{ false && true }}`, `!true`, `0` or `${{ fromJSON('false') }}` (#750, CodeRabbit). The
# reader below evaluates the expression language (literals, `( ) [ ] . ! < <= > >= == != && ||`
# and the pure functions) and gives every context (`github`, `steps`, `needs`, ...) and every
# status function but `always()` a value that only the run knows. `&&` and `||` can still decide
# around such a value: `false && steps.x.outputs.y` is a constant false.

#: Functions whose value depends on the run (`always()` does not: it is always true).
_RUNTIME_FUNCTIONS = frozenset({"success", "failure", "cancelled", "hashfiles"})

_JSON_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")

_EXPRESSION_TOKEN = re.compile(
    r"""\s*(?:
        (?P<string>'(?:[^']|'')*')
      | (?P<number>-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)
      | (?P<name>[A-Za-z_][A-Za-z0-9_-]*)
      | (?P<op>&&|\|\||==|!=|<=|>=|[!<>()\[\].,*])
    )""",
    re.VERBOSE,
)


class _Runtime:
    """A value that only the run knows; ``truthy`` is what is known of it anyway (None: nothing)."""

    __slots__ = ("truthy",)

    def __init__(self, truthy: bool | None = None) -> None:
        self.truthy = truthy


def _truth(value: object) -> bool | None:
    """GitHub's truthiness: ``false``, ``0``, ``-0``, ``''`` and ``null`` are falsy."""
    if isinstance(value, _Runtime):
        return value.truthy
    if value is None or value is False:
        return False
    if isinstance(value, float):
        return not (value == 0 or math.isnan(value))
    if isinstance(value, str):
        return value != ""
    return True  # true, arrays and objects


def _as_number(value: object) -> float:
    """GitHub's coercion for a comparison of two types: null 0, booleans 1/0, strings parsed."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        return float(text) if _JSON_NUMBER.fullmatch(text) else math.nan
    return math.nan  # arrays and objects


def _as_string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, str):
        return value
    return "Array" if isinstance(value, list) else "Object"


def _kind(value: object) -> str:
    if value is None or isinstance(value, (bool, float, str)):
        return type(value).__name__
    return "array" if isinstance(value, list) else "object"


def _equal(left: object, right: object) -> bool:
    """Loose equality: strings ignore case, other types meet as numbers, NaN equals nothing."""
    if _kind(left) == _kind(right):
        if isinstance(left, str) and isinstance(right, str):
            return left.casefold() == right.casefold()
        if isinstance(left, (list, dict)):
            return left is right  # arrays and objects are equal only to themselves
        return left == right
    return _as_number(left) == _as_number(right)


def _ordered(operator: str, left: object, right: object) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        a: str | float = left.casefold()
        b: str | float = right.casefold()
    else:
        a, b = _as_number(left), _as_number(right)
        if math.isnan(a) or math.isnan(b):
            return False
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[operator]


def _both(left: object, right: object) -> object:
    """``&&``: the first falsy operand, else the last; falsy for sure if either side is."""
    known = _truth(left)
    if known is False:
        return left
    if known is True:
        return right
    return _Runtime(False if _truth(right) is False else None)


def _either(left: object, right: object) -> object:
    """``||``: the first truthy operand, else the last; truthy for sure if either side is."""
    known = _truth(left)
    if known is True:
        return left
    if known is False:
        return right
    return _Runtime(True if _truth(right) is True else None)


def _from_json(value: object) -> object:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list):
        return [_from_json(item) for item in value]
    return {key: _from_json(item) for key, item in value.items()}  # type: ignore[union-attr]


def _call(name: str, args: list[object]) -> object:
    """A function of the expression language; a runtime one, or a runtime argument, is unknown."""
    if name == "always":
        return True
    if name in _RUNTIME_FUNCTIONS or any(isinstance(arg, _Runtime) for arg in args):
        return _Runtime()
    try:
        if name == "contains":
            haystack, needle = args
            if isinstance(haystack, list):
                return any(_equal(item, needle) for item in haystack)
            return _as_string(needle).casefold() in _as_string(haystack).casefold()
        if name in ("startswith", "endswith"):
            text, part = (_as_string(arg).casefold() for arg in args)
            return text.startswith(part) if name == "startswith" else text.endswith(part)
        if name == "format":
            template, *values = args
            return re.sub(
                r"\{\{|\}\}|\{(\d+)\}",
                lambda m: _as_string(values[int(m[1])]) if m[1] else m[0][0],
                _as_string(template),
            )
        if name == "join":
            items, *separator = args
            glue = _as_string(separator[0]) if separator else ","
            if isinstance(items, list):
                return glue.join(_as_string(item) for item in items)
            return _as_string(items)
        if name == "tojson":
            (value,) = args
            return json.dumps(value)
        if name == "fromjson":
            (value,) = args
            return _from_json(json.loads(_as_string(value)))
    except (ValueError, IndexError, TypeError):
        pass
    return _Runtime()  # a function or arguments GitHub would reject: assume nothing


def _member(value: object, key: object) -> object:
    if isinstance(value, _Runtime) or isinstance(key, _Runtime):
        return _Runtime()
    if isinstance(value, dict):
        wanted = _as_string(key).casefold()
        return next((item for name, item in value.items() if name.casefold() == wanted), None)
    if isinstance(value, list) and isinstance(key, float) and key.is_integer():
        return value[int(key)] if 0 <= key < len(value) else None
    return None


class _Expression:
    """A recursive-descent reader of one GitHub Actions expression (lowest precedence first)."""

    def __init__(self, text: str) -> None:
        self.tokens: list[tuple[str, str]] = []
        position, text = 0, text.rstrip()
        while position < len(text):
            match = _EXPRESSION_TOKEN.match(text, position)
            if match is None or match.lastgroup is None:
                raise ValueError(f"cannot read {text[position:]!r}")
            self.tokens.append((match.lastgroup, match[match.lastgroup]))
            position = match.end()
        self.position = 0

    def value(self) -> object:
        result = self._or()
        if self.position != len(self.tokens):
            raise ValueError(f"unexpected {self.tokens[self.position][1]!r}")
        return result

    def _operator(self) -> str | None:
        if self.position < len(self.tokens) and self.tokens[self.position][0] == "op":
            return self.tokens[self.position][1]
        return None

    def _take(self, expected: str | None = None) -> tuple[str, str]:
        if self.position >= len(self.tokens):
            raise ValueError("the expression ends too early")
        token = self.tokens[self.position]
        if expected is not None and token != ("op", expected):
            raise ValueError(f"expected {expected!r}, found {token[1]!r}")
        self.position += 1
        return token

    def _or(self) -> object:
        left = self._and()
        while self._operator() == "||":
            self._take()
            left = _either(left, self._and())
        return left

    def _and(self) -> object:
        left = self._equality()
        while self._operator() == "&&":
            self._take()
            left = _both(left, self._equality())
        return left

    def _equality(self) -> object:
        left = self._relation()
        while (operator := self._operator()) in ("==", "!="):
            self._take()
            right = self._relation()
            if isinstance(left, _Runtime) or isinstance(right, _Runtime):
                left = _Runtime()
            else:
                left = _equal(left, right) == (operator == "==")
        return left

    def _relation(self) -> object:
        left = self._unary()
        while (operator := self._operator()) in ("<", "<=", ">", ">="):
            self._take()
            right = self._unary()
            if isinstance(left, _Runtime) or isinstance(right, _Runtime):
                left = _Runtime()
            else:
                left = _ordered(str(operator), left, right)
        return left

    def _unary(self) -> object:
        if self._operator() == "!":
            self._take()
            known = _truth(self._unary())
            return _Runtime() if known is None else not known
        return self._postfix()

    def _postfix(self) -> object:
        value = self._primary()
        while (operator := self._operator()) in (".", "["):
            self._take()
            if operator == "[":
                key = self._or()
                self._take("]")
            else:
                kind, name = self._take()
                if (kind, name) == ("op", "*"):
                    value = _Runtime()  # an object filter: not worth evaluating here
                    continue
                if kind != "name":
                    raise ValueError(f"a property name cannot be {name!r}")
                key = name
            value = _member(value, key)
        return value

    def _primary(self) -> object:
        kind, text = self._take()
        if kind == "string":
            return text[1:-1].replace("''", "'")
        if kind == "number":
            return float(text)
        if (kind, text) == ("op", "("):
            value = self._or()
            self._take(")")
            return value
        if kind == "name":
            if self._operator() == "(":
                self._take()
                args: list[object] = []
                if self._operator() != ")":
                    args.append(self._or())
                    while self._operator() == ",":
                        self._take()
                        args.append(self._or())
                self._take(")")
                return _call(text.lower(), args)
            if text.lower() in ("true", "false"):
                return text.lower() == "true"
            if text.lower() == "null":
                return None
            return _Runtime()  # a context: github, env, steps, needs, matrix, inputs, ...
        raise ValueError(f"unexpected {text!r}")


def _constant_truth(condition: str) -> bool | None:
    """True or False when GitHub decides an `if:` before the run; None when the run decides."""
    text = condition.strip()
    whole = re.fullmatch(r"\$\{\{(.*)\}\}", text, re.DOTALL)
    if whole and "${{" not in whole[1]:
        text = whole[1]
    elif "${{" in text:
        return True  # text outside `${{ }}` makes the value a non-empty string, which is true
    try:
        return _truth(_Expression(text).value())
    except (ValueError, IndexError):
        return None  # GitHub rejects the whole workflow when it cannot read an expression


def _is_live(step: dict) -> bool:
    """False only when the step is switched off by a constant, not by a runtime gate.

    A constant is any `if:` that GitHub decides before the run (`_constant_truth`), so
    `${{ false && true }}` is as dead as `false`. The same applies to a job's `if:`.
    """
    condition = step.get("if")
    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition
    if str(condition).strip().lower() in _STATICALLY_DISABLED:
        return False
    return _constant_truth(str(condition)) is not False


@pytest.mark.parametrize(
    "condition",
    [
        False,
        0,
        "false",
        "${{ false }}",
        "${{ false && true }}",
        "${{ true && false }}",
        "false && steps.relevance.outputs.run == 'true'",
        "${{ steps.relevance.outputs.run == 'true' && false }}",
        "${{ !true }}",
        "!(true || github.event_name == 'push')",
        "${{ 1 == 2 }}",
        "${{ 'a' != 'A' }}",
        "${{ '' }}",
        "${{ null }}",
        "${{ always() && false }}",
        "${{ contains('abc', 'z') }}",
        "${{ startsWith('abc', 'b') }}",
        "${{ fromJSON('false') }}",
        "${{ fromJSON('{\"run\": false}').run }}",
        "${{ format('{0}', '') }}",
    ],
)
def test_a_constant_false_if_switches_a_step_off(condition: object) -> None:
    """Any `if:` that GitHub decides to be false before the run is dead (#750, CodeRabbit)."""
    assert not _is_live({"if": condition, "run": "pytest"})


@pytest.mark.parametrize(
    "condition",
    [
        None,
        True,
        "true",
        "${{ always() }}",
        "success()",
        "${{ !cancelled() }}",
        "steps.relevance.outputs.run == 'true'",
        "needs.verify.outputs.publish == 'true'",
        "${{ github.event_name != 'pull_request' || steps.relevance.outputs.run == 'true' }}",
        "${{ false || github.event_name == 'schedule' }}",
        "${{ hashFiles('pyproject.toml') != '' }}",
        "${{ 'A' == 'a' }}",
        "${{ 1 == '1' }}",
        "${{ false }} && true",  # text outside ${{ }}: a non-empty string, which is true
        "${{ fromJSON('[0]')[1] == null }}",
        "steps.relevance.outputs.run == (",  # GitHub rejects the workflow, which fails by itself
    ],
)
def test_a_runtime_or_true_if_keeps_a_step_live(condition: object) -> None:
    """A gate that reads the run (or is true) is not a switch-off, so the step is live."""
    assert _is_live({"if": condition, "run": "pytest"})


def _steps(text: str) -> list[dict]:
    """Every step of every job, from the PARSED workflow.

    Deliberately a YAML parse -- the one place this module departs from its otherwise
    stdlib-only (``ast``/``re``) style. Two text-matching versions of the guard below were
    both false-green, and the second failure is the argument for parsing:

    * v1 searched the whole file for the bare action name, which this workflow's own header
      comment contains, so deleting the step kept the guard green (Codex, #319);
    * v2 stripped whole-line comments and matched a ``uses:`` line pattern, which a
      ``run: |`` block scalar containing that text still satisfies, and which a perfectly
      valid ``uses: "actions/attest-build-provenance@<sha>"`` still misses (CodeRabbit,
      #319).

    A parser tells a step's ``uses`` key from a string that merely looks like one, which no
    regex over the same text can. ``yaml`` is on the base 3-OS ``test`` matrix already --
    ``test_issue_forms``, ``test_docs_prd_pointers``, ``test_citation_metadata``,
    the surviving agent suites all import it -- so this costs no dependency.
    """
    workflow = yaml.safe_load(text)
    return [
        step
        for job in (workflow.get("jobs") or {}).values()
        for step in (job.get("steps") or [])
        if isinstance(step, dict)
    ]


def _step_uses(text: str) -> list[str]:
    """Every step's ``uses:`` value -- an action the runner really invokes."""
    return [s["uses"] for s in _steps(text) if isinstance(s.get("uses"), str)]


def _step_run_scripts(text: str) -> str:
    """Every step's ``run:`` script -- the shell the runner really executes."""
    return "\n".join(s["run"] for s in _steps(text) if isinstance(s.get("run"), str))


def test_the_integrity_anchor_that_replaced_signing_is_still_wired() -> None:
    """The other half of ADR-0059, and the half that actually protects a downloader.

    Removing signing is only defensible because the manifests and the attestation remain.
    A guard on the absence alone would stay green if the replacement were deleted too,
    which is the worse outcome of the two, so both are pinned together.

    Both halves assert against the PARSED workflow: the attestation as a live step's
    ``uses`` key **in the publishing job**, the manifests as text in a real step's ``run``
    script. See :func:`_steps` for the false-greens that got it here -- three distinct ways
    for this guard to pass while the published Release carries no provenance, two of them
    found by reviewers rather than by me.
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    attesting = [
        step
        for step in _job_steps(text, PUBLISH_JOB)
        if isinstance(step.get("uses"), str)
        and step["uses"].startswith(ATTEST_ACTION)
        and _is_live(step)
    ]
    assert attesting, (
        f"the `{PUBLISH_JOB}` job must carry a live `uses: {ATTEST_ACTION}<sha>` step -- "
        "ADR-0059's integrity anchor for the installers. A step in another job, or one "
        "switched off by a constant `if:`, attests nothing about the published Release. "
        f"Steps found in `{PUBLISH_JOB}`: "
        f"{sorted(s.get('uses', '(run)') for s in _job_steps(text, PUBLISH_JOB))}"
    )
    assert "SHA256SUMS" in _step_run_scripts(text), (
        "a `run:` step in release.yml must still produce the SHA-256 manifests -- "
        "ADR-0059's other anchor"
    )


def test_the_anchor_guard_recognises_a_step_and_nothing_else() -> None:
    """The regression test for the guard itself, since its whole failure was staying green.

    An absence-assertion proves nothing unless presence is shown, so both false-green forms
    that actually shipped are pinned here beside the valid spellings that must pass.
    """
    step = "jobs:\n  release:\n    steps:\n      - uses: {}\n"

    # Valid spellings. The quoted form is why this is a parse and not a regex: v2's pattern
    # rejected it while accepting text that was not a step at all.
    assert _step_uses(step.format("actions/attest-build-provenance@abc123")) == [
        "actions/attest-build-provenance@abc123"
    ]
    assert _step_uses(step.format('"actions/attest-build-provenance@abc123"')) == [
        "actions/attest-build-provenance@abc123"
    ]

    # v1's false green: named in a comment, with no step at all.
    commented = "jobs:\n  release:\n    steps:\n      # uses: actions/attest-build-provenance@v3\n"
    assert not [u for u in _step_uses(commented) if u.startswith(ATTEST_ACTION)]

    # v2's false green: the same text inside a `run:` block scalar, which is a shell script
    # and attests nothing.
    block_scalar = (
        "jobs:\n"
        "  release:\n"
        "    steps:\n"
        "      - name: Not an attestation\n"
        "        run: |\n"
        "          echo 'uses: actions/attest-build-provenance@abc123'\n"
    )
    assert not [u for u in _step_uses(block_scalar) if u.startswith(ATTEST_ACTION)]


def test_the_anchor_guard_rejects_an_attestation_that_cannot_run() -> None:
    """The two bypasses Codex named that surviving the parse alone would not have closed.

    A step can be perfectly real, perfectly parsed, and still attest nothing about the
    published Release -- by sitting in a different job, or by being switched off with a
    constant. Both are the same failure ADR-0059 removed the signing legs to end: present,
    green, and dead.
    """
    attest = f"      - uses: {ATTEST_ACTION}abc123\n"

    # Wrong job. `build` runs per-platform and publishes nothing.
    wrong_job = (
        f"jobs:\n  build:\n    steps:\n{attest}  release:\n    steps:\n      - run: echo hi\n"
    )
    assert not [
        s
        for s in _job_steps(wrong_job, PUBLISH_JOB)
        if str(s.get("uses", "")).startswith(ATTEST_ACTION)
    ]

    # Right job, switched off by a constant.
    disabled = f"jobs:\n  release:\n    steps:\n{attest}        if: false\n"
    steps = _job_steps(disabled, PUBLISH_JOB)
    assert steps and str(steps[0]["uses"]).startswith(ATTEST_ACTION)
    assert not _is_live(steps[0])

    # A runtime gate is NOT a constant -- the real step carries one and must stay live.
    gate = "        if: needs.verify.outputs.publish == 'true'\n"
    live = f"jobs:\n  release:\n    steps:\n{attest}{gate}"
    assert _is_live(_job_steps(live, PUBLISH_JOB)[0])


# --- The published-docs liveness leg (docs-liveness.yml, issue #325) ---
# A scheduled thin wrapper over scripts/check_docs_site.py, whose logic is unit-tested offline in
# tests/test_docs_site_guard.py. These guards keep the wrapper honest: it really runs the script,
# it can never gate a merge, it can go red, and it holds the token scope the Pages probe needs.
DOCS_LIVENESS_WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "docs-liveness.yml"
)
DOCS_GUARD_SCRIPT = "scripts/check_docs_site.py"


def _docs_liveness_jobs() -> dict[str, dict]:
    workflow = yaml.safe_load(DOCS_LIVENESS_WORKFLOW.read_text(encoding="utf-8"))
    return {k: v for k, v in (workflow.get("jobs") or {}).items() if isinstance(v, dict)}


def _docs_guard_steps() -> list[tuple[dict, dict]]:
    """(job, step) for every step whose ``run:`` invokes the guard script on a non-comment line."""
    return [
        (job, step)
        for job in _docs_liveness_jobs().values()
        for step in (job.get("steps") or [])
        if isinstance(step, dict)
        and isinstance(step.get("run"), str)
        and any(
            DOCS_GUARD_SCRIPT in ln and not ln.lstrip().startswith("#")
            for ln in step["run"].splitlines()
        )
    ]


def test_docs_liveness_workflow_really_runs_the_guard_script() -> None:
    text = DOCS_LIVENESS_WORKFLOW.read_text(encoding="utf-8")
    assert DOCS_GUARD_SCRIPT in _step_run_scripts(text), "no step's run: names the guard script"
    live_steps = [s for s in _steps(text) if _is_live(s)]
    assert any(step in live_steps and _is_live(job) for job, step in _docs_guard_steps()), (
        f"docs-liveness.yml must keep a live step (in a live job) that runs {DOCS_GUARD_SCRIPT}"
    )


def test_docs_liveness_is_scheduled_daily_and_non_required_by_construction() -> None:
    triggers = _on_block_child_keys(DOCS_LIVENESS_WORKFLOW.read_text(encoding="utf-8"))
    assert {"schedule", "workflow_dispatch"} <= triggers, f"on-triggers: {sorted(triggers)}"
    assert triggers.isdisjoint({"pull_request", "push", "merge_group"}), (
        "docs-liveness.yml must stay advisory (no PR/push/merge_group trigger); "
        f"on-triggers: {sorted(triggers)}"
    )

    def crons(path: Path) -> list[str]:
        on = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        on = on.get("on", on.get(True)) or {}  # YAML 1.1 reads a bare `on:` key as True
        schedule = on.get("schedule") if isinstance(on, dict) else None
        return [str(e["cron"]) for e in (schedule or []) if isinstance(e, dict) and "cron" in e]

    (cron,) = crons(DOCS_LIVENESS_WORKFLOW)
    minute, hour, *rest = cron.split()
    assert minute.isdigit() and hour.isdigit() and rest == ["*", "*", "*"], f"not daily: {cron}"
    others = {
        tuple(c.split()[:2])
        for wf in DOCS_LIVENESS_WORKFLOW.parent.glob("*.yml")
        if wf != DOCS_LIVENESS_WORKFLOW
        for c in crons(wf)
    }
    assert (minute, hour) not in others, f"cron {cron} shares its minute/hour with another leg"


def test_docs_liveness_can_go_red() -> None:
    jobs = _docs_liveness_jobs()
    assert jobs, "docs-liveness.yml must keep a job"
    for job_id, job in jobs.items():
        assert "continue-on-error" not in job, f"job {job_id} must be able to go red"
        for step in job.get("steps") or []:
            assert "continue-on-error" not in step, f"a {job_id} step is continue-on-error"
    guard_steps = _docs_guard_steps()
    assert guard_steps
    for _job, step in guard_steps:
        assert not re.search(r"\|\|\s*(true|:)\b", step["run"]), "the guard step is silenced"


def test_docs_liveness_declares_pages_read() -> None:
    guard_steps = _docs_guard_steps()
    assert guard_steps, f"no step runs {DOCS_GUARD_SCRIPT}"
    for job, _step in guard_steps:
        permissions = job.get("permissions") or {}
        assert permissions.get("contents") == "read", permissions
        assert permissions.get("pages") == "read", permissions


# The release-tag grammar (issue #380). PRD §12.7 states a release tag's form as
# `vMAJOR.MINOR.PATCH`, and ADR-0060 §4's update client allowlists exactly that form on the
# attested `sourceRepositoryRef`. Before #380 the publisher checked only for a leading `v` and
# called a tag a prerelease when it held a hyphen, so `v1.0.0.0` published as a STABLE Release
# that the client then refuses. The grammar is declared ONCE, as the `TAG_ERE` literal in the
# `verify` job's resolve step; the guards below read that literal back out of the parsed
# workflow (restating it here would test the test), and execute the step scripts themselves,
# because nothing else in this repository lints a `run:` script.
RESOLVE_STEP_NAME = "Resolve target tag + publish mode"
RELEASE_CREATE_STEP_NAME = "Create the GitHub Release"
DOCS_PUBLISH_STEP_NAME = "Publish the documentation site (dispatch docs.yml)"
PRERELEASE_EXPRESSION = "${{ needs.verify.outputs.prerelease }}"

#: Committed literals, never `git tag` output: `ci.yml`'s `test` job checks out at the default
#: depth, so no tag exists in CI. The first ten are the tags that existed when #380 was groomed
#: (2026-08-13; there is no v0.9.0) -- real shapes, not a standing claim about the tag list.
TAG_ACCEPT = (
    "v0.0.0",
    "v0.1.0",
    "v0.2.0",
    "v0.3.0",
    "v0.4.0",
    "v0.5.0",
    "v0.6.0",
    "v0.7.0",
    "v0.8.0",
    "v1.0.0-rc1",
    "v1.0.0",
    "v10.2.30",
    "v1.0.0-rc.2",
)
#: `v1.0.0\n` is here because Python's `$` also matches before a trailing newline while bash's
#: `[[ =~ ]]` does not; it is what makes a test built on `re.match` a red test, not a footnote.
TAG_REJECT = (
    "v1.0.0.0",
    "vfoo",
    "v1.0",
    "v1",
    "v1.0.0+build.7",
    "v1.0.0-rc1+build.7",
    "v1.0.0-",
    "v01.0.0",
    "1.0.0",
    "V1.0.0",
    "refs/heads/main",
    "v1.0.0\n",
)
TAG_PRERELEASE = ("v1.0.0-rc1", "v1.0.0-rc.2")
TAG_STABLE = ("v1.0.0", "v0.8.0")


def _release_step(job_id: str, name: str) -> dict:
    """The ONE step of ``job_id`` named exactly ``name``, from the parsed release workflow."""
    steps = [
        s
        for s in _job_steps(RELEASE_WORKFLOW.read_text(encoding="utf-8"), job_id)
        if s.get("name") == name
    ]
    assert len(steps) == 1, (
        f"release.yml's `{job_id}` job must carry exactly one step named {name!r}; "
        f"found {len(steps)}"
    )
    return steps[0]


def _release_tag_ere() -> str:
    """The ``TAG_ERE`` literal, extracted from the resolve step's ``run:`` script.

    Anchored to an assignment line, so a comment that quotes the grammar cannot stand in for it.
    """
    run = _release_step("verify", RESOLVE_STEP_NAME).get("run")
    assert isinstance(run, str), f"the {RESOLVE_STEP_NAME!r} step must carry a `run:` script"
    found = re.findall(r"^[ \t]*TAG_ERE='([^']+)'[ \t]*$", run, re.MULTILINE)
    assert len(found) == 1, (
        f"the {RESOLVE_STEP_NAME!r} step must declare the release-tag grammar exactly once, as "
        f"a single-quoted `TAG_ERE='...'` literal (issue #380); found {len(found)}"
    )
    return found[0]


def _bash() -> str:
    """The bash that runs the extracted step scripts.

    ``ci.yml``'s ``test`` job runs ``bash -el {0}`` on all three OSes (Git Bash on Windows), so
    bash is expected everywhere; on Linux its absence FAILS rather than skips, so these legs
    cannot go universally vacuous.
    """
    bash = shutil.which("bash")
    if bash is None:
        if sys.platform.startswith("linux"):
            pytest.fail("bash is not on PATH; the release-script tests must not go vacuous")
        pytest.skip("bash is not on PATH on this platform")
    return bash


def _write_script(path: Path, text: str) -> Path:
    # LF line endings on every OS: bash reads a CR as part of the command word.
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_release_tag_grammar_accepts_the_stated_form_only() -> None:
    """The extracted ``TAG_ERE`` admits exactly the committed accept table and classifies it.

    Applied with ``re.fullmatch``, never ``re.match``/``re.search``: Python's ``$`` also matches
    immediately before a trailing newline while bash's ``[[ =~ ]]`` anchors at end of string, so
    a test on ``re.match`` would be strictly looser than the runner it mirrors -- the
    ``"v1.0.0\\n"`` reject row is what turns that divergence red. The prerelease verdict is
    group 4's truthiness (bash leaves a non-participating group as ``""``, Python as ``None``),
    which is the same group the workflow reads from ``BASH_REMATCH[4]``.

    The literal is held to constructs POSIX ERE and Python ``re`` read the same way, since
    compiling the extracted text with ``re`` is only a test of the runner if both engines agree
    on it: no POSIX bracket classes, no escape other than ``\\.``, no non-greedy operator, no
    backreference.
    """
    ere = _release_tag_ere()
    assert "[:" not in ere, f"TAG_ERE must not use a POSIX character class: {ere!r}"
    assert not re.search(r"\\[^.]", ere), f"TAG_ERE may escape only `.` (`\\.`): {ere!r}"
    assert not re.search(r"[*+?]\?", ere), f"TAG_ERE must not use a non-greedy operator: {ere!r}"
    grammar = re.compile(ere)

    def prerelease_of(match: re.Match[str]) -> bool:
        return bool(match.group(4)) if grammar.groups >= 4 else False

    wrong: list[str] = []
    for tag in TAG_ACCEPT:
        if grammar.fullmatch(tag) is None:
            wrong.append(f"rejected {tag!r}, which the grammar must accept")
    for tag in TAG_REJECT:
        if grammar.fullmatch(tag) is not None:
            wrong.append(f"accepted {tag!r}, which the grammar must reject")
    for tag, prerelease in [(t, True) for t in TAG_PRERELEASE] + [(t, False) for t in TAG_STABLE]:
        match = grammar.fullmatch(tag)
        if match is not None and prerelease_of(match) is not prerelease:
            wrong.append(f"classified {tag!r} as prerelease={not prerelease}")
    if grammar.groups < 4:
        wrong.append("lost group 4, the prerelease component the workflow reads")
    assert not wrong, f"release.yml's TAG_ERE {ere!r} " + "; ".join(wrong)


def test_release_prerelease_verdict_comes_from_the_parsed_tag() -> None:
    """Every prerelease decision reads the ``verify`` output, never a hyphen search over ``$TAG``.

    (a) ``verify`` exports ``prerelease`` from the resolve step; (b) both publish steps map an
    env var to that output and read it on a non-comment line -- anchored to non-comment lines
    because a rationale comment naming the variable would otherwise keep this green with the
    read deleted, the vacuity trap the staging gate's ``read -ra`` guard records; (c) the
    ``*-*`` glob appears nowhere in the file, matched as a regex over the whole text so a
    line-broken ``case`` -- the form the second occurrence had -- cannot hide; (d) neither step
    tests ``$TAG`` with ``case``, ``[``, ``[[`` or ``test``. This pins where the value comes
    from; :func:`test_release_publish_scripts_follow_the_prerelease_output` pins what it does.
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"\*-\*", text) is None, (
        "the `*-*` hyphen-search glob must not appear anywhere in release.yml (issue #380)"
    )
    outputs = (yaml.safe_load(text)["jobs"]["verify"].get("outputs")) or {}
    assert "steps.resolve.outputs.prerelease" in str(outputs.get("prerelease", "")), (
        "the `verify` job must export `prerelease: ${{ steps.resolve.outputs.prerelease }}`"
    )
    tag_operand = re.compile(
        r'\bcase\s+"?\$\{?TAG\b'
        r"|(?:^|[\s;&|!(])\[\[?\s[^\]\n]*\$\{?TAG\b"
        r"|\btest\s[^;&|\n]*\$\{?TAG\b"
    )
    for name in (RELEASE_CREATE_STEP_NAME, DOCS_PUBLISH_STEP_NAME):
        step = _release_step("release", name)
        keys = [
            k for k, v in (step.get("env") or {}).items() if str(v).strip() == PRERELEASE_EXPRESSION
        ]
        assert keys, f"the {name!r} step must map an env var to `{PRERELEASE_EXPRESSION}`"
        run = step.get("run")
        assert isinstance(run, str), f"the {name!r} step must carry a `run:` script"
        code = [ln for ln in run.splitlines() if not ln.lstrip().startswith("#")]
        reads = re.compile(r"\$\{?" + re.escape(keys[0]) + r"\b")
        assert any(reads.search(ln) for ln in code), (
            f"the {name!r} step must read ${keys[0]} on a non-comment line of its script"
        )
        offenders = [ln for ln in code if tag_operand.search(ln)]
        assert not offenders, (
            f"the {name!r} step must not decide anything by testing $TAG -- the verdict is the "
            f"`verify` job's parsed prerelease component: {offenders}"
        )


def _run_resolve(bash: str, tmp_path: Path, tag: str, event: str) -> tuple[int, str, dict]:
    step = _release_step("verify", RESOLVE_STEP_NAME)
    declared = set((step.get("env") or {}).keys())
    assert declared == {"EVENT", "PUSH_TAG", "INPUT_REF", "INPUT_DRY_RUN"}, (
        f"the {RESOLVE_STEP_NAME!r} step's env keys changed ({sorted(declared)}); "
        "update this harness so every key it declares is set on both event paths"
    )
    script = _write_script(tmp_path / "resolve.sh", step["run"])
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    if event == "push":
        keys = {"EVENT": "push", "PUSH_TAG": tag, "INPUT_REF": "", "INPUT_DRY_RUN": ""}
    else:
        keys = {"EVENT": event, "PUSH_TAG": "main", "INPUT_REF": tag, "INPUT_DRY_RUN": "true"}
    env = {**os.environ, **keys, "GITHUB_OUTPUT": output.as_posix()}
    proc = subprocess.run(
        [bash, script.as_posix()],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    written = dict(
        line.split("=", 1)
        for line in output.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return proc.returncode, proc.stdout + proc.stderr, written


@pytest.mark.parametrize("event", ["workflow_dispatch", "push"])
@pytest.mark.parametrize("tag", TAG_ACCEPT + TAG_REJECT)
def test_release_resolve_script_enforces_the_grammar(tmp_path: Path, tag: str, event: str) -> None:
    """The resolve step's real script, under bash, on BOTH event paths, per table row.

    Nothing in this repository lints a ``run:`` script (no shellcheck, no actionlint), so a
    shell mistake -- quoting ``"$TAG_ERE"`` inside ``[[ =~ ]]``, which matches the pattern
    literally and rejects every real tag -- would land behind green YAML-parsing tests. Running
    the dispatch path (``publish=false``) as well as the tag push (``publish=true``) is what
    makes "the check is not ``publish``-gated" a test rather than a promise.
    """
    bash = _bash()
    code, out, written = _run_resolve(bash, tmp_path, tag, event)
    publish = "true" if event == "push" else "false"
    if tag in TAG_ACCEPT:
        assert code == 0, f"{event}: the resolve step rejected {tag!r}:\n{out}"
        expected = {
            "tag": tag,
            "publish": publish,
            "prerelease": "true" if tag in TAG_PRERELEASE else "false",
        }
        got = {k: written.get(k) for k in expected}
        assert got == expected, f"{event}: {tag!r} wrote {written} to $GITHUB_OUTPUT:\n{out}"
    else:
        assert code != 0, f"{event}: the resolve step accepted {tag!r}:\n{out}"
        errors = [ln for ln in out.splitlines() if ln.startswith("::error::")]
        assert any("vMAJOR.MINOR.PATCH" in ln and tag.strip() in ln for ln in errors), (
            f"{event}: rejecting {tag!r} must print an `::error::` line naming the tag and the "
            f"`vMAJOR.MINOR.PATCH` form; got:\n{out}"
        )


@pytest.mark.parametrize("tag", ["v1.0.0-rc1", "v1.0.0"])
@pytest.mark.parametrize("prerelease", ["true", "false"])
@pytest.mark.parametrize("name", [RELEASE_CREATE_STEP_NAME, DOCS_PUBLISH_STEP_NAME])
def test_release_publish_scripts_follow_the_prerelease_output(
    tmp_path: Path, name: str, prerelease: str, tag: str
) -> None:
    """Both publish scripts, run under bash with ``gh`` stubbed, obey ``PRERELEASE`` alone.

    Crossing ``PRERELEASE`` with how the tag is spelled is the point: the verdict must follow
    the ``verify`` output and ignore the hyphen. ``gh`` is a shell function in a wrapper that
    ``source``\\ s the extracted script, so nothing depends on an executable bit or a ``PATH``
    entry and the Windows leg behaves like the others. The compared literal is the lowercase
    ``true`` the workflow's own step gates use (``needs.verify.outputs.publish == 'true'``).
    """
    bash = _bash()
    step = _release_step("release", name)
    # The wiring is test_release_prerelease_verdict_comes_from_the_parsed_tag's to pin; this
    # test pins the behaviour, so it falls back to the conventional name rather than failing
    # on the wiring first and hiding what the script does.
    keys = [
        k for k, v in (step.get("env") or {}).items() if str(v).strip() == PRERELEASE_EXPRESSION
    ]
    variable = keys[0] if keys else "PRERELEASE"
    script = _write_script(tmp_path / "step.sh", step["run"])
    argv = tmp_path / "gh_argv"
    wrapper = _write_script(
        tmp_path / "wrapper.sh",
        f'gh() {{ printf \'%s\\n\' "$@" >> "$GH_ARGV"; }}\nsource "{script.as_posix()}"\n',
    )
    notes = tmp_path / "notes.md"
    notes.write_text("notes\n", encoding="utf-8")
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "TAG": tag,
        "NOTES": notes.as_posix(),
        "GITHUB_STEP_SUMMARY": summary.as_posix(),
        "GH_ARGV": argv.as_posix(),
        variable: prerelease,
    }
    proc = subprocess.run(
        [bash, wrapper.as_posix()],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"{name!r} failed (PRERELEASE={prerelease}, TAG={tag}):\n{out}"
    called = argv.read_text(encoding="utf-8").splitlines() if argv.exists() else []
    case = f"{name!r} with PRERELEASE={prerelease}, TAG={tag}"
    if name == RELEASE_CREATE_STEP_NAME:
        assert called[:2] == ["release", "create"], (
            f"{case} must call `gh release create`: {called}"
        )
        assert ("--prerelease" in called) is (prerelease == "true"), (
            f"{case}: `--prerelease` must be passed exactly when PRERELEASE is `true`: {called}"
        )
    elif prerelease == "true":
        assert called == [], f"{case} must not dispatch the documentation site: {called}"
    else:
        assert called[:3] == ["workflow", "run", "docs.yml"], (
            f"{case} must dispatch `gh workflow run docs.yml`: {called}"
        )


# --- The shared install-smoke script (packaging.yml + release.yml, issue #217) ---
# The per-platform offline install-smoke lives in ONE checked-in script,
# packaging/scripts/install_smoke.sh, invoked by both the advisory packaging.yml
# (`installer` job) and the release pipeline's `build` job — extracting it is what stops
# the two smokes drifting apart (the drift class #213 closed for the wheel-staging steps
# and #218 closed again for the setuptools bound). These guards keep the extraction
# honest: both callers really invoke the script, the release invocation gates the exact
# bytes that job uploads, the script fails its caller when any probe fails, the offline
# property is asserted positively, and the probe set cannot be silently gutted (the two
# re-pointed guards above).
INSTALL_SMOKE_SCRIPT = (
    Path(__file__).resolve().parents[1] / "packaging" / "scripts" / "install_smoke.sh"
)
INSTALL_SMOKE_PATH = "packaging/scripts/install_smoke.sh"

#: (workflow file, the job whose steps must invoke the smoke script).
SMOKE_CALLERS = ((PACKAGING_WORKFLOW, "installer"), (RELEASE_WORKFLOW, "build"))


def _install_smoke_code_lines() -> str:
    """The smoke script's NON-COMMENT lines, joined.

    The string guards match against these only, for a measured reason: ``_RUNNER`` (and
    friends) already lived in an explanatory comment in packaging.yml's smoke, and the
    extraction carried those comments across with the code they document — so a whole-text
    check would stay green after the command itself was deleted.
    """
    return "\n".join(
        ln
        for ln in INSTALL_SMOKE_SCRIPT.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )


def _smoke_invocations(text: str, job_id: str) -> list[dict]:
    """The live steps of *job_id* whose ``run:`` script invokes the smoke script."""
    return [
        step
        for step in _job_steps(text, job_id)
        if _is_live(step) and isinstance(step.get("run"), str) and INSTALL_SMOKE_PATH in step["run"]
    ]


def test_install_smoke_script_is_invoked_by_both_workflows() -> None:
    """One script, two callers (issue #217) — the invocation, from the PARSED workflow.

    A raw-text search is forbidden here for a measured reason: the ``_steps`` docstring
    records this module's own false-green (v1 searched the whole file for a bare action
    name that the workflow's header comment contained), and packaging.yml's header already
    narrates the smoke — so an implementer who deleted the invoking step while updating a
    comment to name the script would satisfy a text search with no invocation at all.
    Job-scoped and live-only: a step in an unrelated job, or one switched off by a
    constant ``if:``, smokes nothing this guard cares about.
    """
    for path, job_id in SMOKE_CALLERS:
        assert _smoke_invocations(path.read_text(encoding="utf-8"), job_id), (
            f"{path.name}'s `{job_id}` job must invoke {INSTALL_SMOKE_PATH} from a live "
            "step's `run:` script — the one checked-in probe sequence both workflows "
            "share (issue #217)"
        )


def test_install_smoke_script_arms_set_euo_pipefail_first() -> None:
    """``set -euo pipefail`` is the FIRST executable line — position, not mere presence.

    Once the probe sequence lives in a script, the calling step's ``-e`` only ever sees
    the script's FINAL exit status — and the Unix branch deliberately ENDS in a non-fatal
    command (an ``if`` block that is simply false on macOS) — so a script without
    ``set -e`` exits 0 on macOS no matter which probe above failed: a
    green-forever release gate, and one the string guards cannot see, because every
    guarded string is still there. Presence anywhere is not enough either: armed below
    the installer run, ``-e`` covers nothing that matters. This is the criterion that
    keeps the extraction from quietly converting the gate into a no-op (issue #217).
    """
    lines = INSTALL_SMOKE_SCRIPT.read_text(encoding="utf-8").splitlines()
    executable = [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    assert executable and executable[0] == "set -euo pipefail", (
        "the first line of packaging/scripts/install_smoke.sh that is neither the "
        "shebang, nor blank, nor a comment must be exactly `set -euo pipefail`; found: "
        f"{executable[:1]}"
    )


POST_INSTALL_BAT = INSTALL_SMOKE_SCRIPT.with_name("post_install.bat")
PRE_UNINSTALL_BAT = INSTALL_SMOKE_SCRIPT.with_name("pre_uninstall.bat")
CONSTRUCT_YAML = INSTALL_SMOKE_SCRIPT.parents[1] / "construct.yaml"


def _bat_code_lines(path: Path) -> list[str]:
    """A batch script's non-blank lines, stripped, minus ``rem`` comments and ``@echo off``."""
    return [
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
        and not re.match(r"(?i)rem(\s|$)", ln.strip())
        and ln.strip().lower() != "@echo off"
    ]


def test_windows_post_install_names_powershell_by_absolute_path() -> None:
    """The shortcut step must not rely on PATH to find PowerShell (issue #551).

    constructor's NSIS template replaces PATH for post_install with the prefix's own
    folders plus ``%SystemRoot%``, ``System32`` and ``System32\\Wbem``, so a bare
    ``powershell`` resolves to nothing on every machine. That is how rc2 shipped with no
    Start Menu shortcut, and the ``2>nul`` on the call hid the reason from the installer
    log. Both halves are pinned: the absolute path, and no discarded error output.
    """
    code = _bat_code_lines(POST_INSTALL_BAT)
    bare = [ln for ln in code if re.match(r"(?i)powershell(\.exe)?\b", ln)]
    assert not bare, (
        "post_install.bat must call PowerShell by its absolute path, never a bare "
        f"`powershell` (the installer's PATH leaves it out); offending lines: {bare}"
    )
    assert any(
        r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" in ln for ln in code
    ), "post_install.bat must name %SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    shortcut_call = [ln for ln in code if '"$s.Save()"' in ln]
    assert shortcut_call and all("2>nul" not in ln for ln in shortcut_call), (
        "the shortcut call must keep its error output so a failure reaches the installer "
        "log (issue #551)"
    )


def test_windows_pre_uninstall_removes_the_shortcut_and_never_fails() -> None:
    """The uninstaller removes what post_install created, and only that (issue #551).

    The shortcut is created imperatively (ADR-0051), so nothing in constructor's own
    uninstall removes it; ``pre_uninstall`` is the hook that runs before the prefix goes.
    """
    recipe = CONSTRUCT_YAML.read_text(encoding="utf-8")
    assert re.search(r"(?m)^pre_uninstall:\s*scripts/pre_uninstall\.bat\s+# \[win\]\s*$", recipe), (
        "construct.yaml must declare `pre_uninstall: scripts/pre_uninstall.bat   # [win]`"
    )
    code = _bat_code_lines(PRE_UNINSTALL_BAT)
    assert any("del " in ln and "Tether.lnk" in ln for ln in code), (
        "pre_uninstall.bat must delete the Tether.lnk shortcut"
    )
    rmdirs = [ln for ln in code if re.search(r"(?i)\b(rmdir|rd)\b", ln)]
    assert rmdirs and all("/s" not in ln.lower() for ln in rmdirs), (
        "pre_uninstall.bat may remove the Start Menu folder only with a plain `rmdir` "
        f"(never /s), so anything else in it survives; found: {rmdirs}"
    )
    assert code[-1].lower() == "exit /b 0", (
        "pre_uninstall.bat must end in `exit /b 0`: cleaning a shortcut may never fail "
        "the uninstall"
    )


def test_windows_install_smoke_fails_on_a_missing_shortcut_and_checks_uninstall() -> None:
    """The shortcut check is a gate, not a warning, and uninstall is exercised (issue #551)."""
    code = _install_smoke_code_lines()
    assert "WARNING: Start Menu shortcut absent" not in code, (
        "install_smoke.sh must fail, not warn, when the Start Menu shortcut is absent"
    )
    for needle in ('test -f "$sm_dir/Tether.lnk"', "Uninstall-Tether.exe", '"_?=$prefix"'):
        assert needle in code, (
            f"install_smoke.sh must keep `{needle}` on a non-comment line (issue #551)"
        )


def test_release_install_smoke_gates_the_bytes_the_build_job_uploads() -> None:
    """The release smoke sits between the build and the upload, unconditional (issue #217).

    Position IS the guarantee: immediately after ``Build the installer`` so it smokes the
    artifact that job just produced, and before the checksum + upload steps so a failing
    smoke means no bundle ever reaches the ``release`` job. No ``if:`` (the platform
    branch lives inside the script, so all four matrix legs must run it) and no soft-fail
    key — the latter is also asserted file-wide by
    ``test_release_staging_gate_is_never_conditional_or_advisory``, but asserting it here
    keeps the reason with the step. Job-scoped, ordered and parsed (``_job_steps``), which
    ``_workflow_step_block`` cannot give: it returns one step's raw text, scans the whole
    file, and would match a same-named step in any job.
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    steps = _job_steps(text, "build")
    names = [str(step.get("name", "")) for step in steps]

    smoke_indices = [
        i
        for i, step in enumerate(steps)
        if isinstance(step.get("run"), str) and INSTALL_SMOKE_PATH in step["run"]
    ]
    assert len(smoke_indices) == 1, (
        f"release.yml's `build` job must invoke {INSTALL_SMOKE_PATH} in exactly one step; "
        f"found {len(smoke_indices)}"
    )
    smoke_i = smoke_indices[0]

    build_i = names.index("Build the installer")
    checksum_i = next(i for i, n in enumerate(names) if n.startswith("SHA-256 checksums"))
    upload_i = next(
        i for i, n in enumerate(names) if n.startswith("Upload the platform release bundle")
    )
    assert build_i < smoke_i == build_i + 1 < checksum_i < upload_i == len(steps) - 1, (
        "the release install-smoke must run immediately after `Build the installer` and "
        "before the checksum step, with the upload step last, so a failing smoke stops "
        f"the bytes from ever being uploaded; got build={build_i} smoke={smoke_i} "
        f"checksums={checksum_i} upload={upload_i} of {len(steps)} steps"
    )

    smoke = steps[smoke_i]
    assert "if" not in smoke, (
        "the release install-smoke step must declare no `if:` — the platform branch lives "
        f"inside the script, and all four matrix legs must smoke; got {smoke.get('if')!r}"
    )
    assert "continue-on-error" not in smoke, (
        "the release install-smoke step must not soft-fail: its whole purpose is that a "
        "failing probe fails the `build` job before the artifact is uploaded"
    )


def test_install_smoke_owns_the_offline_env_and_no_caller_restates_it() -> None:
    """The offline property is asserted POSITIVELY, in the one place it lives (issue #217).

    Both halves: (a) the script itself exports ``PIP_NO_INDEX=1`` and ``CONDA_OFFLINE=1``
    on non-comment lines — the same anchoring the string guards use, and for the same
    reason: an implementer who explains the two exports in a comment above them would
    otherwise keep a substring check green after deleting one; and (b) neither caller
    restates them as step-level ``env:``, where the two copies could drift — read from the
    parsed step's keys, never the file text, because a whole-file check would fire on an
    explanatory comment at the invocation site. Nothing asserted either variable before
    this, in either direction, so dropping the exports was invisible.
    ``MSYS2_ARG_CONV_EXCL`` is exempt by name: it may live at either side if the runners
    turn out to require a caller-side copy (issue #217 records the fallback).
    """
    code = _install_smoke_code_lines()
    for var in ("PIP_NO_INDEX", "CONDA_OFFLINE"):
        assert re.search(rf"^\s*export {var}=1\s*$", code, re.M), (
            f"packaging/scripts/install_smoke.sh must `export {var}=1` on a non-comment "
            "line — the offline property lives in the script, not in the callers"
        )
    for path, job_id in SMOKE_CALLERS:
        for step in _smoke_invocations(path.read_text(encoding="utf-8"), job_id):
            declared = set(step.get("env") or {}) & {"PIP_NO_INDEX", "CONDA_OFFLINE"}
            assert not declared, (
                f"{path.name}'s install-smoke step must not restate {sorted(declared)} as "
                "step-level `env:` — the script exports the offline pair itself, and a "
                "second copy at the call site is exactly the drift issue #217 removes"
            )


# --- The release-staging completeness gate (release.yml, M9 / ADR-0059) ---
# The `release` job aggregates the 4-leg `build` matrix with `download-artifact` and stages the
# assets with `find ... -exec cp`, which exits 0 when it matches NOTHING -- even under `set -euo
# pipefail`. The two steps that would otherwise notice an empty stage (attest-build-provenance over
# out/*.exe|*.pkg|*.sh, and `gh release create`) are BOTH gated on publish == 'true', so a
# `workflow_dispatch` DRY RUN would stage only the frozen lock files, skip the gated steps, and
# report GREEN: the rehearsal could not detect its own most important failure. Even on a real
# publish, attest is satisfied by >=1 match per pattern, so "1 .pkg where 2 were required" is
# invisible to it. The staging step therefore carries an unconditional completeness gate; these
# gate honest, non-vacuous, and in lockstep with the matrix and the constructor recipe.
STAGING_STEP_NAME = "Stage the release assets"
#: The `verify`-job step that gates a publish on the tagged commit's post-merge checks
#: (issue #266). Its guards live in the post-merge-gate section further down; the constant
#: sits here beside its staging sibling so the two pinned step names stay in one place.
POSTMERGE_STEP_NAME = "Require green post-merge checks"
CONSTRUCT_RECIPE = Path(__file__).resolve().parents[1] / "packaging" / "construct.yaml"
INSTALLER_EXTS = frozenset({"exe", "pkg", "sh"})


def _workflow_step_block(text: str, name_prefix: str) -> str:
    """Raw YAML text of the ``- name: <name_prefix>...`` step, up to the next sibling step.

    A small indent scan in the stdlib-only style of this module (``ast``/``re``, never a YAML
    import), so a guard can assert on one step's keys and ``run:`` script without the rest of the
    workflow leaking in. Returns ``""`` when no such step exists.
    """
    lines = text.splitlines()
    start = indent = None
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("- name:") and stripped.split(":", 1)[1].strip().startswith(
            name_prefix
        ):
            start, indent = i, len(raw) - len(raw.lstrip(" "))
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        raw = lines[j]
        if not raw.strip():
            continue
        cur = len(raw) - len(raw.lstrip(" "))
        if cur < indent or (cur == indent and raw.lstrip().startswith("- ")):
            end = j
            break
    return "\n".join(lines[start:end])


def _release_staging_step() -> str:
    block = _workflow_step_block(RELEASE_WORKFLOW.read_text(encoding="utf-8"), STAGING_STEP_NAME)
    assert block, f"release.yml must keep a '{STAGING_STEP_NAME}...' step in the `release` job"
    return block


def _expected_legs() -> dict[str, str]:
    """``EXPECTED_LEGS`` from the staging step, parsed as ``{platform: installer extension}``."""
    declared = re.search(r'^\s*EXPECTED_LEGS="([^"]*)"', _release_staging_step(), re.M)
    assert declared, (
        'the staging step must declare EXPECTED_LEGS="<platform>:<ext> ..." -- the single source '
        "of truth for what a complete release contains"
    )
    entries = declared.group(1).split()
    malformed = [e for e in entries if e.count(":") != 1]
    assert not malformed, (
        f'each EXPECTED_LEGS entry must be exactly "<platform>:<ext>"; malformed: {malformed}'
    )
    return dict(entry.split(":", 1) for entry in entries)


def test_release_stages_every_frozen_environment_lock() -> None:
    """The release bill of materials includes each isolated environment lock."""
    block = _release_staging_step()
    locks = {
        "conda-lock.yml": "conda-lock.yml",
        "sidecar/conda-lock.yml": "sidecar-conda-lock.yml",
        "deep/conda-lock.yml": "deep-conda-lock.yml",
    }
    for source, asset in locks.items():
        assert f'cp {source} "out/{asset}"' in block
        assert f"[ -s out/{asset} ]" in block


def test_step_block_extractor_stops_at_the_next_sibling_step() -> None:
    """Anchor the scanner itself: one step's body, never the following step's."""
    sample = (
        "    steps:\n"
        "      - name: Alpha step\n"
        "        run: |\n"
        "          echo one\n"
        "\n"
        "      - name: Beta step\n"
        "        run: echo two\n"
    )
    block = _workflow_step_block(sample, "Alpha")
    assert "echo one" in block
    assert "Beta" not in block
    assert _workflow_step_block(sample, "Gamma") == ""


def test_release_staging_gate_covers_every_build_matrix_leg() -> None:
    """The staging gate expects exactly the platforms the ``build`` matrix produces.

    Binding ``EXPECTED_LEGS`` to the matrix in the same file is what makes the gate maintainable: a
    legitimate matrix change (dropping osx-64, adding linux-aarch64) fails here in the required
    3-OS matrix and forces the same PR to update the gate, instead of leaving a silently-wrong
    expected count behind that would only surface at release time.
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    # `- { os: ..., platform: <p> }` flow-mapping entries; requiring the closing brace keeps this
    # off `PLATFORM: ${{ matrix.platform }}` and the `release-${{ matrix.platform }}` artifact name.
    matrix_platforms = set(re.findall(r"platform:\s*([A-Za-z0-9_.-]+)\s*\}", text))
    assert matrix_platforms, "could not parse jobs.build.strategy.matrix platforms from release.yml"

    legs = _expected_legs()
    assert set(legs) == matrix_platforms, (
        "release.yml's staging gate must expect exactly the `build` matrix legs; "
        f"matrix={sorted(matrix_platforms)} EXPECTED_LEGS={sorted(legs)}. If the matrix "
        "legitimately changed, update EXPECTED_LEGS in the staging step of the same file."
    )


def test_release_staging_gate_matches_the_constructor_installer_types() -> None:
    """The gate's per-leg extensions match what ``packaging/construct.yaml`` actually builds.

    ``EXPECTED_LEGS`` has TWO halves with two different authorities: the platform names come from
    the build matrix (guarded above), the installer extensions come from constructor's per-OS
    ``installer_type:`` selectors. Without this guard the extension half is an untested hardcode --
    the classic stale-mapping trap. If a constructor upgrade or a recipe edit changed the Linux
    installer away from ``.sh``, the gate would otherwise fail at release time with the diagnosis
    one step removed from the cause; here it fails in the required matrix, naming both sides.
    """
    recipe = CONSTRUCT_RECIPE.read_text(encoding="utf-8")
    # `installer_type: exe    # [win]` -- conda-build line selectors, so the key repeats per OS.
    by_selector = {
        selector: ext
        for ext, selector in re.findall(r"^installer_type:\s*(\w+)\s*#\s*\[(\w+)\]", recipe, re.M)
    }
    assert by_selector.keys() == {"win", "osx", "linux"}, (
        f"could not parse per-OS `installer_type:` selectors from {CONSTRUCT_RECIPE.name}; "
        f"got {sorted(by_selector)}"
    )
    legs = _expected_legs()
    unknown = sorted(set(legs.values()) - INSTALLER_EXTS)
    assert not unknown, (
        f"installer extensions must be one of {sorted(INSTALLER_EXTS)}; got {unknown}"
    )
    for platform, ext in sorted(legs.items()):
        selector = platform.split("-", 1)[0]  # linux-64 -> linux, osx-arm64 -> osx, win-64 -> win
        assert selector in by_selector, (
            f"EXPECTED_LEGS platform '{platform}' maps to unknown constructor selector "
            f"'{selector}' (expected one of {sorted(by_selector)})"
        )
        assert ext == by_selector[selector], (
            f"EXPECTED_LEGS says '{platform}' produces a .{ext}, but {CONSTRUCT_RECIPE.name} "
            f"builds `installer_type: {by_selector[selector]}` for [{selector}]. "
            "Update whichever is wrong -- they must agree."
        )


def test_both_build_drivers_export_every_extra_files_env_var() -> None:
    """Both installer builds must set every env var ``construct.yaml``'s ``extra_files`` reads.

    ``extra_files`` entries are UNCONDITIONAL: an unset var falls back to a literal
    ``staging/<name>.whl`` that nothing ever creates, and constructor's
    ``preconda.copy_extra_files`` raises ``FileNotFoundError`` on it. packaging.yml (advisory) and
    release.yml (the leg that ships installers to users) duplicate the same build recipe, so a wheel
    added to the recipe and staged in only one of them breaks the other -- and constructor aborts
    before the shared install-smoke (issue #217) could run, so the ``constructor`` build itself is
    the thing that fails. A
    ``workflow_dispatch`` dry run does exercise that build (``dry_run`` defaults true; only the
    signed-tag check and the publish steps are gated on ``publish == 'true'``), so a maintainer who
    dry-runs first would see it -- but nothing on the tag path catches it earlier, and there the
    failure lands mid-release. Same drift class, and same remedy, as
    ``test_every_setup_micromamba_call_site_pins_the_binary`` (issue #212).
    """
    recipe = CONSTRUCT_RECIPE.read_text(encoding="utf-8")
    extra_files = recipe.split("extra_files:", 1)[1].split("\nlicense_file:", 1)[0]
    wanted = sorted(set(re.findall(r'environ\.get\(\s*"(\w+)"', extra_files)))
    assert wanted, f"could not parse `extra_files` env vars from {CONSTRUCT_RECIPE.name}"

    for path in (PACKAGING_WORKFLOW, RELEASE_WORKFLOW):
        text = path.read_text(encoding="utf-8")
        missing = [var for var in wanted if f"{var}=" not in text]
        assert not missing, (
            f"{path.name} builds the installer but never exports {missing}, which "
            f"{CONSTRUCT_RECIPE.name}'s `extra_files` reads unconditionally -- constructor would "
            "abort with FileNotFoundError on the unstaged fallback path. Stage the artifact and "
            "export the var, or drop the `extra_files` entry."
        )


def test_release_staging_gate_is_never_conditional_or_advisory() -> None:
    """The gate must be neither ``if:``-gated nor ``continue-on-error`` -- anywhere in release.yml.

    A ``workflow_dispatch`` dry run skips attest-build-provenance and ``gh release create`` (both
    ``publish == 'true'``), so staging is the ONLY place a missing installer can surface on a
    rehearsal. Conditioning the step restores exactly that blind spot. ``continue-on-error: true``
    is the cheaper mistake: the step still prints its ``::error::`` lines while the job goes GREEN,
    which reads like a working gate in the log. The ``if:`` match is indentation-agnostic on
    purpose -- a fixed-column pattern goes silently vacuous the moment the step is reindented, the
    worst failure mode a contract test can have. (Shell ``if [ ... ]`` lines carry no colon after
    ``if``, so they cannot false-positive.)
    """
    body = _release_staging_step().splitlines()[1:]
    step_if = [ln for ln in body if ln.strip().startswith("if:")]
    assert not step_if, (
        "the release-staging gate must NOT be conditioned on `if:` -- a dry run "
        "(publish != 'true') is exactly the run that has to detect a missing installer, since "
        f"attest-build-provenance and `gh release create` are both publish-gated; got {step_if}"
    )
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "continue-on-error" not in text, (
        "release.yml must not soft-fail any step: a `continue-on-error` step cannot gate anything, "
        "and the staging completeness gate depends on its failure failing the job"
    )
    assert not re.search(r"^  release:\n(?:    .*\n)*?    if:", text, re.M), (
        "the `release` job itself must not be `if:`-gated -- that would skip the gate wholesale"
    )


def test_release_staging_gate_counts_every_installer_kind_and_fails_falsy() -> None:
    """The gate really COMPARES what it staged, per leg and per installer kind, and fails falsy.

    Anti-gutting, in three specific directions:

    * A total-only check is NOT equivalent. The two macOS legs are distinguished only by the arch
      constructor embeds in the .pkg name, and ``merge-multiple: true`` silently overwrites
      same-named files -- so 2 .exe + 1 .pkg + 1 .sh still totals 4 while the Intel-Mac installer
      is missing. Only per-extension counts see that.
    * The comparison SHAPE is asserted, not a mention of ``${#got_*[@]}``: that token also appears
      inside each failure message, so a mention-only assertion stays green when the comparison is
      neutered to ``[ 1 -eq 1 ]``.
    * ``read -ra`` is mandatory. ``shopt -s nullglob`` is enabled in this script, so iterating an
      UNQUOTED ``$EXPECTED_LEGS`` would DELETE any leg word containing a glob metacharacter rather
      than keep it literal -- the gate would then expect fewer installers and pass on an incomplete
      release. This test parses the same string with ``str.split`` (no globbing), so it could not
      detect that divergence itself; the only defence is requiring the glob-safe parse.

    The per-leg ``SHA256SUMS-<platform>.txt`` receipt is an orthogonal net: it is the only
    per-PLATFORM evidence available (installer filenames use constructor's naming, which does not
    map back to a matrix key) and it proves each bundle arrived at the expected depth.
    """
    block = _release_staging_step()
    # Anchored to a non-comment line on purpose: the rationale comment above the parse also
    # contains the words "read -ra", so a plain substring check would stay green with the parse
    # deleted -- the same vacuity trap this test exists to prevent.
    parse = re.search(r'^\s*read -ra (\w+) <<<"\$EXPECTED_LEGS"', block, re.M)
    assert parse, (
        "the gate must parse EXPECTED_LEGS with `read -ra`, not unquoted word splitting: "
        "`shopt -s nullglob` is enabled, so an unquoted leg containing a glob metacharacter is "
        "DELETED rather than left literal -- the gate would silently expect fewer installers and "
        "pass on an incomplete release (fail-open)"
    )
    array = parse.group(1)
    assert re.search(r'^\s*for leg in "\$\{' + array + r'\[@\]\}"; do', block, re.M), (
        f'the leg loop must iterate the quoted array ("${{{array}[@]}}") produced by `read -ra`; '
        "iterating $EXPECTED_LEGS directly reintroduces the nullglob fail-open"
    )
    assert "SHA256SUMS-${platform}.txt" in block, (
        "the gate must require one SHA256SUMS-<platform>.txt per build leg (the arrival receipt)"
    )
    for ext in sorted(INSTALLER_EXTS):
        assert re.search(
            r'\[\s*"\$\{#got_' + ext + r'\[@\]\}"\s*-eq\s*"\$want_' + ext + r'"\s*\]', block
        ), (
            f"the gate must COMPARE the staged .{ext} count against the count derived from "
            "EXPECTED_LEGS (a total-only check passes when a second .exe masks a missing .pkg, "
            "and a bare mention of ${#got_*[@]} inside an error string is not a comparison)"
        )
    assert "::error::" in block, "the gate must annotate its failures with ::error::"
    assert re.search(r"^\s*false\s*$", block, re.M), (
        "the gate must fail via a bare falsy command, never `exit` (which reports exit 1 under "
        "the `bash -el {0}` login shell used elsewhere in this workflow)"
    )


# --- The post-merge check gate (release.yml `verify`, issue #266 / ADR-0057) ---
# Dropping the strict up-to-date rule (2026-07-28) accepted that a semantic conflict between
# two independently-green PRs can land green; the post-merge runs on `main` are the
# compensating detection (ADR-0057), and this gate turns that detection into a release
# control: `verify` fetches the tagged commit's check runs and a committed stdlib-only
# script — scripts/verify_postmerge_checks.py, unit-tested offline in
# tests/test_verify_postmerge_checks.py — owns the verdict AND the disposition. The guards
# here pin the workflow half only: the frozen context list, its binding to jobs that really
# report on `main`, the paginated fetch, the job's permissions, and the fetch-and-pipe split
# that keeps every comparison out of the step.

#: The `main-baseline` ruleset's eleven required contexts, frozen here AND in release.yml's
#: REQUIRED_CONTEXTS. The failure asymmetry is why both copies are pinned: a wrong or stale
#: name does not fail at review time — it blocks every future release with a false "never
#: reported", which only the binding test below can catch early.
REQUIRED_MAIN_CONTEXTS = frozenset(
    {
        "lint",
        "test (ubuntu-latest)",
        "test (macos-latest)",
        "test (windows-latest)",
        "pre-commit",
        "commitlint",
        "secret-scan",
        "conda-lock-verify",
        "docs-build",
        "schema-guard",
        "sidecar / parity",
    }
)


def _postmerge_gate_steps() -> dict[str, dict]:
    """The two gate steps, keyed by job id — `verify` (pre-build) and `release` (recheck).

    Exactly one step per job whose name starts with ``POSTMERGE_STEP_NAME``: zero means the
    gate (or its publish-time recheck) was dropped, two means a duplicate that would let the
    copies drift apart. Both failure modes deserve their own message, so this asserts here
    rather than letting ``next()`` raise ``StopIteration`` in each caller.
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    steps: dict[str, dict] = {}
    for job_id in ("verify", "release"):
        matches = [
            step
            for step in _job_steps(text, job_id)
            if str(step.get("name", "")).startswith(POSTMERGE_STEP_NAME)
        ]
        assert len(matches) == 1, (
            f"release.yml's `{job_id}` job must carry exactly one "
            f"'{POSTMERGE_STEP_NAME}...' step; found {len(matches)} — the verify-time gate "
            "and the publish-time recheck are one step each, or the TOCTOU window reopens"
        )
        steps[job_id] = matches[0]
    return steps


def _postmerge_declared_contexts() -> set[str]:
    """``REQUIRED_CONTEXTS`` from the workflow-level ``env:``, as the set of context names.

    Workflow-level deliberately: the verify-time gate and the publish-time recheck consume
    the ONE copy, so the two invocations cannot drift apart. Blank and ``#``-prefixed lines
    are stripped exactly as the script's ``parse_contexts`` strips them (a YAML block scalar
    keeps such lines as literal content and pipes them to the script), so a commented-out
    context is absent from BOTH sides of this pin, never just one.
    """
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    declared = (workflow.get("env") or {}).get("REQUIRED_CONTEXTS")
    assert isinstance(declared, str), (
        "release.yml must declare the frozen main-baseline context list as a workflow-level "
        "`env: REQUIRED_CONTEXTS: |` block — the single copy both gate steps consume"
    )
    lines = [line.strip() for line in declared.splitlines()]
    return {line for line in lines if line and not line.startswith("#")}


def test_release_postmerge_gate_pins_the_exact_ruleset_contexts() -> None:
    """The committed context list is exactly the `main-baseline` ruleset's eleven.

    The ruleset is the AUTHORITY and it is out-of-band, maintainer-owned state: it lives on
    the repository, not in the tree, so this list is a frozen copy — deliberately not read
    live, or a ruleset edit nobody reviewed would silently steer the release gate. The cost
    of freezing is a sync obligation, stated in ADR-0057: a ruleset change edits BOTH the
    ruleset and release.yml's REQUIRED_CONTEXTS (plus this pin). One context per line in the
    workflow-level env block because a space-separated value cannot represent
    `sidecar / parity`.
    """
    assert _postmerge_declared_contexts() == REQUIRED_MAIN_CONTEXTS, (
        "release.yml's REQUIRED_CONTEXTS must equal the main-baseline ruleset's required "
        "contexts exactly — a missing name silently un-gates that check, an extra or "
        "misspelled one blocks every release with a false 'never reported'. If the ruleset "
        "legitimately changed, update the step and this pin in the same PR."
    )


def test_postmerge_contexts_bind_to_jobs_that_report_on_main_pushes() -> None:
    """Every required context resolves to a job that actually produces it on a `main` push.

    The runtime symptom of a stale or unreachable name is a permanently blocked release,
    not a red test — the guard would wait forever for a context nothing produces. So each
    entry must match a job `name:` in some workflow whose `push` trigger covers `main`
    (`ci.yml`'s `test` job expands over its `matrix.os` into `test (<os>)`). PyYAML parses
    an unquoted ``on:`` key as the boolean ``True`` — the block is read from the parsed
    mapping under that key, which is also why ``_on_block_child_keys`` (an indent scan)
    is not used here: it returns only the immediate child keys, never ``push.branches``.
    """
    produced: dict[str, str] = {}
    for path in sorted((GITHUB_DIR / "workflows").glob("*.y*ml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(workflow, dict):
            continue
        on_block = workflow.get(True)
        push = on_block.get("push") if isinstance(on_block, dict) else None
        branches = push.get("branches") if isinstance(push, dict) else None
        if not isinstance(branches, list) or "main" not in branches:
            continue
        for job_id, job in (workflow.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            name = str(job.get("name", job_id))
            os_values = ((job.get("strategy") or {}).get("matrix") or {}).get("os")
            if isinstance(os_values, list):
                for os_value in os_values:
                    produced[f"{name} ({os_value})"] = path.name
            else:
                produced[name] = path.name

    unbound = sorted(_postmerge_declared_contexts() - set(produced))
    assert not unbound, (
        "every REQUIRED_CONTEXTS entry must be produced by a job in a workflow that "
        f"pushes to main; nothing produces {unbound} — a release would block forever on a "
        "false 'never reported'. Fix the job name, the push trigger, or the list."
    )


def test_postmerge_fetch_paginates_on_a_live_line() -> None:
    """Both gate steps' check-run fetches carry ``--paginate``, on lines that execute.

    A busy `main` commit carries 100+ check runs (the scheduled `triage`/`reap` runs pile
    onto whatever commit is the tip), and even at ``per_page=100`` ten of the eleven
    required contexts are absent from the first page — so an unpaginated fetch refuses
    every release cut from a busy commit with a false "never reported". Anchored to a
    non-comment line because a step's own rationale comment may name ``--paginate``: a bare
    substring check would stay green with the flag deleted.
    """
    for job_id, step in _postmerge_gate_steps().items():
        run = str(step.get("run", ""))
        assert re.search(r"^[ \t]*gh api[^#\n]*--paginate", run, re.M), (
            f"the `{job_id}` gate step must fetch check runs with `gh api ... --paginate` "
            "on a live (non-comment) line; without it ten of the eleven required contexts "
            "are missing from a busy commit's first page and the guard reports a false "
            "'never reported'"
        )


def test_release_verify_declares_both_permissions_and_the_step_token() -> None:
    """Both gate jobs grant the reads the gate needs, and both steps their token.

    A job-level ``permissions:`` block replaces the workflow-level ``contents: read``
    WHOLESALE, so `verify` must restate it beside the new ``checks: read`` or
    ``actions/checkout`` breaks in that job — and `release`, which already declared write
    permissions for the publish steps, must add ``checks: read`` for the recheck or its
    ``gh api`` calls 403. ``GH_TOKEN`` is declared per-step (the signature step set the
    precedent), and ``gh api`` without it dies asking for one.
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    jobs = yaml.safe_load(text)["jobs"]
    verify_permissions = (jobs["verify"] or {}).get("permissions") or {}
    assert verify_permissions.get("contents") == "read", (
        "jobs.verify.permissions must restate `contents: read` — a job-level permissions "
        "block replaces the workflow default wholesale, and checkout needs the read"
    )
    for job_id in ("verify", "release"):
        permissions = (jobs[job_id] or {}).get("permissions") or {}
        assert permissions.get("checks") == "read", (
            f"jobs.{job_id}.permissions must grant `checks: read` — its post-merge gate "
            "step reads the tagged commit's check runs"
        )
    for job_id, step in _postmerge_gate_steps().items():
        assert (step.get("env") or {}).get("GH_TOKEN") == "${{ github.token }}", (
            f"the `{job_id}` gate step must declare `env: GH_TOKEN: ${{{{ github.token }}}}`"
            " — GH_TOKEN is set per-step, never job-wide, and `gh api` requires it"
        )


def test_postmerge_step_is_fetch_and_pipe_passing_publish_verbatim() -> None:
    """Both gate steps evaluate on every run and hold no second copy of the disposition.

    Three properties per step, each with a distinct failure mode: no ``if:`` at all, so a
    dry run still evaluates and reports (the M9 milestone needs the rehearsal sighted); the
    raw publish output expression (``steps.resolve`` in `verify`, ``needs.verify`` in
    `release`) handed through an env var to ``--publish-mode`` VERBATIM, so the committed
    script — not a workflow expression — is the single place that interprets it (a
    ``== 'true'`` comparison here would silently turn a typo'd value into advisory, the
    fail-open the script exists to close); and no shell conditional over that variable
    inside the step, so the disposition cannot grow an untested duplicate at the call site.
    """
    publish_expressions = {
        "verify": "${{ steps.resolve.outputs.publish }}",
        "release": "${{ needs.verify.outputs.publish }}",
    }
    conditional = re.compile(r"^\s*(?:(?:el)?if\b|\[\[?|test\b|case\b)")
    for job_id, step in _postmerge_gate_steps().items():
        expression = publish_expressions[job_id]
        assert "if" not in step, (
            f"the `{job_id}` gate step must declare no `if:` — it evaluates on every run, "
            f"dry runs included, and the script owns the disposition; got {step.get('if')!r}"
        )
        publish_vars = [
            key
            for key, value in (step.get("env") or {}).items()
            if str(value).strip() == expression
        ]
        assert publish_vars, (
            f"the `{job_id}` gate step's env must map a variable to the exact expression "
            f"`{expression}` — comparing or rewriting it in the workflow would put a "
            "second, untested copy of the disposition at the call site"
        )
        run = step.get("run")
        assert isinstance(run, str), f"the `{job_id}` gate step must carry a `run:` script"
        variable = publish_vars[0]
        assert re.search(r'--publish-mode\s+"\$' + re.escape(variable) + '"', run), (
            f"the `{job_id}` gate step must pass ${variable} to --publish-mode verbatim; "
            "the script — not the step — decides what the value means"
        )
        offenders = [
            line
            for line in run.splitlines()
            if not line.lstrip().startswith("#")
            and f"${variable}" in line
            and conditional.search(line)
        ]
        assert not offenders, (
            f"no line of the `{job_id}` gate step may both name ${variable} and be a shell "
            f"conditional — that is a second, untested disposition rule at the call site: "
            f"{offenders}"
        )


def test_publish_time_recheck_sits_flush_against_the_publish_steps() -> None:
    """`release` runs guard-checkout → attest → recheck → Release-create consecutively.

    The recheck exists to close the TOCTOU window between the verify-time gate and the
    publish (the nightly sidecar cron lands new `sidecar / parity` suites on the main tip
    while the ~30-60 minute build matrix runs), and NOTHING may execute between it and
    `gh release create` — the residual window must stay the recheck's own runtime, since
    no GitHub primitive binds Release creation to unchanged check state atomically. The
    attestation therefore comes BEFORE the recheck, not after: attesting artifacts that
    are then never released distributes nothing, while running it between the verdict and
    the publish would widen the window by its runtime. The scoped guard-script checkout
    must also sit flush against the group and AFTER the SBOM step: it materializes a
    second tree at the workflow's own commit (a pre-gate tag's tree has no script, so
    resolving it from the main checkout would fail every dry-run rehearsal on a missing
    file), and sbom-action scans ``path: .``, so hoisting the checkout above the SBOM
    would leak that mixed-version tree into the published bill of materials.
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    names = [str(step.get("name", "")) for step in _job_steps(text, "release")]

    def index_of(prefix: str) -> int:
        matches = [i for i, name in enumerate(names) if name.startswith(prefix)]
        assert len(matches) == 1, (
            f"the `release` job must carry exactly one step named '{prefix}...'; "
            f"found {len(matches)}"
        )
        return matches[0]

    checkout = index_of("Check out the guard script")
    recheck = index_of(POSTMERGE_STEP_NAME)
    attest = index_of("Attest build provenance")
    create = index_of("Create the GitHub Release")
    assert [attest, recheck, create] == [checkout + 1, checkout + 2, checkout + 3], (
        "the `release` steps must run guard-checkout → attest → recheck → Release-create, "
        "consecutively — anything between the recheck and the publish (the attestation "
        "included) widens the TOCTOU window the recheck exists to close; got indices "
        f"checkout={checkout}, attest={attest}, recheck={recheck}, create={create}"
    )
    assert index_of("Generate a CycloneDX SBOM") < checkout, (
        "the guard-script checkout must come after the SBOM step — sbom-action scans "
        "`path: .` and would inventory the second, workflow-commit tree into the released "
        "bill of materials"
    )


# --- setup-micromamba: one action version, one binary version, and Dependabot can see both ---
# Three separate silent-drift traps converge on this action, and none is watched by anything else:
#
#  1. `micromamba-version` defaults to `latest`, so the BINARY that restores the pin-and-held
#     conda-locks re-resolves on every run. Upstream setup-micromamba#306 (STATUS_ACCESS_VIOLATION
#     on windows; fixed in micromamba 2.6.2) and #307 (sharded repodata, fixed in 2.7.0) both
#     turned FLOATING consumers red with no repo-side change. Here that lands on all three required
#     `test` legs plus schema-guard at once, on an unrelated PR.
#  2. Dependabot's `github-actions` ecosystem with `directory: "/"` scans only `.github/workflows`
#     plus a ROOT-level action.yml -- NOT `.github/actions/**` (dependabot-core#6345, still open).
#     That is how `.github/actions/setup-env` sat on setup-micromamba v2 while packaging.yml and
#     release.yml were bumped to v3.0.0: the composite action was invisible to the bot.
#  3. Closing (2) took a SECOND `github-actions` entry in .github/dependabot.yml -- group `actions`
#     for `/` (.github/workflows) and group `actions-composite` for `/.github/actions/*` -- and two
#     entries open two pull requests for one action bump. Merge one without the other and the
#     ACTION SHA splits across call sites with CI fully green: #446 alone would have left the
#     setup-env composite on v3.1.0 while packaging.yml and release.yml ran v3.2.1 (#448).
#
# These guards keep all three closed: every call site pins the same binary AND runs the same
# action SHA under the same `# v<X.Y.Z>` tag comment, and every composite action on disk is inside
# some Dependabot update entry's scope.
GITHUB_DIR = Path(__file__).resolve().parents[1] / ".github"
DEPENDABOT_CONFIG = GITHUB_DIR / "dependabot.yml"
SETUP_MICROMAMBA = "mamba-org/setup-micromamba@"

#: The pinned micromamba binary. A `mamba-org/micromamba-releases` TAG, build suffix INCLUDED:
#: setup-micromamba interpolates the value verbatim into
#: `releases/download/<tag>/micromamba-<arch>`, and its input regex rejects a bare `2.8.1`.
#: Bump here first, then every call site -- the guard below requires them to agree.
MICROMAMBA_PIN = "2.8.1-0"

#: The trailing `# v<X.Y.Z>` tag comment on a `uses: mamba-org/setup-micromamba@<sha>` line,
#: quoted or not. Read from TEXT because a YAML parse discards comments; counted against the
#: PARSED call sites per file, so a spelling this misses fails instead of vanishing (#510).
#: Horizontal whitespace only around the ``#``: ``\s`` would cross the newline and accept a
#: tag sitting on the NEXT line as this ``uses:`` line's trailing comment.
_SETUP_MICROMAMBA_TAG = re.compile(
    r"^\s*(?:-\s*)?uses:\s*[\"']?"
    + re.escape(SETUP_MICROMAMBA)
    + r"[^\s\"'#]*[\"']?[ \t]+#[ \t]*(v\d+\.\d+\.\d+)\b",
    re.M,
)


def _yaml_load(path: Path) -> object:
    """Parse *path* as YAML. Local import, matching tests/test_adr_index.py's precedent."""
    import yaml  # provided by the base conda-lock (a mkdocs dependency)

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _workflow_or_action_steps(document: object) -> list[dict]:
    """Every step of a parsed workflow (``jobs.*.steps``) OR composite action (``runs.steps``).

    :func:`_steps` reads ``jobs`` only, and ``.github/actions/setup-env/action.yml`` has no
    ``jobs:`` key at all (its top level is ``name``/``description``/``inputs``/``runs``) -- a
    lookup that stopped there would parse, look right, and never see the composite call site
    this section mostly exists for.
    """
    if not isinstance(document, dict):
        return []
    containers = [
        job.get("steps") for job in (document.get("jobs") or {}).values() if isinstance(job, dict)
    ]
    runs = document.get("runs")
    if isinstance(runs, dict):
        containers.append(runs.get("steps"))
    return [step for steps in containers for step in (steps or []) if isinstance(step, dict)]


def _setup_micromamba_steps(text: str) -> list[dict]:
    """Every ``setup-micromamba`` step in one document, from the PARSED steps.

    A parse, not a regex: the old line count missed the valid quoted spelling and counted a
    ``run:`` block scalar that merely contained the text -- the same two false-greens
    :func:`_steps` records against the attestation guard (#319).
    """
    return [
        step
        for step in _workflow_or_action_steps(yaml.safe_load(text))
        if isinstance(step.get("uses"), str) and step["uses"].startswith(SETUP_MICROMAMBA)
    ]


def _setup_micromamba_uses(text: str) -> list[str]:
    """Every ``setup-micromamba`` ``uses:`` value in one document, one per parsed call site."""
    return [step["uses"] for step in _setup_micromamba_steps(text)]


#: A full commit SHA, the only immutable ref: a branch or tag ref can move under the guard.
_FULL_SHA = re.compile(r"[0-9a-f]{40}")


class _MicromambaCallSites(NamedTuple):
    """One file's ``setup-micromamba`` call sites."""

    uses: list[str]  # parsed `uses:` values, one per call site
    pins: list[str | None]  # each call site's OWN `with: micromamba-version`, None if unset
    tags: list[str]  # trailing `# v<X.Y.Z>` comments on the `uses:` lines (text)

    @property
    def shas(self) -> list[str]:
        return [value.split("@", 1)[1] for value in self.uses]


def _micromamba_call_sites_in(text: str) -> _MicromambaCallSites:
    """One document's call sites, each pin read from that step's own ``with:`` mapping.

    Never from pin-shaped lines anywhere in the file: a call site with no
    ``micromamba-version`` floats to ``latest``, and a matching line in some other step's
    ``run:`` block must not stand in for it.
    """
    steps = _setup_micromamba_steps(text)
    pins: list[str | None] = []
    for step in steps:
        inputs = step.get("with")
        value = inputs.get("micromamba-version") if isinstance(inputs, dict) else None
        pins.append(None if value is None else str(value))
    return _MicromambaCallSites(
        [step["uses"] for step in steps], pins, _SETUP_MICROMAMBA_TAG.findall(text)
    )


def _mutable_refs(call_sites: dict[str, _MicromambaCallSites]) -> list[str]:
    """``path: ref`` for every call site whose ref is not a full 40-hex commit SHA."""
    return sorted(
        f"{name}: {sha}"
        for name, site in call_sites.items()
        for sha in site.shas
        if not _FULL_SHA.fullmatch(sha)
    )


def _setup_micromamba_call_sites() -> dict[str, _MicromambaCallSites]:
    """``{repo-relative path: that file's setup-micromamba call sites}``, for every call site.

    Discovered by GLOB over ``.github/``, never a hardcoded file list: this repo grew from one
    call site to three, and a hardcoded list would let a fourth float to ``latest`` unnoticed --
    reintroducing exactly the drift these guards exist to prevent. The glob reaches workflows
    and ``.github/actions/*/action.yml`` alike; :func:`_setup_micromamba_uses` reads both step
    containers, so a fourth site in either is covered without editing any list.
    """
    found: dict[str, _MicromambaCallSites] = {}
    for path in sorted(GITHUB_DIR.rglob("*.y*ml")):
        site = _micromamba_call_sites_in(path.read_text(encoding="utf-8"))
        if site.uses:
            found[path.relative_to(GITHUB_DIR).as_posix()] = site
    return found


def test_the_setup_micromamba_extraction_reads_steps_and_nothing_else() -> None:
    """The regression test for the extraction itself, since its failure mode is staying green.

    Every guard in this section stands on :func:`_setup_micromamba_uses` finding every call
    site and nothing else. The line regex it replaced got two of these four rows wrong -- (b)
    counted zero and (c) counted one -- and a ``jobs:``-only parse gets (d) wrong, counting the
    composite action as no call site at all while ``len(call_sites) >= 3`` stayed satisfiable
    only by luck. All four are asserted together so a regression names every row it breaks.

    (a) a plain workflow step counts once;
    (b) the quoted spelling -- valid YAML, and the form that made the attestation guard
        false-green -- counts once;
    (c) the same text inside a ``run:`` block scalar is a line of shell, not a step: zero;
    (d) a composite action whose only top-level keys are ``name`` and ``runs``, with the call
        site under ``runs.steps``, counts once.
    """
    sha = "0123456789abcdef0123456789abcdef01234567"
    call = f"{SETUP_MICROMAMBA}{sha}"
    rows = {
        "a: plain workflow step": f"jobs:\n  build:\n    steps:\n      - uses: {call}\n",
        "b: quoted workflow step": f'jobs:\n  build:\n    steps:\n      - uses: "{call}"\n',
        "c: run block scalar": (
            "jobs:\n"
            "  build:\n"
            "    steps:\n"
            "      - name: Not a call site\n"
            "        run: |\n"
            f"          uses: {call}\n"
        ),
        "d: composite runs.steps": (
            f"name: setup-env\nruns:\n  using: composite\n  steps:\n    - uses: {call}\n"
        ),
    }
    expected = {
        "a: plain workflow step": 1,
        "b: quoted workflow step": 1,
        "c: run block scalar": 0,
        "d: composite runs.steps": 1,
    }
    counts = {row: len(_setup_micromamba_uses(text)) for row, text in rows.items()}
    assert counts == expected


def test_a_call_site_pin_is_read_from_its_own_with_mapping() -> None:
    """A pin counts only under its own step's ``with:``, never as a pin-shaped line elsewhere.

    The unpinned call site below floats to ``latest``; the ``run:`` block of the next step
    carries the exact pin text, which a file-wide line count accepted as its pin (#502).
    """
    sha = "0123456789abcdef0123456789abcdef01234567"
    unpinned = (
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        f"      - uses: {SETUP_MICROMAMBA}{sha} # v1.2.3\n"
        "      - run: |\n"
        f"          echo micromamba-version: {MICROMAMBA_PIN}\n"
    )
    pinned = (
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        f"      - uses: {SETUP_MICROMAMBA}{sha} # v1.2.3\n"
        "        with:\n"
        f"          micromamba-version: '{MICROMAMBA_PIN}'\n"
    )
    assert _micromamba_call_sites_in(unpinned).pins == [None]
    assert _micromamba_call_sites_in(pinned).pins == [MICROMAMBA_PIN]


def test_the_version_tag_must_trail_its_own_uses_line() -> None:
    """A ``# v<X.Y.Z>`` on the line after ``uses:`` is not that call site's trailing tag.

    With ``\\s`` around the ``#`` the tag regex crossed the newline, so a one-call-site file
    whose tag had moved down a line still parsed one tag per call site and passed (#502).
    """
    sha = "0123456789abcdef0123456789abcdef01234567"
    trailing = f"jobs:\n  build:\n    steps:\n      - uses: {SETUP_MICROMAMBA}{sha} # v1.2.3\n"
    next_line = (
        f"jobs:\n  build:\n    steps:\n      - uses: {SETUP_MICROMAMBA}{sha}\n        # v1.2.3\n"
    )
    assert _micromamba_call_sites_in(trailing).tags == ["v1.2.3"]
    assert _micromamba_call_sites_in(next_line).tags == []


def test_a_mutable_action_ref_is_not_a_pin() -> None:
    """Call sites agreeing on ``@main`` or a tag still fail: only a 40-hex SHA is immutable."""
    sha = "0123456789abcdef0123456789abcdef01234567"
    sites = {
        name: _MicromambaCallSites([f"{SETUP_MICROMAMBA}{ref}"], [MICROMAMBA_PIN], ["v1.2.3"])
        for name, ref in {"a.yml": "main", "b.yml": "v2", "c.yml": sha, "d.yml": sha[:7]}.items()
    }
    assert _mutable_refs(sites) == ["a.yml: main", "b.yml: v2", f"d.yml: {sha[:7]}"]


def test_every_setup_micromamba_call_site_pins_the_binary() -> None:
    """Every ``setup-micromamba`` call site pins ``micromamba-version``, all to the same value.

    Unset, the input defaults to ``latest`` and re-resolves the binary at run time. Pin-and-hold
    (PRD §4.1) covers the tool that MATERIALISES an environment, not only the locks it reads --
    and Dependabot watches ``uses:`` SHAs, never ``with:`` input VALUES, so nothing else is
    looking at this.

    One shared value keeps it to a single reviewed knob. To be precise about what that does NOT
    buy: only ``.github/actions/setup-env`` restores a conda-lock with micromamba.
    packaging.yml/release.yml pass no ``environment-file`` -- they SOLVE a throwaway ``pkgbuild``
    tool env from ``create-args``, and the shipped installer's environments are rendered by
    ``conda-lock render`` and materialised by constructor/conda-standalone. Agreement here is
    maintenance convenience (one number to bump), not a CI-matches-the-signed-release guarantee.
    """
    assert re.fullmatch(r"\d+\.\d+\.\d+-\d+", MICROMAMBA_PIN), (
        "the pin must be a plain micromamba-releases tag INCLUDING the build suffix (e.g. "
        "2.8.1-0): setup-micromamba rejects a bare `2.8.1`, and its input regex also admits "
        "rc/alpha/beta/dev tags, which a pin-and-hold release toolchain must never ship"
    )

    call_sites = _setup_micromamba_call_sites()
    assert len(call_sites) >= 3, (
        "expected at least the setup-env composite + packaging.yml + release.yml to call "
        f"setup-micromamba; found {sorted(call_sites)}. If a call site was removed, update this "
        "guard deliberately."
    )

    mismatched = sorted(
        f"{name} ({site.pins.count(None)} of {len(site.uses)} call site(s) unpinned)"
        for name, site in call_sites.items()
        if None in site.pins
    )
    assert not mismatched, (
        "every setup-micromamba call site needs its OWN `micromamba-version` pin -- unset, the "
        f"input floats to `latest` and re-resolves the binary at run time: {mismatched}"
    )

    values = {value for site in call_sites.values() for value in site.pins if value is not None}
    assert values == {MICROMAMBA_PIN}, (
        f"all setup-micromamba call sites must pin micromamba-version to {MICROMAMBA_PIN!r} "
        f"(bump MICROMAMBA_PIN and every call site in one commit); found {sorted(values)} "
        f"across {sorted(call_sites)}"
    )


def test_every_setup_micromamba_call_site_runs_the_same_action_version() -> None:
    """Every ``setup-micromamba`` call site runs ONE action SHA under ONE ``# v<X.Y.Z>`` tag.

    The pin test above guards the BINARY; nothing else in the repository compares the ACTION
    SHA across call sites, and Dependabot is what moves it -- through two update entries, so two
    pull requests per bump (trap 3 in the section header). Merging one of the pair alone leaves
    the composite action and the workflows on different versions of the action with every
    check green, which is precisely the state #446 would have produced without #445.

    The trailing tag comment is asserted too: a parse discards comments, so it is read from the
    text, and the file must carry as many tags as it has PARSED call sites -- an unannotated
    ``uses:`` line fails here rather than being silently skipped. The count is per file, so it
    does not tie each tag to its own call site's line; #510 tracks that. Every ref must be a
    full 40-hex commit SHA before agreement is checked: call sites that all moved to ``@main``
    would otherwise agree on a mutable ref.
    """
    call_sites = _setup_micromamba_call_sites()
    assert len(call_sites) >= 3, (
        "expected at least the setup-env composite + packaging.yml + release.yml to call "
        f"setup-micromamba; found {sorted(call_sites)}"
    )

    mutable = _mutable_refs(call_sites)
    assert not mutable, (
        "every setup-micromamba call site must pin the action to a full 40-hex commit SHA -- a "
        f"branch or tag ref can move without any change here: {mutable}"
    )

    untagged = sorted(
        f"{name} ({len(site.tags)} `# v<X.Y.Z>` comment(s) for {len(site.uses)} call site(s))"
        for name, site in call_sites.items()
        if len(site.tags) != len(site.uses)
    )
    assert not untagged, (
        "every `uses: mamba-org/setup-micromamba@<sha>` line needs its own trailing "
        f"`# v<X.Y.Z>` tag comment, so a reviewer can read which release the SHA is: {untagged}"
    )

    shas = {
        sha: sorted(name for name, site in call_sites.items() if sha in site.shas)
        for sha in {sha for site in call_sites.values() for sha in site.shas}
    }
    assert len(shas) == 1, (
        "all setup-micromamba call sites must run the SAME action SHA; found "
        f"{shas}. The usual cause is a split Dependabot bump: .github/dependabot.yml has two "
        "`github-actions` entries -- group `actions` for `/` (.github/workflows) and group "
        "`actions-composite` for `/.github/actions/*` -- so one action bump arrives as TWO pull "
        "requests, and they must land together. One merged without the other splits the "
        "versions; land the sibling too, or bump the lagging call site by hand."
    )

    tags = {
        tag: sorted(name for name, site in call_sites.items() if tag in site.tags)
        for tag in {tag for site in call_sites.values() for tag in site.tags}
    }
    assert len(tags) == 1, (
        "all setup-micromamba call sites must carry the SAME trailing `# v<X.Y.Z>` tag "
        f"comment; found {tags}. A bump that moved the SHA but not the comment (or the reverse) "
        "leaves the next reviewer reading the wrong release."
    )


def _dependabot_github_actions_directories() -> list[str]:
    """Every directory pattern the ``github-actions`` ecosystem entries point Dependabot at."""
    config = _yaml_load(DEPENDABOT_CONFIG)
    dirs: list[str] = []
    for entry in config["updates"]:
        if entry.get("package-ecosystem") != "github-actions":
            continue
        assert not ("directory" in entry and "directories" in entry), (
            "`directory` and `directories` are mutually exclusive within one update entry"
        )
        if "directory" in entry:
            value = str(entry["directory"])
            assert "*" not in value, (
                "globbing is supported only by the plural `directories` key; "
                f"`directory: {value!r}` is treated as a literal path and would match nothing"
            )
            dirs.append(value)
        dirs.extend(str(d) for d in entry.get("directories", []))
    assert dirs, ".github/dependabot.yml must define at least one `github-actions` update entry"
    return dirs


def test_dependabot_watches_every_composite_action() -> None:
    """Every composite action under ``.github/actions/`` is inside some update entry's scope.

    ``directory: "/"`` is NOT repo-wide for this ecosystem -- Dependabot searches
    ``/.github/workflows`` plus a ROOT-level ``action.yml`` only -- so SHA pins inside
    ``.github/actions/<name>/action.yml`` are never bumped (dependabot-core#6345). Coverage is
    restored by a second ``github-actions`` entry using ``directories:`` (the only key that
    supports globbing). This guard fails the moment a composite action exists that no entry
    covers, so the gap cannot be reintroduced by adding an action and forgetting the config.

    Matching uses :meth:`PurePosixPath.match`, NOT :func:`fnmatch.fnmatch`: Dependabot expands
    these patterns with Ruby's ``Dir.glob``, where ``*`` does not cross a ``/``. ``fnmatch`` lets
    ``*`` cross separators and would report a NESTED action (``.github/actions/a/b/action.yml``)
    as covered when Dependabot would in fact skip it -- a false pass on precisely this bug.
    """
    repo_root = GITHUB_DIR.parent
    actions = sorted(
        {
            "/" + manifest.parent.relative_to(repo_root).as_posix()
            for name in ("action.yml", "action.yaml")
            for manifest in (GITHUB_DIR / "actions").rglob(name)
        }
    )
    assert actions, "expected at least one composite action under .github/actions/"

    declared = _dependabot_github_actions_directories()
    uncovered = [
        action
        for action in actions
        if not any(PurePosixPath(action).match(pattern) for pattern in declared)
    ]
    assert not uncovered, (
        "composite action(s) hold SHA-pinned `uses:` that Dependabot will never bump "
        f"(dependabot-core#6345): {uncovered}. Configured directories: {declared}. Note "
        '`directory: "/"` covers ONLY /.github/workflows + a ROOT action.yml -- add these paths '
        "to a `directories:` entry, or widen its glob (`*` does not cross a `/`)."
    )


def test_no_dependabot_glob_overlaps_the_workflows_directory() -> None:
    """No ``github-actions`` glob may re-match ``/.github/workflows`` (the duplicate-PR trap).

    ``.github/workflows`` is already scanned implicitly by the ``directory: "/"`` entry. A second
    entry whose glob also matches it violates the documented "no overlap in directories defined"
    rule for multiple blocks of one ecosystem and produces duplicate PRs for every action
    (dependabot-core#10884, where ``directories: ['**/*']`` did exactly that). This is why the
    glob is ``/.github/actions/*`` and not ``**/*``.
    """
    for pattern in _dependabot_github_actions_directories():
        if pattern == "/":
            continue
        assert not PurePosixPath("/.github/workflows").match(pattern), (
            f"dependabot.yml glob {pattern!r} re-matches /.github/workflows, which the "
            '`directory: "/"` entry already scans implicitly -> duplicate PRs for every action '
            "(dependabot-core#10884). Keep non-root globs disjoint from `/`."
        )


# --- The workstation tier runs in NO CI job, on purpose (ADR-0071, #698) ---
# A `workstation` test needs private laboratory data. No runner has that data, and no GitHub
# self-hosted runner is registered for it: `scripts/workstation/run_private_checks.py` runs these
# tests from a scheduled task on the maintainer's workstation. So every pytest command in every
# workflow and composite action must deselect them. `tests/conftest.py` adds the other half: a
# workstation test carries no other tier marker, so `-m large` or `-m sidecar` cannot reach one.
WORKSTATION_RUNNER = GITHUB_DIR.parent / "scripts" / "workstation" / "run_private_checks.py"
_PYTEST_COMMAND = re.compile(r"(?:^|[\s;&|(])(?:python\s+-m\s+)?pytest(?=\s|$)")


def _selects(expression: str, marks: set[str]) -> bool:
    """Evaluate a pytest ``-m`` expression (``and``/``or``/``not``/parentheses) for *marks*."""
    tokens = re.findall(r"\(|\)|[\w.-]+", expression)
    position = 0

    def take() -> str:
        nonlocal position
        position += 1
        return tokens[position - 1]

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def either() -> bool:
        value = both()
        while peek() == "or":
            take()
            value = both() or value
        return value

    def both() -> bool:
        value = negation()
        while peek() == "and":
            take()
            value = negation() and value
        return value

    def negation() -> bool:
        if peek() == "not":
            take()
            return not negation()
        if peek() == "(":
            take()
            value = either()
            assert take() == ")", expression
            return value
        return take() in marks

    value = either()
    assert peek() is None, f"unparsed rest of {expression!r}"
    return value


def test_the_marker_expression_evaluator_follows_pytest_precedence() -> None:
    assert _selects("workstation", {"workstation"})
    assert not _selects("not large and not workstation", {"workstation"})
    assert _selects("not large and not sidecar and not deep", {"workstation"})
    assert not _selects("large", {"workstation", "gui"})
    assert _selects("large or workstation and gui", {"large"})  # `and` binds tighter
    assert not _selects("(large or workstation) and gui", {"workstation"})
    assert _selects("not (large or deep)", {"workstation"})


def _workflow_pytest_commands() -> list[tuple[str, str]]:
    """(file, command) for every pytest command in every workflow and composite action."""
    commands = []
    documents = sorted(GITHUB_DIR.glob("workflows/*.yml"))
    documents += sorted(GITHUB_DIR.glob("actions/*/*.yml"))
    for path in documents:
        for step in _workflow_or_action_steps(_yaml_load(path)):
            script = step.get("run")
            if not isinstance(script, str):
                continue
            for line in re.sub(r"\\\n", " ", script).splitlines():
                if not line.lstrip().startswith("#") and _PYTEST_COMMAND.search(line):
                    commands.append((path.name, line.strip()))
    return commands


def test_no_workflow_selects_a_workstation_test() -> None:
    """Every pytest command deselects ``workstation``, alone or with ``gui``.

    A command with ``-m`` must evaluate false for such a test; a command without one must name
    only test files that apply no ``workstation`` marker. If this fails, add ``and not
    workstation`` to that command's ``-m`` expression: these tests run only from
    ``scripts/workstation/run_private_checks.py`` (docs/maintainers/workstation-runner.md).
    """
    commands = _workflow_pytest_commands()
    offenders = []
    for name, command in commands:
        argv = shlex.split(command)
        expressions = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "-m"]
        expressions = [e for e in expressions if e != "pytest"]  # `python -m pytest`
        if expressions:
            tiers = ({"workstation"}, {"workstation", "gui"})
            if any(_selects(e, marks) for e in expressions for marks in tiers):
                offenders.append(f"{name}: {command}")
            continue
        paths = [arg for arg in argv if arg.startswith("tests/")]
        files = [f for p in paths for f in sorted(GITHUB_DIR.parent.glob(p.split("::")[0]))]
        sources = [f.read_text(encoding="utf-8") for f in files]
        if not files or any(_uses_marker(source, "workstation") for source in sources):
            offenders.append(f"{name}: {command}")
    runner = WORKSTATION_RUNNER.relative_to(GITHUB_DIR.parent).as_posix()
    assert not offenders, (
        "these CI pytest commands can select a `workstation` test, which needs private data and "
        f"runs only from {runner}: {offenders}"
    )
    # Anchor against a scan that finds nothing: the required 3-OS matrix runs two spellings.
    matrix = [cmd for name, cmd in commands if name == "ci.yml" and "not workstation" in cmd]
    assert len(matrix) == 2, commands


def test_the_workstation_tier_is_registered_and_its_runner_selects_it() -> None:
    import tomllib

    pyproject = tomllib.loads((GITHUB_DIR.parent / "pyproject.toml").read_text(encoding="utf-8"))
    markers = pyproject["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("workstation:") for m in markers), markers
    source = WORKSTATION_RUNNER.read_text(encoding="utf-8")
    assert '"pytest", "-m", "workstation"' in source
    selfcheck = TESTS_DIR / "test_workstation_selfcheck.py"
    assert _uses_marker(selfcheck.read_text(encoding="utf-8"), "workstation")
