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

- Create the pinned base environment from the committed `conda-lock.yml` — **restore
  it, never solve fresh** (pin-and-hold):

  ```bash
  micromamba create -n tether -f conda-lock.yml   # or: conda-lock install -n tether
  micromamba activate tether
  pip install -e . --no-deps                      # deps come from the lock, not pip
  ```

- The tMAVEN idealization sidecar is a **separate** PyQt5 / `numpy<2` environment
  built from `sidecar/conda-lock.yml`. Two things it needs live outside that lock
  (tMAVEN itself, pinned by commit, and `setuptools<81` for the `pkg_resources` API
  tMAVEN imports without declaring), so use the guided script rather than doing it by
  hand:

  ```bash
  python scripts/setup_sidecar.py     # writes the $TETHER_SIDECAR_PYTHON you need
  ```

  Only the `sidecar`-marked tests need it; everything else runs without it.
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
  # exactly what the required `test` matrix runs (see Test tiers below)
  QT_QPA_PLATFORM=offscreen pytest -m "not large and not sidecar and not deep"
  ```

  Large/gated fixtures are exercised only by the scheduled `large-fixtures.yml`
  tier, never by the required matrix.

### Test tiers and markers

A bare `pytest` runs **everything**, marked included — `-m` is a filter, not a default.
The required matrix is therefore an explicit *exclusion*: it runs unmarked **and** `gui`
tests, and excludes only the three tiers that need something CI does not have. Each of
those has its own workflow:

| marker | needs | where it runs |
|---|---|---|
| *(unmarked)* | nothing beyond the base env | `ci.yml` — required, 3 OS |
| `gui` | PySide6/napari/pyqtgraph; headless via `QT_QPA_PLATFORM=offscreen` | `ci.yml` — required, 3 OS |
| `sidecar` | a live tMAVEN env (`$TETHER_SIDECAR_PYTHON`) | `sidecar.yml` — never the required matrix |
| `deep` | the isolated torch stack (`deep/conda-lock.yml`) | `deep.yml` / `deep-gpu.yml` — advisory |
| `large` | the gated large-fixture tier | `large-fixtures.yml` — scheduled |

```bash
# The required matrix, verbatim — ci.yml runs this on all three OSes
# (Linux wraps it in `xvfb-run -a`; QT_QPA_PLATFORM=offscreen is the local equivalent).
QT_QPA_PLATFORM=offscreen pytest -m "not large and not sidecar and not deep"

