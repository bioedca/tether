<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Test-fixture provenance

Small, defensible fixtures **derived** from the read-only `example-data/`
sibling (the real Mondragón-Lab acquisitions cited in `docs/PRD.md`). The large
originals — the ~0.9 GB UCKOPSB movie, its full `.tdat` and the video-010 `.tmap` — are
**never** committed (PRD §8 NFR-FIXTURES). The one committed `.tmap` is the bead map, in the
Git LFS tier ([Bead `.tmap`](#bead-tmap-decoder-input-lfs)). Regenerate with:

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
| `tests/fixtures/bead_prealign_oracle.npz` | 4-DOF prealign oracle (M1 S5b registration) | `…20250721…15-36.tmap` + `map.tif` | 3,884,619 B + 792,946 B | 104,307 B | `a993036a53d1d492…` + `b538b539ee75add3…` | 2 bead-channel crops + `.tmap` ground truth |
| `tests/fixtures/large/bead_prealign.tmap` | Deep-LASI `.tmap` decoder input (`read_tmap`, #733) | `…20250721…15-36.tmap` | 3,884,619 B | 3,853,675 B | `a993036a53d1d492…` | acquisition directory redacted (same length) · **LFS** |
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

## Bead `.tmap` (decoder input, LFS)

`tests/fixtures/large/bead_prealign.tmap` (3,853,675 B, sha256
`8dc33e5c08ff765cf34940ec2e89b72cad3c19abecb7fa2f718f544a4aa46b9b`, Git LFS) is the bead
`.tmap` of the section above (source sha256 `a993036a…`, verified when it was copied from the
maintainer's workstation on 2026-11-11). It lets `read_tmap` run on a real file: the
`large-fixtures` job decodes it and compares the result with `bead_prealign_oracle.npz`
(`tests/test_register.py`), and runs one `tether extract --tmap` end to end
(`tests/test_extract_cli.py`). **Transformation:** the source stores the absolute acquisition
directory in six strings; each is replaced by a placeholder of the same byte length and the
element that holds them is recompressed. No other byte changes, and `read_tmap` decodes the copy
to exactly the same channels. The 1 MB plain-git limit does not permit a plain commit, so the
file is in LFS (#733, maintainer decision B1). **License:** GPL-3.0-or-later (Mondragón Lab,
Northwestern), the licence of its decoded numbers. Regenerate with
`scripts/make_bead_tmap_fixture.py` (the output SHA-256 can change with the zlib version; the
decoded content cannot).

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

## kinSoftChallenge simulated datasets (M8 kinetics oracle) — **CC-BY-4.0**

Unlike every fixture above (Mondragón-Lab data, GPL-3.0-or-later), these are
**external third-party data under a different license**: the kinSoftChallenge
simulated datasets [Götz2022], deposited by **Markus Götz & Sonja Schmid** at
Zenodo **doi:10.5281/zenodo.5701310** under **CC-BY-4.0**. They are annotated
CC-BY-4.0 in `REUSE.toml` (an `override` entry) and attributed in `NOTICE`;
attribution travels with them. The `MATLAB_simulator.zip` from the same record is
**not** vendored (PRD N2 — Tether ships no data simulator).

- `tests/fixtures/large/kinsoft_sim.hdf5` (2,088,914 B · **LFS**) — the three
  simulated datasets repacked into one HDF5, one group per difficulty level:
  `level1` (`Fig2`, 75 traces, dt 0.2 s), `level2` (`Fig3`, 150, dt 0.1 s),
  `level3` (`Fig4`, 250, dt 0.2 s). Each group holds `idd`/`ida`/`iaa`/`fret_e`
  as `(n_traces, max_frames)` float32 arrays zero-padded past each trace's
  `length`, plus the source-zip name and its SHA-256. It backs the M8
  kinetics-validation oracle (PRD §8 NFR-VALID(c), §9 M8): fitted rates on a level
  must fall within that dataset's reported inter-tool spread (**advisory**).
- `tests/fixtures/kinsoft_trace_sample.txt` (1,979 B · plain git) — one raw
  challenge trace verbatim (`sim_dataset_Fig4/sim_level3_final_publish/trace_58.txt`,
  42 frames), so the raw `.txt` reader (`tether.io.read_kinsoft_trace`) is
  exercised in the required matrix. `test_kinsoft_io.py` also asserts it equals
  its row inside the packed fixture.

| Source (in the Zenodo record) | Size | SHA-256 |
|---|---|---|
| `sim_dataset_Fig2.zip` | 859,187 B | `07af3ab1b7e7427ab93b423fbc302cac112ea4487391c5cfdbc9b6668827344c` |
| `sim_dataset_Fig3.zip` | 1,267,442 B | `f738698227eef76a2d1cd0118ca81453233a9d913de12ee2e6266888ee9eb284` |
| `sim_dataset_Fig4.zip` | 948,240 B | `cfecd68f8917b7e96d5f8eb1c838a1a0462f554d409ffc9671c072ba626d6425` |

**Accessed:** 2026-07-13 (downloaded from Zenodo). **License:** CC-BY-4.0
(Götz & Schmid) — **not** GPL-3.0. Regenerate by downloading the three zips from
the record, then:

```sh
uv run --no-project --with h5py --with numpy \
    python scripts/make_kinsoft_fixture.py --source <dir-with-the-3-zips>
```

Both files, and the three source zips, have entries in `tests/reference_data/manifest.toml`
(ids `kinsoft-sim-packed`, `kinsoft-trace-sample`, `kinsoft-sim-fig2` to `kinsoft-sim-fig4`;
ADR-0073). The weekly `reference-data` job downloads the zips, verifies the checksums above, and
compares `kinsoft_trace_sample.txt` with its member of `sim_dataset_Fig4.zip` (verified again on
2026-11-11).

### kinSoftChallenge ground-truth kinetic models (M8 kinetics oracle)

The **blind-challenge** trace files above carry no ground truth. The M8 within-spread
oracle (`schema/kinsoft_reference.json`, PRD §11.2, ADR-0048) needs the simulated
datasets' ground-truth rate constants and the reported inter-tool spread; both are
**transcribed from the paper's open-access Supplementary Information** [Götz2022]
(doi:10.1038/s41467-022-33023-3, CC-BY — numeric facts, not a copy of the expression):

- **Suppl. Table 5** (simulation parameters, kinetic-model diagrams) → the 2-state
  Fig. 2 ground truth **k₁₂ = 0.15 s⁻¹ (low→high), k₂₁ = 0.22 s⁻¹ (high→low)**
  (sampling 5 s⁻¹, 75 traces), and the Fig. 3 (3-state) diagram.
- **Suppl. Table 1** (Fig. 4 ground-truth rate matrix) → the full 4-state level-3
  model (k₁₂ = 0.053, k₁₄ = 0.018, k₂₁ = 0.080, k₂₃ = 0.250, k₃₂ = 0.680,
  k₄₁ = 0.032 s⁻¹; all other off-diagonal rates 0).
- **Main text** → the reported inter-tool spread: the 14 analyses inferred 2-state
  rates within a maximum **12 % of ground truth** (5 % average), above a ≥ 3 % (1σ)
  finite-dataset floor — the advisory acceptance band.

`schema/kinsoft_reference.json` is Tether's own (GPL-3.0) compilation of these
values with full source provenance; only `level1` has an active oracle (a FRET-only
idealizer cannot resolve the overlapping-FRET 4-state case — the paper itself uses
cumulative dwell-time distributions there).

## Source Data of Campos-Chavez et al. 2024 (paper reference data) — **CC-BY-NC-ND-4.0**

**External data under a licence that permits no shared Adapted Material.** The Source Data of the maintainer's
study (Campos-Chavez E, Paul S, Zhou Z, Alonso D, Verma AR, Fei J, Mondragón A,
*Nature Communications* 15:6592 (2024), **doi:10.1038/s41467-024-50885-x**) is published by Springer
Nature under **CC BY-NC-ND 4.0**. The files below are **unchanged** members of the archive
`41467_2024_50885_MOESM4_ESM.zip`, byte for byte; nothing was converted, cropped, corrected or
re-encoded, and under the project rule of ADR-0083 none may be. That rule is stricter than the
licence, which permits sharing the material in whole or in part and technical format modifications
(§2(a)(1)(A), §2(a)(4)), but not sharing Adapted Material (§1(a)) such as a corrected copy. They are for non-commercial use only. They are annotated
`CC-BY-NC-ND-4.0` in `REUSE.toml` and attributed in `NOTICE`; the hooks that rewrite whitespace and
ruff skip their directories, and `.gitattributes` keeps their bytes (`-text`) on every OS.

| Source archive | Size | SHA-256 |
|---|---|---|
| `41467_2024_50885_MOESM4_ESM.zip` | 94,911,677 B | `83141ce0dc63db35aae2a3f17d793df003de7460651a68b13cd3f2e6f9376c71` |

**Download URL:**
`https://static-content.springer.com/esm/art%3A10.1038%2Fs41467-024-50885-x/MediaObjects/41467_2024_50885_MOESM4_ESM.zip`
(the Source Data link of the article page; Python `urllib` with default headers answered HTTP 200).
**Retrieved:** 2026-11-12. The archive has 154 files and no top-level folder, so each committed path
below the fixture directory is the member path.

| Committed file | Archive member | Size | SHA-256 | Tier |
|---|---|---|---|---|
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/CalculateDoubleExponentialErrors.py` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/CalculateDoubleExponentialErrors.py` | 1,520 B | `0780f46cffa547b7a58a5bd8271639b78a7e4be14b7460f67c6b0b7d215b0523` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-RAG-0.out` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-RAG-0.out` | 144 B | `bb5ef7d3f8c2de204a7f6c546e3a3ad270e73c33e8c815f4bcabbab48619fd12` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-RAG-0.txt` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-RAG-0.txt` | 72 B | `653e64a5f8a6290f8d1791674979900334c43a4ef402971c2df1f1cefea5b461` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-RAG-4.out` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-RAG-4.out` | 144 B | `c1d30c7438f4bf239dbcb5a4fb8cb74f39a318daa2f6bb3f72e94aaf001800cf` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-RAG-4.txt` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-RAG-4.txt` | 72 B | `7749cb8b610e085d0e2498debbefa346ac350bf87bd5f5b2abf79917600049e7` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+NCCA-2State-0.out` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+NCCA-2State-0.out` | 145 B | `a3cee5cdb477d364849ce8629dc2ace7051e65097062241d69f9cf15ad97481c` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+NCCA-2State-0.txt` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+NCCA-2State-0.txt` | 74 B | `d5518bb602c4cebb40cb313c179c765d87789b4ec6129c2f3a1c6ca9dece58d9` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+NCCA-5.out` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+NCCA-5.out` | 144 B | `6d89cc6cf888bb7797bd4439b17e2c99b7c7718da0f1d908c679d292f3ea7f4e` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+NCCA-5.txt` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+NCCA-5.txt` | 70 B | `80b0acb9f3abe12ac30776cce6f7bd8319fffc9f2c17f3b339c4317695658ed6` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+Wt-0.out` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+Wt-0.out` | 145 B | `6b114de98635bd9660de84c99001705cce8f33fdb3dc0f7aae92a163ab120002` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+Wt-0.txt` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+Wt-0.txt` | 72 B | `84847b6caa0e99da38cb6df70e7a5cd97f7bf8825436bc22eccf9c1a172b0462` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+Wt-4.out` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+Wt-4.out` | 142 B | `15d413040cec187d01d40b6e90a9c642d54db326593e83865f89ec8269c387da` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+Wt-4.txt` | `ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/Double-Wt+Wt-4.txt` | 70 B | `ba2ff1aa1320b87ec1c723e413b41f77253bea3c414281b578e0b8bd7fbc2886` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/Kmatrix/CalculateKbounds.py` | `ErrorCalculations_CodeAndSourceData/Kmatrix/CalculateKbounds.py` | 3,995 B | `81d06b5b1be8ef29558ff9e7c91c932d9fda871bdb28aab642398c62f77e3320` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/Kmatrix/DeltaDiscriminator.out` | `ErrorCalculations_CodeAndSourceData/Kmatrix/DeltaDiscriminator.out` | 569 B | `69757bbaa36d1840bcaa2fc88e9cf7453fc2756a30930d7c54ad69626a189825` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/Kmatrix/DeltaDiscriminator.txt` | `ErrorCalculations_CodeAndSourceData/Kmatrix/DeltaDiscriminator.txt` | 58 B | `176ef1d7ac986dc96756322fb539db59eee2cfc247ef4895c03d3011b65355ef` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/Kmatrix/DeltaNCCA.out` | `ErrorCalculations_CodeAndSourceData/Kmatrix/DeltaNCCA.out` | 560 B | `b73bb640abc6fa9dd299a493f34be3d845c63785868f8cb5ff0b95a6404bb1eb` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/Kmatrix/DeltaNCCA.txt` | `ErrorCalculations_CodeAndSourceData/Kmatrix/DeltaNCCA.txt` | 64 B | `630ba34cffaf35051091dd8cbe0357d81f4a427040c7e238dd846252e27d3523` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/Kmatrix/LinkerMutant.out` | `ErrorCalculations_CodeAndSourceData/Kmatrix/LinkerMutant.out` | 958 B | `cacf2aa7aac53a750f534e26b0605711d8f7c8355357f668674cee42d368ca5e` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/Kmatrix/LinkerMutant.txt` | `ErrorCalculations_CodeAndSourceData/Kmatrix/LinkerMutant.txt` | 137 B | `36987a7fcd37d9acb6f5ae61492d3a181411287ae15f0442e7e604be818abb13` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/Kmatrix/RAGmutant.out` | `ErrorCalculations_CodeAndSourceData/Kmatrix/RAGmutant.out` | 1,497 B | `1e820cf6eed492e6602609ab44b7751203721e3e7f37b01fe17b33e77b226a1c` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/Kmatrix/RAGmutant.txt` | `ErrorCalculations_CodeAndSourceData/Kmatrix/RAGmutant.txt` | 241 B | `ce380d66829cf948aada4f617e2453f4613b0c43d9c053c26642cd939e78f790` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/Kmatrix/Wt-Wt.out` | `ErrorCalculations_CodeAndSourceData/Kmatrix/Wt-Wt.out` | 954 B | `295844a39d61fa2572e47f6c1f13fcbe1b787f7f6cfb56b05693aa7225b71bc2` | git |
| `tests/fixtures/campos_chavez_2024/ErrorCalculations_CodeAndSourceData/Kmatrix/Wt-Wt.txt` | `ErrorCalculations_CodeAndSourceData/Kmatrix/Wt-Wt.txt` | 135 B | `d35a52f44cdcd4ff8157c045eeca5083412ba28751a159ece27b36c156f4f481` | git |
| `tests/fixtures/campos_chavez_2024/checkTDP.py` | `checkTDP.py` | 2,174 B | `5cd40c12d8f6bcc7caf611639fc63249368cfde0a1cf1647f460b87b5d7002ee` | git |
| `tests/fixtures/large/campos_chavez_2024/smFRET_SourceData/intraFRET_WT+WT_13uM.txt` | `smFRET_SourceData/intraFRET_WT+WT_13uM.txt` | 6,723,000 B | `0f4c89027159593e885e02cdc7800218aed540bc1b44ccdd29d4846064a58839` | **LFS** |

**One member is not committed:**
`ErrorCalculations_CodeAndSourceData/FractionOfClosedState/ErrorFractionOfClosedState.ipynb`
(508,299 B, `50c413e49720c4fbec076cc442c3f3a2fe8d203846e2494b9661e05297dde8c0`). Its saved cells
hold absolute paths of a personal computer, and this repository holds no private path. The
licence forbids a redacted copy, so the notebook stays in the archive only; the weekly job
verifies it there with the archive checksum.

The trace matrix is the smallest file of `smFRET_SourceData/`. Two members have that size,
`intraFRET_WT+WT_13uM.txt` and `intraFRET_WT+WT_5uM.txt`, and they are byte-identical in the
publisher's archive (both `0f4c8902…a58839`); the first in archive order is committed. A check that
reads both files from the downloaded archive gets the same traces twice.

**`Kmatrix/CalculateKbounds.py` does not parse** (`IndentationError` at line 51: five spaces where
its block has four). The publisher's file and the copy that the audit of #695 read are
byte-identical (`81d06b5b…3320`), so the fault is in the published file. The licence forbids a
corrected copy, so tests use the `.txt` inputs and `.out` outputs and never import the script.

Every file has an entry in `tests/reference_data/manifest.toml` (ids `campos2024-…`, with
`extract_member`), and the archive has the `download` entry `campos2024-source-data`. The weekly
`reference-data` job downloads the archive, verifies the checksum above, and compares each committed
file with its member (`tests/test_reference_data_download.py`).

## Git-LFS gated tier (`tests/fixtures/large/`)

`smd_281mol.hdf5` (the redistributable ≥50-molecule population SMD) and its
paired `model_281mol.hdf5` (4-state consensus vbHMM) are tracked by Git-LFS via
`.gitattributes` (`tests/fixtures/large/**`). They back the M0.5/M6
idealization-parity gate and are **not** pulled by the default CI checkout, so
the required `test` matrix never depends on them (their load test is
`@pytest.mark.large` and skips on an unmaterialized LFS pointer). The
kinSoftChallenge `kinsoft_sim.hdf5` (above) is tracked the same way.

## M1 extraction-vs-Deep-LASI acceptance measurement (M1 S9 PR-C2)

The §9 M1 acceptance oracle (`tether.project.oracle`) was run on the **uncommitted**
gated UCKOPSB pair via `scripts/run_m1_oracle.py` (imported `.tmap` leg), recorded here
as the durable measurement (the movie is never committed — PRD §8 NFR-FIXTURES):

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

## Tutorial movie (docs asset)

`docs/tutorial/data/tutorial_movie.tif` is not a test fixture derived from
`example-data/`. It is **first-party synthetic output** of
`scripts/make_tutorial_movie.py`, derived from no external dataset, and is the
sample movie the guided first run (`docs/tutorial.md`, issue #176) starts from. It
lives under `docs/` so the published site serves it next to the page, with no
external download host. Its licence is the repository's REUSE blanket entry,
`GPL-3.0-or-later`, with no separate annotation needed.

Shape `(24, 40, 80)` big-endian `uint16`, 157,674 B: three colocalized
donor/acceptor pairs with a known +1 px x translation between the halves, seeded
Poisson noise (`numpy.random.default_rng(176)`), two molecules with two-state FRET
changes and one whose donor and acceptor brighten together. The script's docstring
records every parameter. Regenerate, or verify the committed bytes, with:

```sh
python scripts/make_tutorial_movie.py
python scripts/make_tutorial_movie.py --check
```

`tests/test_docs_tutorial.py` runs the `--check` derivation on every OS in the
required matrix.

## MATLAB reference outputs (#700, ADR-0074)

Outputs of the MATLAB reference tools live under `tests/fixtures/reference/<tool>/<run>/`, each
directory with a `provenance.json` that states the tool commit, the MATLAB release, the input and
output SHA-256 values and the method (`scripts/reference/matlab/`). `tests/test_matlab_reference.py`
verifies every committed record. `stand-in/selftest` is not a reference: the `matlab` stand-in of
the tests (`tests/_matlab_standin.py`) made it, against a one-file repository, so that the scan
always has one record to check.
