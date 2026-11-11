<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0070 — An advisory check for swapped donor and acceptor halves

- **Status:** accepted (rule, thresholds and measured rates signed off by the maintainer on the
  implementing pull request)
- **Date:** 2026-11-11
- **Deciders:** bioedca
- **PRD anchor:** §7.1 (FR-EXTRACT), §7.11 (FR-BATCH), §11.2 (donor-side check rows)
- **Milestone:** M15

## Context and problem statement

`tether extract --donor-side {left,right}` tells the extractor which half of a split frame holds
the donor. A wrong value raises no error. Every trace is read from the wrong half, every apparent
FRET efficiency comes out as roughly `1 − E`, and nothing downstream notices: the histograms, the
corrections and the idealization all run on the inverted data. Until now the only defence was the
manual check in `docs/cli.md` and the extraction how-to, which asks the user to find one
acceptor-bleach trace by eye.

The traces carry the answer. Under donor excitation the two dyes bleach in a way that tells them
apart:

- **The acceptor bleaches first.** Energy transfer stops, so the acceptor signal steps **down** to
  background and the donor signal steps **up**, because the donor is no longer quenched. This is
  the step the per-molecule γ estimate is built on [Roy2008], [McCann2010], [Hellenkamp2018], and
  `tether.fret.gamma` reads the same step.
- **The donor bleaches first.** The acceptor loses its only excitation, so both signals step down
  to background together.

Only the first event has a direction. If the channel labelled *acceptor* shows the donor's
behaviour, rising when the other one drops, in a large majority of such events, the halves are
probably swapped.

