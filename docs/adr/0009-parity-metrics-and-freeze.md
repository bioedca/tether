# 0009 — Idealization-parity metric definitions and the M0.5 freeze

- **Status:** accepted
- **Date:** 2026-06-26
- **Deciders:** bioedca
- **PRD anchor:** §7.4 (parity definition), §11.2 (the tolerance row), §8 NFR-VALID(b), §12.6 (`sidecar/parity`)
- **Milestone:** M0.5 S4 (ratifies [ADR-0007](0007-parity-is-statistical.md); the M2/M6 hard gate)

## Context and problem statement

[ADR-0007](0007-parity-is-statistical.md) decided that idealization parity is
*statistical, asserted against a frozen tolerance*. This ADR records **how the
four §11.2 numbers are computed and frozen** — the part 0007 deferred to "M0.5
S4 replaces the provisional defaults with measured values." Two concrete design
questions had to be answered to make the gate well-defined: (1) how do you
compare two idealizations whose state **labels are an arbitrary permutation**
(tMAVEN's VB init is random), and (2) what **freeze rule** turns a measured
spread into a single non-flaky bound?

## Decision drivers

- tMAVEN self-reseeds (`initialize_gmm` → `np.random.seed`), so state labels and
  ordering differ run-to-run; a naive frame-by-frame label comparison is
  meaningless without alignment.
- The four metrics must each have one unambiguous definition that CI and the
  measurement harness share (`compare_models` is the single source of truth).
- The frozen bound must not flake on an unseen seed, yet must not be looser than
  the §11.2 design intent unless the data demands it.
- FRET states are 1-D and ordered by efficiency — a natural canonical order.

## Considered options

- **Label alignment:** (A) sort states by ascending mean FRET and relabel both
  models to that canonical order; (B) Hungarian assignment on a cost matrix; (C)
  greedy nearest-mean matching. **A** is exact for 1-D ordered FRET states, has no
  ties in practice, and is trivially reproducible.
- **Freeze rule:** (A) freeze exactly at the measured worst case; (B) freeze at
  the *more permissive* of {provisional default, measured-worst ± safety margin};
  (C) keep the provisional defaults regardless.

## Decision outcome

Chosen: **canonical-mean alignment (A)** + **freeze rule B** (margin = 0.5:
ceilings ×1.5, floors lowered by `0.5·(1−worst)`). The four metrics are:

1. **state count** — fraction of traces where ref and test occupy the same number
   of distinct states (informative traces only);
2. **per-state mean ΔE** — max |Δ| between mean-sorted matched levels (`inf` if
   the state counts differ, so a mismatch can never read as agreement);
3. **Viterbi per-frame agreement** — fraction of in-window frames whose canonical
   state matches;
4. **relative ELBO** — `|ΔELBO| / |ELBO|`.

**Measured result (M0.5 S4, 2026-06-26).** 20 self-reseeded `vbconhmm`
(vb Consensus HMM) fits per fixture — the 281-mol fits compared to the *committed
reference model* (`model_281mol.hdf5`), the 4-mol fits cross-seed — gave a
**numerically negligible** spread on all four metrics: state-count and Viterbi
agreement exactly 1.0; per-state |ΔE| ≤ 8.3 × 10⁻⁹; relative ΔELBO ≤ 1.7 × 10⁻¹⁰.
The vb Consensus HMM converges to the same global optimum regardless of seed on
this data, so the provisional §11.2 tolerances are **confirmed with overwhelming
margin** and frozen unchanged:

| metric | frozen bound |
|---|---|
| state count exact | ≥ 90% of traces |
| per-state mean \|ΔE\| | ≤ 0.02 (FRET units) |
| Viterbi per-frame agreement | ≥ 95% |
| relative \|ΔELBO\| | ≤ 0.01 |

The full per-run evidence lives in `schema/parity_tolerance.json`; the freeze is a
one-time ratification (re-freeze ⇒ a new ADR + a deliberate re-run).

### Consequences

- Good: a permutation-invariant, single-definition gate; the measured spread is
  far inside the bound, so `sidecar/parity` will not flake.
- Scope: the measurement covers the **vb Consensus HMM** path (the committed
  reference type + the M6 method + the Appendix-D.2 fixture type). Per-trace
  vbFRET parity (M2 one-click) is asserted against the **same** frozen row; its
  own cross-seed spread is not separately measured here.
- Follow-up: `sidecar.yml` runs the live assertion (dispatch + nightly). Promoting
  `sidecar/parity` to a **required** status check (per §12.6/§12.10) is gated on a
  first confirmed-green dispatch run that proves tMAVEN installs on the runner.

## More information

PRD §7.4, §11.2, §8 NFR-VALID(b), §12.6; `src/tether/idealize/parity.py`
(`compare_models`, `freeze`); `scripts/measure_parity.py`;
`schema/parity_tolerance.json`. Science: vbFRET = VB-EM HMM with ELBO model
selection [Bronson 2009, *Biophys J*]; consensus VB-HMM/ebFRET [van de Meent
2014, *Biophys J*]. Builds on [ADR-0007](0007-parity-is-statistical.md).
