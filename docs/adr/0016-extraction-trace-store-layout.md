# 0016 — Extraction trace-store layout: zero-pad-to-max-T traces, cached patches, and the molecule_key content hash

- **Status:** accepted
- **Date:** 2026-06-29
- **Deciders:** bioedca
- **PRD anchor:** §5.1 (`/traces`, `/patches`, `/molecules`, `/settings`, `molecule_key`), §7.10 (cross-file join key), Appendix E Stages 14–15, §9 M1, §11.2
- **Milestone:** M1 (S8 — per-frame background + Sum integration → coordinate-tagged traces)

## Context and problem statement

M1 S7 (ADR-0015) produced the donor-anchored molecule list; the integration
primitive `integrate_traces` (M0.5 S5) already computes the 10-frame temporal-MA
ring background and the top-hat `I = TOT − bg·N_psf` Sum integration, returning
*both* the corrected intensity and the uncorrected disk sum. S8 must **persist**
that result: it is the **first writer of extraction data** into a `.tether`.

The open questions the schema skeleton (frozen at M0) deliberately left for the
first writer to settle — `/traces`, `/patches`, `/settings` are forward-declared as
**empty container groups**, so their per-record payload layout is additive *data*
this PR defines:

1. **How are traces laid out** when one experiment spans many movies of differing
   frame count (`/traces` is a single rectangular array, §5.1)?
2. **What is cached in `/patches`?**
3. **How is `molecule_key`** — the cross-file content identity (§7.10) — computed,
   and how does it differ from `molecule_id`?
4. **What goes in the not-yet-computed `/molecules` fields** (corrections, bleach,
   ML class) at extraction, before M3 corrections / M5 ML exist?

## Decision drivers

- **Additive-only over the M0 freeze** — write *data* into the pre-declared frozen
  container groups; never alter structure (`schema-guard` stays green, ADR-0005).
- **Faithful to §5.1** — rectangular zero-padded `/traces`, raw **and** corrected
  intensities, per-frame background, a cached patch per molecule.
- **The apparent-E substrate (ADR-0003)** — extraction precedes corrections; the
  store must represent "no factor yet" unambiguously, never a fabricated value.
- **A stable cross-file join key (§7.10)** — `molecule_key` must survive re-location
  of the same molecule into a split/subset file and absorb float-repr jitter.
- **Provenance travels with the datum (NFR-REPRO)** — the movie row, the provisional
  condition, the effective parameters, and the registration confidence are written
  alongside the traces.

## Considered options

**Trace layout (`/traces`).**
- **A. Six rectangular `(n_molecules, max_n_frames)` arrays —
  `{donor,acceptor}_{raw,corrected,background}` — chunked + gzip, zero-padded to the
  experiment-max frame count as movies are appended; each molecule's `frame_range`
  delimits its valid native extent inside the pad.** Chosen — §5.1's "single
  rectangular array zero-padded to the experiment-max `n_frames`" (mirroring
  tMAVEN's `concatenate_smds` pad-to-`maxt`); storing **raw (uncorrected) and
  corrected** satisfies both §5.1 and the S8 mandate (the uncorrected trace feeds
  M3 bleach detection); the per-frame **background** is kept for QA + reconstruction.
- **B. One per-molecule variable-length dataset.** Rejected — fragments the store,
  defeats vectorized cohort reads, and diverges from the §5.1 rectangular contract.
- **C. Store only corrected, reconstruct raw on demand.** Rejected — S8 explicitly
  requires the uncorrected trace persisted (bleach detection input), and §5.1 says
  *raw and corrected*.

**Float precision.** `float32` for traces + patches — the raw/corrected/background
redundancy is deliberate (§5.1), and `float32` halves the (gzip-compressed) store at
ample precision for disk-sum intensities; the FRET histogram / S9 oracle are
scale-robust. Storage, not a scientific factor, so no Consensus gate.

**Patch cache (`/patches`).**
- **D. One `window×window` temporal-mean crop per molecule per channel.** Chosen —
  a denoised representative thumbnail for movie-less curation + the static overlap
  view (§5.1), cheap and sufficient.
- **E. Full per-frame patch stack.** Rejected — redundant with `/traces` + the
  memmap round-trip (§5.2); large for no curation benefit.

