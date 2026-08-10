# Contributing to Tether

Thanks for your interest in Tether. This is the contributor-facing summary of
[`docs/PRD.md` §12](docs/PRD.md). `AGENTS.md` governs agent operations and safety;
the PRD governs product and science. Where this file abbreviates either, that
respective contract governs.

## Development model — solo + CI and review gates (scales up cleanly)

Tether is currently maintained **solo (account `bioedca`) with CI and a fixed
review lane as merge gates**: branch protection on `main` requires
green required CI plus a self-review checklist on every PR, while `AGENTS.md`
requires a substantive final-head review on one fixed lane, cheapest provider first:
**Codex on the green diff until nothing blocking remains, then optionally one metered
Greptile review, then CodeRabbit with no actionable comments before merge.**
Copilot is advisory only and never satisfies a leg. The ruleset still requires zero GitHub approval
reviews; load-bearing changes additionally need any qualified human/domain judgment
specified in `AGENTS.md`. This scales to required human reviews + `CODEOWNERS` if
contributors join (PRD §12.3).

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
  long-lived `develop`/`release` branches; milestones M0–M10 are GitHub
  Milestones, not git branches.
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
  restored environment needs the same step before any of this runs:

  ```bash
  python -m pip install "pytest==9.1.1" "pytest-qt==4.5.0"

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

# Skip Qt. The optional tiers must be excluded here too — `not gui` on its own still
# selects deep/sidecar/large, and the deep tests import torch, which the base lock
# does not carry.
pytest -m "not gui and not large and not sidecar and not deep"
```

`--strict-markers` rejects an *unregistered* marker, which catches `-m deepp`. It does
**not** protect a negated expression: `-m "not largge"` is a perfectly valid filter that
happens to exclude nothing, so a typo there silently pulls the optional tiers back into
the run rather than failing. Read the collected count, not just the exit code.

Two naming rules are enforced by `tests/test_marker_contract.py` rather than
convention: a live sidecar test must be named `test_*sidecar*.py`, and deep tests use
the `test_*_deep.py` suffix — the isolated workflows select on those globs.

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
      **CodeRabbit with no actionable comments is required**, and that is a verdict a completed
      review reached rather than an absence of one: record the review itself — permalink, the
      `commit_id` it read, **which must be the final head**, `submitted_at` with a state of
      **`COMMENTED` or `APPROVED`** (a `PENDING` review has no `submitted_at` and is not a submitted
      one; a `DISMISSED` one is a verdict *withdrawn* and proves nothing), and the
      opening of its body. **A review of any earlier head does not qualify, however clean it was** —
      answering a finding moves the head, so that review is evidence about a diff this one is no
      longer. The clean verdict is written by the `Actionable comments posted:` line being
      **absent** rather than reading `0`. Neither silence
      nor a green `CodeRabbit` status check is the gate; both are also what a request that reviewed
      **nothing** leaves behind (see the full-review command below). Greptile is optional, and its absence
      for want of credits is recorded rather than excused as a review. Blocking
      findings fixed, non-blocking ones deferred to a follow-up issue, per `AGENTS.md`.
- [ ] A resolved design decision that changed → PRD and/or an ADR updated in the
      **same** PR.

## Merging (PRD §12.2, §12.6)

Merge **squash-only** (linear history, delete-branch-on-merge) once the review is
addressed **and all required CI checks are green** — wait for in-progress checks;
**never merge over a red or pending check**.

