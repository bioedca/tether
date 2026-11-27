# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline merge-queue tests with fake GitHub backed by a temporary Git origin."""

from __future__ import annotations

import ast
import http.client
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
SCRIPT = _REPO / "scripts" / "merge_queue.py"

_spec = importlib.util.spec_from_file_location("merge_queue", SCRIPT)
assert _spec is not None and _spec.loader is not None
mq = importlib.util.module_from_spec(_spec)
sys.modules["merge_queue"] = mq
_spec.loader.exec_module(mq)

REPO = "bioedca/tether"
_KEYS = "AUTHOR_NAME AUTHOR_EMAIL COMMITTER_NAME COMMITTER_EMAIL"
_WHO = {f"GIT_{key}": "dev@example.org" for key in _KEYS.split()}
_LINES = "".join(f"line {i}\n" for i in range(1, 11))
_EPOCH = 1_800_000_000  # the fake GitHub stamps request commits from here, in seconds


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    command = ["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args]
    # A test run from a Git hook inherits `GIT_DIR` and friends; keep every call in `cwd`.
    ambient = {k: v for k, v in os.environ.items() if k not in mq.REPOSITORY_LOCATION_VARS}
    merged = {**ambient, **_WHO, **(env or {})}
    done = subprocess.run(
        command, cwd=cwd, env=merged, capture_output=True, encoding="utf-8", check=check
    )
    return done.stdout.strip()


class World:
    """The three repositories, the pull requests GitHub knows, and the fake transport."""

    def __init__(self, root: Path) -> None:
        self.origin, self.dev, self.runner = root / "origin.git", root / "dev", root / "runner"
        _git(root, "init", "--quiet", "--bare", "--initial-branch=main", str(self.origin))
        _git(root, "init", "--quiet", "--initial-branch=main", str(self.dev))
        _git(self.dev, "remote", "add", "origin", str(self.origin))
        self.commit("main", {"shared.txt": _LINES, "README": "tether\n"})
        _git(root, "clone", "--quiet", str(self.origin), str(self.runner))
        self.pulls: dict[int, dict] = {}
        self.locked: set[str] = set()  # refs whose delete GitHub rejects
        self.rejected: set[str] = set()  # refs whose creation GitHub rejects with 422
        self.calls: list[tuple[str, str]] = []
        self.merged: list[tuple[int, str, dict]] = []
        self.clock = 0
        self.contexts: list[str] | None = ["lint", "commitlint", "merge-queue"]
        self.runs: dict[str, list[dict]] = {}  # check runs by commit
        self.statuses: list[tuple[str, str, str]] = []  # (commit, state, description)
        self.comments: list[tuple[int, str]] = []
        self.comment_failures: list[str] = []  # how the next comment POSTs fail, in order
        self.labels: set[int] = set()
        self.refusal: tuple[int, str] | None = None  # the next merge answers this
        self.skew = False  # the fake merge lands the tree of `main`, not the candidate's
        self.github = mq.GitHub(REPO, self.transport)
        self.git = mq.Git(self.runner)

    def commit(self, branch: str, files: dict[str, str], start: str = "origin/main") -> str:
        if _git(self.dev, "branch", "--list", branch):
            _git(self.dev, "checkout", "--quiet", branch)
        elif _git(self.dev, "branch", "--list", "main"):  # not the first commit of all
            _git(self.dev, "checkout", "--quiet", "-B", branch, start)
        for name, text in files.items():
            (self.dev / name).write_text(text, encoding="utf-8", newline="\n")
        _git(self.dev, "add", "--all")
        _git(self.dev, "commit", "--quiet", "--allow-empty", "-m", f"change {branch}")
        _git(self.dev, "push", "--quiet", "origin", branch)
        return _git(self.dev, "rev-parse", "HEAD")

    def pull(self, number: int, files: dict[str, str]) -> str:
        _git(self.dev, "fetch", "--quiet", "origin")
        head = self.commit(f"pr-{number}", files)
        pull = {"state": "open", "draft": False, "base": {"ref": "main"}, "title": f"pr {number}"}
        self.pulls[number] = pull | {"head": {"sha": head, "repo": {"full_name": REPO}}}
        return head

    def join(self, number: int, files: dict[str, str]) -> str:
        head = self.pull(number, files)
        mq.join(self.github, number, head, f"squash message of #{number}")
        return head

    def squash(self, *numbers: int) -> list[str]:
        """Squash-merge the pull requests in order on a scratch branch; return each tree."""
        _git(self.dev, "fetch", "--quiet", "origin")
        _git(self.dev, "checkout", "--quiet", "-B", "scratch", "origin/main")
        trees = []
        for number in numbers:
            _git(self.dev, "merge", "--squash", "--quiet", self.pulls[number]["head"]["sha"])
            _git(self.dev, "commit", "--quiet", "--allow-empty", "-m", f"squash #{number}")
            trees.append(_git(self.dev, "rev-parse", "HEAD^{tree}"))
        return trees

    def plan(self) -> dict[int, mq.Step]:
        self.git.fetch()
        base = self.git.rev("refs/remotes/origin/main")
        requests = mq.read_requests(self.git)
        pulls = {r.pr: self.pulls.get(r.pr) for r in requests}
        steps = mq.plan(self.git, base, requests, pulls, REPO)
        self.steps = steps
        return {step.pr: step for step in steps}

    def origin_ref(self, ref: str) -> str | None:
        return _git(self.origin, "rev-parse", "--verify", "--quiet", ref, check=False) or None

    def passing(self, commit: str, **conclusions: str) -> None:
        """Give ``commit`` a finished run of each context, ``success`` unless named."""
        names = [c for c in self.contexts or [] if c != "merge-queue"]
        self.runs[commit] = [
            {
                "name": n,
                "status": "completed",
                "conclusion": conclusions.get(n, "success"),
                "started_at": "2026-11-26T10:00:00Z",
                "html_url": f"https://ci.example/{n}",
            }
            for n in names
        ]

    def pass_candidates(self, **conclusions: str) -> None:
        for _, commit in mq.Git(self.origin).refs("refs/heads/mq/pr-"):
            self.passing(commit, **conclusions)

    def _merge(self, number: int, payload: dict) -> tuple[int, bytes]:
        if self.refusal:
            (status, message), self.refusal = self.refusal, None
            return status, json.dumps({"message": message}).encode()
        pull = self.pulls[number]
        if payload["sha"] != pull["head"]["sha"]:
            return 409, b'{"message": "Head branch was modified. Review and try the merge again."}'
        main = _git(self.origin, "rev-parse", "main")
        out = _git(self.origin, "merge-tree", "--write-tree", main, payload["sha"])
        tree = out.splitlines()[0]
        tree = _git(self.origin, "rev-parse", "main^{tree}") if self.skew else tree
        message = f"{payload['commit_title']}\n\n{payload['commit_message']}"
        commit = _git(self.origin, "commit-tree", tree, "-p", main, "-m", message)
        _git(self.origin, "update-ref", "refs/heads/main", commit)
        pull["state"], pull["merged"] = "closed", True
        self.merged.append((number, commit, payload))
        return 200, json.dumps({"merged": True, "sha": commit}).encode()

    def transport(self, method: str, url: str, body: bytes | None) -> tuple[int, bytes]:
        path = url.removeprefix(f"https://api.github.com/repos/{REPO}")
        payload = json.loads(body) if body else None
        self.calls.append((method, path))
        if (method, path) == ("GET", "/git/ref/heads/main"):
            sha = _git(self.origin, "rev-parse", "main")
            return 200, json.dumps({"ref": "refs/heads/main", "object": {"sha": sha}}).encode()
        if (method, path) == ("GET", "/rules/branches/main?per_page=100"):
            if self.contexts is None:
                return 500, b"{}"
            checks = {"required_status_checks": [{"context": c} for c in self.contexts]}
            rules = [{"type": "deletion"}, {"type": "required_status_checks", "parameters": checks}]
            return 200, json.dumps(rules).encode()
        if method == "GET" and (found := re.fullmatch(r"/commits/(\w+)/check-runs\?.*", path)):
            return 200, json.dumps({"check_runs": self.runs.get(found[1], [])}).encode()
        if method == "POST" and (found := re.fullmatch(r"/statuses/(\w+)", path)):
            self.statuses.append((found[1], payload["state"], payload["description"]))
            return 201, b"{}"
        if method == "POST" and (found := re.fullmatch(r"/issues/(\d+)/comments", path)):
            if self.comment_failures:  # this POST fails; "lost" means it lands but the reply fails
                failure = self.comment_failures.pop(0)
                if failure != "lost":
                    return 502, b"{}"
                self.comments.append((int(found[1]), payload["body"]))
                return 502, b"{}"
            self.comments.append((int(found[1]), payload["body"]))
            return 201, b"{}"
        if method == "GET" and (found := re.fullmatch(r"/issues/(\d+)/comments\?(.*)", path)):
            query = dict(part.split("=", 1) for part in found[2].split("&"))
            size, page = int(query["per_page"]), int(query["page"])
            bodies = [{"body": body} for number, body in self.comments if number == int(found[1])]
            return 200, json.dumps(bodies[(page - 1) * size : page * size]).encode()
        if method == "POST" and (found := re.fullmatch(r"/issues/(\d+)/labels", path)):
            self.labels.add(int(found[1]))
            return 200, b"[]"
        if method == "DELETE" and (found := re.fullmatch(r"/issues/(\d+)/labels/\S+", path)):
            if int(found[1]) not in self.labels:
                return 404, b"{}"
            self.labels.discard(int(found[1]))
            return 200, b"[]"
        if (method, path) == ("GET", "/issues?labels=merge-queue&state=all&per_page=100"):
            return 200, json.dumps([{"number": n} for n in sorted(self.labels)]).encode()
        if method == "PUT" and (found := re.fullmatch(r"/pulls/(\d+)/merge", path)):
            return self._merge(int(found[1]), payload)
        if method == "GET" and (found := re.fullmatch(r"/pulls/(\d+)", path)):
            pull = self.pulls.get(int(found[1]))
            return (200, json.dumps(pull).encode()) if pull else (404, b"{}")
        if method == "GET" and (found := re.fullmatch(r"/git/commits/([0-9a-f]{40})", path)):
            tree = _git(self.origin, "rev-parse", f"{found[1]}^{{tree}}")
            return 200, json.dumps({"tree": {"sha": tree}}).encode()
        if (method, path) == ("POST", "/git/commits"):
            self.clock += 1  # GitHub stamps the commit; each request is one second later
            when = {"GIT_COMMITTER_DATE": f"@{_EPOCH + self.clock} +0000"}
            (parent,) = payload["parents"]
            made = ["commit-tree", payload["tree"], "-p", parent, "-m", payload["message"]]
            return 201, json.dumps({"sha": _git(self.origin, *made, env=when)}).encode()
        if (method, path) == ("POST", "/git/refs"):
            if payload["ref"] in self.rejected:
                return 422, b'{"message": "Validation Failed", "errors": [{"code": "invalid"}]}'
            if self.origin_ref(payload["ref"]):
                return 422, b'{"message": "Reference already exists"}'
            _git(self.origin, "update-ref", payload["ref"], payload["sha"])
            return 201, b"{}"
        if method == "DELETE" and path.startswith("/git/refs/"):
            ref = path.removeprefix("/git/")
            if not self.origin_ref(ref) or ref in self.locked:
                return 422, b'{"message": "Reference does not exist, or a rule rejects the delete"}'
            _git(self.origin, "update-ref", "-d", ref)
            return 204, b""
        if method == "GET" and path.startswith("/git/matching-refs/"):
            prefix = "refs/" + path.removeprefix("/git/matching-refs/")
            out = _git(self.origin, "for-each-ref", "--format=%(refname) %(objectname)")
            pairs = [line.split() for line in out.splitlines()]
            found = [{"ref": n, "object": {"sha": c}} for n, c in pairs if n.startswith(prefix)]
            return 200, json.dumps(found).encode()
        return 500, b"{}"


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def main_exit(world: World, *argv: str) -> int:
    return mq.main(["--repo", REPO, *argv], github=world.github, git=world.git)


def test_the_script_imports_only_the_standard_library() -> None:
    nodes = list(ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8"))))
    names = [alias.name for node in nodes if isinstance(node, ast.Import) for alias in node.names]
    names += [node.module for node in nodes if isinstance(node, ast.ImportFrom) and node.module]
    # The one sibling, verify_postmerge_checks, is held to the standard library by its own tests.
    allowed = set(sys.stdlib_module_names) | {"verify_postmerge_checks"}
    assert {name.split(".")[0] for name in names} <= allowed, names


def test_join_records_the_request_as_a_ref_and_the_second_join_loses(world: World) -> None:
    head, message = world.pull(7, {"a.txt": "a\n"}), "feat: a\n\nCloses #1"
    mq.join(world.github, 7, head, message)

    request = world.origin_ref("refs/merge-queue/pr-7")
    assert request is not None
    assert _git(world.origin, "show", "-s", "--format=%P", request) == head
    assert _git(world.origin, "show", "-s", "--format=%B", request) == message
    trees = [_git(world.origin, "rev-parse", f"{commit}^{{tree}}") for commit in (request, head)]
    assert trees[0] == trees[1]
    with pytest.raises(mq.Refused, match="already has a request"):
        mq.join(world.github, 7, head, "another message")
    assert world.origin_ref("refs/merge-queue/pr-7") == request

    world.git.fetch()
    assert mq.read_requests(world.git) == [mq.Request(7, head, message, _EPOCH + 1)]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda pull: pull.update(state="closed"), "#7 is not open"),
        (lambda pull: pull.update(draft=True), "#7 is a draft"),
        (lambda pull: pull["base"].update(ref="release"), "#7 targets release, not main"),
        (lambda pull: pull["head"]["repo"].update(full_name="x/tether"), "#7 comes from a fork"),
        (lambda pull: pull["head"].update(repo=None), "#7 comes from a fork"),
        (lambda pull: pull["head"].update(sha="f" * 40), "the head of #7 is " + "f" * 40),
    ],
    ids=["closed", "draft", "other-base", "fork", "deleted-fork", "moved-head"],
)
def test_join_refuses_a_pull_request_that_cannot_merge(world: World, change, reason: str) -> None:
    head = world.pull(7, {"a.txt": "a\n"})
    change(world.pulls[7])
    with pytest.raises(mq.Refused, match=re.escape(reason)):
        mq.join(world.github, 7, head, "message")
    assert world.origin_ref("refs/merge-queue/pr-7") is None
    assert not [call for call in world.calls if call[0] != "GET"]


