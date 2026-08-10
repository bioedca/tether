# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The seat-wide credit counter, tested on behaviour rather than on the prose that describes it.

`tests/test_greptile_config.py` already pins the configuration that stops Greptile firing unasked.
This file covers the other half: the number a worker is told to read before spending a credit.

An earlier draft of this file asserted phrases and their textual order in the review-gate prose.
Codex rejected that on PR #385, correctly: it reinstates the prose-drift category
`.agents/bin/scope_guard.py` retired, where a harmless rewording turns the base matrix red while
executable behaviour can contradict the document and still pass. So what is asserted below is what
the counter *does* - which repositories it totals, which reviews it bills, and what it prints when
it cannot tell - never how any document happens to word it.

Stdlib only, so it runs on the base 3-OS `test` matrix.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[1]
SCRIPT = _REPO / ".agents" / "bin" / "greptile_usage.py"

_spec = importlib.util.spec_from_file_location("tether_greptile_usage", SCRIPT)
assert _spec is not None and _spec.loader is not None
usage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usage)


def _pr(number: int, author: str = "bioedca", updated: str = "2026-08-02T00:00:00Z") -> dict:
    return {"number": number, "author": {"login": author}, "updatedAt": updated}


def _review(bot: str = "greptile-apps[bot]", when: str = "2026-08-02T00:00:00Z") -> dict:
    return {"user": {"login": bot}, "submitted_at": when}


def _fake_gh(prs: dict[str, list], reviews: dict[int, list], fail: set[str] | None = None):
    """Stand in for `_gh`.

    Branches on the subcommand rather than substring-matching the whole argv: a repository name
    appears inside the review path too, so a single lookup table would answer the review call with
    the pull-request list.
    """

    def call(*args: str) -> Any:
        if args and args[0] == "pr":  # `pr list --repo <owner/name> ...`
            repo = args[args.index("--repo") + 1]
            if repo in (fail or set()):
                raise subprocess.CalledProcessError(1, args, stderr=b"rate limit exceeded")
            return prs.get(repo, [])
        # `api --paginate repos/<owner>/<name>/pulls/<n>/reviews` — a bare list, because
        # `--paginate` concatenates the pages of an array endpoint itself. It used to carry
        # `--slurp`, which wrapped the pages in an outer list the caller flattened straight back
        # out; dropping it (#417) is what makes this readable on gh 2.45.0 in the WSL lane.
        path = args[-1]
        return reviews.get(int(path.rsplit("/pulls/", 1)[1].split("/")[0]), [])

    return call


def test_the_seat_total_spans_every_repository_not_just_this_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Credits are billed per SEAT, so a per-repository number cannot say what is left.

    One credit in each of two repositories is two credits against the same 50 - the arithmetic a
    Tether-only counter gets wrong by construction.
    """
    monkeypatch.setattr(
        usage,
        "_gh",
        _fake_gh(
            {"bioedca/tether": [_pr(1)], "bioedca/Yeliztli": [_pr(2)], "bioedca/tbox-finder": []},
            {1: [_review()], 2: [_review()]},
        ),
    )
    monkeypatch.setattr("sys.argv", ["greptile_usage.py", "--month", "2026-08"])
    assert usage.main() == 0
    out = capsys.readouterr().out
    assert f"used 2 of {usage.INCLUDED_CREDITS}" in out
    assert "48 remaining" in out


def test_an_unreadable_repository_makes_the_total_unknown_rather_than_low(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail closed. This is the finding Codex raised on PR #385.

    Recording zero for a repository that could not be listed reads as *unused* and overstates the
    remaining balance - on the one number the review lane tells a worker to consult before spending
    a credit. A partial count is worse than no count, because it looks like an answer.
    """
    monkeypatch.setattr(
        usage,
        "_gh",
        _fake_gh({"bioedca/tether": [_pr(1)]}, {1: [_review()]}, fail={"bioedca/Yeliztli"}),
    )
    monkeypatch.setattr("sys.argv", ["greptile_usage.py", "--month", "2026-08"])
    assert usage.main() == usage.EXIT_UNKNOWN
    captured = capsys.readouterr()
    assert "UNKNOWN" in captured.err
    assert "remaining" not in captured.out, "a balance must not be printed when it is not known"


