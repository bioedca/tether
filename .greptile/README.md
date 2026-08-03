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
branch of the PR*, so a branch that predates this directory still auto-fires. The auto-trigger
toggle at `app.greptile.com` is the only cover for those, and it is a maintainer action — nothing in
this repository can set it.

**It does not enforce a budget.** Nothing stops a seventeenth `@greptileai` except not typing it.

## Precedence

`.greptile/` **overrides** a root `greptile.json`, which is the legacy form — do not add one, it
would be silently ignored. Dashboard settings sit below this directory; organization-enforced rules
sit above it and cannot be overridden here.

The review lane this serves, the seat-wide credit counter, and this directory's review
`instructions` are being added separately — see #384.
