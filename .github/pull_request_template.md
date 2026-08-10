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
- Risk (may only increase): low | standard | high
- Risk rationale:
- Final head SHA:
- Codex — first, on the green diff, and not optional (the draft by default; a ready-opened PR whose reason is recorded is asked there, at the same point in the lane): reviewed, nothing blocking outstanding (quote it) | reviewed, findings answered below | not reviewed (say why). Unmetered, so uncapped
- Greptile: spent N credits (a standard review is 1, a TREX review 3) | skipped — no budget this month | skipped (say why). Balance from `<py> .agents/bin/greptile_usage.py`, where `<py>` is your lane's interpreter
- **CodeRabbit — the last gate**: no actionable comments (quote the review — permalink, the **full 40-hex** `commit_id` it read **which must be the final head**, its `submitted_at`, its state — **`COMMENTED` or `APPROVED`**, since `DISMISSED` is a verdict withdrawn and `PENDING` is unsubmitted — and the opening of its body, which must show that **`Actionable comments posted:` is ABSENT**: zero is written by that line not being there, and a clean body opens straight onto `🧹 Nitpick comments` or `No actionable comments were generated`. A review of an earlier head does not qualify, a `PENDING` one is not submitted, a `DISMISSED` one is a verdict withdrawn, and a green status check with no review body is **not** the gate) | in flight (status check `pending` — never re-request, it aborts the run) | throttled, retrying after the stated interval *and* a non-pending status check (a wait, not a freeze) | unavailable (freezes the PR)
- Provider that did not review: none | which, and why — a quota refusal means the provider **did not review**, and never counts as a pass
- Findings: `<N>` serious (fixed) | `<M>` below the floor (deferred to #____, or dropped if this is an agent-layer path — ADR-0064). Dropped is not silent: reply on the thread in the wording `AGENTS.md` §Review gives, and resolve it
- Human sign-off: n/a | release/tag/signing | new scientific claim **or citation** (reviewer and evidence)

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
- [ ] **Review complete** (`AGENTS.md` §Review) — the diff went green before anything was asked to read it, on a draft by default or on a ready-opened PR whose reason is recorded above, and then **Codex on that green diff before any metered provider**; at least one external provider reviewed the **final head** and its verdict is quoted above with all five of **which provider it was** — its name, never its @-handle, since a mention in the PR body fires the bot — permalink, the **full 40-hex** `commit_id` it read, `submitted_at`, and a state of **`COMMENTED` or `APPROVED`**; and **CodeRabbit returned no actionable comments at that head**, asked with the **full-review** command. Neither silence nor a green `CodeRabbit` status check is the gate — both are also what a request that reviewed *nothing* leaves behind. A provider that could not act is recorded above with the reason, and a quota refusal means the provider **did not review**, and never counts as a pass. Serious findings fixed; the rest deferred to one follow-up issue, or dropped without one if this is an agent-layer path (ADR-0064) — dropping still owes the thread the reply `AGENTS.md` §Review words, so the decision is on the record rather than inferred from silence. Every conversation resolved.
- [ ] **Provenance stamped** — coordinates / corrections / app-version / parameters written into the `.tether` for any new analysis (NFR-REPRO).
- [ ] **New tunables registered in PRD §11.2** (single source of truth), not hardcoded.
- [ ] **Scientific/statistical claims carry a citation**; **SPDX `GPL-3.0-or-later`** header on every new source file (`reuse lint` green).
- [ ] A resolved PRD decision that changed is reflected in the PRD and/or a `docs/adr/` ADR in this same PR.

## Testing

<!-- How was this verified? Name the tests / fixtures / OS matrix. Headless GUI runs use QT_QPA_PLATFORM=offscreen. -->
