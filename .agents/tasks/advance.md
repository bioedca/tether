<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later

Injected verbatim by .agents/bin/swarm_slots.py as an ADVANCE worker's whole task text. The launcher
substitutes the {{PLACEHOLDER}} tokens.

THIS IS NOT AN AMEND. It is issued when a review came back CLEAN on an unfinished LANE, which owes
nothing and so published no `agent:needs-amend` — the gap that left the lane stranded before the gate
it cannot merge without (#394). A session handed the AMEND text here would go looking for blocking
findings, find none, and either invent work or stop.

An unfinished lane is not only a draft. The stranded DRAFT is the incident that found this, but
`_advance_state` publishes for any phase that still has a step left — a ready pull request whose
CodeRabbit gate has not been asked for, or has passed with the merge not yet armed, is equally
stranded and equally this session's business.

The authority is one ref in refs/lane-advances/, taken before this file was rendered, so this session
holds exactly one advance. It is deliberately NOT in refs/amend-rounds/: moving a pull request from
one phase to the next is not a review round and must not spend one.

Keep it SHORT; it is resident context for the whole session.
-->

# ADVANCE — issue #{{ISSUE}}, move the review lane on by one phase

The claim on **#{{ISSUE}}** is still held, its pull request is still open, and **a review has come
back with nothing blocking**. The previous session pushed and exited, which is normal. Nobody is
walking the lane, and that is why this session exists.

| | |
|---|---|
| Branch | `{{BRANCH}}` |
| Generation | `{{GENERATION}}` |
| Vendor lane | `{{VENDOR}}` |
| Metered rounds already spent | **{{ROUND}} of {{CAP}}** |
| Rounds still available | **{{REMAINING}}** |
| What this session spends | **nothing** — an advance is not a review round |
| Why this session exists | {{REASON}} |

Read root `AGENTS.md`, `docs/agents/review.md` and `.agents/skills/tether-worker/SKILL.md` first.
`review.md` carries the lane, the request wording, and the throttle rules — **not this file**.

## Do

1. Find the open pull request for `{{BRANCH}}`. Confirm it is still **green** and that the latest
   review has nothing blocking outstanding. If either is false, **stop and record why in the PR
   body** — the state that authorised this session has changed under it.

   **Do not stop merely because the pull request is no longer a draft.** A ready PR still has lane
   left: the CodeRabbit gate to request, and the merge to arm once that comes back clean. Which
   step you are on is decided in 3, not by the draft flag.
2. **Revalidate the fence immediately before EVERY authoritative write**, starting with the
   stop-and-record path in 1:
   `{{PYTHON}} .agents/bin/claim.py check --issue {{ISSUE}} --generation {{GENERATION}}`.
   Exit `5` means the claim was reaped and reclaimed — stop, write nothing.

   **Once at the start is not enough**, and this said so until CodeRabbit pointed out on #407 that
   it only fenced the first write. A session can be reaped between choosing a phase and performing
   it, and every step in 3 is a write on somebody's pull request — a push, a metered request, a
   ready transition, a merge. Re-run the check before each one and before the PR-body write in 4,
   not once for all of them. A reaped worker that writes anyway is writing on a **successor's** PR.
3. Work out which lane phase is next from what the PR body records, and do **only that one**.

   **Writing the lane state in 4 is what releases the next attempt** (#412). The advance ref is
   keyed on a digest of the PR body, so until you record something no launcher can issue another
   session for this step — and if you stop without recording, none ever will and the lane stalls
   for a person. That is the safe direction and it is also why 4 is not optional.

   Belt and braces on the two phases where a duplicate would cost real money: **read the provider's
   own state before spending, and treat a request already in flight as done.** Both are marked
   below. Neither is safe to infer from the PR body alone.

   - **Codex is not yet clean** → answer what is left, push, request the next Codex round.
   - **Codex is clean, Greptile not yet spent** → read the seat balance with
     `{{PYTHON}} .agents/bin/greptile_usage.py`. **It reports credits spent per PR: if this pull
     request already has one this month, another session spent it — record that and move on rather
     than buying a second.** Spend one credit if there is budget and none is recorded here; if there
     is none at all, record *"Greptile: no credits this month"* and move to the next step.
     **Exhaustion never blocks.**
   - **Greptile settled** → mark the pull request ready for review. This starts the two-round cap.
   - **Ready, no CodeRabbit review yet** → **read the `CodeRabbit` commit status before asking.**
     `Review in progress` means another session already asked and a second request destroys the run
     it is waiting for; treat that as this phase being done. Only `Review skipped` or a
     completed review at an older head is an unasked gate. A rate-limit refusal names its own retry
     time and costs nothing, so it is a *wait*, not a failure.
   - **Ready, CodeRabbit came back with no actionable comments** → the gate is satisfied and the
     only step left is arming the merge — **if this session holds merge authority**. It does not
     hold it by default: the `refs/lane-advances/` ref authorises one *phase transition*, and
     `AGENTS.md` requires **explicit per-PR merge authority** that is never inferred. Without it,
     the lane state you write in 4 is *"gate satisfied, awaiting merge authority"*; the lane is
     finished and a person decides the merge. Arming is available even at `agent:review-capped`,
     because arming is not a review request — but that is about the *cap*, not about authority.
4. **Write the new lane state into the PR body** with
   `{{GH}} api -X PATCH repos/{owner}/{repo}/pulls/<PR> -F body=@<file>` — **not `pr edit`**, which
   fails on the older `gh` the WSL lane resolves (#418). That record is the only thing carrying the
   lane to the next session.

   Arm auto-merge **only** when both hold: the lane is complete — CodeRabbit returned no actionable
   comments at this head — **and** this session was given explicit merge authority for this PR
   (`{{GH}} pr merge <PR> --auto --squash --match-head-commit <SHA>`). CodeRabbit is not a required
   check, so arming before it has passed merges the PR straight past its own gate; and the advance
   ref is not merge authority, so arming without that authority merges code nobody authorised.

   **Then exit** — do not sit and poll. Last, and written last: an `exit` placed above the arming
   paragraph tells a worker reading in order to leave before the one command that ends the lane.

## Do not

- **Do not go looking for findings to fix.** There are none blocking; that is the precondition of
  this session. Rewriting working code here is scope breach.
- **Do not walk more than one phase.** Each step begins only when the previous has nothing blocking
  left, and you cannot know that about a review you have just requested. The next session gets the
  next phase.
- **Do not skip a phase to reach the gate sooner.** Marking a PR ready before its draft phase is done
  spends metered rounds on work the free lane had not finished.
- Do not rebase or force-push a published branch, and do not open a second PR for this issue.
