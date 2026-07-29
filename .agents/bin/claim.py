#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Claim an issue by atomically creating its agent ref, and fence writes against a successor.

The mutex is ``POST /git/refs``: it returns ``201`` to the first writer and ``422 Reference already
exists`` to every other. One call, no election, no coordinator, identical for every vendor
(ADR-0057).

Two properties are easy to get wrong and are therefore explicit here.

**Eligibility is a precondition of the claim, not a consequence of it.** The mutex decides *who*
works an issue; it never decides *whether* the issue may be worked. A claim taken on unapproved or
since-edited work is invalid no matter who won the race.

**Liveness and fencing must be server-recorded.** A commit's ``committedDate`` is written by the
client — ``GIT_COMMITTER_DATE`` sets it to anything — so a reaper keying on it can preserve a dead
claim forever or reclaim a live one. The repository activity API stamps ``timestamp`` and assigns a
strictly increasing ``id`` itself, and no request parameter sets either. That ``id`` is the claim's
generation: a reclaim deletes and recreates the ref, so the successor's ``branch_creation`` carries
a greater ``id``, and a superseded worker revalidating before a write is refused.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = os.environ.get("TETHER_REPO", "bioedca/tether")
API = "https://api.github.com"
BRANCH_PREFIX = "agent/issue-"
ADR_NAMESPACE = "adr-reservations"
VENDORS = ("claude", "codex", "copilot")
READY_RE = re.compile(r"<!--\s*tether-agent-ready\s*(\{.*?\})\s*-->", re.DOTALL)
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ADR_REF_RE = re.compile(r"^refs/" + ADR_NAMESPACE + r"/(\d{4})$")
ADR_FILE_RE = re.compile(r"^(\d{4})-")
REQUIRED_LABEL = "status:ready"

EXIT_INELIGIBLE = 3
EXIT_LOST = 4
EXIT_SUPERSEDED = 5


class ClaimError(RuntimeError):
    """A claim precondition failed. The message is safe to print; it carries no path."""