def test_join_refuses_a_short_head_an_empty_message_and_an_unknown_number(
    world: World, tmp_path: Path
) -> None:
    head = world.pull(7, {"a.txt": "a\n"})
    with pytest.raises(mq.Refused, match="40-hex"):
        mq.join(world.github, 7, head[:12], "message")
    with pytest.raises(mq.Refused, match="the squash message is empty"):
        mq.join(world.github, 7, head, " \n")
    with pytest.raises(mq.Refused, match="#8 is not a pull request"):
        mq.join(world.github, 8, head, "message")
    (tmp_path / "message.txt").write_text("message\n", encoding="utf-8")
    asked = ("join", "--pr", "8", "--sha", head, "--message-file", str(tmp_path / "message.txt"))
    assert main_exit(world, *asked) == 3


def test_a_rejected_request_write_is_an_error_and_not_a_duplicate(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    head = world.pull(7, {"a.txt": "a\n"})
    world.rejected.add("refs/merge-queue/pr-7")
    with pytest.raises(mq.QueueError, match="did not create refs/merge-queue/pr-7"):
        mq.join(world.github, 7, head, "message")
    assert world.origin_ref("refs/merge-queue/pr-7") is None

    (tmp_path / "message.txt").write_text("message\n", encoding="utf-8")
    asked = ("join", "--pr", "7", "--sha", head, "--message-file", str(tmp_path / "message.txt"))
    capsys.readouterr()
    assert main_exit(world, *asked) == 2
    error = capsys.readouterr().err
    assert "refs/merge-queue/pr-7" in error and "already has a request" not in error

    world.rejected.clear()
    assert main_exit(world, *asked) == 0
    assert main_exit(world, *asked) == 3  # the ref exists now, so the 422 is a duplicate
    assert "#7 already has a request" in capsys.readouterr().err


def test_leave_deletes_only_the_request_and_the_next_reconcile_the_candidate(world: World) -> None:
    world.join(7, {"a.txt": "a\n"})
    world.plan()
    mq.build(world.git, world.github, world.steps, window=5)
    assert world.origin_ref("refs/heads/mq/pr-7")

    assert mq.leave(world.github, 7) == {"request": True}
    assert world.origin_ref("refs/merge-queue/pr-7") is None
    assert world.origin_ref("refs/heads/mq/pr-7"), "the candidate is the reconcile's to delete"
    assert mq.leave(world.github, 7) == {"request": False}
    world.plan()
    assert mq.build(world.git, world.github, world.steps, window=5)["deleted"] == [7]
    assert world.origin_ref("refs/heads/mq/pr-7") is None


def test_a_reconcile_that_read_before_a_leave_is_undone_by_the_one_after_it(world: World) -> None:
    world.join(7, {"a.txt": "a\n"})
    world.plan()  # a reconcile reads the requests ...
    stale = world.steps
    mq.leave(world.github, 7)  # ... a leave runs ...
    mq.build(world.git, world.github, stale, window=5)  # ... and the stale reconcile pushes
    assert world.origin_ref("refs/heads/mq/pr-7")
    assert not [c for c in world.calls if c[0] == "DELETE" and "heads" in c[1]], "leave left it"

    world.plan()  # the reconcile that follows the leave is the last writer
    assert mq.build(world.git, world.github, world.steps, window=5)["deleted"] == [7]
    assert world.origin_ref("refs/heads/mq/pr-7") is None


def test_a_delete_that_github_rejects_is_an_error_and_not_an_absent_ref(world: World) -> None:
    world.join(1, {"a.txt": "a\n"})
    world.join(12, {"b.txt": "b\n"})
    world.locked.add("refs/merge-queue/pr-12")
    assert [mq.leave(world.github, 1)["request"] for _ in range(2)] == [True, False]  # 12 stays
    with pytest.raises(mq.QueueError, match="did not delete refs/merge-queue/pr-12"):
        mq.leave(world.github, 12)
    assert main_exit(world, "leave", "--pr", "12") == 2
    assert world.origin_ref("refs/merge-queue/pr-12")


@pytest.mark.parametrize("body", [b"<html>", b'{"state":"open"}'])
def test_malformed_pull_data_is_an_error(body: bytes) -> None:
    github = mq.GitHub(REPO, Mock(return_value=(200, body)))
    with pytest.raises(mq.QueueError):
        github.pull(1)


@pytest.mark.parametrize(
    "replies", [[(200, b"{}")], [(200, b'{"tree":{"sha":"tree"}}'), (201, b"[]")]]
)
def test_invalid_commit_data_is_an_error(replies: list[tuple[int, bytes]]) -> None:
    github = mq.GitHub(REPO, Mock(side_effect=replies))
    with pytest.raises(mq.QueueError):
        github.create_request(1, "a" * 40, "squash")


@pytest.mark.parametrize("body", [b'{"ref":"unknown"}', b"[null]"])
def test_invalid_ref_lookup_does_not_report_an_absent_ref(body: bytes) -> None:
    github = mq.GitHub(REPO, Mock(side_effect=[(422, b""), (200, body)]))
    assert mq.main(["leave", "--pr", "1"], github=github) == 2


@pytest.mark.parametrize("error", [TimeoutError(), http.client.IncompleteRead(b"{")])
def test_transport_read_is_an_error(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    monkeypatch.setattr(mq.urllib.request, "urlopen", Mock(side_effect=error))
    assert mq.main(["leave", "--pr", "1"], github=mq.GitHub(REPO, mq.urllib_transport(None))) == 2


def test_each_candidate_tree_is_the_tree_of_the_squash_merges_in_order(world: World) -> None:
    world.join(3, {"shared.txt": _LINES.replace("line 2\n", "line two\n")})
    world.join(1, {"shared.txt": _LINES.replace("line 9\n", "line nine\n"), "b.txt": "b\n"})
    world.join(2, {"c.txt": "c\n"})
    world.commit("main", {"README": "tether, moved on\n"})  # `main` moves after the joins

    steps = world.plan()

    assert [step.pr for step in world.steps] == [3, 1, 2]  # join order, not number order
    assert [step.state for step in world.steps] == [mq.CANDIDATE] * 3
    assert [steps[n].tree for n in (3, 1, 2)] == world.squash(3, 1, 2)
    assert _git(world.runner, "rev-parse", f"{steps[1].commit}^") == steps[3].commit  # stacked
    assert steps == world.plan()  # the same inputs give the same commits


def test_a_conflict_with_main_removes_and_the_one_behind_is_built_without_it(world: World) -> None:
    world.join(1, {"shared.txt": _LINES.replace("line 5\n", "line five\n")})
    world.join(2, {"c.txt": "c\n"})
    world.commit("main", {"shared.txt": _LINES.replace("line 5\n", "LINE 5\n")})

    steps = world.plan()

    assert (steps[1].state, steps[1].reason) == (mq.REMOVE, "conflict-with-main")
    assert steps[1].paths == ("shared.txt",) and steps[1].tree is None and steps[1].commit is None
    assert (steps[2].state, steps[2].tree) == (mq.CANDIDATE, world.squash(2)[0])


def test_a_conflict_with_the_pull_request_ahead_holds_until_that_one_leaves(world: World) -> None:
    world.join(1, {"shared.txt": _LINES.replace("line 5\n", "line five\n")})
    world.join(2, {"shared.txt": _LINES.replace("line 5\n", "LINE 5\n")})
    world.join(3, {"c.txt": "c\n"})

    steps = world.plan()

    assert steps[1].state == mq.CANDIDATE
    assert (steps[2].state, steps[2].blocked_by, steps[2].paths) == (mq.HELD, 1, ("shared.txt",))
    assert (steps[3].state, steps[3].tree) == (mq.CANDIDATE, world.squash(1, 3)[1])
    assert mq.build(world.git, world.github, world.steps, window=2)["pushed"] == [1, 3]

    mq.leave(world.github, 1)
    steps = world.plan()

    assert [step.pr for step in world.steps] == [2, 3]
    assert [steps[n].tree for n in (2, 3)] == world.squash(2, 3)


def test_a_closed_pull_request_and_a_moved_head_are_removed(world: World) -> None:
    world.join(1, {"a.txt": "a\n"})
    world.join(2, {"b.txt": "b\n"})
    world.join(3, {"c.txt": "c\n"})
    world.pulls[1]["state"] = "closed"
    world.pulls[2]["head"]["sha"] = world.commit("pr-2", {"b.txt": "pushed after the join\n"})

    steps = world.plan()

    assert (steps[1].state, steps[1].reason) == (mq.REMOVE, "closed")
    assert (steps[2].state, steps[2].reason) == (mq.REMOVE, "head-moved")
    assert (steps[3].state, steps[3].tree) == (mq.CANDIDATE, world.squash(3)[0])
    del world.pulls[3]  # GitHub answers 404
    assert world.plan()[3].reason == "closed"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda pull: pull["base"].update(ref="release"), "retargeted"),
        (lambda pull: pull.update(draft=True), "draft"),
    ],
    ids=["retargeted", "draft"],
)
def test_a_retargeted_or_draft_pull_request_is_removed_and_left_out(
    world: World, change, reason: str
) -> None:
    world.join(1, {"a.txt": "a\n"})
    world.join(2, {"b.txt": "b\n"})
    change(world.pulls[1])

    steps = world.plan()

    assert (steps[1].state, steps[1].reason) == (mq.REMOVE, reason)
    assert steps[1].tree is None and steps[1].commit is None
    assert (steps[2].state, steps[2].tree) == (mq.CANDIDATE, world.squash(2)[0])
    assert mq.build(world.git, world.github, world.steps, window=5)["pushed"] == [2]
    files = _git(world.origin, "ls-tree", "-r", "--name-only", "mq/pr-2").splitlines()
    assert "b.txt" in files and "a.txt" not in files
    assert world.origin_ref("refs/heads/mq/pr-1") is None


