# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A later run on ``main`` never cancels or replaces an earlier one (#1265).

``ci.yml``, ``sidecar.yml`` and ``schema-guard.yml`` used one concurrency group per ref with
``cancel-in-progress: true``. On a pull request that is right: a new push supersedes the old
head. On ``main`` it cancelled the checks of every commit in a merge burst except the last (on
2026-11-19, seven of eight), so the commit that broke ``main`` was unknown and ``release.yml``
(#266) could release none of them.

GitHub keeps at most one running and one pending run per concurrency group; a newly queued run
replaces the pending one, and with ``cancel-in-progress`` also cancels the running one (GitHub
docs, "Control the concurrency of workflows and jobs", read through Context7 ``/github/docs`` on
2026-11-19). Two runs can therefore interfere only when their groups are equal. These tests
evaluate each block's expressions for two runs and compare the results. For manual sidecar dispatch
they also run the ref classifier and check how its output affects parity.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml  # provided by the base conda-lock (a mkdocs dependency)

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
GUARDED = ("ci.yml", "sidecar.yml", "schema-guard.yml")
MAIN = "refs/heads/main"
OTHER_REFS = ("refs/pull/1265/merge", "refs/heads/agent/issue-1265", "refs/heads/main-102")

_TOKEN = re.compile(r"\s*(?:('(?:[^']|'')*')|(\d+)|(==|!=|&&|\|\||[!(),])|([A-Za-z_][\w.-]*))")
_INTERPOLATION = re.compile(r"\$\{\{(.*?)\}\}")


def _evaluate(expression: str, context: dict[str, object]) -> object:
    """Evaluate the subset of the Actions expression language a concurrency block uses."""
    tokens: list[str] = []
    pos = 0
    while pos < len(expression.rstrip()):
        match = _TOKEN.match(expression, pos)
        assert match, f"cannot read the expression {expression!r} at {pos}"
        tokens.append(match.group(match.lastindex or 0))
        pos = match.end()
    tokens.reverse()

    def take() -> str:
        return tokens.pop()

    def primary() -> object:
        token = take()
        if token == "(":
            value = either()
            assert take() == ")"
            return value
        if token == "!":
            return not primary()
        if token.startswith("'"):
            return token[1:-1].replace("''", "'")
        if token.isdigit():
            return int(token)
        if token in ("true", "false"):
            return token == "true"
        if tokens and tokens[-1] == "(":
            take()
            args = [either()]
            while take() == ",":
                args.append(either())
            assert token.lower() == "format", f"unsupported function {token}"
            return re.sub(r"\{(\d+)\}", lambda m: _text(args[1 + int(m[1])]), str(args[0]))
        assert token.lower() in context, f"unknown context {token}"
        return context[token.lower()]

    def compare() -> object:
        left = primary()
        if tokens and tokens[-1] in ("==", "!="):
            equal = take() == "=="
            right = primary()
            same = _text(left).casefold() == _text(right).casefold()
            return same if equal else not same
        return left

    def both() -> object:
        value = compare()
        while tokens and tokens[-1] == "&&":
            take()
            right = compare()
            value = right if value else value
        return value

    def either() -> object:
        value = both()
        while tokens and tokens[-1] == "||":
            take()
            right = both()
            value = value if value else right
        return value

    value = either()
    assert not tokens, f"unread tokens in {expression!r}"
    return value


def _text(value: object) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _resolve(field: object, context: dict[str, object]) -> object:
    if not isinstance(field, str):
        return field
    whole = re.fullmatch(r"\s*\$\{\{(.*)\}\}\s*", field)
    if whole and "${{" not in whole[1]:
        return _evaluate(whole[1], context)
    return _INTERPOLATION.sub(lambda m: _text(_evaluate(m[1], context)), field)


def _read_workflow(workflow: str) -> dict:
    return yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))


