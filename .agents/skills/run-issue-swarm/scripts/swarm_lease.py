#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compute and verify the scope digest that binds a maintainer's swarm approval.

ADR-0057 decided that a claim becomes an atomic git ref, retiring the lease, TTL, heartbeat and
coordinator election this file used to implement. That decision is *recorded but not yet
implemented* — see the ADR's "Adoption status" — so this module no longer ships the lease tooling,
and nothing has replaced it yet. Do not infer a claim mechanism from this file's absence of one;
root ``AGENTS.md`` is the contract in the interim.

What is unaffected, and all that remains here, is the approval binding: a maintainer comment
carrying the SHA-256 of the exact issue snapshot they approved, so a later edit to the title or
body provably invalidates it.

The digest is a published contract: markers already posted on #188, #189, #216 and #218 must keep
verifying, so ``_scope_hash`` normalization is frozen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

READY_RE = re.compile(r"<!--\s*tether-agent-ready\s*(\{.*?\})\s*-->", re.DOTALL)
# The live marker plus the retired lease/run markers. Retired names stay listed so a comment
# carrying both an approval and stale coordination state is refused rather than half-read.
# Longest-first: tether-swarm-run would otherwise shadow tether-swarm-run-transition.
MARKER_TOKEN_RE = re.compile(
    r"<!--\s*(tether-swarm-merge-authority|tether-swarm-run-transition|tether-swarm-run|"
    r"tether-agent-ready|tether-agent-lease)\b",
    re.IGNORECASE,
)
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
READY_FIELDS = {"version", "criteria_sha256"}
MAX_GITHUB_ID = 2**63 - 1
MAX_INPUT_BYTES = 131_072


class LeaseError(ValueError):
    """An approval input or comment violates the stable schema."""


def _require_single_marker_token(text: str, expected: str) -> None:
    """Reject a comment that mixes or repeats coordination marker tokens.

    One comment carries one machine-readable claim. A comment holding two markers is ambiguous
    about which one the maintainer meant, so it is refused rather than resolved. Only the
    enumerated coordination markers count — a prose or fenced mention of an unrelated anchor
    such as ``tether-grooming-v1`` is not a claim and must not trip this.
    """
    tokens = [match.group(1).lower() for match in MARKER_TOKEN_RE.finditer(text)]
    if tokens != [expected]:
        raise LeaseError("coordination comment mixes or repeats marker tokens")