def test_a_missing_gh_is_unknown_not_over_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`gh` absent from PATH must exit 2, never 1. Codex raised this on PR #392.

    `FileNotFoundError` is not `CalledProcessError` - the process never ran - so it escaped every
    handler and fell out as an unhandled traceback, exit 1. That is the code reserved for the
    definite answer *the seat is over budget*, so a caller that stops on 1 and retries on 2 would
    have read a missing dependency as an exhausted budget and skipped a review it could afford.
    """

    def _no_gh(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError(2, "No such file or directory", "gh")

    # `subprocess.run` is patched rather than `_gh`, so the real `_gh` runs and its OSError arm is
    # what the exit code is being read from.
    monkeypatch.setattr(subprocess, "run", _no_gh)
    monkeypatch.setattr("sys.argv", ["greptile_usage.py", "--month", "2026-08"])
    assert usage.main() == usage.EXIT_UNKNOWN
    assert usage.EXIT_UNKNOWN != usage.EXIT_OVER_BUDGET
    captured = capsys.readouterr()
    assert "gh" in captured.err
    assert "remaining" not in captured.out, "no balance may be printed when gh never ran"


def test_a_gh_too_old_for_a_flag_is_reported_as_that_not_as_usage_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#417: a rejected flag must name the flag and the build found, not a fragment of help.

    This is how `--slurp` failed in the WSL lane, where `gh` is the apt package at 2.45.0 and the
    documented `python3` invocation resolves to it. `gh` answers `unknown flag: --x` and then prints
    its **whole usage block**, so the last stderr line - which is what the handler used to report -
    is a line of syntax help. The balance came back UNKNOWN, correctly, for a reason that named
    nothing anyone could act on.

    The flag is gone (`--paginate` already concatenates an array endpoint's pages), so this asserts
    the *diagnosis* rather than the flag: any future flag this script grows must fail legibly on a
    `gh` too old for it.
    """
    usage_text = (
        "unknown flag: --slurp\n\nUsage:  gh api <endpoint> [flags]\n\nFlags:\n  --cache duration\n"
    )

    def _rejects_the_flag(*args: object, **_kwargs: object) -> object:
        argv = args[0] if args else []
        if isinstance(argv, list) and "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=b"gh version 2.45.0 (2025-07-18)\n")
        raise subprocess.CalledProcessError(1, argv, stderr=usage_text.encode())

    monkeypatch.setattr(subprocess, "run", _rejects_the_flag)
    monkeypatch.setattr("sys.argv", ["greptile_usage.py", "--month", "2026-08"])
    assert usage.main() == usage.EXIT_UNKNOWN
    captured = capsys.readouterr()
    assert "--slurp" in captured.err, "the rejected flag must be named"
    assert "2.45.0" in captured.err, "the build actually found must be named"
    assert usage.MINIMUM_GH in captured.err, "the required floor must be named"
    assert "Usage:" not in captured.err, "the usage block is what this replaced"
    assert "remaining" not in captured.out, (
        "an unreadable seat must print no balance - the whole point of failing closed"
    )


