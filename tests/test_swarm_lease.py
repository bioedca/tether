# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for the public issue-swarm lease helper."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "skills" / "run-issue-swarm" / "scripts" / "swarm_lease.py"
RUN_ID = "swarm-test"
REPOSITORY = "bioedca/tether"
ISSUE = "17"
OWNER = "bioedca"
AGENT_ID = "codex-branch-manager-0123abcd"
PERSONA = "branch-manager"
BASE_SHA = "a" * 40
BRANCH = "fix/issue-17-stabilize-lease"
CRITERIA_SHA = "b" * 64
APPROVAL_COMMENT_ID = "1234"
NOW = "2026-07-21T00:00:00Z"
TEST_CLOCK_ENV = "TETHER_SWARM_ALLOW_TEST_CLOCK"
RUN_FILTER = 'is:issue is:open label:"status:ready"'
RUN_FILTER_SHA = hashlib.sha256(RUN_FILTER.encode()).hexdigest()
ANCHOR_ISSUE = "17"


def _run(
    *args: str,
    input_text: str | None = None,
    allow_test_clock: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop(TEST_CLOCK_ENV, None)
    if allow_test_clock:
        env[TEST_CLOCK_ENV] = "1"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        input=input_text,
        text=True,
        encoding="utf-8",
        env=env,
        capture_output=True,
        check=False,
    )


def _binding_args(**overrides: str) -> list[str]:
    values = {
        "run_id": RUN_ID,
        "repository": REPOSITORY,
        "issue": ISSUE,
        "owner": OWNER,
        "agent_id": AGENT_ID,
        "base_sha": BASE_SHA,
        "branch": BRANCH,
        "criteria_sha": CRITERIA_SHA,
        "approval_comment_id": APPROVAL_COMMENT_ID,
    }
    values.update(overrides)
    return [
        "--run-id",
        values["run_id"],
        "--repository",
        values["repository"],
        "--issue",
        values["issue"],
        "--owner",
        values["owner"],
        "--agent-id",
        values["agent_id"],
        "--base-sha",
        values["base_sha"],
        "--branch",
        values["branch"],
        "--criteria-sha256",
        values["criteria_sha"],
        "--approval-comment-id",
        values["approval_comment_id"],
    ]


def _comment(
    *,
    branch: str = BRANCH,
    hours: str = "4",
    agent_id: str = AGENT_ID,
    now: str = NOW,
) -> subprocess.CompletedProcess[str]:
    return _run(
        "comment",
        *_binding_args(branch=branch, agent_id=agent_id),
        "--persona-slug",
        PERSONA,
        "--hours",
        hours,
        "--now",
        now,
    )


def _run_control_comment(
    *,
    terminal_policy: str = "PR-ready",
    merge_authority_comment_id: str | None = None,
    authority_text: str | None = None,
    run_filter: str = RUN_FILTER,
) -> subprocess.CompletedProcess[str]:
    args = [
        "run-comment",
        "--run-id",
        RUN_ID,
        "--repository",
        REPOSITORY,
        "--anchor-issue",
        ANCHOR_ISSUE,
        "--owner",
        OWNER,
        "--filter",
        run_filter,
        "--count",
        "4",
        "--terminal-policy",
        terminal_policy,
        "--start-sha",
        BASE_SHA,
        "--now",
        NOW,
    ]
    if merge_authority_comment_id is not None:
        args.extend(["--merge-authority-comment-id", merge_authority_comment_id])
    if authority_text is not None:
        args.extend(
            [
                "--merge-authority-repository",
                REPOSITORY,
                "--merge-authority-issue",
                ANCHOR_ISSUE,
                "--merge-authority-author",
                OWNER,
                "--merge-authority-server-created-at",
                NOW,
                "--merge-authority-server-updated-at",
                NOW,
                "--merge-authority-file",
                "-",
            ]
        )
    return _run(*args, input_text=authority_text)


def _merge_authority_comment(*, run_id: str = RUN_ID) -> subprocess.CompletedProcess[str]:
    return _run(
        "merge-authority-comment",
        "--run-id",
        run_id,
        "--repository",
        REPOSITORY,
        "--anchor-issue",
        ANCHOR_ISSUE,
        "--owner",
        OWNER,
        "--filter-sha256",
        RUN_FILTER_SHA,
        "--count",
        "4",
        "--start-sha",
        BASE_SHA,
        "--now",
        NOW,
    )