QT_QPA_PLATFORM=offscreen pytest -m gui    # just the GUI tier
pytest -m "not gui"                        # skip Qt entirely
```

Markers are `--strict-markers`, so a typo in `-m` fails rather than silently selecting
nothing. Two naming rules are enforced by `tests/test_marker_contract.py` rather than
convention: a live sidecar test must be named `test_*sidecar*.py`, and deep tests use
the `test_*_deep.py` suffix — the isolated workflows select on those globs.

### Building the docs locally

The required `docs-build` gate is `mkdocs build --strict`, where **warnings are
errors**. A new page must be registered in `mkdocs.yml` `nav` or the build fails, and
it must not link to `docs/PRD.md`, which the site deliberately does not serve.

```bash
pip install -r requirements-docs.txt
mkdocs build --strict          # the gate
mkdocs serve                   # live preview at http://127.0.0.1:8000
```

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

## Proposing an ADR (PRD §12.7)

Architecture Decision Records under [`docs/adr/`](docs/adr/README.md) are where the
*rationale* survives. They are load-bearing here: the PRD records what was decided, the
ADR records **why**, and which options were rejected.

Write one when a change settles a question that a future reader could reasonably decide
differently — a schema-affecting change, a dependency/isolation boundary, an algorithm
choice with a scientific trade-off, or anything that supersedes an earlier ADR. Routine
bug fixes and refactors do not need one.

1. Copy [`docs/adr/0000-template.md`](docs/adr/0000-template.md) to
   `NNNN-kebab-title.md`, where `NNNN` is the next unused number. Numbers are
   contiguous — do not skip.
2. Fill all five frontmatter fields (**Status**, **Date**, **Deciders**, **PRD anchor**,
   **Milestone**) and keep the MADR headings from the template.
3. `Status` is `proposed` | `accepted` | `deprecated` | `superseded by ADR-NNNN`. When a
   record supersedes another, say so in both, and link with a real Markdown link —
   `[ADR-0004](0004-pin-and-hold-dual-lock-isolation.md)`, not a bare `[ADR-0004]`,
   which renders as literal brackets.
4. **Add the row to [`docs/adr/README.md`](docs/adr/README.md) in the same PR**, using
   the record's own H1 as the Title cell. `tests/test_adr_index.py` enforces that the
   index is complete, that every link resolves, and that titles match their heading.
5. **Land the ADR in the PR that implements the decision** — the §0.4 DoD rule. An ADR
   merged separately from its implementation drifts immediately.

## AI-assisted contributions

AI assistance is allowed. Two rules, and they are not negotiable because the failure
modes are silent:

- **Verify before you submit.** You are the author of anything you open a PR with.
  Generated code, docstrings and prose must be checked against what the code actually
  does — a plausible-sounding docstring that misstates behaviour is worse than none,
  because it is believed. If you cannot verify a claim, do not ship it.
- **Check what you send.** Before pasting unpublished code, unreleased data or anything
  under embargo into a third-party service, confirm your group's policy *and* that
  service's data-retention terms. This repository is public, but not everything in your
  working tree is.

Note that an automated reviewer reads every diff opened against this repository (see
**Merging**), which is itself a third-party service processing the contents of your PR.

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
- [ ] **Docs updated** — the `mkdocs` pages under `docs/` *and* the public docstrings
      for anything user-facing this PR changes; `docs-build` green.
- [ ] **No large data committed** — no movie, `.tether`, or reference dataset in the
      tree; small fixtures live in `tests/fixtures/`, anything large is gated.
- [ ] **No secrets committed** — no token, key, credential or private path in code,
      tests, logs or fixtures; `secret-scan` green.
- [ ] Code scanning clean (CodeQL reports no new alerts); Conventional-Commit PR title.
- [ ] A resolved design decision that changed → PRD and/or an ADR updated in the
      **same** PR.

## Merging (PRD §12.2, §12.6)

Merge **squash-only** (linear history, delete-branch-on-merge) once the review is
addressed **and all required CI checks are green** — wait for in-progress checks;
**never merge over a red or pending check**.

The `main-baseline` ruleset requires these **11** status checks:

`lint` · `test (ubuntu-latest)` · `test (macos-latest)` · `test (windows-latest)` ·
`pre-commit` · `commitlint` · `secret-scan` · `conda-lock-verify` · `schema-guard` ·
`docs-build` · `sidecar / parity`

**CodeQL is enforced, but it is not one of them.** It runs through GitHub code-scanning
**default setup** — which is why there is no `codeql.yml` in `.github/workflows/`, and
is what PRD §12.8 recommends for a solo maintainer — and is gated by a separate
`code_scanning` rule on the same ruleset (`alerts: errors`,
`security_alerts: high_or_higher`). Do not go looking for a missing workflow.

**Reviews.** The ruleset requires **0 approving reviews** but does require
**conversation resolution**: an unresolved review thread blocks the merge even when
every check is green. An automated reviewer (CodeRabbit) reads every diff, so in
practice that is the thread you will need to resolve — push the fix, reply on the
thread, and let the re-review resolve it. If its status check goes green while the PR
shows **zero** reviews and zero inline comments, that is a rate-limit artifact, not an
approval: re-request explicitly with a `@coderabbitai review` comment and wait for a
real walkthrough before merging.

## Reporting bugs & security issues

Blank issues are disabled: open a new issue and pick from the forms offered, which
route the report and apply the right labels for you.

- **Security vulnerabilities:** do **not** use a public issue — see
  [`SECURITY.md`](SECURITY.md) (GitHub Private Vulnerability Reporting). This is the
  one route where taking the wrong one causes harm.
- **Something wrong in the docs** — inaccurate, missing, unclear, stale, or a dead
  link. Include the **page URL** and the entry from the docs site's **version
  selector**: the site is versioned with `mike`, so both are needed to reproduce what
  you saw.
- **Open-ended questions** — "how should I approach…?" — belong in
  [Discussions Q&A](https://github.com/bioedca/tether/discussions/categories/q-a)
  rather than the issue tracker. A question whose answer turns out to be missing from
  the docs becomes a `type:docs` issue; a question that had to be asked is itself a
  documentation signal.

By contributing, you agree your contributions are licensed under
`GPL-3.0-or-later`.
