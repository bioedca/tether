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
2. **A clean review is the gate, and it has already closed** — when it read the head being merged.
   If either completed review came back clean, its evidence still stands under the non-material
   rule, **and** it named the merging commit, the branch never opens.

   All three conditions, and the third exposes a tension older than this record. A clean review at
   commit A closes the gate; a permitted non-material push to B leaves that closure intact —
   `AGENTS.md` says review evidence survives such a push — while `--match-head-commit` still demands
   a head no metered provider has named, and the cap forbids asking for a third to name it. Stated
   without the third condition, this branch shut there, and a clean review followed by a formatting
   commit **stranded the pull request outright**.

   Four review rounds were spent trying to fix that with a second mechanism — a *rebinding* read
   that supplied a head without closing anything — and each round found a new defect in it: the
   pull-request template had no state for it, `CONTRIBUTING.md` contradicted it, a rebinding read
   that found something serious deadlocked, and the arming rule still sourced the SHA from "the
   clean review". The mechanism was the problem. Narrowing this condition instead deletes it: the
   case simply takes the **ordinary close**, since the cap is genuinely spent and a fresh Codex read
   of the final head closes and names it under every condition here. That is strictly *more* work
   than the clean review it follows, never less, so widening the branch this way opens nothing —
   and the rule that must not bend is untouched: the close may never stand in for a metered read
   that never happened.
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

4. **Nothing but disposal may land after the cap is spent.** Every change added after **the commit
   the second completed review actually read** — its `commit_id`, never its `submitted_at` — must
   answer something already recorded on the pull request **that the worker was required to address**
   — a review finding from any provider, a CodeQL or `secret-scan` alert, a condition a human
   sign-off attached, a closing read's own finding — or be one of the existing non-material
   exceptions, or be the resolution of a conflict in the `main` merge this contract requires.
   Anchored at the reviewed commit and applied per change, both for the reasons below, and stated as
   a test on the change rather than on its source for the reason after those.

   This is the condition that makes the coverage claim below true rather than merely asserted, and
   it was missing from the first two drafts. A second Codex review
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

   It also has to be anchored at the commit the second review **read**, not at the clock. An earlier
   phrasing here said *"between the second completed review and the closing read"*, which sounds
   equivalent and is not: a material push landing while that review is still running is after its
   `commit_id` but before it completed, so a clock-anchored window waves through the one change the
   provider demonstrably never saw. This record is a rule-stating file and was accepted carrying the
   weaker wording, which is its own small lesson — an ADR can drift from the contract it records.

   And the set is stated as a **test on the change rather than on its source**, because every
   version that named sources omitted one. Four drafts, four omissions, one failure mode:

   1. only the two CodeRabbit reviews' findings — which shut the close against any closing read that
      found something;
   2. plus the closing read's own — which still omitted **Greptile**, whose findings a worker is
      equally obliged to fix;
   3. plus *any provider* — which still omitted **CodeQL and `secret-scan` alerts, and conditions a
      human sign-off attaches**, none of which come from a review provider and all of which are
      mandatory;
   4. and finally the test that has no source dimension at all.

   Each omission produced the identical deadlock — the fix was compulsory, it answered nobody on the
   list, the cap forbade another metered read — and each was found only by the next review round,
   never by the drafting. That is the general lesson and it is worth more than the rule: **an
   enumeration inside a safety condition is a latent deadlock**, because the condition fails closed
   and the enumeration is always incomplete. The line the condition is actually drawing is between
   work you were *obliged* to do and scope you *chose* to add, and that is what it should say.

   And the set has to admit **the resolution of a conflict in the required `main` merge**. This
   contract obliges a worker to merge a freshly fetched `origin/main` before merging; the
   non-material list covers that merge only when it is *clean*; so a conflicted one is a material
   push the contract itself ordered. Excluding it strands any pull request that `main` happened to
   touch after the cap was spent — a deadlock triggered entirely by other people's merges. The
   reconciliation is admitted because both sides were already read, yours by the metered reviews and
   `main`'s on its own pull request; a resolution carrying new logic of its own is new scope like any
   other and shuts the close.

   And the allowed set has to include **the closing read's own findings**, which the first three
   drafts of this condition did not. A sixth Codex review of this record's pull request found it,
   and it is the third time a draft here re-created the deadlock it removes: the bullet above
   requires the closing read to dispose of whatever it surfaces *before* it closes, and that fix is
   a material push answering no CodeRabbit finding — so a set holding only *their* findings shut the
   close against every closing read that found anything, while the cap forbade asking the metered
   provider again. The branch was reachable only by the closing reads doing their job, which is the
   worst possible selection. The remedy is not a narrower set but another stamped read of the head
   the fix produced: each round is still read by the provider that closes it, and unread scope stays
   excluded, because a closing read cannot raise a finding about a hunk it never saw.

