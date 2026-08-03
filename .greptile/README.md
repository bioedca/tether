<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Greptile is metered, and this directory stops it firing unasked

`config.json` sets **`"skipReview": "AUTOMATIC"`**, so Greptile never reviews a pull request on its
own. It still reviews when asked:

```
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

**It does not enforce a budget.** Nothing stops a seventeenth `@greptileai` except not typing it.

## Once this has merged, this repository no longer needs the account-level limit

`skipReview: "AUTOMATIC"` is strictly stronger than a file-change limit: it suppresses *every*
automatic review here regardless of size, including the one-file PRs the limit lets through. So the
account-wide `fileChangeLimit` can be relaxed for the other repositories' sake as soon as each of
them carries its own `.greptile/` — it is a stop-gap for the window before that, not a permanent
control. Until then, leaving it at `1` costs this repository nothing.

## Precedence

`.greptile/` **overrides** a root `greptile.json`, which is the legacy form — do not add one, it
would be silently ignored. Dashboard settings sit below this directory; organization-enforced rules
sit above it and cannot be overridden here.

## Reading the balance before spending

Greptile publishes no usage API, so the only programmatic reading is counted from the GitHub side:

```
python3 .agents/bin/greptile_usage.py
```

It reports credits used this month across **all three** repositories on the seat, because a
per-repository number cannot tell you what is left. It errs toward reading low — a TREX review costs
3 and is counted as 1 — so reconcile against Settings → Usage before treating it as authoritative.

The review lane this serves is specified in [`docs/agents/review.md`](../docs/agents/review.md).
