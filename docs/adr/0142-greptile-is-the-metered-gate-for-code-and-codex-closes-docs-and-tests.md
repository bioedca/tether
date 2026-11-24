<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0142 — Greptile is the metered gate for code, Codex alone closes docs and tests, and CodeRabbit is the fallback

- **Status:** accepted; amends [ADR-0065](0065-a-spent-metered-cap-closes-on-the-unmetered-provider.md)
  where it names CodeRabbit as the metered gate, [ADR-0069](0069-one-metered-review-then-the-codex-close.md)
  (the optional Greptile review and the Greptile stand-in are retired) and
  [ADR-0099](0099-load-bearing-pull-requests-take-one-greptile-review.md) (its route test is
  replaced); their other decisions govern unchanged
- **Date:** 2026-11-24
- **Deciders:** bioedca
- **PRD anchor:** §12 (development & version-control protocol)
- **Milestone:** none (cross-cutting; the review lane was built in M11)

## Context and problem statement

CodeRabbit was the last metered gate of almost every pull request (`AGENTS.md` §Review before this
record). Its allowance is for the whole account, and on 2026-11-14 it was one review an hour
(ADR-0099, Context). The Claude threads take turns through a queue, so that a request is not refused
and a running review is not destroyed. The queue made pull requests wait for days. PR #1084 (#535,
an XS citation fix) joined the queue on 2026-11-15 19:36Z at place 52, and got its CodeRabbit review
on 2026-11-20 14:09Z. On 2026-11-23 the queue held 64 entries.

On 2026-11-19 00:33Z the maintainer added 50 Greptile reviews: "We now have 50 new Greptile reviews,
they all can be used on tether, separate from the usual 45 monthly quota. Allocate them wisely in the
most load-bearing PRs." No repository rule recorded them. A project note allocated them as an
optional review before CodeRabbit, so CodeRabbit stayed the gate and the wait did not change.

On 2026-11-23 20:34Z the maintainer asked for "an issue that lets the new Greptile reviews replace
CodeRabbit". That is #1316. The agent layer takes a capability change only from a
maintainer-opened issue (ADR-0064), and #1316 is that issue.

## Decision drivers

- A pull request must not wait days for its metered review.
- Greptile credits are the maintainer's money: 50 a cycle per seat, shared with `Yeliztli` and
  `tbox-finder`, and Tether's share of 45 (ADR-0099).
- Two workers must reach the same route from the pull request's changed files alone.
- One completed review per metered provider (ADR-0069) stays.
- `AGENTS.md` has a byte budget (`tests/test_agent_contract_is_runnable.py`: 29,696 bytes, measured
  on a Windows checkout with CRLF line endings), and on `main` it was at 29,693. So `AGENTS.md`
  carries the three gates and the budget reading, and this record carries the full test, the
  reasons and the arithmetic.

## The maintainer's decisions

He answered on decision cards in the project thread on 2026-11-23, and in his own words for
decision 4 (20:48Z). Decision 5 is his choice on the approval card of #1316 (20:53Z).

1. **Only code pull requests take a Greptile review.** Docs-only and test-only pull requests take no
   metered review: a clean posted Codex review of the final head is their gate.
2. **The 50 extra Greptile reviews (2026-11-19) are a one-time grant.** When they are used, Tether's
   limit goes back to 45 credits a cycle (from the 17th).
3. **After Greptile findings, the Codex close.** Every finding is disposed of, then a fresh posted
   Codex review of the final head that reports nothing actionable closes the gate. No second
   Greptile review, and no CodeRabbit review.
4. **The CodeRabbit queue stays, at one review an hour.** His words: "queue stays, one coderabbit
   review per hour, greptile has no queue and should fallback to coderabbit (and enter the queue) if
   no credits until credits added."
