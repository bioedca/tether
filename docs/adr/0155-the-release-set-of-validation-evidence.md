<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0155 — The release set of validation evidence lives beside the tag, and its one-commit rule runs only in the release workflow

- **Status:** accepted
- **Date:** 2026-11-26
- **Deciders:** bioedca (the acceptance criteria of #1439)
- **PRD anchor:** §9.3 *Stable release acceptance*, §9.5 (the validation suite), §12.7
- **Milestone:** M18
- **Issue:** [#1439](https://github.com/bioedca/tether/issues/1439)
- **Builds on:** [ADR-0071](0071-private-data-checks-run-from-a-workstation-task.md) (the
  workstation record of oracle (a), #699)

## Context and problem statement

PRD §9.5 says the validation suite runs end to end when every gate oracle has passing evidence
bound to the release commit, with one artifact per oracle naming the commit it was produced at.
Only oracle (a) had a record (`docs/validation/oracle-a-last-run.json`), and nothing checked that
a set of records names one commit. A set that must name one commit can be complete only at the
release commit, so a rule that ran on every pull request would be red on all of them. Where does
the set live, which commit does it name, and when does the one-commit rule run?

## Decision drivers

- **A record cannot name the commit that contains it.** A commit's SHA covers its own files.
- **An ordinary pull request must not fail** because no set exists or the newest set names an
  older commit.
- **The tag must not publish without its set** (PRD §9.3), and the check must run where the tag
  is built.
- **Summary numbers and neutral ids only**: no laboratory data, private path or machine name.

## Considered options

1. **A committed set per tag, `docs/validation/release/<tag>/`, checked in full by the test
   suite, required for its tag only by `release.yml`** (chosen).
2. **Records attached to the GitHub Release as assets.** The set would not be reviewed in a pull
   request, nothing in the repository could check it, and the Release does not exist when
   `verify` runs.
3. **One "latest" set, overwritten at each release, with the one-commit rule on every pull
   request.** Every pull request after the set lands would name a newer commit than the set, so
   the rule would either be red or have to be skipped, and an older release would lose its
   evidence.

## Decision outcome

Chosen option: **1**.

- **Format.** One JSON record per oracle (a) to (g) and one for the deep-tier run, schema
  `tether-validation-record/1`: the oracle, its tier, the commit, exactly one of a run URL of this
  repository or the SHA-256 of a local-run log, the measured numbers (names and numbers only), the
  §11.2 rows or frozen files they are compared with, and the outcome. The deep record also shows
  its CUDA tests executed and not skipped (§9.5). `scripts/validation_evidence.py` writes and
  checks them; the record of (a) is made from the #699 record and the hash of the workstation
  result file it names.
- **Where the set lives.** `docs/validation/release/<tag>/`, eight files. It is committed and
  reviewed like any change, and it stays after the release, so each release keeps its evidence.
- **Which commit.** The *release commit* is the commit the oracles ran at. The tag is placed on
  the commit that adds the set, and that commit may differ from the release commit only by the
  files of its own set.
- **When the rules run.** `tests/test_validation_evidence.py` checks the format on every pull
  request and checks in full every set that is committed (all eight records, one commit, every
  gate passed, (c) advisory). It requires no set. The `verify` job of `release.yml` runs
  `check-release` for the tag it builds: the set of that tag must be complete and bind the tag
  commit as above. It fails a stable tag that publishes; for a prerelease or a dry run it reports
  and does not fail.

### Consequences

- Good: an ordinary pull request is never red for want of a set, and the tag cannot publish a
  stable release without one.
- Good: a set is reviewed in a pull request, and a later edit to it is checked again.
- Bad: no other change may land on `main` between the release commit and the merge of the set; if
  one does, the oracles run again at the new commit.
- Neutral: a prerelease needs no set. `release.yml` is CI configuration, and this changes it.

## More information

PRD §9.5 is unchanged in meaning: the set is the "one artifact per oracle naming the commit it was
produced at". No oracle, tier or tolerance changes. `docs/validation.md`
(*The evidence records of a release*) and `docs/release.md` (step 6) say how to make and check the
set.
