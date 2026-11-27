# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Record merge-queue requests, build the train of candidates and merge it (#1266, #1268).

It runs on bare runner Python (ADR-0135). In ``observe`` mode it writes only request refs and
candidate branches. In ``on`` mode the reconcile also judges the candidates, merges the front of
the train, removes a pull request that cannot merge and writes the statuses, comments and label.
The reconcile is the one owner of every write but the request ref: a ``leave`` deletes only that.

Before it merges or removes, the reconcile *takes* the request (#1480): one atomic push deletes the
request ref, if it still holds the request the pass read, and creates a pending ref that records
what the run is doing. A ``leave`` or a rejoin that lands first makes the push fail, so the run
writes nothing; one that lands later finds no request to change. A run that stops halfway leaves
the pending ref, and the next run finishes it, writing the removal comment exactly once.
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
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_postmerge_checks as checks  # noqa: E402  (a sibling script, standard library only)

BASE = "main"
REQUEST_PREFIX = "refs/merge-queue/pr-"
PENDING_PREFIX = "refs/merge-queue/pending/pr-"  # a taken request: a merge or a removal under way
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
MERGE = "merge"  # the other action a pending ref records

OBSERVE, ON = "observe", "on"
PASSED, FAILED, PENDING = "passed", "failed", "pending"
CONTEXT = LABEL = "merge-queue"  # the commit status context and the label
MAX_PASSES = 20  # a pass that merges or removes builds the train again; each pass changes one

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
    commit: str = field(default="", compare=False)  # the request commit itself


@dataclass(frozen=True)
class Pending:
    """A taken request: the ref ``PENDING_PREFIX + pr`` holds ``commit``, whose one parent is the
    request commit. ``comment`` is the removal comment still to write, or empty."""

    pr: int
    action: str  # MERGE or REMOVE
    commit: str
    request: Request
    comment: str = ""


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


def run_url() -> str | None:
    """Return the address of this workflow run, or ``None`` outside Actions."""
    env = os.environ
    parts = [env.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")]
    return "{}/{}/actions/runs/{}".format(*parts) if all(parts) else None


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
            return error.code, error.read()
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

    def tree_of(self, commit: str) -> str:
        """Return the tree of ``commit`` as GitHub has it."""
        tree = self._call("GET", f"/git/commits/{commit}")[1].get("tree")
        if not isinstance(tree, dict) or not isinstance(tree.get("sha"), str):
            raise QueueError(f"GitHub did not return a tree SHA for {commit}")
        return tree["sha"]

    def required_contexts(self) -> list[str]:
        """Return the contexts the active rules of ``main`` require, without ``merge-queue``.

        A failed read, or rules that require nothing, is an error: there is no verdict then.
        """
        rules = self._call("GET", f"/rules/branches/{BASE}?per_page=100", expect=list)[1]
        contexts: list[str] = []
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            listed = (rule.get("parameters") or {}).get("required_status_checks")
            if not isinstance(listed, list):
                raise QueueError("GitHub returned an invalid required_status_checks rule")
            for item in listed:
                if not isinstance(item, dict) or not isinstance(item.get("context"), str):
                    raise QueueError("GitHub returned an invalid required status check")
                if item["context"] != CONTEXT and item["context"] not in contexts:
                    contexts.append(item["context"])
        if not contexts:
            raise QueueError(f"the active rules of {BASE} require no check; refusing a verdict")
        return contexts

    def check_runs(self, commit: str) -> list[dict[str, Any]]:
        """Return every check run at ``commit``, all pages."""
        runs: list[dict[str, Any]] = []
        for page in range(1, 11):  # 1000 runs; the endpoint itself stops at 1000 suites
            path = f"/commits/{commit}/check-runs?per_page=100&page={page}"
            listed = self._call("GET", path)[1].get("check_runs")
            if not isinstance(listed, list):
                raise QueueError(f"GitHub returned invalid check runs for {commit}")
            runs += [run for run in listed if isinstance(run, dict)]
            if len(listed) < 100:
                break
        return runs

    def set_status(self, commit: str, state: str, description: str) -> None:
        """Write the commit status ``merge-queue`` on ``commit``."""
        payload = {"state": state, "context": CONTEXT, "description": description[:140]}
        if url := run_url():
            payload["target_url"] = url
        self._call("POST", f"/statuses/{commit}", payload, accept=(201,))

    def comment(self, number: int, body: str) -> None:
        self._call("POST", f"/issues/{number}/comments", {"body": body}, accept=(201,))

    def add_label(self, number: int) -> None:
        self._call("POST", f"/issues/{number}/labels", {"labels": [LABEL]}, expect=list)

    def remove_label(self, number: int) -> None:
        """Take the label ``merge-queue`` off ``number``; one it does not carry is no error."""
        self._call("DELETE", f"/issues/{number}/labels/{LABEL}", accept=(200, 404), expect=None)

    def labelled(self) -> list[int]:
        """Return the pull requests and issues that carry the label ``merge-queue``."""
        path = f"/issues?labels={LABEL}&state=all&per_page=100"
        found = self._call("GET", path, expect=list)[1]
        if not all(isinstance(i, dict) and isinstance(i.get("number"), int) for i in found):
            raise QueueError("GitHub returned invalid labelled issues")
        return [item["number"] for item in found]

    def main_commit(self) -> str:
        """Return the commit ``main`` points at on GitHub now."""
        sha = (self._call("GET", f"/git/ref/heads/{BASE}")[1].get("object") or {}).get("sha")
        if not isinstance(sha, str):
            raise QueueError(f"GitHub returned no commit for {BASE}")
        return sha

    def commented(self, number: int, text: str) -> bool:
        """Return whether a comment on ``number`` contains ``text``, reading every page."""
        page = 1
        while True:
            path = f"/issues/{number}/comments?per_page=100&page={page}"
            listed = self._call("GET", path, expect=list)[1]
            if any(isinstance(c, dict) and text in str(c.get("body") or "") for c in listed):
                return True
            if len(listed) < 100:
                return False
            page += 1

    def merge(self, number: int, head: str, title: str, message: str) -> tuple[str | None, str]:
        """Squash-merge ``number`` pinned to ``head``: ``(new main commit, "")``, or ``(None,
        GitHub's message)`` when GitHub refuses."""
        payload = {"merge_method": "squash", "sha": head}
        payload |= {"commit_title": title, "commit_message": message}
        status, data = self._transport(
            "PUT", f"{self._base}/pulls/{number}/merge", json.dumps(payload).encode("utf-8")
        )
        try:
            reply = json.loads(data)
        except ValueError:
            reply = None
        reply = reply if isinstance(reply, dict) else {}
        if status == 200 and reply.get("merged") is True and isinstance(reply.get("sha"), str):
            return reply["sha"], ""
        if status in (405, 409, 422):
            return None, str(reply.get("message") or f"HTTP {status}")
        raise QueueError(f"PUT /pulls/{number}/merge returned HTTP {status}")

    def create_request(self, number: int, head: str, message: str) -> bool:
        """Record the request of pull request ``number``. ``False`` means it already has one."""
        payload = {"message": message, "tree": self.tree_of(head), "parents": [head]}
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
        specs.append(f"+{PENDING_PREFIX}*:{PENDING_PREFIX}*")
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

    def update_refs(self, moves: Mapping[str, tuple[str | None, str | None]]) -> bool:
        """Apply ``{ref: (new, expected)}`` on the remote in one atomic push, or nothing.

        ``new`` ``None`` deletes the ref; ``expected`` ``None`` means it must not exist. ``False``
        means a ref no longer held what was expected, so nothing changed. Any other failure,
        a server without atomic pushes included, raises ``QueueError``.
        """
        leases = [f"--force-with-lease={ref}:{old or ''}" for ref, (_, old) in moves.items()]
        specs = [f"{new or ''}:{ref}" for ref, (new, _) in moves.items()]
        done = self.run("push", "--atomic", "--porcelain", *leases, self.remote, *specs, ok=(0, 1))
        if done.returncode == 0:
            return True
        listed = self.run("ls-remote", self.remote, *moves).stdout.splitlines()
        now = {ref: commit for commit, ref in (line.split("\t", 1) for line in listed)}
        if any(now.get(ref) != old for ref, (_, old) in moves.items()):
            return False
        raise QueueError(f"git push of {', '.join(moves)} failed: {done.stdout.strip()}")

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


def _commit(git: Git, ref: str, prefix: str, commit: str, kind: str) -> tuple[int, str, int, str]:
    """Return ``(number, parent, committer time, message)`` of a one-parent queue commit."""
    number = ref[len(prefix) :]
    # `%ct` is seconds since the epoch. The text of `%cI` differs between git versions.
    shown = git.run("show", "-s", "--format=%P%x00%ct%x00%B", commit).stdout
    parents, joined, message = shown.split("\x00", 2)
    if not number.isdigit() or len(parents.split()) != 1:
        raise QueueError(f"{ref} is not a merge-queue {kind} (one parent, numeric name)")
    return int(number), parents.strip(), int(joined), message.rstrip("\n")


def _request(git: Git, ref: str, commit: str) -> Request:
    number, head, joined, message = _commit(git, ref, REQUEST_PREFIX, commit, "request")
    return Request(number, head, message, joined, commit)


def read_requests(git: Git) -> list[Request]:
    """Return the recorded requests, the oldest first: the order of the train."""
    requests = [_request(git, ref, commit) for ref, commit in git.refs(REQUEST_PREFIX)]
    return sorted(requests, key=lambda r: (r.joined, r.pr))


def read_pending(git: Git) -> list[Pending]:
    """Return the taken requests that a run left unfinished, by pull request number."""
    found: list[Pending] = []
    for ref, commit in git.refs(PENDING_PREFIX):
        number, parent, _, message = _commit(git, ref, PENDING_PREFIX, commit, "pending ref")
        subject, _, comment = message.partition("\n\n")
        action = subject.removeprefix("merge-queue ").removesuffix(f" #{number}")
        if action not in (MERGE, REMOVE):
            raise QueueError(f"{ref} is not a pending merge or removal")
        request = _request(git, f"{REQUEST_PREFIX}{number}", parent)
        found.append(Pending(number, action, commit, request, comment))
    return sorted(found, key=lambda p: p.pr)


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
    """Delete the request of pull request ``number``, if it exists.

    The candidate branch, its status and the label are the reconcile's: the reconcile that
    follows every ``leave`` deletes them. One owner means that a reconcile which read the
    requests before the ``leave`` is followed by one that reads after it, and the last writer of
    ``mq/pr-N`` is always a reconcile that saw the ``leave``. A request the reconcile has taken
    is gone already, so a ``leave`` after the take finds nothing and the merge or removal goes on.
    """
    return {"request": github.delete_ref(f"{REQUEST_PREFIX}{number}")}


def plan(
    git: Git,
    base: str,
    requests: Sequence[Request],
    pulls: Mapping[int, Mapping[str, Any] | None],
    repo: str,
) -> list[Step]:
    """Decide one state for each request, in train order.

    ``git`` holds ``base`` (the commit of ``main``) and every request head. ``requests`` is
    oldest first. ``pulls`` contains GitHub's response for each request, or ``None``.

    A request is removed with one reason, checked in this order: ``closed`` (closed, or GitHub
    has no such pull request), ``retargeted`` (it no longer targets ``main``), ``fork`` (its head
    is not in this repository), ``draft`` (it was converted to a draft), ``head-moved`` (its head
    is not the pinned head) and ``conflict-with-main``. The requests behind a removed one are
    built without it.
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
        elif (pull["head"].get("repo") or {}).get("full_name") != repo:
            # `join` checks this too; a candidate branch runs with this repository's rights.
            step = Step(request.pr, REMOVE, request.head, reason="fork")
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


def build(
    git: Git, github: GitHub, steps: Sequence[Step], window: int, tips: dict[int, str] | None = None
) -> dict[str, list[int]]:
    """Make the candidate branches match the first ``window`` candidates of the train.

    Keep branches whose trees match, with their runs, and remove branches outside the window.
    ``tips``, when given, receives the commit each wanted branch holds now: its runs decide.
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
            tip = step.commit
        if tips is not None and tip is not None:
            tips[number] = tip
    for number in sorted(existing):
        if number not in wanted:
            github.delete_ref(f"refs/heads/{CANDIDATE_PREFIX}{number}")
            built["deleted"].append(number)
    return built


def verdict(
    runs: Sequence[Mapping[str, Any]], contexts: Sequence[str]
) -> tuple[str, list[tuple[str, str, str]]]:
    """Judge one candidate: ``(passed | failed | pending, [(context, conclusion, url)])``.

    The deciding run and the ``commitlint`` exemption are ``verify_postmerge_checks``'s. A
    context with no run, or whose deciding run has not finished, is pending, never passed.
    """
    failed: list[tuple[str, str, str]] = []
    pending = False
    for context in contexts:
        run = checks.deciding_run([dict(r) for r in runs], context)
        if run is None or run.get("status") != "completed":
            pending = True
        elif run.get("conclusion") not in checks.accepted(context):
            failed.append((context, str(run.get("conclusion")), str(run.get("html_url") or "")))
    return (FAILED if failed else PENDING if pending else PASSED), failed


def judge_train(raw: Sequence[str]) -> list[str]:
    """Apply the train rule to raw verdicts, front first: a failure counts only when every
    candidate ahead of it passed, because a failure can come from a pull request ahead."""
    judged, clear = [], True
    for state in raw:
        judged.append(PENDING if state == FAILED and not clear else state)
        clear = clear and state == PASSED
    return judged


REJOIN = (
    "To rejoin, merge `main` into the branch, fix the cause, get the closing review on the new "
    "head, then join again at that head with "
    "`gh workflow run merge-queue.yml --ref main -f action=join -f pr={pr} -f sha=<new head> "
    '-f message="<squash message>"`. The go-ahead you had still stands, and the pull requests '
    "behind this one go on without it."
)


def removal_comment(step: Step, failed: Sequence[tuple[str, str, str]] = (), said: str = "") -> str:
    """The one comment a removal writes: the reason, then the way back."""
    if failed:
        lines = [f"- `{c}` concluded `{how}`" + (url and f": {url}") for c, how, url in failed]
        why = "its candidate failed these required checks:\n\n" + "\n".join(lines)
    elif said:
        why = "GitHub refused the merge: \n\n> " + said.replace("\n", "\n> ")
    elif step.reason == "conflict-with-main":
        why = "it conflicts with `main` in " + ", ".join(f"`{p}`" for p in step.paths)
    elif step.reason == "head-moved":
        why = f"its head moved after the join, which pinned `{step.head}`"
    else:
        why = {"retargeted": "it no longer targets `main`", "draft": "it became a draft"}.get(
            step.reason or "", "its head is not in this repository"
        )
    return f"The merge queue removed this pull request: {why}\n\n" + REJOIN.format(pr=step.pr)


def _marker(git: Git, request: Request, action: str, comment: str) -> str:
    """Make the commit of a pending ref: the request commit as parent, the action and comment."""
    message = f"merge-queue {action} #{request.pr}" + (f"\n\n{comment}" if comment else "")
    return git.commit_tree(git.tree(request.commit), request.commit, message, request.joined)


def take(git: Git, request: Request, action: str, comment: str = "") -> Pending | None:
    """Swap the request ref for a pending ref in one atomic push, before any other write.

    ``None``, and no write, when the request ref no longer holds ``request``: a leave or a rejoin
    landed after the pass read it, and the new request is judged on its own.
    """
    marker = _marker(git, request, action, comment)
    moves = {
        f"{REQUEST_PREFIX}{request.pr}": (None, request.commit),
        f"{PENDING_PREFIX}{request.pr}": (marker, None),
    }
    return Pending(request.pr, action, marker, request, comment) if git.update_refs(moves) else None


def _restore(git: Git, pending: Pending) -> None:
    """Give a taken merge back to the queue: the request again, unless a rejoin replaced it."""
    ref, mark = f"{REQUEST_PREFIX}{pending.pr}", f"{PENDING_PREFIX}{pending.pr}"
    if git.update_refs({ref: (pending.request.commit, None), mark: (None, pending.commit)}):
        return
    if not git.update_refs({mark: (None, pending.commit)}):
        raise QueueError(f"{mark} changed while this run held it")


def _clear(git: Git, github: GitHub, pending: Pending) -> None:
    """Delete the candidate, the label and, last, the pending ref of a finished take."""
    github.delete_ref(f"refs/heads/{CANDIDATE_PREFIX}{pending.pr}")
    github.remove_label(pending.pr)
    if not git.update_refs({f"{PENDING_PREFIX}{pending.pr}": (None, pending.commit)}):
        raise QueueError(f"{PENDING_PREFIX}{pending.pr} changed while this run held it")


def finish(git: Git, github: GitHub, pending: Pending) -> None:
    """Finish a take. Every step may run again; the comment is written once, found by its tag.

    A removal writes the ``failure`` status and its comment, unless that comment is there
    already: a run that stopped after the POST, or whose POST reply was lost, wrote it. A merge
    that did not happen goes back to the queue.
    """
    if pending.action == MERGE:
        pull = github.pull(pending.pr)
        if not (pull and pull.get("merged") is True):
            _restore(git, pending)
            return
    elif pending.comment:
        said = "removed from the merge queue; see the comment"
        github.set_status(pending.request.head, "failure", said)
        tag = f"<!-- merge-queue removal {pending.commit} -->"
        if not github.commented(pending.pr, tag):
            github.comment(pending.pr, f"{pending.comment}\n\n{tag}")
    _clear(git, github, pending)


def remove(git: Git, github: GitHub, step: Step, comment: str | None, request: Request) -> bool:
    """Remove ``step`` from the queue. ``comment`` is ``None`` for a closed pull request.

    ``False``, and no write, when ``take`` finds the request changed. The pending ref keeps the
    comment until it is written, so a run that fails to write it leaves the next run to.
    """
    pending = take(git, request, REMOVE, comment or "")
    if pending is None:
        return False
    finish(git, github, pending)
    return True


def _pass(
    git: Git, github: GitHub, contexts: Sequence[str], window: int, report: dict[str, Any]
) -> bool:
    """One pass of the ``on`` mode. Return whether it merged or removed, so the train changed."""
    git.fetch()
    base = git.rev(f"refs/remotes/{git.remote}/{BASE}")
    if base is None:
        raise QueueError(f"the remote has no {BASE}")
    unfinished = read_pending(git)
    for pending in unfinished:  # a run that stopped halfway; nothing else is written before
        finish(git, github, pending)
        report["finished"].append({"pr": pending.pr, "action": pending.action})
    if unfinished:
        return True
    requests = read_requests(git)
    pulls = {r.pr: github.pull(r.pr) for r in requests}
    steps = plan(git, base, requests, pulls, github.repo)
    report |= {"base": base, "steps": [asdict(s) for s in steps]}
    removals = [s for s in steps if s.state == REMOVE]
    by_pr = {r.pr: r for r in requests}
    for step in removals:
        comment = None if step.reason == "closed" else removal_comment(step)
        if remove(git, github, step, comment, by_pr[step.pr]):
            report["removed"].append({"pr": step.pr, "reason": step.reason})
    if removals:
        return True
    tips: dict[int, str] = {}
    report["built"] = build(git, github, steps, window, tips)
    train = [s for s in steps if s.state == CANDIDATE]
    raw = [
        verdict(github.check_runs(tips[s.pr]), contexts) if s.pr in tips else (PENDING, [])
        for s in train
    ]
    judged = judge_train([state for state, _ in raw])
    report["verdicts"] = {s.pr: state for s, state in zip(train, judged, strict=True)}
    for place, step in enumerate(steps, start=1):
        pull = pulls[step.pr] or {}
        if LABEL not in [label.get("name") for label in pull.get("labels") or []]:
            github.add_label(step.pr)
        if step.state == HELD:
            github.set_status(step.head, "pending", f"held: waits for #{step.blocked_by}")
        elif not (train and step is train[0] and judged[0] != PENDING):
            github.set_status(step.head, "pending", f"place {place} of {len(steps)} in the train")
    if not train or judged[0] == PENDING:
        return False
    front, request = train[0], by_pr[train[0].pr]
    if judged[0] == FAILED:
        if remove(git, github, front, removal_comment(front, failed=raw[0][1]), request):
            report["removed"].append({"pr": front.pr, "reason": "failed-checks"})
        return True
    taken = take(git, request, MERGE)
    if taken is None:
        return True  # it left or rejoined after this pass read the requests; build again
    github.set_status(front.head, "success", "its candidate passed every required check")
    if github.main_commit() != base:
        _restore(git, taken)
        return True  # `main` moved after the verdict: the candidate was not tested on it
    title = f"{(pulls[front.pr] or {}).get('title') or 'merge queue'} (#{front.pr})"
    merged, said = github.merge(front.pr, front.head, title, request.message)
    if merged is None:
        comment = removal_comment(front, said=said)
        marker = _marker(git, request, REMOVE, comment)
        if not git.update_refs({f"{PENDING_PREFIX}{front.pr}": (marker, taken.commit)}):
            raise QueueError(f"{PENDING_PREFIX}{front.pr} changed while this run held it")
        finish(git, github, Pending(front.pr, REMOVE, marker, request, comment))
        report["removed"].append({"pr": front.pr, "reason": "merge-refused"})
        return True
    _clear(git, github, taken)
    report["merged"].append({"pr": front.pr, "commit": merged})
    landed = github.tree_of(merged)
    if landed != front.tree:
        said = (
            f"the tree of {BASE} commit {merged} is {landed}, but candidate {tips[front.pr]} of "
            f"#{front.pr} has {front.tree}"
        )
        # `main` can still move in the instant between the check above and the merge call.
        github.comment(front.pr, f"The merge queue merged a tree it did not test: {said}.")
        raise QueueError(f"{said}; merging nothing more in this run")
    return True


def run_on(git: Git, github: GitHub, window: int) -> dict[str, Any]:
    """Reconcile in ``on`` mode: judge, merge the front, remove, and build again until stable."""
    contexts = github.required_contexts()  # no contexts, no verdict: this raises first
    report: dict[str, Any] = {"mode": ON, "contexts": contexts, "merged": [], "removed": []}
    report["finished"] = []
    for _ in range(MAX_PASSES):
        if not _pass(git, github, contexts, window, report):
            break
    queued = {step["pr"] for step in report.get("steps", [])}
    for number in github.labelled():  # a pull request that left keeps no label
        if number not in queued:
            github.remove_label(number)
    return report


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
    for key in ("merged", "removed"):
        if key in report:
            done = ", ".join(f"#{item['pr']}" for item in report[key]) or "none"
            lines.append(f"\n{key.capitalize()}: {done}")
    return "\n".join(lines) + "\n"


def _reconcile(git: Git, github: GitHub, window: int | None, mode: str) -> dict[str, Any]:
    if mode == ON and window is not None:
        return run_on(git, github, window)
    git.fetch()
    base = git.rev(f"refs/remotes/{git.remote}/{BASE}")
    if base is None:
        raise QueueError(f"the remote has no {BASE}")
    requests = read_requests(git)
    steps = plan(git, base, requests, {r.pr: github.pull(r.pr) for r in requests}, github.repo)
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
    building.add_argument(
        "--mode", choices=(OBSERVE, ON), default=OBSERVE, help="on judges, merges and removes"
    )
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
            mode = getattr(args, "mode", OBSERVE)
            report = _reconcile(git or Git(), github, window, mode)
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
