<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# The review gate

These rules are part of the agent contract. `AGENTS.md` points here and grants no merge authority on
its own: **an agent that has not read this page may not request a review or merge a pull request.**
Authorization still has to come from `AGENTS.md` — merge is under explicit per-PR authority, and
nothing on this page supplies it.

- Record `low`, `standard`, or `high` in the PR with a reason. Risk may only increase. **It no longer
  routes providers** — every PR walks the same lane below, whatever its risk. What risk states now is
  how much scrutiny the change deserves, and so whether a metered credit is worth spending on it
  (step 2). The authoring agent is never the only reviewer.
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
4. **CodeRabbit is the last gate before merge** — `@coderabbitai full review` — at least one
   CodeRabbit review with **no actionable comments**. Nothing merges without it.

**Never write a provider's handle in a comment you do not intend as a request.** A mention fires the
bot even inside backticks — a code span is not an escape. Quoting the trigger while *describing* it
spent a real fair-use review on a draft that was not ready for one, and throttled the PR that was
(measured, 2026-08-03). When you need to name a command in prose, break the handle or describe it:
*"the full-review command"*. This is why the lane's own documentation is the one place these strings
belong.

**Ask CodeRabbit with `full review`, not `review`.** The bare `@coderabbitai review` is the
*incremental* command, and it applies only where automatic reviews are **paused**. They are
**disabled** here, so it does not run — it answers *"CodeRabbit is an incremental review system and
does not re-review already reviewed commits"* and reviews nothing. That reply is easy to read as a
review that found nothing, which is the failure this gate exists to catch. Measured on #392.

**Read the status check before every ask — a second request aborts a review in flight.** The
`CodeRabbit` commit status is the liveness signal: `pending` / *"Review in progress"* means one is
running **right now**, and asking again cancels it, so the window is spent and nothing comes back.
The bot's own comments are not that signal — *"Full review finished"* reports the pass that just
ended and says nothing about what is still queued behind it.

```
gh pr checks <PR> --json name,state,description --jq '.[] | select(.name == "CodeRabbit")'
```

The check answers *is one running*, never *did one happen*: green with no review body is the silent
suppression below, and `pending` is a review you must not interrupt. Measured on #385 (2026-08-03):
a retry sent thirty minutes into a live review killed it and triggered the adaptive limit. Silence is
not evidence of a throttle — it is what a large review looks like from outside.

**You must ask — no provider self-fires here.** One request per provider per round; a provider that
was not asked **has not declined**. Author-side or local output, and a status-only result, never
satisfy this gate.

**One exception, and it has already cost money.** `.greptile/config.json` is read from the pull
request's *source branch*, so a branch cut before that file landed still auto-fires Greptile the
moment the PR opens (below, under the seat budget). Do not treat an unsolicited review as an
irregularity to be undone: it is a real review and its credit is spent either way. Answer it as you
would any other, record **step 2 as spent** with the head it read, and do not ask again. It consumes
a round only if the PR was already ready when it arrived. Rebasing such a branch onto a base
carrying the config prevents the *next* one.

**A request that produced no review has not been spent.** The one-per-round limit counts requests
that were *answered with a review* — so a throttle refusal, or the wrong command running nothing,
leaves the allowance intact and you ask again — once the status check shows nothing
running. Without that, the fair-use refusal below would deadlock
the mandatory gate: retry required by one rule, forbidden by the other. What the limit forbids is
asking a provider to look **again** at work it has already reviewed this round.

