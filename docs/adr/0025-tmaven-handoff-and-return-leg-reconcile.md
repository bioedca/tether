# 0025 — Bidirectional tMAVEN hand-off + non-destructive return-leg re-import with a per-trace reconcile

- **Status:** accepted
- **Date:** 2026-07-02
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §7.4 (bidirectional hand-off, return-leg re-import, the reconcile prompt), §5.3 (interoperability — the retained store is authoritative; recover the link by exact intensity matching), Appendix D.1 (SMD container; no per-molecule slot; exporter subsets/reorders)
- **Milestone:** M2 (S7 — headless core; the interactive per-trace reconcile *dialog* is the GUI follow-up)

## Context and problem statement

M2 S7 (PLAN §6) makes the tMAVEN integration **bidirectional**: a one-click "Hand to
tMAVEN" exports an SMD the standalone GUI opens directly, and the **return leg**
re-imports the edited session — an idealization model, and/or edited analysis windows
and integer classes — back into the `.tether`. The building blocks already exist from
M0.5 S1: the SMD codec (`tether.idealize.smd`, ADR-0002) and the exact-intensity
return-leg matcher (`tether.idealize.match_return_leg`). What is undefined is the
**store-integration contract**: how the hand-off SMD is assembled from the store, how a
returning session is resolved and re-imported *non-destructively*, and how the per-trace
differences (idealization / analysis-window / class) are surfaced for the user to accept
or reject rather than silently overwritten (PRD §7.4).

Two frozen-PRD facts constrain the design (Appendix D.1): tMAVEN's SMD writer has **no
per-molecule slot**, and its exporter **subsets/reorders** molecules by the GUI
selection mask. So a returning SMD's coordinates (even Tether's own, if they survived)
are **not trusted**; identity must be recovered another way.

## Decision drivers

- **The retained store is authoritative (§5.3, ADR-0002).** Tether re-resolves each
  returning trace to its molecule by **exact intensity-trace matching** of the SMD `raw`
  series, with molecule-id / row order as a *hint only*; unmatched returning traces are
  **reported, never guessed**.
- **Non-destructive (§7.4).** A return leg must never clobber existing data: an imported
  model lands as a *new* `/idealization/{model}`, and window/class edits are applied only
  on explicit per-trace acceptance.
- **Schema freeze (ADR-0005).** The imported model is additive **data** under the frozen
  `/idealization` container — no structural change, `schema-guard` stays green. An
  accepted window edit rewrites the mutable `/molecules.analysis_window` *field* (data,
  not structure).
- **Provenance travels with the datum (ADR-0001).** An imported model records its source
  SMD + model file and the reconcile match counts.
- **Staleness is a data-model property (ADR-0024, §5.1).** An accepted analysis-window
  change must re-invalidate that molecule's dependent idealizations without a new flag.
- **Category↔class is M4 (ADR-0023, §7.6).** Only the non-lossy `class 0 ↔
  uncategorized` leg of the integer↔category map exists before the M4 editable list.
- **Headless-first (§4.2, §7.11).** The whole path is `tether.project.handoff`; the GUI
  reconcile dialog adds no logic.

## Considered options

**Return-leg identity**
- **A — exact intensity match of SMD `raw` vs the retained store** (id/order as a hint).
  tMAVEN preserves `raw` byte-for-byte across a save (corrections/idealization live in
  separate arrays), so equality *is* the correct identity test.
- **B — trust the returning coordinates / molecule ids**: broken by Appendix D.1 (no
  per-molecule slot; the exporter drops/reorders) — the exact gap §5.3 calls out.

**Re-import destructiveness**
- **A — new `/idealization/{model}`; window/class edits applied only on per-trace
  accept** (a reconcile diff the caller resolves).
