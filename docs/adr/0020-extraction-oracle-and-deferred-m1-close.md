# 0020 — The extraction-vs-Deep-LASI acceptance oracle; M1 close deferred (detection-faithfulness gap surfaced)

- **Status:** accepted
- **Date:** 2026-06-30
- **Deciders:** bioedca
- **PRD anchor:** §9 M1 (recall ≥ 95 % @ 1px, per-frame integrated-intensity Pearson r ≥ 0.99, registration RMS ≤ 0.5 px); §8 NFR-VALID (a); §7.11; §2.2 / NFR-FIXTURES (gated tier); Appendix A
- **Milestone:** M1 (S9 — PR-C2 of the PR-C split: the acceptance oracle + `large-fixtures.yml`; the M1 close + `v0.1.0` tag is **deferred to PR-C3**, see below)

## Context and problem statement

ADR-0018/0019 landed the `tether extract` CLI and the native-**and**-imported-`.tmap`
registration paths, deferring the **acceptance oracle** + the M1 close to PR-C2. PR-C2
must answer two questions:

1. **How is §9 M1 acceptance measured**, and where does the measurement run, given the
   real oracle needs the gated ~0.9 GB UCKOPSB movie + the full ~250-molecule Deep-LASI
   export, neither committable (ADR-0018 never-fabricate; PLAN §2.2)?
2. **Does native extraction actually meet the §9 M1 tolerance** on the UCKOPSB pair —
   the gate for closing M1 + tagging `v0.1.0`?

## Decision drivers

- The §9 M1 acceptance is a **conjunction** (recall ∧ Pearson ∧ RMS); it must be
  measurable on real data, reproducible, and never weakened to pass (PLAN §1.3.8).
- `main` stays green; a structural CI run must not block on an LFS / ~GB pull (PLAN §2.2).
- One concern per PR; land what is green, defer the rest, surface blockers honestly.

## Considered options and decision

### The oracle (a pure scorer + a thin on-disk convenience). *Chosen.*

`tether.project.oracle` is dependency-light (numpy only in the core):

- `match_coordinates` — deterministic **greedy unique nearest-neighbour** within the
  1 px tolerance (no Hungarian is required by §9 M1; greedy global-min is order-independent
  and stops one detection claiming two truths).
- `evaluate_extraction` — the pure scorer over plain arrays: recall (Deep-LASI molecules
  as the denominator), coordinate RMS of matched pairs, and per-frame Pearson r both
  **per-molecule** (the gated, robust statistic) and **pooled** (reported, weaker —
  between-molecule variance inflates it). Pearson is settled textbook linear-agreement
  math (Consensus N/A — it is not a contested FRET/biophysics fact; the only choice is
  reporting granularity, resolved toward the robust per-molecule median).
- `evaluate_project` — reads a written `.tether` (`read_molecules` / `read_traces`) and a
  Deep-LASI `.mat`, then delegates. Registration RMS is carried as a field (the imported
  `.tmap` leg trusts Deep-LASI's registration → `nan` → not gated; the native bead-fit
  RMS ≤ 0.5 px is locked separately in `test_register`, ADR-0014).

### Where the real oracle runs: gated tier + structural CI + a local proof. *Chosen.*

The committed default-CI tests are **structural** (pure-scorer unit tests; a real-slice
Pearson check on the committed 4-molecule Deep-LASI slice; an `evaluate_project` wiring
test) — none claim the full acceptance numbers, none need the gated movie. The full
acceptance runs (a) **locally** via `scripts/run_m1_oracle.py` (the measured numbers
recorded below + in `tests/fixtures/PROVENANCE.md`), and (b) in the new gated
`large-fixtures.yml` workflow (`-m large`, manual + weekly, **never a required check**),
which skips-if-absent where the uncommitted movie is unavailable. This mirrors the
existing `@pytest.mark.large` 281-mol pattern and the `sidecar` gate (PLAN §2.2, ADR-0009).

### M1 close: **DEFERRED** — native extraction does NOT yet meet §9 M1. *Chosen, honestly.*

Running the oracle on the real UCKOPSB pair (imported `.tmap` leg) measured:

| metric | measured | §9 M1 gate |
|---|---|---|
| matched-molecule recall @ 1px | **0.204** (51/250) | ≥ 0.95 |
| donor per-frame Pearson r (median, matched) | 0.988 (raw) | ≥ 0.99 |
| acceptor Pearson r (median, matched) | 0.876 (raw) | ≥ 0.99 |
| coord RMS of the 51 matched | 0.289 px | (the matched are exact) |

The gap is **at the detection stage**, confirmed not to be an oracle/coordinate bug:
coordinate frames are perfectly aligned (identical ranges, zero offset, the 51 matched
are exact, no transpose). Raw donor detection finds **199 spots** (Deep-LASI: 250) of
which only **51 (25.6 %)** are within 1px of a Deep-LASI donor; acceptor 181 detected,
16.8 % recall. Tether and Deep-LASI detect **largely different molecule sets**. The M1
detector (S1–S3) was validated only on the 64×64×**50**-frame fixture — a *single*
detection block — so the multi-block moving-average / max-projection behaviour at full
scale (1700 frames → ~34 blocks) was never exercised and diverged silently. This is
exactly the gap the §9 M1 acceptance gate exists to catch; weakening it is disallowed
(PLAN §1.3.8).

## Decision outcome

PR-C2 lands the acceptance **instrument** — the oracle module, the structural default-CI
tests, the gated `large-fixtures.yml` + the gated acceptance test (currently `xfail`,
`strict=False`, with the reason pointing here), and `scripts/run_m1_oracle.py` — and
records the measured shortfall. **M1 stays OPEN.** Closing M1 + tagging `v0.1.0` move to
**PR-C3**, which must diagnose and fix the detection faithfulness (re-read `cumIMG.m`,
`Wave_Partfind.m`, `findPart.m`; reproduce Deep-LASI's detection image + spot set on the
*full* movie), re-run `scripts/run_m1_oracle.py` to recall ≥ 0.95 / Pearson ≥ 0.99, remove
the `xfail` (the gated leg XPASSes), then close M1.

### Consequences

- Good: the §9 M1 acceptance is now **measurable and reproducible**; the detection gap is
  precisely localized rather than shipped unnoticed under a too-small fixture.
- Good: no schema change (additive read-only; `schema-guard` green); no new §11.2 tunable;
  no conda-lock change (numpy-only core; scipy/h5py already locked for the on-disk path).
- Cost: M1 close + `v0.1.0` slip to PR-C3; the gated acceptance test is `xfail` until the
  detector is fixed. The rotation/flip **apply** for an imported `.tmap` (ADR-0019) also
  remains deferred — the real UCKOPSB `.tmap` has empty rotation/flip, so it does not block.

## More information

PRD §9 M1, §8 NFR-VALID (a), §7.11, §2.2, Appendix A; ADR-0011 (recall homed at M1),
ADR-0014 (RMS gate / native-vs-imported map), ADR-0015 (donor-anchored coloc), ADR-0016
(trace store), ADR-0017 (the Deep-LASI reader the oracle consumes), ADR-0018/0019 (the
CLI + imported `.tmap`). `src/tether/project/oracle.py`, `tests/test_oracle.py`,
`.github/workflows/large-fixtures.yml`, `scripts/run_m1_oracle.py`.
