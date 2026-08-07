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
  under-counted by 2, and the dashboard must settle it.
- Whether re-triggering on the same PR bills twice is **undocumented**. Every submission is counted,
  which assumes it does. If it does not, that assumption reads *high* - the only way this number
  errs on the safe side, and the reason the two cannot be netted into a single direction.
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


class Unreadable(RuntimeError):
    """A repository on the seat could not be counted, so the seat total is unknown.

    Raised rather than absorbed. A transient API, authorization or rate-limit error used to record
    zero usage for that repository, which reads as *unused* and overstates the remaining balance -
    on the one number the review lane tells a worker to consult before spending a credit. An
    unknown total must look unknown.
    """


#: `gh` rejecting a flag it does not have. The message is stable across the versions in play and is
#: the only part of the failure that names the cause; everything after it is usage text.
_UNKNOWN_FLAG = re.compile(r"unknown flag:\s*(\S+)")

#: The floor ADR-0060 already pins for this repository, for `gh attestation verify` (CVE-2025-25204
#: made it fail open in 2.49.0-2.66.x). Stated here so a version failure names the requirement that
#: already exists rather than inventing a new one.
MINIMUM_GH = "2.67.0"


def _gh_version() -> str:
    """The `gh` build actually on PATH, for an error message. Never raises - this is diagnostics."""
    try:
        out = subprocess.run(  # noqa: S603
            ["gh", "--version"], capture_output=True, check=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    first = out.stdout.decode("utf-8", "replace").strip().splitlines()
    return first[0] if first else "unknown"


def _why(exc: subprocess.CalledProcessError) -> str:
    """Why `gh` failed, naming a rejected flag as one rather than echoing usage text (#417).

    The last stderr line is the right default for an API or authorization failure, and the wrong one
    for a missing flag: `gh` answers `unknown flag: --x` and then prints its whole usage block, so
    the last line is a fragment of syntax help and the actual cause has scrolled past. That is how
    `--slurp` on gh 2.45.0 presented, and it read as an unexplained failure of the seat count rather
    than as a version mismatch anyone could act on.
    """
    stderr = exc.stderr.decode("utf-8", "replace") if exc.stderr else ""
    rejected = _UNKNOWN_FLAG.search(stderr)
    if rejected:
        return (
            f"the gh on PATH does not support {rejected.group(1)} - found {_gh_version()}, "
            f"need >= {MINIMUM_GH}. In WSL this is usually the apt package, which pins an older "
            f"build than the native install"
        )
    lines = stderr.strip().splitlines()
    return lines[-1] if lines else "unreadable"


def _gh(*args: str) -> object:
    try:
        out = subprocess.run(["gh", *args], capture_output=True, check=True)  # noqa: S603
    except OSError as exc:
        # `gh` missing from PATH, or not executable. This is NOT `CalledProcessError` - it never
        # ran - so without this arm it escaped every handler and exited 1, which is the code
        # reserved for the definite answer "the seat is over budget". A caller that stops on 1 and
        # retries on 2 would have read a missing dependency as an exhausted budget. WSL is the
        # ordinary way to hit it: `gh` is on PATH natively and in WSL here, but nothing guarantees
        # both, and the documented invocation is `python3`, which is the WSL interpreter.
        raise Unreadable(f"cannot run gh ({exc.strerror or exc})") from exc
    try:
        return json.loads(out.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # A zero exit with output that is not the JSON asked for - a proxy login page, a truncated
        # response. Unparseable is unknown, never zero.
        raise Unreadable(f"gh returned output that is not JSON ({exc})") from exc


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
        raise Unreadable(f"{repo}: {_why(exc)}") from exc

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
            #
            # Deliberately WITHOUT `--slurp` (#417). On an endpoint that answers with a JSON array,
            # `--paginate` already concatenates the pages into one array, so `--slurp` only wrapped
            # them in an outer list this then had to flatten back. It bought nothing and cost the
            # whole read in the WSL lane: `--slurp` arrived after gh 2.45.0, which is what the
            # Ubuntu package pins and what `python3` - the documented interpreter for this script -
            # resolves `gh` to there. The failure was a `CalledProcessError` whose last stderr line
            # is a line of usage text, so it read as a mystery rather than as a missing flag, and
            # the balance a worker must consult before spending a metered credit was unreadable.
            reviews = _gh("api", "--paginate", f"repos/{repo}/pulls/{pull['number']}/reviews")
        except subprocess.CalledProcessError as exc:
            # Same rule as the repository listing above, and the same reason: a PR whose reviews
            # could not be read is not a PR with zero reviews. Skipping it silently would subtract
            # from the spend and add to the apparent balance - the one direction that matters.
            raise Unreadable(f"{repo}#{pull['number']} reviews: {_why(exc)}") from exc
        if not isinstance(reviews, list):
            # A dict here is GitHub's error envelope, not a page of reviews. Treating it as empty
            # would subtract from the spend in the one direction that matters.
            raise Unreadable(f"{repo}#{pull['number']} reviews: not a list of reviews")
        hits = [
            review
            for review in reviews
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
        "\n  A proxy, not an invoice, and it can err BOTH ways: a TREX review costs 3 credits and"
        "\n  is counted here as 1, while a re-triggered review is counted twice on the assumption"
        "\n  - undocumented - that it bills twice. Reconcile against Settings -> Usage before"
        "\n  relying on the remaining figure."
    )
    if args.fail_over_budget and left <= 0:
        print("\nerror: the seat has no included credits left this month", file=sys.stderr)
        return EXIT_OVER_BUDGET
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