- **B — overwrite the matched model / windows in place**: violates §7.4 ("rather than
  silently overwriting") and loses the prior idealization.

**Preview vs commit**
- **A — split `read_return_leg` (pure preview) from `apply_reconcile` (commit)**, both
  over one deterministic match+diff core; the GUI renders the preview, the user's
  decisions drive the commit.
- **B — a single apply that also returns the diff**: couples display to mutation; harder
  to show a dry-run prompt.

**Imported-model writer**
- **A — reuse the single `/idealization` writer** (`write_idealization_model`, factored
  out of the M2 S6 fitter) with `nstates_selected_by="imported"` + return-leg provenance
  attrs.
- **B — a second bespoke writer in `handoff`**: duplicates the atomic staging/swap and
  risks the two writers drifting on the frozen layout.

## Decision outcome

Chosen: **A / A / A / A.**

`tether.project.handoff` implements the round trip over the existing SMD codec + matcher:

- `hand_off_to_tmaven(project, molecule_keys, out_path, …)` writes a persistent SMD the
  standalone GUI opens: `raw` from the selected molecules' `corrected` traces, the
  per-trace analysis windows as `pre_list`/`post_list`, Tether coordinates + identities
  in the superset group, and neutral integer `classes` (`0` = uncategorized — the
  category→class map is M4).
- `read_return_leg(...)` reads the returning SMD, matches it to the store by intensity
  (`match_return_leg`, id-order hint), and returns a `ReconcileReport`: one
  `TraceReconcile` per matched molecule (an analysis-window diff and/or a class diff) +
  the unmatched returning rows. Read-only — this is what the GUI reconcile prompt renders.
- `apply_reconcile(...)` re-runs the same deterministic match (the intensity identity does
  not depend on windows/classes), then commits only the accepted changes:
  1. If `import_idealization` + a model file: read the Appendix-D.2 model, remap its rows
     to store molecules through the match (unmatched dropped + reported), recompute each
     matched molecule's input-provenance hash over the **returning window** (the window
     the model was fit over), and write a **new** `/idealization/{model_name}` via the
     shared `write_idealization_model` (`selected_by="imported"`, source SMD/model + match
     counts stamped). Refuses to clobber an existing model unless `overwrite=True`.
  2. Accepted analysis-window edits → `/molecules.analysis_window`, which **re-stales**
     those molecules' dependent idealizations: `stale_molecule_keys` recomputes each
     model's hash over the *new* window and reports the divergence — no new flag needed
     (ADR-0024).
  3. Accepted class changes: `class 0` clears `category` to uncategorized; a **non-zero**
     class is recorded as *deferred* (no write) pending the M4 integer↔category table.

`accept_windows` / `accept_classes` are `True` (all applicable) or an iterable of
`molecule_id`, mirroring the per-trace accept/reject the GUI dialog will drive.

The match tolerance is a tight absolute-equality guard (`atol=1e-6`), **not** a scientific
tunable — `raw` is preserved exactly across a tMAVEN save — so it gets no PRD §11.2 row,
consistent with how `match_return_leg` landed at M0.5.

### Consequences

- Good: identity survives tMAVEN's coordinate-dropping/reordering exporter; a foreign or
  edited-past-recognition trace is reported unmatched, never mis-attributed.
- Good: fully non-destructive — a new named model per re-import, window/class edits only
  on explicit acceptance; `schema-guard` stays green (proved by a
  `diff_manifest(build_manifest(), introspect(file))` test).
- Good: an accepted window edit re-stales dependent idealizations for free, reusing the
  ADR-0024 staleness machinery; the imported model itself is consistent (its hash is over
  the returning window).
- Good: one writer for the `/idealization` layout (fitter + importer) — a single place the
  frozen container's contents are defined.
- Trade-off: `apply_reconcile` re-reads + re-matches rather than consuming the prior
  report's arrays (a small recompute) — chosen so the report stays a lightweight,
  display-only value and the commit is self-contained/idempotent.
- Scope split: the interactive per-trace reconcile **dialog** wired into the shell (with
  a real-GL / computer-use GUI smoke) is the M2 S7 **PR-B** follow-up; this PR is the
  Qt-free headless core the dialog will call. Class `> 0` reconciliation lands with the
  M4 editable category list + integer↔category lookup (ADR-0023).
- Note (frozen-PRD asymmetry): PRD §9 M2's *acceptance* column has no hand-off/re-import
  clause, so the tests validate against **FR-IDEALIZE §7.4 + §5.3**, not a §9 M2 oracle
  (round-trip integrity is additionally exercised at M6 and by the §2 round-trip/schema
  fixture).

## More information

- PRD §7.4 (bidirectional hand-off + return leg + reconcile prompt + integer↔category),
  §5.3 (authoritative store, exact intensity matching), Appendix D.1 (SMD container).
- Reuses [ADR-0002](0002-smd-superset-round-trip.md) (SMD superset + the round-trip gap
  the intensity matcher closes) and [ADR-0024](0024-idealization-store-layout-staleness-and-nstates.md)
  (the `/idealization` writer + staleness); defers class `> 0` to
  [ADR-0023](0023-curation-label-codec-and-labels-log.md)'s M4 category list.
- Related: [ADR-0005](0005-m0-schema-freeze.md) (the freeze this respects),
  [ADR-0001](0001-provenance-first-data-model.md) (provenance travels with the datum).