- **Metered providers, and the seat they share.** Greptile bills **one credit per completed review,
  charged to the PR author**, from **50 per seat per month** — and this account's one seat is shared
  across `tether`, `Yeliztli` and `tbox-finder`. A TREX review costs **3**. Before spending one, read
  the balance; after a month's worth of PRs, expect it to be gone:

  ```sh
  <py> .agents/bin/greptile_usage.py
  ```

  `<py>` is your lane's interpreter — `python3` in WSL bash, `python` in native PowerShell. Naming
  one of them on a page both lanes read would strand the other lane on the balance check it is
  required to make before spending a credit.

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
  **Greptile `P1`** — its badges use the same P-scale as Codex, so they map straight across, and a
  paid review whose findings could all be deferred would be a credit spent on nothing —
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
  `ROUND = N of 2`; past the cap it injects none, so no worker ever holds authority for a third.
  **A round is a metered review that found something blocking.** A clean one is the lane
  *terminating*, not a round, so it costs nothing — which is what stops the cap and the gate
  contradicting each other. Without that rule a round-2 review with findings left a PR needing a
  review at the head that answered them, and forbidden to buy one: green, mergeable and unmergeable
  at once (#399, measured on #385).
- **At the cap you may ask once more, and only to verify convergence.** Answer everything, push,
  and request the final review. Clean satisfies the gate and the lane ends. Blocking again means the
  count has passed the cap: `agent:gate-blocked` goes on, and it is **a maintainer's** — safety-class
  findings escalate, the rest become follow-ups, and you stop. **That escalation belongs here and
  not at the cap**, which is where this page used to put it: at `agent:review-capped` the round-2
  findings still have to be *fixed*, and a worker is still issued the session that fixes them, so
  telling it to escalate and stop there would end the lane one step before the review the gate
  requires (CodeRabbit on #408).
  Stop-list, not judgement: **never a review request while `agent:gate-blocked` is present**, and
  under `agent:review-capped` never more than that one *completed* convergence review. A request
  that produced no review has not spent it — a fair-use refusal naming a retry time is a wait, so
  wait it and ask again after reading the status check.
- **The cap withholds AMEND authority past itself, not at itself**, and the distinction is what
  makes the convergence check reachable. An AMEND answers a review; it is not a review, so it is not
  a round. At `agent:review-capped` the round-2 findings still have to be fixed, and a worker is
  still issued the session that fixes them — otherwise the lane could never reach the *everything
  answered, everything pushed* state the convergence check requires, and the rule written to
  un-deadlock the gate would deadlock it one step earlier (CodeRabbit on #408). Authority stops once
  `agent:gate-blocked` is published, because then the convergence check has failed too.
- **A clean review on an unfinished lane resumes the claim, and does not spend a round.** The lane
  is a sequence, and a review that finds nothing owes nothing — so under the AMEND-only signal it
  published no authority at all and the lane sat before the gate it cannot merge without (#394).
  `triage.py` now publishes **`agent:needs-advance`** when a PR is green, settled, owes nothing and
  has been reviewed at its current head, and the launcher issues **one** ADVANCE session against
  it: `.agents/tasks/advance.md`, which moves the lane on by exactly one phase and exits. It is not
  an AMEND — there are no blocking findings to fix — and its ref lives in `refs/lane-advances/`
  rather than `refs/amend-rounds/`, so advancing a phase never costs a metered round.

  **The stranded draft is the incident; the rule is broader.** A draft whose free review came back
  clean is what found this, but a *ready* pull request is stranded in the same way — nobody has
  asked CodeRabbit, or it has passed and the merge is not armed — and neither step is a round. So
  the authority is published for any phase with a step left, and withheld only once the lane is
  genuinely complete: ready **and** armed. Being at `agent:review-capped` does not withhold it,
  because the remaining steps are not rounds.

  **`agent:gate-blocked` does withhold it**, and that rule belongs to neither issue alone. Past the
  cap the convergence check came back blocking too, so a maintainer decides — and an advance is
  precisely the automatic state that label says no longer remains. The window it closes is a real
  one: `owed` stops holding the moment the author answers the findings and resolves the threads
  (#393), which is exactly when a session would otherwise be dispatched to walk a lane that has
  stopped terminating, toward a review it has no round left to buy.
- **Capability is not quota, and the two fail differently.** A selected provider reporting nothing to
  review at the head it read satisfies its leg — including a Codex 👍 reaction, its documented form
  of "no suggestions". Quote the provider, never the author or another commenter. *Exhaustion* is not
  that: a provider with no budget left has **not** reviewed, and saying so is the honest record.
  Greptile out of credits is expected and skippable (step 2). **CodeRabbit unavailable freezes the
  PR** — it is the last gate, and nothing merges past it. Never swap providers to evade quota.
- **Throttled is not unavailable, and the difference is a stated retry time.** CodeRabbit's fair-use
  limit is *adaptive*: sustained recent activity drops the seat to a per-interval allowance, and the
  refusal names when the next included review is due — *"Your next included review will be available
  in 10 minutes"*. That is a **wait**, not a freeze. Wait the stated interval, confirm the status
  check is not `pending`, then ask again; do not record it as unavailability, and do not escalate it
  to the maintainer as one. The elapsed interval is necessary and **not sufficient** — a retry that
  lands on a running review destroys it and re-saturates the limit, which is how a wait turns into
  the freeze it was not. A freeze is the case where it cannot act **at all**, or where it goes
  silent with a green check.
  The refusal also offers to proceed **through usage-based billing**. Never take it: paying to skip
  a wait is a spending decision, and it belongs to the maintainer, not to a worker trying to finish.
  Measured on #392, and the driver is request *cadence* — several PRs reviewed in one sitting is what
  triggers it, so pace the asks rather than batching them.
- Human sign-off: releases, tags, signing, any new scientific claim or citation. Nothing else waits.
- Merge under explicit per-PR authority, with checks green, threads resolved, and evidence bound to
  the merged head. Then **arm auto-merge and exit** — never wait, never poll. Squash with
  `--match-head-commit`, which is what replaces the merge queue this repository cannot have.
