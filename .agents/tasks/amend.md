<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later

Injected verbatim by .agents/bin/swarm_slots.py as an AMEND worker's whole task text. The launcher
substitutes the {{PLACEHOLDER}} tokens.

THE LAUNCHER REFUSES TO INJECT THIS FILE AT ALL ONCE {{ROUND}} WOULD EXCEED THE CAP. That refusal is
the enforcement: a worker never receives the authority for a third round, so nothing has to decline a
request that was never granted. Do not add an "if capped" branch here — this file existing in a
session already means the round was authorised.

Keep it SHORT; it is resident context for the whole session.
-->

# AMEND — issue #{{ISSUE}}, round {{ROUND}} of {{CAP}}

The claim on **#{{ISSUE}}** is still held and its pull request is still open. You are continuing that
work, not starting over: the previous session pushed and exited, which is normal.

| | |
|---|---|
| Branch | `{{BRANCH}}` |
| Generation | `{{GENERATION}}` |
| Vendor lane | `{{VENDOR}}` |
| **Review rounds spent** | **{{ROUND}} of {{CAP}}** |
| Rounds remaining after this one | **{{REMAINING}}** |
| Why this session exists | {{REASON}} |

Read root `AGENTS.md`, `docs/agents/review.md` and `.agents/skills/tether-worker/SKILL.md` first.
The severity floor you classify against lives on `review.md`, not in the resident contract.

## Do

1. Find the open pull request for `{{BRANCH}}`. Read the failing checks and every unresolved review
   thread on it.
2. Fix the **blocking** findings only — severity axis: CodeRabbit `Critical`/`Major`, Codex `P1`, Greptile `P1`, plus
   the label-independent list in `docs/agents/review.md`. Everything else is deferred to **one**
   follow-up issue
   for this PR, answered `Deferred: … Tracked in #N`, and its thread resolved.
3. Revalidate the fence before any authoritative write:
   `{{PYTHON}} .agents/bin/claim.py check --issue {{ISSUE}} --generation {{GENERATION}}`.
4. Push, reply to every thread you answered, get the checks green, update the lane state in the PR
   body, and **exit**.

   **Arm auto-merge only if the lane is complete** — CodeRabbit returned no actionable comments at
   this head (`{{GH}} pr merge <PR> --auto --squash --match-head-commit <SHA>`). If it has not,
   arming merges the PR *past* the mandatory gate: CodeRabbit is not a required check, so nothing
   else is holding it. Answering a draft-phase finding is not the end of the lane.

## Do not

- **Do not fix a non-blocking finding.** That is scope breach, not diligence — and a `Deferred:` that
  points at an issue which does not exist is a contract violation, so file the follow-up first.
- **Do not request another review round.** {{REMAINING}} remain, and they are not yours to spend: if
  another is warranted, the launcher will start a further session. If this is round {{CAP}}, the next
  state is not a third round — safety-class findings escalate to the maintainer and the rest become
  follow-ups.
- Do not rebase or force-push a published branch, and do not open a second PR for this issue.
