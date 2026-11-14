<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0098 — The PySMACKS tutorial labels are the reference of the deep classifier

- **Status:** accepted
- **Date:** 2026-11-14
- **Deciders:** bioedca
- **PRD anchor:** §7.5 (quality ranking), §9 M8 (the deep classifier), §9.5 (the validation suite)
- **Milestone:** M19

## Context and problem statement

The deep-learning add-on is a classifier that the user trains (a 1-D CNN and a bidirectional
LSTM, accept or reject; ADR-0047). Tether supplies no trained model, so no reference model exists.
Before #759 the deep tier trained very small models on synthetic arrays and checked that the loss
fell; no test had a known answer on real labelled traces, and no number was frozen. A change of the
loss, the model or the data preparation could change every score the classifier gives and still
pass. Which public labelled set is the reference, which metric and floor does the default model
have to reach on it, and how much may its numbers move from one run to the next?

## Decision drivers

- The maintainer interview of 2026-11-11 (card T1): self-drift (A), an accuracy floor on public
  labelled traces (B), a comparison with the quality ranker (C), and a failed nightly run opens an
  issue (F).
- The deep env has torch, NumPy, SciPy and h5py; it has no scikit-image and no tifffile, so the
  inputs are trace arrays, not movies.
- The nightly job stays below 20 minutes on a GitHub runner.
- A limit or a floor is measured, never chosen, and it must fail a model that predicts the majority
  class.
- The add-on stays out of the validated core (docs/validation.md): this is a regression check, not
  a validation of the classifier's output on other data.

## Considered options

- **Synthetic traces with a planted answer.** Rejected for the floor: the existing smoke tests
  already train on synthetic classes; the issue asks for real, human-labelled traces.
- **The DeepFRET or Deep-LASI training sets.** Not chosen: neither is in the reference-data manifest
  (ADR-0073), and their labels follow a six-way taxonomy that Tether's binary accept/reject
  classifier cannot use yet (ADR-0023).
- **The PySMACKS tutorial traces (chosen).** Already in the manifest since #720 (download tier,
  28 MB, CC0-1.0, SHA-256 pinned) and read by the test-only `_openfret` reader. A person labelled
  frame ranges of 123 of the 1,136 traces.

For the metric:

- **Accuracy at a threshold of 0.5.** Rejected: 97 of the 149 ranges are reject, so a model that
  says reject to all gets 0.65.
- **Balanced accuracy at 0.5.** Reported, not the floor: it moves in steps of 1/16 or 1/29, and it
  depends on a calibration the product never uses, because Tether only ranks with the score and
  never drops a trace by it (PRD §7.5).
- **Area under the ROC curve (chosen).** It measures the ranking itself, which is how the product
  uses the score, it needs no threshold, and a constant score gets exactly 0.5.

## Decision outcome

**The samples.** One labelled range `[indmin, indmax)` of one trace is one sample: its
background-subtracted donor and acceptor under donor excitation (rule 1 of PRD §2.2: no ALEX
frames). `FRET` is accept; `DO` and `AO` are reject. 123 traces carry 149 ranges, 52 of them
accept. The model reads the leading 500 frames of each range (the default window) at the default
normalization.

**The split.** By trace, never by range: 26 traces carry a `FRET` range and then a `DO` or `AO`
range of the same molecule, and a split by range would put one molecule on both sides.
`tether.ml.deep.train_val_split` draws 30 % of the traces, stratified by the combination of labels
each carries, with seed 759. `schema/deep_classifier_reference.json` stores the 37 test traces as a
list, and a test checks that the rule still gives that list. Train part: 104 ranges (36 accept);
test part: 45 ranges (16 accept).

**The training.** The default hyperparameters of `train_classifier` (PRD §11.2) and training seed
0, on CPU. One training took 39 to 85 seconds on the ten GitHub runners of the measurement.

