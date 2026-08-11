<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0065 — A spent metered cap closes on the unmetered provider, not on a maintainer

- **Status:** accepted; supersedes the review-gate terminus of [ADR-0064](0064-the-agent-layer-coordinates-writers-not-reviews.md), whose other decisions govern unchanged
- **Date:** 2026-08-10
- **Deciders:** bioedca
- **PRD anchor:** §12 (development & version-control protocol)
- **Milestone:** M11 - Agent-swarm infrastructure

## Context and problem statement

> **What of ADR-0064 survives.** Everything except one clause. Its four coordination controls, its
> drop rule, its feature-complete boundary and its removals **govern unchanged**; this record
> supersedes only the fourth of the four review rules it kept — *"one CodeRabbit review with no
> actionable comments is the last gate before merge"* (ADR-0064:311–312). That distinction is stated
> here rather than compressed into the `Status` bullet because `scripts/gen_adr_index.py` extracts
> that field with a single-line pattern and copies it verbatim into the index.

Three rules in `AGENTS.md` §Review were individually sound and jointly unsatisfiable:

| line | rule |
|---|---|
| `:188` | CodeRabbit is the last gate — **at least one review with no actionable comments** |
| `:211` | **Two completed reviews per METERED provider**, then stop |
| `:221` | If a third pass would be needed, **hand the PR to the maintainer** |

If both permitted metered reviews found anything at all, the zero-finding review the gate demanded
required a third read, which the cap forbade. The PR was then stuck behind a human indefinitely,
with nothing wrong with it.

### The rule priced diligence as failure

This is the defect, and it is sharper than the deadlock it produced. The old gate bundled two
different questions into one test:

1. *Were the findings dealt with?*
2. *Did a provider read the head being merged?*

and then assigned both to the **most expensive** provider in the lane. The arithmetic that falls out
runs backwards. A review that finds three real problems costs two credits to close — one to find
them, one to confirm the fixes — while a review that finds nothing costs one. A pull request was
charged for having been reviewed usefully, and the charge was paid in the currency ADR-0064 was
most careful about: the maintainer's metered budget.

That inversion is why the deadlock is not an edge case. It is reached by exactly the PRs the lane is
working correctly on.

### It was reached

