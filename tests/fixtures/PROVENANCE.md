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

## Ha laboratory SAM/SAH riboswitch snapshots (#796, ADR-0086) — **CC-BY-4.0**

External third-party data under a different licence: the data of Liao et al., Nucleic Acids Res.
51:8957 (2023), doi:10.1093/nar/gkad633, deposited by **Ting-Wei Liao** (Ha Single-Molecule Lab)
at Zenodo **doi:10.5281/zenodo.8088172** (version 1.1, 2023-06-27) under **CC-BY-4.0**. They are
annotated CC-BY-4.0 in `REUSE.toml` (an `override` entry) and attributed in `NOTICE`.

The 56 files are verbatim copies (no transformation) of members of the record's Fig. 1 archive,
`Fig 1 Studies of ligand-free conformations.zip` (75,685,670 B, MD5
`c5929e9679142e85fa9ae3156e3440b2`, SHA-256
`a8c0e41a9c080b39048bcee22503d86bb58d99824aa82c564d08535dbf5422f3`), folder
`20200129 RNA only condition/RNA only/` (shortened to `…/RNA only/` below):

- the 53 short binary snapshots `hel1.traces` to `hel53.traces` (20 frames each, 361 to 467
  molecules; the format is in `_ha_traces.py` under `tests/`). The three long movies
  `hel54.traces` to `hel56.traces` (about 5 MB each) and every `.pks`, `.smt`, `.smt2`, `.tif`,
  `.tlog` and `.log` file of the folder are not copied;
- `frets.dat`, the laboratory's 46,950 FRET values, one on each line;
- `test3.dat`, its normalised histogram (centre and fraction; bin width 0.015, centres -0.4 to
  1.4);
- `counts2.txt`, the record that the laboratory's script wrote of its parameters (leakage 0.12,
  intensity cutoffs 100 and 1000, FRET split 0.5, green-excitation frames 1 to 10,
  red-excitation frames 11 to 20) and of its counts for each of the 56 movies.