def test_build_pushes_the_window_keeps_a_same_tree_and_deletes_the_rest(world: World) -> None:
    world.join(1, {"a.txt": "a\n"})
    world.join(2, {"b.txt": "b\n"})
    world.join(3, {"c.txt": "c\n"})
    steps = world.plan()

    built = mq.build(world.git, world.github, world.steps, window=2)

    assert built == {"pushed": [1, 2], "kept": [], "deleted": []}
    assert world.origin_ref("refs/heads/mq/pr-1") == steps[1].commit
    assert world.origin_ref("refs/heads/mq/pr-3") is None
    world.plan()
    again = mq.build(world.git, world.github, world.steps, window=2)
    assert again == {"pushed": [], "kept": [1, 2], "deleted": []}

    # Pull request 1 merges: `main` takes its squash, and its request goes.
    world.squash(1)
    _git(world.dev, "push", "--quiet", "origin", "scratch:main")
    mq.leave(world.github, 1)
    _git(world.origin, "update-ref", "refs/heads/mq/pr-1", steps[1].commit)  # a stale branch
    before = world.origin_ref("refs/heads/mq/pr-2")
    after = world.plan()

    built = mq.build(world.git, world.github, world.steps, window=2)

    assert after[2].commit != steps[2].commit and after[2].tree == steps[2].tree
    assert built == {"pushed": [3], "kept": [2], "deleted": [1]}
    assert world.origin_ref("refs/heads/mq/pr-2") == before  # the tested commit stays
    assert world.origin_ref("refs/heads/mq/pr-1") is None
    assert _git(world.origin, "rev-parse", "mq/pr-3^{tree}") == world.squash(2, 3)[1]