**`molecule_key` vs `molecule_id`.**
- **F. `molecule_id` = fresh UUID (stable once assigned); `molecule_key` =
  `sha256(movie_sha256 | qx | qy)` with `donor_xy` quantized to 0.1 px.** Chosen —
  §5.1 distinguishes the two: `molecule_id` is the globally stable UUID, inherited
  unchanged by a split/subset; `molecule_key` is the **content identity** that joins
  the same molecule across files (§7.10). Quantizing to 0.1 px (below the 8 px
  detection min-separation, so no collision between distinct molecules) absorbs
  float-repr jitter so a re-located molecule hashes identically. Deterministic, no
  salt.

**Un-computed `/molecules` fields at extraction.**
- **G. `alpha`/`gamma`/`correction_confidence`/`quality_class` = `NaN`; `delta` = 0
  (inert, ADR-0008); `bleach_frames` = `(−1, −1)`; `curation_label` = 0;
  `condition_id` = the provisional filename parse (= `condition_id_provisional`,
  validated at M4).** Chosen — `NaN`/`−1`/`0` are unambiguous "not computed yet"
  sentinels (the apparent-E substrate, ADR-0003); they are never read as a factor (a
  finite-factor gate precedes any M3 median, so no `NaN` reaches E). The registration
  `molecule_tags` (a `low-confidence-registration` tag for an over-gate fit, §7.1) is
  imprinted onto every molecule of the movie.

## Decision outcome

Chosen: **A + D + F + G**, in a new `tether.imaging.extract` module that follows the
additive-HDF5-write discipline of `tether.imaging.calibrate.write_calibration`
(ADR-0014): open an *existing* compatible project `r+`
(`assert_is_compatible_project` — refuse a foreign/future/partial file), write only
data into the frozen container groups, **movie write-once**. Public API:

- `molecule_key(movie_sha256, donor_xy)` and `MOLECULE_KEY_QUANTUM_PX = 0.1`.
- `extract_molecules(donor_channel, acceptor_channel, molecules, …)` →
  `MoleculeTraces` (donor + acceptor `IntegratedTraces` + temporal-mean patches +
  the effective parameters).
- `write_extraction(project, *, movie, molecules, traces, parsed, registration_map,
  settings)` → the fresh `molecule_id`s; appends `/movies` + `/molecules` + the six
  `/traces` arrays (zero-pad-to-max-T) + `/patches` + write-once `/settings/extraction`.
- `MovieMetadata` (the `/movies` row provenance) and minimal readers
  `read_molecules` / `read_traces` / `read_patches` (the write↔read round-trip; the
  M2 browser builds the rich view).

Row `i` of `/molecules`, every `/traces` array, and every `/patches` array is the
same molecule (positional trace↔molecule join).

### Consequences

- Good: §5.1 extraction persistence is homed faithfully and **additively** — proved
  by a test asserting `diff_manifest(build_manifest(), introspect(written_file)) == []`
  (the written file differs from a fresh skeleton only by *added* groups/datasets — the
  same additive-only invariant `schema-guard` enforces, applied here to the writer's
  output rather than to the committed golden the CI gate diffs). The apparent-E
  substrate (ADR-0003) extends to the data model: `NaN`/`−1` sentinels, never a
  fabricated factor.
- New tunable `molecule_key_quantum_px` (0.1 px) registered in §11.2.
- Trade-off: the raw/corrected/background redundancy triples the trace byte count
  before compression — accepted per §5.1 (gzip + `float32` keep it modest; raw is the
  bleach-detection input, not reconstructable-for-free given the temporal-MA background).
- Deferred to S9: the real-data extraction-vs-Deep-LASI recall/Pearson/RMS oracle +
  the `tether extract` CLI (these tests are synthetic top-hats with exact expected
  intensities); to M2: the curation `analysis_window` narrowing (defaults to the full
  native window here) and the rich `/patches` overlap UI.

## More information

PRD §5.1 (`/traces` zero-pad-to-`maxt`, `/patches`, `/molecules`, `/settings`,
`molecule_key`), §7.10 (cross-file join key), Appendix E Stages 14–15
(`deeplasi/functions/traces/extractTracesC.m:13-33`, `classes/TRACERdata.m:38`), §9 M1,
§11.2. ADR-0015 (the donor-anchored molecule list this consumes), ADR-0014 (the
`RegistrationMap`/tags and the additive-`/calibration` write precedent), ADR-0008
(δ inert), ADR-0003 (apparent-E / never-fabricate), ADR-0005 (M0 schema freeze).
`src/tether/imaging/extract.py`, `tests/test_extract.py`.
