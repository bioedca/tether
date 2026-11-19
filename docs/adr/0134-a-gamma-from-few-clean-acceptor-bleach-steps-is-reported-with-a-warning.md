<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0134 — A γ from few clean acceptor-bleach steps is reported with a warning

- **Status:** accepted; adds to [ADR-0028](0028-gamma-acceptor-bleach-step-estimator.md) and [ADR-0130](0130-the-gamma-levels-are-averaged-over-20-frames-each-side.md), whose decisions govern unchanged
- **Date:** 2026-11-19
- **Deciders:** bioedca
- **PRD anchor:** §7.2 (γ), §11.2 (the γ clean-step warning fraction row), Appendix B.2 step 4
- **Milestone:** M19

## Context and problem statement

Tether's γ (ADR-0028, ADR-0130) is `ΔI_A / ΔI_D` across each molecule's acceptor-bleach step,
over 20 frames each side. On a clean step the acceptor drops and the donor rises. A molecule's
γ is kept only inside `(0, 5]`, and the dataset γ is the median of the kept values.

That gate reads a sign, not a shape. Where the donor does not rise on most molecules, `ΔI_D` is
noise around zero, and about half of the steps still come out with a positive ratio by chance.
The gate keeps the ones whose noise came out at `ΔI_A / 5` or more, so the median can follow
the width of the noise rather than the dyes, and Tether reported that γ with no sign that most
of its steps did not have the expected shape (#1232).

\#1233 made the workstation's Deep-LASI corrected-FRET check (#715, ADR-0115) record the
evidence on every scheduled run: the reasons per molecule, `n_gamma_step_clean` (donor rose
**and** corrected acceptor dropped, among the molecules past both segment gates), the raw-ratio
percentiles before the gates and the median step changes. The scheduled run of 2026-11-19 on
`main` at `1acd4f0915c7481576dc4d2cf5a21bac0b60a5f0` (result `20261119T090002Z-1acd4f0915c7`,
`on_main` true; the corrected-FRET check passed on all 13 ids) recorded, at the default 20-frame
windows. *Measured* is `ok` + `degenerate_donor` + `out_of_range`; neutral ids only:

| id | measured | clean | clean fraction | raw-ratio median | median `ΔI_D` | γ Tether | Deep-LASI `g(1 + b)` |
|---|---|---|---|---|---|---|---|
| `lab-03` | 78 | 55 | 0.705 | 0.921 | 72.8 | 1.147 | 1.068 |
| `lab-04` | 88 | 68 | 0.773 | 0.858 | 207.8 | 0.942 | 0.788 |
| `lab-05` | 83 | 58 | 0.699 | 1.009 | 69.1 | 1.210 | 1.109 |
| `lab-01` | 45 | 20 | 0.444 | −0.035 | −12.7 | 1.873 | 0.620 |
| `lab-02` | 107 | 22 | 0.206 | −0.061 | −314.9 | 0.701 | 0.253 |
| `lab-06` | 24 | 14 | 0.583 | 1.109 | 5.7 | 2.274 | 0.834 |
| `lab-07` | 39 | 20 | 0.513 | 1.228 | 2.5 | 1.767 | — |
| `lab-08` | 27 | 14 | 0.519 | 1.267 | 7.7 | withheld | 1.546 |
| `dlexp-01` | 8 | 1 | 0.125 | −0.119 | −284.4 | withheld | 1.305 |
| `dlexp-02` | 12 | 2 | 0.167 | −0.111 | −109.6 | withheld | 0.019 |

`lab-10`, `lab-12` and `lab-13` withhold α, so no step was measured. `lab-07` has no Deep-LASI
factors to compare with.

On `lab-03` to `lab-05` Tether's γ is 1.07 to 1.20 times Deep-LASI's, and 70 to 77 % of the
measured steps are clean. On `lab-01`, `lab-02` and `lab-06` it is 2.7 to 3.0 times Deep-LASI's,
and 21 to 58 % are clean. Where the corrected acceptor drops and the donor change is only noise
with an equal chance of either sign, the expected clean fraction is about one half, which a synthetic dataset of the same
construction reproduces (46 of 100, `tests/test_fret_gamma_clean_steps.py`). Were the acceptor
change also noise, independent of the donor's and equally likely either sign, it would be about
one quarter; leakage correction couples the two changes, so that case is a rough guide only. A measured step follows a detected acceptor
bleach, so its acceptor usually drops and the one-half case is the closer one, but the gates do
not guarantee that the leakage-corrected acceptor drops: where it does not, the step cannot be
clean, so the clean fraction falls further and the warning fires all the more.

The two groups are named by that ratio, not by a tolerance. Under the ±10 % γ tolerance of
PRD §11.2 only `lab-03` (1.07) and `lab-05` (1.09) agree, and `lab-04` (1.20) does not. In this
record, *near* Deep-LASI means within 1.2 times its γ, and *far* means 2.7 times or more.

## Decision drivers

- The user should be able to see when a reported γ rests mostly on steps without the expected
  shape, from the summary they already read, without running a diagnostic.
- A warning must change no γ: the known-γ recovery tests and their limits (ADR-0028, #728)
  pass unchanged, and nothing frozen is loosened.
- The count must mean the same thing in the product as in the workstation record, so the
  fraction chosen from those records carries over.
- A tunable stays registered (PRD §11.2), and a stored stamp stays additive (PRD §5).

## Considered options

- **A — Warn only, below a registered clean-step fraction.** Count the clean steps, stamp the
  count, and say in the correction summary when an applied γ rests on fewer than the fraction.
  γ is unchanged.
- **B — Withhold γ below the fraction.** Treat such a dataset like one with too few qualifying
  molecules: no γ, apparent E. Not chosen: it would remove a factor from three of the
  six acquisitions that have one on the measured set (four of seven with `lab-07`), on one run of
  one laboratory's data. The issue left the choice to the maintainer, who chose A on 2026-11-19.
- **C — Gate each molecule on its own shape.** Already the case for `ΔI_D ≤ 0`
  (`degenerate-donor`); a stricter per-molecule gate, such as a donor rise of several standard
  errors, would change which molecules give a γ, and so every γ. Out of scope (#1232 changes no
  γ formula or level window, ADR-0130).
- **D — No count, leave it to the workstation records.** Rejected: the records are on the
  maintainer's workstation, not in the user's project.

## Decision outcome

Chosen option: **A**, warn only, chosen by the maintainer on 2026-11-19 over option B.

- **The count.** `tether.fret.gamma.TraceGamma` carries `delta_donor` (`ΔI_D`, the donor's rise)
  and `delta_acceptor` (`ΔI_A`, the leakage-corrected acceptor's drop) for every molecule that
  passed both segment gates, measured over the estimator's own windows before the sign and
  ceiling gates, and `NaN` otherwise. `step_measured` and `step_clean` read them, and
  `GammaEstimate.n_step_evaluated` and `n_step_clean` count them. The definition is #1233's
  `n_gamma_step_clean`, and a test asserts the two agree on one synthetic acquisition that gives
  every reason the estimator has.
- **The fraction.** `DEFAULT_MIN_CLEAN_STEP_FRACTION = 0.65` in `tether.fret.gamma`, registered in
  PRD §11.2 and `docs/reference/parameters.md`, and passed as
  `compute_gamma(min_clean_step_fraction=…)`. The warning fires when
  `n_step_clean < fraction · n_step_evaluated`, only when γ was applied (a withheld γ already says
  so), and never with no measured step. `0` turns it off; a value outside `[0, 1]` is refused.
  0.65 lies in the gap between the highest fraction where γ is far from Deep-LASI's (0.583,
  `lab-06`) and the lowest where it is near (0.699, `lab-05`), near its middle, and above the one
  half that donor noise gives with a real acceptor drop. It separates near from far; it does
  not mark agreement within the ±10 % tolerance, which `lab-04` misses while clearing 0.65. With it, `lab-03`, `lab-04` and `lab-05` raise no warning, and
  `lab-01`, `lab-02`, `lab-06` and `lab-07` do.
- **The stamp.** `/settings/gamma` gains `n_step_evaluated`, `n_step_clean`,
  `min_clean_step_fraction` and `few_clean_steps`. The change is additive: no attribute is
  removed or retyped, and the frozen schema is untouched. A project written before this record
  has none of the four.
- **The summary.** `GammaSummary` carries the two counts and `few_clean_steps`. The batch correct
  stage, whose detail line is the correction summary of a `tether batch` run, adds
  `γ from few clean steps: <clean> of <measured> (flagged)`, which the progress report marks as
  flagged, as it does a low-confidence registration.

### Consequences

- Good: a γ like `lab-01`'s (2.7 times Deep-LASI's from 20 clean steps out of 45) now arrives
  with a warning in the batch log and a flag in the project, and nothing that was correct moves.
- Good: the workstation check records the same count, so each scheduled run keeps measuring the
  fraction against Deep-LASI on the same acquisitions.
- Trade-off: the margin is thin. The gap is 0.583 to 0.699, on three acquisitions each side from
  one laboratory and one run, at one level window. `lab-06` has only 24 measured steps, where one
  step moves its fraction by 0.04. A new acquisition near the boundary can fall either way, and
  the fraction should be revisited when more acquisitions are recorded.
- Trade-off: the flag is a reason to check γ, not proof that it is wrong. A step whose donor
  did not rise counts as measured but never enters the median, so a dataset can fall below the
  fraction with a sound γ. The warning text and the docs say "check", not "noise".
- Trade-off: the warning is only as visible as the surface that shows it: today the batch log
  and `/settings/gamma`. The application's correction surface (§9.6, #927) does not show it yet.
- Withholding γ below the fraction (option B) was considered and not chosen. Revisit it with the
  fraction, once more acquisitions are recorded.

## More information

- The measurement: #1233 (PR #1236, merged as `b3323ae`), recorded by the scheduled workstation
  run named above and read on the workstation on 2026-11-19 without a new run. Earlier context:
  #715 (PR #1109, ADR-0115), #1206 (PR #1219, ADR-0130) and #546.
- Literature: Consensus, query "single-molecule FRET gamma factor acceptor photobleaching donor
  recovery anticorrelated intensity change selection criterion", 2026-11-19. The multi-laboratory
  benchmark defines the individual γ as the ratio of the acceptor's decrease to the donor's
  increase upon acceptor bleaching ([Hellenkamp2018](https://doi.org/10.1038/s41592-018-0085-0)),
  and the practical guide reads anticorrelated donor and acceptor changes as the sign of energy
  transfer ([Roy2008](https://doi.org/10.1038/nmeth.1208)); [McCann2010](https://doi.org/10.1016/j.bpj.2010.04.063)
  remains the source of the per-molecule route. None of them sets a fraction of clean steps, so
  0.65 is Tether's own, chosen from the measurement above. That donor noise with a real acceptor
  drop gives a clean step about half the time assumes `ΔI_D` is equally likely to be either sign
  on those steps (a zero mean alone would not do), which holds for the synthetic dataset's
  Gaussian noise and is shown there.
- The decision: #1232.
