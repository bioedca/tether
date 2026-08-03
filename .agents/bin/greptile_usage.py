#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""How many Greptile credits the seat has spent this month, across every repository it uses.

Greptile publishes **no usage API** - the dashboard (Settings -> Usage) is the only official
surface. But its billing rule is precise enough to count from the GitHub side:

    "Billing counts completed reviews, not PRs. Each finished review consumes one credit,
     charged to the PR author."   and   "Skipped reviews don't count."

So one completed ``greptile-apps[bot]`` review submission, on a pull request authored by the seat
holder, is one credit. Credits are billed **per seat**, not per repository, which is why this counts
across ``REPOS`` rather than the repository it happens to live in: a Tether-only number cannot say
what is left when two other repositories draw on the same 50.

**This is a proxy, not an invoice**, and it is written to fail toward over-counting rather than
under-counting, because the expensive mistake is believing there is budget left when there is not:

- A **TREX** review costs 3 credits, not 1. This counts submissions, so a TREX review is
  under-counted by 2 - the one direction it does err, and the one the dashboard must settle.
- Whether re-triggering on the same PR bills twice is **undocumented**. Every submission is counted,
  which assumes it does.
- A review on a PR the seat did not author is invisible here, and correctly so: that credit is
  charged to whoever authored it.

Reconcile against Settings -> Usage before treating the number as authoritative.

Stdlib only, and shells out to ``gh`` for auth - the same soft dependency ``claim.py`` already has.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime

#: The account whose seat the credits are billed to.
SEAT = "bioedca"

#: Every repository that seat opens pull requests in. Credits are per seat, so this list - not the
#: repository this file sits in - is the unit the 50 applies to. A fourth repository that starts
#: receiving reviews and is not listed here makes the remaining count read high, which is the
#: dangerous direction; add it.
REPOS = ("bioedca/tether", "bioedca/Yeliztli", "bioedca/tbox-finder")

BOT = "greptile-apps"

#: Greptile Pro, per seat, per billing period.
INCLUDED_CREDITS = 50

#: Tether's self-imposed share of the seat. Advisory - nothing enforces it, and exceeding it is not
#: an error, it just means the other two repositories have less.
REPO_SHARE = 16

EXIT_OVER_BUDGET = 1


def _gh(*args: str) -> object:
    out = subprocess.run(["gh", *args], capture_output=True, check=True)  # noqa: S603
    return json.loads(out.stdout.decode("utf-8"))


def _credits(repo: str, month: str) -> tuple[int, int, list[tuple[int, int, str]]]:
    """Credits, PRs and per-PR detail for one repository in ``month`` (``YYYY-MM``)."""
    try:
        prs = _gh(
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            "100",
            "--json",
            "number,author,updatedAt",
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", "replace").strip().splitlines()
        print(f"  ! {repo}: {message[-1] if message else 'unreadable'}", file=sys.stderr)
        return 0, 0, []

    credits = prs_seen = 0
    detail: list[tuple[int, int, str]] = []
    for pull in prs:  # type: ignore[union-attr]
        if (pull.get("author") or {}).get("login") != SEAT:
            continue  # the credit is charged to the author, not to the repository
        if pull["updatedAt"][:7] < month:
            continue  # untouched this month, so it cannot carry a review inside it
        try:
            reviews = _gh("api", f"repos/{repo}/pulls/{pull['number']}/reviews")
        except subprocess.CalledProcessError:
            continue
        hits = [
            review
            for review in reviews  # type: ignore[union-attr]
            if BOT in (review.get("user") or {}).get("login", "").lower()
            and (review.get("submitted_at") or "")[:7] == month
        ]
        if hits:
            credits += len(hits)
            prs_seen += 1
            detail.append((pull["number"], len(hits), hits[0]["submitted_at"][:10]))
    return credits, prs_seen, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--month", help="YYYY-MM; defaults to the current UTC month")
    parser.add_argument(
        "--fail-over-budget",
        action="store_true",
        help="exit 1 when the seat is out of included credits (for a scheduled check)",
    )
    args = parser.parse_args()
    month = args.month or datetime.now(UTC).strftime("%Y-%m")

    per_repo: Counter[str] = Counter()
    prs: Counter[str] = Counter()
    detail: list[tuple[str, int, int, str]] = []
    for repo in REPOS:
        credits, seen, rows = _credits(repo, month)
        per_repo[repo] = credits
        prs[repo] = seen
        detail.extend((repo, number, count, when) for number, count, when in rows)

    used = sum(per_repo.values())
    left = INCLUDED_CREDITS - used
    print(f"Greptile credits — seat {SEAT}, month {month}")
    print(f"  used {used} of {INCLUDED_CREDITS} included; {left} remaining")
    print()
    for repo in REPOS:
        share = ""
        if repo.endswith("/tether"):
            share = f"  (Tether share: {per_repo[repo]}/{REPO_SHARE})"
        print(f"  {repo:<26} {per_repo[repo]:>2} credit(s) over {prs[repo]} PR(s){share}")

    if detail:
        print("\n  spent on:")
        for repo, number, count, when in sorted(detail, key=lambda row: row[3], reverse=True):
            plural = "s" if count != 1 else ""
            print(f"    {when}  {repo}#{number}  {count} review{plural}")

    print(
        "\n  A TREX review costs 3 credits and is counted here as 1, so this can only read LOW."
        "\n  Reconcile against Settings -> Usage before relying on the remaining figure."
    )
    if args.fail_over_budget and left <= 0:
        print("\nerror: the seat has no included credits left this month", file=sys.stderr)
        return EXIT_OVER_BUDGET
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