**Self-drift.** `tests/golden/deep/pysmacks_classifier.json` holds the loss of each of the 20
epochs, the score of each of the 45 test ranges, the three metrics, the range counts and two sums
of the train tensor (they move when the data preparation changes even if no score does). The test
compares every float with a relative limit of `self_drift.rtol` = 5e-5. It was measured: ten runs
of the measurement job, each on its own GitHub runner, trained with seed 0. The runners had four
CPU types (AMD EPYC 7763 in five runs, AMD EPYC 9V74 in three, two Intel Xeon types in one run
each). Runs on the same CPU type, and the two Intel runs, gave the same bits. The largest relative
difference between two runs was 4.6e-6, in the score of a test range, between an Intel run and an
EPYC 7763 run; in the loss it was 5.6e-7. The limit is ten times the largest difference, rounded
up to one significant digit. The golden file holds run 2 (EPYC 7763, the most frequent type). A deliberate change
runs `scripts/update_deep_golden.py` in the deep env and pastes its old/new table with a `Cause:`
line; the `golden-table` workflow checks the pull request body as it does for ADR-0080.

**The floor.** Ten training seeds (0 to 9) on the same split gave ROC AUC values of
0.9849, 0.9741, 0.9806, 0.9892, 0.9914, 0.9892, 0.9871, 0.9978, 0.9892 and 0.9914 (mean
0.9875, sample standard deviation 0.0065). The floor is the lowest of the ten minus three standard
deviations, rounded down to two decimals: 0.9741 - 0.0195 = 0.9546, so 0.95. A model that predicts the majority class
gives a constant score and an area of 0.5, and a test shows that it fails the floor.

**The ranker.** `deep / ranker` trains the gradient-boosting quality ranker (`tether.ml.gbranker`,
default hyperparameters) on the same train part, with the trace features of the same leading 500
frames, and writes both models' ROC AUC, balanced accuracy and precision@k (k = 16, the number of
accept ranges in the test part) to the job summary. It runs in the base env because the ranker
needs scikit-learn, which the isolated deep stack does not carry (ADR-0047). No limit.

**The failure report.** The `report` job of `deep.yml` watches both jobs and opens or updates one
issue (`ci(scheduled): deep failed on main`) when either fails (#697).

### Consequences

- Good: a change of the loss, the model or the data preparation that moves a score beyond the
  limit fails the nightly job and shows on a pull request that touches the deep paths; the
  pull request of #759 showed it with deliberate changes.
- Good: the floor fails a model that predicts the majority class or ranks the reject ranges first.
- Good: the nightly job stays well below 20 minutes (1 minute 43 seconds for the whole `deep` workflow on pull request #1026: `deep / smoke` 72 s with a cold data cache, `deep / ranker` 26 s).
- Neutral: the deep job now downloads 28 MB from Zenodo when its cache is cold; a failed download
  fails the job, and a skip for want of the file fails its skip budget, so it is never silent.
- Bad: the task is easy. A donor-only or an acceptor-only range is far from a `FRET` range in its
  intensities, so the floor says that the model learns this one distinction, not that it sorts good
  from bad `FRET` traces. The workstation checks of #760 are the place for harder sets.
- Bad: the default training is not smooth on this set: on all ten seeds the loss climbs back to
  0.34 to 0.55 at some epoch after the tenth before it falls again. The final scores still agree
  (the spread above); the classifier itself is out of scope here.
- Bad: the ROC area saturates near 1 on an easy task, so it tells little about small differences
  between good models. Balanced accuracy and precision@k are in the job summary beside it.

## Provenance

- Data: Molcrette B, Schmid S. Dataset for the PySMACKS tutorial (v1.0.0). Zenodo (2026).
  doi:10.5281/zenodo.21793247. File `PySMACKS_tutorial_traces_dataset.json.zip`, 28,009,862 bytes,
  SHA-256 `2c19091c55326b1eadda42b0e99c8a8c6123f92f9ecccafae176db7fbcb3da59`, CC0-1.0 (manifest
  entry `pysmacks-tutorial-traces`, #720, whose citation the maintainer signed off on 2026-11-13).
- Environment: `deep/conda-lock.yml` (torch 2.12.1, NumPy 2.1.3) on `ubuntu-latest`; the ranker
  in the base lock.
- Measurement: the temporary `deep-measure` workflow of pull request #1026, run 43609708676, at commit
  `fdfbcb7928a9c70a51a55136e8a1f28996513001`; its numbers are in `schema/deep_classifier_reference.json`.
