<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0029 — Composite corrected-FRET provenance hash + per-factor idealization staleness scope

- **Status:** accepted
- **Date:** 2026-07-03
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §5.1 (`/idealization` provenance hash, lines 275–287), §7.2, §7.4, §9 M3 (FR-CORRECT)
- **Milestone:** M3

## Context and problem statement

The M0.5/M2 idealization store (ADR-0024) already stamped each fitted model with a
**per-molecule input-provenance hash** and a `stale_molecule_keys` recomputation, but
that hash covered only the *windowed intensity values* fed to the fit
(`input_trace_hash`). That was correct while idealization ran purely on the apparent-E
substrate (M1/M2: `corrected` = background-subtracted disk intensity). It is **not**
sufficient once M3 lands photophysical corrections: applying or changing leakage α or
detection γ changes the corrected-FRET a molecule's idealization is meant to reflect,
yet leaves the raw windowed intensity untouched — so a trace-values-only hash would
**never flag the idealization stale** when a correction changed. The corrected-FRET PR
(#77) foresaw this and explicitly deferred "persisting own-vs-fallback γ" and the
per-factor re-stale scope to this PR.

PRD §5.1 spells out exactly what the hash must cover and how the re-flag scope must
behave (lines 275–287): the hash is "of the inputs the corrected-FRET was computed
from — the molecule's *effective applied* α and γ, the apparent-E toggle, the
analysis-window bounds, and the input-trace identity", **deliberately not** a hash of
the final E array (which would miss a window edit that rounds to the same E) and
**deliberately not** the global factor set (which would falsely STALE the whole cohort
on any unrelated global-median shift).

## Decision — the composite provenance hash (`input_provenance_hash`)

Each molecule's staleness stamp becomes a SHA-256 over, in order:

1. the **input-trace identity** — the existing `input_trace_hash` over the windowed
   donor/acceptor intensity of the fit's `intensity_quantity` (the background-subtracted
   layer; a re-extraction that changes the raw transitively changes it);
2. the **analysis-window bounds** `(pre, post)` as explicit integers (so a same-length
   window shift with coincidentally-identical values still re-stales — the §5.1 "not a
   hash of the final E array" rationale);
3. the molecule's **effective applied α** and **effective applied γ**, taken from
   `/molecules.alpha` / `/molecules.gamma` when a real correction is in force, and
   **folded as the apparent-E identity `(α=0, γ=1)`** when the molecule's
   `correction_method` is an apparent-E method (`apparent-E (corrections unavailable)`,
   `apparent-E (user toggle)`) or the un-corrected `""` extraction default.

Folding the **effective** factor (not the raw stored one) means the *apparent-E toggle*
enters the hash through its effect on the applied correction: flipping to apparent
drops the effective factors to `(0, 1)` and re-stales; and a stored-factor edit *while
a molecule stays on apparent E* does **not** spuriously re-stale it (the corrected-E it
would feed is never displayed). Floats are folded via `float.hex` (exact IEEE-754,
byte-identical across the 3-OS matrix) with explicit `nan`/`inf` tokens, so the digest
never depends on a NaN bit pattern.

The same function is used by both writers of `/idealization/{model}` — the in-app
fitter (`idealize_molecules`) and the tMAVEN return-leg importer (`handoff`) — so a
freshly-imported model's stored hash equals `stale_molecule_keys`' recompute exactly and
never reads as false-stale.

## Decision — per-factor re-flag scope falls out of reading each molecule's own factors

The scope is **not** implemented with special-case branches; it is an emergent property
of hashing each molecule's *own* stored factors (§5.1):

- **γ is per-molecule with a population-median fallback** (ADR-0028): a qualifying
  molecule keeps its own γ in `/molecules.gamma`; the rest carry the dataset median. A
  γ-median shift on a re-run changes only the **fallback** molecules' stored γ, so only
  their composite hash diverges — **only the fallback molecules re-stale**. No separate
  own-vs-fallback flag is persisted: the stored γ value *is* the discriminator.
- **applied α is purely global** (the donor-only-sample median applied identically to
  every FRET molecule, §7.2): an α recalibration changes **every** molecule's
  `/molecules.alpha`, so the whole cohort re-stales — the intended condition-wide
  re-idealization event, not a cheap edit.

## Decision — exclusion + one-click re-idealize

- `live_molecule_keys` is the complement of `stale_molecule_keys` within a model — the
  non-stale key set TDP/dwell analysis (M6) **includes**; STALE molecules are excluded
  until refreshed, so a state path is never mixed with inputs it was not fit on (§5.1).
- `reidealize` re-fits an existing model over its recorded molecule set + configuration
  (`model_type`, `intensity_quantity`, the fixed `nstates` or a fresh max-ELBO sweep
  over the recorded grid) with `overwrite=True` — the headless half of the "one-click
  re-idealize" a STALE model offers. Both are exposed on `Project`.

## Considered options

- **A — Fold the *global* factor set (the `/settings` α/γ medians) into the hash.**
  Rejected by PRD §5.1: it would falsely STALE the whole cohort whenever any unrelated
  global median shifts, defeating the per-factor scope.
- **B — Hash the final per-frame corrected-E array.** Rejected by PRD §5.1: a
  window-only edit (or any change that rounds to the same E) would slip through; and it
  couples staleness to a derived array rather than its inputs.
- **C — Persist an explicit per-molecule `own-vs-fallback γ` column** to drive the
  scope. Rejected as unnecessary surface: appending a `/molecules` column is additive
  and schema-guard-legal, but the stored γ value already discriminates fallback from
  own-γ molecules for the median-shift scope, so a flag adds a field without adding
  behavior.
- **D (chosen) — Composite hash of the *effective applied* per-molecule factors +
  window + trace identity**, with the scope emerging from reading each molecule's own
  factors. Faithful to PRD §5.1 verbatim, no schema change, one hash function shared by
  both model writers.

## Consequences

- **Positive:** applying/changing corrections now correctly re-stales dependent
  idealizations with the exact §5.1 per-factor scope; TDP/dwell get a clean live-set to
  consume at M6; the importer stays consistent so hand-off round-trips don't false-stale;
  fully additive — `/idealization/{model}` model subgroups are still additive data
  (ADR-0024/0005), no `/molecules` column added, schema-guard green, no version bump.
- **Negative / follow-up:** the hash scheme changed (prefix `…-provenance-v2`), so a
  model persisted by the pre-M3 trace-only hash reads as stale after upgrade and must be
  re-idealized once — acceptable (no persisted user idealizations predate M3; the model
  is transient and cheaply refit). The GUI surface (a STALE badge + a one-click
  re-idealize button wired to `reidealize`) is a thin follow-up over this headless core,
  mirroring how ADR-0024/0026 split the headless core from the GUI wiring.
- **Neutral:** idealization still *fits* on the apparent-E substrate at M3 (switching
  the fit input to corrected-E is out of scope here); this PR makes the idealization
  *aware* of corrections via provenance, which is what §5.1 requires.
