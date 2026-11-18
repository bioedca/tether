<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0130 — The γ levels are averaged over the whole segments each side of the acceptor-bleach step

- **Status:** accepted; amends [ADR-0028](0028-gamma-acceptor-bleach-step-estimator.md) on the frames each γ level is averaged over, whose other decisions govern unchanged
- **Date:** 2026-11-18
- **Deciders:** bioedca
- **PRD anchor:** §7.2 (γ), §11.2 (the correction tolerance window row), Appendix B.2 step 4
- **Milestone:** M19

## Context and problem statement

ADR-0028 measures a molecule's γ as `ΔI_A / ΔI_D` across its acceptor-bleach step and takes each
of the four intensity levels as the mean of a **3-frame half-window**: `[step − 3, step)` before the
step and `[step, step + 3)` after it. It cites Deep-LASI's `deep_autocorrect_2color.m` as the source
of the formula, but Deep-LASI does not average 3 frames. At the pinned commit `d2d348cb` it averages
**every** frame of each segment: all the frames its network classes as FRET before the step
(`spFRET_frames`, classes 7 and 8, line 108) and all the acceptor-bleached frames after it
(`da_acc_bleached`, class 2, line 109), in the four `mean(...)` lines 118–127. The segment gate at
line 129 counts those same two frame sets.

The workstation check of #715 (PR #1109, ADR-0115, neutral ids only) measured what this costs on
real acquisitions. Recomputing Deep-LASI's γ from frames rebuilt out of the export's selection
range reproduced the exported `g` and `b` exactly on `lab-03`, `lab-04` and `lab-05`. On those
molecules:

- Tether's γ formula on **Deep-LASI's frames**, with Deep-LASI's leakage, gives Deep-LASI's
  `g(1 + b)`: median ratios 1.013, 1.025 and 1.019.
- Tether's γ with its **3-frame windows** is 0.83 to 1.50 times Deep-LASI's (quartiles), with
  medians by acquisition of 0.92, 1.15 and 0.94.
- Tether's γ with **whole segments around Tether's own step** gives 1.013, 1.021 and 1.024.
- The steps agree: Tether's step lies within 5 frames of Deep-LASI's for 89 to 100 % of the
  molecules.

So the formula and the step agree, and most of the per-molecule scatter between the two tools
comes from the 3-frame level windows. A level averaged over 3 frames carries `1/√3` of one frame's
noise; one averaged over a 40-frame segment carries `1/√40` of it.

## Decision drivers

- ADR-0028 names Deep-LASI as the source of the estimator; the frames it averages are part of that
  estimator.
- Per-molecule γ is applied per molecule (ADR-0028), so its scatter goes straight into each
  molecule's corrected E.
- A tunable stays registered (PRD §11.2), and a stored stamp stays additive (PRD §5).

## Considered options

- **A — Keep the 3-frame half-window as the default.** Rejected: it is what separates Tether from
  the tool its estimator cites, on real data.
- **B — Whole segments by default, the half-window kept as an option.** Chosen by the maintainer
  (2026-11-18, on #1206).
- **C — Whole segments only; retire the half-window.** Not chosen: the maintainer kept the short
  window available for a trace whose levels drift within a segment.

## Decision outcome

Chosen option: **B**.

- `tether.fret.gamma.DEFAULT_GAMMA_HALF_WINDOW` is now `None`. With `half_window=None` the
  pre-step level is the mean over the whole FRET segment `[0, step)` and the post-step level the
  mean over the whole donor-only segment `[step, donor_bleach)` — the two segments the
  `min_window_frames` gate already checks. Frames after the donor bleach are never averaged.
- A positive whole number `half_window` keeps the earlier behaviour: `half_window` frames each side
  of the step, the post window still stopped at the donor bleach. `3` is the earlier default. Zero,
  a negative number, a fraction, a boolean or a string is refused with `ValueError` rather than read
  as either mode.
- Unchanged from ADR-0028: the formula and its bare-`I_D` convention, the `(0, 5]` ceiling, the
  `degenerate-donor` gate, the `min_window_frames` segment gate, the median of the qualifying
  molecules, its withholding below `min_qualifying_traces` and the per-molecule fallback.
- **PRD §11.2.** The "Correction tolerance window (γ half-width)" row now states the whole-segment
  default, the half-window option, and Deep-LASI as the source. Appendix B.2 step 4 and §7.2 say the
  same.
- **Stored stamp.** `/settings/gamma` gains a `level_window` attribute, `whole-segment` or
  `half-window`. `half_window` stays an integer attribute and is `0` when the whole segments were
  used. A project written before this record has no `level_window` and used `half_window` frames.
  The change is additive: no attribute is removed or retyped, and the frozen schema is untouched.

### Consequences

- Good: Tether's per-molecule γ is now computed on the frames its cited source uses, and its
  scatter on noisy plateaus shrinks with the segment length
  (`tests/test_fret_gamma.py::test_whole_segment_uses_every_frame_and_scatters_less`).
- Good: the known-γ recovery tests and their limits (ADR-0028, #728) pass unchanged; the
  reference-formula parity check now runs on both the default and the 3-frame option.
- Trade-off: a level that drifts within a segment (a slow photophysical change) is averaged in,
  where a short window would follow the step. The option stays for that case.
- Every frozen number that depends on γ was regenerated, none loosened: the self-drift golden file
  `tests/golden/synthetic_traces.json` (#702; the other two golden inputs withhold γ),
  and the `/settings/gamma` sample of `docs/reproducibility.md`, whose old value the old code
  reproduces exactly from the page's own commands.
- Follow-up, owed before #1206 closes: rerun the #715 workstation corrected-FRET check with this
  default and record the new γ medians and FRET differences on #546. No agreement limit against
  Deep-LASI is set here; that stays with #546.

## More information

- Deep-LASI source: `gitlab.com/simon71/deeplasi`, commit `d2d348cb44804f91555aec9c79413c26bf6de045`
  (the pin in `scripts/reference/matlab/pins.toml`), file
  `functions/deeplearning/deep_autocorrect_2color.m`, lines 108–109 (the frame sets), 118–127 (the
  segment means), 129 (the gate). Read on 2026-11-18; SHA-256 of the file as served
  `6306d07bcab0a077d6681f74492a51c9195200f1b1cb3d0eba34350d42d665f9`. Deep-LASI carries no licence
  file, so nothing from it is committed; this record cites line numbers only.
- Literature: Consensus, query "gamma correction factor single-molecule FRET acceptor
  photobleaching mean donor acceptor intensity before and after bleaching", 2026-11-18. The
  multi-laboratory benchmark describes the individual γ as the donor's increase compared with the
  acceptor's intensity before photobleaching, without fixing a window
  ([Hellenkamp2018](https://doi.org/10.1038/s41592-018-0085-0)); [McCann2010](https://doi.org/10.1016/j.bpj.2010.04.063)
  remains the source of the per-molecule photobleaching route. The noise argument is the standard
  error of a mean.
- The measurement: #715, PR #1109 and ADR-0115 (the workstation run, neutral ids `lab-03`, `lab-04`,
  `lab-05`); the decision: #1206.
