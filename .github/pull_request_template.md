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
- Review path (may only increase): low | standard | high
- Risk rationale:
- Reviewed head SHA:
- Copilot review: pending | complete
- Codex review (standard/high): n/a | pending | complete
- CodeRabbit `@coderabbitai review` walkthrough (high): n/a | pending | complete
- Human/domain review or maintainer-approved equivalent:

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
- [ ] **Review path complete** — Copilot first; Codex for standard/high; `@coderabbitai review` for high/load-bearing changes; required human/domain review recorded.
- [ ] **Provenance stamped** — coordinates / corrections / app-version / parameters written into the `.tether` for any new analysis (NFR-REPRO).
- [ ] **New tunables registered in PRD §11.2** (single source of truth), not hardcoded.
- [ ] **Scientific/statistical claims carry a citation**; **SPDX `GPL-3.0-or-later`** header on every new source file (`reuse lint` green).
- [ ] A resolved PRD decision that changed is reflected in the PRD and/or a `docs/adr/` ADR in this same PR.

## Testing

<!-- How was this verified? Name the tests / fixtures / OS matrix. Headless GUI runs use QT_QPA_PLATFORM=offscreen. -->
