<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Test-fixture provenance

Small, defensible fixtures **derived** from the read-only `example-data/`
sibling (the real Mondragón-Lab acquisitions cited in `docs/PRD.md`). The large
originals — the ~0.9 GB UCKOPSB movie and full `.tdat`/`.tmap` — are **never**
committed (PLAN §2.1, §2.2). Regenerate with:

```sh
uv run --no-project --with h5py --with tifffile --with numpy \
    python scripts/make_fixtures.py
```

| Fixture | Purpose | Source file | Source size | Fixture size | Source SHA-256 | Notes |
|---|---|---|---|---|---|---|
| `tests/fixtures/movie_be_64x64x50.tif` | big-endian movie crop (M0 open; M1/M2 smoke) | `Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif` | 891,955,083 B | 417,990 B | `c4293f00ed2ac72d…` | cropped |
| `tests/fixtures/smd_4mol.hdf5` | 4-molecule tMAVEN SMD (M0.5/M2 parity) | `video10.hdf5` | 87,551 B | 87,551 B | `227930deeb3ca03d…` | verbatim copy |
| `tests/fixtures/smd_2mol.hdf5` | 2-molecule tMAVEN SMD (import round-trip) | `video 25,26,27,28.hdf5` | 61,780 B | 61,780 B | `95439c4bc54063a9…` | verbatim copy |
| `tests/fixtures/large/smd_281mol.hdf5` | 281-molecule population SMD (parity gate) | `model-source-smd-281mol.hdf5` | 3,925,718 B | 3,925,718 B | `286130c45a679263…` | verbatim · **LFS** |
| `tests/fixtures/large/model_281mol.hdf5` | 4-state vbHMM model (parity gate) | `model.hdf5` | 2,621,011 B | 2,621,011 B | `8f78fa48ad0311fd…` | verbatim · **LFS** |
| `tests/fixtures/aperture_oracle.npz` | aperture Sum-integration oracle (M0.5 S5) | `…010.tif` + `…010.mat` | 891,955,083 B + 9,053,155 B | 460,472 B | `c4293f00ed2ac72d…` + `af1b5be33aa63f87…` | 6 donor crops + `don` oracle |
| `tests/fixtures/tdat_coloc_slice.tdat` | TIRFdata colocalization + detection slice (M0.5 S6 / M1 S9 decode) | `…010.tif…00-00.tdat` | 37,039,831 B | 131,296 B | `b6a911d48bc27cd1…` | coloc table + `ParticleDetectionMode` + MCOS `Channel` blob (per-channel `DetectionThreshold`) |
| `tests/fixtures/tmap_coeffs.npz` | Deep-LASI `.tmap` registration coefficients (M0.5 S6 registration) | `…20250718…13-40.tmap` | 3,872,385 B | 5,905 B | `7db0cf80d161847e…` | decoded degree-2 coeffs only |
| `tests/fixtures/acceptor_oracle.npz` | acceptor aperture-integration oracle via `.tmap` apply (M0.5 S5/S6) | `…010.tif` + `…010.mat` + `…13-40.tmap` | 891,955,083 B + 9,053,155 B + 3,872,385 B | 271,214 B | `c4293f00ed2ac72d…` + `af1b5be33aa63f87…` + `7db0cf80d161847e…` | 6 acceptor crops + `acc` oracle |
| `tests/fixtures/bead_prealign_oracle.npz` | 4-DOF prealign oracle (M1 S5b registration) | `…20250721…15-36.tmap` + `map.tif` | 792,946 B + 3,884,619 B | 104,307 B | `b538b539ee75add3…` + `a993036a53d1d492…` | 2 bead-channel crops + `.tmap` ground truth |
| `tests/fixtures/deeplasi_export_slice.mat` | Deep-LASI `.mat` reader slice (M1 S9 oracle) | `…010.mat` | 9,053,155 B | 8,127 B | `af1b5be33aa63f87…` | 4 mol × 80 frames: `fret_pairs` + 6 trace arrays; v5, real `movie_name` filename + redacted `movie_path` directory |
| `tests/fixtures/deeplasi_traces_slice.txt` | Deep-LASI `…-donc-accc-w.txt` reader slice (M1 S9 oracle) | `…010-donc-accc-w.txt` | 7,739,364 B | 6,412 B | `0892ae965a947003…` | first 80 frames × 8 cols (4 mol, donor/acc interleaved) |

