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

This file is also the single home of the **frozen approval-scope normalization** (``_scope_hash``).
It moved here from the withdrawn lease helper so that deleting that helper could not break the
claim mutex, and so that the digest an agent recomputes and the digest a maintainer publishes come
from one implementation rather than two.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import http.client
import importlib.util
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any, NamedTuple

# `markdown_structure.py` sits beside this file and is loaded by path, the way `reaper.py` loads
# this one: these scripts run from any working directory and are never installed. It is the one
# module here that is not stdlib - it imports the CommonMark parser the base lock pins (ADR-0066),
# and a lane whose interpreter lacks it is told what to install rather than shown a traceback.
_structure = importlib.util.spec_from_file_location(
    "tether_markdown_structure",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "markdown_structure.py"),
)
if _structure is None or _structure.loader is None:  # pragma: no cover - packaging accident
    raise SystemExit("error: markdown_structure.py is missing next to claim.py")
_markdown = importlib.util.module_from_spec(_structure)
try:
    _structure.loader.exec_module(_markdown)
except ModuleNotFoundError as exc:  # pragma: no cover - environment, not logic
    print(f"error: {exc}", file=sys.stderr)
    sys.exit(2)

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

#: The one supported relaxation of TLS *conformance* checking. See :func:`_ssl_context`; it is never
#: applied silently, and it never touches chain or hostname verification.
STRICT_OPT_OUT = "TETHER_ALLOW_NONSTRICT_X509"

#: Whether this process has already said that it relaxed conformance checking. Per process, not per
#: request: ``_paginate`` can make twenty calls, and twenty identical warnings is none.
_ANNOUNCED = False

EXIT_INELIGIBLE = 3
EXIT_LOST = 4
EXIT_SUPERSEDED = 5


class ClaimError(RuntimeError):
    """A claim precondition failed. The message is safe to print; it carries no path."""


class IneligibleError(ClaimError):
    """A **decided answer about the issue**, and the only thing that may reach exit ``3``.

    Exactly six: the number is a pull request rather than an issue (:func:`_issue`), or the issue
    is not open, not ``status:ready``, assigned to someone else, carries no maintainer approval
    binding its current snapshot, or declares an autonomy this file may not claim
    (:func:`_check_eligible`). ``AGENTS.md`` defines exit ``3`` as
    *ineligible - do not work it*, and a compliant agent obeys it, so anything reported that way
    must be something the server actually told us about the issue.

    **The classification is positive on purpose.** The first fix for #315 subtyped the *failures*
    instead - a ``TransportError`` caught ahead of a blanket ``except ClaimError`` - and Codex's P1
    on #386 showed why that is not enough: ``_token`` raises a plain ``ClaimError`` when there is no
    GitHub token, and ``_issue``/``_paginate`` raise one on any 401, 403 or 5xx. None of those is a
    verdict, all slipped past a subtype-only arm, and the guarantee stayed false. Enumerating what
    *is* a verdict fails safe instead: a new error type added to this file tomorrow exits 2
    unless someone deliberately makes it a verdict.
    """


class TransportError(ClaimError):
    """The API could not be *asked*, as opposed to having answered.

    Not load-bearing for the exit code any more - :class:`IneligibleError` decides that - but it
    still carries the distinction the message needs: ``urllib`` wraps a certificate rejection and
    an unreachable host in one ``URLError``, and those want different words and different remedies.
    """


def _nonstrict_x509_allowed() -> bool:
    """Whether this machine has opted out of strict X.509 *conformance* checking.

    **Only the literal ``1`` arms it** - compared exactly, no ``strip`` and no truthiness. An
    interlock that fires on anything truthy is not an interlock: ``0``, ``false``, ``no`` and a
    stray ``true`` all have to mean *no*, or a typo in a shell profile relaxes a TLS check on a
    path that carries a GitHub token.

    An earlier version stripped whitespace, arguing that ``"1 "`` from a ``.env`` line is
    unambiguous and that refusing it turns a set variable into a silent no-op. Both reviewers
    rejected it and they were right: the contract says *literal*, and the no-op is not silent - a
    value that does not arm produces the ordinary strict failure, which prints the cause and this
    variable. A malformed setting failing loudly is the safer direction to err.
    """
    return os.environ.get(STRICT_OPT_OUT) == "1"


def _ssl_context() -> ssl.SSLContext:
    """The TLS context for every API call. Verification is on; only conformance is negotiable.

    CPython 3.13 turned ``ssl.VERIFY_X509_STRICT`` on by default in ``create_default_context()``,
    and under it OpenSSL rejects a CA certificate whose Basic Constraints extension is not marked
    critical - which is exactly what TLS-inspecting proxies routinely issue. The certificate is
    *found* and *trusted*; it is refused for a conformance defect, so ``SSL_CERT_FILE`` cannot help.
    ``pyproject.toml`` declares ``requires-python = ">=3.11"`` and Windows is the primary
    development platform, so 3.13 and 3.14 are supported interpreters that cannot reach the API.

    There is therefore one opt-out, and it is deliberately the narrowest that works:
    ``TETHER_ALLOW_NONSTRICT_X509=1`` clears *that flag only*, restoring pre-3.13 conformance
    checking. ``verify_mode`` stays ``CERT_REQUIRED`` and ``check_hostname`` stays ``True``, so the
    chain is still verified and the host is still authenticated - this tool sends a GitHub token,
    and a context that skipped either would hand it to whoever answered.

    Three things it is deliberately not: not the default, not a retry after a strict failure, and
    not ``PYTHONHTTPSVERIFY=0``. The strict failure prints the cause and names this variable
    (:func:`_transport_error`); an operator sets it per machine, or does not. ADR-0061.

    And it is **not silent**. A process that has quietly stopped enforcing a check is
    indistinguishable in a log from one that never needed to, so the relaxation announces itself on
    stderr - once per process, because ``_paginate`` can make twenty calls and a warning repeated
    twenty times is one nobody reads.
    """
    context = ssl.create_default_context()
    if _nonstrict_x509_allowed():
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        _announce_nonstrict()
    return context


def _announce_nonstrict() -> None:
    """Say so, on stderr, the first time the relaxed context is built in this process."""
    global _ANNOUNCED
    if _ANNOUNCED:
        return
    # Latch *after* the write, not before. If stderr is closed or its reader has exited, the print
    # raises and the notice never arrived; latching first would let a caller that catches the write
    # error and retries get a relaxed context in silence, which is the one thing this interlock
    # exists to prevent (Codex P2 on #388).
    print(
        f"notice: {STRICT_OPT_OUT}=1 is set, so X.509 strict-conformance checking is relaxed for "
        "this process. Certificate chain and hostname verification remain enabled (ADR-0061).",
        file=sys.stderr,
    )
    _ANNOUNCED = True


#: OpenSSL's wording for conformance rejections that ``VERIFY_X509_STRICT`` adds - the failures
#: clearing that flag can actually fix. Matched on the message rather than on a numeric code because
#: the codes are not stable across OpenSSL builds and a wrong code silently mutes the remedy.
#:
#: **This list is not assumed to be exhaustive**, and nothing downstream depends on it being so.
#: OpenSSL gates a family of checks behind ``X509_V_FLAG_X509_STRICT`` and the wording varies by
#: build, so an unmatched message means *unknown*, not *not-conformance*. See
#: :func:`_certificate_detail`, which says so rather than guessing.
_STRICT_MARKERS = (
    "not marked critical",
    "basic constraints",
    "invalid ca certificate",
    "missing authority key identifier",
    "missing subject key identifier",
    "key usage extension",
    "key usage violation",
    "authority and subject key identifier mismatch",
    "authority and issuer serial number mismatch",
    "certificate version",
)
#: The failure for which ``SSL_CERT_FILE`` genuinely is the answer: the issuer is simply not here.
_MISSING_ISSUER_MARKERS = ("unable to get local issuer", "self-signed certificate")
#: Failures that are certainly *not* conformance defects, so the opt-out certainly cannot help. An
#: expired certificate is expired however X.509 conformance is configured.
_NOT_CONFORMANCE_MARKERS = (
    "expired",
    "not yet valid",
    "hostname mismatch",
    "is not valid for",
    "revoked",
    "certificate signature failure",
)


def _certificate_detail(cause: str) -> str:
    """The message for a certificate rejection, with only the remedy that fits what was seen."""
    head = f"the GitHub API's TLS certificate failed verification: {cause}."
    lowered = cause.lower()
    relaxed = _nonstrict_x509_allowed()

    # Classify what was observed FIRST, and let the opt-out's state qualify only the branches that
    # are actually about conformance. The opt-out used to short-circuit ahead of all of this and
    # announce "the chain itself is not trusted", which is wrong for an expired certificate or a
    # hostname mismatch - neither is a chain-trust failure, and both survive the relaxation because
    # it deliberately leaves chain and hostname verification on (Codex P1 on #388).
    if any(marker in lowered for marker in _MISSING_ISSUER_MARKERS):
        return (
            f"{head} The issuer could not be found in this machine's trust store. If it uses a "
            "private or corporate CA, point SSL_CERT_FILE at that CA bundle. This is not a "
            f"conformance defect, so {STRICT_OPT_OUT} cannot address it."
        )
    if any(marker in lowered for marker in _NOT_CONFORMANCE_MARKERS):
        return (
            f"{head} This is not a conformance defect - the certificate is invalid however X.509 "
            f"conformance is configured - so {STRICT_OPT_OUT} cannot address it, and the "
            "certificate should not be accepted."
        )
    if any(marker in lowered for marker in _STRICT_MARKERS) and _strict_is_the_default():
        if relaxed:
            return (
                f"{head} That reads like a conformance signature, but {STRICT_OPT_OUT} is already "
                "set, so strict conformance is not what rejected it: chain and hostname "
                "verification stay on under the relaxation, and one of those refused. Do not relax "
                "verification further."
            )
        return (
            f"{head} The host is reachable and the certificate was found - this is a local trust "
            "decision. CPython 3.13+ verifies under ssl.VERIFY_X509_STRICT, which rejects a CA "
            "whose Basic Constraints extension is not marked critical, and TLS-inspecting proxies "
            "routinely issue exactly that; SSL_CERT_FILE cannot help, because the CA is not "
            f"missing. If that describes this machine, set {STRICT_OPT_OUT}=1 to relax that one "
            "conformance check - chain and hostname verification stay on."
        )
    if relaxed:
        return (
            f"{head} {STRICT_OPT_OUT} is already set, so strict conformance is not the cause: "
            "chain and hostname verification remain enabled under the relaxation, and this "
            "failure came from one of them. Do not relax verification further."
        )
    if _strict_is_the_default():
        # Deliberately non-committal, and both halves of that are review findings. Offering the
        # remedy here pointed expired certificates at a TLS switch that cannot help them; *denying*
        # it here was equally unfounded, because `_STRICT_MARKERS` cannot be exhaustive - OpenSSL
        # gates a family of checks behind X509_V_FLAG_X509_STRICT and words them per build, so an
        # unmatched message is genuinely unknown. Asserting either way is the confidently-wrong
        # message #315 exists to remove; the honest answer names both possibilities and an
        # experiment that separates them.
        #
        # The experiment is the opt-out itself, on THIS interpreter. Re-running under an older one
        # was the first suggestion and it does not isolate the flag: a different interpreter brings
        # a different OpenSSL build, a different default CA path and - on this machine's documented
        # split - a different environment entirely, so success there proves nothing about encoding.
        # Toggling one variable in one process does (Codex P1 on #388).
        return (
            f"{head} This interpreter verifies under ssl.VERIFY_X509_STRICT (CPython 3.13+), and "
            "this tool cannot tell from that message whether strict conformance is the cause or "
            f"the chain is genuinely untrusted. To find out, set {STRICT_OPT_OUT}=1 and run the "
            "same command again on this same interpreter: if it succeeds, only the CA's encoding "
            "was at fault and that setting is the supported fix. If it still fails, the "
            "certificate is not acceptable and must not be forced."
        )
    return (
        f"{head} Strict conformance checking is not enabled on this interpreter, so it is not "
        "the cause."
    )