def test_a_ref_that_join_did_not_write_stops_the_plan(world: World) -> None:
    world.join(1, {"a.txt": "a\n"})
    root = _git(world.origin, "rev-list", "--max-parents=0", "main")
    _git(world.origin, "update-ref", "refs/merge-queue/pr-9", root)
    world.git.fetch()
    with pytest.raises(mq.QueueError, match="refs/merge-queue/pr-9 is not a merge-queue request"):
        mq.read_requests(world.git)
    assert main_exit(world, "plan") == 2


def test_the_commands_print_json_and_write_the_step_summary(
    world: World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = world.origin.parent / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    head = world.pull(1, {"shared.txt": _LINES.replace("line 5\n", "line five\n")})
    message = world.origin.parent / "message.txt"
    message.write_text("fix: five\n", encoding="utf-8")
    assert main_exit(world, "join", "--pr", "1", "--sha", head, "--message-file", str(message)) == 0
    assert json.loads(capsys.readouterr().out) == {"joined": 1, "head": head}
    world.join(2, {"shared.txt": _LINES.replace("line 5\n", "LINE 5\n")})

    assert main_exit(world, "plan") == 0
    planned = json.loads(capsys.readouterr().out)
    assert "built" not in planned and world.origin_ref("refs/heads/mq/pr-1") is None
    assert [(s["pr"], s["state"]) for s in planned["steps"]] == [(1, "candidate"), (2, "held")]

    assert main_exit(world, "build", "--window", "1") == 0
    built = json.loads(capsys.readouterr().out)
    assert built["built"] == {"pushed": [1], "kept": [], "deleted": []}
    text = summary.read_text(encoding="utf-8")
    assert "| #1 | candidate |" in text and "| #2 | held | waits for #1 (`shared.txt`) |" in text
    assert "Candidates pushed: #1" in text

    assert main_exit(world, "leave", "--pr", "2") == 0
    assert json.loads(capsys.readouterr().out) == {"left": 2, "request": True}


def test_the_json_and_the_step_summary_show_the_retargeted_and_draft_reasons(
    world: World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = world.origin.parent / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    for number in (1, 2, 3):
        world.join(number, {f"{number}.txt": f"{number}\n"})
    world.pulls[1]["base"]["ref"] = "release"
    world.pulls[2]["draft"] = True

    assert main_exit(world, "plan") == 0

    planned = json.loads(capsys.readouterr().out)["steps"]
    assert [(s["pr"], s["state"], s["reason"]) for s in planned] == [
        (1, "remove", "retargeted"),
        (2, "remove", "draft"),
        (3, "candidate", None),
    ]
    text = summary.read_text(encoding="utf-8")
    assert "| #1 | remove | retargeted |" in text and "| #2 | remove | draft |" in text


def _decoy(root: Path) -> tuple[Path, dict[str, str]]:
    """A separate repository, and ambient variables that point Git at it."""
    decoy = root / "decoy"
    _git(root, "init", "--quiet", "--initial-branch=main", str(decoy))
    (decoy / "decoy.txt").write_text("decoy\n", encoding="utf-8")
    _git(decoy, "add", "decoy.txt")
    _git(decoy, "commit", "--quiet", "-m", "decoy")
    dot = decoy / ".git"
    ambient = {"GIT_DIR": str(dot), "GIT_WORK_TREE": str(decoy), "GIT_COMMON_DIR": str(dot)}
    ambient |= {"GIT_INDEX_FILE": str(dot / "index"), "GIT_OBJECT_DIRECTORY": str(dot / "objects")}
    return decoy, ambient


def _state(decoy: Path) -> tuple[str, bytes]:
    """The refs and the index of ``decoy``, read with an explicit location."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    refs = subprocess.run(  # noqa: S603
        ["git", f"--git-dir={decoy / '.git'}", "for-each-ref", "--format=%(refname) %(objectname)"],
        env=clean,
        capture_output=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return refs, (decoy / ".git" / "index").read_bytes()


def test_queue_git_uses_its_checkout_whatever_the_environment_names(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    decoy, ambient = _decoy(world.origin.parent)
    before = _state(decoy)
    world.join(1, {"a.txt": "a\n"})
    runner_head = _git(world.runner, "rev-parse", "HEAD")
    for name, value in ambient.items():
        monkeypatch.setenv(name, value)

    assert world.git.rev("HEAD") == runner_head
    steps = world.plan()
    assert mq.build(world.git, world.github, world.steps, window=5)["pushed"] == [1]

    assert world.origin_ref("refs/heads/mq/pr-1") == steps[1].commit
    assert _state(decoy) == before


def test_the_fixture_git_stays_in_its_temporary_repositories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decoy, ambient = _decoy(tmp_path)
    before = _state(decoy)
    for name, value in ambient.items():
        monkeypatch.setenv(name, value)

    (tmp_path / "world").mkdir()
    world = World(tmp_path / "world")
    head = world.join(1, {"a.txt": "a\n"})

    assert world.origin_ref("refs/heads/pr-1") == head
    assert world.origin_ref("refs/merge-queue/pr-1")
    assert _state(decoy) == before


# --- part 2 (#1268): the verdict, the merge, the removal and the `on` mode -----------------------


def run_on(world: World, window: int = 5) -> dict:
    return mq.run_on(world.git, world.github, window)


def _writes(world: World) -> list[tuple[str, str]]:
    """The calls of part 2 that write: statuses, comments, labels and merges."""
    kinds = ("/statuses/", "/comments", "/labels", "/merge")
    return [(m, p) for m, p in world.calls if m != "GET" and any(k in p for k in kinds)]


def _run(name: str, conclusion: str | None, started: str, url: str = "") -> dict:
    status = "completed" if conclusion else "in_progress"
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": started,
        "html_url": url,
    }


def test_the_required_contexts_come_from_the_active_rules_without_the_queue(world: World) -> None:
    assert world.github.required_contexts() == ["lint", "commitlint"]
    world.contexts = None  # the rules cannot be read
    world.join(1, {"a.txt": "a\n"})
    with pytest.raises(mq.QueueError, match="HTTP 500"):
        run_on(world)
    assert main_exit(world, "build", "--mode", "on") == 2
    world.contexts = ["merge-queue"]  # the rules require nothing else
    with pytest.raises(mq.QueueError, match="require no check"):
        run_on(world)
    assert not _writes(world) and world.origin_ref("refs/heads/mq/pr-1") is None


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([_run("lint", "success", "1"), _run("lint", "failure", "2")], mq.FAILED),  # newest wins
        ([_run("lint", "failure", "1"), _run("lint", "success", "2")], mq.PASSED),  # a re-run
        ([_run("lint", "success", "1"), _run("lint", None, "2")], mq.PENDING),  # not finished
        ([_run("commitlint", "skipped", "1")], mq.PASSED),  # the one exemption
        ([_run("lint", "skipped", "1")], mq.FAILED),  # and only for commitlint
        ([], mq.PENDING),  # no run is pending, never passed
    ],
    ids=["newest-failure", "re-run", "unfinished", "commitlint-skipped", "lint-skipped", "none"],
)
def test_the_verdict_is_the_release_guards_rule_by_import(runs: list, expected: str) -> None:
    context = runs[0]["name"] if runs else "lint"
    assert mq.checks.__file__ == str(_REPO / "scripts" / "verify_postmerge_checks.py")
    assert mq.verdict(runs, [context])[0] == expected


def test_a_failure_counts_only_when_every_candidate_ahead_passed(world: World) -> None:
    ok, bad, wait = mq.PASSED, mq.FAILED, mq.PENDING
    assert mq.judge_train([ok, bad, bad]) == [ok, bad, wait]
    assert mq.judge_train([wait, bad]) == [wait, wait]
    world.join(1, {"a.txt": "a\n"})
    world.join(2, {"b.txt": "b\n"})
    run_on(world)  # builds both; no runs yet, so nothing is judged
    world.passing(world.origin_ref("refs/heads/mq/pr-2"), lint="failure")

    report = run_on(world)

    assert report["verdicts"] == {1: wait, 2: wait} and not world.comments
    assert world.origin_ref("refs/merge-queue/pr-2"), "#2 may have failed because of #1"


def test_passed_candidates_at_the_front_merge_in_order_in_one_run(world: World) -> None:
    heads = {n: world.join(n, {f"{n}.txt": f"{n}\n"}) for n in (1, 2, 3)}
    run_on(world)
    trees = {n: _git(world.origin, "rev-parse", f"mq/pr-{n}^{{tree}}") for n in (1, 2, 3)}
    world.pass_candidates()

    report = run_on(world)

    assert [number for number, _, _ in world.merged] == [1, 2, 3]
    for number, commit, payload in world.merged:
        assert payload == {
            "merge_method": "squash",
            "sha": heads[number],
            "commit_title": f"pr {number} (#{number})",
            "commit_message": f"squash message of #{number}",
        }
        assert _git(world.origin, "rev-parse", f"{commit}^{{tree}}") == trees[number]
        assert (heads[number], "success") in [(c, s) for c, s, _ in world.statuses]
    assert [m["pr"] for m in report["merged"]] == [1, 2, 3]
    assert not _git(world.origin, "for-each-ref", "refs/merge-queue", "refs/heads/mq")
    assert not world.labels and not world.comments


def test_a_tree_that_differs_after_a_merge_fails_the_run_and_merges_nothing_more(
    world: World,
) -> None:
    world.join(1, {"a.txt": "a\n"})
    world.join(2, {"b.txt": "b\n"})
    run_on(world)
    candidate = world.origin_ref("refs/heads/mq/pr-1")
    world.pass_candidates()
    world.skew = True

    with pytest.raises(mq.QueueError) as error:
        run_on(world)

    (merged,) = [commit for _, commit, _ in world.merged]
    tree = _git(world.runner, "rev-parse", f"{candidate}^{{tree}}")
    assert merged in str(error.value) and candidate in str(error.value) and tree in str(error.value)
    assert world.origin_ref("refs/merge-queue/pr-2"), "#2 waits for a run that can trust main"
    ((number, body),) = world.comments  # the merged pull request says so, where people look
    assert number == 1 and merged in body and tree in body


def test_main_that_moved_after_the_verdict_stops_the_merge_and_rebuilds(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.join(1, {"a.txt": "a\n"})
    run_on(world)
    world.pass_candidates()
    write, moved = world.github.set_status, []

    def move_main_then_write(commit: str, state: str, description: str) -> None:
        if state == "success" and not moved:  # a direct merge lands after the verdict
            moved.append(world.commit("main", {"README": "moved on\n"}))
        write(commit, state, description)

    monkeypatch.setattr(world.github, "set_status", move_main_then_write)
    run_on(world)

    assert not world.merged, "the candidate was tested on the old main, not this one"
    assert world.origin_ref("refs/merge-queue/pr-1") and not world.comments
    assert _git(world.origin, "rev-parse", "mq/pr-1^") == moved[0], "rebuilt on the new main"


def test_a_refused_merge_removes_the_pull_request_and_quotes_github(world: World) -> None:
    head = world.join(1, {"a.txt": "a\n"})
    run_on(world)
    world.pass_candidates()
    world.refusal = (405, 'Required status check "lint" is expected.')

    report = run_on(world)

    assert report["removed"] == [{"pr": 1, "reason": "merge-refused"}] and not world.merged
    ((number, body),) = world.comments
    assert number == 1 and '> Required status check "lint" is expected.' in body
    assert (head, "failure") == world.statuses[-1][:2]
    assert world.origin_ref("refs/merge-queue/pr-1") is None


def test_a_failed_front_is_removed_with_one_comment_naming_the_checks(world: World) -> None:
    head = world.join(1, {"a.txt": "a\n"})
    world.join(2, {"b.txt": "b\n"})
    run_on(world)
    world.passing(world.origin_ref("refs/heads/mq/pr-1"), lint="failure")

    report = run_on(world)
    run_on(world)  # a second run comments no second time

    assert report["removed"][0] == {"pr": 1, "reason": "failed-checks"}
    ((number, body),) = world.comments
    assert number == 1 and "- `lint` concluded `failure`: https://ci.example/lint" in body
    assert "To rejoin" in body and "-f pr=1" in body
    assert [s for c, s, _ in world.statuses if c == head][-1] == "failure"
    assert world.origin_ref("refs/heads/mq/pr-1") is None
    # The train behind is built again without it: #2 now sits on `main` alone.
    assert _git(world.origin, "rev-parse", "mq/pr-2^{tree}") == world.squash(2)[0]


def test_a_conflict_and_a_moved_head_are_removed_with_their_reason(world: World) -> None:
    world.join(1, {"shared.txt": _LINES.replace("line 5\n", "line five\n")})
    pinned = world.join(2, {"b.txt": "b\n"})
    world.commit("main", {"shared.txt": _LINES.replace("line 5\n", "LINE 5\n")})
    world.pulls[2]["head"]["sha"] = world.commit("pr-2", {"b.txt": "pushed after the join\n"})

    run_on(world)

    bodies = dict(world.comments)
    assert len(world.comments) == 2
    assert "conflicts with `main` in `shared.txt`" in bodies[1]
    assert f"its head moved after the join, which pinned `{pinned}`" in bodies[2]
    assert all("To rejoin" in body for body in bodies.values())


def test_a_held_and_a_queued_pull_request_are_pending_and_labelled(world: World) -> None:
    head1 = world.join(1, {"shared.txt": _LINES.replace("line 5\n", "line five\n")})
    head2 = world.join(2, {"shared.txt": _LINES.replace("line 5\n", "LINE 5\n")})
    head3 = world.join(3, {"c.txt": "c\n"})

    run_on(world)

    latest = {commit: description for commit, _, description in world.statuses}
    assert {state for _, state, _ in world.statuses} == {"pending"}
    assert latest[head2] == "held: waits for #1"
    assert (
        latest[head1] == "place 1 of 3 in the train"
        and latest[head3] == "place 3 of 3 in the train"
    )
    assert world.labels == {1, 2, 3} and not world.comments

    mq.leave(world.github, 3)
    run_on(world)
    assert world.labels == {1, 2}, "the label goes when the pull request leaves"


def test_a_request_whose_head_is_now_a_fork_is_removed(world: World) -> None:
    world.join(1, {"a.txt": "a\n"})
    world.pulls[1]["head"]["repo"] = {"full_name": "someone/tether"}

    assert world.plan()[1].reason == "fork"
    run_on(world)
    assert "its head is not in this repository" in world.comments[0][1]
    assert world.origin_ref("refs/heads/mq/pr-1") is None


def test_a_pull_request_that_left_during_the_run_is_not_merged(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.join(1, {"a.txt": "a\n"})
    run_on(world)
    world.pass_candidates()
    read = world.github.check_runs

    def leave_then_read(commit: str) -> list:
        mq.leave(world.github, 1)  # a leave lands after this pass read the requests
        return read(commit)

    monkeypatch.setattr(world.github, "check_runs", leave_then_read)
    run_on(world)
    assert not world.merged and world.origin_ref("refs/heads/mq/pr-1") is None


def test_a_closed_pull_request_leaves_quietly(world: World) -> None:
    head = world.join(1, {"a.txt": "a\n"})
    run_on(world)
    world.pulls[1]["state"] = "closed"  # merged the direct way, around the queue
    world.statuses.clear()

    assert run_on(world)["removed"] == [{"pr": 1, "reason": "closed"}]
    assert not world.comments and head not in [c for c, _, _ in world.statuses]
    assert not world.labels and world.origin_ref("refs/merge-queue/pr-1") is None


def _observe_scenario(world: World, kind: str) -> None:
    """Set up a state in which `on` mode would write ``kind``."""
    five = {"shared.txt": _LINES.replace("line 5\n", "line five\n")}
    other = {"shared.txt": _LINES.replace("line 5\n", "LINE 5\n")}
    world.join(1, five if kind in ("conflict", "held") else {"a.txt": "a\n"})
    if kind in ("merge", "failed", "refused", "label-gone"):
        run_on(world)
        world.pass_candidates(**({"lint": "failure"} if kind == "failed" else {}))
    world.refusal = (405, "no") if kind == "refused" else None
    if kind == "conflict":
        world.commit("main", other)
    elif kind == "held":
        world.join(2, other)
    elif kind == "label-gone":
        mq.leave(world.github, 1)
    world.calls.clear()
    world.statuses.clear()
    world.comments.clear()


@pytest.mark.parametrize("kind", ["merge", "failed", "refused", "conflict", "held", "label-gone"])
def test_observe_mode_writes_no_status_comment_label_or_merge(world: World, kind: str) -> None:
    _observe_scenario(world, kind)
    labels, merged = set(world.labels), list(world.merged)

    assert main_exit(world, "build") == 0  # the default mode is observe
    assert main_exit(world, "build", "--mode", "observe") == 0

    assert not _writes(world), kind
    assert (world.labels, world.merged, world.comments) == (labels, merged, [])


def test_the_on_mode_command_prints_what_it_merged(
    world: World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = world.origin.parent / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    world.join(1, {"a.txt": "a\n"})
    assert main_exit(world, "build", "--mode", "on") == 0
    world.pass_candidates()
    capsys.readouterr()

    assert main_exit(world, "build", "--mode", "on") == 0

    assert [m["pr"] for m in json.loads(capsys.readouterr().out)["merged"]] == [1]
    assert "Merged: #1" in summary.read_text(encoding="utf-8")


def test_a_rejoin_during_the_run_merges_the_new_request_and_not_the_old(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = world.join(1, {"a.txt": "a\n"})
    run_on(world)
    world.pass_candidates()
    read, raced = world.github.check_runs, []

    def rejoin_then_read(commit: str) -> list:
        if not raced:  # a leave and a rejoin land after the pass read the requests
            raced.append(mq.leave(world.github, 1))
            mq.join(world.github, 1, head, "the replacement message")
        return read(commit)

    monkeypatch.setattr(world.github, "check_runs", rejoin_then_read)
    run_on(world)

    ((_, _, payload),) = world.merged
    assert payload["commit_message"] == "the replacement message"


def test_a_rejoin_during_the_run_is_not_removed_for_the_old_candidate(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.join(1, {"a.txt": "a\n"})
    run_on(world)
    world.pass_candidates(lint="failure")
    read, raced = world.github.check_runs, []

    def rejoin_then_read(commit: str) -> list:
        if not raced:  # the author leaves and rejoins at a corrected head mid-pass
            raced.append(mq.leave(world.github, 1))
            world.pulls[1]["head"]["sha"] = world.commit("pr-1", {"a.txt": "fixed\n"})
            mq.join(world.github, 1, world.pulls[1]["head"]["sha"], "message")
        return read(commit)

    monkeypatch.setattr(world.github, "check_runs", rejoin_then_read)
    run_on(world)

    assert world.origin_ref("refs/merge-queue/pr-1"), "the new request is judged on its own"
    assert not world.comments and "failure" not in [s for _, s, _ in world.statuses]


# --- #1480: one write boundary and a durable removal comment -------------------------------------


def _race_the_take(world: World, monkeypatch: pytest.MonkeyPatch, race) -> None:
    """Run ``race`` once, just before the first atomic push: after the last read of the pass."""
    update, raced = world.git.update_refs, []

    def race_then_update(moves: dict) -> bool:
        if not raced:
            raced.append(race())
        return update(moves)

    monkeypatch.setattr(world.git, "update_refs", race_then_update)


def test_a_rejoin_just_before_the_merge_never_merges_the_old_message(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = world.join(1, {"a.txt": "a\n"})
    run_on(world)
    world.pass_candidates()

    def rejoin() -> None:
        mq.leave(world.github, 1)
        mq.join(world.github, 1, head, "the replacement message")

    _race_the_take(world, monkeypatch, rejoin)
    run_on(world)

    ((_, _, payload),) = world.merged
    assert payload["commit_message"] == "the replacement message"
    assert not _git(world.origin, "for-each-ref", "refs/merge-queue")


def test_a_leave_just_before_the_merge_merges_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.join(1, {"a.txt": "a\n"})
    run_on(world)
    world.pass_candidates()
    _race_the_take(world, monkeypatch, lambda: mq.leave(world.github, 1))

    run_on(world)

    assert not world.merged and not world.comments
    assert not _git(world.origin, "for-each-ref", "refs/merge-queue", "refs/heads/mq")


def test_a_leave_after_the_take_is_too_late_and_the_merge_goes_on(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.join(1, {"a.txt": "a\n"})
    run_on(world)
    world.pass_candidates()
    read, left = world.github.main_commit, []

    def leave_then_read() -> str:
        left.append(mq.leave(world.github, 1))  # the request is taken: nothing to delete
        return read()

    monkeypatch.setattr(world.github, "main_commit", leave_then_read)
    run_on(world)

    assert left[0] == {"request": False}
    ((_, _, payload),) = world.merged
    assert payload["commit_message"] == "squash message of #1"


def test_a_rejoin_just_before_a_removal_keeps_the_replacement_request(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.join(1, {"a.txt": "a\n"})
    run_on(world)
    world.pass_candidates(lint="failure")

    def rejoin() -> None:
        mq.leave(world.github, 1)
        world.pulls[1]["head"]["sha"] = world.commit("pr-1", {"a.txt": "fixed\n"})
        mq.join(world.github, 1, world.pulls[1]["head"]["sha"], "message")

    _race_the_take(world, monkeypatch, rejoin)
    run_on(world)

    request = mq.read_requests(world.git)[0]
    assert request.head == world.pulls[1]["head"]["sha"], "the new request is judged on its own"
    assert not world.comments and "failure" not in [s for _, s, _ in world.statuses]


@pytest.mark.parametrize("failure", ["refused", "lost"])
def test_a_failed_removal_comment_is_written_exactly_once_by_a_later_run(
    world: World, failure: str
) -> None:
    head = world.join(1, {"a.txt": "a\n"})
    run_on(world)
    world.passing(world.origin_ref("refs/heads/mq/pr-1"), lint="failure")
    world.comment_failures = [failure]  # "lost": the comment lands, but its reply fails

    assert main_exit(world, "build", "--mode", "on") == 2
    assert world.origin_ref("refs/merge-queue/pr-1") is None, "out of the queue"
    assert world.origin_ref("refs/merge-queue/pending/pr-1"), "the comment is still owed"
    assert mq.leave(world.github, 1) == {"request": False}, "a leave cannot drop what is owed"

    report = run_on(world)
    run_on(world)

    assert report["finished"] == [{"pr": 1, "action": "remove"}]
    ((number, body),) = world.comments
    assert number == 1 and "- `lint` concluded `failure`" in body and "To rejoin" in body
    assert [s for c, s, _ in world.statuses if c == head][-1] == "failure"
    assert not _git(world.origin, "for-each-ref", "refs/merge-queue", "refs/heads/mq")
    assert not world.labels


def test_a_merge_call_that_errors_puts_the_request_back(world: World) -> None:
    world.join(1, {"a.txt": "a\n"})
    run_on(world)
    world.pass_candidates()
    world.refusal = (502, "bad gateway")  # no verdict from GitHub: an error, not a refusal

    with pytest.raises(mq.QueueError, match="HTTP 502"):
        run_on(world)
    assert world.origin_ref("refs/merge-queue/pending/pr-1") and not world.merged

    report = run_on(world)

    assert report["finished"] == [{"pr": 1, "action": "merge"}]
    ((_, _, payload),) = world.merged
    assert payload["commit_message"] == "squash message of #1" and not world.comments
    assert not _git(world.origin, "for-each-ref", "refs/merge-queue", "refs/heads/mq")


def test_a_ref_update_that_fails_for_another_reason_is_an_error(world: World) -> None:
    world.join(1, {"a.txt": "a\n"})
    world.git.fetch()
    (request,) = mq.read_requests(world.git)
    world.git.remote = str(world.origin.parent / "missing.git")

    with pytest.raises(mq.QueueError):
        mq.take(world.git, request, mq.REMOVE)


@pytest.mark.parametrize(("count", "tagged"), [(1000, None), (1050, 1049)], ids=["1000", "page-11"])
def test_the_comment_lookup_reads_every_page(world: World, count: int, tagged: int | None) -> None:
    world.comments = [
        (1, f"comment {i}" + (" <!-- tag -->" if i == tagged else "")) for i in range(count)
    ]

    assert world.github.commented(1, "<!-- tag -->") is (tagged is not None)
    pages = [p for m, p in world.calls if m == "GET" and "/comments?" in p]
    assert len(pages) == count // 100 + 1