def _record(comment: str) -> dict[str, object]:
    match = re.search(r"<!-- tether-agent-lease\s*(\{.*\})\s*-->", comment, re.DOTALL)
    assert match, "rendered comment must contain exactly one lease record"
    return json.loads(match.group(1))


def _run_record(comment: str) -> dict[str, object]:
    match = re.search(r"<!-- tether-swarm-run\s*(\{.*\})\s*-->", comment, re.DOTALL)
    assert match, "rendered comment must contain exactly one run record"
    return json.loads(match.group(1))


def _run_transition_record(comment: str) -> dict[str, object]:
    match = re.search(
        r"<!-- tether-swarm-run-transition\s*(\{.*\})\s*-->", comment, re.DOTALL
    )
    assert match, "rendered comment must contain exactly one run transition"
    return json.loads(match.group(1))


def test_identities_are_unique_public_safe_and_four_hours() -> None:
    result = _run(
        "identities",
        "--count",
        "4",
        "--owner",
        OWNER,
        "--hours",
        "4",
        "--run-id",
        RUN_ID,
        "--exclude-persona",
        PERSONA,
        "--now",
        NOW,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    workers = payload["workers"]
    assert payload["lease_hours"] == 4
    assert len(workers) == 4
    assert len({worker["agent_id"] for worker in workers}) == 4
    assert all(worker["persona_slug"] != PERSONA for worker in workers)
    assert all(
        re.fullmatch(r"codex-[a-z0-9-]+-[0-9a-f]{8}", worker["agent_id"])
        for worker in workers
    )


def test_run_control_round_trips_with_strict_immutable_policy() -> None:
    comment = _run_control_comment()

    assert comment.returncode == 0, comment.stderr
    record = _run_record(comment.stdout)
    assert record["mode"] == "running"
    assert record["terminal_policy"] == "PR-ready"
    assert record["merge_authority_comment_id"] is None
    assert record["filter_sha256"] == RUN_FILTER_SHA
    assert "filter" not in record
    assert RUN_FILTER not in comment.stdout

    inspected = _run("run-inspect", input_text=comment.stdout)
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout) == record


def test_merge_run_requires_a_bound_authority_comment(tmp_path: Path) -> None:
    authority = _merge_authority_comment()
    assert authority.returncode == 0, authority.stderr
    wrong_authority = _merge_authority_comment(run_id="swarm-other")
    assert wrong_authority.returncode == 0, wrong_authority.stderr

    missing = _run_control_comment(terminal_policy="merge")
    unverified = _run_control_comment(
        terminal_policy="merge", merge_authority_comment_id=APPROVAL_COMMENT_ID
    )
    mismatched = _run_control_comment(
        terminal_policy="merge",
        merge_authority_comment_id=APPROVAL_COMMENT_ID,
        authority_text=wrong_authority.stdout,
    )
    accepted = _run_control_comment(
        terminal_policy="merge",
        merge_authority_comment_id=APPROVAL_COMMENT_ID,
        authority_text=authority.stdout,
    )

    assert missing.returncode == 2
    assert unverified.returncode == 2
    assert mismatched.returncode == 2
    assert "does not match" in mismatched.stderr
    assert accepted.returncode == 0, accepted.stderr
    assert _run_record(accepted.stdout)["merge_authority_comment_id"] == int(
        APPROVAL_COMMENT_ID
    )

    unbound_inspect = _run("run-inspect", input_text=accepted.stdout)
    assert unbound_inspect.returncode == 2
    authority_path = tmp_path / "authority.md"
    authority_path.write_text(authority.stdout, encoding="utf-8")
    bound_inspect = _run(
        "run-inspect",
        "--merge-authority-file",
        str(authority_path),
        input_text=accepted.stdout,
    )
    assert bound_inspect.returncode == 0, bound_inspect.stderr
    authority_inspected = _run("merge-authority-inspect", input_text=authority.stdout)
    assert authority_inspected.returncode == 0, authority_inspected.stderr

    lineage_document = {
        "version": 1,
        "run_comment": {
            "comment_id": 9000,
            "repository": REPOSITORY,
            "issue": int(ANCHOR_ISSUE),
            "author": OWNER,
            "server_created_at": NOW,
            "server_updated_at": NOW,
            "body": accepted.stdout,
        },
        "merge_authority_comment": {
            "comment_id": int(APPROVAL_COMMENT_ID),
            "repository": REPOSITORY,
            "issue": int(ANCHOR_ISSUE),
            "author": OWNER,
            "server_created_at": NOW,
            "server_updated_at": NOW,
            "body": authority.stdout,
        },
        "events": [],
    }
    lineage = _run("run-lineage", input_text=json.dumps(lineage_document))
    assert lineage.returncode == 0, lineage.stderr

    same_comment = json.loads(json.dumps(lineage_document))
    same_comment["run_comment"]["comment_id"] = int(APPROVAL_COMMENT_ID)
    rejected_lineage = _run("run-lineage", input_text=json.dumps(same_comment))
    assert rejected_lineage.returncode == 2
    assert "distinct prior" in rejected_lineage.stderr


