# 0041 — Persist the full Appendix-D.2 population model; add ebFRET as a second global idealizer

- **Status:** accepted
- **Date:** 2026-07-08
- **Deciders:** bioedca
- **PRD anchor:** §4.2, §10 (FR-IDEALIZE), Appendix D.2
- **Milestone:** M6

## Context and problem statement

The M2 one-click idealizer ([ADR-0024](0024-one-click-idealization-store.md)) persisted
only the per-state/per-molecule members of the tMAVEN model (`mean`, `var`, `tmatrix`,
`norm_tmatrix`, `idealized`, `state_path`) into `/idealization/{model}`. The M6 analysis
suite (PRD §10) needs the *population* kinetics — a TDP, dwell/rate fits, and state
populations — which requires the rest of the Appendix-D.2 `model` group: the N×N
transition-**`rates`** matrix, the **unnormalized** initial-state Dirichlet posterior
**`pi`** (concentration parameters — the real 281-mol reference has `pi.sum() ≈ 285`, the
trace count, so it must be divided by its sum for the initial-state probability vector),
the **normalized** state populations **`frac`** (sums to 1), and the variational
**`priors/`** hyperparameters. It also introduces a second global idealizer, **ebFRET**,
alongside consensus VB-HMM. How much of the D.2 member set should Tether persist, and how
is ebFRET wired without disturbing the M0 schema freeze?

## Decision drivers

- **PRD Appendix D.2** states Tether's `/idealization/{model}` layout "mirrors the full
  member set above (including `var`, `tmatrix`, `rates`, `pi`, and `priors/`)".
- **M0 schema freeze** ([ADR-0005](0005-m0-schema-freeze.md)): `/idealization` is a frozen
  *container* group; only additive per-record data may be written under it.
- **Provenance-first** ([ADR-0001](0001-provenance-first-data-model.md)): a stored model
  should be a faithful, self-describing artifact — the analysis suite reads the model,
  not a re-derivation.
- **Reuse over reinvent** (PRD §4): the rate/prior fields are computed by tMAVEN; Tether
  should faithfully carry what the sidecar produces, not recompute kinetics.

## Considered options

- **A.** Persist the full D.2 population set (`rates`, `pi`, `frac`, `priors/`) as
  optional additive members, and add ebFRET (`ebhmm`) as another `model_type` in the
  sidecar dispatch.
- **B.** Persist only `rates` (drop `pi`/`frac`/`priors`) — the minimum for a TDP/rate plot.
- **C.** Recompute `rates` in the base env from `norm_tmatrix` + frame time rather than
  carrying tMAVEN's `rates`.

## Decision outcome

Chosen option: **"A"**, because it matches the PRD Appendix-D.2 layout verbatim, keeps the
stored model a complete portable artifact, and stays within the schema freeze (every new
member is optional per-record data under the already-frozen `/idealization` container).
The members are written only when the fit produced them, so a threshold/k-means model —
which has no rate matrix or priors — simply omits them and reads back `None`. ebFRET is the
empirical-Bayes population HMM [vandeMeent2014] that pools information across molecules to
sharpen a consensus kinetic model; it writes the same D.2 `model` group, so no
per-algorithm storage branch is needed. Option B loses `pi`/`frac`/`priors` that §10 state
populations and a future ebFRET-seed reuse want; Option C would re-derive a quantity tMAVEN
already reports (drift risk, and it duplicates science Tether deliberately delegates).

### Consequences

- Good: `/idealization/{model}` round-trips the full population model; the M6 suite
  (TDP/dwell/rate) reads it directly; imported models (the tMAVEN return leg) are as
  complete as in-app fits.
- Good: `schema-guard` stays green — the frozen golden introspects an empty
  `create_project`, which contains no model subgroup, so additive members never touch it.
- Trade-off: the new members are all optional (`None`-tolerant), so readers must guard for
  legacy models written before M6 (they carry no `rates`/`pi`/`frac`/`priors`).
- Follow-up: the **live** 281-mol parity ratification for ebFRET and consensus VB-HMM
  (§9 M6 oracle b, against the M0.5-frozen §11.2 tolerance) lands in a follow-up PR — it
  runs in the out-of-band `sidecar.yml` job (a live tMAVEN fit), not on the PR matrix.

## More information

- PRD §4.2, §10, Appendix D.2; real reference model `example-data/tmaven-model/model.hdf5`.
- Sidecar dispatch: `tether.idealize._sidecar_runner._DISPATCH`; reader
  `tether.idealize.driver.read_model`; store writer/reader `tether.project.idealize`.
- tMAVEN modeler: `tmaven/tmaven/controllers/modeler/modeler.py` `run_ebhmm` (ebFRET),
  `run_vbconhmm` (consensus VB-HMM).
- Related: [ADR-0024](0024-one-click-idealization-store.md) (the model layout it extends),
  [ADR-0007](0007-parity-is-statistical.md)/[ADR-0009](0009-parity-metrics-and-freeze.md)
  (the parity tolerance the follow-up ratifies against).
- Consensus: ebFRET is the empirical-Bayes population HMM for smFRET —
  [van de Meent et al. 2014, *Biophysical Journal*](https://consensus.app/papers/details/94863e6d77fa5458a6c4d0f9b231781f/).