[PR #434](https://github.com/bioedca/tether/pull/434) is the first pull request to arrive there, on
2026-08-10, and the record is complete because the state was documented as it happened:

- 15/15 required checks green, `mergeStateStatus: CLEAN`, every commit signed.
- Codex reviewed five times and converged with nothing blocking outstanding.
- CodeRabbit review 1 at `454eebf` — `Actionable comments posted: 2`. Both fixed. On the replies
  CodeRabbit **confirmed one and withdrew the other** in its own words: *"I withdraw the finding."*
- CodeRabbit review 2 at `359675e` — `Actionable comments posted: 1`, labelled 🟡 Minor. Fixed in
  `02fb081`, a **three-line comment edit** — squarely on the non-material exception list.
- All three review threads resolved. Nothing outstanding.

Two completed metered reviews, every finding disposed of, and the pull request could not merge.

### Two things already anticipated this

**`claim.py doctor` detects the class and cannot remedy it.** Its `unarmed` section reports *"open
pull requests that are finished and that nothing will merge"* — precisely this state — but `doctor`
reports and never writes, by design and with a test asserting it issues no write verb. Detection
without an agent-reachable remedy is what produces a stranded pull request rather than a fixed one.

**ADR-0064 booked the loss.** Its §Consequences lists *"automatic resumption of a stranded pull
request"* among what the cut gave up, on the evidence that the empty `refs/lane-advances/` namespace
showed it had never once occurred. That evidence was true when written. It is no longer.

### The section already handled the same situation correctly one provider over

`AGENTS.md:183–187` disposes of an exhausted **metered** provider in words: *"if the seat is empty
record 'Greptile: no credits this month' and move on; **exhaustion never blocks**."* A spent
CodeRabbit cap is the same situation and blocked. This record makes CodeRabbit consistent with
Greptile rather than inventing a principle for it.

## Decision drivers

- **The unmetered provider is already trusted and already uncapped.** `AGENTS.md:165` calls Codex
  *"unmetered and so uncapped"* and `:168` calls it *"not optional: it is what makes the metered
  providers affordable."* Nothing new is being extended to it; it is being allowed to finish.
- **The safety property must not move.** *"You are never the only reviewer of your own diff"* must
  hold identically after the change.
- **No new machinery.** ADR-0064 established that review state has no concurrency content and needs
  no ledger, label state machine or launcher. The fix has to be prose an agent evaluates.
- **The branch must not become the preferred path.** An agent must not be able to reach a cheaper
  route by deliberately spending the cap.

## Considered options

1. **Keep the escalation.** Rejected: it is the human gate this record exists to remove, and #434
   shows it fires on well-reviewed work rather than on risky work.
2. **Raise the cap to three.** Rejected: it moves the deadlock rather than removing it, and spends
   more of the budget ADR-0064 was protecting. A fourth review would be demanded next.
3. **Reframe the gate for every PR** as *"every finding disposed"* rather than *"the review found
   nothing."* Rejected, though it is the most intellectually appealing of the three. It swaps a
   third-party attestation for a self-attestation on the **default** path, where the deadlock is
   rare; it collides with `CONTRIBUTING.md`'s rule that a credit-funded review must not be
   answerable entirely by deferral; and since *serious* is the provider's own labelling, it would
   let every 🟡 Minor exit through deferral with no re-read. A rare deadlock does not justify
   loosening the common case.
4. **A narrow branch that opens only on a spent cap.** Adopted.

## Decision outcome

**When the metered cap is genuinely spent and every finding is genuinely disposed of, a fresh Codex
read of the final head closes the gate in the metered provider's place. The maintainer escalation is
deleted rather than supplemented.**

### What makes it narrow

Four conditions shut the branch, and each is **readable off the pull request** rather than out of
an agent's account of its own reasoning:

1. **A refusal is not a spent cap.** A throttle, a quota refusal or a failed run reviewed nothing,
   so it is a wait, and waiting is still what you do. This also means an *unavailable* CodeRabbit
   still freezes the pull request — nothing reviewed, so nothing opens the close.
2. **A clean review is the gate, and it has already closed.** If either completed review came back
   clean and its evidence still stands under the non-material rule, the branch never opens.

   With one carve-out that review found, and it exposes a tension older than this record.
   *Closing the gate* and *supplying a head the merge can bind* are two different jobs. A clean
   review at commit A closes the gate; a permitted non-material push to B leaves that closure intact
   — `AGENTS.md` says review evidence survives such a push — while `--match-head-commit` still
   demands A, which is no longer the head. With the cap spent there is no third metered read to
   rebind it, so a clean review followed by a formatting commit **stranded the pull request
   outright**. A stamped Codex read of B supplies the binding without re-opening a gate that was
   never in question. The rule that must not bend is the other one: the close may never stand in
   for a metered read that never happened.
3. **The second review must have been asked after the first one's findings were disposed of** — by
   commits that answer them, or, where the disposition is a deferral or a sub-floor drop, by the
   replies and resolutions that record it. Asking twice at one head with nothing answered in
   between is one review asked twice.

   **The test is the disposal, not a new commit**, and an earlier draft got this wrong in a way
   worth recording. It required the two reviews to sit at *different `commit_id`s*. Codex's review
   of this record's own pull request found the hole: when a review's findings are all non-serious,
   the prescribed disposition is deferral or a sub-floor drop, which moves **no head**. A second
   review at that same head would then have been locked out of the close while the cap forbade a
   third — **the deadlock re-created by the rule written to remove it**. A rule that fixes a
   deadlock must be checked against its own failure mode, and this one was not until a provider
   checked it.

4. **Nothing but disposal may land after the cap is spent.** Every commit between the second
   completed review and the closing read must answer a finding those reviews recorded, or be one of
   the existing non-material exceptions.

   This is the condition that makes the *"third opinion on a twice-read diff"* claim below true
   rather than merely asserted, and it was missing from the first two drafts. A second Codex review
   of this record's own pull request found it: a material push **after** the cap is spent leaves the
   cap spent, so the close still applied — and the new code would then be read by the closing
   provider and by nobody else. Material pushes re-arm review but do **not** raise the two-review
   ceiling, so there was no path by which a metered provider could ever see that scope. It also made
   the branch reachable by choice, which conditions 1–3 were written to prevent: push the risky part
   last. A safety property that holds only when the author does not think to break it is not a
   safety property.

   The same condition also has to be read **per change, not per commit**. A commit that answers a
   recorded finding and carries an unrelated hunk alongside it satisfies any per-commit phrasing
   while smuggling in exactly the scope the condition excludes.

### The closing read must stamp its own head

The SHA that reaches `--match-head-commit` must come from something the **provider** wrote. This is
a fifth condition in substance, and it was the last one found.

Codex's clean result is often a bare 👍 carrying no commit. Two review rounds pulled in opposite
directions here, and the resolution is the interesting part. One round found that demanding
`commit_id` / `submitted_at` / `COMMENTED` from the closer made the path **unsatisfiable in the
ordinary clean case**, since a reaction has none of those. The next round found that accepting the
reaction made the head **author-asserted rather than provider-attested** — and a push landing while
the read is in flight would then let a pull request name a head the provider never saw, which is
precisely what binding the merge exists to prevent.

Both are right, and the resolution is not a compromise between them: a reaction is a perfectly good
*lane result* and simply is not a *close*. What closes is any artifact the provider itself stamps
with the commit it read. Where the only output is an unstamped reaction the gate stays shut until a
stamped one exists, which costs an unmetered re-run and nothing else.

**Motive is deliberately not a test.** An earlier draft closed the gaming path with *"an ask made to
spend the cap is not one of the two."* That was rejected on review for two reasons: a motive is not
checkable by anyone, including the agent itself; and read strictly it **restores the deadlock in a
new form**, since an agent that suspects its own motive concludes the cap is unspent, so the close is
shut, so it must ask again — which it cannot, because two asks have been made. A rule that fixes a
deadlock must not be able to re-create one.

The remaining deterrent is structural rather than moral: spending an ask to reach the close **buys
nothing even if it works**, because the close costs the disposal of every finding *plus* a further
review on top. It is strictly more work than the clean pass it replaces.

### The close is a substitute, not a discount

Anything the closing read surfaces is disposed of by the same three dispositions — fixed,
deferred-and-tracked, or dropped sub-floor — before it closes. An earlier draft held the closing read
to *"nothing blocking"*, which would have silently dropped two severity bands relative to the
zero-actionable-comments bar it replaces, and on agent-layer paths those findings are not tracked at
all. The bar does not move; only who holds it does.

### Clearing the gate is still not authority to merge

Stated as its own rule rather than as a tail clause, because it is the most damaging available
misreading. `AGENTS.md:32–33` — *"Do not infer merge authority"* — is untouched.

### What the merge binds to

`--match-head-commit` still names *"the 40-hex head the clean review read."* When the close applies,
the Codex closing read **is** that clean review. The merge bullet is left byte-identical: it is the
only place in `AGENTS.md` satisfying both mechanical guards in
`tests/test_agent_contract_is_runnable.py` — the `_SHA_DEFINED` window and the arming-page floor —
and its referent was always provider-neutral in words. Only context had pointed it at CodeRabbit.

## Consequences

**Good.** A pull request that has been reviewed, has had its findings fixed and its threads resolved
can finish without a human. The escalation is deleted from all **three** files that carried it —
`AGENTS.md`, `CONTRIBUTING.md` and
[`docs/PRD.md`](https://github.com/bioedca/tether/blob/main/docs/PRD.md) §12.4. An earlier draft of
this record said *two*, and the third was found by review rather than by the sweep that looked for
it: the PRD phrases it as *"the lane stops for the maintainer"* rather than *"hand the PR to the
maintainer"*, so a
phrase-matched search missed it. **A rule stated in five files is found by reading all five, not by
grepping the wording you happen to remember** — and this record is the third place in this pull
request where a fix landed in one file and not its mirrors. Deleting it **repairs** `AGENTS.md:227`, *"Human sign-off: releases,
tags, signing, any new scientific claim or citation. Nothing else waits,"* which was false while a
capped review gate waited on a human. That sentence becoming true again is affirmative evidence the
escalation was the anomaly rather than the design.

**Bad, and named rather than minimised.** The terminal verdict on a capped PR now comes from the
provider the repository does **not** pay for, and Codex's reliability is therefore load-bearing in a
way it was not before. Two things bound that. It is a fresh read of the exact head being merged, not
a re-quoted earlier pass. And it is reached only after two completed metered reviews have already
read the diff, and condition 4 above is what keeps that true by refusing the close to any scope that
landed after the cap was spent — so it is a third opinion on a twice-read diff, never a first opinion
on an unread one. **If Codex's review quality degrades, this paragraph is the part of the record that
stops holding**, and nothing in this repository would detect that.

A second cost: §Review grows by roughly twenty lines in a file ADR-0064 deliberately shrank, and
whose resident-context driver — *"`AGENTS.md` is read on every model call by every agent"* — argues
against every addition. The four shutting conditions and the stamped-head rule are what that length
buys, and they are the
part that cannot be compressed without making the branch a judgment call.

**Reversible.** Prose only. No script, no workflow, no label, no ref namespace, no test fixture. A
`git revert` restores the escalation exactly.

## Adoption status

Landed with this record. The six surfaces that restated the retired rule move in the same pull
request, because `AGENTS.md:177–182` puts every rule-stating file on the material list precisely so
that a push changing what the gate requires cannot leave a stale copy behind:
`.agents/skills/tether-worker/SKILL.md`, `.agents/skills/tether-worker/agents/openai.yaml`,
`CONTRIBUTING.md`, `.github/pull_request_template.md`,
[`docs/PRD.md`](https://github.com/bioedca/tether/blob/main/docs/PRD.md) §12 and
`.greptile/README.md`.

`openai.yaml` is called out because it is the likeliest omission and the most consequential one:
four lines, read by no test, and injected as the Codex lane's **default prompt**, so a stale copy
there briefs every future Codex-lane worker under a contract that no longer exists.

**This pull request cannot use the branch it introduces.** `AGENTS.md:6` says only instructions on
the default branch govern and unmerged edits are inert, and every file it touches is material, so it
re-arms its own review and is judged under the old gate. That is the correct order and not an
oversight: the rule earns its way in under the regime it replaces.
