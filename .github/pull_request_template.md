<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later

Pull-request template (PRD §12.2, §12.4). The PR title must be a Conventional
Commit — `type(scope): summary (FR-ID)` — because it becomes the squash-merge
commit. Scope is a §4.2 module (io | imaging | fret | idealize | ml | analysis
| gui | project) or a cross-cutting area (schema | ci | deps | docs | release).
-->

## Summary

<!-- One concern per PR. What does this change do, and why? -->

## Linked tracking

<!-- Fill the footers; the squash commit inherits them. -->
- Closes: #
- Milestone: M
- FR:

## Type of change

- [ ] `feat` — new capability
- [ ] `fix` — bug fix
- [ ] `docs` / `chore` / `ci` / `build` / `refactor` / `test` / `perf`
- [ ] `!` / `BREAKING CHANGE:` — a deliberate schema-version bump (the only sanctioned breaking change)

## Self-review checklist (PRD §12.4)

Confirm before requesting review:

- [ ] **Schema freeze respected** — no structural change to the §5 HDF5 skeleton frozen at M0; only additive *data* (`schema-guard` green). A legitimate structural change carries an ADR + an explicit schema-version bump.
- [ ] **conda-lock updated if dependencies changed** — base stack *and/or* the isolated tMAVEN sidecar lock, kept distinct (§4.1/§4.3); `conda-lock-verify` is green.
- [ ] **Tests added/updated** for the change; new GUI behavior has a headless `pytest-qt` test; **CodeQL clean**.
- [ ] **Provenance stamped** — coordinates / corrections / app-version / parameters written into the `.tether` for any new analysis (NFR-REPRO).
- [ ] **New tunables registered in PRD §11.2** (single source of truth), not hardcoded.
- [ ] **Scientific/statistical claims carry a citation**; **SPDX `GPL-3.0-or-later`** header on every new source file (`reuse lint` green).
- [ ] A resolved PRD decision that changed is reflected in the PRD and/or a `docs/adr/` ADR in this same PR.

## Testing

<!-- How was this verified? Name the tests / fixtures / OS matrix. Headless GUI runs use QT_QPA_PLATFORM=offscreen. -->
