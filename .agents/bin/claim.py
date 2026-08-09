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
import hashlib
import http.client
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


def _graphql(query: str, variables: dict[str, Any], what: str) -> dict[str, Any]:
    """Run one GraphQL query and return ``data``. Raises ``ClaimError`` on anything else.

    A second query path exists because a few facts are simply **not on REST**. The first is review
    *thread resolution* (#393): ``/pulls/{n}/comments`` carries the comment but never whether its
    thread was resolved, so the only mechanism the contract prescribes for a non-blocking finding —
    reply, resolve, link a follow-up, do not push — was invisible to ``triage.py``, which therefore
    kept owing an AMEND forever.

    Deliberately thin. It shares ``_token`` and ``_ssl_context`` with :func:`_request`, so the
    ADR-0061 transport behaviour — including ``TETHER_ALLOW_NONSTRICT_X509`` and the transport-vs-
    verdict distinction #388 turns on — is the same code and not a second copy of it.

    **GraphQL answers 200 with an ``errors`` array**, so an unchecked caller reads a failed query as
    an empty result. That is the fail-open direction for every use this has, and it is why this
    raises rather than returning a status: the caller decides what an unreadable answer means, and
    cannot do that if the failure looks like data.
    """
    status, payload = _request("POST", "/graphql", {"query": query, "variables": variables})
    if status != 200 or not isinstance(payload, dict):
        raise ClaimError(f"{what} could not be read (HTTP {status})")
    if payload.get("errors"):
        messages = "; ".join(
            str(error.get("message", error))
            for error in payload["errors"]
            if isinstance(error, dict)
        )
        raise ClaimError(f"{what} could not be read: {messages or 'GraphQL reported an error'}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ClaimError(f"{what} returned no data")
    return data


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

#: Any of these anywhere in the declared value refuses it, even when it *opens* with an admitting
#: token. A value like "agent-can-do-alone for the drafting; the membership question is a
#: maintainer decision" declares two things, and the safe reading of a split declaration is the
#: restrictive one.
AUTONOMY_REFUSES = (
    "maintainer decision",
    "maintainer input",
    "human action",
    "needs-maintainer-input",
    "needs-human-action",
    "human-executed",
    "external/human",
)

_AUTONOMY_HEADING = re.compile(
    r"^\#{1,6}[ \t]*(?:execution[ \t]+)?autonomy[ \t]*$\n(.*?)(?=^\#{1,6}[ \t]|\Z)",
    re.M | re.S | re.I,
)
_AUTONOMY_BULLET = re.compile(
    r"^[-*][ \t]*\*\*[ \t]*(?:execution[ \t]+)?autonomy(?P<qualifier>[^:*]*)[:*]*\*\*"
    r"[: \t]*(?P<value>.+)$",
    re.M | re.I,
)
_GROOMING_BLOCK = re.compile(r"<!--[ \t]*tether-grooming-v1[ \t]*-->(.*?)(?=<!--|\Z)", re.S)
_MARKUP = re.compile(r"[`*_]+")


def _normalize_autonomy(value: str) -> str:
    """Lowercase, strip Markdown emphasis, trim the ends, drop a trailing period.

    Feeds :func:`_flatten_autonomy`, which is what comparison uses. Internal whitespace is left
    alone here; the flattener collapses it.
    """
    return _MARKUP.sub("", value).strip().rstrip(".").strip().lower()


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
    `AUTONOMY_REFUSES` altogether - "needs maintainer sign-off", "human review required" - is still
    admitted, because this compares against a list rather than reading English. The list is the
    guarantee; the flattener only stops punctuation defeating it. Every phrasing the corpus actually
    uses is on the list, which is why the list is not speculatively widened.

    **Separators are widened before the markup strip, not after.** `_MARKUP` removes `_` because it
    is Markdown emphasis, so running it first turns `needs_human_action` into `needshumanaction` -
    one word, matching nothing, admitted. Widening first makes it `needs human action`.
    """
    widened = re.sub(r"[\s_/-]+", " ", value)
    return re.sub(r"\s+", " ", _normalize_autonomy(widened)).strip()


def _declared_autonomy(body: str) -> list[tuple[str, str]]:
    """Every Execution-autonomy declaration in the authoritative source, as ``(raw value, where)``.

    The value is returned **unnormalized**. `_normalize_autonomy` strips `_` as Markdown
    emphasis, so normalizing here would glue `needs_human_action` into one word before
    `_flatten_autonomy` could widen it - the caller needs the raw text to do both.

    The corpus renders this several ways - an `## Execution autonomy` heading, a shorter
    `## Autonomy` one, a ``- **Autonomy:**`` bullet, and a ``<!-- tether-grooming-v1 -->`` block -
    and they do not always agree, so the order below is the decision.

    **A grooming block wins**, because those blocks exist to restate readiness after the body above
    them went stale. Reading the body first would let a superseded value admit work the grooming
    pass had already restricted.

    **Within one source there is no such precedence, so every declaration in it is returned and the
    caller refuses if any of them does.** This returned only the first match and looked for bullets
    before headings, which meant a body whose `## Execution autonomy` section read `maintainer
    decision required` was admitted outright when any bullet elsewhere read `agent-can-do-alone` -
    the heading was never reached. Nothing justifies a bullet outranking a heading the way a
    grooming block outranks a stale body; two disagreeing declarations in one source mean the issue
    is not clearly groomed, which is the case this gate exists to refuse. It is the same rule
    `_autonomy_refusal` already applies to a single value that names both.

    **`Autonomy after unblock` declares autonomy like any other spelling.** It is tempting to read
    it as conditional and refuse, but that conflates two questions. *What kind of work is this* is
    what this function answers; *is it still blocked* is what the `status:` label answers, and #336
    scopes dependency parsing out of this check deliberately. Refusing on the qualifier produced a
    measured false negative: #214's own grooming block reads `**Status:** unblocked` two lines above
    `**Autonomy after unblock:** agent-can-do-alone`, so the condition it names is already met and
    refusing it would bar work that is genuinely ready. The qualifier is kept in the returned label
    so a refusal message can still quote where the value came from.
    """
    for source, where in ((_grooming_section(body), "grooming block"), (body, "body")):
        if not source:
            continue
        found: list[tuple[str, str]] = []
        for match in _AUTONOMY_BULLET.finditer(source):
            qualifier = _normalize_autonomy(match.group("qualifier"))
            label = f"{where} bullet" + (f" ({qualifier})" if qualifier else "")
            found.append((match.group("value"), label))
        # `finditer`, not `search`: a body can carry the heading twice, and taking only the first
        # hid a second one that restricted the issue behind an admitting first one - the same
        # first-match-wins defect this function was just fixed for, one level down.
        for heading in _AUTONOMY_HEADING.finditer(source):
            lines = [ln.strip() for ln in heading.group(1).split("\n") if ln.strip()]
            if lines:
                found.append((lines[0], f"{where} heading"))
        if found:
            return found
    return []


def _grooming_section(body: str) -> str:
    """The text of a ``tether-grooming-v1`` block, or ``""``. Joined when there are several."""
    return "\n".join(m.group(1) for m in _GROOMING_BLOCK.finditer(body))


def _autonomy_refusal(body: str) -> str | None:
    """Why this issue's declared autonomy bars a claim, or ``None`` when it admits.

    Fails **closed** in all three directions, because the cost is asymmetric. Refusing work an agent
    could have done wastes a claim attempt and is corrected by a re-groom. Admitting work an agent
    must not do is #246: *"a wrong first upload cannot be replaced (PyPI forbids re-uploading a
    version)"* - a permanent public artifact with wrong metadata, on a registry that will not take
    it back. So an absent declaration refuses too: an issue that never declared autonomy was never
    groomed, and silence is not consent.
    """
    declarations = _declared_autonomy(body)
    if not declarations:
        return (
            "declares no Execution autonomy, so it has not been groomed for agent work. An absent "
            "declaration is refused rather than assumed - add one to the issue"
        )
    # Every declaration in the authoritative source must admit. One restrictive line is enough to
    # refuse however many admitting ones sit beside it - the same asymmetry as a single value that
    # names both, applied across the source rather than within one string.
    for raw, where in declarations:
        # Quote the issue verbatim; decide on the flattened form. Showing the normalized value
        # would print `needshumanaction` for a body that says `needs_human_action`.
        value = raw.strip()
        flat = _flatten_autonomy(raw)
        refused = [token for token in AUTONOMY_REFUSES if _flatten_autonomy(token) in flat]
        admits = any(flat.startswith(_flatten_autonomy(token)) for token in AUTONOMY_ADMITS)
        if refused and admits:
            return (
                f"declares autonomy {value!r} ({where}). That is a split declaration - it also "
                f"names {refused[0]!r} - and the restrictive half governs, so it needs a maintainer"
            )
        if refused or not admits:
            return (
                f"declares autonomy {value!r} ({where}); only {AUTONOMY_ADMITS[0]!r} may be "
                "claimed by an agent"
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
    refusal = _autonomy_refusal(issue.get("body") or "")
    if refusal is not None:
        raise IneligibleError(f"#{number} {refusal}")

    comments = _paginate(f"/repos/{REPO}/issues/{number}/comments", f"#{number} comments")
    if not _approval_binds(issue, comments, owner):
        raise IneligibleError(
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
    stale). That leaves a ref no automated path clears. See #303.
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