def _token() -> str:
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClaimError("no GitHub token: set GH_TOKEN or run gh auth login") from exc
    return out.stdout.decode("utf-8").strip()


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    """Return (status, parsed-json). HTTP errors are returned, not raised: 422 is an answer."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - fixed https API host
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "tether-claim",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            payload = response.read()
            return response.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            return error.code, json.loads(payload) if payload else None
        except ValueError:
            return error.code, None
    except urllib.error.URLError as exc:
        raise ClaimError("GitHub API is unreachable") from exc


def _scope_hash(title: str, body: str) -> str:
    """Delegate to the frozen digest in swarm_lease.py rather than reimplementing it.

    Four markers published on live issues depend on that normalization; a second copy of it here
    would be a second thing to keep in step, and the first divergence would be silent.
    """
    helper = Path(__file__).resolve().parents[1] / "skills/run-issue-swarm/scripts/swarm_lease.py"
    # newline="" so no platform line-ending rewrite can silently change the digest.
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "body.md"
        target.write_text(body, encoding="utf-8", newline="")
        out = subprocess.run(
            [
                sys.executable,
                str(helper),
                "scope-hash",
                "--title",
                title,
                "--body-file",
                str(target),
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
    if out.returncode != 0:
        raise ClaimError("could not compute the approved-scope digest")
    return out.stdout.decode("utf-8").strip()


def _approval_binds(issue: dict[str, Any], comments: list[dict[str, Any]], owner: str) -> bool:
    """True when a maintainer comment approves the CURRENT title/body snapshot."""
    expected = _scope_hash(issue["title"], issue["body"] or "")
    for comment in comments:
        if (comment.get("user") or {}).get("login") != owner:
            continue
        matches = READY_RE.findall(comment.get("body") or "")
        if len(matches) != 1:
            continue
        try:
            record = json.loads(matches[0])
        except ValueError:
            continue
        digest = record.get("criteria_sha256") if isinstance(record, dict) else None
        if isinstance(digest, str) and HASH_RE.fullmatch(digest) and digest == expected:
            return True
    return False


def _check_eligible(number: int, owner: str) -> dict[str, Any]:
    status, issue = _request("GET", f"/repos/{REPO}/issues/{number}")
    if status != 200 or not isinstance(issue, dict):
        raise ClaimError(f"issue #{number} could not be read")
    if issue.get("pull_request"):
        raise ClaimError(f"#{number} is a pull request, not an issue")
    if issue.get("state") != "open":
        raise ClaimError(f"#{number} is not open")
    labels = {label["name"] for label in issue.get("labels", [])}
    if REQUIRED_LABEL not in labels:
        raise ClaimError(f"#{number} is not {REQUIRED_LABEL}")
    assignees = [a["login"] for a in issue.get("assignees", [])]
    if [a for a in assignees if a != owner]:
        raise ClaimError(f"#{number} is assigned to someone else")

    status, comments = _request("GET", f"/repos/{REPO}/issues/{number}/comments?per_page=100")
    if status != 200 or not isinstance(comments, list):
        raise ClaimError(f"#{number} comments could not be read")
    if not _approval_binds(issue, comments, owner):
        raise ClaimError(
            f"#{number} has no maintainer approval binding its current title and body; "
            "it may have been edited after approval"
        )
    return issue


def _generation(number: int) -> int | None:
    """Server-assigned generation: the newest branch_creation activity id for this claim ref.

    Never derived from commit metadata - see the module docstring.

    A later branch_deletion means the claim is gone and the answer is ``None``. Reading only
    creations would fence **open**: the activity API keeps the historical creation entry after the
    ref is deleted, so a reaped worker would revalidate against its own stale generation and be
    told it still holds a claim that no longer exists. Verified live - after ``DELETE`` on a ref,
    its ``branch_creation`` id is still returned.
    """
    ref = f"refs/heads/{BRANCH_PREFIX}{number}"
    status, entries = _request("GET", f"/repos/{REPO}/activity?ref={ref}&per_page=100")
    if status != 200 or not isinstance(entries, list):
        raise ClaimError("repository activity could not be read")
    creations = [int(e["id"]) for e in entries if e.get("activity_type") == "branch_creation"]
    if not creations:
        return None
    newest = max(creations)
    deletions = [int(e["id"]) for e in entries if e.get("activity_type") == "branch_deletion"]
    if any(deletion > newest for deletion in deletions):
        return None
    return newest


def _default_sha() -> str:
    status, ref = _request("GET", f"/repos/{REPO}/git/ref/heads/main")
    if status != 200 or not isinstance(ref, dict):
        raise ClaimError("default branch head could not be read")
    return ref["object"]["sha"]


def _cmd_agent_id(args: argparse.Namespace) -> None:
    print(f"{args.vendor}-{secrets.token_hex(4)}")


def _cmd_claim(args: argparse.Namespace) -> None:
    number = args.issue
    try:
        _check_eligible(number, args.owner)
    except ClaimError as exc:
        print(f"ineligible: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_INELIGIBLE) from None

    branch = f"{BRANCH_PREFIX}{number}"
    base = args.base or _default_sha()
    status, _ = _request(
        "POST", f"/repos/{REPO}/git/refs", {"ref": f"refs/heads/{branch}", "sha": base}
    )
    if status == 422:
        print(f"lost: {branch} already exists; another agent holds #{number}", file=sys.stderr)
        raise SystemExit(EXIT_LOST)
    if status != 201:
        raise ClaimError(f"claim ref creation failed with HTTP {status}")

    generation = _generation(number)
    if generation is None:
        raise ClaimError("claim ref created but no server activity record appeared")

    # The label is a MIRROR, never the lock. If this write fails the claim is still valid and the
    # next agent still gets 422; the reverse would not be safe, so failure here is not fatal.
    label_ok = True
    for method, path, body in (
        ("POST", f"/repos/{REPO}/issues/{number}/labels", {"labels": [f"agent:{args.vendor}"]}),
        ("DELETE", f"/repos/{REPO}/issues/{number}/labels/{REQUIRED_LABEL}", None),
        ("POST", f"/repos/{REPO}/issues/{number}/labels", {"labels": ["status:in-progress"]}),
    ):
        code, _ = _request(method, path, body)
        if code not in (200, 201):
            label_ok = False

    print(
        json.dumps(
            {
                "version": 1,
                "issue": number,
                "branch": branch,
                "base_sha": base,
                "generation": generation,
                "vendor": args.vendor,
                "label_mirror": label_ok,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _cmd_check(args: argparse.Namespace) -> None:
    current = _generation(args.issue)
    if current is None:
        print(f"superseded: no claim ref for #{args.issue}", file=sys.stderr)
        raise SystemExit(EXIT_SUPERSEDED)
    if current != args.generation:
        print(
            f"superseded: generation {args.generation} was reclaimed; current is {current}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_SUPERSEDED)
    print(json.dumps({"version": 1, "issue": args.issue, "generation": current, "held": True}))


def _cmd_release(args: argparse.Namespace) -> None:
    current = _generation(args.issue)
    if current is not None and current != args.generation:
        print(
            f"refusing: #{args.issue} was reclaimed at generation {current}; "
            f"releasing would delete a successor's claim",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_SUPERSEDED)
    status, _ = _request("DELETE", f"/repos/{REPO}/git/refs/heads/{BRANCH_PREFIX}{args.issue}")
    if status not in (204, 404, 422):
        raise ClaimError(f"claim ref deletion failed with HTTP {status}")
    _request("DELETE", f"/repos/{REPO}/issues/{args.issue}/labels/agent:{args.vendor}", None)
    _request("DELETE", f"/repos/{REPO}/issues/{args.issue}/labels/status:in-progress", None)
    _request("POST", f"/repos/{REPO}/issues/{args.issue}/labels", {"labels": [REQUIRED_LABEL]})
    print(json.dumps({"version": 1, "issue": args.issue, "released": True}))


def _next_adr_number() -> int:
    status, refs = _request("GET", f"/repos/{REPO}/git/matching-refs/{ADR_NAMESPACE}")
    reserved = set()
    if status == 200 and isinstance(refs, list):
        for ref in refs:
            match = ADR_REF_RE.match(ref.get("ref", ""))
            if match:
                reserved.add(int(match.group(1)))
    status, entries = _request("GET", f"/repos/{REPO}/contents/docs/adr")
    if status == 200 and isinstance(entries, list):
        for entry in entries:
            match = ADR_FILE_RE.match(entry.get("name", ""))
            if match:
                reserved.add(int(match.group(1)))
    return max(reserved) + 1 if reserved else 1


def _cmd_reserve_adr(args: argparse.Namespace) -> None:
    base = _default_sha()
    candidate = _next_adr_number()
    for _ in range(args.attempts):
        # Deliberately NOT refs/tags/: hatch-vcs derives the package version from tags, so a
        # non-version tag makes `pip install -e .` fail and turns main red. A custom namespace is
        # the same compare-and-swap on creation, and invisible to every tag consumer.
        status, _ = _request(
            "POST",
            f"/repos/{REPO}/git/refs",
            {"ref": f"refs/{ADR_NAMESPACE}/{candidate:04d}", "sha": base},
        )
        if status == 201:
            print(json.dumps({"version": 1, "adr": f"{candidate:04d}", "reserved": True}))
            return
        if status != 422:
            raise ClaimError(f"ADR reservation failed with HTTP {status}")
        candidate += 1
    raise ClaimError(f"could not reserve an ADR number in {args.attempts} attempts")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claim an issue by atomic ref creation.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    agent_id = subparsers.add_parser("agent-id", help="print a public-safe worker identity")
    agent_id.add_argument("--vendor", choices=VENDORS, required=True)
    agent_id.set_defaults(func=_cmd_agent_id)

    claim = subparsers.add_parser("claim", help="check eligibility, then take the mutex")
    claim.add_argument("--issue", type=int, required=True)
    claim.add_argument("--vendor", choices=VENDORS, required=True)
    claim.add_argument("--owner", default="bioedca", help="login whose approval counts")
    claim.add_argument("--base", help="base SHA; defaults to the current default-branch head")
    claim.set_defaults(func=_cmd_claim)

    check = subparsers.add_parser("check", help="revalidate a claim before an authoritative write")
    check.add_argument("--issue", type=int, required=True)
    check.add_argument("--generation", type=int, required=True)
    check.set_defaults(func=_cmd_check)

    release = subparsers.add_parser("release", help="delete the claim ref and requeue the issue")
    release.add_argument("--issue", type=int, required=True)
    release.add_argument("--generation", type=int, required=True)
    release.add_argument("--vendor", choices=VENDORS, required=True)
    release.set_defaults(func=_cmd_release)

    reserve = subparsers.add_parser("reserve-adr", help="atomically reserve the next ADR number")
    reserve.add_argument("--attempts", type=int, default=16)
    reserve.set_defaults(func=_cmd_reserve_adr)
    return parser


def main() -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = _parser().parse_args()
    try:
        args.func(args)
    except ClaimError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OverflowError, RecursionError, AttributeError):
        print("error: input exceeds safe processing limits", file=sys.stderr)
        return 2
    except OSError:
        print("error: operating-system I/O failure", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
