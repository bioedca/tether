# 0024 — `/idealization/{model}` store layout, per-molecule input-provenance hash (staleness), and auto state-count by max-ELBO

- **Status:** accepted
- **Date:** 2026-07-01
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §5 (`/idealization/{model}` — state path, means, transition matrix, ELBO, per-molecule input-provenance hash), §7.4 (one-click idealization + staleness), §4.2/§4.3 (`tether.idealize` staleness tracking)
- **Milestone:** M2 (S6 — headless core; the dock `I`-key + Viterbi step overlay is the GUI follow-up)

## Context and problem statement

M2 S6 (PLAN §6) turns a selection of extracted molecules into an in-app vbFRET /
consensus VB-HMM idealization written back into the `.tether`. The sidecar driver
(`tether.idealize.run_vbfret`, M0.5) already fits a model from an SMD; what was
undefined is the **store-integration contract**: how the fit is persisted under the
M0-frozen `/idealization` group, how the number of states is chosen when the user
does not fix it, and how a later change to the inputs (a re-extraction, or an M3
correction) is detected so a stale idealization is never silently trusted (PRD §5
requires each model be "stamped with a per-molecule provenance hash of the inputs";
§7.4/§4.2 require staleness tracking).

## Decision drivers

- **Schema freeze (ADR-0005).** `/idealization` is a frozen §5 container group; a
  *model* subgroup must be additive **data** — no structural change, `schema-guard`
  stays green (the guard introspects an empty `create_project`, which contains no
  model subgroup).
- **Provenance travels with the datum (ADR-0001; §5).** A persisted idealization must
  carry enough to know *what it was computed from*, so a downstream change invalidates
  it deterministically rather than leaving a wrong-but-plausible model attached.
- **Statistical correctness (§7.4).** When the state count is not user-fixed it must be
  chosen by a defensible criterion, not a hardcoded guess.
- **Headless-first (§4.2, §7.11).** The whole path is a `tether.project` function the
  batch runner and the GUI both call; the GUI adds no logic.
- **Apparent-E MVP (ADR-0016).** At M2 the fit input is the background-subtracted
  `corrected` intensity (disk sum − local background — the M1 meaning; photophysical
  α/γ corrections are M3), consistent with `tether.fret.apparent_fret`.

## Considered options

**Model persistence**
- **A — one additive subgroup `/idealization/{model_name}`** holding the model summary
  (`mean`/`var`/`tmatrix`/`norm_tmatrix`/`elbo`), the `(n, T)` `idealized` levels +
  int64 `state_path`, and per-molecule `molecule_key` / `molecule_id` / `input_hash`,
  plus provenance attrs (`type`, `nstates`, `intensity_quantity`,
  `nstates_selected_by`, `app_version`, `created_utc`).
- **B — one flat model per project** (no `{model_name}`): cannot hold the multiple
  models §7.4/§6 need (vbFRET vs consensus vs a re-import).

**Staleness stamp (the per-molecule input hash)**
- **A — SHA-256 of the exact windowed input intensities** (donor+acceptor over each
  molecule's analysis window, cast to `float64`, folded with the quantity name).
- **B — hash the correction parameters / app-version only**: misses an input-value
  change that shares the same params (e.g. a re-extraction at the same settings).
- **C — no hash; recompute on open**: violates §5 (the stamp must be stored) and
  cannot detect a change made outside the app.

**State-count selection**
- **A — auto = maximum ELBO over a small grid** (`nstates ∈ {1,2,3,4}`), with an
  explicit `nstates` as a manual override.
- **B — fixed `nstates` only**: no one-click "just idealize it" path.
- **C — BIC/AIC**: not the variational-evidence criterion the embedded tMAVEN / vbFRET
  lineage uses.

## Decision outcome

Chosen: **A / A / A.**

`idealize_molecules(project, molecule_keys, …)` reads `/molecules` + `/traces`, builds
an SMD over exactly the selected molecules (analysis window from the editable
`analysis_window`, defaulting to the native `frame_range`), fits via the sidecar, and
writes one additive **`/idealization/{model_name}`** subgroup. Each molecule's
`input_hash` (`input_trace_hash`) is the SHA-256 of its exact windowed donor+acceptor
input; `stale_molecule_keys` recomputes it from the *current* store and reports the
molecules whose inputs diverged — the §5 staleness signal the M3 re-idealize flow and
the dock consume. When `nstates` is `None` the fit is repeated over `nstates_grid` and
the **maximum-ELBO** model is kept — the standard, statistically-consistent VB-HMM
state-count selection for smFRET (max evidence beats max likelihood [Bronson2009];
ELBO-maximization carries theoretical guarantees [CheriefAbdellatif2018]); the per-`k`
ELBOs and the selection mode are recorded for provenance. The `nstates_grid` default is
registered in PRD §11.2 (not hardcoded).

The **raw parity acceptance** (a fresh fit reproduces the committed reference within the
M0.5-frozen §11.2 tolerance) remains `test_parity_sidecar` at the `run_vbfret` layer;
this PR adds the **store-integrated** path and its own live cross-seed check
(`@pytest.mark.sidecar`, deselected from CI), plus a full headless suite (a faked
sidecar) that locks the write layout, auto-nstates, subset selection, the window/hash
semantics, staleness detection, and `schema-guard`-green additivity.

### Consequences

- Good: writing a model is additive data only — `schema-guard` stays green (proved by a
  `diff_manifest(build_manifest(), introspect(file))` test); multiple named models
  coexist (vbFRET / consensus / re-import).
- Good: a re-extraction or an M3 correction that changes a trace flips that molecule's
  `input_hash`, so the idealization is provably stale for it and only it — the §5/§7.4
  staleness contract, testable headlessly (no sidecar).
- Good: one-click "just idealize" works (auto max-ELBO) while a power user pins
  `nstates`; the choice + its evidence are stamped into the model.
- Trade-off: auto mode runs the sidecar once per grip entry (N fits). Acceptable at M2
  selection sizes; a future PR may prune the grid or early-stop on a declining ELBO.
- Scope split: the GUI (`I`-key handler + Viterbi step overlay in the trace dock +
  computer-use live-smoke) is a separate M2 S6 PR-B — it cannot merge autonomously
  because the CLAUDE.md GUI gate requires a computer-use live-smoke unavailable in an
  autonomous run.

## More information

- PRD §5 (`/idealization/{model}` fields + input-provenance hash), §7.4 (one-click
  idealize + staleness + non-destructive re-import), §4.2/§4.3 (`tether.idealize`),
  §11.2 (the state-count-selection tunable row this PR adds).
- Implements the headless seam behind the future dock `I` key; the return-leg re-import
  (M2 S7) reuses `tether.idealize.match_return_leg`.
- Related: [ADR-0005](0005-m0-schema-freeze.md) (the freeze this respects),
  [ADR-0009](0009-parity-metrics-and-freeze.md) (the parity tolerance the fit is judged
  against), [ADR-0016](0016-extraction-trace-store-layout.md) (the `corrected` intensity
  input + `molecule_key`).
- [Bronson2009] Bronson, Fei, Hofman, Gonzalez & Wiggins, *Biophys. J.* (2009) — vbFRET;
  max-evidence (ELBO) VB-HMM model selection. [CheriefAbdellatif2018] Chérief-Abdellatif,
  *Consistency of ELBO maximization for model selection* (2018).
