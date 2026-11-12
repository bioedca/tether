<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0080 — A self-drift suite freezes end-to-end numbers in golden files

- **Status:** accepted
- **Date:** 2026-11-12
- **Deciders:** bioedca
- **PRD anchor:** §9.5 (the validation suite), §12.6 (continuous integration)
- **Milestone:** M19

## Context and problem statement

Each stage of Tether has unit tests, but no test froze what Tether computes from end to end.
A change that moves the final numbers a little — a different rounding, a changed default, a
reordered correction — passed when each unit test still passed, and the reference oracles use
limits wide enough to hide it. The test audit of 2026-11-11 confirmed this for the project
layer (PRJ-U1; #728, #729, #730). How does a pull request see, and the maintainer approve,
every change of an end-to-end number, on all three operating systems, without the tests
becoming a second source of truth for what is scientifically correct?

## Decision drivers

- The tests must find self-drift, and a different result between operating systems or after a
  dependency update (interview of 2026-11-11, Q2: A, D).
- Scope: the base application, the values that the GUI shows, the CLI and the export files
  (Q3: A, C, D).
- The maintainer approves each change of a frozen number on the pull request, which shows the
  old number, the new number and the cause (Q6: A).
- Self-drift tests are a regression suite, not oracles (Q36: A).
- The suite runs in the required matrix (no network, no large file) in less than 60 seconds.

## Considered options

- **Widen the existing oracles' coverage.** Rejected: an oracle has a limit chosen for
  scientific agreement with another tool; tightening it to catch drift would turn it into a
  regression check and blur what its number means.
- **Snapshot whole `.tether` stores byte for byte.** Rejected: a store holds uuids and
  timestamps, and HDF5 layout bytes move with h5py versions for reasons that change no result.
- **Golden files of named quantities with one tolerance policy.** Chosen.

## Decision outcome

Chosen option: **golden files of named quantities with one tolerance policy**, because each
quantity has a name the pull request table can show, and the policy can be exact where the
result is exact.

- Three inputs, all committed or generated: the 4-molecule tMAVEN SMD fixture
  (`tests/fixtures/smd_4mol.hdf5`, analysis-only import), the #701 synthetic movie run through
  the CLI (`tether batch --no-idealize`), and synthetic traces built from the #701 generator so
  that photobleaching, leakage α, γ and corrected E all run (twelve acceptor-first bleaches,
  twelve donor bleaches, twelve unbleached molecules).
- Each input runs through the public API: extraction, background, photobleach, leakage, γ,
  apparent and corrected FRET, the population histogram, cross-correlation, the raw FRET cloud,
  a native Gaussian-HMM idealization stored as a model, TDP, dwell times, transition
  probability, state number, and the two export tables. The sidecar fit is out of scope: its
  random starts need the parity limits, not exact goldens.
- The quantities are written to `tests/golden/<input>.json` (sorted keys, strict JSON, non-finite
  values as strings). `tests/test_golden.py` compares them; `tests/test_golden_gui.py` compares
  the histogram that the dock draws, and its title label, with the same golden file.
- **Tolerance policy.** Integers, strings, booleans, the shape of each value and the set of
  quantities compare exactly; an integer that becomes a float is a change. A float compares
  with the relative limit `FLOAT_RTOL`, relative to the larger magnitude, with no absolute
  floor, so a quantity that is only rounding noise near zero is not stored. An exported table
  compares by the SHA-256 of its text after random ids are masked and each float cell is
  rounded to 10 significant digits. Integer arrays (state paths, dwell lengths, TDP counts)
  compare by the SHA-256 of their little-endian `int64` bytes.
- **The limit is measured, not chosen.** The golden files were written on Linux. The first CI
  run of #702 compared them with `FLOAT_RTOL = 0` on Linux, macOS and Windows (table under
  *More information*). Every integer, string and checksum matched on all three. The largest
  float difference was 5.9e-10, in the output of an iterative fit; `FLOAT_RTOL = 1e-8` leaves
  a factor of about 17 above it. A later change that moves a float by more than that is a
  change, whatever its cause.
- `python scripts/update_golden.py` regenerates the files and prints a Markdown table: golden
  file, quantity, old value, new value, relative change. It lists every quantity that
  changed, also one inside the limit. An array that kept its shape gets one row for each
  changed element, named by its index, up to 20 rows; one more row counts the rest, whose
  values the diff of the golden file shows. It writes no file until every input has run.
  `--check` writes nothing and exits 1 when a quantity moved.
- The `golden-table` workflow runs on each pull request (also when its body is edited) and
  fails when a file under `tests/golden/` changed and the body does not carry the table header,
  a row for each changed file and a `Cause:` line. Text in HTML comments does not count.

### Consequences

- Good: eleven single-line mutants of the pipeline (changed defaults, `<` for `<=` and an off
  by one at a window edge, a swapped correction order, a changed normalization, a changed
  export rounding) each fail the suite; the pull request of #702 lists them.
- Good: a dependency update that moves a number fails its pull request, and the table records
  what moved.
- Bad / trade-off: a legitimate change now also rewrites golden files and needs a table and a
  cause; that is the cost Q6 accepts.
- Bad / trade-off: the `golden-table` workflow runs the checker from the pull request's own
  tree, so a pull request can change the checker. Review of the diff is the guard, and the
  checker is a material path.
- Bad / trade-off: the workflow is not a required check until the maintainer adds it to the
  ruleset.
- Bad / trade-off: the native HMM on the 4-molecule SMD fixture is poorly conditioned (two
  nearly equal states); it is kept because a change there is still a change, and the cross-OS
  measurement below covers it.

## More information

- Issue #702 (tracking #695); generator #701 (ADR-free, `tests/_synth.py`).
- Cross-OS measurement: CI run 43255520414 of pull request #838 (head `ee9ac80`, base
  `153823e`), locked base environment, golden files written on Linux. Linux matched every
  quantity bit for bit. The largest relative difference per kind of result:

  | Kind of result | Quantities | macOS | Windows |
  |---|---|---|---|
  | Closed-form arithmetic and reductions | molecule means, histogram, cross-correlation, FRET cloud, registration coordinates | 2.7e-13 | 1.4e-13 |
  | Native Gaussian HMM (EM to a 1e-6 relative log-likelihood stop) | means, sigmas, transition matrix, log-likelihood | 4.6e-15 | 9.3e-15 |
  | Dwell-time survival fit (least squares) | rates and their confidence intervals | 5.9e-10 | 2.4e-10 |
  | Integers, strings, state paths, TDP counts, export checksums | all | exact | exact |

  The cross-correlation differences come from lags whose values are near zero, where a
  difference of one unit in the last place is a larger relative change.
