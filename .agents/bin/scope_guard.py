#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the diff budget nothing currently measures, and report - never block.

Every ``size:*`` label carries a budget in its description and nothing checks it. That budget is the
control that would have caught #218 at **26x over** ``size:S`` before it became #239's +3,754/-229.

**Advisory by design, and it must stay that way for now.** The thresholds are visibly miscalibrated:
measured at their merged heads, every prose-only PR in the swarm rebuild passes its budget and every
PR introducing a new executable exceeds even ``size:L``, the largest rung that exists (#275 2.30x,
#276 3.62x, #285 1.30x, #287 1.75x). That pattern is a missing rung rather than six undisciplined
PRs, and promoting an untested threshold to a required check relocates the failure instead of
removing it. So this reports, and the ladder changes as a deliberate decision with the evidence
attached.

Two definitions are **choices**, not gaps being filled, and both are recorded here because two
people otherwise compute different answers from one rule:

* **The budget counts ADDED lines.** The plan's only worked example implies deletions were counted
  (3,983/150 = 26.6 with, 3,754/150 = 25.0 without), and counting them is wrong for this repository
  because it penalises removal work. #280 is the proof: 385 added against 834 deleted, the largest
  net simplification in the rebuild, passes on added lines and scores **3.0x over** the same budget
  once deletions count.
* **The proportional-test rule's operands are diff additions**, not resulting file sizes. Both
  readings were already in use - #269 is "303 against a cap of 516" read as file sizes and "188 vs
  362" read as additions - so the ambiguity was real and had to be settled rather than inherited.

Lockfiles and generated files are excluded, matching what the label descriptions already say.
"""

from __future__ import annotations

import argparse
import ast
import base64
import fnmatch
import hashlib
import importlib.util
import json
import re
import sys
import tomllib
import urllib.parse
from pathlib import Path
from typing import Any

try:  # PyYAML is a mkdocs dependency in the base lock, but not present for a bare interpreter.
    import yaml

    _YAML_ERRORS: tuple[type[Exception], ...] = (yaml.YAMLError,)

    class _Yaml12Loader(yaml.SafeLoader):
        """A loader with YAML **1.2** booleans, because these files are GitHub Actions workflows.

        PyYAML implements YAML 1.1, where ``on``, ``off``, ``yes`` and ``no`` all resolve to
        booleans - which is why `tests/test_triage.py` reaches a workflow's trigger block through
        the key ``True`` rather than ``"on"``. For a materiality digest that coercion is not a
        curiosity, it is a collapse: an unquoted `with:` input edited from ``on`` to ``yes`` sends a
        *different string* to the action while both canonicalize to JSON ``true``, so the push
        digests as unchanged and stale review evidence survives a real change.

        The whitespace substitution this replaced had the same failure with a different cause, and
        replacing one silent collapse with another would have been no improvement.
        """

    _BOOL_12 = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
    _Yaml12Loader.yaml_implicit_resolvers = {
        first: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    for _first in "tTfF":
        _Yaml12Loader.yaml_implicit_resolvers.setdefault(_first, []).insert(
            0, ("tag:yaml.org,2002:bool", _BOOL_12)
        )
except ImportError:  # pragma: no cover - exercised by the workflow's minimal environment
    yaml = None
    _Yaml12Loader = None
    _YAML_ERRORS = ()

BIN = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("tether_claim", BIN / "claim.py")
if _spec is None or _spec.loader is None:  # pragma: no cover - packaging accident
    raise SystemExit("error: claim.py is missing next to scope_guard.py")
claim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(claim)

REPO = claim.REPO

# The values in the `size:*` label descriptions, in added lines. `size:L` is the largest rung that
# exists - there is no `size:XL` - so anything above 900 has no bucket at all and is reported as
# such.
BUDGETS = {"size:XS": 50, "size:S": 150, "size:M": 400, "size:L": 900}

# "excl. lockfiles/generated", quoting the label descriptions. A re-solved lock is thousands of
# lines of machine output and says nothing about how much a human has to review.
EXCLUDED_FROM_BUDGET = (
    "conda-lock.yml",
    "sidecar/conda-lock.yml",
    "deep/conda-lock.yml",
    "packaging/locks/*",
    "src/tether/_version.py",
    "uv.lock",
)

TEST_FILE_LINE_CAP = 400
PROPORTIONAL_FLOOR = 80

# The banned-test-category heuristic flags a test that pins governance PROSE, which is the retired
# prose-drift guard (`tests/test_review_policy.py`, deleted in #260) coming back under a new name.
#
# It is a heuristic, so it needs an allowlist and each entry needs a reason. Asserting on the
# CONTENT of executable or configuration files is not prose-pinning - a parsed `permissions:`
# mapping or the absence of a `Remove-Item` in a reclaim path is an assertion about code that
# happens not to be Python - so those are not flagged in the first place.
PROSE_GUARD_ALLOWLIST = {
    # A real PRD §12.7 / §9-M9 gate: every ADR is indexed and every cross-link resolves. It reads
    # docs/adr/*.md, but it asserts on STRUCTURE (links, completeness), never on wording.
    "tests/test_adr_index.py",
    # The suite for THIS heuristic. Its added lines carry the detected shape as *fixture strings* -
    # `'+    assert "two rounds" in Path("AGENTS.md").read_text()'` is the input being classified,
    # not a read this suite performs. Found by running the guard against its own PR, where it was
    # the tool's first live finding and it was wrong. A detector that cannot be tested without
    # accusing its own tests is not worth keeping, so the entry goes here rather than into a more
    # elaborate rule that would have to tell a quoted diff line from a real one.
    "tests/test_scope_guard.py",
}
GOVERNANCE_PROSE = ("AGENTS.md", "CONTRIBUTING.md", "docs/PRD.md", "docs/adr/*.md")

EXIT_OVER_BUDGET = 3


class GuardError(RuntimeError):
    """A measurement precondition failed. Safe to print; carries no local path."""


def _excluded(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in EXCLUDED_FROM_BUDGET)


def _is_test(path: str) -> bool:
    return path.startswith("tests/") and path.endswith(".py")


def _pull_request(number: int) -> dict[str, Any]:
    status, pr = claim._request("GET", f"/repos/{REPO}/pulls/{number}")
    if status != 200 or not isinstance(pr, dict):
        raise GuardError(f"PR #{number} could not be read (HTTP {status})")
    return pr


def _files(number: int) -> list[dict[str, Any]]:
    """Every file in the PR diff.

    Paginated. A truncated read would under-report the budget, which is the fail-open direction for
    a measurement whose whole purpose is to notice something being too large.

    """
    return claim._paginate(f"/repos/{REPO}/pulls/{number}/files", f"PR #{number} file list")


# The compare endpoint returns at most this many files and says so in its own response rather than
# paginating them. Silently measuring a truncated list is the fail-open direction, so it is refused.
MAX_COMPARE_FILES = 300


def _compare_files(base: str, head: str) -> list[dict[str, Any]]:
    """The diff of ``head`` against an arbitrary ``base``, in the PR-file-list shape.

    This is what makes ``--base`` a *measurement* rather than an annotation. #216 resumes from the
    `fe74ae4` checkpoint, which is already +259/-20 against an XS budget of 50: a PR opened from
    there carries those lines in its own file list, so measuring it against `main` charges the
    claimant for work they inherited. Comparing checkpoint-to-head counts only what they added.

    """
    status, payload = claim._request("GET", f"/repos/{REPO}/compare/{base}...{head}")
    if status != 200 or not isinstance(payload, dict):
        raise GuardError(f"{base}...{head} could not be compared (HTTP {status})")
    files = payload.get("files")
    if not isinstance(files, list):
        raise GuardError(f"{base}...{head} returned no file list")
    if len(files) >= MAX_COMPARE_FILES:
        raise GuardError(
            f"{base}...{head} touches at least {MAX_COMPARE_FILES} files, the compare endpoint's "
            "cap; the measurement would be truncated and read as smaller than it is"
        )
    return files


def _labels(pr: dict[str, Any], issue: dict[str, Any] | None) -> set[str]:
    """Labels from the PR and its linked issue.

    Both, because the `size:*` label is applied at grooming on the ISSUE while a reviewer looking at
    the PR expects to see it there. Taking the union means either placement works.

    """
    names = {label["name"] for label in pr.get("labels", []) if isinstance(label, dict)}
    if issue:
        names |= {label["name"] for label in issue.get("labels", []) if isinstance(label, dict)}
    return names


def _linked_issues(pr: dict[str, Any]) -> list[int]:
    """Issue numbers from `Closes:` footers in the PR body.

    Deliberately only the closing keyword, and deliberately not `Refs:`. One PR closes one issue; a
    `Refs:` pointing at three related items is normal and is not a scope problem.
    """
    body = pr.get("body") or ""
    return sorted({int(n) for n in re.findall(r"(?i)\bcloses:?\s+#(\d+)", body)})


def _budget(labels: set[str]) -> tuple[str | None, int | None]:
    held = [name for name in BUDGETS if name in labels]
    if len(held) != 1:
        # Two size labels is a grooming error and no size label means the issue was never sized.
        # Neither is something to guess through.
        return (None, None)
    return (held[0], BUDGETS[held[0]])


# ------------------------------------------------------------------- materiality


def _canonical(path: str, text: str) -> str:
    """A form of the file that ignores non-material differences.

    This is what ``AGENTS.md``'s material-change rule turns on, and today that rule is applied by
    judgement alone. *Non-material*: a clean merge, formatting, comment and docstring edits, ADR
    renumbering. *Material*: everything else.

    Python goes through ``ast.dump`` with docstrings stripped, so reformatting and rewording a
    docstring are invisible while a changed expression is not. Structured config is
    whitespace-normalised. ADR filenames are canonicalised by the caller, since a renumber changes
    the *name* rather than the body.

    """
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            raise GuardError(f"{path} could not be parsed for a materiality digest") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    node.body = body[1:] or [ast.Pass()]
        return ast.dump(tree)
    if path.endswith((".yml", ".yaml", ".toml", ".json")):
        return _structured(path, text)
    # Everything else - prose, PowerShell, fixtures - is compared verbatim. Guessing at a canonical
    # form for prose is how a real wording change gets called non-material.
    return text


def _structured(path: str, text: str) -> str:
    """Canonicalize structured config through its own data model, never through its whitespace.

    Whitespace-normalizing YAML is not conservative, it is WRONG, and in the unsafe direction.
    Indentation is the nesting, so moving ``peer: 2`` out from under ``root:`` to the top level
    changes the document's meaning while both forms collapse to the same string - and a digest that
    calls that non-material preserves review evidence the change should have invalidated. The same
    substitution rewrites whitespace *inside quoted strings*, so a changed command line in a
    workflow step reads as unchanged.

    Mappings are key-sorted because mapping order is not semantic in any of these formats; sequences
    are not, because a workflow's ``steps:`` order is.

    **Fails closed.** A file that cannot be parsed - or YAML when PyYAML is not installed, which is
    the case for a bare interpreter - is compared verbatim, so every byte change is material. The
    cost is a formatting-only edit re-arming a review; the alternative cost is a semantic edit not
    doing so.
    """
    try:
        if path.endswith(".json"):
            parsed: Any = json.loads(text)
        elif path.endswith(".toml"):
            parsed = tomllib.loads(text)
        else:
            if _Yaml12Loader is None:
                return text
            parsed = list(yaml.load_all(text, Loader=_Yaml12Loader))
    except (ValueError, UnicodeDecodeError, RecursionError) + _YAML_ERRORS:
        return text
    # `default=str` keeps a TOML datetime or a YAML date from raising; the string form is stable,
    # and these are compared against themselves, never interpreted.
    return json.dumps(parsed, sort_keys=True, default=str)


_ADR_NAME_RE = re.compile(r"(docs/adr/)\d{4}(-)")


def _canonical_path(path: str) -> str:
    """Erase an ADR's number from its path.

    ADR renumbering is explicitly non-material, and it is a *rename*: the record's body is unchanged
    while `0054-x.md` becomes `0058-x.md`. Comparing canonical paths means the pair matches up.
    """
    return _ADR_NAME_RE.sub(r"\1NNNN\2", path)


def _digest(entries: dict[str, str]) -> str:
    payload = json.dumps(
        {_canonical_path(p): _canonical(p, t) for p, t in sorted(entries.items())},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def materiality(before: dict[str, str], after: dict[str, str]) -> dict[str, Any]:
    """Whether the change from ``before`` to ``after`` is material, per file and overall."""
    paths = {_canonical_path(p) for p in (*before, *after)}
    by_canonical: dict[str, dict[str, str]] = {}
    for source, side in ((before, "before"), (after, "after")):
        for path, text in source.items():
            by_canonical.setdefault(_canonical_path(path), {})[side] = _canonical(path, text)
    changed = sorted(
        p for p in paths if by_canonical[p].get("before") != by_canonical[p].get("after")
    )
    return {
        "material": bool(changed),
        "material_paths": changed,
        "before_digest": _digest(before),
        "after_digest": _digest(after),
    }


# ------------------------------------------------------------------ measurement


def _blob(sha: str, path: str) -> str | None:
    """A file's text at one commit, or ``None`` when it does not exist there.

    ``None`` is a real answer - a file added by the push is absent before it and present after - and
    is what lets :func:`materiality` see an addition or a deletion rather than an empty edit.
    """
    quoted = urllib.parse.quote(path)
    status, payload = claim._request("GET", f"/repos/{REPO}/contents/{quoted}?ref={sha}")
    if status == 404:
        return None
    if status != 200 or not isinstance(payload, dict):
        raise GuardError(f"{path} at {sha[:8]} could not be read (HTTP {status})")
    if payload.get("encoding") != "base64":
        # Over ~1 MB the contents API answers with an empty body and `encoding: none`. Returning a
        # SHA-dependent sentinel makes the pair differ and the change material, which is the
        # fail-closed direction; a constant would silently call every large file unchanged.
        return f"unreadable-at-{sha}"
    raw = base64.b64decode(payload.get("content") or "")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # A binary fixture has no canonical form, so it is compared by content digest.
        return f"binary:{hashlib.sha256(raw).hexdigest()}"


def materiality_between(since: str, head: str) -> dict[str, Any]:
    """Whether the change from ``since`` to ``head`` re-arms the review gate.

    This is the half of the guard that answers a question the contract already asks and nobody could
    previously answer except by judgement: `AGENTS.md`'s review evidence survives a *non-material*
    push, so whether a push restarts the gate turns on exactly this classification.
    """
    paths = [entry["filename"] for entry in _compare_files(since, head)]
    before = {p: text for p in paths if (text := _blob(since, p)) is not None}
    after = {p: text for p in paths if (text := _blob(head, p)) is not None}
    return materiality(before, after)


def measure(
    number: int, *, base_override: str | None = None, since: str | None = None
) -> dict[str, Any]:
    pr = _pull_request(number)
    linked = _linked_issues(pr)
    issue = None
    if len(linked) == 1:
        status, candidate = claim._request("GET", f"/repos/{REPO}/issues/{linked[0]}")
        if status == 200 and isinstance(candidate, dict):
            issue = candidate

    labels = _labels(pr, issue)
    label, budget = _budget(labels)
    head = (pr.get("head") or {}).get("sha")
    if base_override:
        if not isinstance(head, str) or not head:
            raise GuardError(f"PR #{number} has no head SHA to compare against {base_override}")
        files = _compare_files(base_override, head)
    else:
        files = _files(number)

    counted = [f for f in files if not _excluded(f["filename"])]
    added = sum(int(f.get("additions", 0)) for f in counted)
    deleted = sum(int(f.get("deletions", 0)) for f in counted)
    test_added = sum(int(f.get("additions", 0)) for f in counted if _is_test(f["filename"]))
    src_added = added - test_added

    findings: list[str] = []

    if budget is None:
        findings.append(
            "no single size:* label on the PR or its linked issue, so there is no budget to "
            "measure against; size is applied at grooming"
        )
        ratio = None
    else:
        ratio = round(added / budget, 2)
        if added > budget:
            findings.append(
                f"{added} added lines against {label}'s {budget} - {ratio}x over"
                + (
                    " and above size:L, the largest rung that exists"
                    if added > BUDGETS["size:L"]
                    else ""
                )
            )

    proportional_cap = max(PROPORTIONAL_FLOOR, 2 * src_added)
    if test_added > proportional_cap:
        findings.append(
            f"{test_added} test lines added against a cap of {proportional_cap} "
            f"(max(80, 2 x {src_added} source))"
        )

    oversized = [
        f["filename"]
        for f in files
        if _is_test(f["filename"])
        and f.get("status") == "added"
        and int(f.get("additions", 0)) > TEST_FILE_LINE_CAP
    ]
    for name in oversized:
        findings.append(f"new test file {name} is over the {TEST_FILE_LINE_CAP}-line cap")

    if len(linked) != 1:
        findings.append(
            f"expected exactly one `Closes:` footer, found {len(linked)}"
            + (f" ({', '.join(f'#{n}' for n in linked)})" if linked else "")
        )

    prose_guards = [
        f["filename"]
        for f in files
        if _is_test(f["filename"])
        and f["filename"] not in PROSE_GUARD_ALLOWLIST
        and any(
            fnmatch.fnmatch(g, pattern) for g in _prose_targets(f) for pattern in GOVERNANCE_PROSE
        )
    ]
    for name in prose_guards:
        findings.append(
            f"{name} appears to pin governance prose, which is the retired prose-drift guard "
            "returning under a new name; assert on behaviour, or allowlist it with a reason"
        )

    return {
        "version": 1,
        "pr": number,
        "advisory": True,
        "base": base_override or (pr.get("base") or {}).get("sha"),
        "head": head,
        "size_label": label,
        "budget": budget,
        "added": added,
        "deleted": deleted,
        "added_with_deletions": added + deleted,
        "ratio": ratio,
        "source_added": src_added,
        "test_added": test_added,
        "proportional_cap": proportional_cap,
        "linked_issues": linked,
        "review_rounds": _review_rounds(number),
        "materiality": materiality_between(since, head) if since and head else None,
        "findings": findings,
    }


# A PYTHON string literal naming a `.md` file - single or double quoted, never backticked. Backticks
# are Markdown emphasis inside a docstring, and matching them was this heuristic's first false
# positive: it flagged tests/test_swarm_slots.py because its docstrings CITE AGENTS.md to explain
# why each test exists, which is what a good test docstring does.
_PROSE_LITERAL_RE = re.compile(r"""["']([^"'\n]{0,120}?\.md)["']""")

# The shape of the retired guard: a governance file is OPENED and its text asserted on. Requiring a
# read on the same added line separates "this test reads AGENTS.md" from "this test explains
# itself by naming AGENTS.md", and only the first is prose-pinning.
_READS_A_FILE_RE = re.compile(r"\b(read_text|read_bytes|readlines|open|Path)\s*\(")


def _prose_targets(entry: dict[str, Any]) -> list[str]:
    """Governance-prose paths a test's ADDED lines both name **and read**.

    Read from the patch rather than the whole file, so an existing test is judged only on what this
    PR added to it.

    Both conditions are required. Naming alone produced a false positive when this was replayed over
    #287: it flagged a suite whose docstrings quote AGENTS.md to justify themselves, which is the
    opposite of the behaviour worth discouraging.
    """
    patch = entry.get("patch") or ""
    added = [line[1:] for line in patch.splitlines() if line.startswith("+")]
    reading = [line for line in added if _READS_A_FILE_RE.search(line)]
    return [m.group(1) for line in reading for m in _PROSE_LITERAL_RE.finditer(line)]


def _review_rounds(number: int) -> int:
    """Distinct head SHAs at which an external provider reported, matching ``triage.py``'s counter.

    Reported rather than acted on. It shares the undercount that counter documents - a provider
    answering in a plain issue comment carries no head binding - so it is a visible number for a
    human audit, not an input to any decision here.

    It shared the OVERCOUNT too, until #307. GitHub rewrites an inline review comment's
    ``commit_id`` as the pull request advances, so one review counted once at the head it read and
    again at the head that answered it. The head is ``original_commit_id`` when present - the same
    fix, made in the same change, because a guard that disagrees with the counter it claims to
    mirror is worse than one that does not mirror it at all.

    **The 12 heads this reported for #276 on #290 came from the broken version and are inflated.**

    It shared the *other* overcount too, until #396. GitHub wraps a bot's reply to a review thread
    in a review submission of its own - empty body, state ``COMMENTED``, one comment carrying
    ``in_reply_to_id`` - so answering a review counted as a round. Mirrored here for the same reason
    the ``original_commit_id`` fix was: a guard reporting MORE rounds than the counter it claims to
    mirror reads as "the evidence is ahead of the labels", which is a corruption report about
    nothing.
    """
    heads: set[str] = set()
    # Mirrors `triage.METERED_PROVIDERS`, not every external provider (ADR-0062). Codex is the
    # unmetered lane and consumes no round; Greptile does, because a spent credit is a real one.
    # Reported by name so a reader can see WHICH counter this claims to mirror.
    providers = {"coderabbitai[bot]", "greptile-apps[bot]"}
    counted_from = _counted_from(number)
    reviews = claim._paginate(f"/repos/{REPO}/pulls/{number}/reviews", f"PR #{number} review state")
    comments = claim._paginate(
        f"/repos/{REPO}/pulls/{number}/comments", f"PR #{number} review state"
    )
    # `owns comments, and all of them are replies` - the same spelling `triage._reply_wrapper_ids`
    # uses, and for the same reason: it asks a submission to prove it is a WRAPPER, so anything the
    # payload does not describe fully is still counted.
    only_replies: dict[Any, bool] = {}
    for entry in comments:
        review_id = entry.get("pull_request_review_id")
        if review_id is not None:
            only_replies[review_id] = only_replies.get(review_id, True) and bool(
                entry.get("in_reply_to_id")
            )
    wrappers = {review_id for review_id, every in only_replies.items() if every}
    for entries, is_reviews in ((reviews, True), (comments, False)):
        for entry in entries:
            login = ((entry.get("user") or {}).get("login")) or ""
            sha = entry.get("original_commit_id") or entry.get("commit_id")
            # A reply is not a review (#396). Stated inline rather than delegated because this
            # module deliberately shares no code with `triage.py` - it is a second opinion, and one
            # that imported the counter it audits would not be one.
            if is_reviews:
                # An unsubmitted draft review is not a round (#400), and it is asked FIRST because
                # a `PENDING` review carrying a body would otherwise pass the `real` test below. It
                # has no `submitted_at`, and the timestamp branch at the foot of this loop counts a
                # timestamp-less entry deliberately - so leaving it in spends a round on a review
                # its author has not sent. Mirrors `triage.UNSUBMITTED_REVIEW_STATE`, stated inline
                # because this module shares no code with the counter it audits.
                if entry.get("state") == "PENDING":
                    continue
                real = bool((entry.get("body") or "").strip()) or entry.get("id") not in wrappers
                real = real or entry.get("state") in {"CHANGES_REQUESTED", "APPROVED", "DISMISSED"}
                if not real:
                    continue
                # A round is a metered review that FOUND something (#399). A clean one is the lane
                # terminating, and counting it here would report one round where triage reports
                # zero on every PR CodeRabbit passes - "evidence ahead of the labels", which is the
                # corruption signal this mirror exists to raise rather than to manufacture.
                if entry.get("state") not in {"CHANGES_REQUESTED"}:
                    continue
            elif entry.get("in_reply_to_id"):
                continue
            # Path-appropriate, exactly as `triage._review_state` reads it, rather than
            # `submitted_at or created_at`. The fallback spelling agrees with triage only by
            # accident of payload shape - the reviews endpoint returns no `created_at`, so the
            # `or` finds nothing and both fall through to "no timestamp, count it". Should a
            # review ever carry one, scope_guard would use it and triage would not, and the guard
            # would report FEWER rounds than the counter it mirrors: "labels ahead of the
            # evidence", which is the corruption it exists to detect. A mirror should hold by
            # construction, not by luck about which fields an endpoint happens to send.
            when = entry.get("submitted_at") if is_reviews else entry.get("created_at")
            if login not in providers or not isinstance(sha, str) or not sha:
                continue
            # Draft-phase rounds are free, so counting them here would report a PR as spent while
            # triage correctly reports it as not - the disagreement this function exists to avoid.
            if (
                counted_from is None
                or not isinstance(when, str)
                or not when
                or when >= counted_from
            ):
                heads.add(sha)
    return len(heads)


def _counted_from(number: int) -> str | None:
    """The first ``ready_for_review`` instant, or ``None`` to count everything.

    A read-only mirror of ``triage._counted_from``, and it has to mirror **every** branch of it, not
    just the common one. A guard whose purpose is to notice disagreement with the authoritative
    counter cannot afford to disagree with it itself: this function reported one round fewer than
    ``triage`` for a PR opened ready that later took a draft excursion, which reads as *the labels
    are ahead of the evidence* - the exact shape of the corruption it is watching for.

    Advisory, so an unreadable timeline counts everything rather than failing the job.
    """
    try:
        events = claim._paginate(
            f"/repos/{REPO}/issues/{number}/timeline", f"PR #{number} timeline"
        )
    except claim.ClaimError:
        return None
    ready = [
        stamp
        for event in events
        if event.get("event") == "ready_for_review"
        and isinstance(stamp := event.get("created_at"), str)
        and stamp
    ]
    drafted = [
        stamp
        for event in events
        if event.get("event") == "convert_to_draft"
        and isinstance(stamp := event.get("created_at"), str)
        and stamp
    ]
    # Opened READY: a PR created ready emits no `ready_for_review`, so a later draft excursion and
    # return would make that return look like the first entry into the counted phase, discarding
    # every round spent before it. A `convert_to_draft` earlier than any `ready_for_review` is the
    # signal, and it means the clock started at creation.
    if drafted and (not ready or min(drafted) < min(ready)):
        return None
    if ready:
        return min(ready)
    # No ready event and no draft excursion. A draft has taken no counted round - returning None
    # here would mean "count everything" and report a Greptile review spent during the prescribed
    # draft phase as a round.
    return _COUNT_NOTHING if _is_draft(number) else None


#: Sorts above every ISO-8601 instant, so the ordinary comparison rejects each entry.
_COUNT_NOTHING = "9999-12-31T23:59:59Z"


def _is_draft(number: int) -> bool:
    status, pull = claim._request("GET", f"/repos/{REPO}/pulls/{number}")
    return status == 200 and bool((pull or {}).get("draft"))


def _render(report: dict[str, Any]) -> str:
    lines = [
        "## scope-guard (advisory)",
        "",
        f"PR #{report['pr']} - **{report['added']}** added, {report['deleted']} deleted",
        "",
        "| measure | value |",
        "|---|---|",
        f"| size label | {report['size_label'] or '—'} |",
        f"| budget (added lines) | {report['budget'] or '—'} |",
        f"| ratio | {report['ratio'] if report['ratio'] is not None else '—'} |",
        f"| source / test added | {report['source_added']} / {report['test_added']} |",
        f"| proportional cap | {report['proportional_cap']} |",
        f"| linked issues | {', '.join(f'#{n}' for n in report['linked_issues']) or '—'} |",
        f"| external review rounds | {report['review_rounds']} |",
        "",
    ]
    material = report.get("materiality")
    if material is not None:
        verdict = (
            "**material** — this push re-arms the review gate"
            if material["material"]
            else ("non-material — review evidence survives this push")
        )
        lines += [f"This push is {verdict}.", ""]
        if material["material_paths"]:
            lines += ["<details><summary>What made it material</summary>", ""]
            lines += [f"- `{p}`" for p in material["material_paths"]]
            lines += ["", "</details>", ""]
    if report["findings"]:
        lines += ["### Findings", ""] + [f"- {f}" for f in report["findings"]]
        lines += [
            "",
            "**Advisory only.** These thresholds are known to be miscalibrated for new-executable "
            "work; see `.agents/bin/scope_guard.py`. Nothing here blocks a merge.",
        ]
    else:
        lines.append("Nothing to report.")
    return "\n".join(lines)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Measure a PR against its diff budget. Advisory.")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument(
        "--base",
        help="measure against this commit instead of the PR's own base; for work resuming from a "
        "checkpoint, where measuring from main would charge a claimant for lines they inherited",
    )
    parser.add_argument(
        "--since",
        help="the head this push replaced; classifies the push as material or not, which is what "
        "decides whether review evidence survives it",
    )
    parser.add_argument("--markdown", action="store_true", help="emit a summary table as well")
    args = parser.parse_args()
    try:
        report = measure(args.pr, base_override=args.base, since=args.since)
    except (GuardError, claim.ClaimError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OverflowError, RecursionError, AttributeError, KeyError, TypeError):
        print("error: input exceeds safe processing limits", file=sys.stderr)
        return 2
    except OSError:
        print("error: operating-system I/O failure", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.markdown:
        print(_render(report), file=sys.stderr)
    # A distinct code so a caller can tell "over budget" from "the measurement failed" - but the
    # workflow does not fail on it, because this check is advisory.
    return EXIT_OVER_BUDGET if report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
