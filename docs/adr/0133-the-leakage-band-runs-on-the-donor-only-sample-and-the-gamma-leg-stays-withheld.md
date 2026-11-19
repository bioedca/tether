<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0133 — The leakage-α band runs on the donor-only sample; the γ-vs-Deep-LASI leg stays withheld

- **Status:** accepted
- **Date:** 2026-11-18
- **Deciders:** bioedca
- **PRD anchor:** §7.2, §9.1 item 8, §9.3 (Corrections), §9.5 oracle (e), §9.6, §11.2 (leakage-α validation band, γ-agreement tolerance)
- **Milestone:** M3 (criteria on record)
- **Issue:** [#546](https://github.com/bioedca/tether/issues/546)

## Context and problem statement

Two M3 legs of oracle (e) were withheld for want of data. [ADR-0027](0027-leakage-alpha-tail-estimator.md)
withheld the **conjunctive leakage-α band** (Tether's donor-only α against its own post-acceptor-bleach
tail α, matched gates, same FRET dataset: relative-median difference ≤ 20 % **and** both medians in
0.05–0.2), because the traces of the lab's Cy3-only donor-only acquisition sat in MCOS objects no code
decoded. [ADR-0028](0028-gamma-acceptor-bleach-step-estimator.md) withheld **γ agreement with the
Deep-LASI median** (±10 % on a shared frame set from Deep-LASI's own per-frame classification, plus a
looser end-to-end CI-overlap check), because the vendored export carries no per-frame classification.

Since then PRD §2.2 rule 2 (#705) lets a test read an old format through a small reader under
`tests/`, `tests/_deeplasi_public.py` (#716) reads Deep-LASI `TRACEdata` traces with the product MCOS
decoder (which reads metadata version 3 since #874), and the workstation runner (#698, ADR-0071)
runs checks on private data. #546 asks whether either leg can now be run, runs it if so, and records
why not if not.

## What the data hold (measured 2026-11-18)

Read-only probes on the maintainer's workstation (a fresh clone of `main` at `0e62613`, the runner's
locked environment) and, for the public file, in a cloud container. Numbers only; no file name.

**The donor-only sample** (`donly-01`, the Cy3-only calibration acquisition ADR-0027 describes;
18 624 213 B, SHA-256 `0e966432…`). The test-only reader reads it unchanged: 91 molecules × 1700
frames at 0.103 s, one green excitation. Tether's photobleach detector finds the donor bleach inside
the trace in 83 of 91. With every frame before the donor bleach as the window, the tail estimator's
gates qualify 89 of 91 (one above the ceiling, one with a non-positive donor), median α **0.1200**,
per-trace 0.054–0.260. Its condition label names the construct, replicate and concentration of the
FRET series `lab-03` to `lab-08`, recorded 20 and 22 days later; the setup fields every file stores
(channel split, photon-conversion factors, frame time, frame transfer) are the same in all of them.

**The Deep-LASI results.** Neither the ten saved Deep-LASI states of the workstation (`lab-01` to
`lab-08`, `lab-10`, `lab-12`), nor the donor-only state, nor the public Figure 3 state of Zenodo
record 7561162 (6100 molecules) holds Deep-LASI's own per-molecule factors: every `TRACEdata`
`Gamma`, `Beta` and `Alpha` is NaN for every molecule, and `DefaultAlpha`, `DefaultBeta` and
`DefaultGamma` are 0. No state holds the per-frame classification: `NeuralNetwork.Probabilities` is
empty, and `TracesCategories` is an empty array. `BleachFrame` is NaN for every molecule of the lab
states and finite for 142 of the 6100 public molecules.

**Where Deep-LASI's γ comes from** (the pinned clone at `d2d348c`, published code).
`functions/deeplearning/deep_autocorrect_2color.m` does not use `BleachFrame`: it takes the
per-frame class of the network (`[~, cat_idx] = max(T.NeuralNetwork.Probabilities{…}, [], 2)`, line
107), the spFRET frames as every frame of class *static* or *dynamic* and the acceptor-bleached
frames as every frame of class *acceptor bleached* (lines 108–109), anywhere in the trace, and divides
the acceptor drop by the donor rise between the two sets (line 130), with the donor scaled by
`(1 + ct)` (lines 118–119). `BleachFrame` itself is set in `predict_trace_categories.m` (lines
156–168) as the frame where the probability of the *donor-bleached* class peaks; at this commit the
loop reads class 1 for every column, so the donor and the acceptor channel both receive the donor's
frame and no acceptor-bleach frame is stored.

The `.mat` exports of #715 carry a per-molecule `g` and `b`, but ADR-0028 found `g` dominated by
the population-median fallback, and an export carries no classification either.

## Decision drivers

- PRD §9.1 item 8 and §9.4: a leg whose data cannot do the job is withheld; nothing is stubbed, and
  no tolerance is widened to make a leg pass.
- The band compares two estimators of one per-condition factor (PRD §7.2), so the FRET side must be
  the condition the donor-only sample calibrates.
- The γ oracle is *estimator-isolated*: both tools on Deep-LASI's own frame selection, so that only
  the estimator differs (ADR-0028 option B was rejected for substituting Tether's frames).

## Considered options

For the leakage band, which FRET data the donor-only α is compared with:

1. **The pooled molecules of the series `lab-03` to `lab-08`**, one tail-α median over all of them.
2. **Each acquisition of that series on its own**, every one required to pass.
3. **One acquisition**, `lab-03`, named before its number is measured.

For the γ leg:

4. **Withhold it**, and name the data that would end the withholding.
5. **Use `BleachFrame` as the shared step.**
6. **Use Tether's own bleach frames with the export's `g`.**

## Decision outcome

**Leakage band: option 1, chosen by the maintainer on 2026-11-18.** α is one factor per condition
(PRD §7.2), and the donor-only label names the whole series, so the series is the dataset whose
leakage it calibrates. Options 2 and 3 test acquisitions separately against one sample. The tail
α of single acquisitions from #715 differ (0.097, 0.103, 0.098 and 0.121 for `lab-03` to `lab-06`,
rounded; 21 %, 15 %, 20 % and 1 % from 0.120), so choosing one after seeing it would choose the
result.

- **The donor-only estimator** is the tail estimator of ADR-0027 with the window
  `[0, donor_pb)`: in a sample with no acceptor every frame before the donor bleach is a donor-only
  frame. It keeps the ratio of window means, `min_window_frames`, `LEAKAGE_CEILING` and
  `min_qualifying_traces` unchanged, which is what *matched gates* means. The tail α of the series
  uses Tether's detector and ADR-0027 unchanged. Both read the saved Deep-LASI states with the same
  test-only reader, so the two sides share one background subtraction (`TracesCorrected`).
- **The check** is `tests/test_leakage_alpha_band.py`, a `workstation` test (ADR-0071). It verifies
  the donor-only file and every series state against their recorded SHA-256, records the two
  medians, their counts and the relative difference under the neutral ids, and asserts the band of
  `tests/_leakage_band.py`. The band's two limits are the §11.2 row, and a required-matrix test
  reads that row so the code and the specification cannot drift. The band is conjunctive: a test
  shows two plausible medians that disagree failing.
- **Test-only.** The donor-only route stays under `tests/`: storing a per-condition α with its
  donor-only source is the α record of [#926](https://github.com/bioedca/tether/issues/926), and #546
  adds no product reader of `.tdat` traces.
- A failing run is a finding reported on the issue. It does not change a limit (PRD §9.4).

**The first run passed** (2026-11-19, the workstation's locked environment, branch head `aa6d8c7`,
every file verified against its recorded SHA-256). The donor-only α of `donly-01` is **0.1200**
(89 of 91 molecules qualifying); the tail α of the pooled series is **0.1012** (260 of its 971
molecules qualifying). The relative difference is **17.0 %**, within the 20 % limit, and both
medians lie in 0.05–0.2, so the conjunctive band passes. By acquisition the tail α is 0.0997
(`lab-03`, 70 of 247 qualifying), 0.0986 (`lab-04`, 77 of 197), 0.1014 (`lab-05`, 66 of 233), 0.1209
(`lab-06`, 14 of 87) and 0.1127 (`lab-07`, 26 of 137); `lab-08` withholds its own median (7 of 70
qualifying, under `min_qualifying_traces`), and its qualifying molecules still count in the pool.
These per-acquisition values read the saved states; the #715 values above read the `.mat` exports.

**γ leg: option 4.** No available file holds the two things the estimator-isolated oracle needs,
Deep-LASI's per-molecule γ and its per-frame classification, and the looser CI-overlap check needs
the same γ. Option 5 fails on its own terms: `BleachFrame` is empty in every lab state, and where it
exists it is the donor's frame for both channels, not the acceptor step. Option 6 is #715's
corrected-FRET comparison (ADR-0115), which uses Tether's frame selection and so is not
estimator-isolated; it stays as it is, with no limit. The leg stays *not yet met* in §9.3 and §9.6.
It can be run once a Deep-LASI state is saved **after** Deep-LASI has classified the traces and run
its automatic correction (the per-frame `NeuralNetwork.Probabilities` and the per-molecule `Gamma`
filled in): either the maintainer saves one from Deep-LASI's window, or the pinned scripted run of
ADR-0074 gains a classify-and-correct task. The shared frame set is then Deep-LASI's own classes, not
`BleachFrame`. [#1244](https://github.com/bioedca/tether/issues/1244) carries that work.

### Consequences

- Good: oracle (e)'s α leg is evidenced on real data (a donor-only sample and its FRET series)
  rather than on synthesized known-α traces alone, and its first run passed.
- Good: the withholding of the γ leg now names its cause in the data and the data that would end it.
- Bad: the band runs only on the workstation, weekly, like the other private-data checks; the
  required matrix checks its arithmetic, not its data.
- Bad: the donor-only file must be placed in the private data folder and mapped as `donly-01`
  (role `donor_only`) before the check can run.
- Neutral: no product code, schema, lock or tunable changes.

## More information

- Builds on [ADR-0027](0027-leakage-alpha-tail-estimator.md) (the tail estimator),
  [ADR-0028](0028-gamma-acceptor-bleach-step-estimator.md) (the γ convention and the deferred oracle),
  [ADR-0071](0071-private-data-checks-run-from-a-workstation-task.md) (the workstation runner),
  [ADR-0074](0074-matlab-reference-outputs-come-from-pinned-runs.md) (pinned Deep-LASI runs) and
  [ADR-0115](0115-deep-lasi-acquisitions-in-oracle-a-and-how-a-failure-is-handled.md) (the #715 checks).
- The probe notes, with the full numbers, are on [#546](https://github.com/bioedca/tether/issues/546).
- Number 0133 was taken as one above the highest record on any branch (0132), because
  `claim.py reserve-adr` cannot create its ref from a cloud container.
