# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Record merge-queue requests and build the train of candidates (issue #1266, ADR-0135).

Part 1 runs on bare runner Python and writes only request refs and candidate branches.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

BASE = "main"
REQUEST_PREFIX = "refs/merge-queue/pr-"
CANDIDATE_PREFIX = "mq/pr-"
DEFAULT_WINDOW = 5
TIMEOUT_SECONDS = 30
#: The variables that move Git to another repository, index or object store. A process started
#: from a Git hook inherits them. ``Git.run`` drops them so that every call stays in its checkout.
#: These are the location entries of ``git rev-parse --local-env-vars`` (git 2.43), without the
#: three configuration entries, plus ``GIT_NAMESPACE``, which moves the refs.
REPOSITORY_LOCATION_VARS = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)

CANDIDATE = "candidate"
HELD = "held"
REMOVE = "remove"

_SHA = re.compile(r"[0-9a-f]{40}")
_NAME, _EMAIL = "tether-merge-queue", "tether-merge-queue@users.noreply.github.com"

#: ``(method, url, json body or None) -> (status, body bytes)``
Transport = Callable[[str, str, bytes | None], tuple[int, bytes]]


class QueueError(RuntimeError):
    """A git or GitHub call failed. This is an error, never a verdict about a pull request."""


class Refused(Exception):
    """``join`` declined a pull request, and the message is the reason."""


@dataclass(frozen=True)
class Request:
    """One recorded request: pull request ``pr`` at ``head`` with squash message ``message``."""

    pr: int
    head: str
    message: str
    joined: int  # the committer time of the request commit, in seconds since the epoch


@dataclass(frozen=True)
class Step:
    """A candidate tree and commit, a hold with its blocker, or a removal with its reason."""

    pr: int
    state: str
    head: str
    tree: str | None = None
    commit: str | None = None
    reason: str | None = None
    blocked_by: int | None = None
    paths: tuple[str, ...] = ()


