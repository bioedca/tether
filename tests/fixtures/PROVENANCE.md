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
| `tests/fixtures/tdat_coloc_slice.tdat` | TIRFdata colocalization slice (M0.5 S6 decode) | `…010.tif…00-00.tdat` | 37,039,831 B | 41,344 B | `b6a911d48bc27cd1…` | coloc table only |
| `tests/fixtures/tmap_coeffs.npz` | Deep-LASI `.tmap` registration coefficients (M0.5 S6 registration) | `…20250718…13-40.tmap` | 3,872,385 B | 5,905 B | `7db0cf80d161847e…` | decoded degree-2 coeffs only |
| `tests/fixtures/acceptor_oracle.npz` | acceptor aperture-integration oracle via `.tmap` apply (M0.5 S5/S6) | `…010.tif` + `…010.mat` + `…13-40.tmap` | 891,955,083 B + 9,053,155 B + 3,872,385 B | 271,214 B | `c4293f00ed2ac72d…` + `af1b5be33aa63f87…` + `7db0cf80d161847e…` | 6 acceptor crops + `acc` oracle |

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
(all `0` for this acquisition). The ~37 MB of trace/patch arrays and the MCOS
object blob are dropped, so it stays in plain git yet faithfully exercises the
M0.5 S6 `tether.io.read_tdat` decoder (coordinates + the Appendix-B factor remap)
in the required test matrix. Regenerate with `scripts/make_tdat_fixture.py`.

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

## Git-LFS gated tier (`tests/fixtures/large/`)

`smd_281mol.hdf5` (the redistributable ≥50-molecule population SMD) and its
paired `model_281mol.hdf5` (4-state consensus vbHMM) are tracked by Git-LFS via
`.gitattributes` (`tests/fixtures/large/**`). They back the M0.5/M6
idealization-parity gate and are **not** pulled by the default CI checkout, so
the required `test` matrix never depends on them (their load test is
`@pytest.mark.large` and skips on an unmaterialized LFS pointer).
