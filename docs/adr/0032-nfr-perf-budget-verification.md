<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0032 — NFR-PERF budget verification: a light M3 gate over slice-scaled envelopes

- **Status:** accepted
- **Date:** 2026-07-03
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §8 (NFR-PERF), §11.2 ("Per-trace UI latency budget"), §12.10, §9 M3
- **Milestone:** M3

## Context and problem statement

PRD §8 (NFR-PERF) states three performance targets, explicitly framed as **"a light §9
gate, not an SLA matrix"** and, per §12.10, **first verified at M3** (the trace dock whose
render latency is budgeted lands at M2, and the overnight extract → correct → idealize
envelope is only end-to-end at M3, so M1 has nothing to measure):

1. **per-trace render+navigate latency ≈ 100 ms** (to sustain the 1–2 s/trace cadence);
2. **a ~100-movie condition finishes extract + correction + idealization overnight**
   ("a scaled estimate from the slice");
3. **a bounded `.tether` size envelope per condition**.

The design question is how to encode these as CI-durable checks in the **default
(small-fixture) test tier** without (a) fabricating numbers, (b) building a flaky
wall-clock SLA on shared runners, or (c) requiring the ~0.9 GB full movie or the tMAVEN
sidecar (neither available headlessly). The §8 reference-hardware floor is a 16 GB / HDD /
~100 GB laptop where a ~100-movie condition is ≈ 90 GB of raw movies (kept on OneDrive
Files-On-Demand, not all hydrated), so the `.tether` + scratch footprint "must stay
modest," and extraction/trace I/O favor sequential/block access.

## Decision

Add a Qt-free **`tether.project.perf`** module (named budget constants + measurement /
projection helpers) plus a `tests/test_project_perf.py` gate, verifying each target as an
**envelope**, not an SLA:

- **Latency (the one PRD-registered value).** `PER_TRACE_LATENCY_BUDGET_S = 0.100` (§11.2
  "Per-trace UI latency budget"). A `@pytest.mark.gui` test times a real `TraceDock`
  `set_trace` (render + navigate) on a reference-length ~1740-frame trace via
  `min_runtime` (minimum over repeats — isolates compute cost from scheduler jitter) and
  asserts it is under the 100 ms budget. Measured ~0.5 ms offscreen: a ~200× headroom
  that still flags a super-linear render regression.

- **`.tether` size envelope.** A molecule's stored cost is dominated by its **six
  redundant float32 intensity layers** ({donor,acceptor}×{raw,corrected,background}, PRD
  §5.1) = `6·4 = 24` B/frame. `measure_store_size` reads a real extracted store's
  *on-disk* dataset bytes (HDF5 `get_storage_size`, so free-space slack is excluded) and
  the test asserts the gzip'd `/traces` cost stays within a 1.5× envelope
  (`36` B/mol/frame; measured ~18) — an **N-robust** claim (the `/traces` marginal, not
  `total/n_mol`, so the fixed skeleton/registration overhead that dominates at small
  molecule counts does not confound it), and it flags a storage-dtype regression (float64
  six-layer = 48 B/frame > 36). `estimate_condition_bytes` projects the reference
  condition (~100 movies × ~250 mol × 1700 frames ≈ 1.1 GB) and asserts it stays under a
  modest `MAX_CONDITION_BYTES = 5 GiB` cap (~5.5% of the 90 GB movie footprint).

- **Overnight envelope (slice-scaled).** `scale_seconds_to_reference_movie` projects a
  small slice's *real measured* extraction time to the reference full movie
  (512×512×1700) by **pixel volume** (extraction is dominated by per-pixel block I/O +
  detection + integration, ~linear in pixels to first order); `project_overnight` scales
  that across the ~100-movie condition and asserts it fits the unattended
  `OVERNIGHT_WINDOW_HOURS = 12 h` window. Measured: a ~10 s/movie projection → ~0.3 h for
  100 movies, ~42× headroom.

## Scope and consequences

- **Only the latency target is a §11.2 tunable.** The size/overnight figures are
  *derived engineering envelopes* consequent on the frozen §5.1 data model and the §8
  reference-hardware floor, which the PRD deliberately left soft ("not an SLA matrix"), so
  they live as documented module constants — the PRD (`docs/PRD.md`) is **not** edited.
- **The full-movie + real-sidecar SLA is deferred to the gated tier.** The default matrix
  scales from a small synthetic slice; the full ≈0.9 GB UCKOPSB movie and the real vbFRET
  sidecar timing belong in `large-fixtures.yml` (PLAN §2.2), never a required check.
  Correction is negligible pure-numpy `O(N·T)`; per-molecule vbFRET idealization is
  bounded and parallelizable across molecules (§8), so the extract-time projection with
  its large window headroom is the honest indicator of "fits overnight."
- **No schema/dep change.** Read-only measurement; `schema-guard` stays green, no
  `conda-lock` change (numpy/h5py/pyqtgraph already in the base lock).
- **Not fabricated.** Every envelope constant is grounded in a real measurement or the
  data model; a value that could not be measured headlessly (full movie, sidecar) is
  explicitly deferred to the gated tier rather than guessed (§Data-gaps discipline).

## Alternatives considered

- **A hard per-movie wall-clock SLA in the default matrix** — rejected: flaky on shared CI
  runners and impossible without the full movie + sidecar; the PRD explicitly declined an
  SLA matrix.
- **Adding §11.2 rows for the size/overnight envelopes** — rejected: over-formalizes what
  §8 left deliberately soft; they are consequences of the data model, not knobs a user
  turns.
- **Naive ×100 of the slice extraction time** — rejected as misleading (a 64×96 slice is
  ~4600× smaller than the reference movie); the projection scales by pixel volume instead.
