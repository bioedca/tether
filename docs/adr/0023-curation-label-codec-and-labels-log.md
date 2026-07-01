# 0023 — Curation-label codec + append-only `/labels` provenance log; category logging deferred to M4

- **Status:** accepted
- **Date:** 2026-07-01
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §5.1 (`/labels` provenance), §7.5 (FR-ML — reject semantics, weights), §7.3 (curation keys)
- **Milestone:** M2 (S5)

## Context and problem statement

M2 curation (PRD §7.5) must log every accept/reject as a provenance-stamped label
so the M5 ranker can train on it and rejects behave correctly. The `/labels/table`
compound dtype was **frozen at M0** (ADR-0005) with the fields `molecule_key,
labeler, timestamp, source_file, source, weight, label_value, condition_id` and a
single `<i4` `label_value` — no "event kind" discriminator. How should an
accept/reject/un-reject be encoded into `label_value` and the molecule's
`curation_label`, and does a *category* assignment (§7.6) also write a `/labels`
row given that single field?

## Decision drivers

- **Schema freeze (ADR-0005).** `label_value` is one frozen `<i4`; no kind column
  can be added without a structural change + version bump. Writing rows must stay
  additive **data**.
- **ML-label cleanliness (§7.5).** The ranker is a binary good/bad quality model;
  the accept/reject signal it reads must not be polluted by unrelated events.
- **Field independence (§5.1/§7.6).** `curation_label` (accept/reject),
  `category` (editable-list value), and `quality_class` (ML output) are three
  *independent* molecule fields; assigning a category does **not** imply accept.
- **Reversibility, never-drop (§7.5).** A reject is a reversible sticky tag with a
  toggleable exclusion filter, kept as an ML label — never a deletion.
- **Category prerequisites are M4.** The editable per-condition category list and
  its integer↔category lookup (§7.6, on `/conditions`) do not exist until M4, so
  an integer category keystroke (`1`–`9`) has no string value to record at M2.

## Considered options

- **A — one signed codec `{UNCURATED 0, ACCEPT +1, REJECT −1}` for both
  `curation_label` and `label_value`; log accept/reject/un-reject; defer category
  `/labels` logging to M4.**
- **B — overload `label_value` with a reserved band** (e.g. accept/reject small
  ints, categories at a `±1000` offset) so category events share `/labels` now.
- **C — binary `{1 good, 0 bad}` `label_value`** (sklearn convention) distinct
  from a differently-coded `curation_label`.

## Decision outcome

Chosen option: **A**. One `IntEnum` codec `CurationLabel = {UNCURATED 0, ACCEPT +1,
REJECT −1}` is shared by `/molecules.curation_label` (current human state) and each
`/labels.label_value` event; `curation_label` reflects the molecule's most recent
human accept/reject/clear event and the ranker reads a clean `+1/−1` signal.
`UNCURATED = 0` matches the `_UNCURATED_LABEL` an extraction already writes
(`tether.imaging.extract`). A human accept/reject/un-reject appends a full-provenance
`/labels` row **and** sets `curation_label`; un-reject reverses a reject only (a
no-op on any other state — never clobbers an accept, never logs a spurious clear).
Human labels carry `weight = 1.0` (§7.5); the `source`-driven decay is an M5 retrain
concern.

Two writer rules fall out of §5.1's field independence and HDF5's lack of a
transaction, and are load-bearing:

- **Only a human label owns `curation_label`.** `curation_label` is the human
  accept/reject state (§5.1:260); a provisional source (`deeplasi-provisional`,
  `cross-condition-seed`) is a cold-start ML prior that lives **only** in a
  `/labels` row and must never mutate `curation_label` (else a machine seed would
  look human-curated — corrupting the "has a human curated this yet?" signal the
  active-learning queue and multi-curator reconciliation depend on).
- **Audit row first, then state.** `r+` is not transactional, so the `/labels`
  audit row is appended *before* `curation_label` is set. A crash between them
  leaves at worst a re-derivable orphan `/labels` row, never an unaudited state
  change. A matched-row `condition_id` divergence is refused (never a silent
  mis-attribution of the label's condition scope).

**Category `/labels` logging is deferred to M4.** Option B's reserved-band overload
would pollute the ML label space and hard-code an offset before the category list
exists; category's authoritative home is `/molecules.category`, and its editable
list + integer↔category lookup are M4 (§7.6). Rejected B. Rejected C because a
second, divergent codec for `curation_label` invites accept/reject/state drift; a
single shared signed codec is simpler and self-consistent (M5 maps `−1→bad` at
train time).

### Consequences

- Good: writing labels is additive data only — `schema-guard` stays green (no
  structural change); the ranker's training signal is a clean `±1`.
- Good: reject is reversible + sticky (persists per-molecule on the stable
  `molecule_key`, so it carries across files, §7.10) with a toggleable exclusion
  filter (`curation_filter_mask`, `include_rejected=False` by default, §7.5).
- Bad / trade-off: a category assignment is not yet in `/labels` — the PLAN §6 S5
  "accept/reject/**category** writes a row" is split, the category leg landing at
  M4 with the list + lookup. The S5 **test gate** (accept/reject rows + reversible
  reject + exclusion) is met in full.
- Follow-up: enforced by `tests/test_labels.py` (provenance completeness,
  reversibility+audit, exclusion toggle, frozen `/labels` dtype) + the standing
  `schema-guard` gate. M4 homes category→`/labels` (its own ADR if the encoding is
  non-trivial); M5 consumes the `±1` labels + applies the `source` weight decay.

## More information

- PRD §5.1 (`/labels` provenance fields, `source ∈ {human, deeplasi-provisional,
  cross-condition-seed}`, mutable `weight`), §7.5 (reject semantics, `w = w₀/(1+n_human)`),
  §7.3 (curation keys), §7.6 (editable category list, M4).
- Implements the writer behind the M2 S2 keymap (ADR context in `tether.gui.curation`):
  `CurationHandlers.accept/reject` wire to `tether.project.labels`.
- Related: [ADR-0005](0005-m0-schema-freeze.md) (the freeze this respects),
  [ADR-0016](0016-extraction-trace-store-layout.md) (`molecule_key`, the
  `curation_label` apparent-E substrate).
