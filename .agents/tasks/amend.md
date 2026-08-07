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

   The reply is what triage reads — a thread resolved with no `Deferred: … Tracked in #N` from you
   still owes. **Resolving fires no event**, so the labels are not recomputed by any of this; the
   dispatch that does it is the LAST action of step 4, once the reply exists for it to read.
3. Revalidate the fence before any authoritative write:
   `{{PYTHON}} .agents/bin/claim.py check --issue {{ISSUE}} --generation {{GENERATION}}`.
4. Push, reply to every thread you answered, resolve the deferred ones, get the checks green, and
   update the lane state in the PR body with
   `{{GH}} api -X PATCH repos/{owner}/{repo}/pulls/<PR> -F body=@<file>` — **not `pr edit`**, which
   fails on the older `gh` the WSL lane resolves (#418).

   **Dispatch triage LAST, after the push and every reply** — `{{GH}} workflow run agent-triage.yml
   -f pr=<PR> -f dry_run=false`. `pull_request_review_thread` is a webhook Actions does not
   implement, so nothing recomputes the labels after a resolve and `agent:needs-amend` would survive
   the answer that cleared it. The order is the whole point and was wrong here until CodeRabbit said
   so on #405: dispatching before the reply gives triage a resolved thread with no
   `Deferred: … Tracked in #N` to read, so it correctly keeps owing — and dispatching before the
   push snapshots a head the push then moves. Either way the run does nothing and the residual it
   exists to clear survives it.

   **Arm auto-merge only if the lane is complete** — CodeRabbit returned no actionable comments at
   this head (`{{GH}} pr merge <PR> --auto --squash --match-head-commit <SHA>`). If it has not,
   arming merges the PR *past* the mandatory gate: CodeRabbit is not a required check, so nothing
   else is holding it. Answering a draft-phase finding is not the end of the lane.

   **`<SHA>` is the 40-hex head the clean review read, never the head re-read while arming** —
   `docs/agents/review.md` §Merge is the rule, including why re-reading it makes the guard always
   pass. You have read that page; nothing merges without it.

   **Then, and only then, exit.** This is the last line of step 4 because it has to be: an `exit`
   written before the dispatch is an instruction to leave before running it, and a worker that
   follows the step in order never recomputes the labels at all.

## Do not

- **Do not fix a non-blocking finding.** That is scope breach, not diligence — and a `Deferred:` that
  points at an issue which does not exist is a contract violation, so file the follow-up first.
- **Do not request another review round.** {{REMAINING}} remain, and they are not yours to spend:
  if another is warranted, the launcher will start a further session.
- **At round {{CAP}} the convergence check is due, and it is still not yours to request.** Answer
  every blocking finding and push, exactly as at any other round; what differs is only that no
  further AMEND follows. Triage then publishes `agent:needs-advance`, and the ADVANCE session it
  dispatches asks for the final review — `.agents/tasks/advance.md` carries the wording, the
  throttle rules, and the `refs/lane-advances/` compare-and-swap that makes the request happen
  **exactly once**. Asking here as well does not make the gate arrive sooner; it spends a second
  metered review on the same head, because nothing stops that ADVANCE session from asking too.
  A round is a review that found something **blocking**, so the convergence check costs nothing, and
  a clean **CodeRabbit** one is what satisfies the gate — it is neither a third round nor optional,
  and without it a capped pull request can never merge. A review returning only non-blocking
  findings is clean for that purpose: defer them, per the first rule above. If the check comes back
  blocking too, `agent:gate-blocked` goes on: safety-class findings escalate to the maintainer, the
  rest become follow-ups, and no further session is authorised.
- Do not rebase or force-push a published branch, and do not open a second PR for this issue.
