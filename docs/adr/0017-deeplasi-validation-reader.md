# 0017 — Minimal Deep-LASI `.mat` / `.txt` validation reader: M1-scoped fields, coordinate convention, and lazy scipy

- **Status:** accepted
- **Date:** 2026-06-29
- **Deciders:** bioedca
- **PRD anchor:** §9 M1 (extraction-vs-Deep-LASI acceptance), §8 NFR-VALID (a), Appendix A (input formats), §11.2
- **Milestone:** M1 (S9 — part 1: the Deep-LASI reader; the CLI + recall/Pearson/RMS oracle + M1 close follow)

## Context and problem statement

The M1 acceptance gate (§9 M1, §8 NFR-VALID (a)) checks that Tether's native
extraction reproduces Deep-LASI's result on the same movie — recall ≥ 95 % @ 1 px,
per-frame integrated-intensity Pearson r ≥ 0.99, registration RMS ≤ 0.5 px. That
comparison needs Deep-LASI's **own** output as the oracle, which lives in two
export artifacts (Appendix A):

- `DeepLASI_MAT_export_*.mat` — MATLAB **v5**, ≈ 9 MB; N molecules × T frames of
  `fret_pairs` (N×4 donor/acceptor pixel coordinates) + raw/corrected/background
  donor + acceptor integrated traces, plus ~25 other fields (FRET, direct
  excitation, `range`/`select`/`tags`, photobleach `pacc`/`pdon`, leakage `b` and
  γ `g`, movie provenance).
- `…-donc-accc-w.txt` — whitespace text, T rows × 2N columns of *corrected*
  donor/acceptor intensities; **no coordinates**.

S9 splits into the reader (this PR) and the CLI + oracle + M1 close (follow-ups).
The open questions this PR settles:

1. **Which fields does a *validation* reader parse** — all ~30, or only what the
   M1 oracle consumes?
2. **What coordinate convention** does `fret_pairs` carry, and how does it map to
   Tether's internal convention?
3. **How does the `.txt` relate to the `.mat`** (column order, which corrected
   fields)?
4. **How is the reader's scipy dependency kept out of the `schema-guard` import
   path** (which imports `tether.io` with a minimal `h5py` + numpy env)?

## Decision drivers

- **A validation reader, not a project importer** — feed the M1 oracle; the full
  round-trip importer is M7 (the `.tdat`/SMD path, §7.3).
- **Never encode an unvalidated premise** — a wrong correction-factor or
  bleach-frame interpretation is a silent bug CI can't catch (CLAUDE.md / §Data-gaps).
- **Faithful to real data** — every parsed field and convention is verified
  against the real export, not assumed.
- **Keep the `tether.io` package import light** — `schema-guard` imports
  `tether.io.schema` through the package with a deliberately minimal env.

## Considered options