def _concurrency(block: dict, context: dict[str, object]) -> tuple[str, object]:
    return str(_resolve(block["group"], context)), _resolve(
        block.get("cancel-in-progress"), context
    )


def _run(workflow: str, ref: str, run_id: int, event: str = "push") -> tuple[str, object]:
    """Return the workflow-level (group, cancel-in-progress) for one run."""
    data = _read_workflow(workflow)
    context = {
        "github.workflow": data["name"],
        "github.ref": ref,
        "github.run_id": run_id,
        "github.event_name": event,
    }
    return _concurrency(data["concurrency"], context)


def _sidecar_parity(
    ref: str, run_id: int, event: str, classification_result: str, is_main: str
) -> tuple[str, object, bool]:
    data = _read_workflow("sidecar.yml")
    parity = data["jobs"]["parity"]
    context = {
        "github.workflow": data["name"],
        "github.ref": ref,
        "github.run_id": run_id,
        "github.event_name": event,
        "needs.classify-dispatch.result": classification_result,
        "needs.classify-dispatch.outputs.is-main": is_main,
    }
    group, cancel_in_progress = _concurrency(parity["concurrency"], context)
    failure_step = parity["steps"][0]
    reports_failure = _resolve(failure_step["if"], context)
    assert isinstance(reports_failure, bool)
    return group, cancel_in_progress, reports_failure


@pytest.mark.parametrize("workflow", GUARDED)
def test_a_later_run_on_main_neither_cancels_nor_replaces_an_earlier_one(workflow: str) -> None:
    (earlier, _), (later, cancels) = _run(workflow, MAIN, 101), _run(workflow, MAIN, 102)
    assert earlier.casefold() != later.casefold(), (
        f"{workflow}: two runs on main share the concurrency group {earlier!r}, so the later one "
        "replaces the earlier one while it waits, or cancels it if cancel-in-progress is on"
    )
    assert cancels is False, f"{workflow}: a run on main must not set cancel-in-progress"


@pytest.mark.parametrize("ref", OTHER_REFS)
@pytest.mark.parametrize("workflow", GUARDED)
def test_every_other_ref_keeps_one_group_per_ref_and_cancels_superseded_runs(
    workflow: str, ref: str
) -> None:
    name = _read_workflow(workflow)["name"]
    (earlier, _), (later, cancels) = _run(workflow, ref, 101), _run(workflow, ref, 102)
    assert earlier == later == f"{name}-{ref}"
    main_group, _ = _run(workflow, MAIN, 102)
    assert later.casefold() != main_group.casefold(), f"{workflow}: {ref} shares the main group"
    assert cancels is True, f"{workflow}: a new run on {ref} must cancel the superseded one"


@pytest.mark.parametrize(
    ("ref", "is_main"),
    [
        (MAIN, "true"),
        ("refs/heads/MAIN", "false"),
        ("refs/heads/Main", "false"),
        ("refs/heads/main-102", "false"),
        ("refs/heads/feature", "false"),
        ("refs/tags/main", "false"),
    ],
)
def test_manual_sidecar_groups_only_literal_main_independently(
    ref: str, is_main: str, tmp_path: Path
) -> None:
    data = _read_workflow("sidecar.yml")
    classifier = data["jobs"]["classify-dispatch"]
    assert _resolve(classifier["if"], {"github.event_name": "workflow_dispatch"}) is True
    assert classifier["permissions"] == {}
    step = classifier["steps"][0]
    output_path = tmp_path / "dispatch-output"
    assert step["shell"] == "python"
    subprocess.run(
        [sys.executable, "-c", step["run"]],
        env=os.environ | {"GITHUB_REF": ref, "GITHUB_OUTPUT": str(output_path)},
        check=True,
    )
    assert output_path.read_text(encoding="utf-8") == f"is-main={is_main}\n"
    assert classifier["outputs"]["is-main"] == f"${{{{ steps.{step['id']}.outputs.is-main }}}}"

    first_top, first_top_cancel = _run("sidecar.yml", ref, 102, "workflow_dispatch")
    second_top, second_top_cancel = _run("sidecar.yml", ref, 103, "workflow_dispatch")
    assert first_top.casefold() != second_top.casefold()
    assert first_top_cancel is second_top_cancel is False

    first, first_cancel, first_fails = _sidecar_parity(
        ref, 102, "workflow_dispatch", "success", is_main
    )
    second, second_cancel, second_fails = _sidecar_parity(
        ref, 103, "workflow_dispatch", "success", is_main
    )
    assert first_fails is second_fails is False
    assert first.casefold() != first_top.casefold()
    automatic_main, _ = _run("sidecar.yml", MAIN, 102)
    assert first.casefold() != automatic_main.casefold()
    for event, run_id in (("push", 101), ("schedule", 104)):
        automatic_top, _ = _run("sidecar.yml", MAIN, run_id, event)
        automatic_parity, _, _ = _sidecar_parity(MAIN, run_id, event, "skipped", "")
        assert first.casefold() not in {automatic_top.casefold(), automatic_parity.casefold()}

    if ref == MAIN:
        assert first.casefold() != second.casefold()
        assert first_cancel is second_cancel is False
    else:
        assert first == second == f"{data['name']}-{ref}"
        assert first_cancel is second_cancel is True


