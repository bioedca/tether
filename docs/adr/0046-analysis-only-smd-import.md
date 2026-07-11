# 0046 — Analysis-only import of a coordinate-less SMD / `.txt` source

- **Status:** accepted
- **Date:** 2026-07-11
- **Deciders:** bioedca
- **PRD anchor:** §7.8, §5.3 (FR-LEGACY) — a raw `.txt`-sourced SMD imported standalone is a degraded, round-trip-disabled analysis-only project
- **Milestone:** M7

## Context and problem statement

M7's legacy path has two branches (§7.8). The **round-trip** branch (ADR-0045) re-imports
a Deep-LASI bundle *with* coordinates. This ADR homes the **other** branch: a raw
`.txt`-sourced tMAVEN **SMD imported standalone** — no `.tdat`, no `.mat`, no movie, e.g.
the M6 281-molecule parity fixture — carries **neither coordinates nor patches** (§5.3,
§7.8). It must still be accepted, but as an explicit **analysis-only project**:
idealization / histograms / TDP / kinetics fully usable (exactly what M6 parity needs),
while the trace↔movie round-trip browser (§7.3) and patch-dependent movie-less curation
are **disabled**, a one-time banner announces *"coordinates and patches absent; movie
round-trip and spot/overlap views unavailable,"* and every molecule is tagged
`round-trip-unavailable` in provenance.

The open questions are the **store-writer** (the M0-frozen schema has no field for
"movie-less/coordinate-less"), a **per-molecule identity** without a movie `sha256` or
coordinates, and how the (later) GUI wizard learns a project is analysis-only.

## Decision drivers

- **Schema freeze (ADR-0005).** Additive *data* only; `schema-guard` must stay green.
- **Never fabricate (PLAN §0.4).** Absent coordinates/patches must be represented as
  genuinely absent, never a stub `[0, 0]` coordinate or a fake movie/patch.
- **Apparent-E never NaN (ADR-0003).** No correction factors ⇒ the explicit apparent-E
  substrate, never a fabricated γ.
- **`molecule_key` is the `/labels` join key (§7.10).** It must be **unique per molecule**
  — a colliding key would cross-contaminate curation labels.
- **Single-session PR scope (PLAN §0.1).** The folder→project wizard is a separate later
  M7 PR; this PR is the headless importer + the durable disable signals it must persist.

## Considered options

- **A — Reuse `write_extraction` with a synthetic stub movie + zeroed coordinates.**
  Rejected: `write_extraction` mandates a valid `MovieMetadata` (non-empty `movie_id`,
  positive dims) and derives `molecule_key` from the movie `sha256` + `donor_xy` (which
  collides to one key for all molecules when coordinates are zeroed, breaking the
  `/labels` join). It would fabricate a movie and coordinates the source does not have.
- **B — A small dedicated movie-less writer** (`tether.project.analysis_import`) that
  writes only additive data: `/molecules` rows with `movie_id=""`, `NaN` coordinates, a
  synthesized unique `molecule_key`, `tags="round-trip-unavailable"`; the SMD/`.txt`
  intensities as the `corrected` `/traces` layers; an additive `/settings/analysis_only`
  project marker; `compute_corrected_fret` to stamp `METHOD_APPARENT_UNAVAILABLE`.
- **C — Add a structural schema field (a `round_trip_available` column/group).** Rejected:
  a structural change under the M0 freeze — an ADR + `schema_version` bump — when the
  existing frozen `tags` field + the additive `/settings` container already carry the
  signal.

## Decision outcome

Chosen option: **"Option B"**. `tether.project.analysis_import.import_analysis_only_project`
consumes a decoded `SMDData` (`tether.idealize.read_smd`, the SMD-HDF5 case, incl. every
raw-`.txt`-sourced SMD and the 281-mol parity fixture) **or** a `DeepLasiTraces`
(`tether.io.deeplasi.read_deeplasi_txt`, a bare `.txt`) and writes a movie-less
analysis-only `.tether` atomically (sibling temp → `os.replace`, re-asserting the
single-writer lock on `overwrite`, mirroring `reconstruct_project`):

- **`/molecules`** — `movie_id=""` (movie-less, as `export_subset_tether` proves a
  `/movies`-row-less store is first-class); `donor_xy`/`acceptor_xy` = **`NaN`**
  (coordinates absent — a sentinel, never fabricated `[0, 0]`); a **synthesized unique,
  deterministic** `molecule_key` = SHA-256 of the source id + row index + the raw
  donor/acceptor trace bytes (an identity hash of real inputs — unique, stable across a
  re-import, intensity-anchored per the §5.3 exact-match key); the SMD's tMAVEN
  `pre_list`/`post_list` as `analysis_window` (else the full native window);
  `tags="round-trip-unavailable"`.
- **`/traces/{donor,acceptor}_corrected`** — the SMD/`.txt` intensities as the apparent-E
  analysis substrate (the `intensity_quantity="corrected"` layer every consumer reads by
  default). **No** `raw`/`background` layers and **no** `/patches` are synthesized — both
  genuinely absent.
- **`/settings/analysis_only`** — an additive project marker (`round_trip_available=False`
  + the banner text + source provenance) read by `read_analysis_only_marker`, so the later
  GUI wizard gates the round-trip/patch views off an O(1) read.
- **Correction** — `compute_corrected_fret` stamps `METHOD_APPARENT_UNAVAILABLE` (α/γ
  left `NaN`): the honest apparent-E substrate (ADR-0003).

The Deep-LASI selection semantics of ADR-0045 do **not** apply: an SMD carries **no
accept/reject mask** — the SMD *is* the curated subset (every row is a selected molecule),
so all molecules import as `UNCURATED` with no fabricated curation.

### Consequences

- Good: schema-guard stays green (additive-only, no `schema.py` change); the §9 M7
  analysis-only clause is met and locked by `tests/test_analysis_import.py` (movie-less,
  round-trip-disabled, molecules tagged, and the FRET histogram runs — incl. the gated
  281-mol parity fixture).
- Good: the whole analysis stack (histogram / idealize / TDP / dwell) runs unchanged on
  the movie-less store; the marker + tag give the future wizard a clean disable signal.
- Bad / trade-off: `NaN` coordinates mean any coordinate-consuming view (round-trip
  navigator, overlap NN readout) must gate on the marker **before** use rather than
  degrade silently — the wizard PR's responsibility (fail-loud on misuse over a fake-`[0,0]`
  silent-wrong-answer is the deliberate choice).
- Follow-up: the Deep-LASI re-analysis **wizard UI** wires the marker to the disabled
  views + the one-time banner (a later M7 PR).

## More information

- PRD §7.8 (analysis-only branch, lines 588–594), §5.3, §9 M7 acceptance.
- Reuses ADR-0003 (apparent-E never NaN), ADR-0005 (schema freeze); the sibling of
  ADR-0045 (round-trip reconstruction), whose "analysis-only degraded import" follow-up
  this closes.
- Builds on PR #122 (`tether.io.intake` — `AcquisitionFileSet.analysis_only`).
- Code: `src/tether/project/analysis_import.py`; tests: `tests/test_analysis_import.py`.
