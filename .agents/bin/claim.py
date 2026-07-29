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

PER_PAGE = 100
MAX_PAGES = 20

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


def _paginate(path: str, what: str) -> list[Any]:
    """Walk every page of a list endpoint.

    ``_request`` discards response headers, so Link-header following is impossible; page until a
    short page arrives instead. Reading only page 1 is not a matter of degree here: a re-approval
    comment past the first 100 would be invisible and the issue reported as *edited after
    approval*, refusing a claim on work that is properly approved.
    """
    joiner = "&" if "?" in path else "?"
    items: list[Any] = []
    for page in range(1, MAX_PAGES + 1):
        status, chunk = _request("GET", f"{path}{joiner}per_page={PER_PAGE}&page={page}")
        if status != 200 or not isinstance(chunk, list):
            raise ClaimError(f"{what} could not be read")
        items += chunk
        if len(chunk) < PER_PAGE:
            return items
    raise ClaimError(f"{what} is larger than this tool will page through")


def _scope_hash(title: str, body: str) -> str:
    """Delegate to the frozen digest in swarm_lease.py rather than reimplementing it.

    Four markers published on live issues depend on that normalization; a second copy of it here
    would be a second thing to keep in step, and the first divergence would be silent.
    """
    helper = Path(__file__).resolve().parents[1] / "skills/run-issue-swarm/scripts/swarm_lease.py"
    # newline="" writes the body's bytes as fetched. Line endings are not the hazard - _scope_hash
    # folds CRLF/CR to LF - but a platform rewrite that alters content (a prepended BOM, say) would
    # change the digest, and the body came from the API rather than from a file to begin with.
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

    comments = _paginate(f"/repos/{REPO}/issues/{number}/comments", f"#{number} comments")
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
    base = f"/repos/{REPO}/activity?ref={ref}"

    # Filter server-side by activity_type. An unfiltered read is newest-first and mixes in every
    # push, so a busy claim branch can push its own branch_creation off the page and make a live
    # holder look reclaimed.
    def ids(kind: str) -> list[int]:
        # Filter server-side AND re-check client-side: the query narrows the page so a busy branch
        # cannot evict what we need, and the re-check means a silently-ignored query parameter
        # degrades to a correct answer rather than a wrong one.
        entries = _paginate(f"{base}&activity_type={kind}", "claim activity")
        return [int(e["id"]) for e in entries if e.get("activity_type") == kind]

    creations = ids("branch_creation")
    if not creations:
        return None
    newest = max(creations)
    if any(deletion > newest for deletion in ids("branch_deletion")):
        return None
    return newest


def _ref_exists(number: int) -> bool:
    """Whether the claim ref exists right now, independent of the activity feed.

    The feed can lag the ref - claim() already treats "201 but no activity record yet" as a real
    state - so ``_generation() is None`` must never be read as "there is nothing to protect".
    """
    status, _ = _request("GET", f"/repos/{REPO}/git/ref/heads/{BRANCH_PREFIX}{number}")
    if status == 200:
        return True
    if status == 404:
        return False
    raise ClaimError(f"claim ref state could not be read (HTTP {status})")


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


def _release_labels(args: argparse.Namespace) -> None:
    """Undo the claim's label mirror. Best-effort: the mirror is never the lock."""
    _request("DELETE", f"/repos/{REPO}/issues/{args.issue}/labels/agent:{args.vendor}", None)
    _request("DELETE", f"/repos/{REPO}/issues/{args.issue}/labels/status:in-progress", None)
    _request("POST", f"/repos/{REPO}/issues/{args.issue}/labels", {"labels": [REQUIRED_LABEL]})


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
    # Distinguish "there is no ref" from "the ref exists but its generation is unreadable".
    # Both used to collapse to None, and release read that as authorization to delete - so a stale
    # worker could delete a live successor's claim and requeue an issue someone was mid-way
    # through. check() reads the same None as fail-closed; the destructive path must not be the
    # permissive one.
    exists = _ref_exists(args.issue)
    current = _generation(args.issue)
    if not exists:
        _release_labels(args)
        print(json.dumps({"version": 1, "issue": args.issue, "released": True, "ref": "absent"}))
        return
    if current is None or current != args.generation:
        held = "unreadable" if current is None else str(current)
        print(
            f"refusing: #{args.issue} claim ref exists at generation {held}, not "
            f"{args.generation}; releasing would delete a successor's claim",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_SUPERSEDED)
    status, _ = _request("DELETE", f"/repos/{REPO}/git/refs/heads/{BRANCH_PREFIX}{args.issue}")
    if status not in (204, 404):
        raise ClaimError(f"claim ref deletion failed with HTTP {status}")
    _release_labels(args)
    print(json.dumps({"version": 1, "issue": args.issue, "released": True, "ref": "deleted"}))


def _next_adr_number() -> int:
    """Highest known ADR number plus one, from BOTH reservations and the committed files.

    Fails closed on a read error. Dropping a failed read used to mean "no ADRs exist", which
    returned 1 - and because the reservation namespace is legitimately empty today, the single
    ``contents`` read is the only source of used numbers. One 403 or 502 was therefore enough to
    hand out 0001, whose compare-and-swap succeeds (no *ref* holds it) while
    ``docs/adr/0001-provenance-first-data-model.md`` has existed since M0. That is precisely the
    duplicate-number collision the reservation scheme exists to prevent, so a read that cannot be
    trusted must stop the reservation rather than silently narrow it.
    """
    reserved = set()
    status, refs = _request("GET", f"/repos/{REPO}/git/matching-refs/{ADR_NAMESPACE}")
    if status != 200 or not isinstance(refs, list):
        raise ClaimError("ADR reservations could not be read; refusing to guess a number")
    for ref in refs:
        match = ADR_REF_RE.match(ref.get("ref", ""))
        if match:
            reserved.add(int(match.group(1)))

    status, entries = _request("GET", f"/repos/{REPO}/contents/docs/adr")
    if status != 200 or not isinstance(entries, list):
        raise ClaimError("the ADR directory could not be read; refusing to guess a number")
    for entry in entries:
        match = ADR_FILE_RE.match(entry.get("name", ""))
        if match:
            reserved.add(int(match.group(1)))

    if not reserved:
        raise ClaimError("no ADRs found at all; refusing to guess a number")
    return max(reserved) + 1


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