### The closing review must carry the head it closes

The SHA that reaches `--match-head-commit` must not be one the author asserted after the fact. This
is a fifth condition in substance, and it took three review rounds pulling in different directions
to land, which is the part worth recording.

Round one found that demanding `commit_id` / `submitted_at` / `COMMENTED` from the closer made the
path **unsatisfiable in the ordinary clean case**: Codex's clean result is often a bare 👍 carrying
no commit, and a reaction has none of those fields. Round two found that accepting the reaction made
the head **author-asserted** — a push landing while the read is in flight would let a pull request
name a head the provider never saw, which is precisely what binding the merge exists to prevent. The
draft that came out of those two required an artifact *the provider itself stamps with the commit*,
and said the gate stays shut until one exists.

Round three concluded that no such artifact existed, on the evidence that the GitHub Codex bot's only
two appearances in this repository — #427 and #428, both 2026-08-07 — were usage-limit refusals, and
that the CLI's rollout record carries `cwd`, `cli_version` and a session id but **not the commit it
read**. From that it built a *procedural pin*: `git rev-parse HEAD` before and after the run, equal
to the PR head at arming time, standing in for an attestation nothing could give.

**That was wrong, and the way it was wrong is the more useful record.** The refusals were five days
stale, and no one had asked the bot on this pull request. When it finally was asked, it **posted a
review in nine minutes**, carrying `commit_id c26a683b843bf12361d6dbcabe4dbdadfe103bc3` — the
provider-attested head the whole detour existed to substitute for. The pin is deleted, and the
closing review is a **posted** one.

