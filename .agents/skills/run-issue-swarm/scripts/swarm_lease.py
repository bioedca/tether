#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate and inspect public-safe issue-swarm identities and coordination comments."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PERSONAS = (
    (
        "branch-manager",
        "The Branch Manager",
        "I've checked this issue out—both emotionally and into a worktree.",
    ),
    (
        "merge-mechanic",
        "The Merge Mechanic",
        "I'm torqueing the tests before this change joins traffic.",
    ),
    (
        "stack-whisperer",
        "The Stack Whisperer",
        "This issue is on my call stack; recursive assignments are unnecessary.",
    ),
    (
        "lint-ranger",
        "The Lint Ranger",
        "I'm riding toward a cleaner diff, one warning at a time.",
    ),
    (
        "cache-wrangler",
        "The Cache Wrangler",
        "This issue is in hot storage; duplicate computation is unnecessary.",
    ),
    (
        "test-pilot",
        "The Test Pilot",
        "This change is cleared for a test flight, not a second cockpit.",
    ),
    (
        "refactor-raccoon",
        "The Refactor Raccoon",
        "I'm sorting this code bin carefully; please don't tip it over.",
    ),
    (
        "bug-barista",
        "The Bug Barista",
        "I'm brewing a fix; the lease covers any unexpected coffee exceptions.",
    ),
    (
        "commit-comet",
        "The Commit Comet",
        "This issue has entered my orbit and is headed for a clean landing.",
    ),
    (
        "build-whisperer",
        "The Build Whisperer",
        "I'm asking the build nicely before reaching for the bigger hammer.",
    ),
    (
        "pointer-sheriff",
        "The Pointer Sheriff",
        "I've got a valid reference to this issue; stray pointers can stand down.",
    ),
    (
        "diff-diver",
        "The Diff Diver",
        "I'm below the hunk line looking for the smallest safe change.",
    ),
)

