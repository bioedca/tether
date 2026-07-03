<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0030 — Headless batch runner: per-movie isolation + provenance-derived per-stage checkpoint

- **Status:** accepted
- **Date:** 2026-07-03
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §6 ("Batch"), §7.11 (FR-BATCH), §7.2, §11.2 ("Over-gate batch policy"), §9 M3
- **Milestone:** M3

## Context and problem statement

FR-BATCH (§7.11) requires an overnight batch runner that isolates each movie
(continue-on-error), checkpoints **per stage** (extract / correct / idealize, so a
resume re-runs only a movie's incomplete stages), and emits a structured log plus an
end-of-run summary enumerating every movie's status and naming any failure. §7.11 also
requires **sidecar supervision** of the long-lived tMAVEN idealization sidecar
(per-IPC-call timeout + liveness, auto-restart up to N=3, an idealization-deferred
startup mode) and that the warn-vs-fail batch policy be configurable.

The whole runner is a ~2-session unit, so it is split: **PR7-A (this PR)** lands the
queue, per-movie isolation, the per-stage checkpoint, the structured log/summary, the
over-gate policy, and provenance; **PR7-B** lands the sidecar supervision paragraph.
The question this ADR settles is the runner's *shape*: what the unit of isolation is,
how a stage's completion is detected for checkpoint/resume, how the correct stage is
sequenced given the M3 estimators' inter-dependencies, and where the seam between PR-A
and PR-B falls.

## Decision — one `.tether` per movie is the unit of isolation

The runner processes a list of `MovieJob`s (movie → its own `output_path` `.tether`),
mirroring `extract_movie`, which already writes a **fresh** project per movie
atomically (temp-file + `os.replace`; never a partial file on failure; write-once
`movie_id`). One store per movie means a corrupt or crashing movie can never damage
another movie's store — the strongest reading of "isolate each movie". It is also the
natural checkpoint granularity (see below) and keeps the runner a thin path-level
orchestrator over the existing headless functions (no `Project` object, no lock — the
`compute_*` and `idealize_molecules` functions open the file directly and do not assert
the single-writer lock, which only the `Project` methods do; each per-movie store is
exclusively owned by the batch process, so there is no contention to guard).

Per-condition aggregation of α/γ *across* movies (a dataset-level median) is **not**
introduced here; each movie's corrections run over its own store with the existing
per-store estimators. Cross-movie/condition aggregation is an M4/M6 concern.

## Decision — checkpoint = provenance presence (no new schema)

A stage is "already done" iff the provenance group it writes is present in the
`.tether`:

- **extract** → `/settings/extraction` (write-once, `write_extraction`);
- **correct** → `/settings/correction` (`compute_corrected_fret`);
- **idealize** → a non-empty `/idealization` (any fitted `/idealization/{model}`).

The store *is* the checkpoint, so a resume re-opens each output and skips the stages
whose output already exists — re-running `run_batch` over the same jobs re-runs only
the incomplete stages. This adds **nothing** to the frozen §5 skeleton
(`schema-guard` stays green), needs no separate journal/state file that could drift
from the store, and composes with `idealize_molecules`' own refuse-to-overwrite guard
(the checkpoint skips a completed idealization before that guard could raise). Any read
error on a candidate output reads as "not done" so the stage is re-attempted rather
than falsely skipped.

## Decision — the correct stage is the Appendix-B ordered sequence, γ gated on applied α

The correct stage runs photobleach → leakage α → **γ (only when α was applied)** →
corrected-FRET. `compute_gamma` requires a non-sentinel `/molecules.alpha` and would
**raise** on the NaN "not computed" sentinel; leakage legitimately *withholds* the
dataset α when fewer than `min_qualifying_traces` donor-only tails qualify
(ADR-0027, "withhold rather than fabricate"). So a withheld α is **not** a stage
failure — γ is skipped and `compute_corrected_fret` degrades the missing factors to
apparent E, never a NaN factor (§7.2, invariant §1.3). Sequencing this (rather than
exposing a single `correct_project()` in `correct.py`) keeps the correction modules'
public surface unchanged and puts the orchestration where it belongs — in the runner.

## Decision — the over-gate policy reuses ADR-0014; additive `/settings/batch` provenance

`policy ∈ {warn, fail}` reuses the existing §11.2 "Over-gate batch policy" (ADR-0014):
a movie whose extraction reports `low_confidence_registration` is kept **with a flag**
under `warn` (default; never abort, never drop) or fails the movie under `fail`. Hard
errors (a corrupt movie, a raising stage) always isolate-and-continue regardless of
policy. Each processed store gets an additive `/settings/batch` group stamping the
policy, app version, per-stage status, and a UTC timestamp (NFR-REPRO) — additive under
the frozen `/settings` container, so no structural change.

## Considered options

- **A — All movies appended into one shared `.tether`** (via `write_extraction`).
  Rejected: a mid-append failure on one movie could corrupt the shared store, breaking
  isolation; it also bypasses `extract_movie`'s full registration/`.tmap` pipeline.
- **B — A separate checkpoint/state file** (`.batch-state.json`) recording per-stage
  status. Rejected: a second source of truth that can drift from the store, and
  redundant — the provenance each stage already writes answers "is it done?" exactly.
- **C — Bundle sidecar supervision (timeout/restart/deferred-mode) into this PR.**
  Rejected for scope: it is the ~150–250-line hard half of FR-BATCH and a distinct §7.11
  paragraph; splitting at the "idealize stage runs once, error-isolated" seam keeps each
  PR reviewable and matches the M-series PR-A/PR-B cadence.
- **D (chosen) — One store per movie, provenance-derived checkpoint, runner-owned
  correct sequencing, policy reused from ADR-0014, additive `/settings/batch`.**

## Consequences

- **Positive:** true per-movie isolation; a resume re-runs only incomplete stages with
  no extra state to keep consistent; the full withheld-α → apparent-E path is exercised
  headlessly through the runner; fully additive (schema-guard green, no `conda-lock`
  change, no new §11.2 tunable — `policy` is the pre-registered ADR-0014 row). The
  runner is a Qt-free library plus a `tether batch` CLI subcommand (FR-BATCH: every
  module usable without the GUI); stage runners are dependency-injected, so isolation,
  checkpoint, and policy are tested without a real movie or a live sidecar (one
  integration test drives the real correct stage on a synthetic store).
- **Negative / follow-up:** **PR7-B** must add sidecar supervision — a per-IPC-call
  timeout, liveness, auto-restart up to N (default 3, a new §11.2 tunable), the
  idealization-deferred-at-startup mode (sidecar absent/corrupt → extract+correct all
  movies, queue idealization) — layering over PR-A's single-attempt, error-isolated
  idealize stage. A GUI "run batch" surface over this headless core is a later thin
  wrapper. No per-condition α/γ aggregation yet (M4/M6).
- **Neutral:** the idealize stage here fails a movie's idealization in isolation on any
  sidecar error (extract+correct stay checkpointed); PR-B upgrades that single failure
  into an up-to-N auto-restart before giving up — a strict superset of PR-A's behavior.
