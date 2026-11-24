#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""How many Greptile credits the seat has spent this cycle, across every repository it uses.

Greptile publishes **no usage API** - the dashboard (Settings -> Usage) is the only official
surface. But its billing rule is precise enough to count from the GitHub side:

    "Billing counts completed reviews, not PRs. Each finished review consumes one credit,
     charged to the PR author."   and   "Skipped reviews don't count."

So one completed ``greptile-apps[bot]`` review, on a pull request authored by the seat holder, is
one credit. A review that finds something leaves a submitted review object; a **clean** one leaves
only a summary comment ("Last reviewed commit: ..."), so both are read. Credits are billed **per
seat**, not per repository, which is why this counts across ``REPOS`` rather than the repository
it happens to live in: a Tether-only number cannot say what is left when two other repositories
draw on the same 50. ``--repo`` narrows the count for a session that can read only some of them (a
cloud session reads only Tether); it then prints Tether's share and no seat balance.

The count runs by **cycle**, not calendar month: a cycle starts on the 17th of a month at 00:00
UTC and ends when the 17th of the next month starts (ADR-0099). ``--cycle 2026-11`` is the cycle
that starts on 2026-11-17; the default is the cycle running now.

Tether's limit in a cycle is its share of 45 plus what is left of a one-time grant of 50 (ADR-0142).
Each cycle from the grant on draws on it what Tether used above 45, so a run counts the earlier
cycles of the grant too. ``--fail-over-share`` exits 1 at that limit, and a worker then takes the
CodeRabbit fallback. ``--kind PR`` prints whether a pull request is ``code``, which takes a Greptile
review, or ``docs/test-only``, which a clean Codex review closes alone.

**This is a proxy, not an invoice**, and it is written to fail toward over-counting rather than
under-counting, because the expensive mistake is believing there is budget left when there is not:

