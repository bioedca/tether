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
import re
from pathlib import Path

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
    (and this contract) to keep the two in lockstep.
    """
    offenders = []
    detected = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if _uses_sidecar_marker(path.read_text(encoding="utf-8")):
            detected.append(path.name)
            if not fnmatch.fnmatch(path.name, SIDECAR_FILE_GLOB):
                offenders.append(path.name)

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
    pytest_lines = [ln for ln in workflow.splitlines() if "pytest -m sidecar" in ln]
    assert pytest_lines, "sidecar.yml must run `pytest -m sidecar`"
    globs = re.findall(r"tests/(\S+\.py)", " ".join(pytest_lines))
    assert globs == [SIDECAR_FILE_GLOB], (
        f"sidecar.yml's `pytest -m sidecar` must collect exactly "
        f"'tests/{SIDECAR_FILE_GLOB}'; found: {globs}"
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


def _uses_marker(source: str, marker: str) -> bool:
    """True if *source* applies ``pytest.mark.<marker>`` in code (not in a string/comment).

    The AST generalization of :func:`_uses_sidecar_marker`: matches the ``pytest.mark.<marker>``
    attribute chain as real syntax (a ``pytestmark`` assignment or a decorator), so docstring or
    comment mentions are ignored.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == marker
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "pytest"
        ):
            return True
    return False


def test_deep_marker_detector_matches_the_sidecar_detector() -> None:
    # The generalized detector agrees with the hand-written sidecar one on sidecar sources.
    assert _uses_marker("import pytest\npytestmark = pytest.mark.deep\n", "deep")
    assert _uses_marker("@pytest.mark.deep\ndef test_x():\n    pass\n", "deep")
    assert not _uses_marker('"""see ``@pytest.mark.deep`` for the live job"""\n', "deep")
    assert not _uses_marker("import pytest\npytestmark = pytest.mark.sidecar\n", "deep")


def test_deep_marked_modules_match_ci_glob() -> None:
    """Every ``@pytest.mark.deep`` module is collected by the deep.yml glob.

    If this fails, either rename the offending file to match ``test_*_deep.py`` or widen the glob
    in ``.github/workflows/deep.yml`` (and this contract) to keep the two in lockstep.
    """
    offenders = []
    detected = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if _uses_marker(path.read_text(encoding="utf-8"), "deep"):
            detected.append(path.name)
            if not fnmatch.fnmatch(path.name, DEEP_FILE_GLOB):
                offenders.append(path.name)

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
