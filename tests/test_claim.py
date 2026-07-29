# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for the atomic issue-claim helper.

Every GitHub call goes through a fake transport: CI must not depend on the network, and the
interesting cases (losing a 422 race, a reclaimed generation) cannot be produced on demand against
a live repository anyway.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "bin" / "claim.py"

_spec = importlib.util.spec_from_file_location("tether_claim", SCRIPT)
assert _spec is not None and _spec.loader is not None
claim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(claim)

DIGEST = "a" * 64
OTHER = "b" * 64
MARKER = '<!-- tether-agent-ready {"version":1,"criteria_sha256":"' + DIGEST + '"} -->'
HEAD = "c" * 40


def _issue(**overrides: Any) -> dict[str, Any]:
    issue = {
        "state": "open",
        "title": "feat(io): a thing",
        "body": "Acceptance criteria\n",
        "labels": [{"name": "status:ready"}],
        "assignees": [],
    }
    issue.update(overrides)
    return issue


class Fake:
    """Records every request and answers from a routing table keyed by (method, path-prefix)."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path))
        for (route_method, prefix), response in self.routes.items():
            if method == route_method and path.startswith(prefix):
                return response
        return 200, None


def _install(monkeypatch: pytest.MonkeyPatch, fake: Fake) -> Fake:
    # Patch the module OBJECT, not a dotted string: a dotted patch of a non-package module
    # resolves differently under CI's import layout.
    monkeypatch.setattr(claim, "_request", fake)
    monkeypatch.setattr(claim, "_scope_hash", lambda title, body: DIGEST)
    return fake


Routes = dict[tuple[str, str], tuple[int, Any]]


def _routes(over: Routes | None = None) -> Routes:
    routes: dict[tuple[str, str], tuple[int, Any]] = {
        ("GET", "/repos/bioedca/tether/issues/7/comments"): (
            200,
            [{"user": {"login": "bioedca"}, "body": f"Approved.\n\n{MARKER}"}],
        ),
        ("GET", "/repos/bioedca/tether/issues/7"): (200, _issue()),
        ("GET", "/repos/bioedca/tether/git/ref/heads/main"): (200, {"object": {"sha": HEAD}}),
        ("POST", "/repos/bioedca/tether/git/refs"): (201, {}),
        ("GET", "/repos/bioedca/tether/activity"): (
            200,
            [{"id": 42, "activity_type": "branch_creation"}],
        ),
    }
    routes.update(over or {})
    return routes


# --------------------------------------------------------------------- eligibility


@pytest.mark.parametrize(
    ("issue", "message"),
    [
        (_issue(state="closed"), "not open"),
        (_issue(labels=[{"name": "status:blocked"}]), "not status:ready"),
        (_issue(assignees=[{"login": "someone-else"}]), "assigned to someone else"),
        (_issue(pull_request={"url": "x"}), "pull request"),
    ],
    ids=["closed", "not-ready", "other-assignee", "is-a-pr"],
)
def test_claim_refuses_ineligible_work_before_creating_any_ref(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    issue: dict[str, Any],
    message: str,
) -> None:
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, issue)})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert message in capsys.readouterr().err
    # The mutex must never be taken for work that may not be worked.
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_claim_refuses_when_the_approval_no_longer_binds_the_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The issue was edited after approval: the marker's digest no longer matches the snapshot."""
    fake = _install(monkeypatch, Fake(_routes()))
    monkeypatch.setattr(claim, "_scope_hash", lambda title, body: OTHER)
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert "edited after approval" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_claim_ignores_an_approval_from_a_non_maintainer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = _routes(
        {
            ("GET", "/repos/bioedca/tether/issues/7/comments"): (
                200,
                [{"user": {"login": "a-stranger"}, "body": MARKER}],
            )
        }
    )
    _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert "no maintainer approval" in capsys.readouterr().err


# --------------------------------------------------------------------- the mutex


def test_claim_wins_and_reports_the_server_assigned_generation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, Fake(_routes()))
    claim._cmd_claim(_args(issue=7))
    record = json.loads(capsys.readouterr().out)
    assert record["branch"] == "agent/issue-7"
    assert record["generation"] == 42
    assert record["base_sha"] == HEAD


def test_losing_the_race_is_an_ordinary_exit_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = _routes(
        {("POST", "/repos/bioedca/tether/git/refs"): (422, {"message": "Reference already exists"})}
    )
    _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_LOST
    captured = capsys.readouterr().err
    assert "already exists" in captured
    assert "Traceback" not in captured
    assert str(ROOT) not in captured


