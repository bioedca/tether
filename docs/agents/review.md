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
  agent is never the only reviewer. Copilot is optional; its absence or quota never blocks.
- **Routing, and you must ask — neither provider self-fires here.** Request once required checks are
  green and the diff is declared final; one request per provider per round; a provider that was not
  asked **has not declined**. `low`/`standard` → Codex. `high` (scientific logic/claims,
  data/provenance/schema, security, dependencies, CI/release, public API, persistence/migration,
  concurrency, HPC/Slurm, or broad cross-component work) → **both** Codex and CodeRabbit, requested
  together and answered as **one round** — two reviewers, never two rounds, since they barely
  overlap. Author-side or local output, and a status-only result, never satisfy this gate.
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
- **Two rounds, issued by the launcher, not requested by you.** One round = a review at a
  declared-final green head plus the answer to its blocking findings. Every AMEND is a fresh
  short-lived session whose task text the launcher injects with an explicit `ROUND = N of 2`; past
  the cap it injects none, so no worker ever holds authority for a third. At the cap, safety-class
  findings escalate to the maintainer and the rest become follow-ups. Stop-list, not judgement:
  **one self-review pass at most**, before the first external request, and **never a review request
  while `agent:review-capped` is present**.
- **Capability is not quota.** A selected provider reporting nothing to review at the head it read
  satisfies its leg — including a Codex 👍 reaction, its documented form of "no suggestions". Quote
  the provider, never the author or another commenter. On `high`, one provider that genuinely
  *cannot* act leaves the other sufficient with the unavailability quoted; never swap to evade
  quota, and genuine unavailability of both freezes the PR.
- Human sign-off: releases, tags, signing, any new scientific claim or citation. Nothing else waits.
- Merge under explicit per-PR authority, with checks green, threads resolved, and evidence bound to
  the merged head. Then **arm auto-merge and exit** — never wait, never poll. Squash with
  `--match-head-commit`, which is what replaces the merge queue this repository cannot have.
