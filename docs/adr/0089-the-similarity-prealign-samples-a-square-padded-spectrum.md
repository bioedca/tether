<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0089 — The similarity prealign samples a square, padded, emphasised spectrum

- **Status:** accepted; amends [ADR-0013](0013-fourier-mellin-similarity-prealign.md) on the spectrum sampling, the band-pass default and the stated regime
- **Date:** 2026-11-13
- **Deciders:** bioedca
- **PRD anchor:** Appendix E Stage 7 (4-DOF similarity prealign), §11.2 (prealign row)
- **Milestone:** M18

## Context and problem statement

Issue #780: `estimate_similarity_prealign` (ADR-0013, `tether extract --prealign similarity`)
returned a scale and a rotation near identity for the small warps that ADR-0013 calls its
validated regime. On a non-saturated synthetic bead field, scale 1.01 and rotation 1° came back
as scale 1.0 and rotation 0° or 0.14°. Only rotations of about 10° were found, and on a
non-square image (the channel half that `extract_movie` passes) not even those. ADR-0013 said
the recovery "is unit-tested", but no test asserted a recovered rotation or scale. Why did the
estimator miss small warps, and what does the real bead crop prove?

## Decision drivers

- Recover the near-identity warp that dual-view registration needs, on square and on
  channel-half images (#780 acceptance items 1 and 2).
- Keep the method a Fourier-Mellin pass, the analogue of Deep-LASI
  `imregcorr(...,'similarity')` (ADR-0013), with the same API and the same four-candidate
  masked-NCC disambiguation.
- Never fit a limit to an implementation (AGENTS.md): a limit that a measurement cannot reach
  is the maintainer's decision.

## Considered options

Four causes were found by experiment (`scikit-image` 0.26.0, SciPy 1.18.0, NumPy 2.1.3, the
base lock):

1. **Non-square grid.** The FFT of an `h × w` image has frequency steps `1/h` and `1/w`. A
   rotation of the image is then not a rotation of the spectrum in index space, so a
   non-square image cannot work.
2. **Coarse sampling.** The log-polar grid had `h` angular samples (1.4° each at 256 rows),
   a radius of `h // 8` (only the lowest frequencies) and nearest-neighbour interpolation, so
   the upsampled phase correlation could not see a sub-step shift.
3. **The band-pass removed the signal.** A difference of Gaussians with a 3 px low cut keeps
   almost nothing above 0.05 cycles/px, where the angular and radial shifts of a 1° or 1 %
   warp are largest.
4. **A fixed envelope pinned the result.** The DC peak, the window spectrum and the bead
   profile do not rotate or scale with the warp. Plain cross-correlation (`normalization=None`)
   of the log-polar images therefore peaked at zero shift.

Options:

- **A. Fix the sampling, keep the method.** Zero-pad the windowed image into a square of twice
  its longer side; multiply the magnitude by the Reddy-Chatterji high-pass emphasis
  `(1 − X)(2 − X)`, `X = cos(πu) cos(πv)` (Reddy & Chatterji 1996, already cited by ADR-0013);
  log-polar resample out to the Nyquist radius with cubic interpolation; phase-correlate with
  `normalization='phase'`; lower the band-pass low cut default from 3 px to 1 px.
- **B. Add an intensity-based refinement** (maximise the masked NCC over the four parameters
  after the Fourier-Mellin pass). Rejected: it is a second method, not the Deep-LASI analogue,
  and on the real crop the NCC optimum was scale 0.9997, so it does not reach the old limit
  either.
- **C. Document the limit only** (the alternative #780 allows). Rejected by the maintainer's
  choice below.

Without the band-pass (only the emphasis), the synthetic field was recovered but the
saturated real crop failed (rotation off by 13.5°). The high cut (20 px) is needed for
saturated data, and with it the low cut must be small: at 3 px the synthetic recovery fails
again; at 0.5, 1 and 1.5 px it passes.

## Decision outcome

Chosen: **A**, with `low_sigma` = 1 px (`ExtractOptions.prealign_low_sigma` and the function
default; `high_sigma` stays 20 px).

Measured on the synthetic field of `tests/test_register_prealign.py` (shapes 256 × 256 and
512 × 256, 140 and 400 beads, seeds 0–4, translation (6, −3) px, 11 warps from identity to
scale 1.02 and rotation 3°): the worst scale error is 0.0009 and the worst rotation error
0.09°. The tests assert 0.003 and 0.3° (a few steps; one step is 0.07° and 0.0011 in scale at
256 px). A call takes about 1.5 s at 512 × 256, against a fraction of a second before; the
default `translation` prealign does not run it.

**The real crop.** `bead_prealign_oracle.npz` is a 256 × 256 crop of a saturated `uint8`
display export. Its ground truth from the `.tmap` is scale 1.00114, rotation 0.038°. The new
estimator gives scale 0.9978 and rotation −0.21° (0.25° from the ground truth), and a
translation 0.77 px from the ground truth.
The crop cannot resolve scale to the 0.0005 limit #734 set:

- detection finds 83 donor and 65 acceptor spots, of which 14 pair within 1.5 px under the
  ground-truth map; a least-squares similarity on them gives scale 1.0028, and a 300-sample
  bootstrap of that fit a scale SD of 0.0023;
- seven correct variants of the method gave 0.9968 to 1.0120 on the crop;
- the old estimator's 1.00135 was one scale step (0.0014) above 1.0, not a measurement — the
  same estimator returns 1.0 or 1.0014 for every synthetic warp of #780.

The maintainer chose (decision card, 2026-11-13 15:12Z, "Restore old 0.005 crop scale limit"):
the crop test keeps its rotation (0.5°) and translation (1.5 px) limits; its scale limit returns
to 0.005, the value before #734; the "scale is not 1.0" line is removed. Scale recovery is
proven on the synthetic field instead. A raw, non-saturated bead movie would make a stronger
real oracle; it is not needed for this decision.

**Regime.** Rotations up to a few degrees and scales within about 2 % of unity, on square and
channel-half images. Rotations near ±90° stay out of scope (ADR-0013).

### Consequences

- Good: `--prealign similarity` now finds the rotation and scale it claims; the strict
  expected failure that #734 left is removed; the tests assert recovered values.
- Trade-off: the similarity prealign is slower (a 2× padded square spectrum). The real crop
  checks scale only to 0.005, because that is what it can show.
- Follow-up: the PRD §11.2 prealign row and `docs/reference/parameters.md` state the new
  `low_sigma` default; `tests/test_docs_parameter_defaults.py` ties them to the source.

## More information

Issue #780 (found by #734, finding IMG-F9); ADR-0013; ADR-0012; PRD Appendix E Stage 7 and
§11.2; `src/tether/imaging/register.py` (`estimate_similarity_prealign`);
`tests/test_register_prealign.py`; `tests/fixtures/PROVENANCE.md`;
`scripts/make_bead_prealign_fixture.py`. Reddy, B. S. & Chatterji, B. N. (1996), "An FFT-based
technique for translation, rotation, and scale-invariant image registration", the high-pass
emphasis filter (cited by ADR-0013).
