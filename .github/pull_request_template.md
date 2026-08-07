<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later

Pull-request template (PRD §12.2, §12.4). The Conventional-Commit title rule used
to live here, in a comment the author never sees while filling the form in — it is
now a visible section below. Keep the checklist in step with PRD §12.4: it is the
mechanism that stops docs, fixtures and secrets hygiene from silently rotting.
-->

## PR title

**This PR's title must be a Conventional Commit** — `type(scope): summary (FR-ID when applicable)` —
because it becomes the squash-merge commit and feeds the changelog (PRD §12.2).
Scope is a §4.2 module (`io` | `imaging` | `fret` | `idealize` | `ml` | `analysis` |
`gui` | `project`) or a cross-cutting area (`schema` | `ci` | `deps` | `docs` |
`release`).

The `commitlint` check validates the **type** prefix only — it runs unconfigured, so it
will pass a title with no scope. Scope is required by convention; include an FR-ID when the
linked work maps to one. The checklist, not CI, enforces those fields.

## Summary

<!-- One concern per PR. What does this change do, and why? -->

## Linked tracking

<!-- Fill the footers; the squash commit inherits them. -->
- Closes: #
- Milestone: M
- FR:
- Risk (may only increase; routes no provider — every PR walks the one lane): low | standard | high
- Risk rationale:
- Final head SHA:
- Review round: 0/2 (no metered round yet — a draft, or ready but not yet reviewed) | 1/2 | 2/2 (a third is a stop-list violation — the launcher issues rounds, not you)
- Draft phase: Codex iterated to nothing-blocking before this went ready | opened ready (say why)
- Greptile: spent one credit | skipped — no budget this month | skipped (say why). Balance from `<py> .agents/bin/greptile_usage.py`, where `<py>` is your lane's interpreter
- **CodeRabbit — the last gate**: no actionable comments (quote the review that said so — permalink, the `commit_id` it read, and the opening of its body; a green status check with no review body is **not** the gate. `docs/agents/review.md` §4 states what that evidence is, including that the clean verdict is written by the `Actionable comments posted:` line being **absent**, not by it reading `0`) | in flight (status check `pending` — never re-request, it aborts the run) | throttled, retrying after the stated interval *and* a non-pending status check (a wait, not a freeze) | unavailable (freezes the PR)
- Independent review result: pending | substantive review complete | nothing to review (quote each selected provider, naming the head it read; a Codex 👍 reaction counts)
- Provider unavailable: no | yes (which, and why it could not act — capability, never quota)
- Findings: <N> blocking (fixed) | <M> non-blocking (deferred to #____)
- Human sign-off: n/a | release/tag/signing | new scientific claim (reviewer and evidence)
- Optional Copilot state: not requested | pending | complete | unavailable | quota-exhausted

## Type of change

- [ ] `feat` — new capability
- [ ] `fix` — bug fix
- [ ] `docs` / `chore` / `ci` / `build` / `refactor` / `test` / `perf`
- [ ] `!` / `BREAKING CHANGE:` — a deliberate schema-version bump (the only sanctioned breaking change)

## Self-review checklist (PRD §12.4)

Confirm before requesting review:

- [ ] **Schema freeze respected** — no structural change to the §5 HDF5 skeleton frozen at M0; only additive *data* (`schema-guard` green). A legitimate structural change carries an ADR + an explicit schema-version bump.
- [ ] **conda-lock updated if dependencies changed** — base, isolated tMAVEN sidecar, and/or isolated deep lock, kept distinct (§4.1/§4.3); `conda-lock-verify` is green.
- [ ] **Tests added/updated** for the change; new GUI behavior has a headless `pytest-qt` test.
- [ ] **Docs updated** — the `mkdocs` pages under `docs/` *and* the public docstrings for anything user-facing this PR changes; a new page is registered in `mkdocs.yml` nav; `docs-build` (`mkdocs build --strict`) is green.
- [ ] **Data policy respected** — no raw/private/unlicensed data or large data in ordinary Git; issue-authorized redistributable fixtures carry license and provenance in named small or LFS/gated paths.
- [ ] **No secrets committed** — no token, key, credential, or private path in code, tests, logs, or fixtures; `secret-scan` and push protection are green.
- [ ] **Code scanning clean** — CodeQL (GitHub code-scanning *default setup*, hence no `codeql.yml` workflow) reports no new alerts on this PR.
- [ ] **Review lane complete** — the lane was walked cheapest-first (`docs/agents/review.md`): Codex iterated on the draft until nothing blocking remained, **or** the PR was opened ready with the reason recorded above, in which case there is no free draft phase and *every* round it has ever taken counts against the cap; one Greptile credit was spent or its absence recorded; and **CodeRabbit returned no actionable comments at the final head**, which is the gate nothing merges past. **That is a verdict a completed review reached, never an absence of one** — record the review itself: its permalink, the `commit_id` it read (which must equal the final head), and the opening of its body, per `docs/agents/review.md` §4. Neither silence nor a green `CodeRabbit` status check is the gate; both are also what a request that reviewed **nothing** leaves behind. Ask with the **full-review** command — the bare incremental one applies only where automatic reviews are *paused*, and they are *disabled* here, so it reviews nothing and answers in words that read a great deal like a clean pass (measured on #392). A provider that genuinely **cannot** act has its unavailability recorded above with the reason — capability, never quota — except CodeRabbit, whose unavailability freezes the PR rather than excusing it. A fair-use refusal naming a retry time is a **wait**, not unavailability: wait it and ask again, which spends neither a round nor the one-per-round request, and never take the usage-based-billing offer — that is the maintainer's spending decision. Copilot is advisory and never satisfies a leg; a quota refusal from it is *did not review*. Applicable human/domain review complete; every conversation and every actionable finding resolved.
- [ ] **Provenance stamped** — coordinates / corrections / app-version / parameters written into the `.tether` for any new analysis (NFR-REPRO).
- [ ] **New tunables registered in PRD §11.2** (single source of truth), not hardcoded.
- [ ] **Scientific/statistical claims carry a citation**; **SPDX `GPL-3.0-or-later`** header on every new source file (`reuse lint` green).
- [ ] A resolved PRD decision that changed is reflected in the PRD and/or a `docs/adr/` ADR in this same PR.

## Testing

<!-- How was this verified? Name the tests / fixtures / OS matrix. Headless GUI runs use QT_QPA_PLATFORM=offscreen. -->
