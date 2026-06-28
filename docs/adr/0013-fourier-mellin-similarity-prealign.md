# 0013 — 4-DOF Fourier-Mellin similarity prealign: log-polar recovery, masked-NCC disambiguation, real-data oracle

- **Status:** accepted
- **Date:** 2026-06-28
- **Deciders:** bioedca
- **PRD anchor:** Appendix E Stage 7 (4-DOF similarity prealign), §11.2 (prealign row), §11.1 (coordinates)
- **Milestone:** M1 (S5b — registration prealign, rotation + scale)

## Context and problem statement

Dual-view registration's coarse prealign (Appendix E Stage 7) is a **4-DOF
similarity** (translation + rotation + isotropic scale) in both Deep-LASI
(`createMapPhaseCorr.m:11` `imregcorr(...,'similarity')`) and the PRD. M1 S5a
(ADR-0012, option E) shipped only the **translation** DOF and recorded the
rotation+scale estimate as a planned follow-up (S5b), making
`SimilarityTransform2D`'s scale/rotation fields first-class "so the S5b estimator
slots in with no API change." ADR-0012 left two items open for S5b:

1. the **sign / 180° disambiguation** the FFT-magnitude method requires (the
   magnitude spectrum is centro-symmetric, so rotation is ambiguous mod 180° and
   the recovered scale's direction is argument-order dependent);
2. a committed **bead-calibration image-pair oracle** to validate rotation+scale
   *faithfully on real data, not only synthetic beads* — a §Data-gaps item.

This ADR records how S5b resolves both.

## Decision drivers

- **Faithful to Deep-LASI and the PRD** — `imregcorr(...,'similarity')` is exactly
  frequency-domain Fourier-Mellin (log-polar of the FFT magnitude for rotation +
  scale, phase correlation for translation); a Python log-polar implementation is
  the faithful analogue, not a divergence (so no new "divergence" ADR — ADR-0012
  already authorises S5b).
- **Validate on real data, never fabricated** (working agreement) — the prealign's
  rotation/scale must be checked against the real staged bead pair.
- **`main` stays green** — ship only what is verifiable; document the validated
  regime honestly rather than over-claim a general `imregcorr` replacement.
- **Additive** — keep ADR-0012's contract (fit-on-original coords, mutual NN,
  pluggable `prealign`); change only how `SimilarityTransform2D` is *estimated*.

## Considered options

**180°/sign/scale-direction disambiguation.**
- **A. Hand-derived sign convention** (closed-form rotation = ±recovered_angle,
  scale = recovered or 1/recovered). Rejected: the correct convention flipped
  between test constructions and the real pair (argument-order dependent) — a
  silent-bug magnet.
- **B. Smallest-|rotation| rule** (pick the candidate nearest 0°). Works for the
  physical near-identity case but still mis-set the scale *direction*.
- **C. Brute-force the four candidates** (rotation `{θ, θ-180}` × scale `{s,
  1/s}`), materialise each, recover its residual translation, and pick the highest
  **masked-overlap NCC**. No hand-derived sign; self-selects the correct branch.

**Translation robustness.** Windowing both images (Hann) before the real-space
phase correlation, vs not. Without windowing, warp-border artefacts corrupt the
recovered shift (observed); with it, the small-similarity regime recovers cleanly.

**Real-data oracle.** (i) image-domain Fourier-Mellin recovery on the staged
`map.tif`; (ii) synthetic recovery + real-pair behaviour check.

## Decision outcome

Chosen: **C (brute-force masked-NCC) + windowed translation + a hybrid oracle.**

`estimate_similarity_prealign(reference, moving)` (returns a
`SimilarityTransform2D` mapping `moving → reference`, default reference = donor):

1. band-pass each image (`difference_of_gaussians`, defaults 3/20 px) → Hann
   window (`filters.window`) → centred FFT magnitude (`fftshift(fft2(...))`);
