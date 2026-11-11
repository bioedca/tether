<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0069 — One metered review, then the Codex close; Greptile stands in for a long CodeRabbit refusal

- **Status:** accepted; supersedes the two-review trigger and the third shutting condition of [ADR-0065](0065-a-spent-metered-cap-closes-on-the-unmetered-provider.md), whose other decisions govern unchanged
- **Date:** 2026-11-10
- **Deciders:** bioedca
- **PRD anchor:** §12 (development & version-control protocol)
- **Milestone:** none (cross-cutting; the review lane was built in M11)

## Context and problem statement

> **What of ADR-0065 survives.** Everything except two clauses. Its close, its closing-review
> requirements, its first, second and fourth shutting conditions and its Codex CLI rule **govern
> unchanged**. This record supersedes only the number of completed reviews a metered provider gets
> on one pull request — two becomes one, both as the cap ADR-0064 left as a convention and as the
> trigger that opens the close — and the third shutting condition, which that change leaves without
> an object. That distinction is stated here rather than compressed into the `Status` bullet
> because `scripts/gen_adr_index.py` extracts that field with a single-line pattern.

The review lane ends on CodeRabbit, and CodeRabbit is rate-limited. How does a pull request finish
its review when the last metered gate cannot be asked?

Measured on `bioedca/tether` from the GitHub REST API on 2026-11-10, for 2026-10-01 to 2026-11-10,
at `main` `f33ab6e`:

- **The limit belongs to the account, not to this repository.** CodeRabbit's own notice on #670
  (2026-11-10) reads: plan Team; *"Your 109 included PR review attempts over the past 7 days set
  your current allowance at 1 review per hour"*; reviews past the limit are billed at US$0.25 per
  file. Tether sent 23 full-review requests in those seven days. The other attempts came from
  elsewhere on the account and are not visible from this repository.
- **Refusals are common and their waits are long.** 103 full-review requests on 60 pull requests;
  21 refused. In November, 9 of 23 were refused, and the waits the refusals named were 32 to 58
  minutes. At one review per hour, every open pull request queues behind every other.