PERSONA_BY_SLUG = {slug: (title, quip) for slug, title, quip in PERSONAS}
LEASE_RE = re.compile(r"<!--\s*tether-agent-lease\s*(\{.*?\})\s*-->", re.DOTALL)
RUN_CONTROL_RE = re.compile(r"<!--\s*tether-swarm-run\s*(\{.*?\})\s*-->", re.DOTALL)
RUN_TRANSITION_RE = re.compile(
    r"<!--\s*tether-swarm-run-transition\s*(\{.*?\})\s*-->", re.DOTALL
)
MERGE_AUTHORITY_RE = re.compile(
    r"<!--\s*tether-swarm-merge-authority\s*(\{.*?\})\s*-->", re.DOTALL
)
MARKER_TOKEN_RE = re.compile(
    r"<!--\s*(tether-swarm-merge-authority|tether-swarm-run-transition|"
    r"tether-swarm-run|tether-agent-lease)\b",
    re.IGNORECASE,
)
SWARM_TOKEN_PREFIX_RE = re.compile(r"<!--\s*tether-swarm-", re.IGNORECASE)
SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
RUN_RE = re.compile(r"^swarm-[a-zA-Z0-9._-]+$")
REPOSITORY_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/"
    r"(?P<name>[A-Za-z0-9_.-]{1,100})$"
)
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
BRANCH_RE = re.compile(
    r"^(?:feat|fix|docs|chore|refactor|test|ci|build|perf|revert)/"
    r"issue-(?P<issue>[1-9][0-9]*)-"
    r"[a-z0-9]+(?:-[a-z0-9]+)*$"
)
BRANCH_MAX_LENGTH = 120
ACTIVE_STATES = {"active"}
TERMINAL_STATES = {"completed", "released"}
ALL_STATES = ACTIVE_STATES | TERMINAL_STATES | {"blocked", "handoff"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_GITHUB_ID = 2**63 - 1
MAX_INPUT_CHARS = 131_072
TEST_CLOCK_ENV = "TETHER_SWARM_ALLOW_TEST_CLOCK"
RECORD_FIELDS = {
    "version",
    "run_id",
    "repository",
    "issue",
    "owner",
    "agent_id",
    "persona_slug",
    "persona",
    "state",
    "issued_at",
    "expires_at",
    "lease_hours",
    "base_sha",
    "branch",
    "criteria_sha256",
    "approval_comment_id",
}
RUN_TRANSITIONS = {
    "running": {"draining", "frozen", "completed"},
    "draining": {"frozen", "completed"},
}
RUN_MODES = {"running", "draining", "frozen", "completed"}
TERMINAL_POLICIES = {"PR-ready", "merge"}
RUN_RECORD_FIELDS = {
    "version",
    "run_id",
    "repository",
    "anchor_issue",
    "owner",
    "filter_sha256",
    "count",
    "mode",
    "terminal_policy",
    "merge_authority_comment_id",
    "start_sha",
    "created_at",
}
RUN_TRANSITION_FIELDS = {
    "version",
    "run_id",
    "repository",
    "anchor_issue",
    "owner",
    "filter_sha256",
    "count",
    "terminal_policy",
    "merge_authority_comment_id",
    "start_sha",
    "run_comment_id",
    "predecessor_comment_id",
    "predecessor_sha256",
    "from_mode",
    "mode",
    "created_at",
}
MERGE_AUTHORITY_FIELDS = {
    "version",
    "run_id",
    "repository",
    "anchor_issue",
    "owner",
    "filter_sha256",
    "count",
    "terminal_policy",
    "start_sha",
    "created_at",
}
LINEAGE_FIELDS = {"version", "run_comment", "merge_authority_comment", "events"}
COMMENT_ENVELOPE_FIELDS = {
    "comment_id",
    "repository",
    "issue",
    "author",
    "server_created_at",
    "server_updated_at",
    "body",
}


class LeaseError(ValueError):
    """A lease input or comment violates the stable schema."""


def _require_single_marker_token(text: str, expected: str) -> None:
    tokens = [match.group(1).lower() for match in MARKER_TOKEN_RE.finditer(text)]
    expected_swarm_tokens = 1 if expected.startswith("tether-swarm-") else 0
    if tokens != [expected] or len(SWARM_TOKEN_PREFIX_RE.findall(text)) != expected_swarm_tokens:
        raise LeaseError("coordination comment mixes or repeats marker tokens")


def _now(value: str | None = None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except (ValueError, OverflowError) as exc:
        raise LeaseError("timestamp must be valid ISO-8601") from exc
    if parsed.tzinfo is None:
        raise LeaseError("timestamps must include a UTC offset or Z")
    try:
        return parsed.astimezone(UTC)
    except OverflowError as exc:
        raise LeaseError("timestamp is outside the supported UTC range") from exc


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clock(args: argparse.Namespace) -> datetime:
    value = getattr(args, "now", None)
    if value is not None and os.environ.get(TEST_CLOCK_ENV) != "1":
        raise LeaseError(f"--now requires the isolated {TEST_CLOCK_ENV}=1 test environment")
    return _now(value)


def _expiry(now: datetime, hours: float) -> str:
    try:
        return _iso(now + timedelta(hours=hours))
    except OverflowError as exc:
        raise LeaseError("lease expiry is outside the supported timestamp range") from exc


def _owner(value: str) -> str:
    owner = value.strip().lstrip("@")
    if not OWNER_RE.fullmatch(owner) or "--" in owner:
        raise LeaseError("owner must be a canonical GitHub login")
    return owner


def _repository(value: str) -> str:
    match = REPOSITORY_RE.fullmatch(value)
    if (
        match is None
        or "--" in match.group("owner")
        or match.group("name") in {".", ".."}
    ):
        raise LeaseError("repository must use canonical GitHub owner/name form")
    return value


def _hours(value: float) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value != 4:
        raise LeaseError("lease hours must be exactly 4")
    return value


def _persona(slug: str) -> tuple[str, str]:
    try:
        return PERSONA_BY_SLUG[slug]
    except KeyError as exc:
        choices = ", ".join(sorted(PERSONA_BY_SLUG))
        raise LeaseError(f"unknown persona {slug!r}; choose one of: {choices}") from exc


def _validate_agent_id(agent_id: str, persona_slug: str) -> None:
    expected = re.compile(rf"^codex-{re.escape(persona_slug)}-[0-9a-f]{{8}}$")
    if not expected.fullmatch(agent_id):
        raise LeaseError("agent_id must exactly match its generated persona identity")


def _positive_github_id(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0 or value > MAX_GITHUB_ID:
        raise LeaseError(f"{field} must be a positive signed 64-bit integer")
    return value


def _run_filter(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise LeaseError("run filter must contain 1-512 characters")
    if (
        value != value.strip()
        or len(value.splitlines()) != 1
        or any(ord(character) < 32 for character in value)
    ):
        raise LeaseError("run filter must be canonical single-line text")
    if any(character in value for character in "<>{}"):
        raise LeaseError("run filter contains unsafe marker characters")
    return value


def _text_sha256(value: str) -> str:
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except UnicodeError as exc:
        raise LeaseError("run filter must be valid UTF-8 text") from exc


def _record_sha256(record: dict[str, Any]) -> str:
    try:
        payload = json.dumps(
            record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, UnicodeError) as exc:
        raise LeaseError("coordination record is not canonical UTF-8 JSON") from exc
    return hashlib.sha256(payload).hexdigest()


def _validate_run_record(record: dict[str, Any]) -> None:
    missing = sorted(RUN_RECORD_FIELDS - record.keys())
    if missing:
        raise LeaseError(f"run record is missing fields: {', '.join(missing)}")
    if record.keys() - RUN_RECORD_FIELDS:
        raise LeaseError("run record has unknown fields")
    string_fields = {
        "run_id",
        "repository",
        "owner",
        "filter_sha256",
        "mode",
        "terminal_policy",
        "start_sha",
        "created_at",
    }
    for field in string_fields:
        if not isinstance(record[field], str):
            raise LeaseError(f"run record {field} must be a string")
    if type(record["version"]) is not int or record["version"] != 1:
        raise LeaseError("unsupported run record version")
    if not RUN_RE.fullmatch(record["run_id"]):
        raise LeaseError("invalid run_id")
    _repository(record["repository"])
    if record["owner"] != _owner(record["owner"]):
        raise LeaseError("run record owner must use its canonical GitHub login")
    _positive_github_id(record["anchor_issue"], "anchor_issue")
    if not HASH_RE.fullmatch(record["filter_sha256"]):
        raise LeaseError("filter_sha256 must be a lowercase SHA-256 digest")
    if type(record["count"]) is not int or not 1 <= record["count"] <= 8:
        raise LeaseError("run count must be between 1 and 8")
    if record["mode"] != "running":
        raise LeaseError("run start mode must be running")
    if record["terminal_policy"] not in TERMINAL_POLICIES:
        raise LeaseError("invalid terminal policy")
    authority_id = record["merge_authority_comment_id"]
    if record["terminal_policy"] == "merge":
        _positive_github_id(authority_id, "merge_authority_comment_id")
    elif authority_id is not None:
        raise LeaseError("PR-ready policy must not contain merge authority")
    if not SHA_RE.fullmatch(record["start_sha"]):
        raise LeaseError("start_sha must be a full lowercase object ID")
    created_at = _now(record["created_at"])
    if record["created_at"] != _iso(created_at):
        raise LeaseError("run timestamp must use canonical UTC YYYY-MM-DDTHH:MM:SSZ form")


def _validate_run_transition(record: dict[str, Any]) -> None:
    missing = sorted(RUN_TRANSITION_FIELDS - record.keys())
    if missing:
        raise LeaseError(f"run transition is missing fields: {', '.join(missing)}")
    if record.keys() - RUN_TRANSITION_FIELDS:
        raise LeaseError("run transition has unknown fields")
    string_fields = {
        "run_id",
        "repository",
        "owner",
        "filter_sha256",
        "terminal_policy",
        "start_sha",
        "predecessor_sha256",
        "from_mode",
        "mode",
        "created_at",
    }
    for field in string_fields:
        if not isinstance(record[field], str):
            raise LeaseError(f"run transition {field} must be a string")
    if type(record["version"]) is not int or record["version"] != 1:
        raise LeaseError("unsupported run transition version")
    if not RUN_RE.fullmatch(record["run_id"]):
        raise LeaseError("invalid run_id")
    _repository(record["repository"])
    if record["owner"] != _owner(record["owner"]):
        raise LeaseError("run transition owner must use its canonical GitHub login")
    _positive_github_id(record["anchor_issue"], "anchor_issue")
    if not HASH_RE.fullmatch(record["filter_sha256"]):
        raise LeaseError("filter_sha256 must be a lowercase SHA-256 digest")
    if type(record["count"]) is not int or not 1 <= record["count"] <= 8:
        raise LeaseError("run transition count must be between 1 and 8")
    if record["terminal_policy"] not in TERMINAL_POLICIES:
        raise LeaseError("invalid terminal policy")
    authority_id = record["merge_authority_comment_id"]
    if record["terminal_policy"] == "merge":
        _positive_github_id(authority_id, "merge_authority_comment_id")
    elif authority_id is not None:
        raise LeaseError("PR-ready policy must not contain merge authority")
    if not SHA_RE.fullmatch(record["start_sha"]):
        raise LeaseError("start_sha must be a full lowercase object ID")
    _positive_github_id(record["run_comment_id"], "run_comment_id")
    _positive_github_id(record["predecessor_comment_id"], "predecessor_comment_id")
    if record["predecessor_comment_id"] < record["run_comment_id"]:
        raise LeaseError("run transition predecessor cannot predate the run comment")
    if not HASH_RE.fullmatch(record["predecessor_sha256"]):
        raise LeaseError("predecessor_sha256 must be a lowercase SHA-256 digest")
    if record["from_mode"] not in RUN_MODES or record["mode"] not in RUN_MODES:
        raise LeaseError("invalid run transition mode")
    if record["mode"] not in RUN_TRANSITIONS.get(record["from_mode"], set()):
        raise LeaseError("run transition must make the mode more restrictive")
    created_at = _now(record["created_at"])
    if record["created_at"] != _iso(created_at):
        raise LeaseError("run transition timestamp must use canonical UTC form")


def _validate_merge_authority(record: dict[str, Any]) -> None:
    missing = sorted(MERGE_AUTHORITY_FIELDS - record.keys())
    if missing:
        raise LeaseError(f"merge authority is missing fields: {', '.join(missing)}")
    if record.keys() - MERGE_AUTHORITY_FIELDS:
        raise LeaseError("merge authority has unknown fields")
    string_fields = {
        "run_id",
        "repository",
        "owner",
        "filter_sha256",
        "terminal_policy",
        "start_sha",
        "created_at",
    }
    for field in string_fields:
        if not isinstance(record[field], str):
            raise LeaseError(f"merge authority {field} must be a string")
    if type(record["version"]) is not int or record["version"] != 1:
        raise LeaseError("unsupported merge authority version")
    if not RUN_RE.fullmatch(record["run_id"]):
        raise LeaseError("invalid run_id")
    _repository(record["repository"])
    if record["owner"] != _owner(record["owner"]):
        raise LeaseError("merge authority owner must use its canonical GitHub login")
    _positive_github_id(record["anchor_issue"], "anchor_issue")
    if not HASH_RE.fullmatch(record["filter_sha256"]):
        raise LeaseError("filter_sha256 must be a lowercase SHA-256 digest")
    if type(record["count"]) is not int or not 1 <= record["count"] <= 8:
        raise LeaseError("merge authority count must be between 1 and 8")
    if record["terminal_policy"] != "merge":
        raise LeaseError("merge authority terminal policy must be merge")
    if not SHA_RE.fullmatch(record["start_sha"]):
        raise LeaseError("start_sha must be a full lowercase object ID")
    created_at = _now(record["created_at"])
    if record["created_at"] != _iso(created_at):
        raise LeaseError("merge authority timestamp must use canonical UTC form")


def _require_merge_authority_binding(
    run_record: dict[str, Any], authority: dict[str, Any] | None
) -> None:
    if run_record["terminal_policy"] == "PR-ready":
        if authority is not None:
            raise LeaseError("PR-ready policy must not contain merge authority")
        return
    if authority is None:
        raise LeaseError("merge policy requires its fetched authority comment")
    _validate_merge_authority(authority)
    fields = {
        "run_id",
        "repository",
        "anchor_issue",
        "owner",
        "filter_sha256",
        "count",
        "terminal_policy",
        "start_sha",
    }
    if any(authority[field] != run_record[field] for field in fields):
        raise LeaseError("merge authority does not match the run binding")


def _validate_record(record: dict[str, Any]) -> None:
    missing = sorted(RECORD_FIELDS - record.keys())
    if missing:
        raise LeaseError(f"lease is missing fields: {', '.join(missing)}")
    extra = sorted(record.keys() - RECORD_FIELDS)
    if extra:
        raise LeaseError("lease has unknown fields")
    string_fields = {
        "run_id",
        "repository",
        "owner",
        "agent_id",
        "persona_slug",
        "persona",
        "state",
        "issued_at",
        "expires_at",
        "base_sha",
        "branch",
        "criteria_sha256",
    }
    for field in string_fields:
        if not isinstance(record[field], str):
            raise LeaseError(f"{field} must be a string")
    if type(record["version"]) is not int or record["version"] != 1:
        raise LeaseError("unsupported lease version")
    issue = _positive_github_id(record["issue"], "issue")
    if not RUN_RE.fullmatch(str(record["run_id"])):
        raise LeaseError("invalid run_id")
    _repository(str(record["repository"]))
    if record["owner"] != _owner(str(record["owner"])):
        raise LeaseError("owner must use its canonical GitHub login")
    title, _ = _persona(str(record["persona_slug"]))
    _validate_agent_id(str(record["agent_id"]), str(record["persona_slug"]))
    if record["persona"] != title:
        raise LeaseError("persona title does not match persona_slug")
    if record["state"] not in ALL_STATES:
        raise LeaseError("invalid lease state")
    issued_at = _now(str(record["issued_at"]))
    expires_at = _now(str(record["expires_at"]))
    if record["issued_at"] != _iso(issued_at) or record["expires_at"] != _iso(expires_at):
        raise LeaseError("lease timestamps must use canonical UTC YYYY-MM-DDTHH:MM:SSZ form")
    if type(record["lease_hours"]) not in {int, float}:
        raise LeaseError("lease_hours must be numeric")
    try:
        hours = _hours(float(record["lease_hours"]))
    except OverflowError as exc:
        raise LeaseError("lease_hours must be a finite numeric value") from exc
    if (expires_at - issued_at).total_seconds() != int(hours * 3600):
        raise LeaseError("lease duration does not match lease_hours")
    if not SHA_RE.fullmatch(str(record["base_sha"])):
        raise LeaseError(
            "base_sha must be a full lowercase 40- or 64-character hexadecimal object ID"
        )
    branch = str(record["branch"])
    branch_match = BRANCH_RE.fullmatch(branch)
    if (
        branch_match is None
        or len(branch) > BRANCH_MAX_LENGTH
        or int(branch_match.group("issue")) != issue
    ):
        raise LeaseError("branch must match the documented conventional worker grammar")
    if not HASH_RE.fullmatch(str(record["criteria_sha256"])):
        raise LeaseError("criteria_sha256 must be a lowercase SHA-256 digest")
    _positive_github_id(record["approval_comment_id"], "approval_comment_id")


def _new_run_id(now: datetime) -> str:
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"swarm-{stamp}-{secrets.token_hex(3)}"


def _new_agent_id(slug: str, used: set[str]) -> str:
    while True:
        candidate = f"codex-{slug}-{secrets.token_hex(4)}"
        if candidate not in used:
            used.add(candidate)
            return candidate


def _extract(text: str) -> dict[str, Any]:
    try:
        encoded_length = len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise LeaseError("lease comment is not valid UTF-8 text") from exc
    if encoded_length > 131_072:
        raise LeaseError("lease comment exceeds the safe parse limit")
    matches = LEASE_RE.findall(text)
    if len(matches) != 1:
        raise LeaseError(f"expected one tether-agent-lease block, found {len(matches)}")
    _require_single_marker_token(text, "tether-agent-lease")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LeaseError("lease JSON repeats a field")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise LeaseError(f"lease JSON contains non-finite constant: {value}")

    try:
        record = json.loads(
            matches[0],
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except LeaseError:
        raise
    except (ValueError, RecursionError) as exc:
        raise LeaseError("lease JSON is invalid") from exc
    if not isinstance(record, dict):
        raise LeaseError("lease JSON must be an object")
    _validate_record(record)
    return record


def _extract_run(text: str) -> dict[str, Any]:
    try:
        encoded_length = len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise LeaseError("run comment is not valid UTF-8 text") from exc
    if encoded_length > 131_072:
        raise LeaseError("run comment exceeds the safe parse limit")
    matches = RUN_CONTROL_RE.findall(text)
    if len(matches) != 1:
        raise LeaseError(f"expected one tether-swarm-run block, found {len(matches)}")
    _require_single_marker_token(text, "tether-swarm-run")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LeaseError("run JSON repeats a field")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise LeaseError(f"run JSON contains non-finite constant: {value}")

    try:
        record = json.loads(
            matches[0],
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except LeaseError:
        raise
    except (ValueError, RecursionError) as exc:
        raise LeaseError("run JSON is invalid") from exc
    if not isinstance(record, dict):
        raise LeaseError("run JSON must be an object")
    _validate_run_record(record)
    return record


def _extract_run_transition(text: str) -> dict[str, Any]:
    try:
        encoded_length = len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise LeaseError("run transition is not valid UTF-8 text") from exc
    if encoded_length > 131_072:
        raise LeaseError("run transition exceeds the safe parse limit")
    matches = RUN_TRANSITION_RE.findall(text)
    if len(matches) != 1:
        raise LeaseError(
            f"expected one tether-swarm-run-transition block, found {len(matches)}"
        )
    _require_single_marker_token(text, "tether-swarm-run-transition")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LeaseError("run transition JSON repeats a field")
            result[key] = value
        return result

    try:
        record = json.loads(matches[0], object_pairs_hook=unique_object)
    except LeaseError:
        raise
    except (ValueError, RecursionError) as exc:
        raise LeaseError("run transition JSON is invalid") from exc
    if not isinstance(record, dict):
        raise LeaseError("run transition JSON must be an object")
    _validate_run_transition(record)
    return record


def _extract_merge_authority(text: str) -> dict[str, Any]:
    try:
        encoded_length = len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise LeaseError("merge authority is not valid UTF-8 text") from exc
    if encoded_length > 131_072:
        raise LeaseError("merge authority exceeds the safe parse limit")
    matches = MERGE_AUTHORITY_RE.findall(text)
    if len(matches) != 1:
        raise LeaseError(
            f"expected one tether-swarm-merge-authority block, found {len(matches)}"
        )
    _require_single_marker_token(text, "tether-swarm-merge-authority")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LeaseError("merge authority JSON repeats a field")
            result[key] = value
        return result

    try:
        record = json.loads(matches[0], object_pairs_hook=unique_object)
    except LeaseError:
        raise
    except (ValueError, RecursionError) as exc:
        raise LeaseError("merge authority JSON is invalid") from exc
    if not isinstance(record, dict):
        raise LeaseError("merge authority JSON must be an object")
    _validate_merge_authority(record)
    return record


def _parse_lineage(text: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LeaseError("lineage JSON repeats a field")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise LeaseError(f"lineage JSON contains non-finite constant: {value}")

    try:
        payload = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except LeaseError:
        raise
    except (ValueError, RecursionError) as exc:
        raise LeaseError("lineage JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise LeaseError("lineage JSON must be an object")
    if set(payload) != LINEAGE_FIELDS:
        raise LeaseError("lineage document fields do not match version 1")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise LeaseError("unsupported lineage version")
    if not isinstance(payload["run_comment"], dict):
        raise LeaseError("lineage run_comment must be an object")
    if payload["merge_authority_comment"] is not None and not isinstance(
        payload["merge_authority_comment"], dict
    ):
        raise LeaseError("lineage merge_authority_comment must be an object or null")
    if not isinstance(payload["events"], list):
        raise LeaseError("lineage events must be a list")
    return payload


def _validate_comment_envelope(
    envelope: dict[str, Any], extractor: Any, label: str
) -> tuple[int, dict[str, Any], datetime]:
    if set(envelope) != COMMENT_ENVELOPE_FIELDS:
        raise LeaseError(f"{label} envelope fields do not match version 1")
    comment_id = _positive_github_id(envelope["comment_id"], f"{label} comment_id")
    if not isinstance(envelope["repository"], str):
        raise LeaseError(f"{label} server repository must be a string")
    repository = _repository(envelope["repository"])
    issue = _positive_github_id(envelope["issue"], f"{label} server issue")
    if not isinstance(envelope["author"], str):
        raise LeaseError(f"{label} server author must be a string")
    author = _owner(envelope["author"])
    if not isinstance(envelope["server_created_at"], str) or not isinstance(
        envelope["server_updated_at"], str
    ):
        raise LeaseError(f"{label} server timestamps must be strings")
    server_created = _now(envelope["server_created_at"])
    server_updated = _now(envelope["server_updated_at"])
    if (
        envelope["server_created_at"] != _iso(server_created)
        or envelope["server_updated_at"] != _iso(server_updated)
    ):
        raise LeaseError(f"{label} server timestamps must use canonical UTC form")
    if server_created != server_updated:
        raise LeaseError(f"{label} immutable comment was edited")
    if not isinstance(envelope["body"], str):
        raise LeaseError(f"{label} server body must be a string")
    record = extractor(envelope["body"])
    if repository != record["repository"] or issue != record["anchor_issue"]:
        raise LeaseError(f"{label} server target does not match its record")
    if author != record["owner"]:
        raise LeaseError(f"{label} server author does not match its record")
    if abs((_now(record["created_at"]) - server_created).total_seconds()) > 300:
        raise LeaseError(f"{label} record clock differs from its server timestamp")
    return comment_id, record, server_created


def _resolve_run_lineage(payload: dict[str, Any]) -> dict[str, Any]:
    run_comment_id, run_record, run_server_created = _validate_comment_envelope(
        payload["run_comment"], _extract_run, "run"
    )
    authority_envelope = payload["merge_authority_comment"]
    if authority_envelope is None:
        authority = None
    else:
        authority_id, authority, authority_server_created = _validate_comment_envelope(
            authority_envelope, _extract_merge_authority, "merge authority"
        )
        if authority_id != run_record["merge_authority_comment_id"]:
            raise LeaseError("merge authority server ID does not match the run record")
        if authority_id >= run_comment_id or authority_server_created > run_server_created:
            raise LeaseError("merge authority must be a distinct prior server comment")
    _require_merge_authority_binding(run_record, authority)
    binding_fields = {
        "run_id",
        "repository",
        "anchor_issue",
        "owner",
        "filter_sha256",
        "count",
        "terminal_policy",
        "merge_authority_comment_id",
        "start_sha",
    }
    records: dict[int, dict[str, Any]] = {run_comment_id: run_record}
    children: dict[int, list[int]] = {}
    parsed_events: list[tuple[int, dict[str, Any]]] = []
    for item in payload["events"]:
        if not isinstance(item, dict):
            raise LeaseError("lineage event envelope must be an object")
        comment_id, event, _ = _validate_comment_envelope(
            item, _extract_run_transition, "run transition"
        )
        if comment_id in records or any(existing == comment_id for existing, _ in parsed_events):
            raise LeaseError("lineage repeats a comment ID")
        parsed_events.append((comment_id, event))

    for comment_id, event in sorted(parsed_events):
        predecessor_id = event["predecessor_comment_id"]
        predecessor = records.get(predecessor_id)
        if predecessor is None or comment_id <= predecessor_id:
            raise LeaseError("lineage event has a dangling or non-prior predecessor")
        if event["run_comment_id"] != run_comment_id:
            raise LeaseError("lineage event belongs to a different run comment")
        if any(event[field] != run_record[field] for field in binding_fields):
            raise LeaseError("lineage event does not match the immutable run binding")
        if event["predecessor_sha256"] != _record_sha256(predecessor):
            raise LeaseError("lineage predecessor digest does not match")
        if event["from_mode"] != predecessor["mode"]:
            raise LeaseError("lineage predecessor mode does not match")
        if _now(event["created_at"]) < _now(predecessor["created_at"]):
            raise LeaseError("lineage event predates its predecessor")
        children.setdefault(predecessor_id, []).append(comment_id)
        records[comment_id] = event

    forks = sorted(parent for parent, child_ids in children.items() if len(child_ids) != 1)
    referenced = {child for child_ids in children.values() for child in child_ids}
    leaves = sorted(comment_id for comment_id in records if comment_id not in children)
    if forks or len(leaves) != 1 or referenced != set(records) - {run_comment_id}:
        return {
            "version": 1,
            "mode": "frozen",
            "safe_to_transition": False,
            "reason": "forked-or-ambiguous-lineage",
            "event_count": len(parsed_events),
            "predecessor_comment_id": None,
            "predecessor_sha256": None,
        }
    leaf_id = leaves[0]
    leaf = records[leaf_id]
    return {
        "version": 1,
        "mode": leaf["mode"],
        "safe_to_transition": leaf["mode"] in RUN_TRANSITIONS,
        "reason": "linear-lineage",
        "event_count": len(parsed_events),
        "predecessor_comment_id": leaf_id,
        "predecessor_sha256": _record_sha256(leaf),
    }


def _read(path: str) -> str:
    try:
        if path == "-":
            text = sys.stdin.read(MAX_INPUT_CHARS + 1)
        else:
            with Path(path).open(encoding="utf-8") as stream:
                text = stream.read(MAX_INPUT_CHARS + 1)
    except UnicodeError as exc:
        raise LeaseError("lease input is not valid UTF-8 text") from exc
    except OSError as exc:
        raise LeaseError("lease input could not be read") from exc
    if len(text) > MAX_INPUT_CHARS:
        raise LeaseError("input exceeds the safe read limit")
    return text


def _scope_hash(title: str, body: str) -> str:
    def normalize(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")

    normalized = {
        "body": normalize(body),
        "title": normalize(title),
        "version": 1,
    }
    try:
        payload = json.dumps(
            normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except UnicodeError as exc:
        raise LeaseError("scope title and body must be valid UTF-8 text") from exc
    return hashlib.sha256(payload).hexdigest()


def _render(record: dict[str, Any]) -> str:
    _validate_record(record)
    owner = record["owner"]
    title = record["persona"]
    state = record["state"]
    _, quip = _persona(record["persona_slug"])

    if state == "active":
        heading = f"🛠️ **@{owner} checking in as _{title}_**"
        message = (
            f"{quip} Please keep your pointers off #{record['issue']} while this lease is live."
        )
    elif state == "completed":
        heading = f"✅ **@{owner}'s _{title}_ has landed the change**"
        message = "Tests green, merge confirmed, tools returned to the rack."
    elif state == "released":
        heading = f"🧹 **@{owner}'s _{title}_ has released this issue**"
        message = "No ownership remains; the dispatcher may return it to the ready queue."
    elif state == "handoff":
        heading = f"🧰 **@{owner}'s _{title}_ is packing a handoff**"
        message = "State is preserved. A new worker needs a recorded transfer before touching it."
    else:
        heading = f"🚧 **@{owner}'s _{title}_ is blocked**"
        message = "The lease remains visible while the dispatcher records the blocker."

    visible = [
        heading,
        "",
        message,
        "",
        f"- **Automated Codex worker:** `{record['agent_id']}`",
        f"- **Work item:** `{record['repository']}#{record['issue']}`",
        f"- **Lease state:** `{state}`",
        f"- **Lease ends:** `{record['expires_at']}`",
        f"- **Branch:** `{record['branch']}`",
        f"- **Approved scope:** `{record['criteria_sha256'][:12]}`",
        f"- **Approval comment ID:** `{record['approval_comment_id']}`",
        "",
        "This persona is automated coordination state, not maintainer approval.",
        "If this lease expires, the dispatcher will inspect the work before requeueing it.",
        "",
        "<!-- tether-agent-lease",
        json.dumps(record, indent=2, sort_keys=True),
        "-->",
    ]
    return "\n".join(visible)


def _render_run(record: dict[str, Any]) -> str:
    _validate_run_record(record)
    visible = [
        f"**@{record['owner']}'s automated Codex issue swarm**",
        "",
        f"- **Run:** `{record['run_id']}`",
        f"- **Repository:** `{record['repository']}`",
        f"- **Anchor issue:** `#{record['anchor_issue']}`",
        f"- **Workers:** `{record['count']}`",
        f"- **Mode:** `{record['mode']}`",
        f"- **Terminal policy:** `{record['terminal_policy']}`",
        f"- **Filter digest:** `{record['filter_sha256'][:12]}`",
        f"- **Policy SHA:** `{record['start_sha'][:12]}`",
        "",
        "This immutable start record is automated coordination state, not issue acceptance.",
        "",
        "<!-- tether-swarm-run",
        json.dumps(record, indent=2, sort_keys=True),
        "-->",
    ]
    return "\n".join(visible)


def _render_run_transition(record: dict[str, Any]) -> str:
    _validate_run_transition(record)
    visible = [
        f"**@{record['owner']}'s Codex swarm is now `{record['mode']}`.**",
        "",
        f"- **Run:** `{record['run_id']}`",
        f"- **Previous mode:** `{record['from_mode']}`",
        f"- **Predecessor comment:** `{record['predecessor_comment_id']}`",
        f"- **Predecessor digest:** `{record['predecessor_sha256'][:12]}`",
        "",
        "This append-only event can only make the run more restrictive.",
        "",
        "<!-- tether-swarm-run-transition",
        json.dumps(record, indent=2, sort_keys=True),
        "-->",
    ]
    return "\n".join(visible)


def _render_merge_authority(record: dict[str, Any]) -> str:
    _validate_merge_authority(record)
    visible = [
        f"**@{record['owner']} authorizes merge completion for this Codex swarm run.**",
        "",
        f"- **Run:** `{record['run_id']}`",
        f"- **Repository:** `{record['repository']}`",
        f"- **Anchor issue:** `#{record['anchor_issue']}`",
        f"- **Workers:** `{record['count']}`",
        f"- **Filter digest:** `{record['filter_sha256'][:12]}`",
        f"- **Policy SHA:** `{record['start_sha'][:12]}`",
        "",
        "This authorizes only guarded merges that satisfy the repository review contract.",
        "",
        "<!-- tether-swarm-merge-authority",
        json.dumps(record, indent=2, sort_keys=True),
        "-->",
    ]
    return "\n".join(visible)


def _cmd_merge_authority_comment(args: argparse.Namespace) -> None:
    now = _clock(args)
    record: dict[str, Any] = {
        "version": 1,
        "run_id": args.run_id,
        "repository": args.repository,
        "anchor_issue": args.anchor_issue,
        "owner": _owner(args.owner),
        "filter_sha256": args.filter_sha256.lower(),
        "count": args.count,
        "terminal_policy": "merge",
        "start_sha": args.start_sha.lower(),
        "created_at": _iso(now),
    }
    print(_render_merge_authority(record))


def _cmd_merge_authority_inspect(args: argparse.Namespace) -> None:
    record = _extract_merge_authority(_read(args.file))
    print(json.dumps(record, indent=2, sort_keys=True))


def _cmd_run_comment(args: argparse.Namespace) -> None:
    now = _clock(args)
    query = _run_filter(args.filter)
    authority_id = args.merge_authority_comment_id
    record: dict[str, Any] = {
        "version": 1,
        "run_id": args.run_id,
        "repository": args.repository,
        "anchor_issue": args.anchor_issue,
        "owner": _owner(args.owner),
        "filter_sha256": _text_sha256(query),
        "count": args.count,
        "mode": "running",
        "terminal_policy": args.terminal_policy,
        "merge_authority_comment_id": authority_id,
        "start_sha": args.start_sha.lower(),
        "created_at": _iso(now),
    }
    _validate_run_record(record)
    authority_metadata = (
        args.merge_authority_comment_id,
        args.merge_authority_repository,
        args.merge_authority_issue,
        args.merge_authority_author,
        args.merge_authority_server_created_at,
        args.merge_authority_server_updated_at,
        args.merge_authority_file,
    )
    if any(value is not None for value in authority_metadata) and not all(
        value is not None for value in authority_metadata
    ):
        raise LeaseError("merge authority server metadata is incomplete")
    if args.merge_authority_file is None:
        authority = None
    else:
        _, authority, authority_server_created = _validate_comment_envelope(
            {
                "comment_id": args.merge_authority_comment_id,
                "repository": args.merge_authority_repository,
                "issue": args.merge_authority_issue,
                "author": args.merge_authority_author,
                "server_created_at": args.merge_authority_server_created_at,
                "server_updated_at": args.merge_authority_server_updated_at,
                "body": _read(args.merge_authority_file),
            },
            _extract_merge_authority,
            "merge authority",
        )
        if authority_server_created > now + timedelta(minutes=5):
            raise LeaseError("merge authority server time is after run creation")
    _require_merge_authority_binding(record, authority)
    print(_render_run(record))


def _cmd_run_inspect(args: argparse.Namespace) -> None:
    record = _extract_run(_read(args.file))
    authority = (
        _extract_merge_authority(_read(args.merge_authority_file))
        if args.merge_authority_file is not None
        else None
    )
    _require_merge_authority_binding(record, authority)
    print(json.dumps(record, indent=2, sort_keys=True))


def _cmd_run_transition_inspect(args: argparse.Namespace) -> None:
    record = _extract_run_transition(_read(args.file))
    print(json.dumps(record, indent=2, sort_keys=True))


def _cmd_run_lineage(args: argparse.Namespace) -> None:
    payload = _parse_lineage(_read(args.file))
    print(json.dumps(_resolve_run_lineage(payload), indent=2, sort_keys=True))


def _cmd_run_transition(args: argparse.Namespace) -> None:
    text = _read(args.file)
    run_markers = len(RUN_CONTROL_RE.findall(text))
    transition_markers = len(RUN_TRANSITION_RE.findall(text))
    if (run_markers, transition_markers) == (1, 0):
        predecessor = _extract_run(text)
        predecessor_extractor = _extract_run
        predecessor_label = "run"
        if args.predecessor_comment_id != args.run_comment_id:
            raise LeaseError("the first transition must point to the run comment")
        from_mode = predecessor["mode"]
        binding = predecessor
    elif (run_markers, transition_markers) == (0, 1):
        predecessor = _extract_run_transition(text)
        predecessor_extractor = _extract_run_transition
        predecessor_label = "run transition"
        if predecessor["run_comment_id"] != args.run_comment_id:
            raise LeaseError("predecessor belongs to a different run comment")
        if args.predecessor_comment_id <= args.run_comment_id:
            raise LeaseError("transition predecessor must follow the run comment")
        from_mode = predecessor["mode"]
        binding = predecessor
    else:
        raise LeaseError("expected exactly one run or run-transition predecessor")
    _, predecessor, _ = _validate_comment_envelope(
        {
            "comment_id": args.predecessor_comment_id,
            "repository": args.predecessor_repository,
            "issue": args.predecessor_issue,
            "author": args.predecessor_author,
            "server_created_at": args.predecessor_server_created_at,
            "server_updated_at": args.predecessor_server_updated_at,
            "body": text,
        },
        predecessor_extractor,
        predecessor_label,
    )
    now = _clock(args)
    if args.mode not in RUN_TRANSITIONS.get(from_mode, set()):
        raise LeaseError(f"cannot transition run {from_mode} to {args.mode}")
    if now < _now(predecessor["created_at"]):
        raise LeaseError("run transition clock precedes its predecessor")
    record: dict[str, Any] = {
        "version": 1,
        "run_id": binding["run_id"],
        "repository": binding["repository"],
        "anchor_issue": binding["anchor_issue"],
        "owner": binding["owner"],
        "filter_sha256": binding["filter_sha256"],
        "count": binding["count"],
        "terminal_policy": binding["terminal_policy"],
        "merge_authority_comment_id": binding["merge_authority_comment_id"],
        "start_sha": binding["start_sha"],
        "run_comment_id": args.run_comment_id,
        "predecessor_comment_id": args.predecessor_comment_id,
        "predecessor_sha256": _record_sha256(predecessor),
        "from_mode": from_mode,
        "mode": args.mode,
        "created_at": _iso(now),
    }
    print(_render_run_transition(record))


def _cmd_identities(args: argparse.Namespace) -> None:
    if args.count < 1 or args.count > 8:
        raise LeaseError("count must be between 1 and 8")
    owner = _owner(args.owner)
    now = _clock(args)
    run_id = args.run_id or _new_run_id(now)
    if not RUN_RE.fullmatch(run_id):
        raise LeaseError("run_id must begin with swarm- and use safe characters")
    excluded = set(args.exclude_persona)
    unknown = sorted(excluded - PERSONA_BY_SLUG.keys())
    if unknown:
        raise LeaseError("unknown excluded persona")
    available = [persona for persona in PERSONAS if persona[0] not in excluded]
    if args.count > len(available):
        raise LeaseError("not enough personas remain after exclusions")
    personas = secrets.SystemRandom().sample(available, args.count)
    used: set[str] = set()
    workers = [
        {
            "agent_id": _new_agent_id(slug, used),
            "persona_slug": slug,
            "persona": title,
        }
        for slug, title, _ in personas
    ]
    payload = {
        "version": 1,
        "run_id": run_id,
        "owner": owner,
        "lease_hours": _hours(args.hours),
        "workers": workers,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def _cmd_scope_hash(args: argparse.Namespace) -> None:
    print(_scope_hash(args.title, _read(args.body_file)))


def _cmd_comment(args: argparse.Namespace) -> None:
    owner = _owner(args.owner)
    title, _ = _persona(args.persona_slug)
    _validate_agent_id(args.agent_id, args.persona_slug)
    if not RUN_RE.fullmatch(args.run_id):
        raise LeaseError("invalid run_id")
    now = _clock(args)
    hours = _hours(args.hours)
    record: dict[str, Any] = {
        "version": 1,
        "run_id": args.run_id,
        "repository": args.repository,
        "issue": args.issue,
        "owner": owner,
        "agent_id": args.agent_id,
        "persona_slug": args.persona_slug,
        "persona": title,
        "state": "active",
        "issued_at": _iso(now),
        "expires_at": _expiry(now, hours),
        "lease_hours": hours,
        "base_sha": args.base_sha.lower(),
        "branch": args.branch,
        "criteria_sha256": args.criteria_sha256.lower(),
        "approval_comment_id": args.approval_comment_id,
    }
    print(_render(record))


def _cmd_inspect(args: argparse.Namespace) -> None:
    record = _extract(_read(args.file))
    now = _clock(args)
    expiry = _now(record["expires_at"])
    delta = (expiry - now).total_seconds()
    remaining = math.ceil(delta) if delta > 0 else math.floor(delta)
    result = dict(record)
    result["expired"] = record["state"] == "active" and expiry <= now
    result["seconds_remaining"] = remaining
    print(json.dumps(result, indent=2, sort_keys=True))


def _require_binding(record: dict[str, Any], args: argparse.Namespace, operation: str) -> None:
    expected = {
        "run_id": args.run_id,
        "repository": args.repository,
        "issue": args.issue,
        "owner": _owner(args.owner),
        "agent_id": args.agent_id,
        "base_sha": args.base_sha.lower(),
        "branch": args.branch,
        "criteria_sha256": args.criteria_sha256.lower(),
        "approval_comment_id": args.approval_comment_id,
    }
    for key, value in expected.items():
        if record[key] != value:
            raise LeaseError(f"{operation} {key} does not match canonical lease")


def _cmd_renew(args: argparse.Namespace) -> None:
    record = _extract(_read(args.file))
    now = _clock(args)
    _require_binding(record, args, "renewal")
    if record["state"] != "active":
        raise LeaseError(f"cannot renew a {record['state']} lease")
    if _now(str(record["expires_at"])) <= now:
        raise LeaseError("cannot renew an expired lease; create a recorded handoff")
    hours = _hours(args.hours)
    record["expires_at"] = _expiry(now, hours)
    record["issued_at"] = _iso(now)
    record["lease_hours"] = hours
    print(_render(record))


def _cmd_transition(args: argparse.Namespace) -> None:
    record = _extract(_read(args.file))
    now = _clock(args)
    _require_binding(record, args, "transition")
    transitions = {
        "active": {"blocked", "completed", "handoff", "released"},
        "blocked": {"handoff", "released"},
        "handoff": {"released"},
    }
    if args.state not in transitions.get(str(record["state"]), set()):
        raise LeaseError(f"cannot transition {record['state']} to {args.state}")
    if (
        record["state"] == "active"
        and _now(str(record["expires_at"])) <= now
        and args.state not in {"handoff", "released"}
    ):
        raise LeaseError("an expired active lease may only transition to handoff or released")
    record["state"] = args.state
    print(_render(record))


def _add_binding_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--criteria-sha256", required=True)
    parser.add_argument("--approval-comment-id", type=int, required=True)


def _add_clock_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--now",
        help=f"test-only ISO-8601 clock override; requires {TEST_CLOCK_ENV}=1",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate public-safe issue-swarm coordination comments."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    merge_authority = subparsers.add_parser(
        "merge-authority-comment",
        help="render explicit run-scoped merge authority from the authenticated request",
    )
    merge_authority.add_argument("--run-id", required=True)
    merge_authority.add_argument("--repository", required=True)
    merge_authority.add_argument("--anchor-issue", type=int, required=True)
    merge_authority.add_argument("--owner", default="bioedca")
    merge_authority.add_argument("--filter-sha256", required=True)
    merge_authority.add_argument("--count", type=int, required=True)
    merge_authority.add_argument("--start-sha", required=True)
    _add_clock_argument(merge_authority)
    merge_authority.set_defaults(func=_cmd_merge_authority_comment)

    merge_authority_inspect = subparsers.add_parser(
        "merge-authority-inspect", help="parse a saved merge-authority comment"
    )
    merge_authority_inspect.add_argument(
        "--file", default="-", help="UTF-8 comment file or - for stdin"
    )
    merge_authority_inspect.set_defaults(func=_cmd_merge_authority_inspect)

    run_comment = subparsers.add_parser(
        "run-comment", help="render a new durable swarm-run control comment"
    )
    run_comment.add_argument("--run-id", required=True)
    run_comment.add_argument("--repository", required=True)
    run_comment.add_argument("--anchor-issue", type=int, required=True)
    run_comment.add_argument("--owner", default="bioedca")
    run_comment.add_argument("--filter", required=True)
    run_comment.add_argument("--count", type=int, required=True)
    run_comment.add_argument(
        "--terminal-policy", choices=sorted(TERMINAL_POLICIES), required=True
    )
    run_comment.add_argument("--merge-authority-comment-id", type=int)
    run_comment.add_argument("--merge-authority-repository")
    run_comment.add_argument("--merge-authority-issue", type=int)
    run_comment.add_argument("--merge-authority-author")
    run_comment.add_argument("--merge-authority-server-created-at")
    run_comment.add_argument("--merge-authority-server-updated-at")
    run_comment.add_argument(
        "--merge-authority-file",
        help="fetched authority comment required for merge policy; - reads stdin",
    )
    run_comment.add_argument("--start-sha", required=True)
    _add_clock_argument(run_comment)
    run_comment.set_defaults(func=_cmd_run_comment)

    run_inspect = subparsers.add_parser(
        "run-inspect", help="parse and check a saved swarm-run control comment"
    )
    run_inspect.add_argument("--file", default="-", help="UTF-8 comment file or - for stdin")
    run_inspect.add_argument(
        "--merge-authority-file", help="fetched authority comment required for merge policy"
    )
    run_inspect.set_defaults(func=_cmd_run_inspect)

    run_transition = subparsers.add_parser(
        "run-transition", help="render an append-only, more-restrictive run-mode event"
    )
    run_transition.add_argument(
        "--file", default="-", help="fetched predecessor comment or - for stdin"
    )
    run_transition.add_argument("--run-comment-id", type=int, required=True)
    run_transition.add_argument("--predecessor-comment-id", type=int, required=True)
    run_transition.add_argument("--predecessor-repository", required=True)
    run_transition.add_argument("--predecessor-issue", type=int, required=True)
    run_transition.add_argument("--predecessor-author", required=True)
    run_transition.add_argument("--predecessor-server-created-at", required=True)
    run_transition.add_argument("--predecessor-server-updated-at", required=True)
    run_transition.add_argument(
        "--mode", choices=sorted(RUN_MODES - {"running"}), required=True
    )
    _add_clock_argument(run_transition)
    run_transition.set_defaults(func=_cmd_run_transition)

    run_transition_inspect = subparsers.add_parser(
        "run-transition-inspect", help="parse a saved run-mode event"
    )
    run_transition_inspect.add_argument(
        "--file", default="-", help="UTF-8 comment file or - for stdin"
    )
    run_transition_inspect.set_defaults(func=_cmd_run_transition_inspect)

    run_lineage = subparsers.add_parser(
        "run-lineage", help="resolve a fully fetched run lineage or freeze on forks"
    )
    run_lineage.add_argument(
        "--file", default="-", help="strict lineage JSON document or - for stdin"
    )
    run_lineage.set_defaults(func=_cmd_run_lineage)

    scope_hash = subparsers.add_parser(
        "scope-hash", help="hash a normalized issue title and body for approval"
    )
    scope_hash.add_argument("--title", required=True)
    scope_hash.add_argument("--body-file", default="-", help="UTF-8 issue body or - for stdin")
    scope_hash.set_defaults(func=_cmd_scope_hash)

    identities = subparsers.add_parser("identities", help="generate a run and worker personas")
    identities.add_argument("--count", type=int, required=True)
    identities.add_argument("--owner", default="bioedca")
    identities.add_argument("--hours", type=float, default=4)
    identities.add_argument("--run-id")
    identities.add_argument(
        "--exclude-persona",
        action="append",
        default=[],
        help="persona slug already active; repeat as needed",
    )
    _add_clock_argument(identities)
    identities.set_defaults(func=_cmd_identities)

    comment = subparsers.add_parser("comment", help="render a new active lease comment")
    comment.add_argument("--issue", type=int, required=True)
    comment.add_argument("--repository", required=True)
    comment.add_argument("--owner", default="bioedca")
    comment.add_argument("--run-id", required=True)
    comment.add_argument("--agent-id", required=True)
    comment.add_argument("--persona-slug", required=True)
    comment.add_argument("--base-sha", required=True)
    comment.add_argument("--branch", required=True)
    comment.add_argument("--criteria-sha256", required=True)
    comment.add_argument("--approval-comment-id", type=int, required=True)
    comment.add_argument("--hours", type=float, default=4)
    _add_clock_argument(comment)
    comment.set_defaults(func=_cmd_comment)

    inspect = subparsers.add_parser("inspect", help="parse and check a saved lease comment")
    inspect.add_argument("--file", default="-", help="UTF-8 comment file or - for stdin")
    _add_clock_argument(inspect)
    inspect.set_defaults(func=_cmd_inspect)

    renew = subparsers.add_parser("renew", help="renew a saved lease and render the comment")
    renew.add_argument("--file", default="-", help="UTF-8 comment file or - for stdin")
    _add_binding_arguments(renew)
    renew.add_argument("--hours", type=float, default=4)
    _add_clock_argument(renew)
    renew.set_defaults(func=_cmd_renew)

    transition = subparsers.add_parser("transition", help="change a saved lease state")
    transition.add_argument("--file", default="-", help="UTF-8 comment file or - for stdin")
    _add_binding_arguments(transition)
    transition.add_argument("--state", choices=sorted(ALL_STATES - {"active"}), required=True)
    _add_clock_argument(transition)
    transition.set_defaults(func=_cmd_transition)
    return parser


def main() -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = _parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except LeaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OverflowError, RecursionError):
        print("error: input exceeds safe processing limits", file=sys.stderr)
        return 2
    except OSError:
        print("error: operating-system I/O failure", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
