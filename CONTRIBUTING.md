# Contributing to Tether

Thanks for your interest in Tether. This is the contributor-facing summary of
[`docs/PRD.md` §12](docs/PRD.md). `AGENTS.md` governs agent operations and safety;
the PRD governs product and science. Where this file abbreviates either, that
respective contract governs.

## Development model — solo + CI and review gates (scales up cleanly)

Tether is currently maintained **solo (account `bioedca`) with CI and a fixed
review lane as merge gates**. Branch protection on `main` requires green required CI and a
self-review checklist on every PR. The review lane and its rules are in
[`AGENTS.md`, section "Review"](AGENTS.md#review). The ruleset requires zero GitHub approval
reviews. Load-bearing changes also need the qualified human or domain judgment that `AGENTS.md`
names. If contributors join, this model scales to required human reviews and `CODEOWNERS`
(PRD §12.3).

`main` is **always releasable and protected**. Never push to `main` directly;
never merge, release, or declare a PR ready while required checks are red or pending.

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
  long-lived `develop`/`release` branches; milestones are GitHub Milestones,
  not git branches.
- **Branch naming:** `type/issue-N-short-slug`, optionally milestone/FR-scoped —
  e.g. `feat/issue-123-m1-atrous-detector`, `fix/issue-124-correct-nan-guard`. The
  slug is kebab-case, ≤ ~5 words. The branch name is not load-bearing (the PR
  title + linked issue carry authoritative metadata).
- **One exception, and it is load-bearing:** an automated agent claims an issue by
  atomically creating `agent/issue-N` — **no slug** — because that ref *is* the mutex
  (`POST /git/refs` answers `201` once and `422` thereafter). A slug derived from the
  title is not deterministic across agents, so two agents could create two different
  refs for one issue and both succeed, silently voiding it. See
  [ADR-0057](docs/adr/0057-github-native-swarm-coordination.md) and `AGENTS.md`.
- **Conventional Commits** govern **both commit messages and PR titles**:
  `type(scope): summary (FR-ID when applicable)`. Types: `feat fix docs chore refactor test ci
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

  `pytest` is **not** in the base lock — CI installs it explicitly, so a freshly
  restored environment needs the same step before any of this runs. The same line
  names `markdown-it-py`, the dev-only CommonMark parser the agent scripts under
  `.agents/bin/` read issue bodies with (ADR-0066); the lock already carries that
  exact version, so on a restored environment it is a no-op:

  ```bash
  python -m pip install "pytest==9.1.1" "pytest-qt==4.5.0" "markdown-it-py==4.2.0"

  # exactly what the required `test` matrix runs (see Test tiers below)
  QT_QPA_PLATFORM=offscreen pytest -m "not large and not sidecar and not deep and not workstation"
  ```

  Large/gated fixtures are exercised only by the scheduled `large-fixtures.yml`
  tier, never by the required matrix.

### Test tiers and markers

A bare `pytest` runs **everything**, marked included — `-m` is a filter, not a default.
The required matrix is therefore an explicit *exclusion*: it runs unmarked **and** `gui`
tests, and excludes only the four tiers that need something CI does not have. Three of
those have their own workflow; the fourth runs in no CI job at all:

| marker | needs | where it runs |
|---|---|---|
| *(unmarked)* | nothing beyond the base env | `ci.yml` — required, 3 OS |
| `gui` | PySide6/napari/pyqtgraph; headless via `QT_QPA_PLATFORM=offscreen` | `ci.yml` — required, 3 OS |
| `sidecar` | a live tMAVEN env (`$TETHER_SIDECAR_PYTHON`) | `sidecar.yml` — never the required matrix |
| `deep` | the isolated torch stack (`deep/conda-lock.yml`) | `deep.yml` / `deep-gpu.yml` — advisory |
| `large` | the gated large-fixture tier | `large-fixtures.yml` — scheduled |
| `workstation` | private laboratory data (`$TETHER_PRIVATE_DATA`) | no CI job — `scripts/workstation/run_private_checks.py` on the maintainer's workstation ([ADR-0071](docs/adr/0071-private-data-checks-run-from-a-workstation-task.md)) |

```bash
# The required matrix, verbatim — ci.yml runs this on all three OSes
# (Linux wraps it in `xvfb-run -a`; QT_QPA_PLATFORM=offscreen is the local equivalent).
QT_QPA_PLATFORM=offscreen pytest -m "not large and not sidecar and not deep and not workstation"

QT_QPA_PLATFORM=offscreen pytest -m gui    # just the GUI tier

# Skip Qt. The optional tiers must be excluded here too — `not gui` on its own still
# selects deep/sidecar/large, and the deep tests import torch, which the base lock
# does not carry.
pytest -m "not gui and not large and not sidecar and not deep and not workstation"
```

`--strict-markers` rejects an *unregistered* marker, which catches `-m deepp`. It does
**not** protect a negated expression: `-m "not largge"` is a perfectly valid filter that
happens to exclude nothing, so a typo there silently pulls the optional tiers back into
the run rather than failing. Read the collected count, not just the exit code.

Two naming rules are enforced by `tests/test_marker_contract.py` rather than
convention: a live sidecar test must be named `test_*sidecar*.py`, and deep tests use
the `test_*_deep.py` suffix — the isolated workflows select on those globs. The rule
works in both directions: every test in a file that matches one of those globs carries
the marker too (`test_setup_sidecar.py` is the one exception), because the job's `-m`
deselects any test that does not. A live sidecar test uses `requires_sidecar` from
`tests/_sidecar_gate.py`, which fails it instead of skipping it where the
`sidecar / parity` job sets `TETHER_REQUIRE_SIDECAR=1`. The same module keeps every
workflow's pytest command from selecting a `workstation` test, and
`tests/conftest.py` stops collection when a `workstation` test also carries `large`,
`sidecar` or `deep`. Without the runner a `workstation` test skips, so a local run that
leaves out `and not workstation` still passes.

### Golden numbers (the self-drift suite)

`tests/test_golden.py` runs three inputs from end to end — the committed 4-molecule SMD
fixture, the synthetic movie of `tests/_synth.py` (through `tether batch`), and synthetic
traces that exercise every correction — and compares each result with its golden file in
`tests/golden/`: molecule counts, per-molecule statistics, histogram counts, the native HMM,
TDP, dwell rates, and the SHA-256 of each exported table. `tests/test_golden_gui.py` checks
that the histogram dock draws the same histogram. It is a **regression suite, not an oracle**:
it fails when a number changes, not when a number is wrong.

Integers, strings and table checksums must match exactly. Floats must match to the relative
limit `FLOAT_RTOL` in the suite's helper module, which [ADR-0080](docs/adr/0080-self-drift-golden-numbers.md)
measured across the three operating systems of the matrix. Do not widen it to make a change
pass.

When a change moves a golden number on purpose:

```bash
python scripts/update_golden.py --check   # list what moved; write nothing
python scripts/update_golden.py           # rewrite tests/golden/ and print the table
```

Paste the printed table (it starts with `| golden file | quantity | old | new | relative change |`)
into the pull request body and add a line that starts with `Cause:` and says why the numbers
moved. An array that kept its shape gets one row for each changed element, at most 20,
and then one row that counts the rest. The `golden-table` workflow fails a pull request that changes a file in `tests/golden/`
without both, and runs again when you edit the body. Never edit a golden file by hand. A
dependency update that moves a number fails here on purpose: the table and the cause are then
the record of what the update changed.

### Coverage floors

The Linux leg of the required matrix runs the same tests under `coverage` with branch
measurement and puts a table of line and branch coverage, for the total and for each
package of `src/tether`, in the job summary (ADR-0072). `.github/coverage-floors.toml`
holds one floor of line coverage for the total and one for each package. The job fails
when a value is below its floor, and when a new package has no floor. A floor can only
go up: `tests/test_coverage_floors.py` fails when a floor is lower than on `main`. When
the job prints a notice that a floor can be raised, raise it in the same or a later pull
request. To see the numbers locally:

```bash
python -m pip install "coverage==7.17.2"
QT_QPA_PLATFORM=offscreen coverage run -m pytest -m "not large and not sidecar and not deep and not workstation and not reference_download"
coverage json -o coverage.json && python scripts/check_coverage_floors.py coverage.json
```

### Building the docs locally

The required `docs-build` gate is `mkdocs build --strict`, where **warnings are
errors**. A new page must be registered in `mkdocs.yml` `nav` or the build fails, and
it must not link to `docs/PRD.md`, which the site deliberately does not serve.

**ADR records are the exception.** `mkdocs.yml` matches them with `not_in_nav:
adr/0*.md`, which keeps each record *in the build* — so `--strict` still validates its
links — while keeping it out of the navigation tree; only the index (`adr/README.md`)
is nav'd. Do **not** add a new ADR to `nav`.

```bash
pip install -r requirements-docs.txt
mkdocs build --strict          # the gate
mkdocs serve                   # live preview at http://127.0.0.1:8000
```

`requirements-docs.txt` is the **only** docs toolchain pin: there is no `docs` extra in
`pyproject.toml`, so do not reach for `pip install -e .[docs]`.

`--strict` only validates links it can *resolve*, so two ordinary tests cover what it
cannot see, on all three operating systems:

- `tests/test_docs_links.py` fails on a reference-style `[text][key]` with no
  `[key]: …` definition in the same page (Python-Markdown renders it as literal text),
  and on a relative link or `#anchor` — heading slug or inline `<a id="…"></a>` — that
  does not resolve. Write a bare citation key as `[Key2018], [Key2005]`, never as
  `[Key2018][Key2005]`, which is a reference link to a missing definition. Its parse
  models the Markdown forms listed in its module docstring and promises nothing beyond
  them. An unlisted form it can detect, and that could change its verdict, fails with
  `unsupported Markdown form, the checker cannot vouch for it` and the file and line:
  rewrite the page in a listed form. Any other unlisted form is not guaranteed; known
  gaps are tracked in #515.
- `tests/test_docs_examples.py` checks that every `from tether…` / `import tether…` line
  in a `python` fence names a real module and object, by reading the source — it never
  imports, so a deep-stack example resolves without torch. The fence must open and close
  at column zero, as `fenced_code` requires. A tether import line anywhere else on a page
  fails the test.

Every `docs-build` run uploads the rendered site as the `docs-site` artifact (kept seven
days), so a reviewer can open the changed page rather than infer it from markdown.
External links are checked weekly, advisory only, by the `external-links` job in
`deps-audit.yml`.

## Documentation

Write each documentation page to the
[writing style guide](docs/maintainers/writing-style.md). It gives the rules for sentences,
words, terms, warnings and page shape, and the files that the rules apply to. A reviewer can
cite a rule by its number. To build the site and run its checks, see
[Building the docs locally](#building-the-docs-locally).

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
   Regenerate the affected lock(s): base, isolated sidecar, and/or isolated deep.
   Keep all three separate—the sidecar's `numpy<2`/PyQt5 and deep PyTorch stack
   must never merge into the PySide6 base—and confirm `conda-lock-verify` is green.

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

Cloud reviewers process PR diffs according to the review lane in `AGENTS.md`; this is
third-party processing. Do not open a PR until its contents are safe to send.

## PR self-review checklist (PRD §12.4)

Before requesting review / merging, confirm:

- [ ] Tests added/updated, green on the 3-OS small-fixture matrix; new GUI
      behavior has a `pytest-qt` test.
- [ ] **Schema freeze respected** (`schema-guard` green; structural change ⇒
      ADR + version bump).
- [ ] **conda-lock** regenerated if deps changed (base, sidecar, and/or deep, isolated);
      `conda-lock-verify` green.
- [ ] Any new tunable registered in PRD §11.2 (single source of truth), not
      hardcoded.
- [ ] Provenance / params / app-version stamped into the `.tether` for any new
      analysis (NFR-REPRO).
- [ ] SPDX `GPL-3.0-or-later` header on every new source file; `reuse lint`
      green.
- [ ] **Docs updated** — the `mkdocs` pages under `docs/` *and* the public docstrings
      for anything user-facing this PR changes; `docs-build` green.
- [ ] **Data policy respected** — no raw/private/unlicensed data or large data in ordinary
      Git; issue-authorized redistributable fixtures carry license and provenance in named
      small or LFS/gated paths.
- [ ] **No secrets committed** — no token, key, credential or private path in code,
      tests, logs or fixtures; `secret-scan` green.
- [ ] Code scanning clean (CodeQL reports no new alerts); Conventional-Commit PR title.
- [ ] **Risk recorded, and the review lane complete** — `low`, `standard`, or `high`, which
      routes nothing; the round; and a
      result from every provider the lane reached — **either** a substantive review **or** that
      provider's own quoted "nothing to review" for the head it read, a Codex 👍 included.
      **CodeRabbit with no actionable comments is required — or, if its one-review cap is spent
      and every finding is disposed of, the Codex review of the final head that closed the gate in
      its place** — and that is a verdict a completed
      review reached rather than an absence of one: record the review itself — permalink, and the
      commit it read, **which must be the final head for whichever review closes the gate**. On the
      cap-spent path CodeRabbit's review is recorded at whatever head it read, and it is the posted
      Codex review that closes and names the final one — asked with an `@codex review` comment, since
      the closer has to be posted and name the head it read — in either shape `AGENTS.md` §Review
      describes — and a local CLI run posts nothing. A `PENDING` review has no
      `submitted_at` and is not a submitted one; a `DISMISSED` one is a verdict *withdrawn* and
      proves nothing. **The `Actionable comments posted:` convention is CodeRabbit's** — its clean
      verdict is written by that line being **absent** rather than reading `0`, and a Codex review
      has no such line: what stands in its place is the verdict it posted, quoted, against the commit
      it names.
      **A review of an earlier head does not close the gate**, whether a material push moved the
      head or a non-material one did. It still stands as the completed review, which is
      exactly what the cap-spent path relies on: recorded at the head it read, with the Codex
      closing review covering the final one. The non-material case is worth naming because it looks
      like an exception and is not one — a clean review followed by a formatting commit keeps its
      evidence, so the cap is spent with nothing outstanding, and the Codex close is what names the
      commit `--match-head-commit` binds. That is the ordinary close doing ordinary work, not a
      special path, and it is strictly more work than the clean review it follows. Neither silence
      nor a green `CodeRabbit` status check is the gate; both are also what a request that reviewed
      **nothing** leaves behind (see the full-review command below). Greptile is optional, and its absence
      for want of credits is recorded rather than excused as a review. Where a Greptile review
      stood in for a CodeRabbit that refused for more than 30 minutes, record the refusal and
      record that review as this item asks of CodeRabbit's. Blocking
      findings fixed, non-blocking ones deferred to a follow-up issue, per `AGENTS.md`.
- [ ] A resolved design decision that changed → PRD and/or an ADR updated in the
      **same** PR.

## Merging (PRD §12.2, §12.6)

This section tells you how a pull request (PR) reaches `main`. The full review and merge rules
are in [`AGENTS.md`, section "Review"](AGENTS.md#review). Where the two differ, `AGENTS.md`
governs.

1. Open the PR as a draft.
2. Wait until every required check is green. Do not merge over a red or pending check.
3. Ask Codex for a review with an `@codex review` comment.
4. Mark the PR ready for review.
5. Ask CodeRabbit with an `@coderabbitai full review` comment. The incremental command reviews
   nothing on this repository.
6. Fix each serious finding. Defer or answer the others as `AGENTS.md` says.
7. Resolve each review thread. An unresolved thread blocks the merge, even when every check is
   green.

No provider reviews a PR automatically here, so a review that was not asked for does not come.
A review usually posts within an hour of the request. A provider can refuse for a quota or a
rate limit, and that can hold a PR for hours. A refusal is a wait, not a pass.

The `main-baseline` ruleset requires these **11** status checks: `lint` · `test (ubuntu-latest)` ·
`test (macos-latest)` · `test (windows-latest)` · `pre-commit` · `commitlint` · `secret-scan` ·
`conda-lock-verify` · `schema-guard` · `docs-build` · `sidecar / parity`. CodeQL is also
enforced, but not as a status check. It runs through code-scanning **default setup** and a
separate `code_scanning` rule, so `.github/workflows/` has no `codeql.yml`.

The maintainer merges, **squash-only** (linear history, branch deleted on merge). An agent merges
only with explicit merge authority for that one PR. A green lane does not give that authority.
Arm auto-merge only after the review lane is complete. The lane is not a required check, so GitHub
can merge as soon as the checks are green. Arm it with this command:

```bash
gh pr merge <PR> --auto --squash --match-head-commit <SHA>
```

`<SHA>` is the 40-hex head that the clean review read. Take it from that review, not from the PR.
Releases, tags, signing and each new scientific claim or citation also need the maintainer's
sign-off.

## Reporting bugs & security issues

Blank issues are disabled: open a new issue and pick from the forms offered, which
route the report and apply the right labels for you. Every public form starts at
`status:backlog`; no form can apply `status:ready`. Work moves through
**new → `status:backlog` → groomed → exact-body maintainer approval →
`status:ready`**. Grooming confirms testable acceptance criteria, dependencies,
execution autonomy, related-work/file overlap, and a one-PR scope before a maintainer
approves the exact title/body snapshot.

Bug, documentation, feature, validation-oracle, and maintenance forms are
work-producing intake. The maintenance form covers chore, CI, test, refactor, and
governance work while blank issues remain disabled; it starts as `type:chore`, and a
maintainer may correct that type during grooming. Every form also requires the same
attestations for duplicate search, private vulnerability reporting, secrets, and
private/raw/unlicensed/user/lab data.

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
  the docs becomes a separate `type:docs` issue; a question that had to be asked is
  itself a documentation signal. The concrete Question form is explicitly non-worker
  intake: it may remain in `status:backlog`, but it cannot become `status:ready` or
  enter the swarm without conversion to a separate work issue.

By contributing, you agree your contributions are licensed under
`GPL-3.0-or-later`.