Automated agents are peers, not a hierarchy: each claims one issue, opens one **draft**
PR, gets it reviewed, and **hands off or merges** rather than sitting and polling. There is no coordinator. Auto-merge is armed at the **end** of the lane, and
completing the lane is **not by itself authority to arm it** — `AGENTS.md` requires
explicit per-PR merge authority, which is a separate grant that no amount of green
checks confers. Arming it on a draft would merge the PR past the mandatory CodeRabbit
gate, since that gate is not a required check. The merge is bound to the head the review evidence covers with
`gh pr merge N --auto --squash --match-head-commit <SHA>` — that guard is what stands in
for the merge queue, which needs an organization-owned repository and so is unavailable
here. **`<SHA>` is the 40-hex head the clean review read, never the head re-read while
arming** — `AGENTS.md` §Review is the rule, including why re-reading it makes the guard
always pass.

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
every check is green. Classify the final diff before merge and follow `AGENTS.md`:
Copilot is optional, while every PR needs substantive independent review requested once
checks are green and the diff is declared final. **Every PR walks the same lane,
cheapest provider first: Codex on the green diff, uncapped — the draft by default, or the
ready PR whose reason is recorded; then optionally one metered Greptile **review** if the
seat has budget, a review being one credit as a standard and three as a TREX; then
CodeRabbit with no actionable comments, which is the last gate before merge.** **Open as a draft and get it green there** — every
required check runs on a draft, so the diff reaches fully green before anyone is asked to
read it, and that is what makes the sequence affordable rather than a policy nobody keeps.
Opening ready is not forbidden, but it spends a metered provider on a diff no cheap one has
seen; the old rationale for allowing it turned on the round counter ADR-0064 retires, so
what remains is simply that it costs more for nothing. Record the reason in the PR. Author-side or local review, and status-only
output, do not satisfy it. **Exhaustion is not incapacity** — a provider with no budget
left has not reviewed: Greptile out of credits is skippable and never blocks, while
CodeRabbit unavailable freezes the PR.

**No provider auto-reviews this repository; you have to ask.** CodeRabbit replies
to an unrequested PR with *"Auto reviews are disabled on this repository"*, and Codex
reviews only when you open a PR for review, mark a draft ready, or comment
`@codex review`. A provider that was never asked has not declined — so if you are
waiting on a review, check that a request was actually posted.

One exception, and it has already cost money: `.greptile/config.json` is read from
the pull request's **source branch**, so a branch cut before that file landed still
auto-fires Greptile on open. Answer that review like any other and record the optional
Greptile step as spent — the credit is gone either way. Rebasing onto a base that
carries the config prevents the next one.

Ask CodeRabbit with **`@coderabbitai full review`**. The bare `@coderabbitai review`
is the *incremental* command and applies only where automatic reviews are **paused**;
they are **disabled** here, so it reviews nothing and replies *"CodeRabbit is an
incremental review system and does not re-review already reviewed commits"* — which
reads a great deal like a review that found nothing.

CodeRabbit's fair-use limit is **adaptive**: several reviews in one sitting drop the
seat to a per-interval allowance, and the refusal names when the next included review
is due. That is a **wait**, not unavailability — wait it out and ask again, which
spends nothing against the two-completed-reviews ceiling, since a request that
produced no review is not one of the two. It also offers to proceed through usage-based
billing; that is the maintainer's spending decision, never a worker's. Pace review
requests rather than batching them.

**Before any re-request, check that a review is not already running.** The `CodeRabbit`
commit status reads `pending` / *"Review in progress"* while one is in flight, and a
second request **aborts it** — spending the window on nothing and triggering the limit
above. The elapsed interval is not on its own a licence to ask:

```bash
gh pr checks <PR> --json name,state,description --jq '.[] | select(.name == "CodeRabbit")'
```

**Do not write a provider's handle in a comment you do not mean as a request.** The
mention fires the bot even inside backticks — a code span is not an escape — so
quoting a trigger while describing it spends a real review. Break the handle, or say
"the full-review command" instead.

Review evidence **survives a non-material push**, so addressing findings does not
restart the gate — merging `main` in cleanly, formatting, comment edits and ADR
renumbering (renumber-only — touching a word of the decision is not) are all non-material, and
that exception list WINS over the material paths below, while executable code, scientific claims, data,
schema, locks, CI/release config and the governance text itself (`AGENTS.md`,
`CLAUDE.md`, this file, `docs/PRD.md`, `docs/adr/**`, `.agents/**`, `docs/agents/**`,
`.claude/**`, `.github/pull_request_template.md`, `.greptile/**`) are material — the
list is *every file that states a rule*, because a push changing what the gate requires
must not keep evidence gathered under the old requirement. A material push
re-arms the review but raises no ceiling: there are **at most two completed reviews per
metered provider** however many pushes precede them (Codex, being unmetered, is uncapped — see below).

