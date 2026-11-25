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
        self.clock = 0
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
        pull = {"state": "open", "draft": False, "base": {"ref": "main"}}
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
        steps = mq.plan(self.git, base, requests, {r.pr: self.pulls.get(r.pr) for r in requests})
        self.steps = steps
        return {step.pr: step for step in steps}

    def origin_ref(self, ref: str) -> str | None:
        return _git(self.origin, "rev-parse", "--verify", "--quiet", ref, check=False) or None

    def transport(self, method: str, url: str, body: bytes | None) -> tuple[int, bytes]:
        path = url.removeprefix(f"https://api.github.com/repos/{REPO}")
        payload = json.loads(body) if body else None
        self.calls.append((method, path))
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
            names = _git(self.origin, "for-each-ref", "--format=%(refname)").splitlines()
            return 200, json.dumps([{"ref": n} for n in names if n.startswith(prefix)]).encode()
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
    assert {name.split(".")[0] for name in names} <= set(sys.stdlib_module_names), names


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


def test_leave_deletes_the_request_and_the_candidate_and_may_run_twice(world: World) -> None:
    world.join(7, {"a.txt": "a\n"})
    world.plan()
    mq.build(world.git, world.github, world.steps, window=5)
    assert world.origin_ref("refs/heads/mq/pr-7")

    assert mq.leave(world.github, 7) == {"request": True, "candidate": True}
    assert world.origin_ref("refs/merge-queue/pr-7") is None
    assert world.origin_ref("refs/heads/mq/pr-7") is None
    assert mq.leave(world.github, 7) == {"request": False, "candidate": False}


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
    assert json.loads(capsys.readouterr().out) == {"left": 2, "request": True, "candidate": False}


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