def urllib_transport(token: str | None) -> Transport:
    """Return the transport that calls the GitHub REST API with ``token``."""

    def send(method: str, url: str, body: bytes | None) -> tuple[int, bytes]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "tether-merge-queue"}
        headers |= {"X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, b""
        except urllib.error.URLError as error:
            raise QueueError(f"{method} {url} failed: {error.reason}") from error

    return send


class GitHub:
    """The few GitHub calls the queue makes. Every call of the module goes through here."""

    def __init__(self, repo: str, transport: Transport) -> None:
        self.repo = repo
        self._base = f"https://api.github.com/repos/{repo}"
        self._transport = transport

    def _call(
        self, method: str, path: str, payload: Any = None, accept=(200, 201), expect=dict
    ) -> tuple[int, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        status, data = self._transport(method, self._base + path, body)
        if status not in accept:
            raise QueueError(f"{method} {self._base}{path} returned HTTP {status}")
        if expect is None or status == 404:
            return status, None
        try:
            reply = json.loads(data)
        except ValueError as error:
            raise QueueError(f"{method} {path} returned invalid JSON") from error
        if not isinstance(reply, expect):
            raise QueueError(f"{method} {path} did not return a JSON {expect.__name__}")
        return status, reply

    def pull(self, number: int) -> dict[str, Any] | None:
        """Return pull request ``number``, or ``None`` when GitHub has none."""
        status, pull = self._call("GET", f"/pulls/{number}", accept=(200, 404))
        if status == 404:
            return None
        match pull:
            case {
                "state": "open" | "closed",
                "draft": bool(),
                "base": {"ref": str()},
                "head": {"sha": str(), "repo": None | {"full_name": str()}},
            }:
                return pull
        raise QueueError(f"GitHub returned invalid pull request data for #{number}")

    def create_request(self, number: int, head: str, message: str) -> bool:
        """Record the request of pull request ``number``. ``False`` means it already has one."""
        tree = self._call("GET", f"/git/commits/{head}")[1].get("tree")
        if not isinstance(tree, dict) or not isinstance(tree.get("sha"), str):
            raise QueueError(f"GitHub did not return a tree SHA for {head}")
        payload = {"message": message, "tree": tree["sha"], "parents": [head]}
        commit = self._call("POST", "/git/commits", payload)[1].get("sha")
        if not isinstance(commit, str):
            raise QueueError("GitHub did not return a SHA for the request commit")
        ref = f"{REQUEST_PREFIX}{number}"
        payload = {"ref": ref, "sha": commit}
        if self._call("POST", "/git/refs", payload, accept=(201, 422), expect=None)[0] == 201:
            return True
        # GitHub answers 422 for an existing ref and also for a write that it rejects, so look.
        if self._has_ref(ref):
            return False
        raise QueueError(f"GitHub did not create {ref} (HTTP 422) and the ref does not exist")

    def delete_ref(self, ref: str) -> bool:
        """Delete ``ref`` (``refs/...``). ``False`` means it did not exist."""
        if self._call("DELETE", f"/git/{ref}", accept=(204, 404, 422), expect=None)[0] == 204:
            return True
        # GitHub answers 422 for an absent ref and also for a delete that it rejects, so look.
        if self._has_ref(ref):
            raise QueueError(f"GitHub did not delete {ref}")
        return False

    def _has_ref(self, ref: str) -> bool:
        """Return whether ``ref`` (``refs/...``) exists, by its exact name."""
        path = f"/git/matching-refs/{ref.removeprefix('refs/')}"
        found = self._call("GET", path, expect=list)[1]
        if not all(isinstance(item, dict) and isinstance(item.get("ref"), str) for item in found):
            raise QueueError(f"GitHub returned invalid matching refs for {ref}")
        return any(item["ref"] == ref for item in found)  # `pr-7` also matches `pr-70`


class Git:
    """The git calls the queue makes, in the checkout ``cwd`` against ``remote``."""

    def __init__(self, cwd: Path | str = ".", remote: str = "origin") -> None:
        self.cwd = str(cwd)
        self.remote = remote

    def run(
        self, *args: str, env: Mapping[str, str] | None = None, ok: Sequence[int] = (0,)
    ) -> subprocess.CompletedProcess[str]:
        """Run ``git`` with ``args`` in ``cwd``. A status outside ``ok`` raises ``QueueError``.

        The ambient ``REPOSITORY_LOCATION_VARS`` are dropped; other variables and ``env`` pass.
        """
        ambient = {k: v for k, v in os.environ.items() if k not in REPOSITORY_LOCATION_VARS}
        command, merged = ["git", *args], {**ambient, **(env or {})}
        done = subprocess.run(  # noqa: S603
            command, cwd=self.cwd, env=merged, capture_output=True, encoding="utf-8", check=False
        )
        if done.returncode not in ok:
            raise QueueError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
        return done

    def fetch(self) -> None:
        """Fetch ``main``, the request refs and the candidate branches, and prune them."""
        local = f"refs/remotes/{self.remote}"
        specs = [f"+refs/heads/{BASE}:{local}/{BASE}", f"+{REQUEST_PREFIX}*:{REQUEST_PREFIX}*"]
        specs.append(f"+refs/heads/{CANDIDATE_PREFIX}*:{local}/{CANDIDATE_PREFIX}*")
        self.run("fetch", "--prune", "--no-tags", self.remote, *specs)

    def rev(self, name: str) -> str | None:
        """Return the commit that ``name`` resolves to, or ``None``."""
        done = self.run("rev-parse", "--verify", "--quiet", f"{name}^{{commit}}", ok=(0, 1))
        return done.stdout.strip() or None

    def tree(self, commit: str) -> str:
        """Return the tree of ``commit``."""
        return self.run("rev-parse", f"{commit}^{{tree}}").stdout.strip()

    def merge_tree(self, ours: str, theirs: str) -> tuple[str | None, tuple[str, ...]]:
        """Merge without a worktree and return ``(tree, ())`` or ``(None, conflict_paths)``."""
        done = self.run(
            "merge-tree", "--write-tree", "--name-only", "--no-messages", ours, theirs, ok=(0, 1)
        )
        lines = done.stdout.splitlines()
        if done.returncode == 0:
            return lines[0], ()
        return None, tuple(line for line in lines[1:] if line)

    def commit_tree(self, tree: str, parent: str, message: str, date: int) -> str:
        """Commit ``tree`` on ``parent`` with the queue identity, at epoch second ``date``."""
        # A fixed identity and date make `plan` repeatable: same inputs, same commits.
        env = {f"GIT_{who}_NAME": _NAME for who in ("AUTHOR", "COMMITTER")}
        env |= {f"GIT_{who}_EMAIL": _EMAIL for who in ("AUTHOR", "COMMITTER")}
        env |= {f"GIT_{who}_DATE": f"@{date} +0000" for who in ("AUTHOR", "COMMITTER")}
        return self.run("commit-tree", tree, "-p", parent, "-m", message, env=env).stdout.strip()

    def push(self, commit: str, branch: str) -> None:
        """Point ``branch`` on the remote at ``commit``."""
        self.run("push", "--force", self.remote, f"{commit}:refs/heads/{branch}")

    def candidates(self) -> dict[int, str]:
        """Return the candidate branches the last fetch saw, as ``{pull request: commit}``."""
        prefix = f"refs/remotes/{self.remote}/{CANDIDATE_PREFIX}"
        names = ((ref[len(prefix) :], commit) for ref, commit in self.refs(prefix))
        return {int(name): commit for name, commit in names if name.isdigit()}

    def refs(self, prefix: str) -> list[tuple[str, str]]:
        """Return ``(ref, commit)`` for each local ref whose name starts with ``prefix``."""
        # `for-each-ref` matches whole path parts, so list the folder and filter the names.
        folder = prefix.rsplit("/", 1)[0] + "/"
        out = self.run("for-each-ref", "--format=%(refname) %(objectname)", folder).stdout
        pairs = [tuple(line.split()) for line in out.splitlines()]
        return [(ref, commit) for ref, commit in pairs if ref.startswith(prefix)]


def read_requests(git: Git) -> list[Request]:
    """Return the recorded requests, the oldest first: the order of the train."""
    requests: list[Request] = []
    for ref, commit in git.refs(REQUEST_PREFIX):
        number = ref[len(REQUEST_PREFIX) :]
        # `%ct` is seconds since the epoch. The text of `%cI` differs between git versions.
        shown = git.run("show", "-s", "--format=%P%x00%ct%x00%B", commit).stdout
        parents, joined, message = shown.split("\x00", 2)
        if not number.isdigit() or len(parents.split()) != 1:
            raise QueueError(f"{ref} is not a merge-queue request (one parent, numeric name)")
        requests.append(Request(int(number), parents.strip(), message.rstrip("\n"), int(joined)))
    return sorted(requests, key=lambda r: (r.joined, r.pr))


def join(github: GitHub, number: int, head: str, message: str) -> None:
    """Record the pinned head and squash message, or raise ``Refused`` with the reason."""
    if not _SHA.fullmatch(head):
        raise Refused("the head must be the full 40-hex commit that the review read")
    if not message.strip():
        raise Refused("the squash message is empty")
    pull = github.pull(number)
    if pull is None:
        raise Refused(f"#{number} is not a pull request")
    if pull.get("state") != "open":
        raise Refused(f"#{number} is not open")
    if pull.get("draft"):
        raise Refused(f"#{number} is a draft")
    if pull["base"]["ref"] != BASE:
        raise Refused(f"#{number} targets {pull['base']['ref']}, not {BASE}")
    if (pull["head"].get("repo") or {}).get("full_name") != github.repo:
        # A candidate branch runs workflows with this repository's permissions.
        raise Refused(f"#{number} comes from a fork")
    if pull["head"]["sha"] != head:
        raise Refused(f"the head of #{number} is {pull['head']['sha']}, not {head}")
    if not github.create_request(number, head, message):
        raise Refused(f"#{number} already has a request")


def leave(github: GitHub, number: int) -> dict[str, bool]:
    """Delete the request and the candidate of pull request ``number``, if they exist."""
    refs = {"request": REQUEST_PREFIX, "candidate": f"refs/heads/{CANDIDATE_PREFIX}"}
    return {name: github.delete_ref(f"{prefix}{number}") for name, prefix in refs.items()}


def plan(
    git: Git, base: str, requests: Sequence[Request], pulls: Mapping[int, Mapping[str, Any] | None]
) -> list[Step]:
    """Decide one state for each request, in train order.

    ``git`` holds ``base`` (the commit of ``main``) and every request head. ``requests`` is
    oldest first. ``pulls`` contains GitHub's response for each request, or ``None``.

    A request is removed with one reason, checked in this order: ``closed`` (closed, or GitHub
    has no such pull request), ``retargeted`` (it no longer targets ``main``), ``draft`` (it was
    converted to a draft), ``head-moved`` (its head is not the pinned head) and
    ``conflict-with-main``. The requests behind a removed one are built without it.
    """
    steps: list[Step] = []
    train: list[tuple[int, str]] = []  # the candidates so far: (pull request, commit)
    tip = base
    for request in requests:
        pull = pulls.get(request.pr)
        tree: str | None = None
        if pull is None or pull.get("state") != "open":
            step = Step(request.pr, REMOVE, request.head, reason="closed")
        elif pull["base"]["ref"] != BASE:
            # A merge goes to the base branch, so a candidate of `main` is no evidence for it.
            step = Step(request.pr, REMOVE, request.head, reason="retargeted")
        elif pull.get("draft"):
            step = Step(request.pr, REMOVE, request.head, reason="draft")
        elif pull["head"]["sha"] != request.head:
            step = Step(request.pr, REMOVE, request.head, reason="head-moved")
        else:
            tree, paths = git.merge_tree(tip, request.head)
            step = Step(request.pr, HELD, request.head, paths=paths)
        if tree is not None:
            message = f"merge queue candidate for #{request.pr}\n\nMerge-Queue-Head: {request.head}"
            tip = git.commit_tree(tree, tip, message, request.joined)
            train.append((request.pr, tip))
            step = Step(request.pr, CANDIDATE, request.head, tree=tree, commit=tip)
        elif step.state == HELD:
            alone, base_paths = git.merge_tree(base, request.head)
            if alone is None:
                step = replace(step, state=REMOVE, reason="conflict-with-main", paths=base_paths)
            else:
                # It merges with `main` alone, so a pull request ahead is in the way: wait.
                ahead = (n for n, c in train if git.merge_tree(c, request.head)[0] is None)
                step = replace(step, blocked_by=next(ahead))
        steps.append(step)
    return steps


def build(git: Git, github: GitHub, steps: Sequence[Step], window: int) -> dict[str, list[int]]:
    """Make the candidate branches match the first ``window`` candidates of the train.

    Keep branches whose trees match, with their runs, and remove branches outside the window.
    """
    wanted = {s.pr: s for s in [s for s in steps if s.state == CANDIDATE][:window]}
    existing = git.candidates()
    built: dict[str, list[int]] = {"pushed": [], "kept": [], "deleted": []}
    for number, step in wanted.items():
        tip = existing.get(number)
        if tip is not None and git.tree(tip) == step.tree:
            built["kept"].append(number)
        elif step.commit is not None:
            git.push(step.commit, f"{CANDIDATE_PREFIX}{number}")
            built["pushed"].append(number)
    for number in sorted(existing):
        if number not in wanted:
            github.delete_ref(f"refs/heads/{CANDIDATE_PREFIX}{number}")
            built["deleted"].append(number)
    return built


def _summary(report: Mapping[str, Any]) -> str:
    steps = report["steps"]
    lines = [f"## Merge queue\n\n`{BASE}` is `{report['base']}`. Requests: **{len(steps)}**.\n"]
    if steps:
        lines += ["| pull request | state | detail |", "|---|---|---|"]
    for step in steps:
        detail = step["reason"] or (step["tree"] and f"tree `{step['tree']}`") or ""
        if step["blocked_by"]:
            detail = f"waits for #{step['blocked_by']}"
        if step["paths"]:
            detail += " (" + ", ".join(f"`{p}`" for p in step["paths"]) + ")"
        lines.append(f"| #{step['pr']} | {step['state']} | {detail} |")
    for key, numbers in report.get("built", {}).items():
        lines.append(f"\nCandidates {key}: " + (", ".join(f"#{n}" for n in numbers) or "none"))
    return "\n".join(lines) + "\n"


def _reconcile(git: Git, github: GitHub, window: int | None) -> dict[str, Any]:
    git.fetch()
    base = git.rev(f"refs/remotes/{git.remote}/{BASE}")
    if base is None:
        raise QueueError(f"the remote has no {BASE}")
    requests = read_requests(git)
    steps = plan(git, base, requests, {r.pr: github.pull(r.pr) for r in requests})
    report: dict[str, Any] = {"base": base, "steps": [asdict(s) for s in steps]}
    if window is not None:
        report["built"] = build(git, github, steps, window)
    return report


def main(
    argv: Sequence[str] | None = None, github: GitHub | None = None, git: Git | None = None
) -> int:
    """Run one command. Exit 0 on success, 2 on an error, 3 when ``join`` refuses."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    commands = parser.add_subparsers(dest="command", required=True)
    joining = commands.add_parser("join", help="record a request")
    joining.add_argument("--pr", type=int, required=True)
    joining.add_argument("--sha", required=True, help="the 40-hex head the clean review read")
    joining.add_argument("--message-file", required=True, help="the squash message")
    leaving = commands.add_parser("leave", help="delete a request and its candidate")
    leaving.add_argument("--pr", type=int, required=True)
    commands.add_parser("plan", help="print the train; write nothing")
    building = commands.add_parser("build", help="plan, then push and delete candidates")
    building.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    args = parser.parse_args(argv)

    try:
        if github is None:
            if not args.repo:
                raise QueueError("no repository: pass --repo or set GITHUB_REPOSITORY")
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            github = GitHub(args.repo, urllib_transport(token))
        if args.command == "join":
            message = Path(args.message_file).read_text(encoding="utf-8")
            join(github, args.pr, args.sha, message.strip("\n"))
            report: dict[str, Any] = {"joined": args.pr, "head": args.sha}
        elif args.command == "leave":
            report = {"left": args.pr, **leave(github, args.pr)}
        else:
            window = args.window if args.command == "build" else None
            report = _reconcile(git or Git(), github, window)
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a", encoding="utf-8") as handle:
                    handle.write(_summary(report))
    except Refused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 3
    except QueueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (OSError, http.client.HTTPException, UnicodeError) as error:
        print(f"error: {args.command} failed ({type(error).__name__})", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