- A **TREX** run costs 3 credits on top of its review. A run asked on demand leaves the request
  comment that asks Greptile for one, and each such request in the cycle is counted at 3 (CodeRabbit
  `Major` on #1038), whether or not the run was billed - the safe direction. A run that an
  account-level rule starts leaves no request, so it is not seen, and the dashboard must settle it.
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

#: The repository whose share `REPO_SHARE` is.
TETHER = "bioedca/tether"

BOT = "greptile-apps"

#: Greptile Pro, per seat, per billing cycle.
INCLUDED_CREDITS = 50

#: The day of the month a cycle starts on, at 00:00 UTC (the maintainer, 2026-11-14: "rolling
#: every month on the 17th"; ADR-0099).
CYCLE_DAY = 17

#: Tether's share of the seat in one cycle, set by the maintainer (ADR-0099, which raised the 15 of
#: ADR-0069). Every Greptile review on Tether draws on it. `--fail-over-share` exits 1 once it and
#: what is left of `GRANT_CREDITS` are used; a worker `AGENTS.md` §Review sends here does not ask
#: Greptile then, and takes the CodeRabbit fallback (ADR-0142).
REPO_SHARE = 45

#: The one-time grant of Greptile reviews for Tether, outside its share (the maintainer, 2026-11-19
#: 00:33Z: "We now have 50 new Greptile reviews, they all can be used on tether, separate from the
#: usual 45 monthly quota"; one-time, #1316 decision 2). In each cycle from `GRANT_CYCLE` on,
#: Tether's credits above `REPO_SHARE` draw on it; once it is used, the limit is `REPO_SHARE` again.
GRANT_CREDITS = 50

#: The cycle the grant was made in: the one that starts on 2026-11-17 (ADR-0142).
GRANT_CYCLE = "2026-11"

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


#: GitHub's search API answers at most this many results for one query, however it is paged. A
#: cycle with more pull requests than this cannot be counted from the search, so it is UNKNOWN.
SEARCH_CAP = 1000

PER_PAGE = 100

#: Greptile's summary comment. A **clean** review posts this comment and no review object (#996,
#: 2026-11-14: "No actionable issues were found", no entry under `pulls/996/reviews`), so a counter
#: that reads review objects alone misses every clean credit - and reads the balance HIGH.
SUMMARY = re.compile(r"Last reviewed commit", re.IGNORECASE)

#: The counter Greptile keeps in that comment: "Reviews (1)", edited in place on each re-review.
REVIEWS_SO_FAR = re.compile(r"Reviews \((\d+)\)")

#: A comment that asks Greptile for a TREX run on demand. Greptile documents the command as its
#: handle followed by `trex` (docs/trex/overview, read 2026-11-14 through Context7).
TREX_ASK = re.compile(r"@greptileai\s+trex\b", re.IGNORECASE)

#: What a TREX run costs on top of its review (Greptile, docs/account/billing-seats).
TREX_CREDITS = 3


def _cycle(start_month: str) -> tuple[str, str]:
    """The first day of the cycle that starts in ``start_month`` and of the next one, ISO dates.

    Every comparison below is on ISO-8601 text, and ``"2026-12-17T00:00:00Z" < "2026-12-17"`` is
    false, so a timestamp ``t`` is in the cycle exactly when ``start <= t < end``.
    """
    year, month = (int(part) for part in start_month.split("-"))
    nyear, nmonth = (year + 1, 1) if month == 12 else (year, month + 1)
    return f"{year:04d}-{month:02d}-{CYCLE_DAY}", f"{nyear:04d}-{nmonth:02d}-{CYCLE_DAY}"


def _next_month(month: str) -> str:
    year, number = (int(part) for part in month.split("-"))
    return f"{year + 1:04d}-01" if number == 12 else f"{year:04d}-{number + 1:02d}"


def grant_draws(used_by_cycle: list[int]) -> list[int]:
    """What Tether drew on the one-time grant in each cycle, from `GRANT_CYCLE` on, in order.

    A cycle draws its credits above `REPO_SHARE`, and never more than the grant has left, so the
    draws add up to `GRANT_CREDITS` at most and every cycle after that has the limit of 45 alone.
    """
    left, draws = GRANT_CREDITS, []
    for used in used_by_cycle:
        draw = min(left, max(0, used - REPO_SHARE))
        draws.append(draw)
        left -= draw
    return draws


#: The files a change to which makes a pull request "code" whatever else it changes (#1316
#: decision 5): `AGENTS.md` at any depth, and these two at the root.
RULE_FILES = ("CLAUDE.md", "docs/PRD.md")


def pull_request_kind(paths: list[str]) -> str:
    """``"code"`` or ``"docs/test-only"`` for a pull request's changed files (ADR-0142).

    ``paths`` is GitHub's list of changed files, a renamed file by both names. A rule file makes it
    code. Otherwise it is docs/test-only when every file is Markdown, under ``docs/`` or under
    ``tests/``, and code when any one is not. No files at all is code, the side that reviews more.
    """
    if not paths:
        return "code"
    for path in paths:
        if path in RULE_FILES or path.rsplit("/", 1)[-1] == "AGENTS.md":
            return "code"
    if all(p.endswith(".md") or p.startswith(("docs/", "tests/")) for p in paths):
        return "docs/test-only"
    return "code"


def _current_cycle(now: datetime) -> str:
    """The ``YYYY-MM`` the cycle running at ``now`` started in."""
    if now.day >= CYCLE_DAY:
        return now.strftime("%Y-%m")
    year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    return f"{year:04d}-{month:02d}"


def _pull_requests(repo: str, start: str) -> list[dict]:
    """The seat's pull requests in ``repo`` updated on or after ``start``, through the REST search.

    REST, not ``gh pr list``: that command reads GraphQL, which the proxy of a cloud session refuses
    (#1035), so the one number a worker must read before spending a credit could not be read there.

    Filtered SERVER-side, not by taking the newest N and hoping: an omitted PR reads as unspent
    credits. Lower bound only. An upper bound looks tempting and is wrong: a pull request reviewed
    in the requested cycle but commented on, synchronised or merged later has an ``updated`` date
    after the cycle, and bounding the range above drops it. A review always moves ``updated``, so
    ``>= start`` cannot miss one. A search that GitHub marks incomplete, or that holds more
    than ``SEARCH_CAP`` results, is UNKNOWN rather than a short list.
    """
    query = f"repo:{repo} is:pr author:{SEAT} updated:>={start}"
    found: list[dict] = []
    page = 1
    while True:
        try:
            result = _gh(
                "api",
                "-X",
                "GET",
                "search/issues",
                "-f",
                f"q={query}",
                "-f",
                "sort=updated",
                "-f",
                "order=asc",
                "-f",
                f"per_page={PER_PAGE}",
                "-f",
                f"page={page}",
            )
        except subprocess.CalledProcessError as exc:
            raise Unreadable(f"{repo}: {_why(exc)}") from exc
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise Unreadable(f"{repo}: the pull-request search did not answer a result page")
        if result.get("incomplete_results"):
            raise Unreadable(f"{repo}: GitHub marked the pull-request search incomplete")
        if not isinstance(result.get("total_count"), int) or result["total_count"] > SEARCH_CAP:
            raise Unreadable(
                f"{repo}: the search holds {result.get('total_count')!r} pull requests, "
                f"more than the {SEARCH_CAP} it can list"
            )
        for item in result["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("updated_at"), str):
                raise Unreadable(f"{repo}: a search result is not a pull request")
            found.append(
                {
                    "number": item.get("number"),
                    "author": item.get("user") or {},
                    "updatedAt": item["updated_at"],
                }
            )
        if len(result["items"]) < PER_PAGE:
            return found
        page += 1


def _listed(repo: str, path: str, what: str) -> list[dict]:
    """A list endpoint read whole, or :class:`Unreadable` - never an empty list for "unknown"."""
    try:
        # `--paginate`: a PR with more than one REST page would otherwise return only the first,
        # and a billed review after it reads as unspent credit.
        #
        # Deliberately WITHOUT `--slurp` (#417). On an endpoint that answers with a JSON array,
        # `--paginate` already concatenates the pages into one array. **Measured**: on gh 2.45.0 in
        # WSL, `--paginate` over `pulls/408/reviews?per_page=1` returned 88 pages as ONE array of
        # 88 elements, parsed by a single `json.loads`. If that ever stopped being true this fails
        # CLOSED rather than short: `test_pages_that_arrive_unmerged_are_unknown_rather_than_a_
        # short_count` drives the concatenated shape and asserts UNKNOWN. `--slurp` arrived after
        # gh 2.45.0, which is what the Ubuntu package pins, so it cost the whole read in that lane.
        listed = _gh("api", "--paginate", path)
    except subprocess.CalledProcessError as exc:
        # A PR whose reviews could not be read is not a PR with zero reviews.
        raise Unreadable(f"{repo} {what}: {_why(exc)}") from exc
    if not isinstance(listed, list):
        # A dict here is GitHub's error envelope, not a page. Treating it as empty would subtract
        # from the spend in the one direction that matters.
        raise Unreadable(f"{repo} {what}: not a list")
    if not all(isinstance(entry, dict) for entry in listed):
        # The ENTRIES are checked as well as the container (CodeRabbit `Major` on #422): a `null`
        # reached `.get(...)` below and exited on a traceback instead of reporting UNKNOWN.
        raise Unreadable(f"{repo} {what}: an entry is not an object")
    return listed


def _by_bot(entry: dict) -> bool:
    return BOT in (entry.get("user") or {}).get("login", "").lower()


def _credits(repo: str, start: str, end: str) -> tuple[int, int, list[tuple[int, int, str]]]:
    """Credits, PRs and per-PR detail for one repository in the cycle ``[start, end)``.

    A pull request's review credits are the larger of two readings, never their sum, because one
    review can leave both: the review objects Greptile submitted in the cycle, and the "Reviews (N)"
    counter of its summary comment when that comment was written in the cycle. The summary is the
    only trace a clean review leaves. Its counter covers the pull request's whole life, so a pull
    request reviewed in two cycles reads its earlier reviews into the later one - an over-count, the
    safe direction. Each TREX run asked on demand in the cycle adds ``TREX_CREDITS`` on top.

    Raises :class:`Unreadable` if anything cannot be read. Never returns zero to mean "could not
    tell".
    """
    credits = prs_seen = 0
    detail: list[tuple[int, int, str]] = []
    for pull in _pull_requests(repo, start):
        if (pull.get("author") or {}).get("login") != SEAT:
            continue  # the credit is charged to the author, not to the repository
        if pull["updatedAt"] < start:
            continue  # untouched this cycle, so it cannot carry a review inside it
        number = pull["number"]
        reviews = _listed(repo, f"repos/{repo}/pulls/{number}/reviews", f"#{number} reviews")
        hits = [
            review
            for review in reviews
            if _by_bot(review) and start <= (review.get("submitted_at") or "") < end
        ]
        comments = _listed(repo, f"repos/{repo}/issues/{number}/comments", f"#{number} comments")
        summary, summary_at = 0, ""
        for comment in comments:
            body = comment.get("body") or ""
            when = comment.get("updated_at") or ""
            if _by_bot(comment) and SUMMARY.search(body) and start <= when < end:
                counter = REVIEWS_SO_FAR.search(body)
                summary = max(summary, int(counter.group(1)) if counter else 1)
                summary_at = max(summary_at, when)
        asked_trex = sum(
            1
            for comment in comments
            if TREX_ASK.search(comment.get("body") or "")
            and start <= (comment.get("created_at") or "") < end
        )
        spent = max(len(hits), summary) + TREX_CREDITS * asked_trex
        if spent:
            credits += spent
            prs_seen += 1
            when = hits[0]["submitted_at"] if hits else summary_at
            detail.append((number, spent, when[:10]))
    return credits, prs_seen, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cycle",
        help=f"YYYY-MM, the cycle that starts on day {CYCLE_DAY} of it; defaults to the cycle now",
    )
    parser.add_argument(
        "--fail-over-budget",
        action="store_true",
        help="exit 1 when the seat is out of included credits (for a scheduled check)",
    )
    parser.add_argument(
        "--repo",
        action="append",
        choices=REPOS,
        help=(
            "count only this repository (repeatable). A session that can read only Tether - a "
            "cloud session - uses --repo bioedca/tether; the seat total is then not read"
        ),
    )
    parser.add_argument(
        "--fail-over-share",
        action="store_true",
        help=(
            "exit 1 when Tether has no credit left in its share and the one-time grant for the "
            "cycle, or when the whole seat was read and is empty: take the CodeRabbit fallback"
        ),
    )
    parser.add_argument(
        "--kind",
        type=int,
        metavar="PR",
        help="print whether pull request PR is 'code' (Greptile) or 'docs/test-only' (Codex alone)",
    )
    args = parser.parse_args()
    if args.kind is not None:
        try:
            files = _listed(
                TETHER, f"repos/{TETHER}/pulls/{args.kind}/files", f"#{args.kind} files"
            )
        except Unreadable as exc:
            print(f"error: cannot read the changed files - {exc}", file=sys.stderr)
            return EXIT_UNKNOWN
        names = [
            name
            for entry in files
            for name in (entry.get("filename"), entry.get("previous_filename"))
            if isinstance(name, str)
        ]
        print(pull_request_kind(names))
        return 0
    selected = tuple(repo for repo in REPOS if repo in (args.repo or REPOS))
    whole_seat = selected == REPOS
    if args.fail_over_budget and not whole_seat:
        print("error: --fail-over-budget needs the whole seat; drop --repo", file=sys.stderr)
        return EXIT_UNKNOWN
    if args.fail_over_share and TETHER not in selected:
        print(f"error: --fail-over-share needs {TETHER} among the repositories", file=sys.stderr)
        return EXIT_UNKNOWN
    month = args.cycle or _current_cycle(datetime.now(UTC))
    # Validated, because every comparison here is lexicographic on ISO-8601 text. A plausible typo
    # like `2026-8` sorts BELOW every real `2026-08-..` timestamp, so it would skip every PR and
    # report all 50 credits remaining - confidently, and in exactly the direction that gets a credit
    # spent that the seat does not have.
    # The shape is checked before the calendar, because `strptime` is LENIENT about zero-padding -
    # it happily parses "2026-8", which is the exact typo that breaks the comparisons.
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        print(f"error: --cycle must be a zero-padded YYYY-MM, not {month!r}", file=sys.stderr)
        return EXIT_UNKNOWN
    try:
        datetime.strptime(month, "%Y-%m")  # noqa: DTZ007 - a calendar month, not an instant
    except ValueError:
        print(f"error: --cycle is not a real calendar month: {month!r}", file=sys.stderr)
        return EXIT_UNKNOWN
    start, end = _cycle(month)

    per_repo: Counter[str] = Counter()
    prs: Counter[str] = Counter()
    detail: list[tuple[str, int, int, str]] = []
    try:
        for repo in selected:
            credits, seen, rows = _credits(repo, start, end)
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

    # The grant: what Tether drew on it in the cycles before this one, each read whole like this
    # one. A past cycle can read low (ADR-0099: a summary edited later counts in the later cycle),
    # and the dashboard settles it.
    granted = month >= GRANT_CYCLE and TETHER in selected
    grant_before = drawn = 0
    if granted:
        earlier: list[int] = []
        past = GRANT_CYCLE
        try:
            while past < month:
                earlier.append(_credits(TETHER, *_cycle(past))[0])
                past = _next_month(past)
        except Unreadable as exc:
            print(f"error: cannot count the grant - {exc}", file=sys.stderr)
            print("The grant left is UNKNOWN. Re-run, or read Settings -> Usage.", file=sys.stderr)
            return EXIT_UNKNOWN
        draws = grant_draws([*earlier, per_repo[TETHER]])
        grant_before = GRANT_CREDITS - sum(draws[:-1])
        drawn = draws[-1]
    limit = REPO_SHARE + grant_before

    used = sum(per_repo.values())
    # Credits Tether drew on the grant are outside the seat's included 50.
    left = INCLUDED_CREDITS - (used - drawn)
    print(f"Greptile credits — seat {SEAT}, cycle {start} to {end} (exclusive)")
    if whole_seat:
        print(f"  used {used} of {INCLUDED_CREDITS} included; {left} remaining")
    else:
        # No seat balance is printed from a partial count: it would read HIGH by exactly what the
        # unread repositories spent. The maintainer reconciles the seat in Settings -> Usage.
        print(f"  seat total NOT READ: counted {', '.join(selected)} only")
    print()
    for repo in selected:
        share = ""
        if repo == TETHER:
            share = f"  (Tether share: {per_repo[repo]}/{REPO_SHARE})"
            if granted:
                share = (
                    f"  (Tether share: {per_repo[repo]}/{REPO_SHARE}; one-time grant: {drawn} "
                    f"used this cycle, {grant_before - drawn} of {GRANT_CREDITS} left; "
                    f"limit this cycle {limit})"
                )
        print(f"  {repo:<26} {per_repo[repo]:>2} credit(s) over {prs[repo]} PR(s){share}")

    if detail:
        print("\n  spent on:")
        for repo, number, count, when in sorted(detail, key=lambda row: row[3], reverse=True):
            plural = "s" if count != 1 else ""
            print(f"    {when}  {repo}#{number}  {count} credit{plural}")

    print(
        "\n  A proxy, not an invoice, and it can err BOTH ways: a TREX run that a rule started,"
        "\n  not a request, costs 3 credits that are not seen here, while a re-triggered review is"
        "\n  counted twice on the assumption - undocumented - that it bills twice. Reconcile"
        "\n  against Settings -> Usage before relying on the remaining figure."
    )
    if args.fail_over_budget and left <= 0:
        print("\nerror: the seat has no included credits left this cycle", file=sys.stderr)
        return EXIT_OVER_BUDGET
    if args.fail_over_share and per_repo[TETHER] >= limit:
        print(f"\nerror: Tether has used its limit of {limit} this cycle", file=sys.stderr)
        return EXIT_OVER_BUDGET
    # Tether's next review draws on the grant once its share is used and the grant has credit left;
    # then the seat's included credits are not what it spends (Codex P2 on #1369).
    from_grant = per_repo[TETHER] >= REPO_SHARE and grant_before - drawn > 0
    if args.fail_over_share and whole_seat and left <= 0 and not from_grant:
        # Tether's share is a ceiling inside the seat, never a credit of its own: where the whole
        # seat was read and it is empty, nothing may be spent however little Tether used (Codex
        # P2 on #1036). A partial count cannot see this, which is why ADR-0099 has the maintainer
        # reconcile the seat.
        print("\nerror: the seat has no included credits left this cycle", file=sys.stderr)
        return EXIT_OVER_BUDGET
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