def test_merge_authority_comment_round_trips() -> None:
    authority = _merge_authority_comment()

    assert authority.returncode == 0, authority.stderr
    inspected = _run("merge-authority-inspect", input_text=authority.stdout)
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)["terminal_policy"] == "merge"


def test_run_transition_is_append_only_predecessor_bound_and_monotonic() -> None:
    comment = _run_control_comment()
    assert comment.returncode == 0, comment.stderr

    drained = _run(
        "run-transition",
        "--run-comment-id",
        "9000",
        "--predecessor-comment-id",
        "9000",
        "--predecessor-repository",
        REPOSITORY,
        "--predecessor-issue",
        ANCHOR_ISSUE,
        "--predecessor-author",
        OWNER,
        "--predecessor-server-created-at",
        NOW,
        "--predecessor-server-updated-at",
        NOW,
        "--mode",
        "draining",
        "--now",
        "2026-07-21T00:01:00Z",
        input_text=comment.stdout,
    )
    assert drained.returncode == 0, drained.stderr
    drained_record = _run_transition_record(drained.stdout)
    assert drained_record["from_mode"] == "running"
    assert drained_record["mode"] == "draining"
    assert drained_record["predecessor_comment_id"] == 9000
    assert re.fullmatch(r"[0-9a-f]{64}", str(drained_record["predecessor_sha256"]))

    frozen = _run(
        "run-transition",
        "--run-comment-id",
        "9000",
        "--predecessor-comment-id",
        "9001",
        "--predecessor-repository",
        REPOSITORY,
        "--predecessor-issue",
        ANCHOR_ISSUE,
        "--predecessor-author",
        OWNER,
        "--predecessor-server-created-at",
        "2026-07-21T00:01:00Z",
        "--predecessor-server-updated-at",
        "2026-07-21T00:01:00Z",
        "--mode",
        "frozen",
        "--now",
        "2026-07-21T00:02:00Z",
        input_text=drained.stdout,
    )
    assert frozen.returncode == 0, frozen.stderr
    frozen_record = _run_transition_record(frozen.stdout)
    assert frozen_record["from_mode"] == "draining"
    assert frozen_record["mode"] == "frozen"

    regression = _run(
        "run-transition",
        "--run-comment-id",
        "9000",
        "--predecessor-comment-id",
        "9002",
        "--predecessor-repository",
        REPOSITORY,
        "--predecessor-issue",
        ANCHOR_ISSUE,
        "--predecessor-author",
        OWNER,
        "--predecessor-server-created-at",
        "2026-07-21T00:02:00Z",
        "--predecessor-server-updated-at",
        "2026-07-21T00:02:00Z",
        "--mode",
        "draining",
        "--now",
        "2026-07-21T00:03:00Z",
        input_text=frozen.stdout,
    )
    assert regression.returncode == 2
    assert "cannot transition run frozen to draining" in regression.stderr

    stale_drain = _run(
        "run-transition",
        "--run-comment-id",
        "9000",
        "--predecessor-comment-id",
        "9000",
        "--predecessor-repository",
        REPOSITORY,
        "--predecessor-issue",
        ANCHOR_ISSUE,
        "--predecessor-author",
        OWNER,
        "--predecessor-server-created-at",
        NOW,
        "--predecessor-server-updated-at",
        NOW,
        "--mode",
        "draining",
        "--now",
        "2026-07-21T00:03:00Z",
        input_text=comment.stdout,
    )
    assert stale_drain.returncode == 0, stale_drain.stderr

    linear_document = {
        "version": 1,
        "run_comment": {
            "comment_id": 9000,
            "repository": REPOSITORY,
            "issue": int(ANCHOR_ISSUE),
            "author": OWNER,
            "server_created_at": NOW,
            "server_updated_at": NOW,
            "body": comment.stdout,
        },
        "merge_authority_comment": None,
        "events": [
            {
                "comment_id": 9001,
                "repository": REPOSITORY,
                "issue": int(ANCHOR_ISSUE),
                "author": OWNER,
                "server_created_at": "2026-07-21T00:01:00Z",
                "server_updated_at": "2026-07-21T00:01:00Z",
                "body": drained.stdout,
            },
            {
                "comment_id": 9002,
                "repository": REPOSITORY,
                "issue": int(ANCHOR_ISSUE),
                "author": OWNER,
                "server_created_at": "2026-07-21T00:02:00Z",
                "server_updated_at": "2026-07-21T00:02:00Z",
                "body": frozen.stdout,
            },
        ],
    }
    linear = _run("run-lineage", input_text=json.dumps(linear_document))
    assert linear.returncode == 0, linear.stderr
    assert json.loads(linear.stdout)["mode"] == "frozen"

    forked_document = dict(linear_document)
    forked_document["events"] = [
        *linear_document["events"],
        {
            "comment_id": 9003,
            "repository": REPOSITORY,
            "issue": int(ANCHOR_ISSUE),
            "author": OWNER,
            "server_created_at": "2026-07-21T00:03:00Z",
            "server_updated_at": "2026-07-21T00:03:00Z",
            "body": stale_drain.stdout,
        },
    ]
    forked = _run("run-lineage", input_text=json.dumps(forked_document))
    assert forked.returncode == 0, forked.stderr
    forked_state = json.loads(forked.stdout)
    assert forked_state["mode"] == "frozen"
    assert forked_state["safe_to_transition"] is False
    assert forked_state["reason"] == "forked-or-ambiguous-lineage"

    edited_leaf = json.loads(json.dumps(linear_document))
    edited_leaf["events"][-1]["server_updated_at"] = "2026-07-21T00:02:01Z"
    edited = _run("run-lineage", input_text=json.dumps(edited_leaf))
    assert edited.returncode == 2
    assert "immutable comment was edited" in edited.stderr

    forged_author = json.loads(json.dumps(linear_document))
    forged_author["run_comment"]["author"] = "different-owner"
    forged = _run("run-lineage", input_text=json.dumps(forged_author))
    assert forged.returncode == 2
    assert "server author does not match" in forged.stderr

    inspected = _run("run-transition-inspect", input_text=frozen.stdout)
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout) == frozen_record


