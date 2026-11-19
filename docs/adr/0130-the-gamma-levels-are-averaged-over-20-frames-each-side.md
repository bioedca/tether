<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0130 — The γ levels are averaged over 20 frames each side of the acceptor-bleach step

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
- The steps agree: Tether's step lies within 5 frames of Deep-LASI's for 89 to 100 % of the
  molecules.

So the formula and the step agree, and most of the per-molecule scatter between the two tools
comes from the 3-frame level windows. A level averaged over 3 frames carries `1/√3` of one frame's
noise; one averaged over 20 frames carries `1/√20` of it.

The first draft of this record made Deep-LASI's **whole segments** the default. Rerunning the
#715 corrected-FRET check with that draft (2026-11-18) showed why a whole segment is the wrong
default for Tether. Tether's pre-step segment is every frame from the start of the trace, and on
these 1,700-frame movies the step falls at frames 700 to 1,280. A donor that dims slowly over that
time lifts the pre-step mean above the post-step one, so the donor no longer rises across the step
and the molecule fails the `degenerate-donor` gate. With whole segments, γ was **withheld**
(fewer than 10 qualifying molecules) on `lab-01`, `lab-02`, `lab-06` and `lab-07`, which had it with
3 frames: 7, 4, 3 and 8 qualifying against 13, 20, 12 and 11. Deep-LASI does not see this because
its network classes only part of the trace as FRET.

The same check, swept over level windows of 3, 10, 20, 50, 100 and 200 frames each side and the
whole segments, gave:

| | 3 frames | 20 frames | 50 frames | whole segments | Deep-LASI `g(1 + b)` |
|---|---|---|---|---|---|
| γ median, `lab-03` | 1.360 | 1.147 | 1.107 | 0.952 | 1.068 |
| γ median, `lab-04` | 1.182 | 0.942 | 0.872 | 0.933 | 0.788 |
| γ median, `lab-05` | 1.423 | 1.210 | 1.174 | 1.180 | 1.109 |
| median \|ΔE\|, `lab-03` | 0.0203 | 0.0155 | 0.0163 | 0.0233 | |
| median \|ΔE\|, `lab-04` | 0.0340 | 0.0211 | 0.0183 | 0.0201 | |
| median \|ΔE\|, `lab-05` | 0.0284 | 0.0280 | 0.0275 | 0.0275 | |
| γ withheld on | none | none | `lab-06` | `lab-01`, `lab-02`, `lab-06`, `lab-07` | |

`ΔE` is Tether's corrected FRET minus Deep-LASI's, per molecule. On `lab-01`, `lab-02` and `lab-06`
Tether's γ stays 1.1 to 4 times Deep-LASI's at every window, so that gap is not a windowing effect
and is left to #546.

## Decision drivers

- Per-molecule γ is applied per molecule (ADR-0028), so its scatter goes straight into each
  molecule's corrected E.
- A factor withheld on an acquisition that had one is a loss to the user, not a refinement.
- A tunable stays registered (PRD §11.2), and a stored stamp stays additive (PRD §5).

## Considered options

- **A — Keep the 3-frame half-window as the default.** Rejected: it carries most of the
  per-molecule scatter against the tool its estimator cites.
- **B — Whole segments by default.** Rejected after the measurement above: it withholds γ on four
  of the seven acquisitions that had it.
- **C — 20 frames each side by default, whole segments and other half-windows kept as options.**
  Chosen by the maintainer (2026-11-18, on #1206).
- **D — A wider fixed window, such as 50 frames.** Not chosen: it withholds γ on `lab-06`, and on
  `lab-03` to `lab-05` it is no closer to Deep-LASI than 20 frames.

## Decision outcome

Chosen option: **C**.

- `tether.fret.gamma.DEFAULT_GAMMA_HALF_WINDOW` is now `20`: the pre-step level is the mean over
  `[step − 20, step)` and the post-step level the mean over `[step, step + 20)`, the post window
  still stopped at the donor bleach. Twenty equals `DEFAULT_MIN_WINDOW_FRAMES`, and the segment gate
  requires both segments to be longer than that, so on every trace that passes the gate both
  windows lie wholly inside their segments.
- `half_window=None` averages the whole segments instead, `[0, step)` and `[step, donor_bleach)`,
  the frames Deep-LASI averages. `half_window=3` is the earlier default. Zero, a negative number, a
  fraction, a boolean or a string is refused with `ValueError`.
- Unchanged from ADR-0028: the formula and its bare-`I_D` convention, the `(0, 5]` ceiling, the
  `degenerate-donor` gate, the `min_window_frames` segment gate, the median of the qualifying
  molecules, its withholding below `min_qualifying_traces` and the per-molecule fallback.
- **PRD §11.2.** The "Correction tolerance window (γ half-width)" row now states the 20-frame
  default, the whole-segment and other half-window options, and this record as the source.
  Appendix B.2 step 4 and §7.2 say the same.
- **Stored stamp.** `/settings/gamma` gains a `level_window` attribute, `half-window` or
  `whole-segment`. `half_window` stays an integer attribute: `20` by default, `0` when the whole
  segments were used. A project written before this record has no `level_window` and used
  `half_window` frames. The change is additive: no attribute is removed or retyped, and the frozen
  schema is untouched.

### Consequences

- Good: on the acquisitions where Tether's γ and Deep-LASI's are comparable, the per-molecule
  corrected-FRET difference shrinks or stays level against 3 frames, and γ is kept on every
  acquisition that had it.
- Good: the scatter on noisy plateaus shrinks with the window
  (`tests/test_fret_gamma.py::test_wider_windows_scatter_less_than_three_frames`); the known-γ
  recovery tests and their limits (ADR-0028, #728) pass unchanged, and the reference-formula parity
  check runs on the default, the whole segments and 3 frames.
- Trade-off: the window and the gate now share one number by choice, not by derivation, and the
  20 was chosen on three acquisitions of one laboratory. A level that drifts within 20 frames of
  the step is still averaged in.
- Every frozen number that depends on γ was regenerated, none loosened: the self-drift golden file
  `tests/golden/synthetic_traces.json` (#702; the other two golden inputs withhold γ), and the
  `/settings/gamma` sample of `docs/reproducibility.md`, whose old value the old code reproduces
  exactly from the page's own commands.
- Follow-up, owed before #1206 closes: record the γ medians and FRET differences of this default
  on #546. No agreement limit against Deep-LASI is set here; that stays with #546.

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
- The measurement: #715, PR #1109 and ADR-0115 (the workstation run, neutral ids `lab-01` to
  `lab-07`), and the window sweep run on the workstation on 2026-11-18 for #1206 with a throwaway
  test that was not committed; the decision: #1206.