Issue [#646](https://github.com/bioedca/tether/issues/646) asks for this check at the end of
extraction, advisory only, with its thresholds and its false-alarm rate on the repository's
fixtures stated. What is the rule, what are its thresholds, and how does it behave where it
cannot decide?

## Decision drivers

- **A false "swapped" warning is the expensive error.** A user who believes it inverts correct
  data, which is the exact harm the check exists to prevent. A missed swap leaves the user where
  they are today, with the manual check. The thresholds are therefore set for a very low false
  alarm and accept a lower detection rate on small movies.
- **Advisory, never blocking.** The check reads the traces and changes nothing. An exception
  inside it is recorded, not raised, so it cannot fail an extraction.
- **Silence when it cannot decide.** A sample with low FRET or no acceptor has few acceptor-first
  steps. It must say "not enough bleach events to check", not guess.
- **Reuse the validated detector.** Bleach frames come from the native single-step detector
  (ADR-0026), not from a second detector with its own failure modes.
- **Reproducible evidence.** Every number below comes from one committed script with a fixed seed.

## Considered options

1. **No check** — keep the manual check as the only defence. Rejected: the issue exists because
   that check is easy to skip.
2. **Compare the two channels' mean intensities.** Rejected: which channel is brighter depends on
   E, on γ and on the camera, so it carries no information about which dye is which.
3. **Vote on every first step, with no requirement on what follows.** This was the first
   prototype. On the kinSoftChallenge sets a transition into a low-acceptor state reads exactly
   like an acceptor bleach: `level2` gave 29 consistent and 35 swapped votes, and the false-alarm
   bound reached a few per cent. Rejected.
4. **Vote only on a complete bleach sequence (chosen).** As option 3, but the molecule votes only
   when the surviving channel also bleaches, at least `min_frames` before the trace ends. A FRET
   transition is not followed by both signals reaching background, so it casts no vote.

For option 4 the two verdict thresholds were swept (`min_events` ∈ {10, 15, 20, 30} ×
`majority` ∈ {0.7, 0.8, 0.9}). The worst false-alarm bound is the highest one-sided 95 % upper
bound over every fixture, simulated condition and movie size (25, 50, 100, 250 molecules) in the
evidence below; the detection columns give P(warning) when the halves really are swapped.

| `min_events` / `majority` | Worst false-alarm bound | Typical, 50 molecules | Typical, 100 | 281-mol fixture, 50 | Binding-like, 100 |
|---|---|---|---|---|---|
| 10 / 0.8 | 5.9 % (kinSoft `level2`, 250) | 100 % | 100 % | 99.4 % | 88.8 % |
| **15 / 0.8** | **0.13 % (kinSoft `level2`, 250)** | **96.7 %** | **100 %** | **99.4 %** | **88.8 %** |
| 20 / 0.7 | 0.91 % (binding-like short, 100) | 64.5 % | 100 % | 99.5 % | 99.9 % |
| 20 / 0.8 | 0.048 % (binding-like short, 100) | 64.5 % | 100 % | 98.9 % | 88.8 % |
| 20 / 0.9 | 0.001 % | 64.5 % | 100 % | 80.4 % | 18.7 % |
| 30 / 0.8 | 0.001 % | 0.7 % | 99.4 % | 38.6 % | 88.8 % |

The kinSoft `level2` bounds for 10 and 15 events are an artefact of a small pool, not of a
measured wrong vote: 150 molecules with zero swapped votes still leave a 95 % upper bound of about
2 % per molecule, and a low event threshold lets that bound reach a verdict. A larger pool would
lower them. They are listed because the rule here is to report the bound, not the point estimate.

## Decision outcome

Chosen option: **option 4 with `min_events` = 15 and `majority` = 0.8**. The first draft chose
20 events, the smallest count whose worst bound is below 0.1 % everywhere. A real movie changed
that (*A real movie extracted both ways*, below): Tether's own extraction gave about 10 votes per
100 molecules, not the fixture's 57, and with the halves swapped 17 of 17 votes pointed the right
way while 20 events stayed silent. 15 is the smallest count whose worst bound stays near 0.1 %
(0.13 %, a small-pool bound; 10 events reach 5.9 %), and it raises detection on a typical
50-molecule movie from 64.5 % to 96.7 %. 0.8 is the largest majority that keeps detection on the
real fixture near 99 % at 50 molecules: 0.9 buys a lower bound that is already negligible and
loses most of the binding-like detection; 30 events loses most small movies.

### The rule (`tether.imaging.side_check`)

1. **Per molecule.** `detect_photobleach` gives a first-bleach frame for each channel. Let the
   earlier one be the *first* step and the later one the *second*.
   - No step, or a first step within `min_frames` of frame 0: **none**.
   - The two steps within `min_frames` of each other: **together** (the donor-bleach pattern).
     Both labellings predict it, so it casts no vote. This also takes in acceptor-first molecules
     whose donor never bleaches in view: the detector puts the donor's step *up* at the same frame.
   - A second step within `min_frames` of the trace end, or none: **unclear**.
   - Otherwise, take the mean of up to `level_frames` frames on each side of the first step
     (stopping at the second step), and the per-frame noise as 1.4826 × MAD of first differences
     / √2 over the same frames. The molecule **votes** when the channel that bleached first drops by
     at least `step_z` standard errors **and** the other channel rises by at least `step_z`. The
     vote is **consistent** when the dropping channel is the one labelled acceptor and **swapped**
     when it is the one labelled donor. Anything else is **unclear**.
2. **Per movie.** With *n* votes and `need = ⌈majority × n⌉`:
   - *n* < `min_events`: **insufficient** ("not enough bleach events to check the donor side").
   - swapped votes ≥ `need`: **swapped**.
   - consistent votes ≥ `need`: **consistent**.
   - otherwise: **mixed**.
   - an exception inside the check: **not-run**, with the exception as the reason.
3. **The check is symmetric.** Exchanging the two channels exchanges consistent and swapped votes
   exactly; the measurement script asserts this on every pool rather than assuming it.

| Tunable | Default | Role |
|---|---|---|
| `SIDE_CHECK_MIN_EVENTS` | 15 | votes needed before any verdict |
| `SIDE_CHECK_MAJORITY` | 0.8 | share of the votes a verdict needs |
| `SIDE_CHECK_STEP_Z` | 3.0 | standard errors for the drop and for the rise |
| `SIDE_CHECK_LEVEL_FRAMES` | 10 | frames averaged on each side of the step |
| `SIDE_CHECK_MIN_FRAMES` | 5 | separation between the steps, and from either end of the trace |

The detector priors are the ADR-0026 defaults (`a = b = β = 1`, `μ = 1000`).

### Where it runs and what it records

- **`extract_movie`** runs the check on the extracted donor and acceptor intensities after
  registration and before the project is written. The result is returned as
  `ExtractionSummary.donor_side_check` and stored, with every threshold it used, as the
  `donor_side_check` key of the `/settings/extraction` `profile_json` attribute. That key is new
  JSON inside an existing attribute, so the HDF5 schema does not change. The check changes no
  stored trace, molecule field or calibration value; a test compares the stored project with the
  check replaced by a stub.
- **`tether extract`** prints the result. A swapped verdict is a warning on standard error that
  names the evidence and the remedy, for example *"41 of 44 bleach events are consistent with
  swapped donor and acceptor halves; extract this movie again with --donor-side right"*. With an
  imported `.tmap` the remedy names the map's channel order instead, because the map fixes the
  geometry. Every other verdict is one line on standard output. The exit code does not change.
- **`tether batch`** reads the stored result after each extraction, also on a resumed run that
  skips the extract stage, marks a flagged movie `halves-swapped?` in its summary line, and lists
  the flagged movies in a footer. Movies with an insufficient, mixed or consistent verdict add
  nothing to the summary. It never changes a movie's status.
- **The application** warning is not in this decision's scope. The maintainer moved it to
  [#350](https://github.com/bioedca/tether/issues/350) (extraction section), which now shows the
  stored result as an acceptance criterion.

### Evidence

Reproduce with `python scripts/measure_side_check.py` (base environment, seed 0, a pool of 2,000
molecules per simulated condition, about 25 s; `--json` writes the full table). The large-tier
fixtures need Git LFS.

**Fixtures, read with the stored labels and with the channels exchanged.**

| Fixture (SHA-256) | Molecules | Votes consistent / swapped | Together | Stored labels | Exchanged |
|---|---|---|---|---|---|
| `smd_2mol.hdf5` (`95439c4b…`) | 2 | 2 / 0 | 0 | insufficient | insufficient |
| `smd_4mol.hdf5` (`227930de…`) | 4 | 1 / 0 | 1 | insufficient | insufficient |
| `large/smd_281mol.hdf5` (`286130c4…`) | 281 | 150 / 10 | 72 | **consistent** | **swapped** |
| `large/kinsoft_sim.hdf5` `level1` (`c1293315…`) | 75 | 0 / 0 | 0 | insufficient | insufficient |
| `large/kinsoft_sim.hdf5` `level2` | 150 | 1 / 0 | 2 | insufficient | insufficient |
| `large/kinsoft_sim.hdf5` `level3` | 250 | 0 / 1 | 24 | insufficient | insufficient |

The 281-molecule fixture is the only one with real bleach sequences in number: real UCKOPSB
traces, 150 of 160 votes (94 %) consistent with the stored labels. The kinSoftChallenge sets
simulate kinetics without photobleaching in view, so the check correctly stays silent on them,
which is the behaviour option 3 failed.

**Movies of N molecules.** A movie's verdict depends only on how many of its molecules vote each
way, so for a population whose molecules vote consistent with probability `p_c` and swapped with
`p_s` the verdict distribution is exact: votes ~ Binomial(N, `p_c + p_s`), and given *v* votes the
swapped ones ~ Binomial(*v*, `p_s / (p_c + p_s)`). `p_c` and `p_s` come from the fixture or from
the simulated pool. The **false alarm** is P(swapped) with the stored labels and `p_s` at its
one-sided 95 % Clopper–Pearson upper bound, so a pool with no wrong vote still gives a non-zero,
conservative rate. **Detection** is P(swapped) with the labels exchanged.

| Source | Votes per 100 molecules | False alarm, worst N | Detection at N = 25 / 50 / 100 / 250 |
|---|---|---|---|
| 281-molecule fixture (resampled) | 56.9 | < 0.01 % | 45.4 % / 99.4 % / 99.98 % / 100 % |
| kinSoft `level2` (resampled) | 0.7 | **0.13 %** (N = 250) | insufficient at every N |
| kinSoft `level3` (resampled) | 0.4 | < 0.01 % | insufficient at every N |
| Typical (E 0.5, 10 % donor-only) | 41.6 | < 0.01 % | 5.0 % / 96.7 % / 100 % / 100 % |
| E 0.05 | 0.1 | < 0.01 % | insufficient at every N |
| E 0.1 | 1.4 | < 0.01 % | insufficient at every N |
| E 0.2 | 17.9 | < 0.01 % | < 0.01 % / 2.5 % / 81.1 % / 100 % |
| E 0.3 | 32.5 | < 0.01 % | 0.42 % / 69.4 % / 100 % / 100 % |
| E 0.7 | 43.0 | < 0.01 % | 6.6 % / 97.9 % / 100 % / 100 % |
| E 0.9 | 41.8 | < 0.01 % | 5.1 % / 96.9 % / 100 % / 100 % |
| Dim (I₀ 200 counts) | 39.0 | < 0.01 % | 2.7 % / 92.9 % / 100 % / 100 % |
| No acceptor (donor-only sample) | 0 | < 0.01 % | insufficient at every N |
| Half donor-only | 21.6 | < 0.01 % | < 0.01 % / 10.5 % / 96.3 % / 100 % |
| γ 0.5 | 27.5 | < 0.01 % | 0.07 % / 39.8 % / 99.9 % / 100 % |
| γ 2 | 43.3 | < 0.01 % | 7.0 % / 98.1 % / 100 % / 100 % |
| Dynamics E 0.15 ↔ 0.85 | 33.6 | < 0.01 % | 0.62 % / 75.4 % / 100 % / 100 % |
| Binding-like E 0 ↔ 0.9 | 55.5 | < 0.01 % | 32.4 % / 81.8 % / 88.8 % / 96.8 % |
| Short movie (300 frames) | 6.0 | < 0.01 % | < 0.01 % / < 0.01 % / 0.09 % / 53.8 % |
| Dynamics, no bleaching in view | 0.8 | < 0.01 % | insufficient at every N |
| Binding-like, short movie | 22.7 | **0.10 %** (N = 50) | < 0.01 % / 0.55 % / 1.3 % / 0.02 % |

Where detection is low the movie reads **insufficient** (too few votes), except in the two
binding-like rows, where the remainder reads **mixed**: an acceptor that leaves and returns
produces anticorrelated steps that are not bleaching, and the second-bleach requirement removes
most but not all of them (86 % and 56 % of their votes are consistent). The short binding-like
movie gives the worst false-alarm bound of any condition with wrong votes, 0.10 % at 50
molecules, and its verdict at 100 and 250 molecules is mostly *mixed*, which the check reports as
a prompt to look at one trace by eye. The overall worst bound, 0.13 %, is the kinSoft `level2`
small-pool bound described under the sweep.

**The simulations.** Each simulated molecule is background-subtracted, under donor excitation
only: before either bleach the donor reads `I₀(1 − E)` and the acceptor `γ I₀ E` plus a 0.07
leakage of the donor; after an acceptor-first bleach the donor reads `I₀` and the acceptor the
leakage only; after the donor bleaches both read background. Bleach frames are exponential
(donor mean 800, acceptor mean 500 frames) over 1,700 frames; I₀ = 600 counts; the per-frame
noise is a 28-count floor plus a shot-like term, fitted to the post-bleach scatter and the
pre-bleach scatter of the 281-molecule fixture. Every condition changes one of these from the
typical row. This is a measurement aid in `scripts/`, not a shipped simulator, so PRD N2 (no data
simulator in v1) is unaffected; the real fixtures remain the evidence the issue names.

**A real movie extracted both ways.** The maintainer ran the check on one real movie that is not
in the repository (UCKOPSB, 1,700 frames, native registration RMS 0.87 px), extracted with
`--donor-side left`, the correct side, and with `--donor-side right`. The counts come from
`classify_bleach_event` on the stored `donor_corrected` and `acceptor_corrected` traces:

| Extraction | Molecules | Votes consistent / swapped | Together | No vote | Verdict at 20 | Verdict at 15 |
|---|---|---|---|---|---|---|
| `left` (correct) | 183 | 0 / 0 | 46 | 137 | insufficient | insufficient |
| `right` (wrong) | 165 | 0 / 17 | 23 | 125 | insufficient | **swapped** |

Two things differ from the fixture. The vote yield is about 10 per 100 molecules, not 57: most
molecules bleach in view (the summed signal of 137 of the 183 ends below 20 % of its start), but
few show a donor rise of three standard errors. And the two extractions do not see the same
molecules. Colocalization is donor-anchored, so every spot on the half labelled donor becomes a
molecule. The likely result is that the correct side keeps the bright donors, mostly low-FRET and
donor-only molecules that rarely vote, and the wrong side keeps the bright acceptors, high-FRET
molecules that vote clearly. On this movie that asymmetry worked for the check: the correct side
collected no wrong vote and the swapped side collected only right ones. At this movie's yield a
165-molecule movie with swapped halves warns with probability about 26 % at 20 events and 73 % at
15 (binomial, with the observed rate as the point estimate).

### Consequences

- Good: a wrong `--donor-side` on a movie with enough acceptor-first bleach events is now caught
  at extraction and named in the batch summary, with the remedy in the message.
- Good: the result and its thresholds travel with the project, so a later reader can see what the
  check said and why.
- Bad / trade-off: the check is silent below about 20 % FRET, without an acceptor, on short
  movies, on most movies under about 35 molecules, and on real movies where few molecules show a
  clear donor rise (the real movie above gave 10 votes per 100 molecules). In those cases the
  manual check in the docs remains the only defence, and the docs say so.
- Bad / trade-off: a sample whose acceptor binds and unbinds can read *mixed*. That is a prompt to
  look, not a verdict, and it does not reach the batch summary.
- Neutral: no frozen oracle or tolerance changes. The detector and its priors are reused as they
  are.

## More information

**References.** Retrieved 2026-11-10.

- [Roy2008] Roy R, Hohng S, Ha T. "A practical guide to single-molecule FRET." *Nature Methods*
  5, 507–516 (2008). doi:[10.1038/nmeth.1208](https://doi.org/10.1038/nmeth.1208), PMID 18511918,
  PMC3769523.
- [McCann2010] McCann JJ, Choi UB, Zheng L, Weninger K, Bowen ME. "Optimizing methods to recover
  absolute FRET efficiency from immobilized single molecules." *Biophysical Journal* 99, 961–970
  (2010). doi:[10.1016/j.bpj.2010.04.063](https://doi.org/10.1016/j.bpj.2010.04.063),
  PMID 20682275, PMC2913196.
- [Hellenkamp2018] Hellenkamp B, et al. "Precision and accuracy of single-molecule FRET
  measurements — a multi-laboratory benchmark study." *Nature Methods* 15, 669–676 (2018).
  doi:[10.1038/s41592-018-0085-0](https://doi.org/10.1038/s41592-018-0085-0). It carries a
  published correction (doi:10.1038/s41592-018-0193-x, 2018-10-16) and no retraction.

**How they were found.** Consensus search, query *"single-molecule FRET acceptor photobleaching
donor intensity increase anticorrelated, donor photobleaching both channels to background"*;
PubMed article metadata and full text for PMIDs 18511918 and 20682275; a Scite reference check of
the three DOIs, term *"acceptor photobleaching donor intensity increase anticorrelated"*, which
also returned the editorial notices above. The physics is the textbook basis of γ from
photobleaching; the thresholds are this record's own measurement, not a literature value.

- Issue [#646](https://github.com/bioedca/tether/issues/646); the application half moved to
  [#350](https://github.com/bioedca/tether/issues/350) by the maintainer on 2026-11-10.
- `src/tether/imaging/side_check.py`, `scripts/measure_side_check.py`,
  `tests/test_donor_side_check.py`.
- [ADR-0026](0026-photobleach-detection-and-window-default.md) for the detector;
  [ADR-0028](0028-gamma-acceptor-bleach-step-estimator.md) for the γ estimator that reads the same
  acceptor-bleach step.
