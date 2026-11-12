<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0079 — The tMAVEN authors' RF2-ArfA data set is a reference oracle

- **Status:** accepted
- **Date:** 2026-11-12
- **Deciders:** bioedca
- **PRD anchor:** §7.4 (parity definition), §8 NFR-VALID and NFR-FIXTURES, §11.2 (the frozen tolerance), §12.6 (`sidecar/parity`)
- **Milestone:** M19
- **Issue:** [#708](https://github.com/bioedca/tether/issues/708)

## Context and problem statement

Every tMAVEN parity check Tether had compared Tether with fits that Tether's own sidecar made, on
Tether's own data. None of them used data that the tMAVEN authors analysed themselves. Zenodo record
[10.5281/zenodo.17246323](https://doi.org/10.5281/zenodo.17246323) (CC-BY-4.0) holds the tMAVEN
SMD files and the "vb Consensus HMM" models of Michael, Huang, Ray, Kinz-Thompson and Gonzalez,
the data behind [their 2025 paper](https://doi.org/10.1073/pnas.2509408122) (PNAS
122(46):e2509408122). The record also gives the tMAVEN settings they used. #708 asks for four
checks on it: import, idealization, kinetics and plots.

Three questions needed a decision: what to commit (the archive is 127.8 MB and the smallest SMD
file is 1.16 MB, larger than the 512 KiB plain-git gate); what to do where a result does not agree
with the frozen tolerance; and how to state the kinetics check, because Tether's dwell-time rates
are a different estimator from the HMM rates in the paper.

## Decision

1. **Commit one pair, download the rest.** The smallest model/SMD pair (RF2 core domain on
   ArfA-pre-incubated non-stop elongation complexes, `RF2_core_wtprensEC`) is committed in plain
   git. The model file is a verbatim copy (499,405 B). The SMD file is a **windowed extract**
   (313,247 B) made by `scripts/make_rf2_arfa_fixture.py`: each frame inside a molecule's analysis
   window `[pre_list, post_list)` is kept bit for bit, each frame outside it is set to `0.0`, and the
   authors' local path in `source_list` is replaced by the archive member name. tMAVEN and Tether
   fit and plot only the frames in the window, so a tMAVEN fit of the extract is equal to a fit of
   the original file (measured). The whole archive is a `download` entry of the reference-data
   manifest ([ADR-0073](0073-one-manifest-for-reference-data-sets.md)); the weekly
   `reference-data` job runs the checks on all 11 SMD files and 9 models, and checks that each
   committed file is what the manifest says it is.
2. **Import** (required matrix on the extract, weekly on the archive). `read_smd`, `write_smd`,
   `read_model`, the analysis-only `.tether` import and an `/idealization` entry give every array
   back. The one loss is the documented one: the `/traces` layers are float32. In 3 of the 11 SMD
   files some intensities are not float32 values; the largest relative rounding is 2^-24 (6.0e-8),
   the float32 limit.
3. **Idealization** (`sidecar / parity`, on the extract). tMAVEN `10f4230` fits a two-state
   consensus VB-HMM with the bleed-through correction (0.035) and **the priors stored in the
   authors' model**, seed 1 ([ADR-0075](0075-seeded-ebfret-parity-gate.md)). tMAVEN's own priors
   come from the data (`mu_prior` = percentiles), and on this file they are not the stored ones
   (`[-0.016, 0.040]` against `[0.0011, 0.9205]`), so they must be passed. The fit is compared with
   the authors' model under the frozen `schema/parity_tolerance.json` row. **That row does not
   change.** Where a metric is outside it, the test pins the measured difference and asserts that
   the row still fails it, so a later agreement is seen, not hidden.
4. **Kinetics** (required matrix and weekly). The check is that the rates Tether stores are the
   rates of the authors' rule, `k_ij = -ln(1 - p_ij) / τ` on the stored transition matrix (τ =
   100 ms), and that Table 1 of the paper follows from them with the paper's photobleaching
   correction (subtract the Api137-bound rate). Tether's dwell-based `pooled_exit_rates` is a
   different estimator (it drops the first and last dwell of each trace), and on these files it
   differs from the HMM rates (for example 1.42 against 0.91 /s for the core wt-nsEC complex);
   it is not used here.
5. **Plots** (`sidecar / parity`, on the extract). tMAVEN's `data_hist2d.get_data` at the authors'
   settings against Tether's A2 and A2b histograms.

## Evidence

Measured 2026-11-12 on tMAVEN `10f4230`, CPython 3.12.13, NumPy 1.26.4 (`sidecar/conda-lock.yml`).
The authors' files name no tMAVEN build; the SMD file and model are dated 2022-11-29. Seeds 1
and 2 give the same model, and a fit of the extract equals a fit of the original file.

| Metric (fresh fit vs authors' model) | Measured | Frozen row |
|---|---|---|
| state-count agreement | 1.0 | ≥ 0.9 |
| per-state \|ΔE\| | 7.2e-5 | ≤ 0.02 |
| Viterbi agreement | 0.99954 | ≥ 0.95 |
| relative ΔELBO | **0.0194** | ≤ 0.01 — **outside** |

The ELBO is the sum of four terms, which tMAVEN stores beside it in the last row of
`likelihood` (columns 1 to 4; column 0 is the sum). Three of them agree to 2.0e-5 relative.
The Gaussian-Wishart term does not (stored −4433.80, fresh −5074.02); tMAVEN adds it once per trace (204 times; its own comment calls this
"multiple-counting the priors"). The current formula on the stored posterior gives the stored
−4433.80 again, so the formula is unchanged; the fresh posterior differs a little (high-FRET
variance +0.21 %), and it is converged (a tighter convergence limit gives the same fit). The rates
agree to 4.3e-4 relative. Because the term is counted 204 times, a small change of the
posterior moves the ELBO by much more than it moves the states, the means or the rates. The test
pins relative ΔELBO ≤ 0.020 and the other three terms to 3e-5.

**Table 1.** 16 of its 21 numbers fall inside the stated SD (the SD of the posteriors). Five do
not: all four (Cy5)RF2-dIII rates of the wt-pre-nsEC complex (0.3705, 0.2755, 0.0593 and 0.1983
/s against 0.32 ± 0.02, 0.25 ± 0.01, 0.08 ± 0.02 and 0.18 ± 0.01) and the mut-nsEC dissociation
from the distal state (1.1710 against 1.11 ± 0.03; that model has four states, of which two are
zero-FRET states that the authors merge). All four wt-pre-nsEC numbers differ in the same file,
so the archived wt-pre-nsEC dIII model is probably not the one behind the table. These five are
pinned as measured differences in `TABLE_1_DIFFERENCES`.

**2-D histogram.** Start-synchronized, Tether's A2 counts equal tMAVEN's cell for cell (204
molecules; tMAVEN has one more time row, with no counts). Post-synchronized on the frames of each
window, both find the same 38 transitions in 29 molecules, and Tether lacks 2 of tMAVEN's 1608
counts: the last window frame of the two transitions whose next dwell runs to the end of the
window (#806). tMAVEN's app reads the whole model chain, whose out-of-window frames are state 0,
so it also counts 2 jumps at a window border (40 transitions in 30 molecules; #707).

## Consequences

- Tether now has a parity check against an analysis that it did not make. A tMAVEN, sidecar-lock
  or Tether change that moves any of these numbers fails `sidecar / parity` or the weekly job.
- The ELBO line is a known, explained failure of the frozen row on this data. It is not a reason
  to widen the row; a re-freeze needs its own ADR (ADR-0009).
- The five Table 1 differences are open questions about the record, not about Tether. They are
  reported in `docs/validation.md`; if a corrected record is published, the pins change in the
  pull request that updates the manifest checksum.
- The weekly job downloads 127.8 MB, once a month per cache key.

## Alternatives considered

- **Git-LFS for the original SMD file.** The required matrix pulls no LFS object, so the import
  and plot checks would not run there. Rejected.
- **Float32 recompression** of the SMD file reached only 822 KB, still over the gate, and changes
  values. Rejected.
- **Let tMAVEN derive its own priors.** It gives a different prior mean for the bound state and so
  compares a different model. Rejected.
- **Compare Tether's dwell-time rates with Table 1.** That tests a different estimator against the
  HMM rates and would fail for a reason unrelated to the data. Rejected; the HMM rates are the
  quantity the paper reports.
