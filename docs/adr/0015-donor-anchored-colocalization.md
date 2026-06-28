# 0015 — Donor-anchored colocalization: keep dark/low-FRET acceptors, apply the map in the coordinate domain

- **Status:** accepted
- **Date:** 2026-06-28
- **Deciders:** bioedca
- **PRD anchor:** §7.1 (FR-EXTRACT; "Colocalization **shall** be donor-anchored"), Appendix E Stages 11–13, §9 M1, §11.2 (colocalization-distance row)
- **Milestone:** M1 (S7 — colocalization + apply-map-at-extraction + crop box)

## Context and problem statement

M1 S6 (ADR-0014) landed the `RegistrationMap` — a donor↔acceptor calibration that
warps coordinates in both directions. S7 must turn detected spots + that map into
the **molecule list** the integrator (S8) extracts: which donor spots become
molecules, where each one's acceptor is read, and which spots are dropped.

Deep-LASI's `findColoc(T, 3)` (`mapping/findColoc.m`) warps every channel's spots
into the reference frame, nearest-neighbour matches within 3 px, and **keeps a
molecule only if it has an independently-detected partner in every channel**
(`findColoc.m:110`). For single-laser (donor-excitation) FRET that "partner in
every channel" rule silently discards exactly the molecules a FRET-efficiency
histogram must keep: the **acceptor-dark and low-FRET** population, whose acceptor
emits too weakly to be detected as its own spot. Dark / non-FRET acceptor states
are a real and substantial fraction of FRET data (Vogel 2012, *PLoS ONE*), so
filtering on independent acceptor detection biases E toward high values.

The open questions: *what anchors a molecule, where is the acceptor read, in which
domain is the map applied, and when is a molecule dropped?*

## Decision drivers

- **Faithful to §7.1 / Stage 11–13** — donor-anchored, coordinate-domain apply,
  21×21 crop box, skip-out-of-frame.
- **Keep the low-FRET / dark-acceptor population** — the histogram must not be
  biased by an independent-detection requirement (Vogel 2012; Wanninger 2023,
  Deep-LASI, motivates reading the acceptor at the mapped position).
- **No interpolation bias** — Deep-LASI rewarps the movie only for display/QA;
  extraction transforms *coordinates*, never resampling pixels (`batchExtraction.m`).
- **Agree with the integrator by construction** — the S7 "is this extractable?"
  test must be the *same* predicate as S8's `valid` mask, or the two disagree and
  an all-zero trace slips into the molecule list.
- **Additive over S6** — consume the `RegistrationMap`; fit nothing new here.

## Considered options

**What anchors a molecule.**
- **A. Donor-anchored: every in-frame donor spot is a molecule; read the acceptor
  at the mapped donor position regardless of independent acceptor detection.**
  Chosen — the §7.1 mandate; retains dark/low-FRET acceptors. The independent
  acceptor detection is still computed but only as an informational
  `acceptor_detected` flag that never drops a molecule.
- **B. findColoc's "partner in every channel".** Rejected — drops the low-FRET /
  dark population, biasing E (the exact failure §7.1 calls out).

**Which domain the map is applied in (Stage 12).**
- **C. Coordinate domain — warp the donor *coordinates* into the acceptor frame
  (`apply_reference_to_moving`), keep sub-pixel precision, round only at the crop.**
  Chosen — no interpolation bias on the integrated intensities.
- **D. Rewarp the acceptor movie, then extract at fixed positions.** Rejected —
  injects resampling/interpolation bias; Deep-LASI reserves the rewarp for QA only.

**The crop-box guardrail (Stage 13).**
- **E. Skip a molecule whose 21×21 window leaves *either* channel's frame**, using
  the shared `aperture_in_frame` predicate (extracted from the integrator so the
  two are one source of truth). Chosen — a FRET pair needs both apertures in-frame;
  a kept molecule is exactly an `integrate_traces`-`valid` one.
- **F. Re-derive an in-frame check locally in coloc.** Rejected — duplicates the
  predicate and risks drift from the integrator's `valid` mask.

**The `acceptor_detected` gate boundary.**
- **G. Strict `< 3 px` (findColoc.m:58), evaluated explicitly in NumPy** on the
  true nearest neighbour, so it is independent of `cKDTree`'s `distance_upper_bound`
  convention (inclusive in current scipy). Chosen — faithful and version-stable;
  the boundary is measure-zero on real sub-pixel data regardless.

## Decision outcome

Chosen: **A + C + E + G**, in a new `tether.imaging.coloc` module.

- `colocalize(donor_spots, registration_map, *, donor_shape, acceptor_shape,
  acceptor_spots=None, window=21, coloc_distance_px=3.0)` → `ColocalizedMolecules`
  (frozen): row-aligned `donor_xy`, `acceptor_xy` (= `apply_reference_to_moving`
  of the donor), `acceptor_detected`, `donor_index`, `acceptor_index`.
- The acceptor read position is the donor warped forward (coordinate domain). The
  `acceptor_detected` flag warps the acceptor spots *into donor coords*
  (`apply_moving_to_reference`, "warp R spots into G coords") and NN-matches each
  kept donor strictly within `coloc_distance_px`; it never filters the list.
- Molecules are kept iff the `window×window` aperture fits in **both** frames, via
  the new shared `tether.imaging.aperture.aperture_in_frame` predicate — which
  `integrate_traces` now also uses for its `valid` mask, so S7-kept ≡ S8-valid.

`DEFAULT_COLOC_DISTANCE_PX = 3.0` (PRD §11.2). The real-data extraction-vs-Deep-LASI
acceptance (recall / Pearson / RMS) is the M1 S9 oracle; S7's tests are synthetic
(known transforms) so each warp is exactly predictable.

### Consequences

- Good: §7.1 colocalization is faithfully homed — donor-anchored (no E bias from a
  detection requirement), coordinate-domain (no interpolation bias), both-channel
  crop-box guardrail sharing one predicate with the integrator. Apparent-E-style
  "never silently drop the population" discipline (ADR-0003) extended to the
  acceptor-dark molecules.
- Trade-off: `acceptor_detected` is informational only; downstream code that wants
  the classic colocalized-only subset filters on the flag itself.
- No new tunable beyond the existing §11.2 colocalization-distance row (now
  cross-referencing this ADR); no schema change (coloc is pure computation).

## More information

PRD §7.1, Appendix E Stages 11–13, §9 M1, §11.2 (colocalization-distance row);
`deeplasi/functions/mapping/findColoc.m:4-112` (the "partner in every channel" gate
at :110), `traces/batchExtraction.m:150-164,182`, `traces/extractTraces.m:9-25`;
ADR-0014 (the `RegistrationMap` this consumes), ADR-0003 (never-silently-drop
sibling); Vogel 2012 (*PLoS ONE*, dark acceptor states), Wanninger 2023
(*Nat. Commun.*, Deep-LASI). `src/tether/imaging/coloc.py`,
`src/tether/imaging/aperture.py` (`aperture_in_frame`), `tests/test_coloc.py`,
`tests/test_aperture.py`.
