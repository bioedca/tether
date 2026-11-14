<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0099 — Small docs-only pull requests take Greptile, not CodeRabbit

- **Status:** accepted; amends [ADR-0069](0069-one-metered-review-then-the-codex-close.md), whose other decisions govern unchanged
- **Date:** 2026-11-14
- **Deciders:** bioedca
- **PRD anchor:** §12 (development & version-control protocol)
- **Milestone:** none (cross-cutting; the review lane was built in M11)

## Context and problem statement

The review lane ends on CodeRabbit, and on 2026-11-14 CodeRabbit could review one pull request an
hour. The maintainer's screenshot of the CodeRabbit usage page (2026-11-14 18:02Z) shows the
account in its "80+ review band": 83 reviews in the past 7 days, refill rate 1 review an hour. The
shared turn queue that the Claude threads use held about 50 open pull requests, so a pull request
that joined then waited about two days for its one review.

Some of those pull requests changed a few lines of Markdown. Three in the queue at that time changed
one file each: 2, 4 and 40 lines (#1014, #1008, #970). They waited behind pull requests that change
executable code, for the same metered read.

How does a small documentation change finish its review without a turn in the CodeRabbit queue?

## Decision drivers

- Every pull request is still read by a metered provider after Codex, as ADR-0069 requires. Only
  which metered provider changes.
- Greptile credits are the maintainer's money. Tether's share of the seat stays 15 a month
  (ADR-0069), and nothing here raises it.
- The test for "small docs-only" must be mechanical, so that two workers reach the same answer and
  a pull request cannot pick its own reviewer by argument.
- A pull request that changes the review rules must not route itself around the reviewer the rules
  name. ADR-0064 calls those paths the agent layer.
- `AGENTS.md` has a byte budget (`tests/test_agent_contract_is_runnable.py`), so the rule there is
  short and the definition is repeated in full here.

## Considered options

1. **Keep every pull request in the CodeRabbit queue.** No change, about two days of wait for a
   two-line fix.
2. **Let Codex close small docs-only pull requests alone.** No metered read at all; ADR-0069 already
   rejected Codex as a stand-in, and the maintainer did not ask for it.
3. **Route small docs-only pull requests to Greptile** (chosen). The maintainer's words, 2026-11-14
   18:04Z: "skip CodeRabbit on small docs-only PRs", "use greptile for those".
4. **Widen "docs-only" to include the tests that guard the documentation** (for example
   `tests/test_docs_*.py`). Not chosen: a test is executable code, and the maintainer said
   docs-only. About a dozen open pull requests on 2026-11-14 had that shape.

## Decision outcome

1. **A pull request is small and docs-only when all of these are true**, read from GitHub's list of
   changed files (the diff against the merge base with `main`), renamed files by both names:
   - every changed file ends in `.md`;
   - every changed file is under `docs/` or at the repository root;
   - no changed file is `AGENTS.md`, `AGENTS.override.md`, `CLAUDE.md` or `CONTRIBUTING.md`, and
     none is under `docs/agents/` — the agent-layer paths of ADR-0064 that can hold Markdown there,
     and `CONTRIBUTING.md`, which also states the merge rules for people;
   - additions plus deletions total at most **300** lines.
   A data file under `docs/` (for example `docs/validation/*.json`) does not qualify; an ADR, the
   PRD and the docs pages do. A new scientific claim or citation still needs the maintainer's
   sign-off, as before.
2. **Such a pull request does not ask CodeRabbit.** After Codex, one standard Greptile review is its
   last metered gate. It is held to everything `AGENTS.md` §Review asks of CodeRabbit's review: it
   is posted by the provider and names the commit it read; it is the one completed review the cap
   allows; its findings are disposed of; and, where it has findings, a fresh Codex review of the
   final head closes the gate under ADR-0065's conditions. A Greptile review already on the pull
   request is that review.
3. **The route draws on Tether's share of 15 a month**, with the optional review and the stand-in.
   A worker asks only while `.agents/bin/greptile_usage.py --fail-over-share` exits 0, counting a
   TREX review as three. Any other exit — the share used, or the count unreadable — sends the pull
   request to the CodeRabbit queue like any other.
4. **A session that cannot read the whole seat reads Tether alone.** A cloud session can reach only
   `bioedca/tether`, so it adds `--repo bioedca/tether`. The script then prints Tether's count and
   no seat balance, and the maintainer reconciles the seat on Greptile's usage page. This relaxes
   ADR-0069's "an unreadable balance is a no" for this route only; the stand-in keeps it.
5. **Qualifying is read at the head the Greptile review reads.** What lands after that review is
   bounded as after any spent cap: disposal, the non-material exceptions, and the resolution of a
   conflict in a `main` merge. A pull request that grows out of the definition before its review
   takes the CodeRabbit queue.
6. **Greptile's clean result is a summary comment, not a review object.** On #996 (2026-11-14) a
   clean review left a comment whose last line links `Last reviewed commit` to the full 40-hex
   commit, and no entry under `pulls/996/reviews`. `AGENTS.md` now names it beside Codex's clean
   comment.

### Changes to the balance script

`greptile_usage.py` could not serve this route from a cloud session, and read low:

- It listed pull requests with `gh pr list`, which reads GraphQL. The proxy of a cloud session
  refuses GraphQL, so every cloud run reported UNKNOWN. It now uses the REST search
  (`search/issues`), and reports UNKNOWN when GitHub marks the search incomplete or it holds more
  than the 1,000 results the search can list.
- It counted review objects only, so the clean #996 review counted as no credit. It now also reads
  Greptile's summary comment, and takes for each pull request the larger of the review objects
  submitted in the month and the comment's "Reviews (N)" counter. One review that leaves both is
  one credit. The counter covers the pull request's whole life, so a pull request reviewed in two
  months reads high in the second; that is the safe direction.
- `--repo` narrows the count, and `--fail-over-share` exits 1 once Tether has used its share.
  `--fail-over-budget` refuses a narrowed count, because a seat balance from part of the seat reads
  high.

The first cloud run of the new script, 2026-11-14, read Tether at 1 of 15 for November: the clean
review of #996, which the old script counted as zero.

### Consequences

- Good: a small docs-only pull request no longer waits for a CodeRabbit turn, and the queue is
  shorter for the pull requests that change code.
- Good: two providers still read each such pull request, Codex and Greptile.
- Good: a cloud session can read Tether's Greptile count.
- Trade-off: Greptile reads these pull requests instead of CodeRabbit. ADR-0069 recorded that
  nothing measured how Greptile's findings compare with CodeRabbit's, and that is still true.
- Trade-off: credits spent here are not available as stand-ins later in the month. With the share
  used, every pull request goes back to the CodeRabbit queue.
- Trade-off: a cloud session does not see the other two repositories on the seat, so it can ask
  while the seat as a whole is empty. The maintainer reconciles the seat on Greptile's usage page.
- Nothing counts the route. Like the cap and the share, it is a convention a worker keeps, and the
  merged history is auditable.

## More information

- [ADR-0064](0064-the-agent-layer-coordinates-writers-not-reviews.md) — the agent layer is
  feature-complete; this capability change is on the maintainer-opened issue #1035.
- [ADR-0065](0065-a-spent-metered-cap-closes-on-the-unmetered-provider.md) — the Codex close and
  its conditions.
- [ADR-0069](0069-one-metered-review-then-the-codex-close.md) — one metered review per pull request,
  the Greptile stand-in and Tether's 15-credit share.
- The rule itself: `AGENTS.md` §Review. Working copies: the pull-request template, the PRD §12,
  `.greptile/README.md`, and the worker skill.
