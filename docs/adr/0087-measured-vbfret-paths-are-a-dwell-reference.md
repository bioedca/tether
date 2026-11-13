<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0087 — Measured traces with a stored vbFRET path are a dwell and transition reference

- **Status:** accepted
- **Date:** 2026-11-13
- **Deciders:** bioedca (issue #797 approved 2026-11-11; the live method chosen on the #797
  thread 2026-11-13: tMAVEN's own per-trace vbHMM in a test-only sidecar script)
- **PRD anchor:** §2.2 ("What the tests can use", rule 2), §8 (NFR-VALID (b), NFR-FIXTURES),
  §7.7 (dwell times and the TDP)
- **Milestone:** M19

## Context and problem statement

`docs/validation.md` said that oracle (b) "has no per-trace vbFRET measurement". The idealization
parity of oracle (b) compares Tether with standalone tMAVEN, and the dwell and TDP tests compare
Tether with ports of tMAVEN's routines. Nothing compared either with the original **vbFRET**
program, and nothing checked Tether's dwell and transition code on measured paths that another
group analysed.

The survey of #721 found two CC-BY-4.0 records by Nicholas Marzano whose measured two-colour
traces carry the per-trace vbFRET_nov12 idealization the authors used (PPR-RNA, Zenodo 11323228;
Hsp70/Hsp90, Zenodo 17118152), and the authors' MIT analysis code `nmarzano/smFRET_module`
v1.0.3 (Zenodo 16791519) that computes dwell times and transitions from them.

Which part of that becomes a fixture, what is the expected output, how do the two programs'
definitions meet, and what does the live comparison with vbFRET assert?

## Decision drivers

- **Original bytes.** PRD §2.2 rule 2: a test reads an old format through a reader under
  `tests/` and starts from the original bytes, not a converted copy.
- **The required matrix.** The deterministic test runs on three operating systems, which do not
  pull Git LFS, so each fixture is a plain-git file of at most 512 KiB (the pre-commit gate and
  `tests/test_fixtures.py`).
- **A reference is never re-derived by the code it checks.** The expected tables come from the
  authors' program, not from Tether.
- **No frozen tolerance without the maintainer.** #797 says to freeze one only after he accepts it.

## Decision

1. **Fixtures.** One whole condition folder of each record, its `.dat` members copied byte for
   byte into a `.tar.xz` (normalized tar headers): PPR `Figure_2/Validation_data/RNAanticognate`
   (114 traces, 482,520 B) and chaperone `Fig1_FigS1_FigS2/single-molecule/KJG/KJG_231027`
   (68 traces, 465,640 B). The folder #797 names first for the chaperone set, `KJE/KJE_231212`,
   is 925 kB even as `.tar.xz`, so the issue's "or smaller folders" applies and the whole smaller
   folder is used, not a cut of the larger one. `scripts/make_marzano_fixture.py fixtures` makes
   both from the checksummed archives; `tests/_marzano_dat.py` reads them.
2. **Expected tables.** `scripts/make_marzano_fixture.py tables` runs smFRET_module v1.0.3 once
   on the fixtures, with its own functions in the order of its `combine_technical_repeats` and
   `master_tdp_cleanup_func`: `remove_outliers(df, "hist")`, `calculate_dwell_time`,
   `generate_transitions`, `remove_outliers_tdp`. One step is ours: its `file_reader` splits paths
   on a backslash, so the script lists the members and builds the same data frame. The four CSV
   tables and a `smfret_module_run.json` (tool, version, archive SHA-256, Python and pandas versions,
   input and output SHA-256) are under `tests/fixtures/reference/marzano/`.
3. **Exact agreement through four stated definitions.** `tests/test_marzano_vbfret.py` puts every
   stored path in a `.tether` store (one state for each distinct level) and requires Tether's
   `population_dwell_times` and `population_transition_density` to give smFRET_module's state
   values, durations in frames and order. The definitions that differ are tested through, not
   around: (a) the last run is right-censored and Tether drops it, while smFRET_module's dwell
   table keeps it and its transition table does not, so the transition table is Tether's
   `include_first=True` set; (b) Tether drops the first run by default, as smFRET_module's own
   `cleanup_dwell(first_dwell="delete")` does; (c) a frame with raw FRET outside (-0.5, 1.5) is
   dropped by smFRET_module and is `NO_STATE` in the store, so dwells agree, but Tether's TDP
   counts no transition across that gap (2 PPR transitions); (d) a molecule with one run gives
   nothing in either program.
4. **Live part, advisory.** Tether's sidecar runner has no per-trace model type, so the
   maintainer chose a test-only sidecar script over a product change.
   `tests/_marzano_vbhmm_child.py` calls tMAVEN's own per-trace step,
   `hmm_vb.tracelevel_vbhmm_modelselection` (1 to 5 states for each trace, the highest evidence
   kept, as vbFRET does), with the paper settings tMAVEN has: at most 5 states and prior
   β = 0.5. vbFRET's μ = 1.5 has no tMAVEN counterpart. The seed is fixed (797).
   `tests/test_marzano_vbfret_sidecar.py` measures the number of occupied states, the state means
   and the Viterbi agreement against the stored path and asserts only that they are defined.
   `docs/validation.md` records the values. **No tolerance is frozen.**

## Consequences

- Tether's dwell and transition extraction now has an exact, external reference on measured
  data, in the required matrix. A change of one frame in the dwell rule fails 6 of the 15 cases.
- The live measurement shows a real difference: tMAVEN's per-trace fit selects more states than
  vbFRET_nov12 (1 state in 8 of 114 PPR traces against vbFRET's 86 of 114), while the levels of
  traces with the same state count agree to about 0.01 FRET. This is recorded, not gated. A
  tolerance, or a decision that the state-number difference is expected, is the maintainer's.
- The fixtures and tables are CC-BY-4.0 (REUSE override); NOTICE carries the data attribution
  and smFRET_module's MIT notice. smFRET_module is not vendored.
- The two archives are `download` entries of the manifest (ADR-0073), so the weekly
  `reference-data` job can re-verify them against Zenodo.

## Considered options

- **The issue's `run_vbfret(..., model_type="vbfret")`.** Not available: no such model type
  exists, and the stand-alone per-trace `run_vbhmm` methods are commented out in the pinned
  tMAVEN. Rejected by the maintainer's choice in favour of option 4 above.
- **Tether's `vbgmm_vbhmm` model type.** Its stored paths are composite states, not each trace's
  own levels, so the comparison would be weaker.
- **Add a per-trace model type to the sidecar runner.** Product code, larger, and it overlaps
  #545.
- **The first 50 traces of `KJE_231212`.** Fits the size limit, but a cut by count is a rule of
  ours; a whole folder is the authors' own unit.
