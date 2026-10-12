<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0067 — GitHub Releases are the only change record

- **Status:** accepted (supersedes the committed-`CHANGELOG.md` wording of PRD §12.7 and its
  single "Internal" changelog bucket)
- **Date:** 2026-10-12
- **Deciders:** bioedca
- **PRD anchor:** §12.7 (Releases & versioning), §12.2 / §12.6 (Conventional Commits)
- **Milestone:** M9

## Context and problem statement

Tether has nine pre-1.0 version tags, `v0.0.0` through `v0.8.0`, and no record of what changed
between any two of them. A scientist re-running an analysis a year from now, or a reviewer asking
whether a result predates a correction-factor fix, had nothing to consult but `git log`.

The [product spec](https://github.com/bioedca/tether/blob/main/docs/PRD.md) §12.7 promised a
`CHANGELOG.md` "generated from commit history, never hand-edited", and a pipeline step that writes
"into `CHANGELOG.md` + the Release body". Nothing ever wrote the committed file. What does exist is
`release.yml`'s changelog step, which builds `out/CHANGELOG-<tag>.md` from Conventional Commits and
publishes it as the Release body and as a Release asset. Two defects in that step would have shipped
in the `v1.0.0` notes:

- **The diff base.** It diffed against `git describe` from the tag's parent, which for `v1.0.0`
  resolves to `v1.0.0-rc2`. The first stable notes would have listed the commits since the last
  release candidate instead of everything since `v0.8.0`.
- **The sections.** It grouped only `feat`, `fix`, `perf`, `docs`, `refactor`, `build` and `ci`. A
  breaking change landed unmarked under its type's group, though §12.7 requires a "⚠ Breaking
  Changes" section, and `test` and `chore` commits appeared nowhere.

Where does a reader find what changed between two versions, and what does the record contain?

## Decision drivers

- **A reader needs one place to look.** Two records that could disagree are worse than one.
- **The notes stay reproducible from `git log`.** No hand-written release prose, so any Release can
  be regenerated and checked against history.
- **Backfilling the pre-1.0 tags is archaeology.** The question a scientist actually asks — does my
  result predate a given fix — is answered by a compare view, which already exists for every pair
  of tags.
- **The stable notes must be complete.** Users skip release candidates; the `v1.0.0` notes are what
  they read.

## Considered options

- **Releases only, no committed file** (chosen).
- **A committed `CHANGELOG.md` regenerated at each release**, as §12.7 described. It duplicates the
  Release body, needs a commit on `main` per release (a write the release workflow does not have),
  and can drift from the Releases it mirrors.
- **Backfilled Releases for `v0.0.0`…`v0.8.0`.** Considered on 2026-07-20 and rejected: it creates
  nine historical Release objects nobody asked for in place of a one-line answer.
- **Adopting `git-cliff` or `release-please`**, the tools §12.7 names. Either is a new dependency in
  the release job for behaviour a short shell step already provides.

## Decision outcome

Chosen option: **Releases only**, because it is the one record that already exists, is already
generated, and needs no write back to `main`.

1. **GitHub Releases are the only change record.** There is no committed `CHANGELOG.md`, and there
   are no Release objects for the pre-1.0 tags. The published `v1.0.0-rc1` and `v1.0.0-rc2` Releases
   stay as they are.
2. **Pre-1.0 history is git history.** `docs/release.md`'s Change record section says so and links a
   GitHub compare view from each pre-1.0 tag to the one before it.
3. **The diff base follows the tag's kind.** A stable tag (the `verify` job's `prerelease=false`)
   diffs against the nearest earlier tag with no prerelease component, so `v1.0.0` covers
   `v0.8.0..v1.0.0`. A prerelease diffs against the nearest earlier tag of any kind, which is what
   rc1 and rc2 did.
4. **The sections, in order:** "⚠ Breaking Changes", Features, Bug fixes, Performance,
   Documentation, Refactoring, Build & packaging, CI, Internal. A commit is breaking when its
   subject is `<type>!:` or `<type>(scope)!:`, or a line of its body begins `BREAKING CHANGE:` (or
   the Conventional Commits synonym `BREAKING-CHANGE:`); it is listed under Breaking and not also
   under its type. That line is meant to be the footer, but the step does not parse the footer
   block: any line starting that way counts. Internal collects `test` and `chore`.
5. **The asset name is unchanged:** `CHANGELOG-<tag>.md`, attached to the Release beside the
   installers.

### Supersedes

- §12.7's "`CHANGELOG.md` is generated from commit history, never hand-edited" and step 4's "into
  `CHANGELOG.md` + the Release body". The Release body is the record; the generated file is a
  Release asset only.
- §12.7's bump-map table, which puts `docs`, `test`, `ci`, `build`, `chore` and `refactor` into one
  "Internal" changelog bucket. Documentation, Refactoring, Build & packaging and CI keep their own
  sections, as every published Release has had them; Internal holds `test` and `chore` only. The
  table's version-bump columns are unaffected.

### Consequences

- Good: the `v1.0.0` notes list everything since `v0.8.0`, with breaking changes set apart.
- Good: a reader has one route to what changed — the Releases page, or a compare URL for any pair
  of tags — and the docs site links it from its landing page.
- Bad / trade-off: history before `v1.0.0-rc1` is raw commit subjects in a compare view, not
  curated notes. That is the accepted cost of not backfilling.
- Bad / trade-off: the notes are only as good as the squash titles. A breaking change merged
  without `!` or a footer is listed under its type, unmarked.
- Follow-up: `tests/test_release_changelog.py` runs the changelog step's own script against a
  throwaway history and fails if the stable diff base, the Breaking section or the Internal section
  is reverted.

## More information

- Issue [#182](https://github.com/bioedca/tether/issues/182) and its maintainer decisions of
  2026-07-20 and 2026-10-12.
- `.github/workflows/release.yml`, step `Generate the changelog`.
- [Change record](../release.md#change-record) in the release documentation.
- [ADR-0059](0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md) for the rest of the
  release pipeline.