2. log-polar resample the magnitude (`warp_polar(scaling='log')`, low-frequency
   `radius = shape[0]//8`), use the unique `[0,180)` angular half, and
   phase-correlate (`phase_cross_correlation`, `normalization=None`) → a raw
   rotation `(360/Nθ)·shiftᵣ` and scale `exp(shiftᵤ / (Nρ/ln radius))`;
3. evaluate the four ambiguity candidates; for each, de-rotate/scale `moving`
   about its centre, recover the residual translation by a **windowed** sub-pixel
   phase correlation (`upsample_factor = 10`), and score the full alignment by
   masked-overlap NCC; keep the best;
4. return the composed transform's `(scale, rotation, translation)`.

Everything is version-matched to the base `conda-lock` scikit-image 0.26 (the
`warp_polar` log-polar constants and `phase_cross_correlation` 3-tuple return were
read from the 0.26 source, not training memory).

**Reliable regime.** Validated for the near-identity regime of sub-degree rotation
and sub-percent scale — all dual-view split-sensor registration needs (the
committed calibration crop's `.tmap` similarity is ≈ 0.04°, ≈ 0.1% off unity).
Larger warps are not validated and grow unreliable on sparse fields (the
synthetic recovery test is robust across RNG seeds only in this near-identity
regime); rotations approaching ±90° are inherently ambiguous from a magnitude
spectrum and are explicitly out of scope.

**Oracle (option ii).** The staged `map.tif` is a contrast-stretched,
partly-saturated `uint8` **display export**: its FFT magnitude is DC-dominated, so
it cannot validate large-warp *recovery* — empirically confirmed (saturating an
otherwise-recoverable synthetic bead field to `uint8` breaks recovery; a correct
estimator returns ≈0°/1.0× on it for an applied warp). The real channel
relationship is itself near-identity, so there is no large warp in the real pair
to recover anyway. Therefore:

- **recovery** (rotation + scale + translation) is unit-tested on a deterministic,
  non-saturated **synthetic** bead field — a legitimate algorithm test against a
  known ground truth, *not* fabricated reference data;
- **real-data behaviour** is locked by the committed `bead_prealign_oracle.npz`
  (two real 256×256 bead-channel crops + the `.tmap`-derived ground-truth
  similarity): the estimator reproduces the real acceptor→donor map to **Δscale
  0.0002, Δrotation 0.04°, Δtranslation 0.33 px** (a real ~7.6 px offset, so a
  no-op estimator fails).

This is *not* "synthetic-only" (ADR-0012's concern): real bead images validate the
real behaviour; synthetic validates the recovery the saturated display image
cannot.

### Consequences

- Good: S5b is a faithful port of Deep-LASI/PRD Stage 7, closing ADR-0012's
  deferred rotation+scale DOF and its data-gap with the maintainer-staged bead
  pair; additive (no API change; `estimate_translation_prealign` remains the
  scale-1/rotation-0 special case); both real and synthetic coverage land.
- Trade-off: the estimator is scoped to the small-similarity (physical) regime,
  not arbitrary rotation. This is sufficient for channel registration and
  documented in the API and tests.
- Data note (non-blocking): because `map.tif` is a display export, real-data
  *recovery* of a large warp could not be validated; a raw (non-saturated) bead
  stack would strengthen that in future, but is not required for a correct S5b.

## More information

PRD Appendix E Stage 7, §11.1, §11.2 (prealign row);
`deeplasi/functions/mapping/createMapPhaseCorr.m:11`; ADR-0012 (registration
pairing; S5b follow-up pre-recorded); the scikit-image gallery "Using Polar and
Log-Polar Transformations for Registration"; Reddy & Chatterji 1996 (FFT-based
translation/rotation/scale-invariant registration); `scripts/make_bead_prealign_fixture.py`,
`tests/test_register_prealign.py`, `tests/fixtures/PROVENANCE.md`.
