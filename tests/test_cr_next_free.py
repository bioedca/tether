# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The repository copy of the CodeRabbit queue script, tested on what it does.

`.agents/bin/cr_next_free.py` is the repository copy of the CodeRabbit queue (ADR-0137). What is
asserted is behaviour: where its state lives, that a join never replaces another entry, how the
pace follows CodeRabbit's own words, and that it says ASK only when nothing is running and the
window is free. GitHub is replaced by a stub; nothing here reaches it.

Stdlib only, so it runs on the base 3-OS `test` matrix.
"""

from __future__ import annotations

import importlib.util
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
SCRIPT = _REPO / ".agents" / "bin" / "cr_next_free.py"

_spec = importlib.util.spec_from_file_location("tether_cr_next_free", SCRIPT)
assert _spec is not None and _spec.loader is not None
queue = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(queue)


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh state folder, with the project folder made absent so it can never be touched."""
    monkeypatch.setattr(queue, "PROJECT_FOLDER", tmp_path / "no-project-folder")
    monkeypatch.delenv(queue.ENVIRONMENT, raising=False)
    folder = tmp_path / "state"
    queue.use_state(folder)
    return folder


def test_the_state_folder_is_resolved_in_the_documented_order(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert queue.state_dir(str(tmp_path / "given")) == tmp_path / "given"
    monkeypatch.setenv(queue.ENVIRONMENT, str(tmp_path / "from-env"))
    assert queue.state_dir() == tmp_path / "from-env"
    monkeypatch.delenv(queue.ENVIRONMENT)
    mounted = tmp_path / "project"
    mounted.mkdir()
    monkeypatch.setattr(queue, "PROJECT_FOLDER", mounted)
    assert queue.state_dir() == mounted
    monkeypatch.setattr(queue, "PROJECT_FOLDER", tmp_path / "absent")
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
        cwd=_REPO,
    ).stdout.strip()
    monkeypatch.chdir(_REPO)
    assert queue.state_dir() == Path(common) / "review-queue"


def test_a_state_folder_priority_list_wins_over_the_tracked_copy(state: Path) -> None:
    assert queue.PRIORITY == queue.TRACKED_PRIORITY
    state.mkdir()
    (state / "priority.md").write_text("# Order\n\n#7 | first\n#3 | second\n", encoding="utf-8")
    queue.use_state(state)
    assert queue.priority() == {7: 0, 3: 1}


def test_the_tracked_priority_list_reads_only_numbered_lines() -> None:
    queue.use_state(Path("unused"))
    ranks = queue.priority()
    lines = queue.TRACKED_PRIORITY.read_text(encoding="utf-8").splitlines()
    numbered = [line for line in lines if queue.RANKED.match(line.strip())]
    assert ranks, "the tracked priority list ranks nothing"
    assert len(ranks) == len(numbered), "a header line was read as a pull request"


