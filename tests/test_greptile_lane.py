# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The seat-wide credit counter, tested on behaviour rather than on the prose that describes it.

`tests/test_greptile_config.py` already pins the configuration that stops Greptile firing unasked.
This file covers the other half: the number a worker is told to read before spending a credit.

An earlier draft of this file asserted phrases and their textual order in `docs/agents/review.md`.
Codex rejected that on PR #385, correctly: it reinstates the prose-drift category
`.agents/bin/scope_guard.py` retired, where a harmless rewording turns the base matrix red while
executable behaviour can contradict the document and still pass. The draft-round counter proved the
point - the prose said draft reviews were exempt while `triage.py` counted them, and no wording
assertion could have caught it. `tests/test_triage.py` now covers that behaviourally.

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
        path = args[-1]  # `api repos/<owner>/<name>/pulls/<n>/reviews`
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
        assert "updated:>=2026-08-01" in call


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