5. **Rule files take Greptile.** A pull request that changes `AGENTS.md` (at any depth), `CLAUDE.md`
   or [`docs/PRD.md`](https://github.com/bioedca/tether/blob/main/docs/PRD.md) takes one Greptile review as a code pull request does, although these files are
   Markdown. Approval card, 20:53Z: "Approve, rule files get Greptile".

## Considered options

1. **Keep CodeRabbit as the gate for every pull request**, with Greptile as an optional review
   before it. That is the wait this record ends.
2. **Greptile for every pull request**, docs included. Not chosen (decision 1): a docs-only or
   test-only pull request does not need a metered read, and the credits go further on code.
3. **Greptile for code, Codex alone for docs and tests, CodeRabbit when there are no credits**
   (chosen).

## Decision outcome

### 1. Which pull requests are code

Read from GitHub's list of changed files (the diff against the merge base with `main`), a renamed
file by both names. `<py> .agents/bin/greptile_usage.py --kind <PR>` reads that list from
`pulls/<PR>/files` and prints the answer, so a worker does not apply the test by hand.

- **Rule files first.** A pull request that changes `AGENTS.md` (at any depth), `CLAUDE.md` or
  [`docs/PRD.md`](https://github.com/bioedca/tether/blob/main/docs/PRD.md) is **code**, whatever its other files (decision 5).
- **Docs/test-only.** Every changed file is Markdown (`*.md`), under `docs/` or under `tests/`, and
  none is a rule file. Docs and tests mixed are docs/test-only too.
- **Code.** Every other pull request: one that changes at least one file outside `tests/` that is
  not Markdown and not under `docs/`. A pull request with no changed files is code, the side that
  reviews more.

Worked examples, which `tests/test_greptile_usage.py` runs:

| Changed files | Kind |
| --- | --- |
| `docs/tutorial.md`, `docs/assets/step.png`, `README.md` | docs/test-only |
| `tests/test_io.py`, `tests/fixtures/a.json` | docs/test-only |
| `docs/glossary.md`, `tests/test_glossary.py` | docs/test-only |
| `AGENTS.md` | code (decision 5) |
| [`docs/PRD.md`](https://github.com/bioedca/tether/blob/main/docs/PRD.md), `docs/tutorial.md` | code (decision 5) |
| `src/tether/io/reader.py` | code |
| `docs/tutorial.md`, `mkdocs.yml` | code |
| `docs/old_tool.md`, renamed from `scripts/old_tool.py` | code |

The Risk line no longer routes anything. ADR-0099's second test (Risk `high` and mostly code) is
replaced by this one, so a code pull request takes Greptile whatever its Risk line says. Its first
test, the rule files, stays as decision 5.

### 2. The gate for a code pull request

After Codex is clean on a green head, the pull request goes ready for review and asks **one standard
Greptile review**, never TREX, while `greptile_usage.py --fail-over-share` exits 0. Greptile has no
queue.

- **Clean:** Greptile's summary comment names the head it read as `Last reviewed commit`, with the
  full 40-hex commit in its link (ADR-0099 item 3). It is a completed review and the gate. If a
  non-material push moved the head after it, a fresh clean Codex review of the final head closes the
  gate (ADR-0065).
- **Findings:** every finding is disposed of, then a fresh posted Codex review of the final head
  that reports nothing actionable closes the gate (decision 3). ADR-0065's conditions apply to that
  close as they did to CodeRabbit's: the cap is spent, nothing is left outstanding, and nothing but
  disposal lands after the commit the Greptile review read. ADR-0099's "one CodeRabbit review of the
  final head" after Greptile findings is retired.
- A Greptile refusal or failed run reviewed nothing. It is a wait, as for any provider.

### 3. The gate for a docs-only or test-only pull request

A fresh posted Codex review of the final head that reports nothing actionable (decision 1). No
metered provider is asked, so there is no cap to spend and the close conditions of ADR-0065 do not
arise: Codex reads the head that is merged. Codex findings are disposed of and Codex reads again,
as on any leg; Codex is uncapped (ADR-0069).

### 4. No credits: the CodeRabbit fallback

When `greptile_usage.py --fail-over-share` exits 1 (Tether's limit for the cycle is used, or the
whole seat was read and is empty) or 2 (the count could not be read), a **code** pull request takes
**one CodeRabbit full review** through the queue (`.agents/bin/cr_next_free.py`), as before this
record, then the Codex close after findings. A clean CodeRabbit walkthrough verified at the live
head is the gate (ADR-0137). The fallback lasts until credits are added. A pull request that already
joined the queue keeps its place there, and does not leave it for Greptile.

Greptile no longer stands in for a refused CodeRabbit (ADR-0069 item 3 is retired). Greptile comes
first now, so a pull request on the fallback has no Greptile credit to stand in with. A CodeRabbit
refusal is a wait.

### 5. One review per metered provider stays

At most one Greptile review on a pull request, and on the fallback at most one CodeRabbit review
(ADR-0069). A pull request that has its Greptile review does not also take CodeRabbit.

### 6. The budget

`.agents/bin/greptile_usage.py` keeps `REPO_SHARE = 45` and the cycle from the 17th, and adds the
grant as two constants: `GRANT_CREDITS = 50` and `GRANT_CYCLE = "2026-11"` (the cycle that started
on 2026-11-17; the grant came on 2026-11-19 00:33Z).

- In each cycle from the grant's on, the credits Tether uses above 45 draw on the grant, and never
  more than it has left. The script reads every earlier cycle of the grant to know what is left.
- Tether's limit in a cycle is 45 plus what the earlier cycles left of the grant. When the grant is
  used, the limit is 45 again (decision 2).
- The output prints the grant used this cycle, the grant left and the limit. `--fail-over-share`
  exits 1 when Tether is at its limit.
- The credits Tether draws on the grant are outside the seat's included 50, so the whole-seat
  balance subtracts only the rest. An empty seat stops a Tether review only when that review
  would draw on the seat: once Tether has used its 45 and the grant has credit left, the next
  review draws on the grant (Codex on #1369).
- Any failed read, of this cycle or of an earlier one, is UNKNOWN (exit 2), never a partial count.
- A cloud session reads Tether alone with `--repo bioedca/tether` (ADR-0099 item 6).

The figures are a proxy. A past cycle can read low (ADR-0099: a summary comment edited in a later
cycle counts in the later one), so the grant left can read high. Reconcile against the Greptile
dashboard (Settings, Usage), as the script's docstring says.

On 2026-11-23 ~20:50Z the script read Tether at 53 for the cycle 2026-11-17 to 2026-12-17: 45 of the
share and 8 of the grant, which leaves 42 of the grant and a limit of 95 this cycle.

### 7. The queue pace

`.agents/bin/cr_next_free.py` paces at **one review an hour** (decision 4): `MAX_PER_HOUR` is 1, in
place of half the allowance CodeRabbit names, at most 5 an hour. A refusal or no reading still gives
the slow pace, which is the same hour. The copy in the maintainer's project folder follows when this
change merges; it is not in this repository.

### 8. The walkthrough rule (#1298)

Issue #1298 asked how to record a clean CodeRabbit full review that posts only a walkthrough. ADR-0137
answered it, and `AGENTS.md` §Review states the rule. The fallback keeps that rule unchanged, so
this record needs no second one.

### Consequences

- Good: a code pull request does not wait in a queue for its metered review while credits remain.
- Good: docs-only and test-only pull requests close on one unmetered read, with no wait.
- Good: the rule for "code" is mechanical and has a command.
- Trade-off: a docs-only or test-only pull request has no metered reader. Codex alone reads it. Docs
  that state rules, such as `CONTRIBUTING.md`, the ADRs, `docs/agents/**` and the worker skill,
  are Markdown and so take Codex alone unless the same pull request changes a rule file of
  decision 5 or any code.
- Trade-off: the grant and the share are spent faster. When both are used, code pull requests wait
  in the CodeRabbit queue again until the next cycle.
- Trade-off: a cloud session does not see the other two repositories on the seat, so it can ask
  while the seat as a whole is empty. The maintainer reconciles the seat on Greptile's usage page
  (ADR-0099).
- Nothing counts the route or the cap. They are conventions a worker keeps, and the merged history
  is auditable.

## More information

- Issue #1316 — the maintainer-opened issue, with the decisions above in its body.
- [ADR-0064](0064-the-agent-layer-coordinates-writers-not-reviews.md) — the agent layer is
  feature-complete; this capability change is on a maintainer-opened issue.
- [ADR-0065](0065-a-spent-metered-cap-closes-on-the-unmetered-provider.md) — the Codex close and
  its conditions, which now follow a Greptile review as they followed a CodeRabbit one.
- [ADR-0069](0069-one-metered-review-then-the-codex-close.md) — one metered review per pull request.
- [ADR-0099](0099-load-bearing-pull-requests-take-one-greptile-review.md) — the Greptile summary
  comment as a completed review, the cycle from the 17th and the share of 45, which stay.
- [ADR-0137](0137-a-verified-clean-coderabbit-walkthrough-is-the-metered-gate.md) — the verified
  clean walkthrough, which stays for the fallback.
- The rule itself: `AGENTS.md` §Review. Working copies: `CONTRIBUTING.md`, the PRD §12, the
  pull-request template, the issue forms' Risk options, `.greptile/README.md` and the worker skill.