**Field scope.**
- **A. Parse only the M1-oracle fields: `fret_pairs` → 0-based donor/acceptor
  coordinates + the six raw/corrected/background `(N, T)` trace arrays + movie
  provenance.** Chosen. The recall/RMS gates need coordinates; the intensity-Pearson
  gate needs the raw integrated traces (`don`/`acc` — the M0.5 aperture oracle
  correlated Tether's integration against `don` to 0.992–0.994); corrected +
  background round out the trace triple Tether's own extractor writes (ADR-0016).
  The photobleach `pacc`/`pdon` and correction factors `b` (= Deep-LASI β → Tether
  α) / `g` (= γ) are **deferred to M3**, where their semantics are verified against
  the bleach-frame gate (§9 M3, NFR-VALID (g)) and the Appendix-B remap (ADR-0008).
  This reader does **not** encode an interpretation of them — in the reference
  export `pacc`/`pdon` are even stored as `uint8` (≤ 255), which cannot hold a
  1700-frame index as-is, so their scaling is genuinely an M3 question.
- **B. Parse the whole struct now.** Rejected — pulls forward bleach/correction
  semantics the M3 gates exist to validate, risking a silent wrong premise, for
  fields M1 never reads.

**Coordinate convention.**
- **C. `fret_pairs` columns are `[x_donor, y_donor, x_acc, y_acc]` with `x` = col,
  `y` = row, **1-based** (MATLAB); subtract 1 → Tether 0-based `[x = col,
  y = row]`.** Chosen — the convention the M0.5 aperture oracle already validated
  (`scripts/make_aperture_fixture.py`, donor-correlation ≈ 0.99) and the one
  `tether.imaging` (detect/coloc/extract) uses. Coordinates are returned as
  `float64` (kept sub-pixel, not rounded — the recall gate is @ 1 px, RMS sub-pixel).

**`.txt` ↔ `.mat`.**
- **D. The `.txt` is donor-first, per-molecule-interleaved (`donc₀ accc₀ donc₁
  accc₁ …`); its columns equal the `.mat` `donc`/`accc` to the text's 5-decimal
  rounding.** Chosen — **empirically verified across all 250 molecules** of the
  reference acquisition (max abs diff 5 × 10⁻⁶). The data-present test re-locks this
  on the full files; the committed slice inherits it by construction.

**scipy in the package import path.**
- **E. Import scipy *lazily* inside `read_deeplasi_mat`** (module scope stays
  numpy-only). Chosen — `tether.io.__init__` re-exports the reader (API
  consistency with `read_tdat`), but `schema-guard` imports `tether.io.schema`
  through the package with `h5py` + numpy only; a module-scope `import scipy` would
  break that gate. `read_deeplasi_txt` needs only `numpy.loadtxt`, so it imports
  nothing extra. (Python caches the module after the first call — negligible cost.)
- **F. Keep the reader out of the `tether.io` re-exports.** Rejected — inconsistent
  with the other readers; lazy import preserves both the API and the minimal env.
- **G. Add scipy to the `schema-guard` env.** Rejected — expands a deliberately
  minimal required-CI env and slows the gate for no schema reason.

**Format.** v5 only: reject MATLAB v7.3 (HDF5) with `NotImplementedError`
(`scipy.io.loadmat` cannot read it — the export is v5 per Appendix A), and any
non-MAT/garbage/bare-HDF5 with a clean `ValueError` (wrapping
`matfile_version`'s raise) rather than leaking a raw scipy error.

## Decision outcome

Chosen **A + C + D + E** in a new `tether.io.deeplasi` module:

- `read_deeplasi_mat(path) -> DeepLasiExport` — 0-based donor/acceptor coordinates
  + the six `(N, T)` trace arrays + provenance: `movie_name` (the source-movie
  *filename*) and `movie_path` (its *directory*) are two distinct Deep-LASI fields
  (PRD §6 / Appendix A), plus `exported_by`. v7.3 input is rejected with a clean
  `NotImplementedError`; non-MAT/garbage input (incl. `matfile_version`'s
  `IndexError` on short input) with a clean `ValueError`.
- `read_deeplasi_txt(path) -> DeepLasiTraces` — the two de-interleaved corrected
  `(N, T)` arrays.
- Both frozen dataclasses with `eq=False` (ndarray fields, cf. `RegistrationMap`)
  and `n_molecules`/`n_frames` properties.

Re-exported from `tether.io`. Tested in two layers (`tests/test_deeplasi.py`): a
committed 4-molecule × 80-frame real slice (`scripts/make_deeplasi_fixture.py`) +
in-test `savemat` round-trips (coordinate conversion, missing/ragged-field and
odd-column guards, v7.3 and non-MAT rejection) in the default matrix, and a
data-present test on the full 250 × 1700 export (skipped when `example-data/` is
absent — mirroring the `tmap_coeffs` data-present pattern).

### Consequences

- Good: the M1 oracle has a faithful, real-data-verified ground-truth reader;
  every convention is checked against the reference acquisition, none assumed.
- Good: `schema-guard` and the `tether.io` import graph stay scipy-free.
- Deferred: the `tether extract` CLI and the recall/Pearson/RMS acceptance oracle
  (S9 follow-up PR — the oracle *consumes* this reader); `pacc`/`pdon` + `b`/`g`
  to M3; full project round-trip import to M7. No `/molecules` schema change, no
  new §11.2 tunable (a fixed-format reader has none).

## More information

PRD §9 M1, §8 NFR-VALID (a), Appendix A (`.mat`/`.txt` columns), §11.2; ADR-0016
(the extraction trace store this oracle validates), ADR-0011 (homing the ≥ 95 %
extraction recall at M1), ADR-0008 (the β→α/γ correction remap deferred here to
M3). `src/tether/io/deeplasi.py`, `tests/test_deeplasi.py`,
`scripts/make_deeplasi_fixture.py`, `tests/fixtures/deeplasi_export_slice.mat`,
`tests/fixtures/deeplasi_traces_slice.txt`.