**Accessed:** 2026-06-22 (date `example-data/` was gathered onto this
workstation; see its `README.md`). **Origin:** Mondragón Lab (Northwestern)
smFRET acquisitions — the project's own data, vendored here as test fixtures.
**License:** `GPL-3.0-or-later`, with the rest of the repository (REUSE blanket).

## Movie crop

`movie_be_64x64x50.tif` is the **brightest 64×64 window**
(top-left pixel `(row=375, col=432)`) of the first
50 frames of the source movie, kept **big-endian uint16**
(shape `(50, 64, 64)`, axes `TYX`) so it exercises the M0 S7 big-endian reader and
the napari open path, and contains real molecules for M1/M2 extraction smoke.

## Aperture-integration oracle

`aperture_oracle.npz` validates the 21×21 aperture + Sum integration
(`tether.imaging.aperture`) against Deep-LASI on real data. It holds 6 donor-spot
crops (`crops`, shape `(6, 120, 21, 21)`, big-endian `uint16`) taken from the
first 120 frames of the source movie around the donor coordinate of 6 molecules,
each paired with that molecule's raw integrated donor trace `don_ref`
(`(6, 120)`) from the `.mat` export — the integration oracle. Also stored:
`molecule_index` (the source-`.mat` molecule index), `full_xy` (the 0-based
`[col, row]` of each spot in the full 512×512 frame), and `local_center`
(`[10, 10]`, the spot's `(row, col)` in every crop). The molecules are those
whose faithful donor integration correlates ≥ 0.9 with the oracle, ranked and the
top 6 kept (`donor-corr` 0.992–0.994). The acceptor channel is
registration-mapped (a `.tmap` apply) and is **not** in this fixture — it rides
the M0.5 S6 `.tdat`/`.tmap` decode. Regenerate with
`scripts/make_aperture_fixture.py`.

## TIRFdata colocalization slice

`tdat_coloc_slice.tdat` is a tiny **MATLAB v7.3** (`.tdat`-format) file derived
from the 37 MB UCKOPSB `.tdat`, holding the real `ParticlesColocalized` matrix
(250 molecules × 17 columns) reached through the same cell → object-reference →
`#refs#` path as the original, plus the three `Default{Alpha,Beta,Gamma}` scalars
(all `0` for this acquisition), the `ParticleDetectionMode` leaf (mode 2,
intensity), and the MCOS `Channel` object blob needed to decode the per-channel
`DetectionThreshold` (M1 S9 PR-C3c-decode-B). The MCOS retention is faithful, not
a stub: the real `#subsystem#/MCOS FileWrapper__` metadata and `temp/Channel`
object-reference markers are copied **verbatim**, along with every FileWrapper
heap cell small enough to be a scalar/short-vector value; the ~37 MB of
trace/patch arrays and the large per-channel images are dropped as null cells,
preserving cell indices so `tether.io.mcos` decodes it through the identical
`value + 2` heap path it walks on the real file. It stays in plain git yet
faithfully exercises the `tether.io.read_tdat` / `read_detection_settings`
decoders (coordinates + the Appendix-B factor remap + detection mode/threshold) in
the required test matrix. Regenerate with `scripts/make_tdat_fixture.py`.

## Deep-LASI `.tmap` registration coefficients

`tmap_coeffs.npz` holds the decoded degree-2 channel-registration transforms from
the 3.7 MB Deep-LASI `.tmap` (a classic MATLAB v5 MAT-file whose coefficients live
in the MCOS `__function_workspace__` blob). `scripts/make_tmap_fixture.py` runs the
real decoder (`tether.imaging.register.read_tmap`) and stores, per channel, the
`images.geotrans.PolynomialTransformation2D` coefficient vectors `A`/`B` (6 each)
and the input/output normalisation affines for both directions
(`ref_to_channel`, `channel_to_ref`), plus the crop rect and `MapParticles` bead
control points. The bulky per-channel result image is dropped, so it stays in
plain git yet lets the required test matrix validate the registration
*independently* of the source file: a native degree-2 fit from the committed
`.tdat` molecule pairs reproduces this imported map to RMS ≈ 0.43 px, agreeing
with it to within 1 px at 99 % of the Deep-LASI molecule positions (the §9
M0.5(b) registration-residual gate, ≤ 0.5 px). This is registration
*faithfulness*, not colocalization recall — the "≥ 95 % of molecules matched
within 1 px" recall gate needs the M1 detection + colocalization pipeline (the
`.tmap`'s own residual to the actual acceptor molecules is median > 1 px). The
`read_tmap` decoder (which leans on a scipy private MAT-5 reader, validated
against **scipy 1.18.0**) is re-checked against this fixture by a
data-present-only test (skipped when the external `.tmap` is absent, e.g. the
default CI checkout). Regenerate with `scripts/make_tmap_fixture.py`.

## Acceptor-intensity oracle

`acceptor_oracle.npz` completes the M0.5(b) aperture-integration comparison for
the **acceptor** channel (the donor half is `aperture_oracle.npz`). The acceptor
is reached through the registration: each donor coordinate is warped into the
acceptor channel's full-frame position with
`tether.imaging.register.TmapChannel.reference_to_channel_image` (which folds in
the acceptor crop origin `[256, 0]` — Deep-LASI registers channel-local
sub-images, `tools/processImage.m`), then a 21×21 aperture is Sum-integrated there
and compared to the `.mat` raw acceptor trace `acc`. It holds 6 acceptor-spot
crops (`crops`, shape `(6, 70, 21, 21)`, big-endian `uint16`) from the first 70
frames around each warped position, each paired with the molecule's raw acceptor
trace `acc_ref` (`(6, 70)`); also `molecule_index`, `pacc` (the acceptor
first-bleach frame), `warped_xy` (full-frame `[x, y]`, all in the acceptor half
`x > 256`), `snapped_xy` (the crop-centre pixel), and `local_center` (`[10, 10]`).
**Acceptor signal is sparse in this field.** Under donor-only excitation (no ALEX
here, `Default Alpha = 0`), acceptor emission arises from FRET and is low or
absent when FRET is low or the acceptor is dark/bleached (Roy, Hohng & Ha 2008,
*Nat. Methods*; Hellenkamp et al. 2018, *Nat. Methods*) — so across all 250
molecules the median correlation is ≈ 0.50 and a broad high-correlation gate like
the always-bright donor's is not achievable here. The fixture
therefore keeps the **strongest-acceptor-signal** molecules (top pre-bleach `acc`
std), validated over the **pre-acceptor-bleach window** where the signal exists —
the `.tmap` apply + aperture recovers the Deep-LASI acceptor intensity (median
corr ≈ 0.85, best ≈ 0.99 across the committed set). This is the *loose* M0.5
preview; the strict broad gate is M1 (PRD §4 M1 / §9 M1). Regenerate with
`scripts/make_acceptor_fixture.py`.

## 4-DOF prealign oracle

`bead_prealign_oracle.npz` validates the M1 S5b Fourier-Mellin similarity
prealign (`tether.imaging.register.estimate_similarity_prealign`, the faithful
analogue of Deep-LASI `imregcorr(...,'similarity')`, `createMapPhaseCorr.m:11`).
It is derived from the **bead-calibration** acquisition staged by the maintainer
(closing the data-gap ADR-0012 flagged):

- Source `map.tif` (792,946 B, sha256 `b538b539ee75add3509f2540c6182df80608b4f047c432588a5829eecf1d5d90`)
  — a single 512×512 contrast-stretched `uint8` bead field, split left/right at
  column 256 (donor cols 0–255, acceptor 256–511), saved as 3 byte-identical
  pseudo-RGB planes.
- Source `…20250721_2025-07-21_15-36.tmap` (3,884,619 B, sha256
  `a993036a53d1d4920fe2a1f8409a33889d0f72ca417162efc910845bc9c3f462`) — the
  paired Deep-LASI registration map (decoded by `read_tmap`).

The fixture holds the two real 256×256 centred channel crops (`donor`,
`acceptor`, `uint8`) plus the **ground-truth acceptor→donor similarity** in the
crop's local `[x, y]` frame (`gt_scale` 1.00114, `gt_rotation_deg` +0.0382,
`gt_translation` ≈ `[-7.59, -1.91]`), obtained by sampling the decoded `.tmap`
polynomial on a grid and least-squares (Umeyama) fitting a similarity (grid fit
residual 0.192 px); also `donor_crop_offset` `[0, 128]` and `acceptor_crop_offset`
`[256, 128]` (`[col, row]`). The estimator recovers this on the real pair to
Δscale 0.0002, Δrotation 0.04°, Δtranslation 0.33 px.

