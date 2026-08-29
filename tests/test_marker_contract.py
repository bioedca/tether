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
from pathlib import Path, PurePosixPath

import yaml  # provided by the base conda-lock (a mkdocs dependency); see `_steps` below

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


def _is_live(step: dict) -> bool:
    """False only when the step is switched off by a constant, not by a runtime gate."""
    condition = step.get("if")
    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition
    return str(condition).strip().lower() not in _STATICALLY_DISABLED


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
    the script's FINAL exit status — and both platform branches deliberately END in
    non-fatal commands (the Windows Start Menu check returns 0 by design; the Unix branch
    ends in an ``if`` block that is simply false on macOS) — so a script without
    ``set -e`` exits 0 on Windows and macOS no matter which probe above failed: a
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
# Two separate silent-drift traps converge on this action, and neither is watched by anything else:
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
#
# These guards keep both closed: every call site pins the same binary, and every composite action
# on disk is inside some Dependabot update entry's scope.
GITHUB_DIR = Path(__file__).resolve().parents[1] / ".github"
DEPENDABOT_CONFIG = GITHUB_DIR / "dependabot.yml"
SETUP_MICROMAMBA = "mamba-org/setup-micromamba@"

#: The pinned micromamba binary. A `mamba-org/micromamba-releases` TAG, build suffix INCLUDED:
#: setup-micromamba interpolates the value verbatim into
#: `releases/download/<tag>/micromamba-<arch>`, and its input regex rejects a bare `2.8.1`.
#: Bump here first, then every call site -- the guard below requires them to agree.
MICROMAMBA_PIN = "2.8.1-0"


def _yaml_load(path: Path) -> object:
    """Parse *path* as YAML. Local import, matching tests/test_adr_index.py's precedent."""
    import yaml  # provided by the base conda-lock (a mkdocs dependency)

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _setup_micromamba_call_sites() -> dict[str, tuple[int, list[str]]]:
    """``{repo-relative path: (call-site count, declared micromamba-version values)}``.

    Discovered by GLOB over ``.github/``, never a hardcoded file list: this repo grew from one
    call site to three, and a hardcoded list would let a fourth float to ``latest`` unnoticed --
    reintroducing exactly the drift these guards exist to prevent.
    """
    found: dict[str, tuple[int, list[str]]] = {}
    for path in sorted(GITHUB_DIR.rglob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        uses = len(re.findall(r"^\s*(?:-\s*)?uses:\s*" + re.escape(SETUP_MICROMAMBA), text, re.M))
        if not uses:
            continue
        pins = re.findall(r"^\s*micromamba-version:\s*[\"']?([^\"'\s#]+)", text, re.M)
        found[path.relative_to(GITHUB_DIR).as_posix()] = (uses, pins)
    return found


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
        f"{name} ({len(pins)} pin(s) for {uses} call site(s))"
        for name, (uses, pins) in call_sites.items()
        if len(pins) != uses
    )
    assert not mismatched, (
        "every setup-micromamba call site needs its OWN `micromamba-version` pin -- unset, the "
        f"input floats to `latest` and re-resolves the binary at run time: {mismatched}"
    )

    values = {value for _, pins in call_sites.values() for value in pins}
    assert values == {MICROMAMBA_PIN}, (
        f"all setup-micromamba call sites must pin micromamba-version to {MICROMAMBA_PIN!r} "
        f"(bump MICROMAMBA_PIN and every call site in one commit); found {sorted(values)} "
        f"across {sorted(call_sites)}"
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