def test_run_parser_rejects_duplicate_markers() -> None:
    comment = _run_control_comment()
    assert comment.returncode == 0, comment.stderr

    duplicate = _run("run-inspect", input_text=comment.stdout + comment.stdout)

    assert duplicate.returncode == 2
    assert "found 2" in duplicate.stderr

    authority = _merge_authority_comment()
    assert authority.returncode == 0, authority.stderr
    mixed = _run("run-inspect", input_text=comment.stdout + authority.stdout)
    assert mixed.returncode == 2
    assert "mixes or repeats marker tokens" in mixed.stderr

    malformed = _run(
        "run-inspect",
        input_text=comment.stdout + "\n<!-- tether-swarm-merge-authority garbage -->",
    )
    assert malformed.returncode == 2
    assert "mixes or repeats marker tokens" in malformed.stderr

    unknown = _run(
        "run-inspect",
        input_text=comment.stdout + "\n<!-- tether-swarm-unknown garbage -->",
    )
    assert unknown.returncode == 2
    assert "mixes or repeats marker tokens" in unknown.stderr


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_run_filter_rejects_unicode_line_separators(separator: str) -> None:
    result = _run_control_comment(run_filter=f"is:issue{separator}is:open")

    assert result.returncode == 2
    assert "single-line" in result.stderr


def test_comment_round_trips_and_expires_after_exactly_four_hours() -> None:
    comment = _comment()

    assert comment.returncode == 0, comment.stderr
    assert "@bioedca" in comment.stdout
    assert "The Branch Manager" in comment.stdout
    assert "2026-07-21T04:00:00Z" in comment.stdout
    assert comment.stdout.count("<!-- tether-agent-lease") == 1

    inspected = _run(
        "inspect",
        "--now",
        "2026-07-21T01:00:00Z",
        input_text=comment.stdout,
    )
    assert inspected.returncode == 0, inspected.stderr
    payload = json.loads(inspected.stdout)
    assert payload["expired"] is False
    assert payload["seconds_remaining"] == 3 * 60 * 60
    assert payload["branch"] == BRANCH


