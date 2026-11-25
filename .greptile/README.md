<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Greptile is metered, and this directory stops it firing unasked

`config.json` sets **`"skipReview": "AUTOMATIC"`**, so Greptile never reviews a pull request on its
own. It still reviews when asked:

```text
@greptileai review this draft
```

## Why

Greptile Pro includes **50 credits per seat per month**, and its billing rule is exact:

> Billing counts completed reviews, not PRs. Each finished review consumes one credit, charged to
> the PR author. Skipped reviews don't count.

A TREX review costs **3**. This account has **one paid seat**, shared across `tether`, `Yeliztli`
and `tbox-finder`, so all three draw on the same 50.

Left on automatic, Greptile spends a credit on every PR the moment it opens. On 2026-08-03 it spent
two in one day across two repositories, neither requested. Milestones M14–M17 alone carry ~40
issues; one auto-review each would consume the seat's whole month on this repository twice over.

## Two things this cannot do

**It does not cover branches cut before it landed.** Greptile reads configuration from the *source
branch of the PR*, so a branch that predates this directory still auto-fires. **There is no
auto-trigger off switch** — automatic review is suppressed by configuration, not by a toggle, which
is why `skipReview` lives here rather than in a dashboard setting.

The cover for those older branches is the account-level **file-change limit**, set to `1` on
2026-08-03: *"Greptile skips PRs over this file count unless someone explicitly tags
`@greptileai`."* It applies to every repository on the seat immediately, without waiting for a
config to land in each. Note the comparison is **exceeding** — a pull request touching exactly one
file is still reviewed automatically, so the limit is a broad net, not a seal.

**It does not enforce a budget.** Tether's limit is its share of **45 credits a cycle** from the
17th (ADR-0099) plus what is left of a one-time grant of 50 (ADR-0142), and nothing stops a review
past it except not typing the request. A code pull request asks only while
`.agents/bin/greptile_usage.py --fail-over-share` exits 0.

## Once this has merged, this repository no longer needs the account-level limit

`skipReview: "AUTOMATIC"` is strictly stronger than a file-change limit: it suppresses *every*
automatic review here regardless of size, including the one-file PRs the limit lets through. So the
account-wide `fileChangeLimit` can be relaxed for the other repositories' sake as soon as each of
them carries its own `.greptile/` — it is a stop-gap for the window before that, not a permanent
control. Until then, leaving it at `1` costs this repository nothing.

## What a spent credit is asked to look for

Since a review here is never free, `instructions` aims it at what this repository is least able to
recover from — a silently wrong scientific number, a non-additive HDF5 schema change without an ADR
and version bump, a secret or private path, unlicensed data, a defect in the single-writer project
lock — and asks for one well-evidenced blocking finding over many stylistic ones. `strictness: 2`
is the matching dial.

One instruction earns its length. A PR usually *cites* the frozen oracles under `schema/` rather
than editing them, and reviewing those frozen values from scratch wastes the credit — so they are to
be taken as given. But that reverses when a PR **changes** a measured fixture, a reference value or a
tolerance: there the value and its assertion move together, so the test proves nothing about the
value, and the derivation is exactly what needs scrutiny. Weakening a tolerance to make an
implementation pass is the failure this repository most needs caught, and it is invisible to CI by
construction.

## Precedence

`.greptile/` **overrides** a root `greptile.json`, which is the legacy form — do not add one, it
would be silently ignored. Dashboard settings sit below this directory; organization-enforced rules
sit above it and cannot be overridden here.

## Reading the balance before spending

Greptile publishes no usage API, so the only programmatic reading is counted from the GitHub side:

```sh
python3 .agents/bin/greptile_usage.py
```

It reports credits used this cycle across **all three** repositories on the seat, because credits are
billed per seat and a per-repository number cannot say what is left. For Tether it also prints the
one-time grant used and left, and the limit this cycle.

It is a proxy, not an invoice, and it can err in **both** directions: a TREX run that an
account rule started costs 3 credits it does not see (a run asked on demand is counted at 3), while a re-triggered review is counted twice on the undocumented assumption that
it bills twice. Reconcile against Settings → Usage before treating the remaining figure as
authoritative. What it will not do is read *low by accident* — a repository or a pull request it
cannot read makes the total **unknown** rather than silently small.

The review lane this configuration serves is specified in [`AGENTS.md`](../AGENTS.md) §Review and
decided in [ADR-0064](../docs/adr/0064-the-agent-layer-coordinates-writers-not-reviews.md). In
short: Greptile is the metered gate of a **code** pull request (ADR-0142) — asked after Codex, on a
diff that is already green. A docs-only or test-only pull request does not ask it, and a code pull
request with no credit left takes CodeRabbit instead: not asked, never without a record, since the
PR body carries the reason as §Review words them. Draft-first is the default rather than a
precondition: a pull request opened ready with its reason recorded is asked there, at the same point
in the lane and on the same green checks. What does not change either way is the order — Codex
reads the diff before any metered provider does. **One review**, and a review is not always one credit — a standard review costs one, a TREX review three (see above). The cap of **at most one completed
review per metered provider** in `AGENTS.md` §Review applies here as everywhere. A request that produced no review — a throttle, a quota refusal, a failed run — is not an
ask and spends nothing. When Greptile's cap is spent with no finding left outstanding, an unmetered
**posted** Codex review closes it — §Review carries the conditions on that, and this page states
none of them.

**Which pull requests are code** (ADR-0142): `python3 .agents/bin/greptile_usage.py --kind <PR>`
reads the changed files. A pull request that changes `AGENTS.md`, `CLAUDE.md` or `docs/PRD.md` is
code; one whose files are all Markdown, under `docs/` or under `tests/` is docs/test-only; every
other is code. The Risk line routes nothing. Greptile no longer stands in for a refused CodeRabbit,
and the script counts a TREX run asked on demand at three; add three for any TREX run an account
rule started (see "Reading the balance before spending").
