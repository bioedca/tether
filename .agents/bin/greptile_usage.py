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
import re
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

#: Distinct from over-budget: over-budget is a known answer, this is no answer at all. A caller
#: that treats them alike is fine; one that retries on 2 and stops on 1 is doing the right thing.
EXIT_UNKNOWN = 2


def _gh(*args: str) -> object:
    out = subprocess.run(["gh", *args], capture_output=True, check=True)  # noqa: S603
    return json.loads(out.stdout.decode("utf-8"))


class Unreadable(RuntimeError):
    """A repository on the seat could not be counted, so the seat total is unknown.

    Raised rather than absorbed. A transient API, authorization or rate-limit error used to record
    zero usage for that repository, which reads as *unused* and overstates the remaining balance -
    on the one number the review lane tells a worker to consult before spending a credit. An
    unknown total must look unknown.
    """


def _credits(repo: str, month: str) -> tuple[int, int, list[tuple[int, int, str]]]:
    """Credits, PRs and per-PR detail for one repository in ``month`` (``YYYY-MM``).

    Raises :class:`Unreadable` if the repository cannot be listed. Never returns zero to mean
    "could not tell".
    """
    try:
        # Filtered SERVER-side, not by taking the newest N and hoping: `--limit` is a maximum
        # number of items FETCHED, so post-filtering cannot detect truncation, and an omitted PR
        # reads as unspent credits.
        #
        # Lower bound only. An upper bound looks tempting and is wrong: a pull request reviewed in
        # the requested month but commented on, synchronised or merged later has an `updated` date
        # in that later month, and bounding the range above drops it. A review always moves
        # `updated`, so `>= month-01` cannot miss one.
        #
        # `sort:updated-asc` so the requested month sorts FIRST and any truncation falls on the
        # months after it. Residual, stated rather than hidden: a historical month in a repository
        # with more than `--limit` pull requests updated since could still truncate. The counter
        # is a proxy for the dashboard, and this is one of the ways it can read low.
        prs = _gh(
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            "500",
            "--search",
            f"updated:>={month}-01 sort:updated-asc",
            "--json",
            "number,author,updatedAt",
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Unreadable(f"{repo}: {message[-1] if message else 'unreadable'}") from exc

    credits = prs_seen = 0
    detail: list[tuple[int, int, str]] = []
    for pull in prs:  # type: ignore[union-attr]
        if (pull.get("author") or {}).get("login") != SEAT:
            continue  # the credit is charged to the author, not to the repository
        if pull["updatedAt"][:7] < month:
            continue  # untouched this month, so it cannot carry a review inside it
        try:
            # `--paginate`: a PR with more than one REST page of reviews would otherwise return
            # only the first, and a billed review after it reads as unspent credit.
            reviews = _gh(
                "api", "--paginate", "--slurp", f"repos/{repo}/pulls/{pull['number']}/reviews"
            )
        except subprocess.CalledProcessError as exc:
            # Same rule as the repository listing above, and the same reason: a PR whose reviews
            # could not be read is not a PR with zero reviews. Skipping it silently would subtract
            # from the spend and add to the apparent balance - the one direction that matters.
            message = exc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise Unreadable(
                f"{repo}#{pull['number']} reviews: {message[-1] if message else 'unreadable'}"
            ) from exc
        # `--slurp` wraps each page in an outer list; flatten before filtering.
        flat = [r for page in reviews for r in page]  # type: ignore[union-attr]
        hits = [
            review
            for review in flat
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
    # Validated, because every comparison here is lexicographic on ISO-8601 text. A plausible typo
    # like `2026-8` sorts BELOW every real `2026-08-..` timestamp, so it would skip every PR and
    # report all 50 credits remaining - confidently, and in exactly the direction that gets a credit
    # spent that the seat does not have.
    # The shape is checked before the calendar, because `strptime` is LENIENT about zero-padding -
    # it happily parses "2026-8", which is the exact typo that breaks the comparisons.
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        print(f"error: --month must be a zero-padded YYYY-MM, not {month!r}", file=sys.stderr)
        return EXIT_UNKNOWN
    try:
        datetime.strptime(month, "%Y-%m")  # noqa: DTZ007 - a calendar month, not an instant
    except ValueError:
        print(f"error: --month is not a real calendar month: {month!r}", file=sys.stderr)
        return EXIT_UNKNOWN

    per_repo: Counter[str] = Counter()
    prs: Counter[str] = Counter()
    detail: list[tuple[str, int, int, str]] = []
    try:
        for repo in REPOS:
            credits, seen, rows = _credits(repo, month)
            per_repo[repo] = credits
            prs[repo] = seen
            detail.extend((repo, number, count, when) for number, count, when in rows)
    except Unreadable as exc:
        # Fail closed. A partial count is worse than no count: it names a remaining balance that is
        # too high, on the number a worker consults before spending a metered credit.
        print(f"error: cannot count the seat - {exc}", file=sys.stderr)
        print(
            "The remaining balance is UNKNOWN, not the partial figure this run could reach. "
            "Re-run, or read Settings -> Usage, before spending a credit.",
            file=sys.stderr,
        )
        return EXIT_UNKNOWN

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
