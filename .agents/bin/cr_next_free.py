#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
# The body mirrors the project-folder copy line for line, so the two stay diffable; its long
# output strings and comments are kept rather than rewrapped.
# ruff: noqa: E501, SIM102, B007
"""Take turns for CodeRabbit on bioedca/tether: one queue file per pull request.

The repository copy of the queue script that Claude threads have run from the maintainer's project
folder since 2026-11-10. It is here so the Codex lane can run it too (`docs/agents/handoff.md`).
The behaviour is the project-folder copy's, with two changes: the state folder is resolved rather
than fixed beside the script, and the docs-only route, off since 2026-11-14, is gone.

CodeRabbit gives the whole account an allowance of reviews each hour, set from its 7-day count of
attempts, and every lane shares it. The script paces at half of the allowance that CodeRabbit named
last. A request that is refused still counts as an attempt, so a lane asks only when it is its turn.

    <py> .agents/bin/cr_next_free.py --join 682 --title "Add the brand kit"
    <py> .agents/bin/cr_next_free.py --pr 682
    <py> .agents/bin/cr_next_free.py --leave 682
    <py> .agents/bin/cr_next_free.py            # the queue, for anyone

The state folder holds `queue/` (ONE FILE FOR EACH PULL REQUEST, `queue/682.md`) and `slots/` (one
file for each 10-minute window a lane holds). It is the first of: `--state DIR`, the environment
variable `TETHER_REVIEW_QUEUE`, the maintainer's project folder where it is mounted, and
`review-queue/` inside the checkout's common git directory, which every worktree of one clone
shares. A lane writes and deletes only its own queue file. Never edit `queue/` by hand.

The priority list is `priority.md` in the state folder when one is there, else the copy tracked at
`.agents/review-queue/priority.md`. Only a session the maintainer asked to order the queue edits it.

GitHub is only read: every call is a GET through `gh api`. Run it from a checkout of the repository.

Exit 0: ASK. It is your turn, no refusal is in force and no review is running.
Exit 1: WAIT. The output names the time for your wake-up.
Exit 2: unknown. The reads failed. Do not ask blindly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = "bioedca/tether"
BOT = "coderabbitai[bot]"
CHECK = "CodeRabbit"
WAIT = re.compile(r"available in (\d+) (minute|hour|second)s?", re.I)
REFUSAL = re.compile(r"rate limit|review limit reached", re.I)
#: CodeRabbit's reply to a review command that it accepted.
PERFORMED = re.compile(r"Action performed.{0,200}?review (triggered|finished)", re.I | re.S)
NUMBER = re.compile(r"/(?:pull|issues)/(\d+)#")
UNIT = {"second": 1, "minute": 60, "hour": 3600}
ENTRY = re.compile(r"^#(\d+) \| (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) \| (.*)$")
#: The priority list. One line for each pull request that goes before the join order:
#: `#<PR> | <why>`. The order of the lines is the order of the turns. A pull request that is not in
#: the list keeps its join order, after the listed ones. Only a session the maintainer asked to
#: order the queue writes this file; a PR's own session never edits it.
TRACKED_PRIORITY = Path(__file__).resolve().parents[1] / "review-queue" / "priority.md"
#: The maintainer's project folder, where Claude threads keep the shared queue. When it is mounted,
#: this copy and the project-folder copy read and write the same queue.
PROJECT_FOLDER = Path("/mnt/project-files/review-limit")
ENVIRONMENT = "TETHER_REVIEW_QUEUE"
RANKED = re.compile(r"^#(\d+)\b")

#: The allowance that CodeRabbit names at the end of each completed review, for example
#: "... set your current allowance at 10 reviews per hour." (2026-11-12 06:37Z, #777).
ALLOWANCE = re.compile(r"set your current allowance at (\d+) reviews? per hour", re.I)
#: The pace is half the allowance that CodeRabbit named last, and never more than MAX_PER_HOUR.
#: The allowance is adaptive: each attempt raises the 7-day count and can lower it again, so the
#: newest reading always wins. With no reading in the last 3 hours, or with a refusal newer than the
#: last reading, the pace falls back to SLOW. The pace is an estimate for the wake-up time and never
#: a reason to ask: the answer comes from what GitHub shows. Before 2026-11-12 it was a fixed 60
#: minutes (1 review an hour, the allowance on 2026-11-10). docs/agents/handoff.md, "The CodeRabbit queue".
MAX_PER_HOUR = 5
#: The pace reads the allowance and refusals of the last 3 hours only.
PACE_LOOKBACK = timedelta(hours=3)
#: Comments are read this far back, so that a refusal whose named wait is longer than the pace's 3
#: hours still holds the slot until that wait ends (Codex P1 on #1296: a refusal naming 6 hours,
#: posted 4 hours ago, fell outside a 3-hour read and the script said ASK). On 2026-11-19 the
#: repository had 445 comments updated in 24 hours, 5 pages of the 20 that gh_all reads.
REFUSAL_LOOKBACK = timedelta(hours=24)
SLOW = timedelta(minutes=60)
#: If the slot is open and the pull request at the head of the queue does not take it, the next
#: one may take it after this long. Without it, one sleeping thread stops the queue.
TAKEOVER = timedelta(minutes=10)
#: A reply that still says "triggered" after this long is taken as a review that ended without an
#: edit. Without it, one reply that CodeRabbit never edits stops the queue.
STUCK = timedelta(minutes=30)
#: A posted review request (`@coderabbitai full review`) that CodeRabbit has not answered yet counts
#: as a review that is running, for this long. CodeRabbit's reply comes 4 to 25 seconds after the
#: request. Before 2026-11-17 the script saw nothing in that gap, and on 2026-11-16 at 14:45Z the
#: request of #1024 came 4 seconds after the request of #1044 and was refused ("Review rate limited").
REQUEST_HOLD = timedelta(minutes=15)
REQUEST = re.compile(r"@coderabbitai\s+(full\s+)?review\b", re.I)
#: Printed when a lane joins and with each WAIT answer of a waiting pull request.
LOCAL = (
    "local check  author-side only. A local CodeRabbit run, where your lane has one, does not use "
    "your turn and satisfies no review leg."
)


def state_dir(explicit: str | None = None) -> Path:
    """The folder that holds `queue/` and `slots/` (see the module docstring for the order)."""
    if explicit:
        return Path(explicit)
    if os.environ.get(ENVIRONMENT):
        return Path(os.environ[ENVIRONMENT])
    if PROJECT_FOLDER.is_dir():
        return PROJECT_FOLDER
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        common = ""
    if not common:
        raise SystemExit("unknown: not inside a git checkout; pass --state DIR")
    return Path(common) / "review-queue"


#: Set by `main()` from `state_dir()`; module level so the functions below read one place.
STATE = Path(".")
QUEUE = STATE / "queue"
SLOTS = STATE / "slots"
PRIORITY = TRACKED_PRIORITY


def use_state(folder: Path) -> None:
    """Point the queue, the window files and the priority list at one state folder."""
    global STATE, QUEUE, SLOTS, PRIORITY
    STATE, QUEUE, SLOTS = folder, folder / "queue", folder / "slots"
    PRIORITY = folder / "priority.md" if (folder / "priority.md").is_file() else TRACKED_PRIORITY


def gh(path: str, **fields: str):
    cmd = ["gh", "api", "-X", "GET", path]
    for key, value in fields.items():
        cmd += ["-f", f"{key}={value}"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


#: Never read more pages than this; a listing that long is reported as unknown, not cut short.
MAX_PAGES = 20


def gh_all(path: str, **fields: str) -> list:
    """Every page of a list endpoint. Until 2026-11-18 the script read one page of 100 comments and
    100 open pull requests. With 78 open pull requests and many CodeRabbit summary comments edited on
    every push, a busy 3 hours could push the reply of the last review off that page; if the head of
    that pull request had moved too, the script saw no review and could say ASK early."""
    items, page = [], 1
    while True:
        batch = gh(path, per_page="100", page=str(page), **fields)
        items += batch
        if len(batch) < 100:
            return items
        page += 1
        if page > MAX_PAGES:
            raise ValueError(f"{path} has more than {MAX_PAGES} pages")


def parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def entries() -> list[tuple[datetime, int, str]]:
    """Every queue file that parses, oldest first. A file that does not parse is reported, not used."""
    found = []
    for path in sorted(QUEUE.glob("*.md")) if QUEUE.is_dir() else []:
        match = ENTRY.match(path.read_text(encoding="utf-8").strip())
        if match and path.stem == match.group(1):
            found.append((parse(match.group(2)), int(match.group(1)), match.group(3)))
        else:
            print(f"ignored      {path.name} does not hold one line '#<PR> | <time>Z | <title>'")
    return sorted(found)


def priority() -> dict[int, int]:
    """The rank of each pull request in `priority.md`, first line first. No file means no ranks."""
    ranks: dict[int, int] = {}
    try:
        lines = PRIORITY.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ranks
    for line in lines:
        match = RANKED.match(line.strip())
        if match and int(match.group(1)) not in ranks:
            ranks[int(match.group(1))] = len(ranks)
    return ranks


def join(number: int, title: str) -> int:
    if (path := QUEUE / f"{number}.md").exists():
        print(f"already in   {path.read_text(encoding='utf-8').strip()}")
        return 0
    QUEUE.mkdir(parents=True, exist_ok=True)
    path = QUEUE / f"{number}.md"
    line = f"#{number} | {stamp(datetime.now(UTC))} | {' '.join(title.split())}\n"
    try:
        with open(path, "x", encoding="utf-8") as handle:  # never replaces an existing entry
            handle.write(line)
    except FileExistsError:
        print(f"already in   {path.read_text(encoding='utf-8').strip()}")
        return 0
    print(f"joined       {line.strip()}")
    print(LOCAL)
    return 0


def leave(number: int) -> int:
    path = QUEUE / f"{number}.md"
    try:
        path.unlink()
        print(f"left         #{number}")
    except FileNotFoundError:
        print(f"not in queue #{number}")
    return 0


def pace(comments, now: datetime) -> tuple[timedelta, str]:
    """The time between two reviews: half the allowance that CodeRabbit named last, at most MAX_PER_HOUR.

    A refusal newer than the last reading, or no reading at all, gives SLOW.
    """
    reading, refused = None, None
    for comment in comments:
        if comment["user"]["login"] != BOT:
            continue
        body, when = comment["body"], parse(comment["updated_at"])
        if now - when > PACE_LOOKBACK:
            continue
        allowance = ALLOWANCE.search(body)
        # A refusal names the allowance too ("... set your current allowance at 1 review per hour",
        # #670), so test for a refusal first: its allowance line is not a new reading.
        if REFUSAL.search(body) and not PERFORMED.search(body):
            if refused is None or when > refused[0]:
                refused = (when, comment["html_url"])
        elif allowance:
            if reading is None or when > reading[0]:
                reading = (when, int(allowance.group(1)), comment["html_url"])
    # Any refusal in the last 3 hours gives SLOW. Before 2026-11-18 a newer allowance reading undid it,
    # but a reading is read from CodeRabbit's summary comment, which CodeRabbit edits again on every
    # push to that pull request, so an old reading could look newer than the refusal.
    if refused:
        return SLOW, f"a refusal at {refused[0]:%H:%M}Z is in the last 3 hours ({refused[1]})"
    if reading is None:
        return SLOW, "no allowance reading in the last 3 hours"
    per_hour = max(1, min(MAX_PER_HOUR, reading[1] // 2))
    return timedelta(minutes=60) / per_hour, (
        f"allowance {reading[1]}/hour at {reading[0]:%H:%M}Z ({reading[2]}); pace {per_hour}/hour"
    )


def take_window(open_at: datetime, k: int, mine: int, now: datetime) -> int:
    """Hold window k of the slot that opened at open_at; return the pull request that holds it."""
    SLOTS.mkdir(parents=True, exist_ok=True)
    for old in SLOTS.glob("*.lock"):
        try:
            if now.timestamp() - old.stat().st_mtime > 86400:
                old.unlink()
        except OSError:
            pass
    path = SLOTS / f"{open_at:%Y%m%dT%H%M%S}-w{k}.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        try:
            return int(path.read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return -1
    with os.fdopen(fd, "w") as handle:
        handle.write(f"{mine} {stamp(now)}\n")
    return mine


def status(mine: int | None) -> int:
    now = datetime.now(UTC)
    since = stamp(now - REFUSAL_LOOKBACK)
    try:
        comments = gh_all(
            f"repos/{REPO}/issues/comments", since=since, sort="updated", direction="desc"
        )
        pulls = gh_all(f"repos/{REPO}/pulls", state="open")

        def coderabbit_runs(pull):
            checks = gh(
                f"repos/{REPO}/commits/{pull['head']['sha']}/check-runs",
                check_name=CHECK,
                per_page="100",
            )
            return pull["number"], [c for c in checks["check_runs"] if c["name"] == CHECK]

        with ThreadPoolExecutor(max_workers=8) as pool:
            runs = dict(pool.map(coderabbit_runs, pulls))
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"unknown: could not read GitHub ({exc})")
        return 2

    # 1. CodeRabbit's comments: the last refusal that names a wait, and each reply to a review
    #    command. The reply is created when the review starts ("... review triggered") and edited
    #    when it ends ("... review finished"). It stays true after the head moves or the pull
    #    request merges, and the check runs of the current head do not: on 2026-11-10 a push to
    #    #670 hid the review that had started 11 minutes before, and this script said ASK.
    slot, paced = pace(comments, now)
    open_at, why = None, "nothing is known; the slot is taken as open since the first entry joined"
    running, served = [], {}
    # 0. Requests that CodeRabbit has not answered yet. A bot comment on the same pull request that
    #    was created after the request is the answer (performed or refused).
    answered: dict[int, datetime] = {}
    for comment in comments:
        target = NUMBER.search(comment["html_url"])
        if comment["user"]["login"] == BOT and target:
            number = int(target.group(1))
            answered[number] = max(
                answered.get(number, parse(comment["created_at"])), parse(comment["created_at"])
            )
    for comment in comments:
        target = NUMBER.search(comment["html_url"])
        if comment["user"]["login"] == BOT or not target or not REQUEST.search(comment["body"]):
            continue
        number, asked = int(target.group(1)), parse(comment["created_at"])
        if now - asked < REQUEST_HOLD and answered.get(number, asked) <= asked:
            running.append(number)
            if open_at is None or asked + slot > open_at:
                open_at, why = (
                    asked + slot,
                    f"request on #{number} posted {asked:%H:%M}Z and not answered yet",
                )
    for comment in comments:
        if comment["user"]["login"] != BOT:
            continue
        body = comment["body"]
        performed = PERFORMED.search(body)
        target = NUMBER.search(comment["html_url"])
        if performed and target:
            number, started = int(target.group(1)), parse(comment["created_at"])
            if performed.group(1).lower() == "finished":
                served[number] = max(served.get(number, started), parse(comment["updated_at"]))
            elif now - started < STUCK:
                running.append(number)
            if open_at is None or started + slot > open_at:
                open_at, why = (
                    started + slot,
                    f"review on #{number} started {started:%H:%M}Z (estimate: one review each {slot.total_seconds() / 60:.0f} minutes)",
                )
            continue
        if not REFUSAL.search(body):
            continue
        match = WAIT.search(body)
        if match:
            until = parse(comment["updated_at"]) + timedelta(
                seconds=int(match.group(1)) * UNIT[match.group(2).lower()]
            )
            if open_at is None or until > open_at:
                open_at, why = until, f"refusal {comment['html_url']}"

    # 2. The check runs of each open head show the same thing for the current head. `success` is
    #    a completed review; a refusal is `neutral` and a draft is `skipped`, and neither took anything.
    for number, checks in runs.items():
        for check in checks:
            if check["status"] in ("queued", "in_progress"):
                running.append(number)
                started = parse(check["started_at"])
            elif check["conclusion"] == "success":
                started = parse(check["started_at"])
                served[number] = parse(check["completed_at"])
            else:
                continue
            if open_at is None or started + slot > open_at:
                open_at, why = (
                    started + slot,
                    f"review on #{number} started {started:%H:%M}Z (estimate: one review each {slot.total_seconds() / 60:.0f} minutes)",
                )

    # 3. The queue: open pull requests that have not had a review since they joined.
    open_numbers = set(runs)
    queue = []
    for joined, number, title in entries():
        if number not in open_numbers:
            print(
                f"stale        #{number} is not an open pull request; its thread can leave the queue"
            )
        elif number in served and served[number] > joined:
            print(f"served       #{number} has its review; its thread can leave the queue")
        elif number in running:
            print(f"in review    #{number} has the current slot")
        else:
            queue.append((joined, number, title))

    # 3b. A pull request that conflicts with main may not ask (docs/agents/handoff.md), yet it
    #     held its place: each one ahead of the first thread that can ask cost a 10-minute window of
    #     every slot (2026-11-18: the slot of 14:35Z was taken at 15:29Z). Such an entry is parked: it
    #     keeps its file and gets its place back as soon as GitHub reads it as mergeable again.
    def conflict(entry):
        try:
            return entry[1], gh(f"repos/{REPO}/pulls/{entry[1]}").get("mergeable_state") == "dirty"
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return entry[1], False  # unreadable: keep the place

    with ThreadPoolExecutor(max_workers=8) as pool:
        dirty = {number for number, bad in pool.map(conflict, queue) if bad}
    for joined, number, title in queue:
        if number in dirty:
            print(
                f"parked       #{number} conflicts with main; it keeps no place until its thread merges a fresh origin/main"
            )
    queue = [entry for entry in queue if entry[1] not in dirty]
    # 4. The priority list goes before the join order. Python's sort is stable, so the pull requests
    #    that are not in the list stay in join order behind the listed ones.
    ranks = priority()
    queue.sort(key=lambda entry: ranks.get(entry[1], len(ranks)))
    if open_at is None and queue:
        open_at = min(joined for joined, _, _ in queue)

    print(f"now          {now:%Y-%m-%d %H:%M}Z")
    print(f"pace         one review each {slot.total_seconds() / 60:.0f} minutes, from {paced}")
    listed = [number for _, number, _ in queue if number in ranks]
    if listed:
        print(
            f"priority     {len(listed)} pull request(s) go first, in the order of priority.md (marked P); the others keep their join order"
        )
    for place, (joined, number, title) in enumerate(queue, start=1):
        note = "  <- you" if number == mine else ""
        mark = "P " if number in ranks else ""
        print(f"queue {place:<6} {mark}#{number} (joined {joined:%H:%M}Z) {title}{note}")
    if not queue:
        print("queue        empty")
    if running:
        print(
            "running      a CodeRabbit review is in progress on "
            + ", ".join(f"#{n}" for n in sorted(set(running)))
        )
    if open_at:
        print(f"slot opens   {open_at:%H:%M}Z, from {why}")

    if mine is None:
        return 1 if running or (open_at and open_at > now) else 0

    if mine in running:
        print(f"answer       WAIT. Your review on #{mine} is running. Do not ask again.")
        return 1
    place = next((i for i, (_, number, _) in enumerate(queue, start=1) if number == mine), None)
    if place is None and mine in dirty:
        print(
            f"answer       WAIT. #{mine} conflicts with main, so it has no place. Merge a fresh origin/main, push, and run this script again."
        )
        return 1
    if place is None:
        print(
            f'answer       WAIT. #{mine} is not in the queue. Run this script with --join {mine} --title "..." first.'
        )
        return 1
    wake = (open_at or now) + (place - 1) * slot + timedelta(minutes=2)
    if running:
        print(LOCAL)
        print(
            f"answer       WAIT. A review is running. Never ask now: a second request destroys it. Wake-up {wake:%H:%M}Z."
        )
        return 1
    if open_at and open_at > now:
        print(LOCAL)
        print(f"answer       WAIT. Wake-up {wake:%H:%M}Z (place {place} of {len(queue)}).")
        return 1
    # The take-over rule: place p may ask once (p - 1) windows of TAKEOVER have passed since the slot
    # opened, so a thread that does not wake up cannot stop the queue.
    k = int((now - (open_at or now)) / TAKEOVER)
    if place > 1 + k:
        turn = (open_at or now) + (place - 1) * TAKEOVER
        print(LOCAL)
        print(
            f"answer       WAIT. The slot is open, and {place - 1} pull request(s) are before you. Wake-up {turn:%H:%M}Z."
        )
        return 1
    ends = (open_at or now) + (k + 1) * TAKEOVER
    if ends - now < timedelta(minutes=2):
        print(LOCAL)
        print(
            f"answer       WAIT. This 10-minute window ends at {ends:%H:%M}Z, too close to ask safely. Run this script again after {ends:%H:%M}Z."
        )
        return 1
    # One ASK in each window. Before 2026-11-17 two threads could read ASK at the same moment (place 1
    # late, place 2 by take-over): #1044 and #1024 asked 4 seconds apart on 2026-11-16 14:45Z and the
    # second request was refused. The first thread that creates the window's file holds the window; an
    # exclusive create is atomic, so a second thread reads WAIT. The holder may run the script again.
    holder = take_window((open_at or now), k, mine, now)
    if holder != mine:
        print(LOCAL)
        print(
            f"answer       WAIT. #{holder} holds this window and is posting its request. Run this script again after {ends:%H:%M}Z."
        )
        return 1
    print(
        f"answer       ASK. It is your turn (place {place} of {len(queue)}); post before {ends:%H:%M}Z. Read the CodeRabbit check of your own head, then post the request at once."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Take turns for CodeRabbit on bioedca/tether.")
    parser.add_argument(
        "--pr", type=int, help="your pull request: print your place and your answer"
    )
    parser.add_argument("--join", type=int, metavar="PR", help="add your pull request to the queue")
    parser.add_argument("--title", default="", help="your thread title, with --join")
    parser.add_argument(
        "--leave", type=int, metavar="PR", help="remove your pull request from the queue"
    )
    parser.add_argument("--state", help="the state folder (default: see the module docstring)")
    args = parser.parse_args()
    use_state(state_dir(args.state))
    print(f"state        {STATE}")
    if args.join is not None:
        return join(args.join, args.title)
    if args.leave is not None:
        return leave(args.leave)
    return status(args.pr)


if __name__ == "__main__":
    sys.exit(main())