**Validation scope.** `map.tif` is a contrast-stretched, partly-saturated display
export — its FFT magnitude is DC-dominated, so it can only validate the
near-identity *real* relationship (the crop's ground truth above — rotation
~0.04°, scale ~0.11% off unity), not
large-warp *recovery* (verified: saturating an otherwise-recoverable synthetic
bead field to `uint8` breaks Fourier-Mellin recovery). Large-warp recovery of the
estimator is therefore unit-tested separately on a deterministic non-saturated
synthetic bead field (`tests/test_register_prealign.py`); this fixture proves the
estimator reports the **true similarity on real bead images**. **Accessed:**
2026-06-27. **License:** GPL-3.0-or-later (Mondragón Lab, Northwestern).
Regenerate with `scripts/make_bead_prealign_fixture.py`.

## Deep-LASI export slice (M1 S9 extraction oracle)

`deeplasi_export_slice.mat` and `deeplasi_traces_slice.txt` are the committed,
plain-git inputs for the `tether.io.deeplasi` validation reader — the reader that
supplies Deep-LASI's own result as the ground-truth oracle for the M1
extraction-vs-Deep-LASI acceptance gate (PRD §9 M1, §8 NFR-VALID (a)). The full
exports (≈ 9 MB `.mat` + ≈ 7.7 MB `.txt`) stay external; each slice keeps the
**first 4 molecules × first 80 frames** of the real
`DeepLASI_MAT_export_…010.mat` / `…-donc-accc-w.txt`:

