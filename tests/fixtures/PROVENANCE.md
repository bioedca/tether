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
values with full source provenance; only `level1` has an active, band-checked oracle.
Levels 2 and 3 are fitted and reported, never band-checked (#717, ADR-0081).

### kinSoftChallenge simulator settings (the ground truth of levels 2 and 3, #717)

Since #717 the ground truth of levels 2 and 3 is read, not transcribed: the record's
`MATLAB_simulator.zip` holds the settings file the simulator read for each level, and
`tests/_kinsoft_config.py` parses it. The three files are copied **verbatim** (byte for
byte, `-text` in `.gitattributes`, outside the whitespace hooks):

- `tests/fixtures/kinsoft_simulator/Config_simulation_round_1_level_1.cfg` (2,942 B)
- `tests/fixtures/kinsoft_simulator/Config_simulation_round_1_level_2.cfg` (3,074 B)
- `tests/fixtures/kinsoft_simulator/Config_simulation_round_1_level_3.cfg` (3,034 B)

| Source (in the Zenodo record) | Size | SHA-256 |
|---|---|---|
| `MATLAB_simulator.zip` | 26,527 B | `0a3c65403782fb1378b600fad9a26da8d99b5f955127f6ddb884afcaa3ed20b4` |

The members are `MATLAB_simulator/Config_simulation_round_1_level_N.cfg`; their own
checksums are in `tests/reference_data/manifest.toml` (ids `kinsoft-simulator` and
`kinsoft-settings-level1` to `kinsoft-settings-level3`), and the weekly `reference-data`
job compares each committed file with its member. **Accessed:** 2026-11-12 (downloaded
from Zenodo; the record's MD5 `49ef3a860a2e05d01233466ccbd06169` also matched).
**License:** CC-BY-4.0, the licence of the record. The zip also holds a `license.txt` that
grants MIT terms to Markus Götz's simulator code; Tether copies none of that code (PRD N2).

The files changed two recorded values. The level-2 entry had read the 1↔2 rates from the
Suppl. Table 5 diagram as k₁₂ = 0.05 and k₂₁ = 0.30 s⁻¹; the settings file gives
q₁₂ = 0.30 (low→mid) and q₂₁ = 0.05 s⁻¹ (mid→low), and Tether's 3-state fit agrees with
the file on which direction is faster. The level-3 rates of Suppl. Table 1 equal the
file's under a renumbering of the states (`settings_state_of` in the JSON).

## MreC-MreD ebFRET sessions (report-only ebFRET comparison, #719) — **CC-BY-4.0**

Single-molecule FRET data of the MreC-MreD study, deposited by **Gilman, Shlosman, Samé Guerra,
Domecillo, Fivenson, Bourrett, Bernhardt, Polizzi, Loparo and Kruse** at Zenodo
**doi:10.5281/zenodo.13910538** (2024) under **CC-BY-4.0**. Annotated CC-BY-4.0 in `REUSE.toml`
and attributed in `NOTICE`. The record's MATLAB code and its `smFRET histograms.xlsx` are **not**
vendored.

- `tests/fixtures/large/ebfret_mrec_sessions.hdf5` (11,729,129 B · **LFS**) — the 11 ebFRET
  session files (`Data/ebFRET outputs/<figure>/<condition>/<condition>-ebFRET.mat`) repacked, one
  group per `<figure>/<condition>`: the fitted traces (`donor`, `acceptor`, NaN-padded float64,
  with `length` and the 0-based crop `crop_min`/`crop_max`) and the stored result for the
  number of states the session shows (`@nstates`): the empirical-Bayes prior state means
  `prior_mu` and the expected-occupancy fractions `population`. Each group records its member
  name, the member's SHA-256 and the MAT-file header. One series of one frame
  (`Figure 2/A29C-T201C+MreD`) is left out (`@n_left_out`). It backs the report-only comparison
  in `tests/test_ebfret_sessions_sidecar.py`.

| Source (in the Zenodo record) | Size | SHA-256 |
|---|---|---|
| `Data.zip` | 46,017,564 B | `175ebcca622dd2e5e56d5ced2e610e236723da93570946e58d5a64f67ed9f932` |

Zenodo states MD5 `a021a0ff561013d483f220adeb1d1c91` for the zip, which the download matched.
**Accessed:** 2026-11-12. **License:** CC-BY-4.0 — **not** GPL-3.0.

The fixture reads only the session files, because each holds both the traces ebFRET fitted and the
result. In the record five `traces.dat` exports do not belong to the session beside them: the
`A29C-T201C` and `A33C-T201C` files are exchanged, so are the two `+MreD` files, and
`MreC-F36R-MreD-traces.dat` is a byte copy of `MreC-L39E-MreD-traces.dat` (their row and
molecule counts against the sessions show it). Regenerate with:

```sh
python scripts/make_ebfret_sessions_fixture.py --source <dir-with-Data.zip>
```

Both files have entries in `tests/reference_data/manifest.toml` (ids `ebfret-mrec-data`,
`ebfret-mrec-sessions`; ADR-0073). The weekly `reference-data` job downloads the zip, verifies
its checksum and compares every array of the committed fixture with a fresh extraction.

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

## RF2-ArfA data set of the tMAVEN authors (#708, ADR-0079) — **CC-BY-4.0**

External third-party data under a different license, as the kinSoftChallenge sets above: the
"RF2-ArfA smFRET data and analysis" record of **Nina Michael, Bridget Huang, Korak Kumar Ray,
Colin Kinz-Thompson and Ruben Gonzalez**, Zenodo **doi:10.5281/zenodo.17246323**, **CC-BY-4.0**.
It holds the tMAVEN SMD files and the "vb Consensus HMM" models behind [Michael2025]
(Proc. Natl. Acad. Sci. USA 122(46):e2509408122, doi:10.1073/pnas.2509408122). Both files are
annotated CC-BY-4.0 in `REUSE.toml` and attributed in `NOTICE`.

| Fixture | Archive member (below `Data/Single molecule data/`) | Member size | Member SHA-256 | Fixture size | Transformation |
|---|---|---|---|---|---|
| `tests/fixtures/rf2_core_wtprensec_model.hdf5` | `RF2_core/Steady state/HMMs/RF2_core_wtprensEC_smd_HMM.hdf5` | 499,405 B | `edeaa298604895a842c3ab7692370d555b05b2e779d7bcb8b2f81b5d01d0be7a` | 499,405 B | verbatim copy |
| `tests/fixtures/rf2_core_wtprensec_smd.hdf5` | `RF2_core/Steady state/RF2_core_wtprensEC_smd.hdf5` | 1,163,720 B | `d1025b31777317aefbf0da58c58cdc360f83fc6292f9d1b5557736c45f6683b9` | 313,247 B | windowed extract (below) |

- **Archive:** `ArfA_RF2_data.zip`, 127,792,051 B, SHA-256
  `1ae523076f89a1e28db2f2035b76ecabd3b4c24e649083158a08ca1ef975c9b8`. **Accessed:** 2026-11-12
  (downloaded from Zenodo). The record's `Data/Single molecule data/tMAVEN parameters.docx`
  (15,913 B, SHA-256 `d5df228a303c4a58be5f155ded06384ae1064f5733f7bc06a199146de5ac2e86`) gives
  the settings the checks use: bleed-through 0.035, and the 2-D histogram settings in
  `tests/_rf2_arfa_child.py`. It is not committed.
- **Build:** the files name no tMAVEN build. The SMD file is dated 2022-11-29 15:53 and the
  model 2022-11-29 16:08 (their own `date_created` and `time_made` attributes). Tether's live
  checks run the pinned tMAVEN `10f4230` (`.github/workflows/sidecar.yml`), which is a later
  build; ADR-0079 records what that difference does to the fit.
- **Windowed extract.** The SMD member is larger than the 512 KiB plain-git gate, so
  `scripts/make_rf2_arfa_fixture.py` keeps every frame inside a molecule's analysis window
  `[pre_list, post_list)` bit for bit and sets every frame outside it to `0.0`; tMAVEN and Tether
  fit and plot only the frames inside. It also replaces the authors' local path in
  `source_list`/`source_name` with the archive member name, copies every other array and
  attribute unchanged, and writes gzip level 9 with the shuffle filter. A tMAVEN fit of the
  extract equals the fit of the original file (measured 2026-11-12).

Regenerate with:

```sh
python scripts/make_rf2_arfa_fixture.py --archive /path/to/ArfA_RF2_data.zip \
    --out tests/fixtures/rf2_core_wtprensec_smd.hdf5
```

The archive and both files have entries in `tests/reference_data/manifest.toml` (ids
`rf2-arfa-archive`, `rf2-core-wtprensec-model`, `rf2-core-wtprensec-smd`; ADR-0073). The weekly
`reference-data` job downloads the archive, compares the model with its member byte for byte,
checks the SMD extract against its member as described above, and runs the import and Table 1
checks of `tests/test_rf2_arfa.py` on all 11 SMD files and 9 models.

## smfret-analysis2 test data (#709, ADR-0084) — **GPL-3.0-only**

Verbatim copies of 78 files of the `test-data/traces/` folder of
[vafabakhsh-lab/smfret-analysis2](https://github.com/vafabakhsh-lab/smfret-analysis2) at commit
`121634e9b533eb3a922fbfe9bc1e7715a9a6a6b4` (2022-05-20, the pin of `scripts/reference/matlab/pins.toml`).
**Retrieved:** 2026-11-12, `git clone` over HTTPS. **Licence:** the repository gives the GPL
version 3 text and no "or later" statement, so `GPL-3.0-only` (REUSE override). The README names
the source acquisition `20201210MRS_mCaSR_D451TAG`.

- The 76 `1 tr<N>.dat` files are the trace files: whitespace-separated columns, of which
  column 1 is time, column 2 donor and column 3 acceptor intensity. `tests/_sa2_traces.py` reads
  them; the product has no importer for them (PRD N4).
- `total_FRET.csv` and `total_XC.csv` are the outputs that this commit's `create_histogram.m`
  wrote from the 76 files with every trace included (its `skipped-traces.csv` is empty). The
  pinned commit's own scripts reproduce both, so no MATLAB run is recorded for them.
- Not copied: `test-data/ground-truth/`. Its files have no header line, a four-column
  cross-correlation and a different `raw` column; the scripts of this commit do not reproduce
  them, so they are from an older script version. The `.dat` copies of the two outputs, the
  movie (`film1.*`) and `skipped-traces.csv` are not needed.

| Fixture | Upstream path | Size | SHA-256 |
|---|---|---|---|
| `tests/fixtures/smfret_analysis2/total_FRET.csv` | `test-data/traces/total_FRET.csv` | 5,160 B | `cdffc7420060aa3db09336a9e3d8110d42e8fd8b1403f57811467070ed8a94d4` |
| `tests/fixtures/smfret_analysis2/total_XC.csv` | `test-data/traces/total_XC.csv` | 1,517 B | `81c3c616823dac1d760185d7c5441725ea57d760d0bae04e392aa810ca17fa0f` |
| `tests/fixtures/smfret_analysis2/traces/1 tr20.dat` | `test-data/traces/1 tr20.dat` | 46,075 B | `37fda8ff0e566f941dae98bcf3b48fd2fd6cef2e550dfed44a00543eca9118c0` |
| `tests/fixtures/smfret_analysis2/traces/1 tr34.dat` | `test-data/traces/1 tr34.dat` | 54,514 B | `9dad2086786b1b87dfdd2318403f8147c0e101616d5cc13d46fbb730ba749fe2` |
| `tests/fixtures/smfret_analysis2/traces/1 tr38.dat` | `test-data/traces/1 tr38.dat` | 193,612 B | `680e623c632404d2897f7c3b0471550b232c184b7ccb4e9c1a3a69dbe20b427c` |
| `tests/fixtures/smfret_analysis2/traces/1 tr40.dat` | `test-data/traces/1 tr40.dat` | 38,218 B | `50446304eab726db3998b85438e43b0cd0ad571d369050e4d9e050e28a822f4c` |
| `tests/fixtures/smfret_analysis2/traces/1 tr42.dat` | `test-data/traces/1 tr42.dat` | 26,578 B | `47ce21a389ad6ed35c5fc7c51b7eb7fdd81f50aeb149bdb043393bb274ca4757` |
| `tests/fixtures/smfret_analysis2/traces/1 tr48.dat` | `test-data/traces/1 tr48.dat` | 16,393 B | `638858170b1fb3eb2c5fb30c31b5222d30e2d880f922c035d230397ffc99032b` |
| `tests/fixtures/smfret_analysis2/traces/1 tr52.dat` | `test-data/traces/1 tr52.dat` | 39,964 B | `b80ca437020ac66531db8c3e09a711159ad3a969430113d717850423c8798736` |
| `tests/fixtures/smfret_analysis2/traces/1 tr57.dat` | `test-data/traces/1 tr57.dat` | 46,657 B | `0810019e3ed90d6c68739ee6330bb47dfe5685fe4598337ca5347d807ce15959` |
| `tests/fixtures/smfret_analysis2/traces/1 tr60.dat` | `test-data/traces/1 tr60.dat` | 72,847 B | `de4cc98ea96a7d2a34b03018f1c3a595e6dcd54a2c34ec097c166e6fc2dd8b33` |
| `tests/fixtures/smfret_analysis2/traces/1 tr61.dat` | `test-data/traces/1 tr61.dat` | 9,118 B | `be53d2540fd8aefd67c2be1a74b54cb036c38e515016dbc20e9b72861c6b34ce` |
| `tests/fixtures/smfret_analysis2/traces/1 tr67.dat` | `test-data/traces/1 tr67.dat` | 41,710 B | `fe1632ffb91c133ffcf40d1d827b58bcbd79b869ef0b462ff7fda13237735298` |
| `tests/fixtures/smfret_analysis2/traces/1 tr70.dat` | `test-data/traces/1 tr70.dat` | 40,837 B | `38c90bea853f1ab35377743771d3e866513fac91b6f32b7f95e2cca5a14be8cc` |
| `tests/fixtures/smfret_analysis2/traces/1 tr76.dat` | `test-data/traces/1 tr76.dat` | 13,192 B | `a31f9240caefd1a934dccd8456bde289153e4936b67038102afe2c185d4e1dca` |
| `tests/fixtures/smfret_analysis2/traces/1 tr77.dat` | `test-data/traces/1 tr77.dat` | 34,726 B | `a287650092d0f2fb6947330f49eab5f9ad7b1ea90ff401fe1994cc9c29bcfbcf` |
| `tests/fixtures/smfret_analysis2/traces/1 tr78.dat` | `test-data/traces/1 tr78.dat` | 34,726 B | `573ebc5e524f36c15a334320693ac92363d3428e9120eafd27ac933b7f137c53` |
| `tests/fixtures/smfret_analysis2/traces/1 tr87.dat` | `test-data/traces/1 tr87.dat` | 90,016 B | `33d11e8ed8f8b8cd59be28eaf296d6ce8928f1a4f8f294cb55e23235902cd5b9` |
| `tests/fixtures/smfret_analysis2/traces/1 tr88.dat` | `test-data/traces/1 tr88.dat` | 29,488 B | `3572329f3b2b20032bebaeb7ee8582d13c8033dd3ddeff0461ffa801c938a319` |
| `tests/fixtures/smfret_analysis2/traces/1 tr89.dat` | `test-data/traces/1 tr89.dat` | 35,308 B | `12523d67bd989f44bb18d55b34bf09646361ab37bf29fa02e45f7e20c14edff2` |
| `tests/fixtures/smfret_analysis2/traces/1 tr98.dat` | `test-data/traces/1 tr98.dat` | 111,550 B | `149af6703815ed234671a7d31a9f37ea25379289a1cd394426206522156ff754` |
| `tests/fixtures/smfret_analysis2/traces/1 tr100.dat` | `test-data/traces/1 tr100.dat` | 46,657 B | `060bb75f1bd364a789088ddbb23dd4469212978ba4a5afaf8662d7d20f0a1ee9` |
| `tests/fixtures/smfret_analysis2/traces/1 tr101.dat` | `test-data/traces/1 tr101.dat` | 48,112 B | `64fab3cc277dea4f542f627ac5f98b322ef15ee8e4d3395a62bf1d984036e116` |
| `tests/fixtures/smfret_analysis2/traces/1 tr106.dat` | `test-data/traces/1 tr106.dat` | 237,553 B | `a005d2aa227a5073aac5e55613ad981374a03527b591da538fcd537cf642744a` |
| `tests/fixtures/smfret_analysis2/traces/1 tr110.dat` | `test-data/traces/1 tr110.dat` | 54,514 B | `c1a7af43287fd087e0b6bcee16870d9006208601f80a71fb1f2e8e76ca6b20aa` |
| `tests/fixtures/smfret_analysis2/traces/1 tr114.dat` | `test-data/traces/1 tr114.dat` | 115,042 B | `c7c3b807d371d11293481647b31556f915565c15018efbea6af8d9b12cece43d` |
| `tests/fixtures/smfret_analysis2/traces/1 tr115.dat` | `test-data/traces/1 tr115.dat` | 37,054 B | `433ee6fdf595462041ab4496b9839e631990a7892813e3c7cf9bde6dca2bd03a` |
| `tests/fixtures/smfret_analysis2/traces/1 tr118.dat` | `test-data/traces/1 tr118.dat` | 116,497 B | `921a7324b1c509612ddba06a35687c0bf6a6eed59cc53c58e228882082127378` |
| `tests/fixtures/smfret_analysis2/traces/1 tr122.dat` | `test-data/traces/1 tr122.dat` | 190,120 B | `33a023566aad2a4afd3ec18fa72376875c683067bbe1b3c54520decda82377ba` |
| `tests/fixtures/smfret_analysis2/traces/1 tr123.dat` | `test-data/traces/1 tr123.dat` | 76,921 B | `3cf47f06eab66a3c299f3334f849935eacf2ba813d45bb9f6b9b9089120f7b9c` |
| `tests/fixtures/smfret_analysis2/traces/1 tr127.dat` | `test-data/traces/1 tr127.dat` | 37,054 B | `5ebecdbc9937df9d5a339180fb08e427312e3d499e9f19f484463c435b3a7a74` |
| `tests/fixtures/smfret_analysis2/traces/1 tr128.dat` | `test-data/traces/1 tr128.dat` | 78,958 B | `a8a0373d573559f68b69632dbc95681f9ee902766dd482547f467552a1ec2d4c` |
| `tests/fixtures/smfret_analysis2/traces/1 tr132.dat` | `test-data/traces/1 tr132.dat` | 28,615 B | `86e62c0ba79f0e0d9003e52471e9b2063b41d7d8841f2d05ec0dca7654924268` |
| `tests/fixtures/smfret_analysis2/traces/1 tr134.dat` | `test-data/traces/1 tr134.dat` | 63,535 B | `7620923d550deb75c59b426e1efda8156c537cd70e63932f4b54ca6f0c258cec` |
| `tests/fixtures/smfret_analysis2/traces/1 tr135.dat` | `test-data/traces/1 tr135.dat` | 109,804 B | `2672bc44959c10bc6b4b19d2e4db2a1e1649f7829a7b6956d8a5137f1e5d79c5` |
| `tests/fixtures/smfret_analysis2/traces/1 tr138.dat` | `test-data/traces/1 tr138.dat` | 103,402 B | `1fb6fe2610204d1317e41f5327fbb165bc2f470459ed2a97a43c50926963f663` |
| `tests/fixtures/smfret_analysis2/traces/1 tr140.dat` | `test-data/traces/1 tr140.dat` | 30,943 B | `2c963099ec48f566f4c825a281f34e2a89738a4c91b7537960592e53b7ead64d` |
| `tests/fixtures/smfret_analysis2/traces/1 tr145.dat` | `test-data/traces/1 tr145.dat` | 241,627 B | `37838c2667d9d1a28ab09c950cfc3d6980aebad69a07d995be4abc710d4042f6` |
| `tests/fixtures/smfret_analysis2/traces/1 tr148.dat` | `test-data/traces/1 tr148.dat` | 193,612 B | `e5934f06c7162418bad803146c724048dba62b508b212695bb78ae567e6f3dc4` |
| `tests/fixtures/smfret_analysis2/traces/1 tr151.dat` | `test-data/traces/1 tr151.dat` | 194,776 B | `751a439146b8404f72412115dafb56b5bead3fe72a4693dfd990e404b35e9053` |
| `tests/fixtures/smfret_analysis2/traces/1 tr152.dat` | `test-data/traces/1 tr152.dat` | 223,585 B | `102af41605a4faccb0399fe097a9ff709b4073b322eab826ac99a15d0a61979b` |
| `tests/fixtures/smfret_analysis2/traces/1 tr154.dat` | `test-data/traces/1 tr154.dat` | 58,006 B | `25700f63bc9c804ec8dba7a483291140b9e1f478623755298ad801c9cc32948f` |
| `tests/fixtures/smfret_analysis2/traces/1 tr155.dat` | `test-data/traces/1 tr155.dat` | 109,513 B | `9385784db988edcc640110e4a3a6827180c55b0bce08b31d2e0257f920ab43f5` |
| `tests/fixtures/smfret_analysis2/traces/1 tr162.dat` | `test-data/traces/1 tr162.dat` | 94,963 B | `7cfc1587255450283dca5ec2510e30f1aca853beade8f9ac8286329fa9fdb8f7` |
| `tests/fixtures/smfret_analysis2/traces/1 tr166.dat` | `test-data/traces/1 tr166.dat` | 39,382 B | `ce34ab51b779d2f7e65fb0d1b71294f9c822a0ba6d50c8fd76d69bcf4ddddaf2` |
| `tests/fixtures/smfret_analysis2/traces/1 tr168.dat` | `test-data/traces/1 tr168.dat` | 181,099 B | `883fa062bb017372eac5e9a17179710fa65dfc28d2e8eae1f50b7bcabdf35c05` |
| `tests/fixtures/smfret_analysis2/traces/1 tr170.dat` | `test-data/traces/1 tr170.dat` | 61,498 B | `e97ffe82a09a9d556f4af841d8c2aab4630eec33947f338e7032ed7f64033e9e` |
| `tests/fixtures/smfret_analysis2/traces/1 tr172.dat` | `test-data/traces/1 tr172.dat` | 67,318 B | `0b1773b349d2e3cdf38393a3829263169dc20966e6280574e6ebb2350182ccd6` |
| `tests/fixtures/smfret_analysis2/traces/1 tr174.dat` | `test-data/traces/1 tr174.dat` | 113,878 B | `9ec38c9ef76c50f354c0b11f1034c9dd0e0bd9a7ff0365d5e139d2bed91fc4e9` |
| `tests/fixtures/smfret_analysis2/traces/1 tr175.dat` | `test-data/traces/1 tr175.dat` | 12,319 B | `b5df8ea6a06151806b785cd7ada3ab4cfd069ccf2a499f501512e3bc9f39d132` |
| `tests/fixtures/smfret_analysis2/traces/1 tr176.dat` | `test-data/traces/1 tr176.dat` | 153,745 B | `67ba7c41a17c877bc3aeaf577b735864594e6469c97b72b6ecf7bff8b68bab4a` |
| `tests/fixtures/smfret_analysis2/traces/1 tr179.dat` | `test-data/traces/1 tr179.dat` | 25,996 B | `2f3e8a1ebf0fe12fa76df9b7525af78788237a3d59242a7be311af5fa4ed4dba` |
| `tests/fixtures/smfret_analysis2/traces/1 tr181.dat` | `test-data/traces/1 tr181.dat` | 148,798 B | `ba58f8b9929e683095e83c4981a41f0a63ef677bcfa61114fff599f594233151` |
| `tests/fixtures/smfret_analysis2/traces/1 tr184.dat` | `test-data/traces/1 tr184.dat` | 34,435 B | `a94f9996cba5e5d2c65e85b1a29d24bc5550643c622f71b64bf801fa912f2fb3` |
| `tests/fixtures/smfret_analysis2/traces/1 tr185.dat` | `test-data/traces/1 tr185.dat` | 93,508 B | `34f882434029dca1298d34052352f1c03ef336201496607683beea19006f6a07` |
| `tests/fixtures/smfret_analysis2/traces/1 tr188.dat` | `test-data/traces/1 tr188.dat` | 195,649 B | `9f2a7c14f9dba907b99eca2210be77c182ffee8088de431e21b1cb7192693ff6` |
| `tests/fixtures/smfret_analysis2/traces/1 tr190.dat` | `test-data/traces/1 tr190.dat` | 14,938 B | `9f8db4ce64b64f8176edd546e0e878cc4b19e6e319988117fbdf8429a7cc6ad0` |
| `tests/fixtures/smfret_analysis2/traces/1 tr191.dat` | `test-data/traces/1 tr191.dat` | 199,141 B | `e169ad485cd3f8b531ff3a32180ed07213edad1dec15cfced0d0cf608b3d2d44` |
| `tests/fixtures/smfret_analysis2/traces/1 tr194.dat` | `test-data/traces/1 tr194.dat` | 199,141 B | `9a7a16796d83eabfdbea6459c5ea8960dfcb0b8ea0191f2bed4ce079fa800947` |
| `tests/fixtures/smfret_analysis2/traces/1 tr195.dat` | `test-data/traces/1 tr195.dat` | 114,751 B | `4ce6759ec576d085ce69536037f4af16a1c0a476265dab49da857affb348c80f` |
| `tests/fixtures/smfret_analysis2/traces/1 tr200.dat` | `test-data/traces/1 tr200.dat` | 71,101 B | `023a4b018e597f31cfd8e1f3e169b618ed43ea62950095af74a2a523ba5c1b9f` |
| `tests/fixtures/smfret_analysis2/traces/1 tr201.dat` | `test-data/traces/1 tr201.dat` | 107,476 B | `a231fb39466cb91bc59486ff0a5bc651543a50385d264d32458c1b3482c3e042` |
| `tests/fixtures/smfret_analysis2/traces/1 tr202.dat` | `test-data/traces/1 tr202.dat` | 126,391 B | `957201a8cfcb22d1ec8c2a1409ca364dfda1c4acbccb0c2db0095f73dada7989` |
| `tests/fixtures/smfret_analysis2/traces/1 tr205.dat` | `test-data/traces/1 tr205.dat` | 190,993 B | `ca03fc1362ac40729a53ce8300396b892e12e406855421931a41c98f7954f2db` |
| `tests/fixtures/smfret_analysis2/traces/1 tr206.dat` | `test-data/traces/1 tr206.dat` | 135,703 B | `5f55a19377b37d7b114b715f0c7719db469806b88f269530c7918257971ba950` |
| `tests/fixtures/smfret_analysis2/traces/1 tr207.dat` | `test-data/traces/1 tr207.dat` | 169,168 B | `e2a36b004b6595fe763b73aae873663b04f8eadfbb43f2767c318d330dcc9eea` |
| `tests/fixtures/smfret_analysis2/traces/1 tr208.dat` | `test-data/traces/1 tr208.dat` | 220,384 B | `1c17047c84e0b541a0d74bddc84012c980727a70feae57832483fb42e3273209` |
| `tests/fixtures/smfret_analysis2/traces/1 tr209.dat` | `test-data/traces/1 tr209.dat` | 145,306 B | `cd3ba308267180c16b6bb4cc03a391ea2575a6db891073f2c8147a8519a0c50f` |
| `tests/fixtures/smfret_analysis2/traces/1 tr210.dat` | `test-data/traces/1 tr210.dat` | 204,670 B | `f8eaf9fba8e223c70f86c106acbc6a804b6775d02574f51fc976a3646b8876ca` |
| `tests/fixtures/smfret_analysis2/traces/1 tr215.dat` | `test-data/traces/1 tr215.dat` | 182,845 B | `ceedc236574f6727b22baa35270036bfbe094c7282f6299619fdd4c7f2489548` |
| `tests/fixtures/smfret_analysis2/traces/1 tr218.dat` | `test-data/traces/1 tr218.dat` | 39,382 B | `162b6fe4b7e31be30f28d39fb0e8b04cfed8914fc357f48a413b1bef5188e7bd` |
| `tests/fixtures/smfret_analysis2/traces/1 tr221.dat` | `test-data/traces/1 tr221.dat` | 161,020 B | `28e7cfcecd4fb0327378fd7e02b234bcc3266e03cf0822f3a5d9e17271230fab` |
| `tests/fixtures/smfret_analysis2/traces/1 tr228.dat` | `test-data/traces/1 tr228.dat` | 60,916 B | `9775deeb4fe0de88df5929620c7608e0682e26fc26498c74949921155bd99e7f` |
| `tests/fixtures/smfret_analysis2/traces/1 tr229.dat` | `test-data/traces/1 tr229.dat` | 145,597 B | `5b7ee35c7612f47c77b3aa31932aea28356b01b2662d608985d0e58c3f8750f3` |
| `tests/fixtures/smfret_analysis2/traces/1 tr233.dat` | `test-data/traces/1 tr233.dat` | 44,329 B | `27ff65fe654851e28d8021fe331f0140aa98af9a4f59a823e846d394ce920b69` |
| `tests/fixtures/smfret_analysis2/traces/1 tr238.dat` | `test-data/traces/1 tr238.dat` | 90,598 B | `afcc8495875b781b3ed7a6f4a8ea1dc838f067e0caec5c51ccbd344b69160b11` |
| `tests/fixtures/smfret_analysis2/traces/1 tr242.dat` | `test-data/traces/1 tr242.dat` | 105,148 B | `e484f5e197d85f09fca33c438781a767799e54e606c9ee3223f351a30a57d7a8` |
| `tests/fixtures/smfret_analysis2/traces/1 tr245.dat` | `test-data/traces/1 tr245.dat` | 239,299 B | `75f55b77fada48722bf39a3f042f26ba52e20e31b076b3078d5fb5f2a1bc250c` |