def test_output_that_is_not_json_is_unknown_rather_than_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A zero exit carrying a proxy login page is not a repository with no pull requests."""

    def _html(*_args: str, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(["gh"], 0, b"<html>login</html>", b"")

    monkeypatch.setattr(subprocess, "run", _html)
    monkeypatch.setattr("sys.argv", ["greptile_usage.py", "--month", "2026-08"])
    assert usage.main() == usage.EXIT_UNKNOWN
    assert "remaining" not in capsys.readouterr().out


def test_pages_that_arrive_unmerged_are_unknown_rather_than_a_short_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """If `--paginate` ever emitted one array per page, the answer must be UNKNOWN, never a number.

    Raised as `P1` on #422 against dropping `--slurp`: the concern was that gh 2.45.0 emits separate
    JSON arrays for a multi-page response, which `_gh` decodes as a single document.

    **It does not** — measured against `pulls/385/reviews`, 112 reviews over four pages, on 2.45.0
    itself: `--paginate` merges them and the whole script traverses that PR in the WSL lane. So this
    is not pinning current behaviour.

    What it pins is that the *feared* behaviour stays safe if a future `gh` ever adopts it.
    Concatenated arrays are not valid JSON, so `json.loads` raises and `_gh` takes its `Unreadable`
    arm. The balance is then unknown, which is the honest answer; the direction that would matter is
    reading page one only and reporting the shortfall as spare budget, and nothing here can do that.
    `--slurp` never protected against this either - it wrapped pages the caller unwrapped again -
    so removing it neither created nor closed the hole.
    """

    def _unmerged_pages(*_args: str, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(["gh"], 0, b'[{"id": 1}]\n[{"id": 2}]\n', b"")

    monkeypatch.setattr(subprocess, "run", _unmerged_pages)
    monkeypatch.setattr("sys.argv", ["greptile_usage.py", "--month", "2026-08"])
    assert usage.main() == usage.EXIT_UNKNOWN
    captured = capsys.readouterr()
    assert "remaining" not in captured.out, "a partial page count must never print a balance"
    assert "UNKNOWN" in captured.err


def test_only_reviews_the_seat_is_billed_for_are_counted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The credit is charged to the PR AUTHOR, so another author's PR is not this seat's cost."""
    monkeypatch.setattr(
        usage,
        "_gh",
        _fake_gh({"bioedca/tether": [_pr(1, author="someone-else")]}, {1: [_review()]}),
    )
    monkeypatch.setattr("sys.argv", ["greptile_usage.py", "--month", "2026-08"])
    assert usage.main() == 0
    assert "used 0 of" in capsys.readouterr().out