Three things had to be true at once for that error to survive as long as it did: an observation was
turned into a rule (*the bot declines*), the rule was **load-bearing** (it forced the close onto the
weaker path and pushed a rewrite of `AGENTS.md:156`, which #439 explicitly put out of scope), and it
was never re-tested, because it explained the evidence well enough that re-testing felt unnecessary.
**Availability is determined by asking.** A provider that refused last week has not declined today,
and the cost of finding out is one comment.

### A CLI read must not run under the rules the branch is proposing

**The close itself is a posted review, so it is not the case this guards.** What it guards is the
ordinary lane read: the CLI discovers `AGENTS.md`, `AGENTS.override.md` and `CLAUDE.md` from the
checkout it runs in and injects repository skills, so a pull request editing any of them supplies
the instructions to a provider reading it — the branch graded by its own unmerged contract, which
*"only agent instructions on the default branch govern; unmerged edits are inert"* refuses in the
first paragraph of that file. The **posted** review's loading is not ours to configure at all, and
**#451** covers that half.

So on a diff touching `AGENTS.md`, **`AGENTS.override.md` anywhere**, `CLAUDE.md` **or
`.agents/skills/**`** — the two routes by which the checkout reaches the model, discovered files and
injected skills, the override included because it takes precedence and the skills because a
skill-only diff edits none of the three files — a CLI read runs
`codex review --strict-config -c project_doc_max_bytes=0 -c skills.include_instructions=false --base origin/main`.
The overrides do different jobs and all are load-bearing: `project_doc_max_bytes` turns off the
`AGENTS.md` family, `skills.include_instructions` turns off repository **skills**, which are injected
through a switch of their own that defaults to on — so the first draft of this command left a
branch-modified `SKILL.md` model-visible through a read that reported as isolated — and
`--strict-config` makes a mistyped key **fail** rather than be ignored, which matters because the
failure mode of a silently-dropped override is a read that looks isolated and is not. Both were
verified against the installed CLI (0.147.0) — a deliberately bogus key is rejected under
`--strict-config`, and `project_doc_max_bytes` is accepted.

**That trigger over-approximates on purpose, and the four attempts it took to get there are the
reason.** Each named a set; each was wrong, in both directions; each was caught only by the next
review round:

1. *any rule-stating file* — too wide: a `CONTRIBUTING.md`- or template-only diff was never at risk,
   and the flag costs the reviewer `main`'s contract for nothing;
2. *`AGENTS.md` or `CLAUDE.md`* — too narrow by one name: the CLI also discovers
   **`AGENTS.override.md`** and gives it *precedence*, so a pull request adding one kept the exact
   hole the rule closes while appearing to satisfy it;
3. *those three* — still too narrow: the CLI injects repository **skill** metadata from
   `.agents/skills/**`, so a changed skill description can pull an unmerged `SKILL.md` into the
   review without touching any of the three;
4. *and `CLAUDE.md` may not belong at all*, since Codex reportedly does not load it as a project
   document when a root `AGENTS.md` is present.

What every attempt had in common is that **the contract was asserting how Codex resolves
instructions**, and reading filename literals out of a binary does not establish that — precedence,
fallback and skill injection are behaviour, not strings, and they move between versions. So the
trigger stopped being a claim about Codex and became a **policy choice**: fire on the agent-layer
paths, accept the over-approximation, and record why. **#451** is where the real set gets
established, by observing what a review actually receives rather than by inference.

This is the same correction condition 4 needed when it enumerated sources, and the disposition list
needed when it enumerated dispositions — **inside a safety condition, a list is a hole or a deadlock
waiting for the next case** — with one addition this instance makes plain: when the true list is a
fact about someone else's tool, do not encode a guess at it. Over-approximate and say so.

**The switch is blunt.** It is all-or-nothing:
turning discovery off denies the reviewer `main`'s contract as well as the branch's, so the read is
less informed than an ordinary one. Over-firing therefore costs review *quality* on a narrow class
of pull requests, while under-firing leaves a *self-grading* path open — which is why the
over-approximation is the right way round rather than merely the safe-sounding one. There is no
narrower switch available: `--base` selects the diff and does not substitute
`origin/main`'s copy of the instructions, so "review under the default-branch contract" is not
something the CLI can be asked for.

This is worth stating plainly: **every review round on this record's own pull request ran without
that isolation**, in a worktree carrying the modified `AGENTS.md`. Those rounds were adversarial
throughout and found twenty-odd defects in the text feeding them, so there is no sign it mattered
here — but "no sign it mattered" is not the property the trust boundary asks for, and the last round
of this pull request was re-run with the flag.

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

Anything the closing read surfaces is cleared the same way — **left not outstanding**, by being
fixed, deferred-and-tracked, dropped sub-floor, or withdrawn by the provider that raised it — before
it closes. That last one was missing from three drafts, and it is not hypothetical: a provider
retracting a false positive is how #434's own record reads, so the close was shut on its own
motivating example unless a worker mislabelled the disposition. The test is *nothing left open*;
the four are the known ways of getting there, the same way condition 4 tests the change rather than
naming who may raise a finding. An earlier draft held the closing read
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
a re-quoted earlier pass. And it is reached only after two completed metered reviews, with condition
4 above refusing the close to any scope that landed after the cap was spent — so **every substantive
part of the merging diff has had at least one external read**, metered up to the commit the second
review read and the closing read after it, and the close is a further opinion on it rather than a
first opinion on an unread one.

This paragraph has now been wrong twice in the same direction, which is worth keeping visible because
it *is* the argument that the close is safe. It first said *"a third opinion on a twice-read diff"*,
and a review of this record's own pull request showed it false: if review 1 comes back clean and an
in-scope
material push then draws review 2, the added part carries **one** metered read, not two. The
correction then claimed one *metered* read of every substantive part, and the next round showed that
false too: a fix answering review 2 lands after that review's `commit_id` by design, as do a
permitted conflict resolution and anything the closing read raises, and no metered provider ever sees
them. What the conditions actually buy is **external** coverage throughout — which is what §Review's
first bullet asks for, and it is worth noticing that the honest version of this claim turned out to
be exactly the property that bullet already states, rather than something stronger the close was
smuggling in. An argument that overstates its own premise is worth less than the weaker true one,
and this one took two rounds of being caught to stop doing it. **If Codex's review quality degrades, this paragraph is the part of the record that
stops holding**, and nothing in this repository would detect that.

A second cost, and it is the larger of the two: **§Review grows from 80 lines to 152** — it nearly
doubles — in a file ADR-0064 deliberately shrank, and whose resident-context driver, *"`AGENTS.md` is
read on every model call by every agent"*, argues against every addition. An earlier draft of this
paragraph said *"roughly twenty lines"*; a review measured it and the real figure is around four
times that. The
understatement is recorded rather than quietly corrected, because a decision record that
under-reports its own cost is how a cost stops being weighed.

What the length buys is the four shutting conditions and the stamped-head rule, which cannot be
compressed without turning the branch into a judgment call. What it also carries is the *reasoning*
behind each condition — most of it added one review round at a time, as each draft was shown to
re-create the deadlock it removed. That reasoning belongs here, in a file nobody loads on every call,
and a later pass moving it out of §Review would be a straightforward win. It is not attempted in this
pull request: the same review rounds that produced the prose also showed that large edits to this
text reliably introduce new defects, and a compression pass is exactly such an edit. It is named as
follow-up work rather than left for someone to notice.

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