- **A second CodeRabbit review bought little.** CodeRabbit completed about 75 reviews on 56 pull
  requests. 19 were second reviews on the same pull request. Ten of those 19 reported nothing. The
  other nine reported 10 Minor findings and 1 Major finding (#541).
- **CodeRabbit still finds what Codex does not.** It reported 40 findings in the period, 35 Minor
  and 5 Major, the Majors on #502, #533, #541 and #567. Each of those pull requests had been read by
  Codex first.
- **Greptile is nearly unused.** One Tether pull request was reviewed by Greptile in the period.
  The seat has 50 credits a month, shared by three repositories.

ADR-0065 opens its Codex close only after **two** completed CodeRabbit reviews. Under a limit of one
review per hour that is two turns in the queue for every pull request whose first review found
anything, and the second turn mostly confirms fixes that the closing Codex review reads anyway.

## Decision drivers

- A pull request must still be read by a metered provider. The 5 Major findings above are the
  evidence that Codex alone is not enough.
- Money is the maintainer's. A worker never accepts usage-based billing, and never spends more
  Greptile credits than the maintainer set.
- A refusal reviewed nothing. Any rule that lets a refusal count as a review removes the gate
  exactly when the provider is busy.
- `AGENTS.md` has a byte budget (`tests/test_agent_contract_is_runnable.py`), so the rule must be
  short and the reasoning lives here.

## Considered options

The maintainer answered one question for each route on 2026-11-10.

1. **How many CodeRabbit reviews before the closing Codex review?** One, or two as today.
2. **Can Greptile replace CodeRabbit after a refusal?** Yes with a share of 15, 25 or 40 credits a
   month for Tether, or no.
3. **How long must the named wait be before a replacement opens?**
4. **Can Codex replace CodeRabbit after a refusal?** No; only when Greptile cannot review; after
   each long refusal; or only for documentation changes.
5. **Pay CodeRabbit for reviews above the limit?**

## Decision outcome

1. **One completed review per metered provider per pull request.** The Codex close of ADR-0065
   opens when **one** completed CodeRabbit review stands and no finding it raised is left
   outstanding. The worker does not ask CodeRabbit again to confirm fixes. The closing Codex review
   of the final head is what confirms them, and it must report nothing actionable, as ADR-0065
   requires.
2. **ADR-0065's third shutting condition is retired.** It required the second review to be asked
   after the first one's findings were disposed of. With one review there is no second ask to
   order. The three that remain: a refusal is not a spent cap; a clean review that still stands and
   read the head being merged is already the gate; and nothing but disposal may land after the cap
   is spent. The last is anchored at the commit the **last completed review** read, which is that
   one review, or the later of two on a pull request reviewed before this record.
3. **After a long refusal, one standard Greptile review may stand in as the metered gate.** All of
   these hold:
   - CodeRabbit **posted** the refusal on the pull request, and it names a wait of **more than 30
     minutes**. The worker quotes it in the pull request.
   - The stand-in is the Greptile review already on the pull request, if there is one. Otherwise it
     is a new one, asked only if `.agents/bin/greptile_usage.py`, read at that moment, shows that
     the seat has credits left and that Tether is under its share. The script counts each review
     submission as one credit, so the worker counts a TREX review as three in that reading. A
     balance the script cannot read is a no, and the refusal is then a wait, as before.
   - It is a standard review. A TREX review costs three credits and is not a stand-in.
   - It is held to everything `AGENTS.md` §Review asks of CodeRabbit's review: posted by the
     provider, naming the commit it read, with no actionable comments — or, where it has findings,
     each one disposed of and the Codex close applied under the same three conditions.
   - CodeRabbit is not asked again on that pull request.
4. **Tether's share of the Greptile seat is 15 credits a month**, the optional review and the
   stand-in together. `REPO_SHARE` in `.agents/bin/greptile_usage.py` records it.
5. **Codex does not stand in.** A refusal still opens no Codex close. That is ADR-0065's first
   condition, and it is unchanged.
6. **Usage-based billing stays refused.** `AGENTS.md` already says so.

Not chosen: two CodeRabbit reviews as today; a Greptile share of 25 or 40; a Codex stand-in in any
of its three forms; payment above the limit.

### Two points the questions did not ask

Both follow from the answers, and the maintainer can reverse either on the pull request that lands
this record.

- **The cap of one covers Greptile too.** ADR-0064 set one cap for every metered provider, and
  `AGENTS.md` already called Greptile *"one review in practice"*. A second Greptile ask would spend
  from a share the maintainer set at the lowest of the three figures offered.
- **A Greptile review already on the pull request counts as the stand-in.** It is a completed
  metered review of the same pull request. Buying a second one would spend a credit to learn
  nothing new, and the closing Codex review still reads the final head.

### Why the third condition can go and the last one cannot

The third condition guarded against one review asked twice at one head to reach the close cheaply.
The close now needs one review, so there is nothing to reach.

The last condition guards against new scope pushed after the metered read. That risk is larger
now, not smaller: there is one metered read, and everything after it is read by Codex alone. So the
condition stays word for word, and only its anchor moves from *the second review* to *the last
completed review*. New scope after that commit shuts the close, and no further CodeRabbit review is
available to read it. Such scope belongs in its own pull request.

### Consequences

- Good: a pull request takes one turn in the CodeRabbit queue. On the measured period that removes
  about one review in four.
- Good: a refusal of more than 30 minutes no longer stops a pull request while Greptile credits
  remain, and two different providers still read it.
- Trade-off: CodeRabbit no longer reads the fixes to its own findings. In the measured period that
  second read found 1 Major finding in 19 reviews. Codex reads those fixes instead.
- Trade-off: scope added after the one metered read has no metered provider left to read it. Under
  ADR-0065 a second CodeRabbit review could. Such a pull request now waits for the maintainer, or
  the new scope moves to its own pull request.
- Trade-off: Greptile has reviewed one Tether pull request, so nothing here measures how its
  findings compare with CodeRabbit's.
- Trade-off: `greptile_usage.py` needs the `gh` GraphQL route and read access to three
  repositories. A session that has neither reads no balance and therefore asks for no new
  stand-in. A Greptile review already on the pull request still counts, because it needs no
  balance reading.
- Nothing counts the cap or the share. Like ADR-0064's cap, both are conventions a worker keeps,
  and the merged history is auditable.

## More information

- [ADR-0064](0064-the-agent-layer-coordinates-writers-not-reviews.md) — the cap and the
  feature-complete boundary. This record changes a capability of the agent layer, which that
  boundary allows only on a maintainer-opened issue. That issue is #684.
- [ADR-0065](0065-a-spent-metered-cap-closes-on-the-unmetered-provider.md) — the close, its
  conditions, and the drafts of them that were wrong.
- The rule itself: `AGENTS.md` §Review. Working copies: `CONTRIBUTING.md`, the pull-request
  template, [the PRD](https://github.com/bioedca/tether/blob/main/docs/PRD.md) §12,
  `.greptile/README.md`, and the worker skill with its launcher prompt.
- Not decided here: the order in which concurrent workers take the one review per hour. That is a
  convention between sessions and changes no file in this repository.