def test_a_review_from_another_month_is_not_this_months_spend(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Credits reset per billing period, so the month filter is the whole point of the number."""
    monkeypatch.setattr(
        usage,
        "_gh",
        _fake_gh(
            {"bioedca/tether": [_pr(1, updated="2026-08-02T00:00:00Z")]},
            {1: [_review(when="2026-07-30T00:00:00Z")]},
        ),
    )
    monkeypatch.setattr("sys.argv", ["greptile_usage.py", "--month", "2026-08"])
    assert usage.main() == 0
    assert "used 0 of" in capsys.readouterr().out


def test_over_budget_and_unknown_are_different_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Over-budget is a known answer; unreadable is no answer. A caller may want to retry one."""
    assert usage.EXIT_OVER_BUDGET != usage.EXIT_UNKNOWN
    monkeypatch.setattr(
        usage,
        "_gh",
        _fake_gh(
            {"bioedca/tether": [_pr(n) for n in range(1, 51)]},
            {n: [_review()] for n in range(1, 51)},
        ),
    )
    monkeypatch.setattr(
        "sys.argv", ["greptile_usage.py", "--month", "2026-08", "--fail-over-budget"]
    )
    assert usage.main() == usage.EXIT_OVER_BUDGET


def test_a_malformed_month_fails_closed_rather_than_reporting_a_full_balance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every comparison here is lexicographic on ISO-8601 text.

    `2026-8` sorts below every real `2026-08-..` timestamp, so it would skip every PR and report all
    50 credits remaining - confidently, and in the direction that gets a credit spent that the seat
    does not have.
    """
    monkeypatch.setattr(usage, "_gh", _fake_gh({}, {}))
    monkeypatch.setattr("sys.argv", ["greptile_usage.py", "--month", "2026-8"])
    assert usage.main() == usage.EXIT_UNKNOWN


def test_the_month_is_queried_server_side_not_filtered_after_a_capped_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--limit` is a maximum fetched, so post-filtering cannot detect truncation.

    An older PR that Greptile reviewed this month would drop out of a capped newest-N fetch, and an
    omitted PR reads as unspent credits.
    """
    seen: list[tuple[str, ...]] = []

    def spy(*args: str):
        seen.append(args)
        return [] if args and args[0] == "pr" else []

    monkeypatch.setattr(usage, "_gh", spy)
    monkeypatch.setattr("sys.argv", ["greptile_usage.py", "--month", "2026-08"])
    assert usage.main() == 0
    listings = [a for a in seen if a and a[0] == "pr"]
    assert listings, "the repositories must actually be listed"
    for call in listings:
        assert "--search" in call, "the month must be part of the query, not a post-filter"
        assert "updated:>=2026-08-01 sort:updated-asc" in call, (
            "lower bound only - an upper bound drops a PR reviewed in the month but touched later"
        )


def test_the_configured_repositories_are_the_ones_billed_to_this_seat() -> None:
    """A repository that receives reviews and is not listed makes the remaining count read HIGH.

    Structural, not prose: the list is the unit the 50 applies to, so it is asserted where it is
    used rather than where it is described.
    """
    assert usage.INCLUDED_CREDITS == 50, "Greptile Pro includes 50 credits per seat per month"
    assert set(usage.REPOS) == {
        "bioedca/tether",
        "bioedca/Yeliztli",
        "bioedca/tbox-finder",
    }
    assert json.loads(json.dumps(list(usage.REPOS))), (
        "REPOS must stay JSON-serialisable for reports"
    )


def test_a_review_list_holding_a_non_object_is_unknown_rather_than_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The container was type-checked and its CONTENTS were not (CodeRabbit `Major` on #422).

    `isinstance(reviews, list)` catches GitHub's error envelope, which is a dict. It does not catch
    a list carrying `null`, and `review.get(...)` then raises `AttributeError` — which `main` does
    not catch, because everything else that cannot be read in this file raises `Unreadable`. So the
    one command a worker must run before spending a metered credit exited on a traceback rather
    than reporting UNKNOWN, and an unreadable balance that *looks* like a crash invites re-running
    it rather than treating the budget as unknown.

    The control is the same payload with a real review object, so what separates them is the entry
    type alone and not the plumbing — and the control asserts the **count**, not the exit code.
    `main() == 0` only means *under budget*, which is equally what a version that silently dropped
    every review would produce, so an exit-code control would have passed against the failure it
    exists to exclude (CodeRabbit `Minor` on #422 — in this very test, one round after the same
    shape was fixed elsewhere in the stack).
    """
    monkeypatch.setattr(usage, "_gh", _fake_gh({"bioedca/tether": [_pr(1)]}, {1: [None]}))
    monkeypatch.setattr("sys.argv", ["greptile_usage.py", "--month", "2026-08"])
    assert usage.main() == usage.EXIT_UNKNOWN
    captured = capsys.readouterr()
    assert "remaining" not in captured.out, "an unreadable list must never print a balance"
    assert "UNKNOWN" in captured.err

    monkeypatch.setattr(usage, "_gh", _fake_gh({"bioedca/tether": [_pr(1)]}, {1: [_review()]}))
    assert usage.main() == 0
    counted = capsys.readouterr().out
    assert f"used 1 of {usage.INCLUDED_CREDITS}" in counted, (
        "the control must show the review was COUNTED; a zero exit only says under-budget"
    )
    assert f"{usage.INCLUDED_CREDITS - 1} remaining" in counted


def test_an_error_envelope_where_a_review_list_belongs_is_unknown(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CONTAINER guard, which had no test of its own until now.

    GitHub answers a failed read with an object — `{"message": "Not Found", ...}` — where the
    reviews endpoint would answer an array. Iterating that yields its KEYS, so every truthy filter
    below would run against strings and the pull request would contribute zero credits: a spend
    subtracted, and a balance that reads higher than it is. That is the one direction this file
    exists to avoid.

    Written after a mutation sweep found that deleting `isinstance(reviews, list)` failed nothing.
    It fails nothing because the entry check added beside it happens to reject the envelope's keys
    too — so the behaviour is safe either way, and neither guard was pinned to the reason it exists.
    """
    monkeypatch.setattr(
        usage, "_gh", _fake_gh({"bioedca/tether": [_pr(1)]}, {1: {"message": "Not Found"}})
    )
    monkeypatch.setattr("sys.argv", ["greptile_usage.py", "--month", "2026-08"])
    assert usage.main() == usage.EXIT_UNKNOWN
    captured = capsys.readouterr()
    assert "remaining" not in captured.out, "an envelope must never be counted as zero reviews"
    assert "UNKNOWN" in captured.err