def test_a_failed_label_write_does_not_void_the_claim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The label is a mirror, never the lock: the next agent still gets 422 either way."""
    routes = _routes({("POST", "/repos/bioedca/tether/issues/7/labels"): (403, None)})
    _install(monkeypatch, Fake(routes))
    claim._cmd_claim(_args(issue=7))
    record = json.loads(capsys.readouterr().out)
    assert record["generation"] == 42
    assert record["label_mirror"] is False


# --------------------------------------------------------------------- fencing


def test_check_passes_only_for_the_current_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, Fake(_routes()))
    claim._cmd_check(_args(issue=7, generation=42))


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([{"id": 99, "activity_type": "branch_creation"}], "was reclaimed"),
        ([], "no claim ref"),
        ([{"id": 99, "activity_type": "push"}], "no claim ref"),
        # The activity API keeps the creation entry after the ref is deleted, so reading only
        # creations would tell a reaped worker it still holds the claim. Verified live.
        (
            [
                {"id": 42, "activity_type": "branch_creation"},
                {"id": 77, "activity_type": "branch_deletion"},
            ],
            "no claim ref",
        ),
    ],
    ids=["reclaimed", "no-ref", "pushes-are-not-creations", "reaped-then-stale-creation-remains"],
)
def test_a_superseded_worker_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entries: list[dict[str, Any]],
    message: str,
) -> None:
    routes = _routes({("GET", "/repos/bioedca/tether/activity"): (200, entries)})
    _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_check(_args(issue=7, generation=42))
    assert exit_info.value.code == claim.EXIT_SUPERSEDED
    assert message in capsys.readouterr().err


def test_release_refuses_to_delete_a_successors_claim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = _routes(
        {
            ("GET", "/repos/bioedca/tether/activity"): (
                200,
                [{"id": 99, "activity_type": "branch_creation"}],
            )
        }
    )
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_release(_args(issue=7, generation=42, vendor="claude"))
    assert exit_info.value.code == claim.EXIT_SUPERSEDED
    assert "releasing would delete a successor" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "DELETE" and "git/refs" in c[1]]


def test_a_recreated_ref_supersedes_the_deleted_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reclaim is delete-then-recreate: the successor's creation must win over the deletion."""
    _install(
        monkeypatch,
        Fake(
            _routes(
                {
                    ("GET", "/repos/bioedca/tether/activity"): (
                        200,
                        [
                            {"id": 42, "activity_type": "branch_creation"},
                            {"id": 77, "activity_type": "branch_deletion"},
                            {"id": 91, "activity_type": "branch_creation"},
                        ],
                    ),
                }
            )
        ),
    )
    assert claim._generation(7) == 91


def test_generation_never_comes_from_commit_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """committedDate is client-settable, so the generation must come from the activity API alone."""
    fake = _install(monkeypatch, Fake(_routes()))
    claim._generation(7)
    paths = [path for _, path in fake.calls]
    assert any("/activity?ref=refs/heads/agent/issue-7" in p for p in paths)
    assert not [p for p in paths if "/commits" in p or "graphql" in p]


# --------------------------------------------------------------------- ADR reservation


def test_reserve_adr_skips_taken_numbers_and_never_uses_a_tag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-version tag breaks hatch-vcs version derivation and turns main red."""
    posted: list[str] = []

    def transport(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "GET" and path.endswith("/git/matching-refs/adr-reservations"):
            return 200, [{"ref": "refs/adr-reservations/0058"}]
        if method == "GET" and path.endswith("/contents/docs/adr"):
            return 200, [{"name": "0057-a.md"}, {"name": "0052-b.md"}]
        if method == "GET" and "git/ref/heads/main" in path:
            return 200, {"object": {"sha": HEAD}}
        if method == "POST" and path.endswith("/git/refs"):
            posted.append(body["ref"])
            return (422, None) if body["ref"].endswith("0059") else (201, {})
        return 200, None

    monkeypatch.setattr(claim, "_request", transport)
    claim._cmd_reserve_adr(_args(attempts=8))
    assert json.loads(capsys.readouterr().out)["adr"] == "0060"
    assert posted == ["refs/adr-reservations/0059", "refs/adr-reservations/0060"]
    assert not [ref for ref in posted if ref.startswith("refs/tags/")]


def _args(**values: Any) -> Any:
    defaults = {"vendor": "claude", "owner": "bioedca", "base": None, "attempts": 16}
    defaults.update(values)
    return type("Args", (), defaults)()