- `deeplasi_export_slice.mat` — `fret_pairs` (4×4 donor/acceptor pixel
  coordinates) + the six `(4, 80)` trace arrays (`don`/`donc`/`bdon`,
  `acc`/`accc`/`bacc`), re-saved as a compressed MATLAB **v5** `.mat` (the on-disk
  format of the real export, `format='5'`). The real `movie_name` (the
  source-movie **filename**) is committed verbatim; `movie_path` — which in the
  real export is an **absolute workstation directory** (a distinct field) — is
  **redacted** to `<redacted-directory>`. `exportedby` (`TRacer_v1`) is kept
  verbatim.
- `deeplasi_traces_slice.txt` — the matching first 80 frames × 8 columns (the same
  4 molecules, donor/acceptor interleaved) at the source's 5-decimal text
  precision. By construction it equals the `.mat` `donc`/`accc` to that rounding,
  the same identity the data-present test locks across all 250 molecules of the
  full export.

**Accessed:** 2026-06-22. **License:** GPL-3.0-or-later (Mondragón Lab,
Northwestern). Regenerate with `scripts/make_deeplasi_fixture.py`.

## Git-LFS gated tier (`tests/fixtures/large/`)

`smd_281mol.hdf5` (the redistributable ≥50-molecule population SMD) and its
paired `model_281mol.hdf5` (4-state consensus vbHMM) are tracked by Git-LFS via
`.gitattributes` (`tests/fixtures/large/**`). They back the M0.5/M6
idealization-parity gate and are **not** pulled by the default CI checkout, so
the required `test` matrix never depends on them (their load test is
`@pytest.mark.large` and skips on an unmaterialized LFS pointer).

## M1 extraction-vs-Deep-LASI acceptance measurement (M1 S9 PR-C2)

The §9 M1 acceptance oracle (`tether.project.oracle`) was run on the **uncommitted**
gated UCKOPSB pair via `scripts/run_m1_oracle.py` (imported `.tmap` leg), recorded here
as the durable measurement (the movie is never committed — PLAN §2.2):

- **Source:** `Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif` (1700×512×512, 891,955,083 B,
  SHA-256 `c4293f00ed2ac72d…`) + `DeepLASI_MAP_…20250718…13-40.tmap`
  (`7db0cf80d161847e…`) + `DeepLASI_MAT_export_…010.mat` (`af1b5be33aa63f87…`, 250 mol).
- **Measured (raw integrated intensity, 1 px match):** recall **0.204** (51/250);
  donor Pearson r median 0.988, acceptor 0.876; coord RMS of the 51 matched 0.289 px.
- **Verdict: §9 M1 acceptance NOT met.** Diagnosed to the **detection stage** (coordinate
  frames perfectly aligned; raw donor detection finds 199 spots, only 51/250 within 1 px
  of Deep-LASI). The M1 detector was validated only on the 64×64×50 single-block fixture,
  so the multi-block max-projection at full scale diverged silently. **M1 close +
  `v0.1.0` are deferred** (ADR-0020); the gated acceptance test is `xfail` until the
  detector is fixed and re-measured to the **full** §9 M1 gate — recall ≥ 0.95 @ 1 px
  **and** per-frame intensity Pearson r ≥ 0.99 **and** registration RMS ≤ 0.5 px (the
  native `.tmap` fit, also locked by `test_register`).
- **Accessed:** 2026-06-30. Re-measure all three gates with `scripts/run_m1_oracle.py`
  against the local `example-data/bla-uckopsb-tbox-video10/`.