@pytest.mark.parametrize(
    ("ref", "event", "cancels"),
    [(MAIN, "push", False), (MAIN, "schedule", False), (OTHER_REFS[0], "pull_request", True)],
)
def test_automatic_sidecar_runs_keep_parity_when_classification_is_skipped(
    ref: str, event: str, cancels: bool
) -> None:
    jobs = _read_workflow("sidecar.yml")["jobs"]
    assert _resolve(jobs["classify-dispatch"]["if"], {"github.event_name": event}) is False
    assert jobs["parity"]["needs"] == "classify-dispatch"
    assert jobs["parity"]["if"] == "${{ !cancelled() }}"
    assert jobs["parity"]["name"] == "sidecar / parity"
    assert _run("sidecar.yml", ref, 102, event)[1] is cancels
    first, first_cancel, first_fails = _sidecar_parity(ref, 102, event, "skipped", "")
    second, second_cancel, second_fails = _sidecar_parity(ref, 103, event, "skipped", "")
    assert first.casefold() != second.casefold()
    assert first_cancel is second_cancel is first_fails is second_fails is False


@pytest.mark.parametrize(
    ("result", "is_main"), [("failure", "false"), ("success", ""), ("success", "no")]
)
def test_failed_manual_classification_cannot_cancel_other_runs_or_pass_parity(
    result: str, is_main: str
) -> None:
    ref = "refs/heads/MAIN"
    group, cancel, reports_failure = _sidecar_parity(ref, 102, "workflow_dispatch", result, is_main)
    assert group.casefold() != f"sidecar-{ref}".casefold()
    assert cancel is False
    assert reports_failure is True
    failure_step = _read_workflow("sidecar.yml")["jobs"]["parity"]["steps"][0]
    assert failure_step["shell"] == "python"
    failure = subprocess.run(
        [sys.executable, "-c", failure_step["run"]], capture_output=True, text=True
    )
    assert failure.returncode != 0
    assert "Could not classify the manual sidecar ref" in failure.stderr


def test_the_evaluator_reads_the_old_rule_as_cancelling_on_main() -> None:
    """The evaluator itself must see the bug the first test exists to catch."""
    context = {"github.workflow": "CI", "github.ref": MAIN, "github.run_id": 1}
    assert _resolve("${{ github.workflow }}-${{ github.ref }}", context) == "CI-refs/heads/main"
    assert _resolve("${{ github.ref != 'refs/heads/main' }}", context) is False
    assert (
        _resolve("${{ github.ref == 'REFS/HEADS/MAIN' && format(':{0}', 7) || '' }}", context)
        == ":7"
    )