def test_clock_override_requires_the_isolated_test_gate() -> None:
    result = _run(
        "identities",
        "--count",
        "1",
        "--owner",
        OWNER,
        "--now",
        NOW,
        allow_test_clock=False,
    )

    assert result.returncode == 2
    assert TEST_CLOCK_ENV in result.stderr


def test_comment_rejects_an_expiry_outside_the_supported_range() -> None:
    result = _comment(now="9999-12-31T23:59:59Z")

    assert result.returncode == 2
    assert "lease expiry is outside" in result.stderr
    assert "Traceback" not in result.stderr
    assert str(ROOT) not in result.stderr


def test_fractional_second_before_expiry_is_still_active() -> None:
    comment = _comment()
    assert comment.returncode == 0, comment.stderr

    before = _run(
        "inspect",
        "--now",
        "2026-07-21T03:59:59.500000Z",
        input_text=comment.stdout,
    )
    at_expiry = _run(
        "inspect",
        "--now",
        "2026-07-21T04:00:00Z",
        input_text=comment.stdout,
    )

    assert before.returncode == 0, before.stderr
    assert json.loads(before.stdout)["expired"] is False
    assert json.loads(before.stdout)["seconds_remaining"] == 1
    assert at_expiry.returncode == 0, at_expiry.stderr
    assert json.loads(at_expiry.stdout)["expired"] is True
    assert json.loads(at_expiry.stdout)["seconds_remaining"] == 0


def test_agent_id_must_exactly_match_the_persona_shape() -> None:
    spoofed_id = "codex-branch-manager-forged-0123abcd"
    generated = _comment(agent_id=spoofed_id)

    assert generated.returncode == 2
    assert generated.stdout == ""
    assert "exactly match" in generated.stderr

    comment = _comment()
    assert comment.returncode == 0, comment.stderr
    record = _record(comment.stdout)
    record["agent_id"] = spoofed_id
    inspected = _run(
        "inspect",
        input_text=f"<!-- tether-agent-lease\n{json.dumps(record)}\n-->",
    )
    assert inspected.returncode == 2
    assert "exactly match" in inspected.stderr


def test_renew_and_transition_require_the_full_immutable_binding() -> None:
    comment = _comment()
    assert comment.returncode == 0, comment.stderr

    renewed = _run(
        "renew",
        *_binding_args(),
        "--hours",
        "4",
        "--now",
        "2026-07-21T01:00:00Z",
        input_text=comment.stdout,
    )
    assert renewed.returncode == 0, renewed.stderr
    assert _record(renewed.stdout)["expires_at"] == "2026-07-21T05:00:00Z"

    transitioned = _run(
        "transition",
        *_binding_args(),
        "--state",
        "handoff",
        input_text=renewed.stdout,
    )
    assert transitioned.returncode == 0, transitioned.stderr
    assert _record(transitioned.stdout)["state"] == "handoff"


@pytest.mark.parametrize("command", ["renew", "transition"])
@pytest.mark.parametrize(
    ("key", "value", "field"),
    [
        ("run_id", "swarm-other", "run_id"),
        ("repository", "other/repository", "repository"),
        ("issue", "18", "issue"),
        ("owner", "other-owner", "owner"),
        ("agent_id", "codex-branch-manager-deadbeef", "agent_id"),
        ("base_sha", "c" * 40, "base_sha"),
        ("branch", "fix/issue-17-other-branch", "branch"),
        ("criteria_sha", "d" * 64, "criteria_sha256"),
        ("approval_comment_id", "1235", "approval_comment_id"),
    ],
)
def test_every_binding_field_is_checked(
    command: str, key: str, value: str, field: str
) -> None:
    comment = _comment()
    assert comment.returncode == 0, comment.stderr
    operation_args = (
        ["--now", "2026-07-21T01:00:00Z"]
        if command == "renew"
        else ["--state", "handoff"]
    )

    result = _run(
        command,
        *_binding_args(**{key: value}),
        *operation_args,
        input_text=comment.stdout,
    )
    assert result.returncode == 2
    assert f"{command if command == 'transition' else 'renewal'} {field}" in result.stderr


