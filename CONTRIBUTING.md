# Contributing to Tether

Thanks for your interest in Tether. This document is the contributor-facing
summary of the development & version-control protocol specified in
[`docs/PRD.md` §12](docs/PRD.md). The PRD is the source of truth; where this file
abbreviates, §12 governs.

## Development model — solo + CI gate (scales up cleanly)

Tether is currently maintained **solo (account `bioedca`) with CI as the merge
gate**: branch protection on `main` requires green required CI plus a self-review
checklist on every PR, with **no mandated second human reviewer**. Every rule
here is written so it scales up to required human reviews + `CODEOWNERS` if
contributors join, without rework (PRD §12.3).

`main` is **always releasable and protected**. Never push to `main` directly;
never advance work or cut a summary while a PR's checks are red.

## Identity & signed commits (PRD §12.1)

- The single authoritative commit-author/committer identity for this repo is
  **`bioedca@u.northwestern.edu`** — a convention introduced in §12 (the
  account's other address `bioedca@gmail.com` is **not** used for repo commits).
- **Commits and tags are SSH-signed** so GitHub renders the *Verified* badge:

  ```bash
  git config user.email bioedca@u.northwestern.edu
  git config gpg.format ssh
  git config user.signingkey <path-to-ssh-public-key>
  git config commit.gpgsign true
  git config tag.gpgsign true
  ```

  The signing key must be registered to the account as a **Signing key** and the
  committer email must be on the account's verified-emails list.
- **2FA is required** on the account.

## Branching & Conventional Commits (PRD §12.2)

- **Model — GitHub Flow.** All work happens on short-lived branches off `main`,
  opened as a PR, merged via **squash-merge**, branch **deleted on merge**. No
  long-lived `develop`/`release` branches; milestones M0–M9 are GitHub
  Milestones, not git branches.
- **Branch naming:** `type/short-slug`, optionally milestone/FR-scoped —
  e.g. `feat/m1-fr-extract-atrous-detector`, `fix/m3-fr-correct-nan-guard`. The
  slug is kebab-case, ≤ ~5 words. The branch name is not load-bearing (the PR
  title + linked issue carry authoritative metadata).
- **Conventional Commits** govern **both commit messages and PR titles**:
  `type(scope): summary (FR-ID)`. Types: `feat fix docs chore refactor test ci
  build perf revert`. The **scope is a §4.2 module** without the `tether.`
  prefix — `io | imaging | fret | idealize | ml | analysis | gui | project` —
  plus cross-cutting `schema | ci | deps | docs | release`. Examples:
  - `feat(imaging): à trous wavelet spot detector (FR-EXTRACT)`
  - `fix(fret): never emit NaN factor on total-correction-failure (§7.2)`

  A breaking change is marked with `!` or a `BREAKING CHANGE:` footer — reserved
  almost exclusively for the deliberate, ADR-backed HDF5 schema-version bump.
- **One concern per PR.** Keep PRs atomic and reviewable; link the issue with a
  `Closes: #N` footer.

## Local setup, hooks & tests

- Create the pinned base environment from the committed `conda-lock` (never solve
  fresh), then `pip install -e . --no-deps`. The tMAVEN sidecar uses its own
  isolated `sidecar/conda-lock.yml`. *(The package skeleton and locks land at
  PLAN M0 S2; until then this repo is governance + spec + scaffold.)*
- Install and run **pre-commit** before every commit:

  ```bash
  pre-commit install
  pre-commit run --all-files
  ```

  Hooks include `ruff` (lint + format), `reuse lint` (SPDX/REUSE licensing), and
  secret/large-file guards (PRD §12.6, §12.9).
- Run the **small-fixture** test suite locally; GUI tests run headless with
  `QT_QPA_PLATFORM=offscreen`:

  ```bash
  pytest -q                       # small committed fixtures (the CI default)
  QT_QPA_PLATFORM=offscreen pytest -m gui
  ```

  Large/gated fixtures are exercised only by the scheduled `large-fixtures.yml`
  tier, never by the required matrix.

## Licensing — SPDX / REUSE (PRD §12.1)

Tether is `GPL-3.0-or-later`. **Every source file carries** an
<!-- REUSE-IgnoreStart -->`SPDX-License-Identifier: GPL-3.0-or-later`<!-- REUSE-IgnoreEnd --> and an `SPDX-FileCopyrightText`
header; non-code files are covered by `REUSE.toml`. `reuse lint` must be green
(enforced in pre-commit and CI). Add a header to every new source file.

## Two load-bearing invariants — do not break

1. **HDF5 schema is additive-only after M0.** The §5 `.tether` group skeleton is
   **frozen at M0**; only additive *data* is allowed. The CI **`schema-guard`**
   gate enforces it. A legitimate structural change carries an **ADR + an
   explicit schema-version bump** — never a silent structural edit.
2. **`conda-lock` is pin-and-hold.** Never casually bump a dependency.
   Regenerate the lock (base **and/or** the isolated sidecar lock, kept
   separate — the sidecar's `numpy<2`/PyQt5 must never merge into the PySide6
   base stack) and confirm `conda-lock-verify` is green when deps change.

## PR self-review checklist (PRD §12.4)

Before requesting review / merging, confirm:

- [ ] Tests added/updated, green on the 3-OS small-fixture matrix; new GUI
      behavior has a `pytest-qt` test.
- [ ] **Schema freeze respected** (`schema-guard` green; structural change ⇒
      ADR + version bump).
- [ ] **conda-lock** regenerated if deps changed (base and/or sidecar, isolated);
      `conda-lock-verify` green.
- [ ] Any new tunable registered in PRD §11.2 (single source of truth), not
      hardcoded.
- [ ] Provenance / params / app-version stamped into the `.tether` for any new
      analysis (NFR-REPRO).
- [ ] SPDX `GPL-3.0-or-later` header on every new source file; `reuse lint`
      green.
- [ ] CodeQL clean; secret-scan green; Conventional-Commit PR title;
      docs/docstrings updated.
- [ ] A resolved design decision that changed → PRD and/or an ADR updated in the
      **same** PR.

## Merging (PRD §12.2, §12.6)

Merge **squash-only** (linear history, delete-branch-on-merge) once the review
is addressed **and all required CI checks are green** — wait for in-progress
checks; **never merge over a red or pending check**. Required checks (from M0):
`lint`, `test (ubuntu/macos/windows)`, `pre-commit`, `commitlint`,
`secret-scan`, `conda-lock-verify`, `schema-guard`, `codeql`, `docs-build`
(plus `sidecar/parity` from M0.5).

## Reporting bugs & security issues

- **Bugs / features:** open an issue using the provided issue forms.
- **Security vulnerabilities:** do **not** use public issues — see
  [`SECURITY.md`](SECURITY.md) (GitHub Private Vulnerability Reporting).

By contributing, you agree your contributions are licensed under
`GPL-3.0-or-later`.
