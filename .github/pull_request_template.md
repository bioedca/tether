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
- Codex closing review — required whenever the CodeRabbit line below records a **spent cap**, whatever heads those two reviews read, **including where one came back clean at a head a permitted non-material push has since moved**: that review's evidence still stands, but no metered provider has named the commit the merge binds, and the cap forbids asking for a third to name it: n/a — a clean CodeRabbit review at the head being merged, with nothing since | **closed the gate** — quote it exactly as the CodeRabbit gate is quoted: permalink or run artifact, the **full 40-hex** head it read **which must be the final head**, when, and what it said. A re-quoted earlier Codex pass is **not** a closing review, since the head that pass read is not the head being merged. It must be **posted by the provider and name the commit it read** — asked with the Codex review command named in `AGENTS.md` §Review, written in prose here because a handle in this template would fire a real review on every PR opened from it. Two shapes both count: a run with findings posts a review whose `commit_id` is the full 40-hex, and a **clean** run posts a comment carrying `Reviewed commit: <short-sha>` — expand that with `git rev-parse` and record both. A local CLI run posts nothing and cannot close. Anything this read surfaces is disposed of above before it closes
- Greptile: reviewed — quote its verdict, not only the spend (spent N credits; a standard review is 1, a TREX review 3), and name the **full 40-hex** head it read, which need not be the final one: Greptile is asked before CodeRabbit, so a later finding-fix legitimately moves the head past it, and only the provider that *closes* the gate must reach the final head. Buying a second review to make a checkbox true is not a reason to spend a credit | skipped — no budget this month | skipped (say why). Balance from `<py> .agents/bin/greptile_usage.py`, where `<py>` is your lane's interpreter
- **CodeRabbit — the last metered gate**: no actionable comments (quote the review — permalink, the **full 40-hex** `commit_id` it read **which must be the final head when CodeRabbit is what closes the gate**, its `submitted_at`, its state — **`COMMENTED` or `APPROVED`**, since `DISMISSED` is a verdict withdrawn and `PENDING` is unsubmitted — and the opening of its body, which must show that **`Actionable comments posted:` is ABSENT**: zero is written by that line not being there, and a clean body opens straight onto `🧹 Nitpick comments` or `No actionable comments were generated`. A review of an earlier head does not qualify: where every push since it is non-material its evidence still stands and it is one of the two completed reviews, but it is the Codex closing review above that names the merging head, so that case is recorded as **cap spent, closed by Codex** and not here. A `PENDING` one is not submitted, a `DISMISSED` one is a verdict withdrawn, and a green status check with no review body is **not** the gate) | in flight (status check `pending` — never re-request, it aborts the run) | throttled, retrying after the stated interval *and* a non-pending status check (a wait, not a freeze) | unavailable (freezes the PR) | **cap spent, closed by Codex** — two *completed* reviews stand, each submitted with a body; the second was asked only after the first one's findings were **disposed of** (by commits that answer them, or by the replies and resolutions recording a deferral or drop — same `commit_id` is fine, since disposal on the record moves no head); no finding from either is left outstanding, with the thread resolved on each — cleared by being fixed, deferred-and-tracked, dropped sub-floor, or **withdrawn by the provider that raised it**; *outstanding* is the test and those are the known ways of clearing one; **nothing but disposal and the non-material exceptions landed after the commit the second review read** — every hunk since answers something already recorded on this PR **that you were required to address** — a review finding from any provider, a CodeQL or `secret-scan` alert, a condition a human sign-off attached, the closing review's own finding; the test is the change, not who raised it — or is a clean `main` merge / formatting / comment or docstring edit / **ADR renumber-only** (a renumber that also edits a word of the decision is material, not an exception), or is the resolution of a conflict in the `main` merge the contract requires, which admits the reconciliation only — so no new scope reached the merge unread by an **external** provider — metered up to the commit the second review read, the closing review after it, which is the guarantee the conditions buy and not a metered read of everything; neither came back clean **at the head being merged with its evidence still standing** — all three, since a clean review that a later material push re-armed is not a gate that already closed, reading it as "neither was ever clean" would strand the case where review 1 was clean at an earlier head and review 2 then found something, and a clean review whose head a *non-material* push has since moved lands **here** rather than on the line above, because its evidence stands but no metered provider has named the commit the merge binds; and the Codex closing review above is quoted. That closing review is then the `<SHA>` the merge below binds to
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
- [ ] **Review complete** (`AGENTS.md` §Review) — the diff went green before anything was asked to read it, on a draft by default or on a ready-opened PR whose reason is recorded above, and then **Codex on that green diff before any metered provider**; **every provider the lane reached** has a result recorded above — a quoted verdict at the head it read, or the reason it produced none — and **the provider that closed the gate reviewed the final head** — where that is Codex, quote what it emits and skip the fields that exist only on a review — but the **full 40-hex** head comes from the provider's own posted artifact, in either shape `AGENTS.md` §Review describes. A head asserted by the author is never acceptable, since it could name a commit the provider never saw. Where the closer is CodeRabbit, its verdict quoted with all six of **which provider it was** — its name, never its @-handle, since a mention in the PR body fires the bot — permalink, the **full 40-hex** `commit_id` it read, `submitted_at`, a state of **`COMMENTED` or `APPROVED`**, and **what it actually said**: the submitted review body, or enough of it to establish the verdict, since metadata alone records that a provider ran and not what it found; and **the gate is closed at that head** — either **CodeRabbit returned no actionable comments** there, asked with the **full-review** command, or its two-review cap is spent under the conditions the CodeRabbit line above sets out and a **fresh posted Codex review of that head closed it in their place**. On the cap-spent path CodeRabbit's two reviews are recorded at **whatever heads they read** — earlier ones where a fix moved the head, the same one where the disposal was a deferral or drop — and it is the Codex closing review that names the final head; requiring CodeRabbit itself to reach the final head there would demand the third review the cap forbids. Neither silence nor a green `CodeRabbit` status check is the gate — both are also what a request that reviewed *nothing* leaves behind. A provider that could not act is recorded above with the reason, and a quota refusal means the provider **did not review**, and never counts as a pass. Serious findings fixed; the rest deferred to one follow-up issue, or dropped without one if this is an agent-layer path (ADR-0064) — dropping still owes the thread the reply `AGENTS.md` §Review words, so the decision is on the record rather than inferred from silence. Every conversation resolved.
- [ ] **Provenance stamped** — coordinates / corrections / app-version / parameters written into the `.tether` for any new analysis (NFR-REPRO).
- [ ] **New tunables registered in PRD §11.2** (single source of truth), not hardcoded.
- [ ] **Scientific/statistical claims carry a citation**; **SPDX `GPL-3.0-or-later`** header on every new source file (`reuse lint` green).
- [ ] A resolved PRD decision that changed is reflected in the PRD and/or a `docs/adr/` ADR in this same PR.

## Testing

<!-- How was this verified? Name the tests / fixtures / OS matrix. Headless GUI runs use QT_QPA_PLATFORM=offscreen. -->