@pytest.mark.parametrize(
    "branch",
    [
        "fix/issue-17--><b>spoof</b><!--",
        "fix/issue-17-bad}",
        "fix/issue-17-`spoof`",
        "fix/issue-17-upperCase",
        "fix/issue-18-wrong-work-item",
        "fix/advisory-ghsa-example-private-work",
        "codex/agent-workflow",
        "fix/issue-17-" + ("a" * 300),
    ],
)
def test_comment_rejects_markup_and_nonconventional_branches(branch: str) -> None:
    result = _comment(branch=branch)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "documented conventional worker grammar" in result.stderr


@pytest.mark.parametrize("hours", ["nan", "inf", "0", "1", "24", "25", "0.0001"])
def test_non_four_hour_values_are_rejected(hours: str) -> None:
    result = _comment(hours=hours)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "lease hours must be exactly 4" in result.stderr


def test_comment_rejects_noncanonical_repository_names() -> None:
    result = _run(
        "comment",
        *_binding_args(repository="../.."),
        "--persona-slug",
        PERSONA,
        "--now",
        NOW,
    )

    assert result.returncode == 2
    assert "canonical GitHub owner/name" in result.stderr


def test_excluded_persona_cannot_inject_logs() -> None:
    result = _run(
        "identities",
        "--count",
        "1",
        "--owner",
        OWNER,
        "--exclude-persona",
        "\n::error:: forged",
        "--now",
        NOW,
    )

    assert result.returncode == 2
    assert result.stderr.strip() == "error: unknown excluded persona"
    assert "forged" not in result.stderr


def test_inspect_rejects_unknown_fields_and_noncanonical_owner() -> None:
    comment = _comment()
    assert comment.returncode == 0, comment.stderr
    record = _record(comment.stdout)

    record["unexpected"] = "not part of version 1"
    unknown = _run(
        "inspect",
        input_text=f"<!-- tether-agent-lease\n{json.dumps(record)}\n-->",
    )
    assert unknown.returncode == 2
    assert "unknown fields" in unknown.stderr

    record.pop("unexpected")
    record["owner"] = "@bioedca"
    noncanonical = _run(
        "inspect",
        input_text=f"<!-- tether-agent-lease\n{json.dumps(record)}\n-->",
    )
    assert noncanonical.returncode == 2
    assert "canonical GitHub login" in noncanonical.stderr


def test_inspect_rejects_noncanonical_uppercase_base_sha() -> None:
    comment = _comment()
    assert comment.returncode == 0, comment.stderr
    record = _record(comment.stdout)
    record["base_sha"] = "A" * 40

    result = _run(
        "inspect",
        input_text=f"<!-- tether-agent-lease\n{json.dumps(record)}\n-->",
    )

    assert result.returncode == 2
    assert "full lowercase" in result.stderr


def test_duplicate_json_keys_are_rejected_without_reflecting_input() -> None:
    comment = _comment()
    assert comment.returncode == 0, comment.stderr
    serialized = json.dumps(_record(comment.stdout))
    duplicate = serialized.replace("{", '{"version": 1,', 1)

    result = _run(
        "inspect",
        input_text=f"<!-- tether-agent-lease\n{duplicate}\n-->",
    )

    assert result.returncode == 2
    assert "lease JSON repeats a field" in result.stderr
    assert "Traceback" not in result.stderr
    assert str(ROOT) not in result.stderr


def test_deeply_nested_json_is_rejected_without_a_traceback() -> None:
    payload = (
        '<!-- tether-agent-lease\n{"issue":'
        + ("[" * 60_000)
        + "0"
        + ("]" * 60_000)
        + "}\n-->"
    )

    result = _run("inspect", input_text=payload)

    assert result.returncode == 2
    assert result.stderr.strip() == "error: lease JSON is invalid"
    assert "Traceback" not in result.stderr
    assert str(ROOT) not in result.stderr


def test_oversized_stdin_is_rejected_before_unbounded_reading() -> None:
    result = _run("inspect", input_text="x" * 131_073)

    assert result.returncode == 2
    assert result.stderr.strip() == "error: input exceeds the safe read limit"
    assert "Traceback" not in result.stderr
    assert str(ROOT) not in result.stderr


