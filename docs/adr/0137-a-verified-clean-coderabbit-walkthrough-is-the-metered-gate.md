<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0137 — A verified clean CodeRabbit walkthrough is the metered gate, and the Codex handoff ends

- **Status:** accepted; amends [ADR-0065](0065-a-spent-metered-cap-closes-on-the-unmetered-provider.md)
  and [ADR-0069](0069-one-metered-review-then-the-codex-close.md) where they read a clean CodeRabbit
  review as a review object; their other decisions govern unchanged
- **Date:** 2026-11-23
- **Deciders:** bioedca
- **PRD anchor:** §12 (development & version-control protocol)
- **Milestone:** none (cross-cutting; the review lane was built in M11)

## Context and problem statement

Two things changed on 2026-11-23, and both live in the agent layer, which takes a capability change
only from a maintainer-opened issue (ADR-0064). #1318 is that issue.

**The clean CodeRabbit result.** `AGENTS.md` §Review asked for the 40-hex head an artifact records,
and named two shapes of artifact: a review object's `commit_id`, and Codex's clean comment carrying
`Reviewed commit: <short-sha>`. A clean CodeRabbit full review often posts neither. It posts a
walkthrough comment, and the App's finish reply and its `CodeRabbit` check say the review is done;
no review object is submitted. The cap-spent close (ADR-0065) asks for a *submitted* review in state
`COMMENTED` or `APPROVED`, so a thread that read only `AGENTS.md` saw no completed CodeRabbit review.
It then either asked CodeRabbit a second time, which breaks the one-review cap (ADR-0069), or waited
for a Codex close that the rule did not need.

