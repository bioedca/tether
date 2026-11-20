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
evaluate each block's expressions for two runs and compare the results, rather than matching the
YAML text, so any spelling that keeps the behaviour passes.
"""

from __future__ import annotations

import re
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


def _run(workflow: str, ref: str, run_id: int) -> tuple[str, object]:
    """Return the (group, cancel-in-progress) one run of ``workflow`` on ``ref`` gets."""
    data = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    block = data["concurrency"]
    context = {"github.workflow": data["name"], "github.ref": ref, "github.run_id": run_id}
    return str(_resolve(block["group"], context)), _resolve(
        block.get("cancel-in-progress"), context
    )


@pytest.mark.parametrize("workflow", GUARDED)
def test_a_later_run_on_main_neither_cancels_nor_replaces_an_earlier_one(workflow: str) -> None:
    (earlier, _), (later, cancels) = _run(workflow, MAIN, 101), _run(workflow, MAIN, 102)
    assert earlier != later, (
        f"{workflow}: two runs on main share the concurrency group {earlier!r}, so the later one "
        "replaces the earlier one while it waits, or cancels it if cancel-in-progress is on"
    )
    assert cancels is False, f"{workflow}: a run on main must not set cancel-in-progress"


@pytest.mark.parametrize("ref", OTHER_REFS)
@pytest.mark.parametrize("workflow", GUARDED)
def test_every_other_ref_keeps_one_group_per_ref_and_cancels_superseded_runs(
    workflow: str, ref: str
) -> None:
    name = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["name"]
    (earlier, _), (later, cancels) = _run(workflow, ref, 101), _run(workflow, ref, 102)
    assert earlier == later == f"{name}-{ref}"
    main_group, _ = _run(workflow, MAIN, 102)
    assert later.casefold() != main_group.casefold(), f"{workflow}: {ref} shares the main group"
    assert cancels is True, f"{workflow}: a new run on {ref} must cancel the superseded one"


def test_the_evaluator_reads_the_old_rule_as_cancelling_on_main() -> None:
    """The evaluator itself must see the bug the first test exists to catch."""
    context = {"github.workflow": "CI", "github.ref": MAIN, "github.run_id": 1}
    assert _resolve("${{ github.workflow }}-${{ github.ref }}", context) == "CI-refs/heads/main"
    assert _resolve("${{ github.ref != 'refs/heads/main' }}", context) is False
    assert (
        _resolve("${{ github.ref == 'REFS/HEADS/MAIN' && format(':{0}', 7) || '' }}", context)
        == ":7"
    )
