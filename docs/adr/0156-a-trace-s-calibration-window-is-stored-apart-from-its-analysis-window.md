<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0156 — A trace's calibration window is stored apart from its analysis window

- **Status:** accepted
- **Date:** 2026-11-26
- **Deciders:** bioedca (issue #928 approved 2026-11-14, grooming card "Approve all recommended", Q3; card A1 option B puts it in v1.0.0)
- **PRD anchor:** §5 (data model), §7.2 FR-CORRECT (G03), §7.6 FR-ANNOTATE
- **Milestone:** M16
- **Issue:** [#928](https://github.com/bioedca/tether/issues/928)

## Context and problem statement

G03 has two events: *edit analysis windows or curation* takes ANALYSIS_READY back to
CORRECTED_REVIEW, and *calibration evidence changes* makes the calibration stale. Its note 03
says the calibration windows are distinct from the FRET-analysis windows, and that the evidence
after the acceptor bleach stays available for the calibration. A trace had one stored window,
`/molecules.analysis_window`, and γ read the whole `frame_range` with the bleach frames. Where is
the second window kept, what is its value in an existing store, and which edit sends which
event?

## Decision drivers

- **Additive, with an honest stamp.** A new table in the skeleton is a structural change: an ADR,
  a `schema_version` bump and a regenerated golden (PRD §12.6). Files stamped 1 to 3 open without
  a rewrite.
- **The γ of an existing store must not change.** The estimator is frozen (ADR-0028, ADR-0130),
  so the default window must read the frames a γ pass read before.
- **An analysis-window edit must not touch the γ evidence**, and a calibration-window edit is
  calibration evidence, as an exclusion of a contributor is (#927).

## Considered options

- **A. A seventh rich table, `/calibration_windows/table`**, append-only, in the skeleton from
  version 4; a molecule with no row has a default read from its bleach-step fields.
- **B. A field appended to `/molecules/table`.**
- **C. A dataset under `/settings`**, additive data with no version bump.

## Decision outcome

Chosen option: **A**, implemented in `src/tether/io/schema.py` (`CALIBRATION_WINDOWS_DTYPE`,
`CALIBRATION_WINDOWS_SINCE`), `src/tether/project/calibration_windows.py`,
`src/tether/project/gamma.py` (the step is read inside the calibration window) and
`src/tether/project/core.py` (the analysis-window event).

B changes the dtype of an existing compound dataset, which h5py can only do by rewriting every
row of every older store; the issue asks for no rewrite of old data. C keeps the record out of
the frozen skeleton, so `schema-guard` would not stop a later rename of its fields, while the
window is calibration evidence that an approval freezes. A is the step ADR-0136 and ADR-0140 took.

### The record and the default

`/calibration_windows/table` has 8 fields: `molecule_id`, `molecule_key`, `window`
(`i4 (2,)`, absolute `[start, stop)`), `by`, `actor`, `reason`, `timestamp` and `app_version`.
The latest row of a `molecule_id` holds; the window `(-1, -1)` gives the row back its default. A
stored window is clamped into the row's `frame_range` when written (the rule of
`set_analysis_window`) and clipped again when read.

A molecule with no row has the default from the bleach-step fields it already has: from
`frame_range[0]` to the donor's first-bleach frame `bleach_frames[0]`, or the whole
`frame_range` when the donor bleach is not detected or is at the first frame. `trace_gamma`
never reads a frame at or after the donor bleach, so this window gives the γ of the whole
`frame_range` exactly; the existing γ tests pass unchanged. Reading writes nothing. The first
write into a store stamped before 4 runs `upgrade_skeleton` in a guarded write, which keeps the
file as it was beside it as `<stem>.v<n>-backup.tether` (PRD §5.4, ADR-0136).

### The two events

- **An analysis-window edit** through the `Project` handle (`set_analysis_window`,
  `reset_analysis_window`, `restore_analysis_windows`) that changes a row in ANALYSIS_READY sends
  *edit analysis windows or curation*, so the state is CORRECTED_REVIEW. The writer lock is
  checked before the window is written. The calibration windows and the γ evidence table are not
  touched. In any other state no event is sent.
- **A calibration-window edit** (`set_calibration_window`, `reset_calibration_window`) follows
  the rule of `set_gamma_contributor` (#927): refused in CALIBRATION_APPROVED, where the evidence
  is frozen; in CORRECTED_REVIEW *calibration evidence changes* and in ANALYSIS_READY *factor or
  contributor evidence changes*, both sent before the write, so the state is CALIBRATION_STALE.
  When the project has a γ pass, the same publish computes it again with the parameters stamped in
  `/settings/gamma`, and GAMMA_REVIEW and CALIBRATION_BLOCKED then follow the new pass as they do
  for a contributor decision.

### The trace view

`TraceView.calibration_window` carries the window into the trace dock, which draws it as a
shaded band on the intensity panel; the analysis window stays two dashed lines on the FRET
panel. A tooltip on each names it (`CALIBRATION_WINDOW_TEXT`, `ANALYSIS_WINDOW_TEXT`).

### Consequences

- Good: an analysis-window edit can no longer reach the calibration, and the calibration keeps
  the donor-only frames after the acceptor bleach that the default analysis window leaves out.
- Good: an older store keeps its γ and opens unchanged; it is migrated only by the first write.
- Bad / trade-off: a file stamped 4 does not open in an app that knows only 3 (PRD §5.4).
- Bad / trade-off: in CALIBRATION_APPROVED the edit is refused rather than making the
  calibration stale, because G03 has no edge out of that state for changed evidence; apply the
  correction version first. This is the rule of the contributor evidence (#927).
- Follow-up: the shell's store reader passes the stored calibration window into each
  `TraceView`; a curation-label change in ANALYSIS_READY is the other half of the first event
  and is not part of #928.
- Gates: `tests/test_calibration_windows.py`, the dock test in `tests/test_trace_dock.py`, and
  `schema-guard` on the regenerated `schema/schema_frozen.json`.

## More information

- G03 note 03 (PRD §7.2), [ADR-0131](0131-the-correction-states-are-a-transition-log-in-the-project.md)
  for the stored state machine, ADR-0136 and ADR-0140 for the migration step.
