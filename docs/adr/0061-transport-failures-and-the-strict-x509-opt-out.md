<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0061 — A transport failure is not a scope verdict, and strict X.509 conformance is opt-out

- **Status:** accepted; reverses a non-goal of [#315](https://github.com/bioedca/tether/issues/315)
- **Date:** 2026-08-03
- **Deciders:** bioedca
- **PRD anchor:** §12.2–§12.5
- **Milestone:** M11 — Agent-swarm infrastructure

> These header fields are copied verbatim into `docs/adr/README.md` by `scripts/gen_adr_index.py`,
> which truncates a long cell mid-sentence. Keep them short; the reasoning belongs below.

**On the status.** The exit-code half implements #315 as written. The opt-out half **reverses a
non-goal that issue originally stated** — "no clearing of `VERIFY_X509_STRICT` in shipped code" —
which is why it is recorded here rather than applied quietly. That reversal is the maintainer's,
amended onto #315's *Expected behavior* on 2026-08-03 and reconfirmed in session the same day; the
two interlocks it attaches (literal `1` only, and an announcement on stderr) are theirs, and are
implemented and tested here. The maintainer's sign-off is the merge of the pull request carrying
this record.

**On the PRD anchor.** No runtime module changes: `claim.py` is CI/agent tooling, not a §4.2
component.

## Context and problem statement

`.agents/bin/claim.py` is the mutex every agent takes before working an issue. Two defects in its
failure handling combined into a silent one.

**A transport failure was reported as an eligibility verdict.** `_cmd_claim` wrapped
`_check_eligible` in a blanket `except ClaimError`, printed `ineligible:` and exited `3` — the code
`AGENTS.md` defines as *ineligible, do not work it*. Every error inside that call, including a
socket or TLS failure raised by `_request`, came back as an answer **about the issue**. An approved,
`status:ready` item with a binding marker was reported to an agent as unapproved, and a compliant
agent correctly walked away from work it should have done. Measured:

```
$ python .agents/bin/claim.py claim --issue 222 --vendor claude
ineligible: GitHub API is unreachable
exit=3
```

while `gh issue view 222` succeeded from the same shell in the same second.

**And "unreachable" named the wrong cause.** `urllib` wraps `ssl.SSLCertVerificationError` in
`URLError`, so a certificate that was found, read and *refused* rendered identically to a host that
never answered.

The underlying refusal is version-dependent, not host-dependent. **CPython 3.13 turned
`ssl.VERIFY_X509_STRICT` on by default in `create_default_context()`**, and under it OpenSSL rejects
a CA certificate whose Basic Constraints extension is not marked critical. TLS-inspecting proxies
routinely issue exactly such a CA. Same machine, same host, same second:

| interpreter | `VERIFY_X509_STRICT` default | `urlopen("https://api.github.com/zen")` |
|---|---|---|
| 3.14.0 | `True` | `CERTIFICATE_VERIFY_FAILED` |
| 3.12.3 | `False` | `200` |

`pyproject.toml` declares `requires-python = ">=3.11"` and Windows is this repository's primary
development platform, so 3.13 and 3.14 are supported interpreters that cannot reach the API at all.
`SSL_CERT_FILE` does not help: the CA is not missing, it is present and judged malformed.

## Decision

**1. `TransportError(ClaimError)`, caught ahead of `ClaimError` in `_cmd_claim` and re-raised.**
A subclass rather than a sibling, so every other `except ClaimError` in the file stays correct and
the failure still reaches `main`'s `error:` / exit `2` by the path that already existed. Exit `3` is
now reachable only by the four decided answers: not open, not `status:ready`, assigned elsewhere, or
no approval binding the current snapshot.

**2. The message names the cause it observed**, distinguishing certificate verification from an
unreachable host, and interpolating only path-free fields (`verify_message`, `strerror`, the
exception's class name) so `ClaimError`'s promise that its message carries no path survives a
misdirected `SSL_CERT_FILE`.

**3. `TETHER_ALLOW_NONSTRICT_X509=1` clears `ssl.VERIFY_X509_STRICT` and nothing else.**
`verify_mode` stays `CERT_REQUIRED` and `check_hostname` stays `True`: the chain is still verified
and the host is still authenticated. It restores pre-3.13 *conformance* checking — the behaviour
3.12 has today — rather than weakening trust. Two interlocks come with it, both from the
maintainer's amendment:

- **Only the literal `1` arms it.** An interlock that fires on anything truthy is not an interlock;
  `true`, `yes` and `TRUE` are the spellings a shell profile picks up by habit, and each would
  otherwise relax a TLS check on a path that carries a GitHub token.
- **It announces itself on stderr**, once per process. A process that has quietly stopped enforcing
  a check is indistinguishable in a log from one that never needed to. Once per process rather than
  per request because `_paginate` can make twenty calls, and a warning repeated twenty times is one
  nobody reads.

## Why the opt-out, given #315 ruled it out

#315's non-goal was written to forbid the blunt instruments — `PYTHONHTTPSVERIFY=0`,
`_create_unverified_context`, `CERT_NONE` — and those stay forbidden, asserted against the parsed
source rather than promised in prose. What it also forbade, and the maintainer has now reversed, is
the narrow one.

The alternative the issue assumed was "run the tooling under 3.12". That is a real workaround and it
is what the WSL lane does by construction. But it is not available to the native lane, which is
where `codex` runs and where 3.14 is the default interpreter; leaving it there means half the swarm
cannot claim, revalidate or reserve an ADR on this machine. A documented, per-machine, explicitly-set
variable is a decision an operator makes once and can audit; an undocumented "use a different
interpreter" is a decision every agent re-derives and gets wrong.

Three properties make it defensible, and all three are load-bearing:

- **Not the default.** Absent the variable the context is CPython's own, unmodified.
- **Not silent.** There is no retry-on-failure path. The strict failure is fatal, and it prints the
  cause and this variable as the remedy; a human sets it or does not.
- **Not a verification bypass.** It is the single narrowest flag that restores 3.12's behaviour.
  `claim.py` sends a GitHub token, so a context that skipped chain or hostname verification would
  hand that token to whoever answered — which is why those two are asserted on, in both the
  opted-in and default cases.

## Consequences

- Native Windows can reach the API again: `scope-hash --issue 382` returns a digest byte-identical
  to the WSL lane's under 3.14 with the variable set.
- Exit `3` becomes trustworthy. It was previously a union of "ineligible" and "could not ask", and
  agents are contractually required to obey it.
- A machine whose proxy CA is malformed now has a supported answer that is written down, instead of
  an oral tradition about interpreter versions.
- The opt-out is a standing, if narrow, reduction in certificate conformance checking wherever it is
  set. It is per-machine and per-environment, never committed, and it is named in `CLAUDE.md`
  §This machine so its presence here is discoverable rather than folklore.

## Alternatives considered

Recorded here so they are not re-litigated. The first three are the maintainer's, from the
amendment on #315; the rest were weighed while implementing.

- **`truststore`** (delegate verification to the OS trust store, which does not apply the strict
  conformance rule). Rejected: it adds a runtime dependency to the claim path, and `.agents/bin/*.py`
  are stdlib-only precisely to avoid that — it would have to land in three isolated dependency locks
  before an agent could take a mutex.
- **Pin the agent tooling to CPython < 3.13.** Rejected: invisible to the 3-OS matrix, it evaporates
  the moment WSL ships 3.13, and `requires-python` is `>=3.11`. It is also out of scope on #315.
- **Route `_request` through `gh api`.** Rejected: a process fork per request across up to twenty
  pagination pages, and a wholesale replacement of a tested transport to work around one flag.
- **Report honestly and stop (the issue as originally written).** Correct as far as it goes, but it
  leaves the native lane unable to claim, revalidate or reserve an ADR at all — half the swarm.
- **Ship a CA bundle or honour `SSL_CERT_FILE`.** Does not work, and this is measured rather than
  assumed: the CA is *found* and refused for a conformance defect. The error is `Basic Constraints of
  CA cert not marked critical`, not `unable to get local issuer certificate`. Supplying it again
  changes nothing.
- **Retry without strict verification after a strict failure.** Rejected: that is exactly the silent
  weakening the non-goal was written against, and it would make the relaxation invisible.