@pytest.mark.parametrize("untrusted_key", ["\n::error:: forged", "\ud800"])
def test_unknown_json_keys_cannot_inject_logs(untrusted_key: str) -> None:
    comment = _comment()
    assert comment.returncode == 0, comment.stderr
    record = _record(comment.stdout)
    record[untrusted_key] = "x"

    result = _run(
        "inspect",
        input_text=f"<!-- tether-agent-lease\n{json.dumps(record)}\n-->",
    )

    assert result.returncode == 2
    assert result.stderr.strip() == "error: lease has unknown fields"
    assert "forged" not in result.stderr
    assert "Traceback" not in result.stderr
    assert str(ROOT) not in result.stderr


def test_huge_integer_json_never_emits_a_traceback_or_local_path() -> None:
    payload = '<!-- tether-agent-lease\n{"issue":' + ("9" * 5_000) + "}\n-->"

    result = _run("inspect", input_text=payload)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert str(ROOT) not in result.stderr


def test_huge_numeric_lease_hours_never_emits_a_traceback_or_local_path() -> None:
    comment = _comment()
    assert comment.returncode == 0, comment.stderr
    record = _record(comment.stdout)
    record["lease_hours"] = 10**400

    result = _run(
        "inspect",
        input_text=f"<!-- tether-agent-lease\n{json.dumps(record)}\n-->",
    )

    assert result.returncode == 2
    assert "finite numeric value" in result.stderr
    assert "Traceback" not in result.stderr
    assert str(ROOT) not in result.stderr


def test_undecodable_stdin_never_emits_a_traceback_or_local_path() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "inspect"],
        cwd=ROOT,
        input=b"\xff",
        capture_output=True,
        check=False,
    )
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 2
    assert result.stdout == b""
    assert stderr.strip() == "error: lease input is not valid UTF-8 text"
    assert "Traceback" not in stderr
    assert str(ROOT) not in stderr


def test_missing_input_file_never_emits_its_path() -> None:
    missing = ROOT / "private-path" / "missing-lease.txt"

    result = _run("inspect", "--file", str(missing))

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == "error: lease input could not be read"
    assert str(missing) not in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("state", [], "state must be a string"),
        ("issued_at", "not-a-timestamp", "valid ISO-8601"),
        (
            "issued_at",
            "2026-07-21\n00:00:00Z",
            "canonical UTC YYYY-MM-DDTHH:MM:SSZ",
        ),
        (
            "issued_at",
            "2026-07-21T00:00:00+00:00",
            "canonical UTC YYYY-MM-DDTHH:MM:SSZ",
        ),
        (
            "issued_at",
            "0001-01-01T00:00:00+23:59",
            "outside the supported UTC range",
        ),
        ("lease_hours", "4", "lease_hours must be numeric"),
        ("issue", True, "issue must be a positive signed 64-bit integer"),
        ("issue", 10**100, "issue must be a positive signed 64-bit integer"),
        (
            "approval_comment_id",
            10**100,
            "approval_comment_id must be a positive signed 64-bit integer",
        ),
    ],
)
def test_inspect_rejects_malformed_field_types(
    field: str, value: object, message: str
) -> None:
    comment = _comment()
    assert comment.returncode == 0, comment.stderr
    record = _record(comment.stdout)
    record[field] = value

    result = _run(
        "inspect",
        input_text=f"<!-- tether-agent-lease\n{json.dumps(record)}\n-->",
    )
    assert result.returncode == 2
    assert message in result.stderr


def test_expired_lease_cannot_be_renewed() -> None:
    comment = _comment()
    assert comment.returncode == 0, comment.stderr

    renewed = _run(
        "renew",
        *_binding_args(),
        "--now",
        "2026-07-21T04:00:00Z",
        input_text=comment.stdout,
    )
    assert renewed.returncode == 2
    assert "cannot renew an expired lease" in renewed.stderr


def test_expired_lease_can_only_transition_to_a_safe_terminal_state() -> None:
    comment = _comment()
    assert comment.returncode == 0, comment.stderr

    completed = _run(
        "transition",
        *_binding_args(),
        "--state",
        "completed",
        "--now",
        "2026-07-21T04:00:00Z",
        input_text=comment.stdout,
    )
    handed_off = _run(
        "transition",
        *_binding_args(),
        "--state",
        "handoff",
        "--now",
        "2026-07-21T04:00:00Z",
        input_text=comment.stdout,
    )

    assert completed.returncode == 2
    assert "only transition to handoff or released" in completed.stderr
    assert handed_off.returncode == 0, handed_off.stderr
    assert _record(handed_off.stdout)["state"] == "handoff"