The maintainer settled it on #1298, 2026-11-20 06:21Z
([comment](https://github.com/bioedca/tether/issues/1298#issuecomment-7032172051)):

> For future PRs while this issue is open, a verified clean CodeRabbit walkthrough at the live head
> satisfies the metered review gate under this standing approval. The walkthrough suffices on this
> path without an extra Codex closing review or a separate decision on the artifact format for each
> PR.
>
> For each completed full review, record permalinked evidence and UTC timing for the full-review
> request, the CodeRabbit App's finish reply, and the matching completion check. Verify the App
> identity, exact run ID, explicit clean verdict, and reviewed range ending at the live 40-hex head.
> The provider's coverage record must name that same head. Check all finding surfaces, including
> inline, grouped and outside-diff comments, and verify that all review threads are resolved with no
> outstanding or disputed findings.
>
> Keep the ordinary Codex review before metered review, the existing findings and cap-spent closing
> rules, the one-completed-review cap, and the queue guards. Merge still requires separate
> maintainer authorization for the reviewed head and all ordinary merge requirements.

The Codex lane cleared the gate of thirteen open pull requests on it. On 2026-11-23 20:44Z
the maintainer chose, on a decision card in the project thread, that the Claude threads apply it too
and that it goes into `AGENTS.md`.

**The Codex handoff.** From 2026-11-19 to 2026-11-23 the Codex lane carried the review and merge work
on the open pull requests under `docs/agents/handoff.md`, with a snapshot of every open pull request
in `docs/agents/handoff-snapshot.md` (PR #1296). The page said it ends "when the maintainer says the
Claude lane has resumed", and that "a later pull request removes this page and its pointer in
`AGENTS.md`". The maintainer said so at 20:35Z ("Codex handoff ended"). At 20:42Z every open pull
request that carried the `handoff:codex` label got a `Handoff log: released` entry naming its head,
stage and next step, and the label was removed from it and from every closed pull request.

## Decision drivers

- The rule must be readable off the pull request, as every other gate condition is (ADR-0065): the
  evidence is a list of permalinks and times, not a worker's account.
- A walkthrough is not weaker evidence than a review object when the App says it finished, names the
  head it read and found nothing. Asking for a review object anyway buys nothing and spends the cap.
- `AGENTS.md` has a byte budget (`tests/test_agent_contract_is_runnable.py`, 29,696 bytes), measured
  on a Windows checkout with CRLF endings, and on `main` it stood at 29,695 with CRLF. The rule can
  enter only if other text leaves, so the reasoning lives here.
- A rule page for a lane that no longer runs is a page every worker must still consider. Removing it
  is the step the page itself named.

## Considered options

1. **Keep the decision on #1298 only.** Not chosen: it binds only "while this issue is open", lives
   outside the contract, and a worker who has not read #1298 gets it wrong.
2. **Require a Codex close after every clean walkthrough.** Not chosen: the maintainer rejected it in
   the decision itself ("without an extra Codex closing review"), and Codex is uncapped but not free
   of wall-clock time.
3. **Write the rule into `AGENTS.md` §Review as a third artifact shape** (chosen), with the evidence
   list, and point here for the reasoning.
4. **Move the metered gate to Greptile** (#1316). Out of scope: not decided when this record was
   written. If #1316 is accepted it amends this record; CodeRabbit's walkthrough rule then still
   covers whatever CodeRabbit review remains.

## Decision outcome

1. **A clean CodeRabbit full review may be a posted walkthrough with no review object.** It is
   *the clean CodeRabbit review* for every rule of `AGENTS.md` §Review once it is verified at the
   live head. The pull request body records, each with a permalink and a UTC time:
   - the full-review request (asked through the queue, as before);
   - the CodeRabbit App's finish reply and the matching completed `CodeRabbit` check;
   - the App identity (the CodeRabbit GitHub App, not a user account) and the run ID;
   - the explicit clean verdict;
   - the reviewed range, ending at the live 40-hex head; the provider's coverage record names that
     same head;
   - that every finding surface was read (inline, grouped and outside-diff comments) and every
     review thread is resolved, with no outstanding or disputed finding.
2. **Such a walkthrough needs no Codex close.** It is a clean review, so the case of `AGENTS.md`
   "if a completed review came back clean … that review is the gate" applies, and the cap-spent close
   of ADR-0065 does not. A walkthrough that carries findings is not clean: dispose of them and close
   on Codex exactly as before.
3. **A walkthrough whose head a permitted push has moved is the completed review of the cap-spent
   close.** A clean `main` merge, formatting, or a comment edit may move the head past the commit
   the walkthrough read. The walkthrough then no longer names the head being merged, and asking
   CodeRabbit again would break the one-review cap. So a walkthrough verified at the head it read
   counts as the *completed* CodeRabbit review that the cap-spent close of ADR-0065 needs, as a
   submitted review in state `COMMENTED` or `APPROVED` does, and a fresh Codex review of the final
   head closes the gate. Greptile found this gap in the first draft of this record (PR #1319).
4. **Unchanged:** Codex first and uncapped; one completed review per metered provider (ADR-0069);
   the queue and its guards (read the `CodeRabbit` check before every ask); the load-bearing Greptile
   route (ADR-0099); and a separate per-PR merge go-ahead pinned to the reviewed head.
5. **The handoff pages are removed**, with their pointer in `AGENTS.md`. The repository copy of the
   queue script (`.agents/bin/cr_next_free.py`) and its priority list stay: the script's own usage
   text is its documentation, and the project-folder queue rule is unchanged.
6. **Two passages of `AGENTS.md` keep their rule and lose their reasoning** to make room under the
   byte budget. The Codex CLI bullet keeps "it would run under rules that have not merged" and cites
   ADR-0065, which records what was observed on codex-cli 0.147.0. The agent-layer sub-floor bullet
   drops its sentence on why it inverts the deferral rule; ADR-0064 records the loop it closes.

### Consequences

- A thread resuming a pull request whose log or body records a clean walkthrough reads the cap as
  spent and the gate as cleared; it does not ask CodeRabbit again.
- #1298 is resolved by this record and closes with the pull request that adds it.
- The old `Handoff log:` comments stay on the pull requests as their history: a `coderabbit-asked`
  entry followed by a completed review means the cap is spent.

## More information

- Issue #1318; decision source #1298
  ([comment 7032172051](https://github.com/bioedca/tether/issues/1298#issuecomment-7032172051)).
- PR #1296 added the handoff pages; #1316 proposes the Greptile gate.
- Related: ADR-0064 (the agent layer is feature-complete), ADR-0065 (the cap-spent close),
  ADR-0069 (one metered review), ADR-0099 (the load-bearing Greptile route).
