<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0099 — Load-bearing pull requests take one Greptile review, not CodeRabbit

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

Greptile was the optional review and the stand-in for a long CodeRabbit refusal, inside a share of
15 credits a month for Tether (ADR-0069). The maintainer rates Greptile as the better reviewer and
gave Tether a larger share of the seat.

Which pull requests take Greptile instead of CodeRabbit, how many credits may they use, and how does
a pull request whose Greptile review had findings close its gate?

## Decision drivers

- Greptile credits are the maintainer's money. The seat has 50 credits a cycle, shared with
  `Yeliztli` and `tbox-finder`.
- The test for the route must be mechanical, so that two workers reach the same answer and a pull
  request cannot pick its own reviewer by argument.
- A pull request gets one review from each metered provider (ADR-0069). A second Greptile review is
  not allowed.
- `AGENTS.md` has a byte budget (`tests/test_agent_contract_is_runnable.py`), measured on a
  Windows checkout with CRLF line endings, and it had about 250 bytes left (29,450 of 29,696). So
  `AGENTS.md` carries a four-line rule that names this record, and the full test and its
  conditions live here.

## Considered options

1. **Keep CodeRabbit as the gate for every pull request.** About two days of wait for each one.
2. **Small docs-only pull requests take Greptile** (the first version of this record, pull request
   #1036). The maintainer reversed it before it merged, 2026-11-14 19:16Z: "Coderabbit should
   actually do all the small doc PRs."
3. **Every mostly-code pull request takes Greptile.** Not chosen: the maintainer chose to keep the
   credits for high-risk ones (decision card, 19:20Z).
4. **Load-bearing pull requests take Greptile** (chosen). The maintainer's words, 19:18Z: "very
   important load-bearing changes that are mostly code, or touch AGENTS.md and CLAUDE.md or the
   PRD, not the rest of the docs."

## Decision outcome

1. **A pull request takes the Greptile route when either of these is true**, read from GitHub's
   list of changed files (the diff against the merge base with `main`), renamed files by both names:
   - a changed file is `AGENTS.md` (at any depth), `CLAUDE.md` or
     [`docs/PRD.md`](https://github.com/bioedca/tether/blob/main/docs/PRD.md); or
   - the pull request body's line `Risk (may only increase):` says `high`, and more than half of
     the changed lines (additions plus deletions) are in files that are not Markdown (`*.md`).

   "Mostly code" is that line count: a test, a script or a workflow is code here, and a Markdown
   file is not. Every other pull request, all the other documentation included, takes CodeRabbit as
   its last metered gate, as before.
2. **One Greptile review, never a second.** After Codex, one standard Greptile review is the
   metered gate of a pull request on the route, and CodeRabbit is not asked first. It is posted by
   the provider and names the commit it read, as `AGENTS.md` §Review asks of every leg.
   - If it is clean, it is the gate, as a clean CodeRabbit review is. If a non-material push moved
     the head after it, a fresh clean Codex review of the final head closes the gate (ADR-0065).
   - If it has findings, every finding is disposed of. Then the pull request takes **one
     CodeRabbit review** of the final head from the CodeRabbit queue, its findings are disposed of,
     and **a fresh Codex review of the final head** that reports nothing actionable closes the gate
     (maintainer, 19:26Z: "make sure PRs only get 1 greptile review, after that, if changes are
     needed to be reviewed, coderabbit and Codex can lift the final approval gate"; decision card,
     19:29Z: "CodeRabbit, then Codex"). A CodeRabbit refusal is then a wait. Greptile does not stand
     in, because the pull request already had its one Greptile review.
3. **Greptile's clean result is a summary comment, not a review object.** On #996 (2026-11-14) a
   clean review left a comment whose last line links `Last reviewed commit` to the full 40-hex
   commit, and no entry under `pulls/996/reviews`. That comment is a *completed* review for every
   rule in `AGENTS.md` §Review, the Codex close included, although it is not a review object in
   state `COMMENTED` or `APPROVED`. That link is the head the review read; the pull-request template
   names this shape on its Greptile line. Greptile found this gap on #1036.
4. **Tether may use 45 credits in each cycle** (maintainer, 19:16Z, and decision card, 19:17Z:
   "Tether alone"). This replaces the share of 15 a month of ADR-0069, and it covers every Greptile
   review on Tether: the route, the optional review and the stand-in. A standard review costs 1
   credit, and a TREX run 3 on top of its review. A worker asks only while
   `.agents/bin/greptile_usage.py --fail-over-share` exits 0. The script counts each TREX run asked
   on demand in the cycle at 3; a run that an account rule starts leaves no request, so a worker
   that knows of one adds 3 (CodeRabbit on #1038). Any other exit — the share used, the
   whole seat read and empty, or the count unreadable — sends the pull request to the CodeRabbit
   queue.
5. **A cycle starts on the 17th** of a month at 00:00 UTC and ends when the 17th of the next month
   starts (maintainer: "rolling every month on the 17th"). The counter counts by cycle.
6. **A session that cannot read the whole seat reads Tether alone.** A cloud session can reach only
   `bioedca/tether`, so it adds `--repo bioedca/tether`. The script then prints Tether's count and
   no seat balance, and the maintainer reconciles the seat on Greptile's usage page. This relaxes
   ADR-0069's "an unreadable balance is a no" for the route only; the stand-in keeps it.
7. **The route is read at the head the Greptile review reads.** A pull request that grows into the
   route after a CodeRabbit review keeps that review, and the cap of one applies.

### Changes to the balance script

`greptile_usage.py` could not serve this route from a cloud session, and read low:

- It listed pull requests with `gh pr list`, which reads GraphQL. The proxy of a cloud session
  refuses GraphQL, so every cloud run reported UNKNOWN. It now uses the REST search
  (`search/issues`), and reports UNKNOWN when GitHub marks the search incomplete or it holds more
  than the 1,000 results the search can list.
- It counted review objects only, so the clean #996 review counted as no credit. It now also reads
  Greptile's summary comment, and takes for each pull request the larger of the review objects
  submitted in the cycle and the comment's "Reviews (N)" counter. One review that leaves both is
  one credit. The counter covers the pull request's whole life, so a pull request reviewed in two
  cycles reads high in the second; that is the safe direction. The same comment, edited in a later
  cycle, no longer counts in the earlier one, so a past cycle can read low; a worker reads only the
  current cycle before it spends (Greptile on #1036, dropped as below the floor).
- It counted calendar months. It now counts cycles from the 17th: `--cycle YYYY-MM` names the
  cycle that starts on the 17th of that month, and the default is the cycle running now.
- `--repo` narrows the count, and `--fail-over-share` exits 1 once Tether has used 45, or when the
  whole seat was read and is empty: the share is a ceiling inside the seat, never a credit of its
  own. `--fail-over-budget` refuses a narrowed count, because a seat balance from part of the seat
  reads high.
- It counted a TREX run as nothing beyond its review, so fifteen reviews that each asked one read
  15 of a share that was spent. It now adds 3 for each comment in the cycle that asks Greptile for a
  TREX run on demand, billed or not (CodeRabbit on #1038). A run that an account rule starts leaves
  no such comment and is not seen.

The first cloud run of the REST script, 2026-11-14, read Tether at 1 credit for November: the clean
review of #996, which the old script counted as zero.

### Consequences

- Good: the pull requests that change the rules or carry high-risk code get the reviewer the
  maintainer rates highest, without a wait in the CodeRabbit queue.
- Good: the CodeRabbit queue holds the documentation and the lower-risk code.
- Good: a cloud session can read Tether's Greptile count.
- Trade-off: a Greptile review with findings costs a CodeRabbit turn as well, so such a pull request
  still waits in the queue before it closes.
- Trade-off: 45 of the seat's 50 credits leave 5 a cycle for the other two repositories.
- Trade-off: the Risk line is the author's own call. "May only increase" in the template means a
  pull request cannot lower it to avoid the route, but nothing checks that it was set high enough.
- Trade-off: a cloud session does not see the other two repositories on the seat, so it can ask
  while the seat as a whole is empty. The maintainer reconciles the seat on Greptile's usage page.
- Nothing counts the route. Like the cap and the share, it is a convention a worker keeps, and the
  merged history is auditable.

## More information

- [ADR-0064](0064-the-agent-layer-coordinates-writers-not-reviews.md) — the agent layer is
  feature-complete; this capability change is on the maintainer-opened issue #1037, which supersedes
  #1035.
- [ADR-0065](0065-a-spent-metered-cap-closes-on-the-unmetered-provider.md) — the Codex close and
  its conditions.
- [ADR-0069](0069-one-metered-review-then-the-codex-close.md) — one metered review per pull request
  and the Greptile stand-in.
- The rule itself: `AGENTS.md` §Review, which points here for the full test. Working copies: the
  pull-request template, the PRD §12, `.greptile/README.md`, and the worker skill.