def _positive_github_id(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0 or value > MAX_GITHUB_ID:
        raise LeaseError(f"{field} must be a positive signed 64-bit integer")
    return value


def _read(path: str) -> str:
    """Read at most ``MAX_INPUT_BYTES`` from a file or stdin, never reflecting the path."""
    try:
        if path == "-":
            payload = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        else:
            with Path(path).open("rb") as stream:
                payload = stream.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise LeaseError("approval input could not be read") from exc
    if len(payload) > MAX_INPUT_BYTES:
        raise LeaseError("input exceeds the safe read limit")
    try:
        return payload.decode("utf-8")
    except UnicodeError as exc:
        raise LeaseError("approval input is not valid UTF-8 text") from exc


def _scope_hash(title: str, body: str) -> str:
    """Digest the normalized issue snapshot an approval binds to.

    Frozen: CRLF and lone CR become LF, trailing newlines are stripped, and the result is hashed
    as compact JSON with sorted keys. Changing any of this silently invalidates every published
    marker, so a pinned digest covers it.
    """

    def normalize(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")

    normalized = {"body": normalize(body), "title": normalize(title), "version": 1}
    try:
        payload = json.dumps(
            normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except UnicodeError as exc:
        raise LeaseError("scope title and body must be valid UTF-8 text") from exc
    return hashlib.sha256(payload).hexdigest()


def _loads(text: str, label: str) -> Any:
    """Parse untrusted JSON without letting it repeat itself back into the logs."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LeaseError(f"{label} JSON repeats a field")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise LeaseError(f"{label} JSON contains a non-finite constant")

    try:
        return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except LeaseError:
        raise
    except (ValueError, RecursionError) as exc:
        raise LeaseError(f"{label} JSON is invalid") from exc


def _validate_ready(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise LeaseError("approval JSON must be an object")
    missing = sorted(READY_FIELDS - record.keys())
    if missing:
        raise LeaseError(f"approval is missing fields: {', '.join(missing)}")
    if record.keys() - READY_FIELDS:
        raise LeaseError("approval has unknown fields")
    if type(record["version"]) is not int or record["version"] != 1:
        raise LeaseError("unsupported approval version")
    digest = record["criteria_sha256"]
    if not isinstance(digest, str) or not HASH_RE.fullmatch(digest):
        raise LeaseError("criteria_sha256 must be a lowercase SHA-256 digest")
    return record


def _extract_ready(text: str) -> dict[str, Any]:
    try:
        encoded_length = len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise LeaseError("approval comment is not valid UTF-8 text") from exc
    if encoded_length > MAX_INPUT_BYTES:
        raise LeaseError("approval comment exceeds the safe parse limit")
    matches = READY_RE.findall(text)
    if len(matches) != 1:
        raise LeaseError(f"expected one tether-agent-ready block, found {len(matches)}")
    _require_single_marker_token(text, "tether-agent-ready")
    return _validate_ready(_loads(matches[0], "approval"))


def _render_marker(digest: str) -> str:
    # Emit "version" first, matching every marker already published on a live issue and the form
    # documented in SKILL.md. Field order is not semantic - the parser is order-agnostic - but a
    # rendered marker that does not look like the existing corpus invites a needless diff.
    record = _validate_ready({"version": 1, "criteria_sha256": digest})
    payload = json.dumps(record, separators=(",", ":"))
    return f"<!-- tether-agent-ready {payload} -->"


def _cmd_scope_hash(args: argparse.Namespace) -> None:
    print(_scope_hash(args.title, _read(args.body_file)))


def _cmd_ready_marker(args: argparse.Namespace) -> None:
    print(_render_marker(_scope_hash(args.title, _read(args.body_file))))


def _cmd_ready_inspect(args: argparse.Namespace) -> None:
    record = dict(_extract_ready(_read(args.file)))
    if args.comment_id is not None:
        record["comment_id"] = _positive_github_id(args.comment_id, "comment_id")
    print(json.dumps(record, indent=2, sort_keys=True))


def _cmd_verify(args: argparse.Namespace) -> None:
    approval = _extract_ready(_read(args.approval_file))
    expected = _scope_hash(args.title, _read(args.body_file))
    if approval["criteria_sha256"] != expected:
        raise LeaseError(
            "approval does not bind the current snapshot; the title or body changed after it "
            "was posted, so a fresh approval is required"
        )
    print(json.dumps({"binds": True, "criteria_sha256": expected, "version": 1}, sort_keys=True))


def _add_snapshot_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--title", required=True)
    parser.add_argument("--body-file", default="-", help="UTF-8 issue body or - for stdin")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute and verify the scope digest binding a swarm approval comment."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scope_hash = subparsers.add_parser(
        "scope-hash", help="hash a normalized issue title and body for approval"
    )
    _add_snapshot_arguments(scope_hash)
    scope_hash.set_defaults(func=_cmd_scope_hash)

    ready_marker = subparsers.add_parser(
        "ready-marker", help="render the approval marker for an issue snapshot"
    )
    _add_snapshot_arguments(ready_marker)
    ready_marker.set_defaults(func=_cmd_ready_marker)

    ready_inspect = subparsers.add_parser(
        "ready-inspect", help="parse and check a fetched approval comment"
    )
    ready_inspect.add_argument("--file", default="-", help="UTF-8 comment file or - for stdin")
    ready_inspect.add_argument("--comment-id", type=int, help="server comment ID to echo back")
    ready_inspect.set_defaults(func=_cmd_ready_inspect)

    verify = subparsers.add_parser(
        "verify", help="check a fetched approval still binds the current issue snapshot"
    )
    verify.add_argument("--title", required=True)
    # Required, unlike the single-input commands: verify reads two files, so defaulting either to
    # stdin is ambiguous. Silently digesting an empty body would report a valid approval as stale -
    # a confident, wrong answer on the gate that decides whether work may proceed.
    verify.add_argument("--body-file", required=True, help="UTF-8 issue body or - for stdin")
    verify.add_argument("--approval-file", required=True, help="fetched approval comment")
    verify.set_defaults(func=_cmd_verify)
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
