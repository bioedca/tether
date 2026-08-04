<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later

Injected verbatim by .agents/bin/swarm_slots.py as an ADVANCE worker's whole task text. The launcher
substitutes the {{PLACEHOLDER}} tokens.

THIS IS NOT AN AMEND. It is issued when a review came back CLEAN on an unfinished draft, which owes
nothing and so published no `agent:needs-amend` — the gap that left the lane stranded before the gate
it cannot merge without (#394). A session handed the AMEND text here would go looking for blocking
findings, find none, and either invent work or stop.

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
| Metered rounds spent | **{{ROUND}} of {{CAP}}** — this session spends none |
| Rounds still available | **{{REMAINING}}** |
| Why this session exists | {{REASON}} |

Read root `AGENTS.md`, `docs/agents/review.md` and `.agents/skills/tether-worker/SKILL.md` first.
`review.md` carries the lane, the request wording, and the throttle rules — **not this file**.

## Do

1. Find the open pull request for `{{BRANCH}}`. Confirm it is still a draft, still green, and that
   the latest review has nothing blocking outstanding. If any of those is false, **stop and record
   why in the PR body** — the state that authorised this session has changed under it.
2. Work out which lane phase is next from what the PR body records, and do **only that one**:
   - **Codex is not yet clean** → answer what is left, push, request the next Codex round, exit.
   - **Codex is clean, Greptile not yet spent** → read the seat balance with
     `{{PYTHON}} .agents/bin/greptile_usage.py`. Spend one credit if there is budget; if there is
     none, record *"Greptile: no credits this month"* and move to the next step. **Exhaustion never
     blocks.**
   - **Greptile settled** → mark the pull request ready for review. This starts the two-round cap.
   - **Ready, no CodeRabbit review yet** → request the full review. Read its status check first: a
     `pending` one is a review running now, and asking again destroys it.
3. Revalidate the fence before any authoritative write:
   `{{PYTHON}} .agents/bin/claim.py check --issue {{ISSUE}} --generation {{GENERATION}}`.
4. **Write the new lane state into the PR body**, then **exit** — do not sit and poll. That record is
   the only thing carrying the lane to the next session.

   Arm auto-merge **only** if the lane is now complete — CodeRabbit returned no actionable comments
   at this head (`{{GH}} pr merge <PR> --auto --squash --match-head-commit <SHA>`). CodeRabbit is not
   a required check, so arming before it has passed merges the PR straight past its own gate.

## Do not

- **Do not go looking for findings to fix.** There are none blocking; that is the precondition of
  this session. Rewriting working code here is scope breach.
- **Do not walk more than one phase.** Each step begins only when the previous has nothing blocking
  left, and you cannot know that about a review you have just requested. The next session gets the
  next phase.
- **Do not skip a phase to reach the gate sooner.** Marking a PR ready before its draft phase is done
  spends metered rounds on work the free lane had not finished.
- Do not rebase or force-push a published branch, and do not open a second PR for this issue.
