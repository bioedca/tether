# 0012 — Registration pairing: mutual NN, fit on original coords; translation prealign first

- **Status:** accepted
- **Date:** 2026-06-27
- **Deciders:** bioedca
- **PRD anchor:** Appendix E Stages 7–8, §11.1 (coordinate conventions), §11.2 (prealign / NN-pairing rows)
- **Milestone:** M1 (S5 — registration prealign + pairing)

## Context and problem statement

Native dual-view registration (PRD Appendix E Stages 6–10) fits a degree-2
polynomial map from matched bead control points. Stages 7–8 produce those
matches: a coarse prealign brings the moving channel's centroids near the
reference channel's, then nearest-neighbour pairing within a pixel gate selects
the correspondences fed to the fit. The degree-2 fit + RMS-residual gate
(`fit_polynomial_transform`, `point_rms`) and the `.tmap` apply path already
exist from the M0.5 preview; this PR (M1 S5) supplies the prealign + pairing glue.

Two faithful-port questions had to be resolved, plus a scope question:

1. **How to pair.** Deep-LASI's `findPairs.m` does a one-directional greedy
   nearest-neighbour gate (`pdist2(...,'Smallest',1)`, keep `dist ≤ tol`). Its
   intended de-duplication (`D + tril(nan(...))`) is a no-op on a row vector and
   its zero-padding is computed-then-discarded, so two moving points can be
   assigned to the **same** reference point (double-assignment).
2. **Which coordinates feed the fit.** `createMapPhaseCorr.m` matches on the
   *prealigned* coords (`XY2(:,1:2) = transformPointsForward(PreMap, …)`) but
   builds the map from the *original* coords (`XY2(:,3:4)`); the prealign is never
   composed into the saved map.
3. **How much prealign to build now.** Stage 7 specifies a **4-DOF similarity**
   prealign (`imregcorr(...,'similarity')` — translation + rotation + isotropic
   scale, via FFT phase correlation / Fourier-Mellin log-polar).

## Decision drivers

- Faithful to Deep-LASI's *intent* where the reference is correct; fix the
  reference where it is buggy (the working agreement favours correctness over
  bug-for-bug porting).
- `main` stays green and releasable: ship only what is verifiable against
  committed, defensible fixtures — no algorithm whose only validation is a
  fabricated oracle.
- One concern per PR, sized to one session; a ~1.5-session unit may split with
  the split recorded (PLAN §0.1, §0.5).
- Coordinate conventions are explicit at every boundary: Tether stores 0-based
  `[x, y]` (PRD §11.1).

## Considered options

**Pairing.**
- **A. Mutual (one-to-one) nearest-neighbour within the gate** (cKDTree both
  directions; keep a pair only when each is the other's nearest within `tol`).
  Guarantees no point is double-assigned.
- **B. Faithful greedy port** of `findPairs.m` (one-directional gate). Reproduces
  the double-assignment bug — a wrong correspondence corrupts the fit.

**Fit coordinates.**
- **C. Fit on the original (un-prealigned) moving coords**; prealign only seeds
  matching. (Deep-LASI's actual behaviour.)
- **D. Fit on the prealigned coords.** Bakes the coarse transform into the saved
  map and double-counts it — wrong.

**Prealign scope (this PR).**
- **E. Translation-only phase-correlation prealign now; defer the 4-DOF
  rotation+scale (Fourier-Mellin) to a follow-up (S5b).**
- **F. Full 4-DOF Fourier-Mellin prealign in this PR.**

## Decision outcome

Chosen: **A + C + E.**

- **Pairing = mutual NN (A).** `pair_control_points` builds a `cKDTree` per side
  and keeps a pair only when the moving point's nearest reference (within `tol`)
  also has that moving point as *its* nearest within `tol`. This yields a unique
  one-to-one matching and fixes `findPairs.m`'s double-assignment. Default
  `tol = 2 px` (PRD §11.2).
- **Fit on original coords (C).** `pair_control_points` matches in the prealigned
  frame (when a `prealign` is given) but **returns the original, un-prealigned
  moving coordinates** (`PairedControlPoints.moving`), ready for
  `fit_polynomial_transform`. The prealign is never composed into the stored map.
- **Translation prealign now; 4-DOF deferred (E).** This PR lands
  `SimilarityTransform2D` (a full 4-DOF representation) and
  `estimate_translation_prealign` (the robust translation DOF, via
  `skimage.registration.phase_cross_correlation`, sub-pixel `upsample_factor = 10`,
  PRD §11.2). The rotation+scale estimate (Fourier-Mellin log-polar of the FFT
  magnitude spectra) is a follow-up (S5b): a prototype confirmed it is
  *recoverable* (scale to ±0.02; rotation magnitude to ~1°) but needs explicit
  sign-convention + 180° (FFT-magnitude symmetry) disambiguation, and — to be
  validated faithfully rather than only on synthetic beads — a committed
  bead-calibration **image-pair** oracle, which the repo does not yet hold (the
  `.tmap` carries unpaired per-channel `MapParticles`, not a labelled
  correspondence or a bead-image pair). That fixture is a §Data-gaps sourcing
  task tracked for S5b. The scale/rotation fields of `SimilarityTransform2D` are
  first-class now, so the S5b estimator slots in with no API change.

### Consequences

- Good: pairing is verified on real committed data — the `tdat_coloc_slice.tdat`
  250 row-aligned donor↔acceptor molecules give a true correspondence oracle; the
  mutual-match, fit-on-original, gate, and uniqueness invariants are all locked by
  tests. Translation prealign recovers a known synthetic shift to sub-pixel.
- Trade-off: until S5b, the prealign covers only the translation DOF, so pairing
  is reliable when the inter-channel rotation/scale is small (the common
  split-sensor case) and otherwise relies on a caller-supplied `prealign`. The
  pairing API takes a pluggable `prealign`, so this is additive, not rework.
- Deviation from Deep-LASI recorded: Tether's pairing is one-to-one where
  `findPairs.m` is greedy. This is an intentional correctness improvement, not a
  faithful-port gap.

## More information

PRD Appendix E Stages 6–10, §11.1, §11.2 (prealign / NN-pairing / polynomial-map
rows); `deeplasi/functions/mapping/createMapPhaseCorr.m`, `findPairs.m`,
`createMap.m`; PLAN §5 S5/S6; ADR-0008 (correction-factor remap), ADR-0011
(extraction-recall homed at M1). Follow-up **now homed in ADR-0013**: S5b (4-DOF
Fourier-Mellin rotation+scale prealign + a committed bead-image-pair fixture).