def _strict_is_the_default() -> bool:
    """Whether CPython's own default context enables strict conformance here (3.13 turned it on).

    Read from ``create_default_context()`` rather than from ``sys.version_info``: the flag is a
    property of the interpreter's ssl defaults, and asking the source of truth costs nothing.
    """
    return bool(ssl.create_default_context().verify_flags & ssl.VERIFY_X509_STRICT)


def _transport_error(exc: urllib.error.URLError) -> TransportError:
    """Name the cause actually observed, rather than blaming the host for a local trust decision.

    ``urllib`` wraps ``ssl.SSLCertVerificationError`` in ``URLError``, so a single "unreachable"
    message used to cover a certificate that was found, read and refused - sending the reader off
    to hunt a network outage that does not exist while ``gh api /zen`` succeeds from the same shell
    in the same second.

    Only path-free fields are interpolated (``verify_message``, ``strerror``, the exception's class
    name). ``str(reason)`` is avoided on purpose: an ``OSError`` renders its ``filename``, so a
    misdirected ``SSL_CERT_FILE`` would print a private path, and ``ClaimError`` promises it does
    not do that.

    **The strict-conformance story is offered only where it can be true.** Codex's P2 on #388: the
    first version told *every* certificate failure that the CA had been found and that
    ``SSL_CERT_FILE`` could not help - including an expired certificate, a hostname mismatch, or a
    genuinely missing issuer, for which ``SSL_CERT_FILE`` is precisely the remedy - and said it on
    3.11 and 3.12, where strict mode is not even enabled. That is the confidently-wrong message this
    issue exists to remove, reintroduced one branch over. Each case now gets the remedy that applies
    to it, and where nothing is known, none is offered.
    """
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLCertVerificationError):
        cause = (reason.verify_message or reason.strerror or "certificate verify failed").strip()
        return TransportError(_certificate_detail(cause))
    if isinstance(reason, OSError) and reason.strerror:
        return TransportError(
            f"the GitHub API could not be reached ({type(reason).__name__}: {reason.strerror})"
        )
    detail = type(reason).__name__ if reason is not None else type(exc).__name__
    return TransportError(f"the GitHub API could not be reached ({detail})")


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
    """Return (status, parsed-json). HTTP errors are returned, not raised: 422 is an answer.

    **Every other failure leaves here as ``ClaimError``**, which is the promise callers actually
    hold: the ones that fail closed catch it and stop, and the few documented to fail *soft* catch
    it and carry on. Only the transport half of that was true — a truncated read or a body that is
    not JSON escaped as ``IncompleteRead`` or ``ValueError`` from the success path, past every one
    of those handlers, and took the whole run down (CodeRabbit on #407, against
    ``triage._verdict_at_head``, whose docstring says an unreadable list means *no verdict seen*).
    Caught at the source rather than at that one call site, because the promise is this function's
    to keep and every caller was relying on it.
    """
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
        with urllib.request.urlopen(  # noqa: S310
            request, timeout=60, context=_ssl_context()
        ) as response:
            payload = response.read()
            return response.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as error:
        # Inside a handler, so the sibling below CANNOT catch what this raises - which is the same
        # defect one branch over, and CodeRabbit's `Major` on #407 for it. Degraded to `(code,
        # None)` rather than raised, because it is the same loss as the unparseable body just
        # below: the status line arrived intact, so the answer is known even when the body is not,
        # and this function's contract is that HTTP errors are RETURNED. Raising would turn a
        # perfectly legible 404 into an exception because its body was truncated.
        try:
            payload = error.read()
        except (OSError, ValueError, http.client.HTTPException):
            return error.code, None
        try:
            return error.code, json.loads(payload) if payload else None
        except ValueError:
            return error.code, None
    except urllib.error.URLError as exc:
        raise _transport_error(exc) from exc
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # Ordering is load-bearing: `HTTPError` and `URLError` are both `OSError`, and both are
        # answers rather than failures, so they are handled above and never reach here. What does
        # is the response going wrong mid-read or arriving as something `json.loads` refuses.
        raise ClaimError(f"the GitHub API answer could not be read ({type(exc).__name__})") from exc


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
    """Digest the normalized issue snapshot a maintainer approval binds to.

    **Frozen.** CRLF and lone CR become LF, trailing newlines are stripped, and the result is hashed
    as compact JSON with sorted keys and ``ensure_ascii=False``. The markers published on #188,
    #189, #216 and #218 were computed by this normalization and must keep verifying, so a pinned
    digest in ``tests/test_claim.py`` covers every clause of it.

    This used to shell out to ``swarm_lease.py`` so there would be exactly one copy of the
    normalization. Re-homing it here keeps that property and removes the reason the indirection
    existed: no temp file, no subprocess, and no file-or-stdin read at all. The body arrives from
    the API as ``str``, and a digest that never touches a file cannot be changed by a shell
    round-trip that prepends a BOM - which is #272's item 2, and the only documented way a
    round-trip could invalidate an approval.
    """

    def normalize(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")

    normalized = {"body": normalize(body), "title": normalize(title), "version": 1}
    try:
        payload = json.dumps(
            normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except UnicodeError as exc:
        raise ClaimError("scope title and body must be valid UTF-8 text") from exc
    return hashlib.sha256(payload).hexdigest()


#: The one declared autonomy an agent may claim under. The issue forms that collect this field offer
#: three values - `agent-can-do-alone`, `maintainer decision required`, `external/human action
#: required` - so this is an enumeration rather than a guess. `agent can complete alone` is a legacy
#: prose spelling that predates the dropdown and is still on live issues.
AUTONOMY_ADMITS = ("agent-can-do-alone", "agent can complete alone")

#: Any of these anywhere in a declared value or scan-only restriction refuses it. A value like
#: "agent-can-do-alone for the drafting; the membership question is a maintainer decision"
#: declares two things, and the safe reading of a split declaration is the restrictive one.
AUTONOMY_REFUSES = (
    "maintainer decision",
    "maintainer input",
    "human action",
    "needs-maintainer-input",
    "needs-human-action",
    "human-executed",
    "external/human",
)

#: The key of a bullet declaration, matched against the **whole first paragraph** of a list
#: item as the parser hands it over - so Markdown has already decided where the item begins, which
#: lines its paragraph spans, and that a fenced example is not a bullet (ADR-0066). The `**`
#: emphasis is not in the pattern at all, because the pattern reads the **rendered** text
#: (``plain``): `- **Autonomy:**`, `- Autonomy:` and `- <b>Autonomy:</b>` all render as the one
#: key, so a restriction written any of those ways is read and a safety verdict never turns on
#: typography. One colon, exactly: `Autonomy::` is not the key the forms write, and a pattern
#: that tolerated a second colon admitted it (Codex on #462). The value may be empty: `-
#: **Autonomy:**` with nothing after it is a field the page shows, and a pattern that required a
#: character there made it vanish beside an admitting bullet (Codex on #462); as an empty value
#: it fails the exact check instead. Whether the item may *admit* is decided from its marker,
#: column and source in `_bullet`, not here.
_AUTONOMY_BULLET = re.compile(
    r"(?:execution[ \t]+)?autonomy(?P<qualifier>[^:\n]*):[ \t]*(?P<value>.*)",
    re.I | re.S,
)
#: The key as a heading's text or a table row's first cell, rendered: a heading's closing `#`
#: run already removed and its indent measured into `Heading.column`, a row's outer pipes already
#: Markdown's business, and emphasis, code and link markup already gone. A trailing colon is
#: tolerated once - `## Autonomy:` is a heading the corpus writes - and never twice, for the
#: reason the bullet gives (Codex on #462). Whatever follows the key on the line is its
#: qualifier, and every heading that starts with the key is read: `## Execution autonomy after
#: unblock` over a restriction was no key at all, so an admitting bullet beside it carried the
#: issue while the page shows the field qualified, and bounding the qualifier to a character
#: class left `once #123's merged` unread the same way (Codex on #462, twice). How the section
#: is read turns on the qualifier's shape - see `_PROSE_QUALIFIER`.
_AUTONOMY_KEY = re.compile(r"(?:execution[ \t]+)?autonomy(?P<qualifier>[^:\n]*?)[ \t]*:?", re.I)
#: The key alone, as a paragraph of its own - `**Execution autonomy**` over the paragraph that
#: holds its value - or as a block raw HTML lays out that is not a heading. It heads what the
#: page draws next, as a heading does, and nothing it heads can admit. No qualifier and no
#: colon: a paragraph is prose, and `Autonomy is discussed above.` must stay prose, while
#: `Autonomy:` alone is the bullet's grammar with an empty value and is read as that. The
#: raw HTML shape was found by Codex on #462 (`<div><h2>Execution autonomy</h2><p>maintainer
#: decision required</p></div>`, read with the bullet's grammar only, which needs a colon);
#: the paragraph is its Markdown mirror.
_BARE_KEY = re.compile(r"(?:execution[ \t]+)?autonomy", re.I)
#: The blocks raw HTML lays out as headings, read as a Markdown heading is read.
_HTML_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
#: The block raw HTML lays out as code, literal as a fence is: an example in a `<pre>` neither
#: declares nor restricts (Codex on #462).
_HTML_LITERAL = frozenset({"pre"})
#: A qualifier that makes the heading one *about* the field rather than the field qualified: it
#: **opens** with a dash. `### Execution autonomy — declared in the grooming block` heads a
#: paragraph of prose on #442, a `status:ready` issue, and reading that paragraph as the field's
#: value refused it. A condition is written without one - `after unblock`, `once #123's merged`,
#: `(if #220 lands)` - and such a heading is a declaration that refuses on its qualifier. A
#: dash-led heading's section is read for restrictions, every block scan-only, and declares
#: nothing; so no heading that names the field goes unread, and the one shape the corpus writes
#: as prose keeps admitting. The dash must be the qualifier's first character after whitespace,
#: matched with ``match`` and never searched for: a dash *later* in it - `after unblock - notes`
#: - made a condition into prose about the field, and the condition was never exact-checked
#: (Codex on #462). A hyphen counts only with whitespace after it, since a condition hyphenates
#: its words.
_PROSE_QUALIFIER = re.compile(r"\s*(?:[\u2014\u2013]|-(?=\s))")
#: The grooming marker. Source selection keys on **this**, never on the text the block yields: a
#: marker ending the body captures no blocks at all, as does one followed only by a comment, and
#: treating either as "no grooming block" handed the decision back to the stale body the block
#: was written to supersede. An empty groom is still a groom.
_GROOMING_MARKER = re.compile(r"<!--[ \t]*tether-grooming-v1[ \t]*-->")
#: The checkbox GitHub draws at the front of a task-list item, as it reaches the rendered text:
#: the ``commonmark`` preset has no task-list rule, so ``[ ]`` and ``[x]`` stay literal.
_TASK_MARKER = re.compile(r"\[(?: |x|X)\](?: |$)")


class _AutonomyValue(NamedTuple):
    """One value to exact-check, or prose to scan for an explicit refusal token.

    ``admits`` is whether this shape may be the reason an issue is claimed. A value that is
    exact-checked but cannot admit - a `+` or indented bullet - refuses when it is not a registered
    value and otherwise counts for nothing, so reading it widens what the gate sees and not what
    it accepts.
    """

    raw: str
    where: str
    scan_only: bool = False
    qualifier: str = ""
    admits: bool = True


def _normalize_autonomy(value: str) -> str:
    """Lowercase, trim the ends, drop a trailing period.

    Feeds :func:`_flatten_autonomy`, which is what comparison uses. Internal whitespace is left
    alone here; the flattener collapses it. This used to strip `` ` ``, `*` and `_` as Markdown
    emphasis, which was right while values were read off the source and wrong once they were
    rendered: a `*` that survives rendering was escaped or quoted and is **shown** on the page, so
    `agent\\*-can-do-alone` is not the registered value and stripping the star admitted it
    (Codex on #462). The parser removes the markup that is markup; nothing is removed here.
    """
    return value.strip().rstrip(".").strip().lower()


def _flatten_autonomy(value: str) -> str:
    """The **comparison** form: separators become spaces, and runs collapse.

    The corpus already spells one concept two ways - `maintainer decision` in 51 issue bodies and
    `needs-maintainer-input` in one - so matching a literal token lets a separator decide a safety
    verdict. `agent-can-do-alone; maintainer-decision required`, the hyphenated form Greptile
    constructed on #428, opens with an admitting prefix and names a restriction the spaced token
    cannot see, so it would have been **admitted**: a fail-open in the one gate whose whole purpose
    is to fail closed.

    Both sides are flattened, so `AUTONOMY_ADMITS` and `AUTONOMY_REFUSES` may use spaces, hyphens,
    underscores or slashes interchangeably. `/` is in that set for `external/human`, the only entry
    carrying one: without it, re-spelling that entry as `external - human` would silently re-open
    the fail-open this function exists to close, for a body that opens with an admitting prefix.

    **It closes the separator class and nothing wider.** A restriction phrased outside
    `AUTONOMY_REFUSES` altogether - "needs maintainer sign-off", "human review required" - makes a
    declaration fail the separate exact-value check; this function does not infer its meaning.
    The list stays load-bearing for scan-only heading prose and table rows, where exact matching is
    deliberately not applied. The flattener only stops punctuation defeating those explicit tokens.

    **Separators are widened on the rendered text**, so `needs_human_action` - literal
    underscores, which CommonMark does not read as emphasis inside a word - becomes
    `needs human action` and matches the entry.
    """
    widened = re.sub(r"[\s_/-]+", " ", value)
    return re.sub(r"\s+", " ", _normalize_autonomy(widened)).strip()


class _Leaf(NamedTuple):
    """A leaf block and the container it was parsed in - the tuple of blocks it is one of.

    The container travels with the leaf because flattening erases it, and one decision needs it
    back: whether a heading's value is the heading's **own** next paragraph or a paragraph inside
    a block quote or list item below it. `## Execution autonomy` over `> agent-can-do-alone` put a
    registered value in the quote, and the flattened section could not tell (Codex on #462).
    Identity, not equality: the tuple is the one the parser built, and two containers never share
    it.
    """

    block: Any
    siblings: tuple[Any, ...]


def _flat(blocks: tuple[_markdown.Block, ...]) -> list[_Leaf]:
    """The leaf blocks of ``blocks`` in document order: what a reader of the rendering sees, top
    to bottom, with list items and block quotes opened up and a table given one leaf per row.

    Containers are not leaves. A heading's section is read off this sequence rather than off the
    heading's siblings, because a reader does not see the ``<li>`` a heading happens to sit in: an
    indented `## Execution autonomy` that Markdown nests inside the bullet above it is still, on
    the rendered page, a heading with the paragraph below it, and the regex reader's successor must
    not lose that paragraph to a container boundary the reader cannot see. Each leaf still names
    its container, for the one decision that must see the boundary.

    One exception, in the other direction: a container none of whose leaves is drawn stands in
    as a leaf itself. The page draws a block quote's bar and a list's bullet whatever they hold,
    so `> <!-- note -->` under `## Execution autonomy` is something the reader sees between the
    heading and the paragraph below it; opened up to its one undrawn leaf, it vanished, and that
    paragraph admitted as the heading's own next paragraph (Codex on #462).
    """
    leaves: list[_Leaf] = []
    for block in blocks:
        if isinstance(block, _markdown.ListBlock):
            inner = [leaf for item in block.items for leaf in _flat(item.blocks)]
        elif isinstance(block, _markdown.BlockQuote):
            inner = _flat(block.blocks)
        elif isinstance(block, _markdown.Table):
            inner = [_Leaf(row, blocks) for row in block.rows]
        else:
            leaves.append(_Leaf(block, blocks))
            continue
        if not any(_drawn(leaf.block) for leaf in inner):
            leaves.append(_Leaf(block, blocks))
        leaves.extend(inner)
    return leaves


def _prose(block: Any) -> str:
    """The text a reader of the rendered ``block`` would see, one string, as flat as the flattener.

    Code is literal, so it is empty here: a token quoted in an example is not a restriction.
    Everything else is the parser's rendering of it - ``plain``, rendered from the inline tokens
    (ADR-0066) - because every attempt to read the rendering off the source with a pattern missed
    a shape: `maintainer decision required` inside a `<summary>` was dropped as non-prose, a key
    split by an inline comment was not the key, and `## [Execution autonomy](url)` was a link
    rather than the heading (all Codex on #462). What the page hides - a comment - is not here,
    so a token inside one is not a restriction. A table row is its cells in order.
    """
    if isinstance(block, (_markdown.Paragraph, _markdown.Heading, _markdown.Html)):
        return block.plain
    if isinstance(block, _markdown.TableRow):
        return " ".join(cell for cell in block.plain if cell)
    return ""


def _html_in(block: Any) -> Iterator[str]:
    """Every run of HTML in ``block`` as the parser read it - comments included - for finding a
    marker that prose hides.

    A raw HTML block is HTML throughout. Inline text yields what the parser found to be HTML,
    and not the source: a code span quoting the marker is text on the page, and searching the
    source read ``Use `<!-- tether-grooming-v1 -->` when re-grooming`` as a misplaced marker
    (Codex on #462). Code is nothing: a marker in a fence is literal.
    """
    if isinstance(block, _markdown.Html):
        yield block.text
    elif isinstance(block, (_markdown.Paragraph, _markdown.Heading)):
        yield from _markdown.inline_html(block.text)
    elif isinstance(block, _markdown.TableRow):
        for cell in block.cells:
            yield from _markdown.inline_html(cell)


def _scan_only(leaves: list[_Leaf], where: str) -> list[_AutonomyValue]:
    """``leaves`` as scan-only values, **one per rendered block**, never one string for the lot.

    Joined across a block boundary, `... is a human` and `Action items ...` read as `human action`
    and refused a registered declaration (Codex on #462). A token governs where it is written.
    """
    found: list[_AutonomyValue] = []
    for leaf in leaves:
        if isinstance(leaf.block, _markdown.TableRow):
            # One per cell, for the same reason: `| human | action items |` is two cells on
            # the page and joining them read `human action` where nobody wrote it (Codex on #462).
            found.extend(
                _AutonomyValue(cell, where, scan_only=True) for cell in leaf.block.plain if cell
            )
            continue
        if isinstance(leaf.block, _markdown.Html):
            # And one per block the raw HTML lays out, for the same reason again; a `<pre>` is
            # literal, as a fence is.
            found.extend(
                _AutonomyValue(piece.text, where, scan_only=True)
                for piece in leaf.block.shown
                if piece.text and piece.tag not in _HTML_LITERAL
            )
            continue
        text = _prose(leaf.block)
        if text:
            found.append(_AutonomyValue(text, where, scan_only=True))
    return found


def _is_grooming_marker(block: Any) -> bool:
    """Whether ``block`` is a marker line: a raw HTML block that **begins** with the marker and
    **shows nothing**.

    Begins with, not contains. Markdown ends a comment block on the line that closes the
    comment, so a marker written on its own line is its own block whatever follows it; a marker
    *inside* some other raw HTML - `<div>`, marker, `</div>` - is one block with the `<div>`, and
    searching that block's text for the marker made the whole construct a grooming block that was
    never on a line of its own (Codex on #462). `_misplaced_marker` refuses that body instead.

    Shows nothing, because the comment block runs to the end of the line that closes it: a
    marker followed on its own line by `maintainer decision required` is one block that begins
    with the marker, and reading it as the marker discarded the restriction the page shows beside
    it (Codex on #462). A block that shows text is not a marker on its own line, and the misplaced
    rule refuses it. Nor is one that draws something without text - `<img>` beside the marker,
    an empty `<details>` - which `plain` is empty for and `_drawn` is not (Codex on #462). A
    marker followed only by other comments draws nothing and is still a marker.
    """
    if not isinstance(block, _markdown.Html) or _drawn(block):
        return False
    return _GROOMING_MARKER.match(block.text.lstrip()) is not None


def _grooming_block(document: tuple[_markdown.Block, ...]) -> tuple[_markdown.Block, ...] | None:
    """The blocks of the **latest** ``tether-grooming-v1`` block, or ``None`` when there is none.

    A grooming block is a marker **on its own line at the top level** and the top-level blocks
    after it, up to the next marker or the end of the body. It runs *through* any other HTML
    comment: terminating on any `<!--` meant one nested aside truncated the authoritative source,
    so a declaration above it governed and a restriction below it was never read - the same
    fail-open direction as every other defect in this path. A comment is not prose, so an aside
    inside the block cannot itself declare or restrict anything.

    **When there are several, the last one is the latest pass and it alone governs.** A block
    supersedes the body because it is the later statement of readiness, and a second block is
    later again: reading every block let an admitting declaration in the first govern a body
    whose latest pass dropped it, which is exactly the fallback the precedence exists to refuse
    (Codex on #462). The latest pass saying nothing is silence, and silence refuses.

    A marker written anywhere else - inside a list item or block quote, or inline in a paragraph -
    is **not** a grooming block, and `_autonomy_refusal` refuses the body outright rather than read
    around it: the marker's whole purpose is to supersede the text above it, and a reader that
    cannot say where that text ends must not guess. A marker inside a fence is literal and is
    neither (ADR-0066).
    """
    current: list[_markdown.Block] | None = None
    for block in document:
        if _is_grooming_marker(block):
            current = []
        elif current is not None:
            current.append(block)
    return None if current is None else tuple(current)


def _misplaced_marker(source: tuple[_markdown.Block, ...]) -> bool:
    """Whether a grooming marker sits anywhere in ``source`` that a grooming block cannot start.

    That is a marker block that is not top-level, or the marker's text anywhere that is not a
    marker block: inline in a paragraph, in a table cell, or inside some other raw HTML. Every
    leaf is searched for the **HTML the parser found in it** rather than its rendered prose,
    because the marker is a comment and prose hides comments - and not its source either, since
    a code span quoting the marker is text (Codex on #462). A marker in a fence is literal, and
    a fence is never searched.

    ``source`` is the authoritative one - the latest grooming block when there is one, else the
    body - and not the whole document: a stale paragraph that *quotes* the marker, above a later
    marker on its own line, is superseded like everything else above that line, and refusing the
    body for it refused a correctly re-groomed issue (Codex on #462). Inside the latest block a
    misplaced marker still refuses, since it is there that what it supersedes cannot be read.
    """
    for leaf in _flat(source):
        if _is_grooming_marker(leaf.block):
            if leaf.siblings is not source:
                return True
        elif any(_GROOMING_MARKER.search(chunk) for chunk in _html_in(leaf.block)):
            return True
    return False


def _declared_autonomy(body: str) -> list[_AutonomyValue]:
    """Every autonomy value or scan-only restriction in the authoritative source.

    The value is returned **unnormalized**. `_normalize_autonomy` strips `_` as Markdown
    emphasis, so normalizing here would glue `needs_human_action` into one word before
    `_flatten_autonomy` could widen it - the caller needs the raw text to do both.

    The corpus renders this several ways - an `## Execution autonomy` heading, a shorter
    `## Autonomy` one, a ``- **Autonomy:**`` bullet, and a ``<!-- tether-grooming-v1 -->`` block -
    and they do not always agree, so the order below is the decision.

    **A grooming block wins whenever one is present, and the latest of them wins over the rest**
    - not merely when it happens to declare an autonomy - because those blocks exist to restate
    readiness after the body above them went stale. Reading the body first would let a superseded
    value admit work the grooming pass had already restricted, and *falling back* to the body when
    the block declares nothing is the same failure wearing a different hat: the value the pass
    dropped would govern because it was dropped. A block that says nothing about autonomy has
    said nothing, and the caller refuses silence. A second block is later again, so it supersedes
    the first the way the first supersedes the body: reading both let an admitting value in an
    earlier pass govern a body whose latest pass had dropped it, the same fallback in a different
    place (Codex on #462).

    **Within one source there is no such precedence, so every declaration in it is returned and the
    caller refuses if any of them does.** This returned only the first match and looked for bullets
    before headings, which meant a body whose `## Execution autonomy` section read `maintainer
    decision required` was admitted outright when any bullet elsewhere read `agent-can-do-alone` -
    the heading was never reached. Nothing justifies a bullet outranking a heading the way a
    grooming block outranks a stale body; two disagreeing declarations in one source mean the issue
    is not clearly groomed, which is the case this gate exists to refuse. It is the same rule
    `_autonomy_refusal` already applies to a single value that names both.

    **A qualified bullet key refuses.** `Autonomy after unblock` is not the bare field the issue
    forms collect, and accepting it leaves a condition-shaped path around exact value matching.
    The one measured false negative that justified the old behavior, #214, closed on 2026-08-12.
    Reading a neighbouring `Status:` bullet to decide whether the qualifier is satisfied is not a
    substitute: that would teach this mutex gate to adjudicate blockedness from prose, which #326
    and #336 deliberately exclude. The qualifier travels separately so the refusal names the line
    a groomer must rewrite.

    **Where a declaration begins and ends is Markdown's decision, and the parser makes it**
    (ADR-0066). A bullet's value is the whole of its first paragraph, however many lines it wraps
    across, and nothing of its second; a heading's value is the first prose block below it on the
    rendered page, and its section runs to the next heading of any level; a fenced or indented
    example is literal. The rest of a list item and the rest of a heading's section are scan-only,
    one rendered block at a time: an explicit `AUTONOMY_REFUSES` token there still governs, but
    ordinary explanatory prose cannot become an unregistered second declaration.

    **Only the registered shapes admit**: a `-` or `*` bullet at column zero, and an ATX heading at
    column zero whose value is a paragraph. GitHub also renders `+` bullets, indented bullets,
    nested and block-quoted items, setext headings, headings inside list items, a table row keyed
    `autonomy` and a value written in raw HTML, and a restriction written any of those ways must
    still be read - so they are exact-checked like any declaration and marked unable to admit. An
    unregistered value in one refuses; a registered value in one counts for nothing. That widens
    what the gate sees without widening what it accepts, the only direction a mutex gate may grow.
    """
    return _declarations(_markdown.parse(body))


#: Raw HTML elements whose end tag pops the stack back to them, closing a `<details>` opened
#: inside (HTML5 "in body": the end tags that generate implied end tags and pop to their
#: element; "in table", "in row" and "in cell" for the table ones). `<div>` is one of them -
#: `</div>` is listed by name - as is every list, table and heading element.
_HTML_SCOPES = frozenset(
    {"address", "article", "aside", "blockquote", "button", "caption", "center", "colgroup"}
    | {"dd", "dialog", "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer"}
    | {"h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "li", "listing", "main", "menu"}
    | {"nav", "ol", "pre", "search", "section", "summary", "table", "tbody", "td", "tfoot"}
    | {"th", "thead", "tr", "ul"}
)
#: The scope boundaries among them: a `</details>` written inside one cannot close a
#: `<details>` opened outside it (HTML5 "has an element in scope").
_HTML_BOUNDARIES = frozenset({"caption", "table", "td", "th"})


def _collapsed(document: tuple[_markdown.Block, ...]) -> list[tuple[int, float]]:
    """The source-line spans GitHub collapses: from a top-level ``<details>`` to its end.

    What a `<details>` takes is the HTML parser's decision, not Markdown's, and these are its
    rules (HTML5 "in body", the start and end tags of `details`). One opened in a raw block, or in
    the running text of a top-level paragraph - where it closes the paragraph - stays open across
    the blocks that follow, to its `</details>` or the end of the body. One opened inside a
    heading, a list item, a block quote or a table cell is popped by that element's own end tag
    and takes nothing past it (Codex on #462). A `</details>` closes the innermost open one from
    wherever it is written - a paragraph, a heading, a list item - a table cell excepted, since a
    cell is a scope boundary the end tag cannot see through. Only the top-level spans are
    returned: the registered shapes are both at column zero, so what a `<details>` inside a
    container collapses is refused already.

    Tags are the parser's reading, comments removed first - a `</details>` in a comment closes
    nothing - and inline HTML is what the parser found, so a tag in a code span is text and
    opens nothing (Codex on #462). A declaration on a line inside a span is not on the page a
    reader sees, so it may refuse and may not admit; `<details open>` is collapsed here too,
    because a disclosure widget is not the registered shape whichever way it starts.
    """
    spans: list[tuple[int, float]] = []
    # Each open `<details>`: the nesting level it was opened at and its source line, innermost
    # last. Level zero is the document's own blocks; each heading, list item or block quote is a
    # level deeper, and its end tag pops whatever was opened inside it.
    opened: list[tuple[int, int]] = []

    def read(found: Iterator[tuple[str, bool]], level: int, line: int) -> None:
        for name, closing in found:
            if name != "details":
                continue
            if not closing:
                opened.append((level, line))
            elif opened:
                at, start = opened.pop()
                if at == 0:
                    spans.append((start, line))

    def leave(level: int) -> None:
        while opened and opened[-1][0] >= level:
            opened.pop()

    def raw(block: _markdown.Html, level: int) -> None:
        # Raw HTML has scopes of its own. A `<details>` opened inside a `<td>`, an `<li>`, a
        # `<div>` or any other `_HTML_SCOPES` element is popped by that element's end tag, and
        # a `</details>` inside a table cell cannot close one opened outside the cell. Left to
        # the end of the body, `<td><details>note</td>` collapsed a registered bullet below the
        # table (Codex on #462). The scope stack is this block's own: whatever is still open
        # when the block ends is read as top-level, since the parser keeps the scope open
        # across the blocks that follow and the safe reading of an unclosed cell is that it
        # collapses to the end. A start tag's implicit close of an open sibling - a second
        # `<li>` or `<td>` - is not modelled, which can only over-collapse.
        scopes: list[str] = []
        first = len(opened)
        for name, closing in _markdown.tags(block.text):
            if name == "details":
                if not closing:
                    opened.append((level + len(scopes), block.line))
                    continue
                bounded = [depth for depth, scope in enumerate(scopes) if scope in _HTML_BOUNDARIES]
                floor = level + 1 + bounded[-1] if bounded else 0
                if opened and opened[-1][0] >= floor:
                    at, start = opened.pop()
                    if at == 0:
                        spans.append((start, block.line))
            elif name not in _HTML_SCOPES:
                continue
            elif not closing:
                scopes.append(name)
            elif name in scopes:
                while scopes:
                    popped = scopes.pop()
                    leave(level + len(scopes) + 1)
                    if popped == name:
                        break
        opened[first:] = [(level, line) for _, line in opened[first:]]

    def run(blocks: tuple[_markdown.Block, ...], level: int) -> None:
        for block in blocks:
            if isinstance(block, _markdown.Html):
                raw(block, level)
            elif isinstance(block, _markdown.Paragraph):
                read(_markdown.inline_tags(block.text), level, block.line)
            elif isinstance(block, _markdown.Heading):
                read(_markdown.inline_tags(block.text), level + 1, block.line)
                leave(level + 1)
            elif isinstance(block, _markdown.ListBlock):
                for item in block.items:
                    run(item.blocks, level + 1)
                    leave(level + 1)
            elif isinstance(block, _markdown.BlockQuote):
                run(block.blocks, level + 1)
                leave(level + 1)
            # A table cell is a scope boundary both ways, code is literal, a rule has no tags.

    run(document, 0)
    spans.extend((start, float("inf")) for at, start in opened if at == 0)
    return spans


def _on_the_page(spans: list[tuple[int, float]], line: int) -> bool:
    """Whether source line ``line`` is outside every collapsed span."""
    return not any(start <= line <= end for start, end in spans)


def _declarations(document: tuple[_markdown.Block, ...]) -> list[_AutonomyValue]:
    """`_declared_autonomy` for an already-parsed body."""
    groomed = _grooming_block(document)
    source, where = (document, "body") if groomed is None else (groomed, "grooming block")
    found: list[_AutonomyValue] = []
    # The collapsed spans are the whole document's, not the source's: a `<details>` opened above
    # the marker is still open below it, so the grooming block's lines inside it are as hidden
    # as any (Codex on #462). The marker supersedes what the body *says*, not where it is drawn.
    collapsed = _collapsed(document)
    leaves = _flat(source)
    for index, leaf in enumerate(leaves):
        block = leaf.block
        key = (
            _AUTONOMY_KEY.fullmatch(_prose(block)) if isinstance(block, _markdown.Heading) else None
        )
        if key is not None and _PROSE_QUALIFIER.match(key.group("qualifier")):
            # A heading about the field: what it heads is read for a restriction and is not
            # the field's value, so nothing here can admit and nothing is exact-checked. The
            # qualifier is read the same way, since `## Execution autonomy - maintainer decision
            # required` carries the restriction in the heading itself (Codex on #462).
            about = f"{where} heading about the field"
            found.append(_AutonomyValue(key.group("qualifier").strip(), about, scan_only=True))
            found.extend(_scan_only(_section(leaves, index), about))
        elif key is not None:
            qualifier = _normalize_autonomy(key.group("qualifier"))
            section = _section(leaves, index)
            if section:
                # The first block the page draws below the heading is the value, whatever it
                # is: a paragraph is the shape the issue forms write, and anything else - a
                # picture, a fence, a rule, a table row, a raw HTML block - fails the exact
                # match. Only an ATX heading at column zero may admit, and only on its **own
                # next paragraph** - a sibling in the same container, on the page as the
                # heading is. A value inside a block quote or a list item below the heading, or
                # written as raw HTML, or inside a `<details>` the heading sits above, is read
                # and exact-checked but is one more shape that can refuse and cannot admit: the
                # registered shape is the heading with the value as its paragraph.
                value = section[0]
                admits = (
                    not qualifier
                    and block.column == 0
                    and block.markup.startswith("#")
                    and isinstance(value.block, _markdown.Paragraph)
                    and value.siblings is leaf.siblings
                    and _plain_markdown(block, value.block)
                    and _on_the_page(collapsed, block.line)
                    and _on_the_page(collapsed, value.block.line)
                )
                found.append(
                    _AutonomyValue(
                        _prose(value.block), f"{where} heading", qualifier=qualifier, admits=admits
                    )
                )
                found.extend(_scan_only(section[1:], f"{where} heading remainder"))
            else:
                # A recognised heading with no prose below it - the last line of the body, or
                # a heading straight under it - has declared an empty value, and recording
                # nothing let an admitting bullet elsewhere carry the issue past a field that
                # names no registered value (Codex on #462). The empty value fails the exact
                # check, as the one-column table's does.
                found.append(
                    _AutonomyValue("", f"{where} heading", qualifier=qualifier, admits=False)
                )
        elif isinstance(block, _markdown.TableRow):
            key = _AUTONOMY_KEY.fullmatch(block.plain[0]) if block.plain else None
            if key is not None and _PROSE_QUALIFIER.match(key.group("qualifier")):
                about = f"{where} table row about the field"
                found.append(_AutonomyValue(key.group("qualifier").strip(), about, scan_only=True))
                found.extend(
                    _AutonomyValue(cell, about, scan_only=True) for cell in block.plain[1:] if cell
                )
            elif key is not None:
                # A row keyed `autonomy` is a declaration that cannot admit: its second cell
                # is exact-checked like a `+` bullet's value, so `human review required` -
                # no registered token, not a registered value - refuses rather than slips
                # past a token scan (Codex on #462). Any further cells are scan-only. A
                # header row is read the same way; a table whose *column* is autonomy is not
                # a shape the forms write, and failing closed on it costs one re-groom. A
                # one-column table has no value cell, and that is an empty value - still a
                # declaration, and one that fails the exact check - rather than no row.
                cells = list(block.plain[1:]) or [""]
                found.append(
                    _AutonomyValue(
                        cells[0],
                        f"{where} table row",
                        qualifier=_normalize_autonomy(key.group("qualifier")),
                        admits=False,
                    )
                )
                found.extend(
                    _AutonomyValue(cell, f"{where} table row remainder", scan_only=True)
                    for cell in cells[1:]
                    if cell
                )
    # The leads `_bullet` read, so the pass below does not read them twice. Only a paragraph
    # lead keyed like a bullet is one: a lead that is raw HTML, or a bare key over the item's
    # next paragraph, is not the bullet's declaration and was skipped here unread, so it is read
    # below like any other block the page shows (Codex on #462).
    leads: list[Any] = []
    for block in _markdown.walk(source):
        if isinstance(block, _markdown.ListBlock):
            for item in block.items:
                lead = _lead(item)
                if isinstance(lead, _markdown.Paragraph) and _keyed(_prose(lead)) is not None:
                    leads.append(lead)
                found.extend(_bullet(item, where, collapsed))
    # Any other text the page shows keyed like a bullet is a field the page shows all the same,
    # and not reading it let a restriction written that way sit beside an admitting bullet
    # unseen: a paragraph that is not an item's lead - on its own at the top level, second in an
    # item, inside a block quote - and, one shape over each time (Codex on #462), a raw HTML
    # block showing `Autonomy: maintainer decision required`, a heading carrying its value on
    # its own line, a table cell. Each is a declaration of the `+` bullet's kind: exact-checked,
    # able to refuse, never able to admit. A heading that is a key, and a row whose first cell
    # is, were read above. Raw HTML is read one rendered block at a time, as a table is read
    # one cell at a time: a `<div>` of two `<p>` is one block here and two paragraphs on the
    # page, and matching the key against the run joined read past the restriction in the
    # second (Codex on #462). What follows a keyed block in the same run is its remainder,
    # scan-only, as a row's further cells are - `<td>Autonomy:</td><td>maintainer decision
    # required</td>` is an empty value and then the restriction, and both are read. And the
    # key alone - `**Execution autonomy**` as a paragraph, over the paragraph that holds its
    # value - heads what the page draws next, as a heading does (`_BARE_KEY`).
    for index, leaf in enumerate(leaves):
        block = leaf.block
        if any(block is lead for lead in leads):
            continue
        if isinstance(block, _markdown.TableRow):
            if block.plain and _AUTONOMY_KEY.fullmatch(block.plain[0]):
                continue
            texts = [(cell, "table cell") for cell in block.plain]
        elif isinstance(block, _markdown.Heading):
            if _AUTONOMY_KEY.fullmatch(_prose(block)):
                continue
            texts = [(_prose(block), "heading line")]
        elif isinstance(block, _markdown.Html):
            found.extend(_raw_html(block, where, leaves, index))
            continue
        elif isinstance(block, _markdown.Paragraph):
            if _BARE_KEY.fullmatch(_prose(block)):
                section = _section(leaves, index)
                value = _prose(section[0].block) if section else ""
                found.append(_AutonomyValue(value, f"{where} bare key", admits=False))
                found.extend(_scan_only(section[1:], f"{where} bare key remainder"))
                continue
            texts = [(_prose(block), "paragraph")]
        else:
            continue
        for text, shape in texts:
            keyed = _keyed(text)
            if keyed is not None:
                qualifier, value, _ = keyed
                found.append(
                    _AutonomyValue(value, f"{where} {shape}", qualifier=qualifier, admits=False)
                )
    return found


def _raw_html(
    block: _markdown.Html, where: str, leaves: list[_Leaf], index: int
) -> list[_AutonomyValue]:
    """``block`` - ``leaves[index]`` - read one rendered block at a time, as the page lays it
    out; nothing here admits.

    A piece the page draws as a heading is read as a Markdown heading is: a key with whatever
    qualifier it carries, the next thing drawn its value unless that is a heading too, and a
    dash-led qualifier prose about the field, scanned itself. Any other piece keyed like a
    bullet is a declaration, and a bare key - `<p>Autonomy</p>`, `<td>Autonomy</td>` - heads
    the next thing drawn as a bare paragraph does. What follows a key is scan-only remainder,
    as a row's further cells are, up to the next heading: a heading opens a section of its own,
    as `_section` stops at one, and `<h2>Human action items</h2>` after the field refused a
    registered bullet when the remainder ran through it (Codex on #462). A `<pre>` piece is
    literal, as a fence is, wherever it falls.

    **A section is the page's, not the block's.** Markdown ends a raw block at a blank line,
    and the paragraphs after it sit under the raw heading on the page exactly as under a
    Markdown one; a heading or bare key whose section is still open when the block ends
    continues through `_section` - the next drawn leaf its value if one is still owed, the rest
    scanned - where reading the block alone left `The upload is a maintainer decision` under
    `<h2>Execution autonomy - notes</h2>` unread (Codex on #462). A keyed piece's remainder
    stays in its block, as a keyed paragraph has none. `<div><h2>Execution autonomy</h2>
    <p>maintainer decision required</p></div>` was read with the bullet's grammar only, which
    needs a colon, so the heading and the restriction under it were both unread (Codex on
    #462).
    """
    found: list[_AutonomyValue] = []
    pieces = block.shown
    shape = f"{where} raw HTML"
    # The open section's scan-only label once a heading or bare key has been read, and the
    # key whose value is still owed - its label and qualifier - when the key was the last
    # thing drawn so far.
    section: str | None = None
    owed: tuple[str, str] | None = None
    keyed_yet = False

    def settle(value: str) -> None:
        nonlocal owed
        if owed is not None:
            found.append(_AutonomyValue(value, owed[0], qualifier=owed[1], admits=False))
            owed = None

    for piece in pieces:
        if piece.tag in _HTML_LITERAL:
            settle("")
            continue
        heading = piece.tag in _HTML_HEADINGS
        key = (_AUTONOMY_KEY if heading else _BARE_KEY).fullmatch(piece.text)
        if heading and key is None:
            settle("")
            keyed_yet = False
            section = None
        if key is not None:
            settle("")
            keyed_yet = True
            qualifier = key.group("qualifier") if heading else ""
            if _PROSE_QUALIFIER.match(qualifier):
                section = f"{shape} heading about the field"
                found.append(_AutonomyValue(qualifier.strip(), section, scan_only=True))
            else:
                section = f"{shape} remainder"
                owed = (shape, _normalize_autonomy(qualifier))
            continue
        if owed is not None:
            settle(piece.text)
            continue
        keyed = _keyed(piece.text)
        if keyed is not None:
            keyed_yet = True
            qualifier, value, _ = keyed
            found.append(_AutonomyValue(value, shape, qualifier=qualifier, admits=False))
        elif keyed_yet and piece.text:
            found.append(
                _AutonomyValue(piece.text, section or f"{shape} remainder", scan_only=True)
            )
    if owed is not None or section is not None:
        rest = _section(leaves, index)
        if owed is not None:
            settle(_prose(rest[0].block) if rest else "")
            rest = rest[1:]
        if section is not None:
            found.extend(_scan_only(rest, section))
    return found


def _names(flat: str, token: str) -> bool:
    """Whether the flattened ``flat`` names the flattened ``token`` - at a word's start, and
    running on if it likes.

    No boundary at all read `nonhuman actions` as `human action` and refused a registered
    declaration whose prose only mentioned the one (Codex on #462). A boundary at both ends
    would miss the plural - `human actions`, `maintainer decisions` - that the corpus writes
    and that governs all the same. So the token must begin a word and may end mid-word. A
    separator before it is a boundary: `non-human action` flattens to `non human action` and
    names the token, which is the fail-closed reading of a negation the gate does not parse.
    """
    return re.search(rf"(?<!\w){re.escape(token)}", flat) is not None


def _plain_markdown(*blocks: Any) -> bool:
    """Whether none of ``blocks`` carries an HTML tag in its source or a Markdown image.

    The registered shapes are plain Markdown. A key or value written with a tag in it renders
    through a rule that approximates a browser - a phrasing tag vanishes, `<q>` draws quotes,
    `<del>` strikes out - and every round of review found one more tag the approximation drew
    differently from the page, each time in the direction of admitting a defaced key or a
    retracted value (Codex on #462, six reads). The rendering is kept for what it is good for,
    finding a restriction however it is dressed; it is no longer allowed to be the reason an
    issue is claimed. A comment is not a tag: it draws nothing, and `_plain` already drops it.

    An image is the same rule from the other side: the page draws a picture, and the rendering
    shows its alternative text, which a reader never sees while the picture loads (Codex on
    #462). The alternative text stays in `plain` so a restriction written there is still found.
    """
    return not any(_markdown.has_tag(block.text) or block.pictured for block in blocks)


def _drawn(block: Any) -> bool:
    """Whether the page draws anything for ``block`` - the test for a leaf a reader sees.

    Prose is drawn, and so is what shows no prose: a picture with no alternative text, a raw
    HTML block whose tags draw a widget or a picture, a code block, a rule, a table row, and a
    container - a list, a block quote, a table - whatever it holds. Only a block the page shows
    nothing for - a comment on its own lines, a paragraph that renders to nothing and carries
    neither a picture nor a tag - is not. A tag counts as drawn whatever the rendering made of
    it, for the reason `_plain_markdown` gives: the approximation may not err in the admitting
    direction. The distinction matters in two places. A heading's value is the first thing the
    page draws below it, and selecting the first *prose* leaf instead let `![](x.png)` or a
    `<details>` opening tag sit between the heading and the paragraph that then admitted as its
    own next paragraph; and an item's lead is its first drawn block, and a lead that skipped a
    nested list let the paragraph under that list admit as the item's own text (Codex on #462).
    """
    if isinstance(block, (_markdown.Paragraph, _markdown.Heading)):
        return bool(block.plain) or block.pictured or _markdown.has_tag(block.text)
    if isinstance(block, _markdown.Html):
        return bool(block.plain) or _markdown.has_tag(block.text)
    return isinstance(
        block,
        (
            _markdown.TableRow,
            _markdown.Code,
            _markdown.Rule,
            _markdown.ListBlock,
            _markdown.BlockQuote,
            _markdown.Table,
        ),
    )


def _section(leaves: list[_Leaf], index: int) -> list[_Leaf]:
    """The drawn leaves below the heading at ``index``, up to the next heading of any level.

    A heading raw HTML lays out ends the section as a Markdown one does, since the page draws
    the two alike: a raw block carrying one is cut to the pieces before it - a copy of the
    block, so a reader of the section sees nothing past the heading - and the section stops
    there (Codex on #462).
    """
    section: list[_Leaf] = []
    for leaf in leaves[index + 1 :]:
        block = leaf.block
        if isinstance(block, _markdown.Heading):
            break
        if isinstance(block, _markdown.Html):
            before = block.shown
            for at, piece in enumerate(block.shown):
                if piece.tag in _HTML_HEADINGS:
                    before = block.shown[:at]
                    break
            if before is not block.shown:
                if before:
                    plain = " ".join(piece.text for piece in before if piece.text)
                    section.append(_Leaf(block._replace(plain=plain, shown=before), leaf.siblings))
                break
        if _drawn(block):
            section.append(leaf)
    return section


def _bullet(
    item: _markdown.ListItem, where: str, collapsed: list[tuple[int, float]]
) -> list[_AutonomyValue]:
    """A list item's first drawn paragraph as a bullet declaration, and the rest of it scan-only.

    The item's declaration is its first block the page draws anything for: a comment on its own
    line above the key - `- <!-- groomed -->` over `**Autonomy:** maintainer decision required`
    - is read past, because reading the item's literal first block instead declared nothing and
    an admitting bullet beside it carried the issue while the page shows the restriction in the
    list (Codex on #462). A leading block the page does draw - a picture, a `<details>` - is the
    item's first block, and an item whose first drawn block is not a paragraph keyed autonomy
    declares nothing here; a nested list inside it is visited by the caller's walk like any
    other. ``collapsed`` is `_collapsed` of the document.
    """
    first = _lead(item)
    if not isinstance(first, _markdown.Paragraph):
        return []
    # Matched on the rendered text, not the source: an inline comment splitting the key, or a
    # `<b>` around it, is invisible on the page and must be invisible here (Codex on #462).
    keyed = _keyed(_prose(first))
    if keyed is None:
        return []
    qualifier, value, task = keyed
    # Exact-checked like any declaration, but only the registered shape - column zero, `-` or
    # `*`, no checkbox, plain Markdown, on the page - may admit. A `+`, indented, nested, quoted
    # or task-list bullet, one carrying a tag or an image, or one inside a `<details>` block can
    # refuse and can never be the reason an issue is claimed.
    registered = (
        item.column == 0
        and item.marker in "-*"
        and not task
        and _plain_markdown(first)
        and _on_the_page(collapsed, item.line)
    )
    found = [_AutonomyValue(value, f"{where} bullet", qualifier=qualifier, admits=registered)]
    rest = list(item.blocks)
    while rest and rest[0] is not first:
        rest.pop(0)
    found.extend(_scan_only(_flat(tuple(rest[1:])), f"{where} bullet remainder"))
    return found


def _lead(item: _markdown.ListItem) -> Any | None:
    """The item's first block the page draws anything for, or ``None`` for an item that draws
    nothing - the block a reader takes for the item's own text."""
    for block in item.blocks:
        if _drawn(block):
            return block
    return None


def _keyed(text: str) -> tuple[str, str, bool] | None:
    """``text`` - a paragraph's rendered text - read as a bullet-shaped declaration, or ``None``.

    Returns the normalized qualifier, the value with its whitespace collapsed, and whether a
    task-list checkbox was read past first. A task-list item draws a checkbox before its text,
    and the key is the text: `- [ ] **Autonomy:** maintainer decision required` is a restriction
    the page shows, and matching the checkbox as part of the key dropped it (Codex on #462).
    """
    task = _TASK_MARKER.match(text)
    if task is not None:
        text = text[task.end() :]
    match = _AUTONOMY_BULLET.fullmatch(text)
    if match is None:
        return None
    qualifier = _normalize_autonomy(match.group("qualifier"))
    value = " ".join(match.group("value").split())
    return qualifier, value, task is not None


def _autonomy_refusal(body: str) -> str | None:
    """Why this issue's declared autonomy bars a claim, or ``None`` when it admits.

    Fails **closed** in all three directions, because the cost is asymmetric. Refusing work an agent
    could have done wastes a claim attempt and is corrected by a re-groom. Admitting work an agent
    must not do is #246: *"a wrong first upload cannot be replaced (PyPI forbids re-uploading a
    version)"* - a permanent public artifact with wrong metadata, on a registry that will not take
    it back. So an absent declaration refuses too: an issue that never declared autonomy was never
    groomed, and silence is not consent.

    A body the parser cannot model is a fourth case and it is **not** a refusal. The structure
    reader raises on a block token it does not model and when the pinned parser itself fails
    (ADR-0066), and either means nobody has read the issue - so the answer is an error, which exits
    ``2``, and never ``ineligible``, which is a verdict about the issue and would tell every agent
    not to work it. `doctor` reports the same failure as an unreadable issue and keeps going.
    """
    try:
        document = _markdown.parse(body)
    except _markdown.MarkdownStructureError as exc:
        raise ClaimError(f"body could not be read as Markdown: {exc}") from exc
    # The latest grooming block is the source even when it is empty: an empty tuple is a block
    # that says nothing, and falling back to the body for it re-read a stale marker the block
    # supersedes (Codex on #462). Silence then refuses for what it is, below.
    groomed = _grooming_block(document)
    if _misplaced_marker(document if groomed is None else groomed):
        return (
            "carries a tether-grooming-v1 marker inside a paragraph, list item, block quote or "
            "other raw HTML, or beside anything drawn on its own line, where a grooming block "
            "cannot start, so what it supersedes cannot be read. Put the marker on its own "
            "top-level line, with "
            "nothing else on it, above the groomed text"
        )
    values = _declarations(document)

    # Refusing tokens are evaluated across every value **before** exact-match or qualifier
    # failures. Otherwise a raw conditional value can return first and hide the canonical token a
    # groomer needs to locate, which broke the hyphenated and underscored regressions from #428.
    for value in values:
        raw = value.raw.strip()
        flat = _flatten_autonomy(value.raw)
        refused = [token for token in AUTONOMY_REFUSES if _names(flat, _flatten_autonomy(token))]
        if refused:
            return (
                f"declares autonomy {raw!r} ({value.where}). It names {refused[0]!r}, so the "
                f"restrictive statement governs; only {AUTONOMY_ADMITS[0]!r} may be claimed by "
                "an agent"
            )

    declarations = [value for value in values if not value.scan_only]
    # Every declaration in the authoritative source must admit. One restrictive line is enough to
    # refuse however many admitting ones sit beside it - the same asymmetry as a single value that
    # names both, applied across the source rather than within one string. This runs before the
    # absence check so that a bullet which cannot admit still names its unregistered value.
    admits = {_flatten_autonomy(token) for token in AUTONOMY_ADMITS}
    for declaration in declarations:
        # Quote the issue verbatim; decide on the flattened form. Showing the normalized value
        # would print `needshumanaction` for a body that says `needs_human_action`.
        value = declaration.raw.strip()
        if declaration.qualifier:
            return (
                f"declares autonomy {value!r} with qualifier {declaration.qualifier!r} "
                f"({declaration.where}); that qualified key is not a registered autonomy value. "
                f"Use the bare field with {AUTONOMY_ADMITS[0]!r} before an agent claims it"
            )
        if _flatten_autonomy(declaration.raw) not in admits:
            return (
                f"declares autonomy {value!r} ({declaration.where}), which is not a registered "
                f"autonomy value; only {AUTONOMY_ADMITS[0]!r} may be claimed by an agent"
            )
    if not any(declaration.admits for declaration in declarations):
        # Same rule as the source choice above: the marker decides, not what it captured. An empty
        # block reported "its body" and sent the reader to fix the wrong half of the issue. A
        # bullet that cannot admit is absence here too: read for restrictions, not a declaration -
        # but a registered value written in such a shape is named, so the groomer rewrites the
        # shape rather than hunts for a declaration the message says is missing.
        where = "its grooming block" if _grooming_block(document) is not None else "its body"
        if declarations:
            shape = declarations[0]
            return (
                f"declares autonomy {shape.raw.strip()!r} only in a shape that cannot admit "
                f"({shape.where}: a `+`, indented, nested, quoted or task-list bullet, a key "
                "with its value in a paragraph that is not a bullet, in raw HTML, on a heading "
                "line or in a table cell, a key alone over the block below it, a table "
                "row, a heading whose value is not its own next paragraph, a key or value "
                "carrying an HTML tag or an image, or anything inside a `<details>` block). "
                "Write it as a column-zero "
                f"`-` bullet or an `## Execution autonomy` heading over {AUTONOMY_ADMITS[0]!r} "
                "as a plain paragraph"
            )
        return (
            f"declares no Execution autonomy in {where}, so it has not been groomed for agent "
            "work. An absent declaration is refused rather than assumed - add one to the issue"
        )
    return None


def _ready_marker(digest: str) -> str:
    """Render the approval marker a maintainer posts to accept a scope snapshot.

    ``version`` is emitted first to match every marker already published on a live issue. Field
    order is not semantic - ``READY_RE`` plus ``json.loads`` are order-agnostic - but a rendered
    marker that does not look like the existing corpus invites a needless diff.
    """
    return f'<!-- tether-agent-ready {{"version":1,"criteria_sha256":"{digest}"}} -->'


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


def _issue(number: int) -> dict[str, Any]:
    """Fetch an issue, refusing a pull request. Both readers of a snapshot start here."""
    status, issue = _request("GET", f"/repos/{REPO}/issues/{number}")
    if status != 200 or not isinstance(issue, dict):
        # Not a verdict: we failed to read it. A 403 or a 5xx says nothing about the issue.
        raise ClaimError(f"issue #{number} could not be read")
    if issue.get("pull_request"):
        # A verdict, and the only one raised outside `_check_eligible`: the server told us what
        # this number is, and it is not claimable work. The other five are in `_check_eligible`.
        raise IneligibleError(f"#{number} is a pull request, not an issue")
    return issue


def _check_eligible(number: int, owner: str) -> dict[str, Any]:
    issue = _issue(number)
    # These five raises are decided answers about the issue. With the pull-request refusal inside
    # `_issue`, called on the line above, they are the **six** that reach exit 3 - the count in
    # `IneligibleError`'s docstring, which is the list of record. Everything else this function can
    # raise (no token, a 403, a 5xx, a TLS refusal) is a failure to ask, and exits 2. See #315.
    if issue.get("state") != "open":
        raise IneligibleError(f"#{number} is not open")
    labels = {label["name"] for label in issue.get("labels", [])}
    if REQUIRED_LABEL not in labels:
        raise IneligibleError(f"#{number} is not {REQUIRED_LABEL}")
    assignees = [a["login"] for a in issue.get("assignees", [])]
    if [a for a in assignees if a != owner]:
        raise IneligibleError(f"#{number} is assigned to someone else")

    # What the issue says about itself, which no label can override. `status:ready` and an approval
    # marker are both applied *to* an issue; this is the issue's own statement about what finishing
    # it requires, and until #336 nothing read it - so a body saying no agent can do this work was
    # claimable anyway, on the strength of two labels that say nothing about the question (#246).
    try:
        refusal = _autonomy_refusal(issue.get("body") or "")
    except ClaimError as exc:
        # Not a verdict: the body could not be read, so the issue number travels on an error.
        raise ClaimError(f"#{number} {exc}") from exc
    if refusal is not None:
        raise IneligibleError(f"#{number} {refusal}")

    comments = _paginate(f"/repos/{REPO}/issues/{number}/comments", f"#{number} comments")
    if not _approval_binds(issue, comments, owner):
        # One message covers three situations a worker cannot tell apart - never approved, approved
        # then edited so the hash no longer binds, or a malformed marker - and only the second is
        # the worker's doing (#326). Rather than guess here, point at the command that distinguishes
        # them. Naming it in the message rather than in `AGENTS.md` costs nothing on every other
        # call, and it is read exactly when it is needed.
        raise IneligibleError(
            f"#{number} has no maintainer approval binding its current title and body; "
            "it may have been edited after approval. Run `claim.py doctor` to see whether the "
            "marker is absent, stale or malformed"
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


def _cmd_scope_hash(args: argparse.Namespace) -> None:
    """Print the digest and the ready-to-paste marker for an issue's current snapshot.

    The snapshot is read from the API rather than from a file the caller prepared, so the digest a
    maintainer posts is by construction the one ``_approval_binds`` will recompute.
    """
    issue = _issue(args.issue)
    digest = _scope_hash(issue["title"], issue["body"] or "")
    print(
        json.dumps(
            {
                "version": 1,
                "issue": args.issue,
                "criteria_sha256": digest,
                "marker": _ready_marker(digest),
            },
            indent=2,
            sort_keys=True,
        )
    )


# The activity API is eventually consistent: `POST /git/refs` can return 201 before the matching
# `branch_creation` entry is readable. Measured on the first live claim this repository ever made -
# a single read missed it, and the record was present moments later.
#
# This is a read-after-write consistency wait, NOT the polling ADR-0057 retired. That was 977
# `wait_*` calls waiting on *other agents*; this waits a few seconds on one server's own index for a
# write it has already acknowledged, and it is bounded by a constant rather than by an outcome.
# Do not remove it as "polling" without re-reading this paragraph.
GENERATION_ATTEMPTS = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)


def _await_generation(number: int) -> int | None:
    """The claim's generation, allowing the activity index to catch up.

    ``None`` when it never does.
    """
    for delay in GENERATION_ATTEMPTS:
        if delay:
            time.sleep(delay)
        generation = _generation(number)
        if generation is not None:
            return generation
    return None


def _unfenced_claim(branch: str) -> None:
    """Report a claim that exists but cannot be fenced. **Deliberately does not delete it.**

    An earlier version deleted the ref after checking its tip still equalled the SHA this call
    created it at. Codex and CodeRabbit both refused that independently, and they were right: the
    `GET`-compare-`DELETE` is not atomic, `DELETE /git/refs` accepts no expected-SHA precondition
    (``reaper.py`` documents this at its own retire path), and the base SHA is **not a claim
    identity** - a successor claiming the same issue while the default branch has not moved creates
    the ref at exactly the same SHA. So the guard can pass on a ref that is no longer ours, and the
    delete then removes a successor's live claim.

    Leaking a claim costs one reaper cycle. Deleting a successor's claim puts two workers on one
    issue, which is the single failure the mutex exists to prevent. Retaining is the correct trade,
    and it is what both reviewers asked for.

    The residual is tracked rather than hidden: if the activity record NEVER appears - as opposed to
    appearing late, which is what was actually observed - the reaper reads it as `activity-unknown`
    and *keeps* the ref rather than reclaiming it, by design (``reaper.py``: absent is unknown, not
    stale). That leaves a ref no automated path clears.

    **Decided in ADR-0064 (#303): report it and leave it to a maintainer, which is what this does.**
    Bounding the unknown would need a clock that ADR-0057 establishes does not exist for a ref with
    no activity record - commit metadata is client-settable - and a separate ownership token would
    redesign the claim identity to serve a case never observed. Only the *lag* has been seen: once,
    in seconds, on 2026-07-30. ``reaper.py``'s `activity-unknown` branch is the other half of this
    decision and says so too.
    """
    raise ClaimError(
        f"the claim ref {branch} exists but its activity record never appeared, so it cannot be "
        "fenced and this claim is not usable. The ref is NOT deleted - releasing it here could "
        "remove a successor's claim, since the base SHA is not a claim identity. Do not re-claim: "
        "the reaper resolves it once the record lands. If it never does, it needs a maintainer "
        "(#303)."
    )


def _cmd_claim(args: argparse.Namespace) -> None:
    number = args.issue
    try:
        _check_eligible(number, args.owner)
    except IneligibleError as exc:
        # ONLY this subclass. Every other `ClaimError` - no token, a 403, a 5xx, a TLS refusal -
        # propagates to `main`, which prints `error:` and returns 2. Exit 3 is a decided answer
        # about the issue, and "I could not ask" is not one; a compliant agent believes exit 3 and
        # stops, so a false verdict costs approved work (#315).
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

    generation = _await_generation(number)
    if generation is None:
        _unfenced_claim(branch)

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


#: How many `#N` references `doctor` resolves per blocked issue. Each costs one request, and a body
#: can name dozens; anything past this is counted in `not_looked_up` rather than dropped silently.
MENTION_CAP = 20

#: How long a finished pull request may sit unarmed before `doctor` mentions it. A worker that has
#: just pushed is mid-flight, not stranded, and flagging it would teach a reader to ignore the list.
UNARMED_GRACE_MINUTES = 45


def _doctor_ready(owner: str) -> list[dict[str, Any]]:
    """Every `status:ready` issue, and whether its approval marker actually admits it.

    Mode A of #326. The distinction that makes this worth printing is **absent** versus **present
    but no longer binding**, because the remedies differ: post a marker, or re-approve after an
    edit.

    It reuses `_approval_binds` and `_scope_hash` rather than re-deriving them, so it cannot
    drift from admission. That is not tidiness - ADR-0063 spent a week removing a ~145-line
    reimplementation of a counter from `scope_guard.py` that had drifted from the original three
    separate times. A reporter that disagrees with the gate it reports on is worse than none.
    """
    # The COLLECTION read fails the same way a single issue does, and must report the same way.
    # Letting it raise loses the other two modes with it - the item-level catch below closed
    # that hole one level down and left this one open.
    try:
        issues = _paginate(f"/repos/{REPO}/issues?state=open&labels={REQUIRED_LABEL}", "ready")
    except ClaimError as exc:
        return [{"collection": f"{REQUIRED_LABEL} issues", "unreadable": str(exc)}]
    out = []
    for issue in issues:
        if issue.get("pull_request"):
            continue
        number = issue["number"]
        # One unreadable comment page must not take the whole report with it. `_paginate` raises on
        # any 401, 403 or 5xx, and an uncaught raise here loses Mode B and Mode C as well - three
        # diagnostics silenced by one transient failure on an issue nobody was asking about. Same
        # rule as the unreadable blocker and the unreadable pull request: say what could not be
        # read, keep going, and never let an absence read as an answer.
        try:
            comments = _paginate(f"/repos/{REPO}/issues/{number}/comments", f"#{number} comments")
            # A body the parser cannot model is unreadable in exactly the same sense: it is a
            # fact about one issue, and the gate it mirrors reports it as an error rather than a
            # verdict (ADR-0066). Reporting `autonomy: false` here would be the verdict.
            autonomy = _autonomy_refusal(issue.get("body") or "") is None
        except ClaimError as exc:
            title = issue.get("title", "")[:60]
            out.append({"issue": number, "title": title, "unreadable": str(exc)})
            continue
        markers = [
            match
            for comment in comments
            if (comment.get("user") or {}).get("login") == owner
            for match in READY_RE.findall(comment.get("body") or "")
        ]
        if _approval_binds(issue, comments, owner):
            marker = "binds"
        elif not markers:
            marker = "absent"
        else:
            malformed = True
            for raw in markers:
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                digest = record.get("criteria_sha256") if isinstance(record, dict) else None
                if isinstance(digest, str) and HASH_RE.fullmatch(digest):
                    malformed = False
            marker = "malformed" if malformed else "stale"
        out.append(
            {
                "issue": number,
                "title": issue.get("title", "")[:60],
                "marker": marker,
                "autonomy": autonomy,
            }
        )
    return out


def _doctor_blocked() -> list[dict[str, Any]]:
    """Every held issue with each `#N` its body mentions, and that number's state.

    Mode B of #326, deliberately **raw data**. The issue asks for issues whose declared blockers
    have all closed, then concedes the parse is heuristic - the Dependencies section is prose, its
    wording varies, and #261 names no issue number at all. It also says *"a false 'unblocked' here
    is worse than a miss."*

    So this never emits the word *unblocked* and never adjudicates. It prints what the body mentions
    and what those items are, and a human decides. A report that must not be trusted is a report
    nobody reads; a report that only claims what it can see is one that can be.

    **Both holding labels, not just `status:blocked`.** #326's own table is the specification and
    three of its seven rows are `status:backlog` - #298, #257 and #258, each naming a dependency
    that has since merged. Querying one label answered a narrower question than the issue asked and
    dropped nearly half of the evidence it was raised on. The two queries are unioned and
    deduplicated by number, because a future issue carrying both labels must appear once.
    """
    issues = []
    seen: set[int] = set()
    out = []
    # Each label is read independently: one failing query must not suppress the other, and a
    # label that could not be read is named rather than silently contributing nothing.
    for label in ("status:blocked", "status:backlog"):
        query = f"/repos/{REPO}/issues?state=open&labels={label}"
        try:
            found = _paginate(query, f"{label} issues")
        except ClaimError as exc:
            out.append({"collection": f"{label} issues", "unreadable": str(exc)})
            continue
        for issue in found:
            if issue["number"] not in seen:
                seen.add(issue["number"])
                issues.append(issue)
    for issue in issues:
        if issue.get("pull_request"):
            continue
        mentioned = sorted({int(n) for n in re.findall(r"#(\d+)", issue.get("body") or "")})
        # Bounded, and the bound is REPORTED rather than silent: a body can name dozens of numbers
        # and each costs a request. A truncation nobody is told about reads as "these are all the
        # references", which is the one thing this report must never imply.
        #
        # A reference the API would not answer for is the same hazard arriving by a different door,
        # so it is reported too. Dropping it made `mentions` say nothing where it should say *I
        # could not tell*: with both lookups failing, an issue naming two blockers rendered as
        # `{"mentions": {}}`, byte-identical to an issue that names none.
        states = {}
        unreadable = []
        for ref in mentioned[:MENTION_CAP]:
            status, other = _request("GET", f"/repos/{REPO}/issues/{ref}")
            if status == 200 and isinstance(other, dict):
                # `pull_request` is present on EVERY pull request the issues API returns - open,
                # closed-unmerged and merged alike - so its mere presence says nothing about
                # whether the thing landed. Only `merged_at` does. Keying on presence reported
                # every referenced pull request as `merged`, which is the one direction this
                # function must never err in: it exists to hand a maintainer raw dependency state,
                # and a blocker reported as merged when it is open is worse than not reporting it
                # (Greptile P1 on #432).
                pull_request = other.get("pull_request") or {}
                merged = bool(pull_request.get("merged_at"))
                states[ref] = "merged" if merged else other.get("state", "?")
            else:
                unreadable.append(ref)
        record = {"issue": issue["number"], "mentions": states}
        # An empty `mentions` is ambiguous and must not be left so: it is what a body naming
        # NO issue number produces, and also what a body whose every named blocker has closed
        # would produce if the states dict were filtered. #326 names #261 as an issue whose
        # Dependencies section is prose with no `#N` at all, and says a false "unblocked" is
        # worse than a miss - so the case where nothing could be parsed says so in as many
        # words rather than rendering as an empty set an operator will read as "all clear".
        if not mentioned:
            record["unparseable"] = "no #N reference in the body"
        if unreadable:
            record["unreadable"] = unreadable
        if len(mentioned) > MENTION_CAP:
            record["not_looked_up"] = len(mentioned) - MENTION_CAP
        out.append(record)
    return out


def _doctor_unarmed(now: float) -> list[dict[str, Any]]:
    """Open pull requests that are finished and that nothing will ever merge.

    Mode C of #326. Every gate the merge asks for is satisfied - not a draft, mergeable, every check
    passing, no auto-merge armed - and the contract's last step, arming it, did not happen. All four
    fields are on the REST pull-request object, so this needs no GraphQL and no new transport.

    **A pull request whose detail call fails is reported, not dropped.** Silently skipping it makes
    an unreadable pull request indistinguishable from an armed one, which is the same defect as
    omitting an unreadable blocker in :func:`_doctor_blocked`: the report reads as *nothing is
    wrong here* when the truth is *nobody looked*. The remedy differs too - a transport failure is
    retried, a genuinely unarmed pull request is armed - so the two must not print alike.

    **`draft` and `state` are re-read from the detail response**, not trusted from the list page.
    The list is a snapshot that can be minutes old on a busy repository, so a pull request that has
    since been closed or returned to draft would otherwise be reported as stranded.

    **`mergeable_state == "clean"` is what proves the review conversations are resolved**, and that
    is a property of this repository's ruleset rather than of GitHub. `main-baseline` sets
    ``required_review_thread_resolution: true``, so an unresolved thread makes the state ``blocked``
    even while ``mergeable`` still reads ``MERGEABLE`` - the two answer different questions, and
    only the second is about conflicts. Verified live on #432: four unresolved threads,
    ``mergeable=MERGEABLE``, ``mergeStateStatus=BLOCKED``. If that ruleset setting is ever turned
    off this check stops carrying that meaning, which is why the dependency is written down here.
    """
    out = []
    try:
        summaries = _paginate(f"/repos/{REPO}/pulls?state=open", "open pull requests")
    except ClaimError as exc:
        return [{"collection": "open pull requests", "unreadable": str(exc)}]
    for summary in summaries:
        # No filtering from the summary at all - the detail response decides everything. Skipping
        # here on the list's `draft` left the staleness only half closed: a pull request marked
        # ready since the list was built was dropped before anything read its current state, so the
        # very pull request most likely to be finished-and-stranded was the one never diagnosed.
        # The cost is one detail call per open pull request, which is what a correct answer costs.
        status, pr = _request("GET", f"/repos/{REPO}/pulls/{summary['number']}")
        if status != 200 or not isinstance(pr, dict):
            out.append({"pr": summary["number"], "unreadable": status})
            continue
        if pr.get("draft") or pr.get("state") != "open":
            continue
        if pr.get("auto_merge") or pr.get("mergeable_state") != "clean":
            continue
        # `calendar.timegm`, not `time.mktime`: GitHub stamps UTC and `mktime` reads a struct as
        # LOCAL time, which shifts every age by the machine's offset and can make a stale pull
        # request look fresh. Found by the Mode C test on a UTC-negative machine.
        updated = calendar.timegm(time.strptime(pr["updated_at"], "%Y-%m-%dT%H:%M:%SZ"))
        idle = (now - updated) / 60
        if idle < UNARMED_GRACE_MINUTES:
            continue
        out.append({"pr": pr["number"], "idle_minutes": int(idle), "head": pr["head"]["sha"][:8]})
    return out


def _cmd_doctor(args: argparse.Namespace) -> None:
    """Report why the claimable queue looks the way it does. **Reports; never writes.**

    #326: an autonomous session landed #318 and then had nothing left to claim while 52 issues
    were open. The queue was empty for bookkeeping reasons and nothing detected it - an
    over-blocked issue costs nothing visible, so nothing ever prompts a re-check.

    Filed as a scheduled workflow; built as a subcommand instead. Every remedy is maintainer
    authority - posting an approval marker, promoting a label, arming someone else's merge - so
    nothing acts while nobody is looking, and a report published thirty minutes ago into a
    workflow summary is strictly worse, at the moment someone looks, than one that answers now.
    It also adds no file, no workflow and no schedule to a layer being shrunk (ADR-0064).

    *Report, never act* is a property of this subcommand rather than of the module: `claim`,
    `release` and `reserve-adr` all write - the last creates a ref - so the guarantee has to be
    about `doctor` itself. Every call it makes is a GET, and `test_doctor_writes_nothing` asserts
    no POST, PATCH, PUT or DELETE is issued, which is what makes it checkable rather than claimed.
    """
    report = {
        "version": 1,
        "ready": _doctor_ready(args.owner),
        "blocked": _doctor_blocked(),
        "unarmed": _doctor_unarmed(time.time()),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claim an issue by atomic ref creation.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    agent_id = subparsers.add_parser("agent-id", help="print a public-safe worker identity")
    agent_id.add_argument("--vendor", choices=VENDORS, required=True)
    agent_id.set_defaults(func=_cmd_agent_id)

    scope_hash = subparsers.add_parser(
        "scope-hash", help="print the approval digest and marker for an issue's current snapshot"
    )
    scope_hash.add_argument("--issue", type=int, required=True)
    scope_hash.set_defaults(func=_cmd_scope_hash)

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

    doctor = subparsers.add_parser(
        "doctor", help="report why the claimable queue looks the way it does; writes nothing"
    )
    doctor.add_argument("--owner", default="bioedca")
    doctor.set_defaults(func=_cmd_doctor)
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
