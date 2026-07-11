# 0045 — Reconstruct a round-trip-ready `.tether` from Deep-LASI legacy data

- **Status:** accepted
- **Date:** 2026-07-11
- **Deciders:** bioedca
- **PRD anchor:** §7.8, §5.3, §5.1 (FR-LEGACY) — reconstruct a Deep-LASI acquisition into a round-trip-ready project without re-extraction
- **Milestone:** M7

## Context and problem statement

M7's legacy path must turn a paired Deep-LASI acquisition (raw movie + `TIRFdata`
`.tdat` + `DeepLASI_MAT_export` `.mat`) into a **round-trip-ready** `.tether` project
*without re-running extraction* (§7.8). The inputs already exist — intake/pairing
(PR #122) and per-molecule coordinate recovery + the SMD intensity cross-check
(PR #124). The open question is the **store-writer**: how to materialize coordinates,
traces, correction factors, bleach/window, the curated selection, and the category
list into the M0-frozen schema faithfully — and what to do about the Deep-LASI
per-molecule NN/HMM state classifications and cached image patches, which the current
readers do **not** decode.

## Decision drivers

- **Schema freeze (ADR-0005).** Only additive *data* may be written; `schema-guard`
  must stay green — so reuse the existing writers, never touch HDF5 structurally.
- **Never fabricate (PLAN §0.4, `Data gaps`).** Missing source data (NN/HMM classes,
  real γ, real patches) must be withheld or clearly deferred, never stubbed.
- **Apparent-E never NaN (ADR-0003).** An export lacking a usable γ must degrade to an
  explicit apparent-E substrate, not a degenerate/fabricated γ.
- **Provisional priors never masquerade as human truth (ADR-0023/0036).** The
  Deep-LASI selection is a cold-start prior, not a human `curation_label`.
- **Single-session PR scope (PLAN §0.1).** The folder→project wizard and the degraded
  analysis-only import are separate M7 PRs.

## Considered options

- **A — Direct HDF5 writer.** Write every group by hand. Rejected: duplicates the
  frozen-dtype logic and risks a silent structural drift past `schema-guard`.
- **B — Reuse the extraction + post-extraction writers, deferring the undecoded
  Deep-LASI fields.** `write_extraction` for movie/molecules/traces/patches; the M3
  correction + photobleach passes; the conditions/labels/weighting writers for the
  category list + curated selection.
- **C — Block on decoding the MCOS NN/HMM blob first.** Reverse-engineer the `.tdat`
  `FileWrapper__` object blob (categories/NN/HMM) before shipping any reconstruction.
  Rejected: large, orthogonal reverse-engineering effort; the round-trip substrate
  (coords + traces + curated selection) is fully recoverable without it.

## Decision outcome

Chosen option: **"Option B"**. `tether.project.reconstruct.reconstruct_project`
orchestrates the existing writers atomically (sibling temp file → `os.replace`, mirroring
`extract_movie`):

- **Coordinates + traces + movie link** via `write_extraction`: coordinates recovered
  from the `.tdat` **or** the `.mat` (caller's `RecoveredCoordinates`, aligned to the
  traced molecules); the `.mat`'s raw/corrected/background series map to the
  raw/corrected/background trace layers; every molecule carries `molecule_key`
  (movie `sha256` + quantized `donor_xy`) linking it to the `/movies` row.
- **Correction factors** via `compute_corrected_fret`: the Appendix-B remapped α/γ
  (ADR-0008) injected as `METHOD_MANUAL` **only when γ > 0**; otherwise the apparent-E
  substrate (`METHOD_APPARENT_UNAVAILABLE`) is stamped explicitly. The committed Cy3-only
  fixture (`DefaultGamma = 0`) exercises the apparent-E path.
- **Bleach + window** via the M3 `compute_photobleach` on the imported corrected traces.
- **Category list** seeded from a caller-supplied vocabulary via
  `sync_conditions` + `set_category_list`.
- **Curated selection** via `set_curation_label(source=deeplasi-provisional)` for each
  molecule the SMD intensity cross-check matched, then `recompute_label_weights` for the
  decaying `w₀/(1+n_human)` weight (§7.5) — never a human `curation_label`.

**Deferred (documented data gaps, not fabrications):** Deep-LASI per-molecule **NN/HMM
category assignments** (undecoded MCOS blob / unparsed `.mat` fields — the vocabulary is
seeded so a future decode attaches assignments additively) and **real image patches**
(the writer accepts caller-supplied patches from the wizard, which opens the movie; else
zero-filled, the movie link makes crops re-cacheable).

### Consequences

- Good: schema-guard stays green (additive-only, no `schema.py` change); the three §9 M7
  sub-clauses (reconstruct from either coordinate source; curated subset + categories
  survive; SMD cross-check passes) are met and locked by `tests/test_reconstruct.py`.
- Good: the correction/curation semantics reuse the audited M3/M5 primitives, so imported
  factors and priors behave identically to natively-produced ones.
- Bad / trade-off: a reconstructed project's per-molecule *category* and *patch* pixels
  are not yet populated from Deep-LASI; a user re-curates from the provisional priors and
  the linked movie until the MCOS-category decode lands.
- Follow-up: the folder→project **wizard** (opens the movie → real patches + hashed
  `MovieMetadata`) and the **analysis-only degraded import** are the next M7 PRs; the
  MCOS NN/HMM/category decode is a later data-sourcing task.

## More information

- PRD §7.8 (reconstruction spec, lines 613–618), §9 M7 acceptance (line 730), §5.1/§5.3.
- Reuses ADR-0008 (correction remap), ADR-0003 (apparent-E), ADR-0023/0036 (provisional
  labels + decaying weight), ADR-0026 (photobleach), ADR-0016 (extraction store layout).
- Builds on PR #122 (`tether.io.intake`) and PR #124 (`tether.io.recover`).
- Code: `src/tether/project/reconstruct.py`; tests: `tests/test_reconstruct.py`.
