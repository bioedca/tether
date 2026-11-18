<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0107 — The MASH-FRET extraction test counts duplicate molecules

- **Status:** accepted; amends [ADR-0088](0088-extraction-test-on-mash-fret-simulated-videos.md) on the frozen limits
- **Date:** 2026-11-15
- **Deciders:** bioedca
- **PRD anchor:** §9.5 (the validation suite), §8 NFR-VALID
- **Milestone:** M19

## Context and problem statement

The extraction test of [ADR-0088](0088-extraction-test-on-mash-fret-simulated-videos.md)
(`tests/test_mash_fret_extraction.py`) matches each Tether molecule to its nearest true molecule
within 1.5 px. It does not check whether two Tether molecules match the same true molecule. If the
extraction wrote each molecule twice (its molecule row and its trace rows), every copy would match a
true molecule, and the found count, the false count, the position errors, the intensity ratios and
the correlations would all stay in their frozen limits. CodeRabbit found this gap in its review of
PR #879; issue [#1006](https://github.com/bioedca/tether/issues/1006) asks for a limit that a
duplicated molecule breaks. ADR-0088 says that only a new ADR may change `LIMITS`.

## Decision drivers

- A duplicated molecule must break a limit.
- The existing limits, and the rule for a false detection, must keep their frozen meaning.
- No new MATLAB run: the frozen MASH-FRET outputs stay as they are.

## Considered options

1. **A one-to-one match** (each true molecule matched once, the rest false). Rejected: it changes
   the meaning of the false count, which is a frozen limit of ADR-0088.
2. **A separate duplicate count beside the many-to-one match.** Chosen.

## Decision outcome

Chosen option 2.

- **The count.** `measure` keeps the many-to-one match of ADR-0088. Of the Tether molecules that
  match a true molecule (within 1.5 px), it counts those whose nearest true molecule an earlier
  matched Tether molecule already took, and `Measured.n_duplicate` reports the count.
- **The measurement**, at `c51da3b` (the head of `main` that this change starts from): 0 in each
  of `easy`, `low-snr`, `dense` and `uneven`. The close pairs of `dense` give no duplicate: Tether
  keeps at most one molecule of each pair, as ADR-0088 already measured.
- **The frozen limit** follows the margin rule of ADR-0088 for counts: at most the measured count.
  It is `"duplicate": 0` for each scenario in `LIMITS`. `violations` breaks the limit when the count
  is above it, when the count is NaN, and when the limit is missing.
- **The demonstration.** A test repeats each molecule row and each trace row of `easy` once. The
  duplicate count then equals at least the found count, and the duplicate limit breaks. The same
  test also shows the gap that this record closes: with the duplicate count set back to 0, the
  doubled output keeps every other limit.

### Consequences

- Good: a doubled molecule table no longer passes the extraction test.
- Good: the other limits of ADR-0088, and the false count, are unchanged.
- Bad: an extraction change that makes a real duplicate (two Tether molecules on one true molecule)
  fails the test and needs a new ADR, as for every other frozen limit.

## References

- [ADR-0088](0088-extraction-test-on-mash-fret-simulated-videos.md), issue
  [#714](https://github.com/bioedca/tether/issues/714) and PR #879, CodeRabbit review 5902721092 on
  `17a90b48afa3a09b93cf5f3c3367a9c9781be983`.
