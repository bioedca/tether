<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0085 — A trailing dwell keeps the last frame of its analysis window

- **Status:** proposed; the re-frozen tMAVEN-app limit waits for the maintainer's decision on #849
- **Date:** 2026-11-12
- **Deciders:** bioedca
- **PRD anchor:** §7.7 FR-ANALYZE, Appendix C A2 (post-synchronized heatmap)
- **Milestone:** M18
- **Issue:** [#806](https://github.com/bioedca/tether/issues/806)

## Context and problem statement

The A2 post-synchronized heatmap (gallery row A2b) aligns every selected state jump to one column
and bins the observed apparent E around it. With `single_dwell=True`, each jump contributes the
dwell before it and the dwell after it. tMAVEN's `gen_sync_list_single` (`data_hist2d.py`, pinned
build `10f4230b`) sets the end of that window to the next jump's frame or, with no next jump, to
`nframes - 1`, the movie's last frame index. `histogram_sync_list` then bins `range(start, end)`,
so the end frame is never binned.

tMAVEN keeps every molecule in one rectangular array the length of the movie, with NaN outside
the molecule's analysis window. Tether passes each molecule's own windowed slice. These two inputs
make the same rule give different answers:

- **At a jump** both tools stop before the jump's frame (the last frame of the dwell after the
  synchronized jump). They agree.
- **At a window that photobleaching ends early** tMAVEN's end is the movie's last frame, which is
  NaN, so it keeps every valid frame of the window. Tether's end was the slice's last frame, so it
  dropped the window's final valid frame.
- **At a window that runs to the movie end** tMAVEN drops the window's final frame, because it is
  also the movie's last frame.

The #707 live comparison (PR #800) measured the second case: 280 of the 281 windows of the
committed SMD end before the movie does, and the largest total-variation distance against the
routine was 0.0061.

## Decision

1. **The trailing window ends at the trace end, inclusive.** A dwell with no later jump keeps every
   frame of the analysis window, its last frame included. Only a real jump ends a dwell early; a
   window end is not a jump. A dwell that ends at a jump and the fixed (`single_dwell=False`)
   window are unchanged.
2. **The movie-end frame is a deliberate departure from tMAVEN.** When a window runs to the movie
   end, tMAVEN drops its final frame and Tether keeps it, by rule 1. The two differ by that one
   frame, and only when it lands inside the time columns. Copying tMAVEN here would need the movie
   length inside the ragged core and would drop a valid frame for no reason of method; it is an
   artifact of the exclusive `range` against a padded row, not a choice the method makes.
3. **The tMAVEN-app gap is re-frozen.** The model the tMAVEN app holds pads its integer state path
   with 0 outside each window, so the app synchronizes on window borders as if they were jumps.
   Tether's `NO_STATE` border is never a jump, and that stays a deliberate difference (#806
   item 2). Under rule 1 the app, which sees the window end as a jump, now drops a frame that
   Tether keeps. The re-measured app values follow the limit file's rounding rule; one entry,
   `1_1_2`, rises from 0 to 0.00039 (limit 0.0004). This item is **proposed**: a frozen limit gets
   wider, and that needs the maintainer's explicit decision before merge.

## Consequences

- On Tether's windowed rows the A2b heatmap equals the tMAVEN routine for all ten measured
  selections, so `tests/test_analysis_plots_sidecar.py` asserts it exactly and the
  `A2b_windowed_vs_tmaven_routine` block leaves `schema/plot_parity_limits.json`.
- On the committed 281-molecule fixture one window (molecule 81) runs to the movie end. Its last
  jump is at frame 1633 and the movie ends at frame 1699, so the final frame maps to column 116,
  outside the 101 columns; the comparison on tMAVEN's rectangular rows therefore stays exact too.
  A data set with a shorter trailing dwell at the movie end would show the one-frame difference of
  rule 2. `tests/test_analysis_histogram_postsync.py` pins that case on a hand-made trace, and the
  parity tests against the verbatim `_tmaven_postsync` oracle give it rows that run one NaN frame
  past every window, which is the case where the two must agree.
- A single-dwell heatmap made before this change has one fewer binned frame per trailing dwell
  whose final window frame lands inside the time columns. No stored data changes: the heatmap is
  computed on demand and is not persisted in the HDF5 store.

## Options considered

- **Keep the old rule and record both items as deliberate differences** (#806's docs-only
  fallback). Rejected as the recommendation: it keeps dropping a valid frame inside the window, and
  the routine gap stays.
- **Match tMAVEN exactly, including the movie-end drop.** Rejected: it needs the movie length in
  the ragged core and reproduces an artifact of tMAVEN's array layout.
- **Copy the app's 0-padding border jumps.** Rejected in #806 itself: a window border is not a state
  change.