def test_a_join_never_replaces_an_entry_and_a_leave_removes_only_its_own(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert queue.join(11, "first title") == 0
    entry = (state / "queue" / "11.md").read_text(encoding="utf-8")
    assert queue.join(11, "another title") == 0
    assert (state / "queue" / "11.md").read_text(encoding="utf-8") == entry
    assert queue.join(12, "other") == 0
    assert queue.leave(11) == 0
    assert sorted(p.name for p in (state / "queue").iterdir()) == ["12.md"]
    assert [number for _when, number, _title in queue.entries()] == [12]
    assert "already in" in capsys.readouterr().out


def _bot(body: str, when: datetime) -> dict:
    return {
        "user": {"login": queue.BOT},
        "body": body,
        "created_at": _stamp(when),
        "updated_at": _stamp(when),
        "html_url": "https://github.com/bioedca/tether/pull/5#issuecomment-1",
    }


def test_the_pace_is_one_review_an_hour_whatever_the_allowance() -> None:
    """The maintainer, 2026-11-23: "one coderabbit review per hour" (ADR-0142)."""
    now = datetime(2026, 11, 19, 20, 0, tzinfo=UTC)
    reading = _bot("set your current allowance at 10 reviews per hour.", now - timedelta(hours=1))
    assert queue.pace([reading], now)[0] == timedelta(minutes=60)
    refusal = _bot("Review rate limited. available in 20 minutes", now - timedelta(minutes=5))
    assert queue.pace([reading, refusal], now)[0] == queue.SLOW
    assert queue.pace([], now)[0] == queue.SLOW


def _github(
    monkeypatch: pytest.MonkeyPatch, comments: list[dict], checks: dict[int, list[dict]]
) -> None:
    pulls = [{"number": n, "head": {"sha": f"{n:040x}"}} for n in checks]

    def gh_all(path: str, **_fields: str) -> list:
        return comments if path.endswith("/issues/comments") else pulls

    def gh(path: str, **_fields: str):
        if "/check-runs" in path:
            number = int(path.split("/commits/")[1].split("/")[0], 16)
            return {"check_runs": checks[number]}
        return {"mergeable_state": "clean"}

    monkeypatch.setattr(queue, "gh_all", gh_all)
    monkeypatch.setattr(queue, "gh", gh)


def test_ask_is_given_once_per_window(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    queue.join(21, "first")
    queue.join(22, "second")
    _github(monkeypatch, [], {21: [], 22: []})
    assert queue.status(21) == 0
    assert "answer       ASK" in capsys.readouterr().out
    assert len(list((state / "slots").glob("*.lock"))) == 1
    assert queue.status(22) == 1, "a second pull request read ASK in the same window"


def test_a_running_review_on_any_pull_request_means_wait(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    queue.join(31, "waiting")
    running = {
        "name": queue.CHECK,
        "status": "in_progress",
        "conclusion": None,
        "started_at": _stamp(datetime.now(UTC) - timedelta(minutes=3)),
    }
    _github(monkeypatch, [], {31: [], 32: [running]})
    assert queue.status(31) == 1
    out = capsys.readouterr().out
    assert "running      a CodeRabbit review is in progress on #32" in out
    assert not (state / "slots").exists() or not list((state / "slots").glob("*.lock"))


def test_an_unanswered_request_holds_the_slot(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    queue.join(41, "waiting")
    request = {
        "user": {"login": "bioedca"},
        "body": "@coderabbitai full review",
        "created_at": _stamp(datetime.now(UTC) - timedelta(seconds=5)),
        "updated_at": _stamp(datetime.now(UTC) - timedelta(seconds=5)),
        "html_url": "https://github.com/bioedca/tether/pull/42#issuecomment-2",
    }
    _github(monkeypatch, [request], {41: [], 42: []})
    assert queue.status(41) == 1
    assert "WAIT" in capsys.readouterr().out


def test_a_refusal_older_than_the_pace_lookback_still_holds_its_named_wait(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex P1 on #1296: a 6-hour wait named 4 hours ago is still in force."""
    queue.join(51, "waiting")
    refusal = _bot(
        "Review rate limited. Next review available in 6 hours.",
        datetime.now(UTC) - timedelta(hours=4),
    )
    refusal["html_url"] = "https://github.com/bioedca/tether/pull/52#issuecomment-3"
    seen: dict[str, str] = {}

    def gh_all(path: str, **fields: str) -> list:
        if path.endswith("/issues/comments"):
            seen.update(fields)
            return [refusal]
        return [{"number": 51, "head": {"sha": f"{51:040x}"}}]

    monkeypatch.setattr(queue, "gh_all", gh_all)
    monkeypatch.setattr(
        queue,
        "gh",
        lambda path, **_f: (
            {"check_runs": []} if "/check-runs" in path else {"mergeable_state": "clean"}
        ),
    )
    assert queue.status(51) == 1
    assert "answer       WAIT" in capsys.readouterr().out
    assert queue.parse(seen["since"]) <= datetime.now(UTC) - timedelta(hours=5)
