<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0064 — The agent layer coordinates writers, not reviews

- **Status:** accepted; supersedes [ADR-0062](0062-draft-first-review-lane-with-metered-providers.md) and [ADR-0063](0063-review-evidence-is-read-not-inferred.md), and the review gate, round cap, launcher and advisory scope guard of [ADR-0057](0057-github-native-swarm-coordination.md)
- **Date:** 2026-08-07
- **Deciders:** bioedca
- **PRD anchor:** §12 (development & version-control protocol)
- **Milestone:** M11 - Agent-swarm infrastructure

## Context and problem statement

> **What of ADR-0057 survives.** Its claim mutex, generation fence, scheduled reaper and
> atomic ADR-number reservation **govern unchanged** — this record supersedes only its review
> gate, round cap, slot launcher and advisory scope guard. That distinction is the whole decision, and it is stated
> here rather than in the `Status` bullet because `scripts/gen_adr_index.py` extracts that
> field with a single-line pattern and copies it verbatim into the index.
>
> **Where every number below comes from.** All counts in this record were taken at
> **`d3fd78ce638a141c7f7402905c6371364bc5cecc`** (`d3fd78c`, 2026-08-07), the commit this record
> branched from, and are `git show <sha>:<path> | wc -l` over these sets — call this the **agent
> pathspec**: **scripts** = the 7 files in `.agents/bin/`; **tests** =
> `test_{triage,swarm_slots,claim,agent_contract_is_runnable,scope_guard,reaper,greptile_usage,`
> `greptile_config,agent_entry_points}.py`; **workflows** = `agent-reaper.yml`,
> `agent-triage.yml`, `scope-guard.yml`; **prose** = `AGENTS.md`, `CLAUDE.md`, both `SKILL.md`,
> `agents/openai.yaml`, `.agents/tasks/*.md`, `docs/agents/*.md`, `.greptile/README.md`; **ADRs**
> = 0052, 0053, 0057, 0061, 0062, 0063. A reader can re-derive each figure from that SHA without
> trusting this page.
>
> **Three kinds of evidence, and only the first is reproducible from a SHA.** The line counts
> above are. The **remote-ref and issue-search counts** name their own commands and are as of the
> same date, but they read live state, so they stand only as a **lower bound** — more refs and
> issues can appear later, and `ls-remote` never reports a deletion. (Re-running it while this
> pull request was open returned six ADR reservations rather than five, the sixth being this
> record's own — which is the append-only property below behaving exactly as claimed.) The
> **vendor quota terms** cannot be pinned to any revision this repository controls; where the
> argument leans on one, it cites the in-repo artifact that encodes it — `INCLUDED_CREDITS = 50`
> in `.agents/bin/greptile_usage.py` and `.greptile/README.md` — which is versioned here even
> though the vendor's own terms are not.

The agent layer is **17,773 lines** — 5,469 of scripts, 10,407 of tests, 502 of workflows and 1,395
of contract prose, task templates and skills — against `src/tether`'s 42,796. **41% the size of the
product it exists to help build**, with a further 1,071 lines of ADRs about itself.
`tests/test_triage.py` alone is 3,932 lines, larger than any single file in `src/tether`.

It did not exist on 2026-07-21. It has since become the only thing being worked on:

- **The last 18 commits are all `(agents)`**, unbroken — the 19th is a product ADR. **35 of the
  last 60** are (`git log --format=%s -60 d3fd78c | grep -c '(agents)'`).
- `git diff --shortstat 2c27171 d3fd78c -- src/tether` returns **empty**: zero changed product
  lines since 2026-07-30, against **+9,809 / −264** in the agent layer over the same window (the
  same command, with the agent pathspec above in place of `src/tether`).

Two mechanisms produce this, and they are independent. Removing either alone leaves the other
running.

### 1. The review-round ledger models another vendor's product, so it cannot converge

`triage.py` reconstructs lane state from third-party review bots' comment streams. It grew from 344
to **1,782** lines in nine days. Every fix has been a newly discovered fact about someone else's
product rather than a defect in our reasoning: GitHub rewrites a review's `commit_id`
([#309](https://github.com/bioedca/tether/issues/309)); a reply in a thread is not a round
([#404](https://github.com/bioedca/tether/issues/404)); a resolved thread stops owing an AMEND
([#405](https://github.com/bioedca/tether/issues/405)); a clean review authorises the next phase
([#407](https://github.com/bioedca/tether/issues/407)). ADR-0063 counted **seven defects in one
week** from a single root cause and fixed that cause; [#425](https://github.com/bioedca/tether/issues/425)
and [#426](https://github.com/bioedca/tether/issues/426) were filed against its successor within
hours of it merging.

This record is the **fourth** to govern the review gate and its round cap — ADR-0057 §Round cap
(07-28) → ADR-0062 (08-03) → ADR-0063 (08-07) → this one — and the **sixth** architectural record
about the agent layer in the seventeen days since ADR-0052 (07-21). Each of the three before this
one produced a larger counter and more issues. Continuing to refine it is the option with the
longest track record here, and that record is unambiguous.

**And it is bookkeeping nobody collects.** `git ls-remote origin` lists what exists on the remote
**now** — it does not report deleted refs, so a live listing is not by itself a history. It is one
here because these four namespaces are **append-only by construction**: `swarm_slots.py` records
each AMEND as a compare-and-swap ref and states that *"refs here are never deleted"*, ADR numbers
are reserved and never reused or renumbered, `refs/reaped/` is the archive a reap writes and never
removes, and nothing in `.agents/bin/` issues a `DELETE` against any of them (the reaper deletes
`refs/heads/agent/issue-<N>` only). So for these namespaces the current set is the ever-created set,
and that is why the counts below are historical:

```text
refs/adr-reservations/{0058,0059,0061,0062,0063}   5   used, works
refs/reaped/issue-400-b6d9aa1…                     1
refs/amend-rounds/252-38550159308-1                1   one AMEND, ever
refs/lane-advances/*                               0   none, ever
```

`swarm_slots.py` and its test — 2,796 lines, and the component `AGENTS.md` calls *"the only issuer
of review rounds"* — have issued **one** round in their life, and the file appears in no workflow,
hook or configuration anywhere in the repository's history. The ADVANCE subsystem (~2,000 lines
merged 08-05 → 08-07 across #394, #407, #408, #411, #414, #419, #423 and ADR-0063) has **never taken
a single ref**. Of the **seven** labels `triage.py` publishes, **five have no live reader**:
`agent:round-1`, `agent:round-2`, `agent:review-capped`, `agent:needs-advance` and
`agent:gate-blocked` are read by `triage.py` itself and by `swarm_slots.py`, and by nothing else —
and `swarm_slots.py` is the launcher no workflow, hook or configuration invokes. ADR-0057 already
records that round-2 is *"provisioned but unreachable by code"*. Only `agent:needs-amend` and
`agent:conflicted` reach a reader that actually runs: `reaper.py`, on the `agent-reaper.yml` cron.
(The launcher's read of the two round labels is `ROUND_LABELS = triage.ROUND_LABELS` at
`swarm_slots.py:102`, consumed at `:203` — an imported constant, so grepping the label text alone
finds neither and reports a smaller reader set than exists.)

So the cap that `AGENTS.md` describes as mechanically enforced is not.
[#300](https://github.com/bioedca/tether/issues/300) concedes this in its own title and proposes a
scheduled **audit** on top: prose, then labels, then an audit of the labels — three layers of control
over one convention.

### 2. The deferral rule converts review nitpicks into work, at better than one per fix

`docs/agents/review.md` requires *"one follow-up issue per PR, reply `Deferred: … Tracked in #N`"*.
On an agent-layer pull request that closes a loop: a sub-floor nitpick becomes an issue, the issue
becomes a pull request, that pull request is read by three providers, and they find more nitpicks.
Measured with `gh search issues --repo bioedca/tether "Deferred from"`:

| window | agent-layer issues spawned by deferred findings |
|---|---|
| 2026-07-29 → 07-30 | 3 |
| **2026-08-03 → 08-07** | **13** (#387, #391, #395, #399, #400, #409, #410, #411, #412, #419, #420, #421, #425) |

Sixteen in ten days, thirteen of them in the last five, and accelerating. #425's first line is
*"Deferred from #424, where CodeRabbit raised it as `Trivial`"*; #426 was *"reached live on #422
within hours of #424 merging."* This is why deleting code alone would not settle anything — the mill
would simply grind whatever remained.

## Decision drivers

- **ADR-0057's own third driver, applied to its successor:** *"Contract text is resident context, so
  its size is a running cost, not a one-off."* The mandatory-read contract has grown from 151 lines
  to **991 across nine files**, every one of them declared "a bar to acting". Extracting sections
  into `docs/agents/` (#294) moved the cost, it did not remove it — and the three task templates the
  launcher injects add 287 more.
- ADR-0052 was retired after a measured **3.48 billion input tokens, 44 hours and two merged pull
  requests**, for a 2,594-line implementation (a 1,504-line helper and a 1,090-line test). The
  scripts and tests that replaced it are **15,876 lines — 6.1× the size of what was too expensive
  to keep.**
- No agent workflow is a required check. The `main-baseline` ruleset requires `lint`, `test` on three
  operating systems, `pre-commit`, `commitlint`, `secret-scan`, `conda-lock-verify`, `docs-build`,
  `schema-guard` and `sidecar / parity` — eleven contexts, all product CI. Everything removed here
  was advisory. Note also that the agent tests are stdlib-only *by design*, so they run inside the
  required `test` job — **three times, on every product pull request.**
- The layer's purpose is to let two vendors work one backlog concurrently. That is a
  mutual-exclusion problem, and the compare-and-swap that solves it landed on 2026-07-29
  ([#275](https://github.com/bioedca/tether/issues/275)).

## Considered options

1. **Fix the eleven open defects in place.** Rejected: the measured defect-injection ratio is ≥1 new
   issue per fix merged, and #426 was created by #409's fix hours after it landed. This is the option
   with the most evidence against it.
2. **Freeze the layer without removing anything.** Rejected: three workflows keep writing labels, and
   the resident-context cost — the driver ADR-0057 named — is unchanged.
3. **Cut the review-bookkeeping half, keep the concurrency half, and close the layer to capability
   growth. Chosen.**
4. **Remove the layer entirely, including the mutex.** Rejected: ADR-0052's failure was that no mutex
   existed. Nothing about the claim path has misbehaved.

## Decision outcome

**Cross-agent coordination is a ref-namespace problem, solved completely by four controls over
namespaces GitHub already provides. Code review is a per-PR, single-owner concern with no
concurrency content, and is governed by convention rather than by a label state machine, a round
ledger and a launcher.**

### What is kept, and why each earns its place

Two concurrent agents can corrupt each other in exactly four ways, and each has a control:

| hazard | mechanism | kind |
|---|---|---|
| two agents work one issue | `claim.py claim` — atomic ref create, `201`/`422` | compare-and-swap |
| two agents pick one ADR number | `claim.py reserve-adr` — the same create, different namespace | compare-and-swap |
| a superseded agent writes after being replaced | `claim.py check` — the generation fence | pre-write check |
| a dead agent holds a claim forever | `reaper.py` + `agent-reaper.yml` | scheduled reclaim |

**Only two of the four are compare-and-swap, and the difference is load-bearing rather than
pedantic.** The two ref creations are genuinely atomic: the server admits exactly one writer and
tells the loser so, which is why a claim needs no lease and no heartbeat. The other two do not have
that property and must not be read as though they did. The generation fence is a *read* immediately
before an authoritative write, so it narrows the window in which a superseded agent can act but
does not close it — that is why the contract says revalidate before **every** such write rather
than once per session. And the reaper's `DELETE /git/refs` accepts no expected-SHA precondition at
all, so its reclaim is unconditional; [#278](https://github.com/bioedca/tether/issues/278) is the
record that this residual is irreducible on GitHub's API and that the mitigation is to archive the
tip immediately before deleting, converting an unarchived loss into a recoverable one. A reader who
took the whole table for compare-and-swap would conclude the layer is safer than it is.

**The scope check is kept too, and it is deliberately outside that table.** `claim.py scope-hash`
and `_check_eligible` read the issue and its comment pages and mutate nothing — no ref, no label,
no write verb anywhere in the path — so listing them as a coordination control would misdescribe
both what they do and why they matter. They are the *precondition* ADR-0057 states: eligibility decides
**whether** an issue may be worked, the mutex decides only **who** works it, and a claim taken on
unapproved or since-edited scope is invalid however cleanly the race was won. That is why the check
runs before the ref is created, and why a refusal leaves no ref behind for anyone to inherit.

`greptile_usage.py` is kept for a different reason: it is the only control on a shared, metered,
cross-repository budget, and it has produced no defects. `.greptile/config.json` is kept because
`skipReview: "AUTOMATIC"` is what actually stopped the one overspend this repository has had
([#389](https://github.com/bioedca/tether/issues/389), two unrequested credits in a day) — the round
cap did not prevent that and could not have.

Applying the same test to everything else — *which two concurrent agents contend for this?* — the
round ledger, the label state machine, the materiality digest, the launcher and the phase states all
answer **none**. Review state is per-PR, a PR has one owner, and the claim mutex already serialises
it. The round counter coordinates one agent *with itself across sessions*, which is a workflow
problem, not a concurrency problem.

### What is removed

`triage.py`, `scope_guard.py`, `swarm_slots.py`, `gate.ps1`, `agent-triage.yml`, `scope-guard.yml`,
`.agents/tasks/*`, `docs/agents/review.md` and their tests. The labels `agent:round-1`,
`agent:round-2`, `agent:review-capped`, `agent:needs-advance` and `agent:gate-blocked`, and the
`refs/amend-rounds/*` and `refs/lane-advances/*` namespaces. `agent:needs-amend` and
`agent:conflicted` survive: the reaper both writes and reads them.

`triage.py` and `scope_guard.py` must be removed together — ADR-0063 made `scope_guard._review_rounds`
delegate to `triage`, so they are now one unit rather than the two independent opinions ADR-0057
intended.

The review gate becomes ~24 lines in `AGENTS.md`, keeping the four rules that are load-bearing and
have never been the source of a defect: the authoring agent is never the only reviewer; work on a
draft so the metered providers see a green diff; read the credit balance before spending one and
never accept a usage-based-billing offer; and one CodeRabbit review with no actionable comments is
the last gate before merge.

### The drop rule

**On agent-layer paths a review finding below the severity floor takes no follow-up issue.** The
paths are `.agents/`, `docs/agents/`, `AGENTS.md`, `CLAUDE.md` and the agent test modules. Reply
`Noted; below the floor on an agent-layer path and not tracked (ADR-0064)` and resolve the thread.
Blocking findings are unaffected, and CodeRabbit remains the merge gate.

This inverts the general rule deliberately and only here, because only here does the output feed
back into the input. Two of the eleven open issues record their own sub-floor provenance and would
not exist under it: #298 (*"Codex, P2, explicitly non-blocking"*) and #425 (*"CodeRabbit raised it
as `Trivial`"*). Most of the thirteen deferred issues of the last five days are of the same kind.

### The layer is feature-complete

**The same path set as the drop rule above, deliberately** — `.agents/`, `docs/agents/`,
`AGENTS.md`, `CLAUDE.md` and the agent test modules. Two rules governing one layer must not disagree
about where that layer is: a test module inside one set and outside the other leaves the
maintainer-issue requirement ambiguous exactly where an agent would be most tempted to resolve the
ambiguity in its own favour.

Those paths accept **bug fixes and safety fixes only**. A capability change requires a
maintainer-opened issue and may not originate in a review finding. The reasoning is recorded here
rather than in `AGENTS.md` precisely because of the resident-context driver above: an ADR is read
once by a human, `AGENTS.md` is read on every model call by every agent.

### Three decisions this record also settles

- **[#278](https://github.com/bioedca/tether/issues/278) — the reaper's irreducible ref-deletion
  window.** GitHub's ref `DELETE` accepts no expected-SHA, so the window between the final read and
  the delete cannot be closed, and the issue's own text says so. **Decided:** archive
  unconditionally against the freshly-read tip immediately before the delete. This does not remove
  the race; it changes its consequence from *unarchived loss* to *archived and recoverable*, which
  is the only property that was ever at stake. The residual is accepted and documented here rather
  than in a module docstring. This is the only place in the layer where an unattended job can
  destroy work, which is why the cheap half of option 2 is taken rather than pure acceptance.
- **[#303](https://github.com/bioedca/tether/issues/303) — a claim ref whose activity record never
  appears.** **Decided:** option 3, which is what the code already does — `claim.py` retains the ref
  and reports, `reaper.py` keeps it as `activity-unknown`, and a maintainer resolves it. Only the
  *lag* has ever been observed, once, in seconds, on 2026-07-30. Bounding the unknown needs a
  trustworthy clock that ADR-0057 establishes does not exist for a ref with no activity record, and
  a separate ownership token would be a redesign of the claim identity to serve a hypothetical.
- **[#300](https://github.com/bioedca/tether/issues/300) — auditing the round cap after merge.**
  **Decided:** no audit. With the ledger removed there is nothing to audit, and the budget it
  protected is metered directly by `greptile_usage.py` and by each vendor's own ceiling.

### Adoption status

This record is the decision; the removals land as a sequence of subtractive pull requests, so this
section is the authority on what is actually gone.

**In force on merge of this record, because this pull request writes them into
`docs/agents/review.md`, `AGENTS.md` and `CLAUDE.md`:** the **drop rule** and the
**feature-complete freeze**. Those two are behavioural rules an agent must follow, and a rule
recorded only in an ADR while the contract still says the opposite is not a rule (Greptile P1 on
issue `#427`).

**Recorded here and nowhere else:** the decisions for issues `#278`, `#303` and `#300`. They are
findings about this repository's own machinery rather than instructions to a worker, so they do not
belong in resident contract text. The two halves of `#303` additionally re-point their module
docstrings — `claim.py::_unfenced_claim` and `reaper.py`'s `activity-unknown` branch — at this
record, which is what that issue's third criterion asks for.

**Landing separately:** the `CLAUDE.md` collapse, the reaper shrink with `#278`'s archive, the
launcher removal, the triage-and-scope-guard removal (one pull request — they are one unit), and the
contract rewrite. Until each lands, the code it names still runs and still governs.

**The [PRD](https://github.com/bioedca/tether/blob/main/docs/PRD.md) §12 is part of that contract
rewrite and is therefore stale between this record merging and that one.** It still describes
ADR-0062's round cap and launcher as current governance.
That is deliberate rather than overlooked — the PRD paragraph and the `AGENTS.md` §Review rewrite
describe one lane and must change together, in the pull request that actually deletes the machinery
— but a reader arriving in the gap should know which of the two to believe. **This record wins**: it
is the later decision, and the PRD text it contradicts is a description of tooling that is on its
way out.

**Retirement gates on the writer, and the two kinds of resource have different writers.** The five
retired labels go after **`triage.py`** stops writing them; the `refs/amend-rounds/*` and
`refs/lane-advances/*` namespaces go after **`swarm_slots.py`** does, since the launcher is their
only writer. The removal here deletes both modules in one pull request, so in practice the two
gates open together — but they are stated separately because gating everything on one module is
only safe while that stays true, and splitting the removals later would leave the other writer free
to recreate what had just been retired. For the labels that recreation is also *silent*:
`POST /repos/{owner}/{repo}/issues/{number}/labels` auto-creates a missing label rather than
failing, so nothing in the audit trail would show it.

## Consequences

**Good.** The layer falls from 17,773 lines to ~4,800 (−73%), and the mandatory-read contract from
991 lines across nine files to ~285 across five (−71%). Three workflows become one, seven labels
become two, two ref namespaces
retire. The required `test` job stops carrying most of 10,407 lines of agent tests on every product
pull request, three times over.

**Bad, and named rather than minimised.** The two-round cap becomes a convention with no counter
behind it, which deliberately re-exposes the failure mode of #276 — nine rounds against a limit of
two. Three things make that acceptable now and did not exist then. The single long-lived coordinator
session that made nine rounds possible was abolished by ADR-0057, whose own text says *"what makes
either enforceable is that a worker is short-lived."* Greptile is hard-capped at 50 credits per seat
per month — a vendor term, so not pinnable to any revision here, but one this repository encodes and
versions as `INCLUDED_CREDITS = 50` in `.agents/bin/greptile_usage.py` and documents in
`.greptile/README.md` — so a runaway's worst case is that the seat empties and reviews stop:
bounded, self-healing, and nothing is billed. CodeRabbit is fair-use-limited by the vendor on terms
it does not publish and adapts to usage, so no figure is quoted for it here; its one unbounded path
is the usage-based-billing offer, which the contract forbids taking. **If either vendor changes
those terms, this paragraph is the part of the record that stops holding** — the decision to drop
the counter rests on them, and nothing in this repository would detect the change.

Also lost: the advisory diff-budget report, whose thresholds ADR-0057 already records as
miscalibrated for new-executable work; the materiality digest, which no decision reads; and
automatic resumption of a stranded pull request, which the empty `refs/lane-advances/` namespace
shows has never once occurred.

**Reversible.** Every removal is subtractive and restorable with `git revert`. The one irreversible
step is deleting the remote labels, which is sequenced after **`triage.py`** stops writing them —
`POST /repos/{owner}/{repo}/issues/{number}/labels` auto-creates a missing label, so retiring them
while a writer survives would silently recreate them. `triage.py` is the writer that matters here
and it is the one the removal deletes; the reaper writes only `agent:needs-amend` and
`agent:conflicted`, which are kept, so waiting on the reaper would wait on an event that never
comes.
