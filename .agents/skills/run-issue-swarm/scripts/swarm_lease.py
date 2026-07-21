#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate and inspect public-safe issue-swarm identities and lease comments."""

from __future__ import annotations

import argparse
import hashlib
import json
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
AGENT_RE = re.compile(r"^codex-[a-z0-9-]+-[0-9a-f]{8}$")
SHA_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
RUN_RE = re.compile(r"^swarm-[a-zA-Z0-9._-]+$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ACTIVE_STATES = {"active"}
TERMINAL_STATES = {"completed", "released"}
ALL_STATES = ACTIVE_STATES | TERMINAL_STATES | {"blocked", "handoff"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class LeaseError(ValueError):
    """A lease input or comment violates the stable schema."""


def _now(value: str | None = None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise LeaseError("timestamps must include a UTC offset or Z")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _owner(value: str) -> str:
    owner = value.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", owner):
        raise LeaseError("owner must be a GitHub login without spaces")
    return owner


def _hours(value: float) -> float:
    if value <= 0 or value > 24:
        raise LeaseError("lease hours must be greater than 0 and at most 24")
    return value


def _persona(slug: str) -> tuple[str, str]:
    try:
        return PERSONA_BY_SLUG[slug]
    except KeyError as exc:
        choices = ", ".join(sorted(PERSONA_BY_SLUG))
        raise LeaseError(f"unknown persona {slug!r}; choose one of: {choices}") from exc


def _validate_record(record: dict[str, Any]) -> None:
    required = {
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
    missing = sorted(required - record.keys())
    if missing:
        raise LeaseError(f"lease is missing fields: {', '.join(missing)}")
    if record["version"] != 1:
        raise LeaseError("unsupported lease version")
    if not isinstance(record["issue"], int) or record["issue"] <= 0:
        raise LeaseError("issue must be a positive integer")
    if not RUN_RE.fullmatch(str(record["run_id"])):
        raise LeaseError("invalid run_id")
    if not REPOSITORY_RE.fullmatch(str(record["repository"])):
        raise LeaseError("repository must use owner/name form")
    _owner(str(record["owner"]))
    if not AGENT_RE.fullmatch(str(record["agent_id"])):
        raise LeaseError("invalid agent_id")
    title, _ = _persona(str(record["persona_slug"]))
    if not str(record["agent_id"]).startswith(f"codex-{record['persona_slug']}-"):
        raise LeaseError("agent_id does not match persona_slug")
    if record["persona"] != title:
        raise LeaseError("persona title does not match persona_slug")
    if record["state"] not in ALL_STATES:
        raise LeaseError("invalid lease state")
    issued_at = _now(str(record["issued_at"]))
    expires_at = _now(str(record["expires_at"]))
    try:
        _hours(float(record["lease_hours"]))
    except (TypeError, ValueError) as exc:
        raise LeaseError("lease_hours must be numeric") from exc
    if (expires_at - issued_at).total_seconds() != float(record["lease_hours"]) * 3600:
        raise LeaseError("lease duration does not match lease_hours")
    if not SHA_RE.fullmatch(str(record["base_sha"])):
        raise LeaseError("base_sha must be a full 40- or 64-character hexadecimal object ID")
    branch = str(record["branch"])
    if not branch or any(char.isspace() for char in branch):
        raise LeaseError("branch must be non-empty and contain no whitespace")
    if not HASH_RE.fullmatch(str(record["criteria_sha256"])):
        raise LeaseError("criteria_sha256 must be a lowercase SHA-256 digest")
    if not isinstance(record["approval_comment_id"], int) or record["approval_comment_id"] <= 0:
        raise LeaseError("approval_comment_id must be a positive integer")


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
    matches = LEASE_RE.findall(text)
    if len(matches) != 1:
        raise LeaseError(f"expected one tether-agent-lease block, found {len(matches)}")
    try:
        record = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise LeaseError(f"lease JSON is invalid: {exc}") from exc
    if not isinstance(record, dict):
        raise LeaseError("lease JSON must be an object")
    _validate_record(record)
    return record


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _scope_hash(title: str, body: str) -> str:
    def normalize(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")

    normalized = {
        "body": normalize(body),
        "title": normalize(title),
        "version": 1,
    }
    payload = json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
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


def _cmd_identities(args: argparse.Namespace) -> None:
    if args.count < 1 or args.count > 8:
        raise LeaseError("count must be between 1 and 8")
    owner = _owner(args.owner)
    now = _now(args.now)
    run_id = args.run_id or _new_run_id(now)
    if not RUN_RE.fullmatch(run_id):
        raise LeaseError("run_id must begin with swarm- and use safe characters")
    excluded = set(args.exclude_persona)
    unknown = sorted(excluded - PERSONA_BY_SLUG.keys())
    if unknown:
        raise LeaseError(f"unknown excluded persona: {', '.join(unknown)}")
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
    if not AGENT_RE.fullmatch(args.agent_id):
        raise LeaseError("agent_id must come from the identities command")
    if not args.agent_id.startswith(f"codex-{args.persona_slug}-"):
        raise LeaseError("agent_id does not match persona_slug")
    if not RUN_RE.fullmatch(args.run_id):
        raise LeaseError("invalid run_id")
    now = _now(args.now)
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
        "expires_at": _iso(now + timedelta(hours=hours)),
        "lease_hours": hours,
        "base_sha": args.base_sha.lower(),
        "branch": args.branch,
        "criteria_sha256": args.criteria_sha256.lower(),
        "approval_comment_id": args.approval_comment_id,
    }
    print(_render(record))


def _cmd_inspect(args: argparse.Namespace) -> None:
    record = _extract(_read(args.file))
    now = _now(args.now)
    expiry = _now(record["expires_at"])
    remaining = int((expiry - now).total_seconds())
    result = dict(record)
    result["expired"] = record["state"] == "active" and remaining <= 0
    result["seconds_remaining"] = remaining
    print(json.dumps(result, indent=2, sort_keys=True))


def _cmd_renew(args: argparse.Namespace) -> None:
    record = _extract(_read(args.file))
    now = _now(args.now)
    expected = {
        "run_id": args.run_id,
        "repository": args.repository,
        "agent_id": args.agent_id,
        "owner": _owner(args.owner),
        "issue": args.issue,
    }
    for key, value in expected.items():
        if record[key] != value:
            raise LeaseError(f"renewal {key} does not match canonical lease")
    if record["state"] != "active":
        raise LeaseError(f"cannot renew a {record['state']} lease")
    if _now(str(record["expires_at"])) <= now:
        raise LeaseError("cannot renew an expired lease; create a recorded handoff")
    hours = _hours(args.hours)
    record["expires_at"] = _iso(now + timedelta(hours=hours))
    record["issued_at"] = _iso(now)
    record["lease_hours"] = hours
    print(_render(record))


def _cmd_transition(args: argparse.Namespace) -> None:
    record = _extract(_read(args.file))
    transitions = {
        "active": {"blocked", "completed", "handoff", "released"},
        "blocked": {"handoff", "released"},
        "handoff": {"released"},
    }
    if args.state not in transitions.get(str(record["state"]), set()):
        raise LeaseError(f"cannot transition {record['state']} to {args.state}")
    record["state"] = args.state
    print(_render(record))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate public-safe issue-swarm personas and lease comments."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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
    identities.add_argument("--now", help="test-only ISO-8601 clock override")
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
    comment.add_argument("--now", help="test-only ISO-8601 clock override")
    comment.set_defaults(func=_cmd_comment)

    inspect = subparsers.add_parser("inspect", help="parse and check a saved lease comment")
    inspect.add_argument("--file", default="-", help="UTF-8 comment file or - for stdin")
    inspect.add_argument("--now", help="test-only ISO-8601 clock override")
    inspect.set_defaults(func=_cmd_inspect)

    renew = subparsers.add_parser("renew", help="renew a saved lease and render the comment")
    renew.add_argument("--file", default="-", help="UTF-8 comment file or - for stdin")
    renew.add_argument("--run-id", required=True)
    renew.add_argument("--repository", required=True)
    renew.add_argument("--agent-id", required=True)
    renew.add_argument("--owner", required=True)
    renew.add_argument("--issue", type=int, required=True)
    renew.add_argument("--hours", type=float, default=4)
    renew.add_argument("--now", help="test-only ISO-8601 clock override")
    renew.set_defaults(func=_cmd_renew)

    transition = subparsers.add_parser("transition", help="change a saved lease state")
    transition.add_argument("--file", default="-", help="UTF-8 comment file or - for stdin")
    transition.add_argument("--state", choices=sorted(ALL_STATES - {"active"}), required=True)
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
    except (LeaseError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