| Fixture | Source member | Size | SHA-256 |
|---|---|---|---|
| `tests/fixtures/ha_sam_sah/hel1.traces` | `…/RNA only/hel1.traces` | 34,966 B | `0ce42e85fd2d41f0ebeeaf4f2697dc5bc573673eced6efc6d3cab12c7a479df3` |
| `tests/fixtures/ha_sam_sah/hel2.traces` | `…/RNA only/hel2.traces` | 33,766 B | `45781c2bd17808f5946a897d4c0c55464e8bf152f29be2b3e5b96eb652fde40c` |
| `tests/fixtures/ha_sam_sah/hel3.traces` | `…/RNA only/hel3.traces` | 34,246 B | `88b26c09e89f48bd86038df15cb9ace18ccf1afff6fb94a0c3edec6671317405` |
| `tests/fixtures/ha_sam_sah/hel4.traces` | `…/RNA only/hel4.traces` | 33,686 B | `a729f934994ca3842f7c62649c2b9ac3c199cfc3bfc5930422f216c66fcdb67f` |
| `tests/fixtures/ha_sam_sah/hel5.traces` | `…/RNA only/hel5.traces` | 32,726 B | `8606c9d59b608683b8806ab6dc03f736c7a5164caae46ad9000d457d3dc773cd` |
| `tests/fixtures/ha_sam_sah/hel6.traces` | `…/RNA only/hel6.traces` | 36,006 B | `869d5100552cffd2257eae0498905ed348bd7c3f6bc4fe0b335124e080ff81ab` |
| `tests/fixtures/ha_sam_sah/hel7.traces` | `…/RNA only/hel7.traces` | 34,966 B | `a6e3cc581785c30c8ac23229fb960758f8fca1e7f116b430a5f2adba888b451c` |
| `tests/fixtures/ha_sam_sah/hel8.traces` | `…/RNA only/hel8.traces` | 35,126 B | `2a0ad48e35e84f88f5422b3961a2e8bc2e4935a68a417ed475cc7d59e87bcecb` |
| `tests/fixtures/ha_sam_sah/hel9.traces` | `…/RNA only/hel9.traces` | 35,366 B | `a3959c95e0099d5af7a3c4c6e9598b279c64ea6ded2e743d8e7ec21de52b8305` |
| `tests/fixtures/ha_sam_sah/hel10.traces` | `…/RNA only/hel10.traces` | 34,966 B | `3cf678464d671693ee9f788ba8cceaeebefffc5be2aeeee80e9a77171ae720c7` |
| `tests/fixtures/ha_sam_sah/hel11.traces` | `…/RNA only/hel11.traces` | 33,846 B | `11a31ece602904eb5874d5606eb3d7de8c54b87079d31398fe8d93bbf02b8263` |
| `tests/fixtures/ha_sam_sah/hel12.traces` | `…/RNA only/hel12.traces` | 34,646 B | `9e210a4e169bf7bd7d3a5d89234002e9146ca624e5b0e49076f035499c4dc87c` |
| `tests/fixtures/ha_sam_sah/hel13.traces` | `…/RNA only/hel13.traces` | 33,286 B | `e921c3579fd76fc687ea1ddda5208e3895d96f002d243b836ff60babdef57cda` |
| `tests/fixtures/ha_sam_sah/hel14.traces` | `…/RNA only/hel14.traces` | 36,006 B | `f003b0c477b084d8160b8eb4b29a3d32ec8d9c6030099413661f4c056a37fccf` |
| `tests/fixtures/ha_sam_sah/hel15.traces` | `…/RNA only/hel15.traces` | 35,046 B | `2a200eb4896fb261f9b9d2431d8aec81e5869c1a4e5b454ac5227c6c6ea389d9` |
| `tests/fixtures/ha_sam_sah/hel16.traces` | `…/RNA only/hel16.traces` | 35,446 B | `ed0232e600c23d80057184e3b3d7b6cb3183329cd43d8423123bbb94a5947781` |
| `tests/fixtures/ha_sam_sah/hel17.traces` | `…/RNA only/hel17.traces` | 34,406 B | `6845b97e09bea414e2811203855f509366fc65dcf3a83a42ebbe9f3f4e81df5c` |
| `tests/fixtures/ha_sam_sah/hel18.traces` | `…/RNA only/hel18.traces` | 34,966 B | `f8092792877fa0f2b251973402244e7ca61d74b72a0e07b291006cb30fdcb90f` |
| `tests/fixtures/ha_sam_sah/hel19.traces` | `…/RNA only/hel19.traces` | 33,126 B | `321dd25dcb0337fa92eee0245bd4f53c17995a7afceb97eb7c11eda289b3334c` |
| `tests/fixtures/ha_sam_sah/hel20.traces` | `…/RNA only/hel20.traces` | 34,726 B | `09ec0505e50543452ece639734502babd9f6e4b7ee65f3c9e2f02128befc444d` |
| `tests/fixtures/ha_sam_sah/hel21.traces` | `…/RNA only/hel21.traces` | 32,966 B | `06be429d05dd97a6c54118161939d48488b0901ee927e88e8d6b5096c94be51b` |
| `tests/fixtures/ha_sam_sah/hel22.traces` | `…/RNA only/hel22.traces` | 34,646 B | `2b8221c6fc94d4b0518f46a25d5b9d6a87f3c10ffa917140e7d64365684eb6a8` |
| `tests/fixtures/ha_sam_sah/hel23.traces` | `…/RNA only/hel23.traces` | 36,086 B | `798ff04f5be85f6de625d58f5c878a4f6036cdadfdad97368ea2526fcd930d53` |
| `tests/fixtures/ha_sam_sah/hel24.traces` | `…/RNA only/hel24.traces` | 33,766 B | `dad5d8eca2dab5887310c27336040ec214947e311187bb5d04c86c6f61f41249` |
| `tests/fixtures/ha_sam_sah/hel25.traces` | `…/RNA only/hel25.traces` | 32,806 B | `04c62648fed6eaa8a8d961c8245bc29ac9f0af2dabfb9969251a9d5379b2deeb` |
| `tests/fixtures/ha_sam_sah/hel26.traces` | `…/RNA only/hel26.traces` | 36,566 B | `33b6fb375fbc8a8c64afdd6ff4825c0a2c38071ab2cb7daec7260fcc8e2c33c0` |
| `tests/fixtures/ha_sam_sah/hel27.traces` | `…/RNA only/hel27.traces` | 33,926 B | `1f5015e1c464cd2f5903f1e9195665c8b14f2360a619aa6d31e2b31ccb1cec09` |
| `tests/fixtures/ha_sam_sah/hel28.traces` | `…/RNA only/hel28.traces` | 34,326 B | `fd9634fb0ca447d30b2c0c6bd072102548ba550b28da765a743e96b4cc8f3d2d` |
| `tests/fixtures/ha_sam_sah/hel29.traces` | `…/RNA only/hel29.traces` | 33,686 B | `a6409f7db0f061150df44f4488805a5feb3ec47830ea7209d86c7710ecbff16f` |
| `tests/fixtures/ha_sam_sah/hel30.traces` | `…/RNA only/hel30.traces` | 34,486 B | `72c7d95e018052d920d0861ae1b8262fc6760cb990eccba42a9dc839c16d8f09` |
| `tests/fixtures/ha_sam_sah/hel31.traces` | `…/RNA only/hel31.traces` | 28,886 B | `ec49f2f6b2d31046e3f7913436c3eee46a7d00effa19fa53015b6c1bf900e3ea` |
| `tests/fixtures/ha_sam_sah/hel32.traces` | `…/RNA only/hel32.traces` | 33,686 B | `905cdfcec85539567a0f937ecd673664170fee067f79890bed3af29f333f0691` |
| `tests/fixtures/ha_sam_sah/hel33.traces` | `…/RNA only/hel33.traces` | 31,926 B | `b00c7151d865da1444365539b03cadc9e9f869f7a5386fa179d9e16592d8d599` |
| `tests/fixtures/ha_sam_sah/hel34.traces` | `…/RNA only/hel34.traces` | 32,486 B | `0b2550cb78b1f1f5eb02dec4a9239921ce0e6a443d128dd0b5f2821863a6eb71` |
| `tests/fixtures/ha_sam_sah/hel35.traces` | `…/RNA only/hel35.traces` | 34,006 B | `cd3a052fb327d35ca77afbd905b56a61ab091c092cf26d6119a82e5f11021b03` |
| `tests/fixtures/ha_sam_sah/hel36.traces` | `…/RNA only/hel36.traces` | 33,046 B | `23f75459d20974df6d07474cab57c875c55873593cda71459c5bf39fa917cadd` |
| `tests/fixtures/ha_sam_sah/hel37.traces` | `…/RNA only/hel37.traces` | 37,366 B | `5fec7b056eafdf917d9acb19e47f5497074ca17578b82fce1185ce4a69a42634` |
| `tests/fixtures/ha_sam_sah/hel38.traces` | `…/RNA only/hel38.traces` | 34,406 B | `1d01c8d3e365a2189b725dbd2603951b54a0323541e047ec9e1f53b7acc62334` |
| `tests/fixtures/ha_sam_sah/hel39.traces` | `…/RNA only/hel39.traces` | 36,166 B | `1c45ed2deec8341d7eb7d6fdfe40af88ea3383dd03bd70cf0b018f15ea44a8cf` |
| `tests/fixtures/ha_sam_sah/hel40.traces` | `…/RNA only/hel40.traces` | 34,966 B | `cf7f51cda154aa0608c5643b370579d8f4cfce7d26389823bf6d756ca02d902e` |
| `tests/fixtures/ha_sam_sah/hel41.traces` | `…/RNA only/hel41.traces` | 36,486 B | `c721253ab239199387a24ee0e47998bdc7a117fa16bbf25522dd6b1b119dc757` |
| `tests/fixtures/ha_sam_sah/hel42.traces` | `…/RNA only/hel42.traces` | 32,886 B | `5f03d8a0c8b7253211afaff969557cc0a88f7d93720f587912187792862dfced` |
| `tests/fixtures/ha_sam_sah/hel43.traces` | `…/RNA only/hel43.traces` | 33,446 B | `fce90bd1f66d67a00040ae9031c2214866af83d91cc7874cf8cca94ba6f97044` |
| `tests/fixtures/ha_sam_sah/hel44.traces` | `…/RNA only/hel44.traces` | 33,686 B | `35a0c27a31625917b450f5c04f45788972858f278820036cccbb3e1dc22e2d56` |
| `tests/fixtures/ha_sam_sah/hel45.traces` | `…/RNA only/hel45.traces` | 35,366 B | `a3c1ea1f76b7640d627e00bc12deb4def90389b3218fb577169529a9ca8e257c` |
| `tests/fixtures/ha_sam_sah/hel46.traces` | `…/RNA only/hel46.traces` | 32,326 B | `e9f35208ccd871ab32c1cefbe0c413c97a76420939e57ab348526c220dd9ed2c` |
| `tests/fixtures/ha_sam_sah/hel47.traces` | `…/RNA only/hel47.traces` | 33,606 B | `6b6442c80f860455ab622b51ae0678babab7350187e84d66d195cef26a316e1d` |
| `tests/fixtures/ha_sam_sah/hel48.traces` | `…/RNA only/hel48.traces` | 34,246 B | `c9f291f3a344be104b3709efac1cbce6635834e33917bafb0225279d50c87cc5` |
| `tests/fixtures/ha_sam_sah/hel49.traces` | `…/RNA only/hel49.traces` | 33,206 B | `1ffc607c41f6609a97db6423399862e9fadf0b337536f9075a4a84c3050d9622` |
| `tests/fixtures/ha_sam_sah/hel50.traces` | `…/RNA only/hel50.traces` | 34,246 B | `5cb01df666816b24f796cd5ea21f3cf1be4ce2be6b3b8d52784dbe8200737385` |
| `tests/fixtures/ha_sam_sah/hel51.traces` | `…/RNA only/hel51.traces` | 33,206 B | `97848ddd5ec85127f86b1f14241adcf56c5e0555d6de63bb69761479e9c0ae92` |
| `tests/fixtures/ha_sam_sah/hel52.traces` | `…/RNA only/hel52.traces` | 33,846 B | `bfb791f196702be6b1118d8437167d70554c50133c07297d324b04f5db090743` |
| `tests/fixtures/ha_sam_sah/hel53.traces` | `…/RNA only/hel53.traces` | 33,526 B | `e5aa2f59582d0fbdd3e7ffe32cb8dbe4a434570528846cc6b62688e05f2349ca` |
| `tests/fixtures/ha_sam_sah/frets.dat` | `…/RNA only/frets.dat` | 845,100 B | `a21913a8517488a8d08eaa1e62bd015df8e543457960ed185fe35463957c2ccb` |
| `tests/fixtures/ha_sam_sah/test3.dat` | `…/RNA only/test3.dat` | 4,114 B | `3c24f94f3064f12be130571a25038ff6bd1398c20b86508633384b7df0bd1a65` |
| `tests/fixtures/ha_sam_sah/counts2.txt` | `…/RNA only/counts2.txt` | 11,219 B | `f837ccac4a18bd2555adf7c3e85cf993c64395b2f7433a978ca7d84d5486f08a` |

**Accessed:** 2026-11-13 (downloaded from Zenodo; MD5 and SHA-256 of the archive checked).
**License:** CC-BY-4.0 (Liao, Ha Single-Molecule Lab) — **not** GPL-3.0. Regenerate by
downloading the archive and copying the members above; every file has an entry in
`tests/reference_data/manifest.toml` (ids `ha-sam-sah-fig1` for the archive and
`ha-sam-sah-hel1` to `ha-sam-sah-hel53`, `ha-sam-sah-frets`, `ha-sam-sah-test3`,
`ha-sam-sah-counts2`), and the weekly `reference-data` job compares each copy with its member.