Fix blocking findings. Blocking is decided on the **severity axis only**: CodeRabbit
`Critical`/`Major`, Codex `P1`, **Greptile `P1`** — its badges use the same P-scale as
Codex, so they map straight across, and a review the seat paid a credit for must not be
answerable entirely by deferral — plus anything touching secrets, unlicensed data, a
frozen oracle, the §5 skeleton, or a CodeQL alert — and anything that falsifies a claim
the PR itself introduces.

CodeRabbit renders three independent things on a finding and only one of them is the
severity: a **domain** (`🎯 Functional Correctness`, `📐 Maintainability & Code Quality`,
…), a **severity** (`🔴 Critical`, `🟠 Major`, `🟡 Minor`), and a machine marker
(`<!-- cr-indicator-types:potential_issue -->`). The marker is a *category*, not a level —
it appears on `🟡 Minor` and `🟠 Major` alike — so it never makes a `Minor` blocking. An
earlier revision of this list read `Critical`/`Major`/`Potential issue`, which mixed the
two axes and left every `Minor` ambiguous.

Defer everything else to **one** follow-up issue per PR and resolve the thread with a
link; do not fix non-blocking findings in the same PR, and never point a deferral at an
issue that does not exist. On agent-layer paths that rule inverts and the finding is
dropped rather than tracked, which is stated in full below; **those same paths are also
feature-complete** (ADR-0064) — they take bug and safety fixes only, so a capability
change needs a maintainer-opened issue and may never originate in a review finding.
Dropping the sub-floor finding without that second rule would still leave a reviewer able
to commission new agent machinery through the deferral above. If a **selected** provider reports nothing to review at the
head it read — a deletion, a pure rename, or Codex's 👍 reaction, which is its documented
"no suggestions" — that satisfies its leg; quote it. A statement from the author, or from
any other commenter, never does. **Exhaustion is not incapacity**: a provider with nothing
to say has reviewed, a provider with no budget left has not. Greptile out of credits is
skippable and never blocks; **CodeRabbit unavailable freezes the PR**, because it is the
gate. Record which and why — and a quota refusal means the provider **did not review**, and never counts as a pass.

**Two completed reviews per metered provider, then stop.** The cap bounds how many times a
provider whose reads cost money or quota is made to *read the diff*, so **Codex
is uncapped** — it is unmetered, which is the whole reason it goes first. Otherwise a request that produced nothing (a throttle, a quota refusal,
a failed run) is not one of the two — counting those would make the gate unsatisfiable
exactly when the provider is rate-limiting. It does **not** license a third review, and it
does not license hammering: honour the retry interval the refusal names, and never
re-request while the status check reads `pending`. **Greptile is one *review* in practice**, and a review is not always one
credit — a standard review costs one, a TREX review three. Two is the shared ceiling, not a second review to plan on, so ask
again only if the first found something blocking and the seat still has budget. If a third
pass would be needed, hand the pull
request to the maintainer with a comment saying why. Nothing counts this for you; the
merged history is auditable and you are trusted with it. On agent-layer paths
(`.agents/`, `docs/agents/`, `AGENTS.md`, `CLAUDE.md` and the agent test modules) a
finding below the severity floor is **dropped rather than tracked**, because there the
follow-up issue becomes another agent-layer pull request and the loop feeds itself
(ADR-0064). Dropped is not silent: reply on the thread in the wording `AGENTS.md`
§Review gives, and resolve it — untracked and unanswered are different things, and only
the reply leaves the decision on the record. Human sign-off is required only for releases, tags, signing, and any new
scientific claim **or citation**.

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
