<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# The review gate

These rules are part of the agent contract. `AGENTS.md` points here and grants no merge authority on
its own: **an agent that has not read this page may not request a review or merge a pull request.**
Authorization still has to come from `AGENTS.md` — merge is under explicit per-PR authority, and
nothing on this page supplies it.

- Record `low`, `standard`, or `high` in the PR with a reason. Risk may only increase. The authoring
  agent is never the only reviewer.
- **Open the PR as a draft, and do the work there.** Every required check runs on a draft, so the
  diff reaches fully green before any metered provider is asked. Greptile skips drafts by default;
  this is what makes the sequence below affordable rather than a policy nobody can keep.

### The lane, cheapest provider first

Each step begins only when the one before it has nothing blocking left.

1. **Draft. Codex, as many rounds as it takes.** Ask, answer, push, ask again, until Codex surfaces
   nothing blocking — then answer that last round too. **The two-round cap does not apply here**:
   Codex is the unmetered lane, and throttling it bought nothing but slower convergence.
2. **Draft. Optionally spend one Greptile credit** — `@greptileai review this draft` — if the seat
   has budget. Answer everything it raises. **Exhaustion never blocks**: a worker that cannot spend
   records *"Greptile: no credits this month"* and moves on. It is a strengthening step, not a gate.
3. **Mark ready for review. The two-round cap starts here**, and from here it governs the metered
   providers only.
4. **CodeRabbit is the last gate before merge**: at least one CodeRabbit review with **no actionable
   comments**. Nothing merges without it.

**You must ask — no provider self-fires here.** One request per provider per round; a provider that
was not asked **has not declined**. Author-side or local output, and a status-only result, never
satisfy this gate.

- **Metered providers, and the seat they share.** Greptile bills **one credit per completed review,
  charged to the PR author**, from **50 per seat per month** — and this account's one seat is shared
  across `tether`, `Yeliztli` and `tbox-finder`. A TREX review costs **3**. Before spending one, read
  the balance; after a month's worth of PRs, expect it to be gone:

  ```
  python3 .agents/bin/greptile_usage.py
  ```

  `.greptile/config.json` sets `skipReview: "AUTOMATIC"` so nothing fires unasked, but it is read
  from the PR's *source branch* — a branch cut before it landed still auto-fires, and the dashboard
  toggle is the only cover for that. **Copilot is budgeted the same way and is advisory only**: it
  never satisfies a leg, and a quota-limit message from it is recorded as *did not review*, never as
  a pass. CodeRabbit runs at a higher cadence but is not free either — it is subject to fair use, and
  its adaptive limit can suppress a review **silently while its status check still goes green**, so a
  green CodeRabbit check with no review body is *not* step 4 satisfied.
- **Material change.** Evidence survives a non-material push, so answering findings never restarts the
  gate. *Material*: executable code, scientific claims, data, schema, locks, CI/release config, and
  governance text (`AGENTS.md`, `CONTRIBUTING.md`, `docs/PRD.md`, `docs/adr/**`, `.agents/**`, and
  **`docs/agents/**`** — these pages are the contract, not commentary on it). *Non-material*: a clean
  `main` merge/rebase, formatting, comment/docstring edits, ADR renumbering. A material push re-arms
  the review and grants **no extra round**.
- **Severity floor — the severity axis only.** Blocking: CodeRabbit `Critical`/`Major`, Codex `P1`,
  and — whatever the label — a secret or private path, raw or unlicensed data, a weakened frozen
  oracle or tolerance, a §5 skeleton change without an ADR and version bump, any CodeQL or
  `secret-scan` alert, or **a finding that falsifies a claim this PR introduces**. CodeRabbit's
  *domain* label and its `cr-indicator-types:` marker are **not** severities and never promote a
  finding; `potential_issue` sits on `🟡 Minor` and `🟠 Major` alike. Everything else is non-blocking:
  one follow-up issue per PR, reply `Deferred: … Tracked in #N` — never at an issue that does not
  exist — and resolve the thread. **Never fix a non-blocking finding in the PR**: that is scope
  breach, not diligence.
- **Two rounds after the draft, issued by the launcher, not requested by you.** One round = a review
  at a declared-final green head plus the answer to its blocking findings. **The cap counts only
  rounds taken once the PR is ready for review, and only against metered providers** — Codex
  iteration while the PR is still a draft is uncounted, which is the point of doing the work there.
  `agent:round-N` and `agent:review-capped` therefore mean *post-draft, metered* rounds, and every
  AMEND is a fresh short-lived session whose task text the launcher injects with an explicit
  `ROUND = N of 2`; past the cap it injects none, so no worker ever holds authority for a third. At
  the cap, safety-class findings escalate to the maintainer and the rest become follow-ups.
  Stop-list, not judgement: **never a review request while `agent:review-capped` is present**.
- **Capability is not quota, and the two fail differently.** A selected provider reporting nothing to
  review at the head it read satisfies its leg — including a Codex 👍 reaction, its documented form
  of "no suggestions". Quote the provider, never the author or another commenter. *Exhaustion* is not
  that: a provider with no budget left has **not** reviewed, and saying so is the honest record.
  Greptile out of credits is expected and skippable (step 2). **CodeRabbit unavailable freezes the
  PR** — it is the last gate, and nothing merges past it. Never swap providers to evade quota.
- Human sign-off: releases, tags, signing, any new scientific claim or citation. Nothing else waits.
- Merge under explicit per-PR authority, with checks green, threads resolved, and evidence bound to
  the merged head. Then **arm auto-merge and exit** — never wait, never poll. Squash with
  `--match-head-commit`, which is what replaces the merge queue this repository cannot have.
