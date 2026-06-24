# 0004 — Pin-and-hold conda-lock with an isolated tMAVEN sidecar lock

- **Status:** accepted
- **Date:** 2026-06-24
- **Deciders:** bioedca
- **PRD anchor:** §4.1 (stack), §4.3 (sidecar); NFR-REPRO
- **Milestone:** M0

## Context and problem statement

tMAVEN pins `numpy<2` + PyQt5, while Tether's GUI is PySide6 on current numpy
with a Numba-bounded numpy ceiling. These cannot coexist in one environment. How
do we get reproducible builds across three OSes without a dependency conflict?

## Decision drivers

- Numba constrains the base numpy upper bound; the GUI stack (napari/PySide6/
  pyqtgraph/scikit-image) is tightly coupled to that numpy + Qt.
- tMAVEN's `numpy<2`/PyQt5 must never contaminate the base stack.
- Reproducibility requires exact, frozen pins — not track-latest solving.

## Considered options

- **A. Two isolated `conda-lock` stacks** — a base lock (PySide6/current numpy)
  and a separate `sidecar/conda-lock.yml` (`numpy<2` + PyQt5 + trimmed tMAVEN
  deps); **pin-and-hold**, frozen per release, bumped only deliberately. The
  sidecar runs out-of-process.
- **B. One environment** with pinned conflicting deps (impossible).
- **C. Track-latest** with CI catching breakage.

## Decision outcome

Chosen option: **A**. The two locks are the single source of truth and stay
**isolated**; `conda-lock-verify` checks both. The sidecar trims tMAVEN's
`install_requires` (omit `biasd`, bound `numba`) so it can ship offline (M9).

### Consequences

- Good: reproducible, conflict-free, offline-bundleable; no silent drift.
- Trade-off: dependency bumps are deliberate work (regenerate + verify both
  locks); Dependabot covers only `pip` + `github-actions`, so `deps-audit.yml`
  backstops the conda locks.
- Follow-up: PLAN invariant — "never casually bump a conda-lock; keep the
  sidecar isolated".

## More information

PRD §4.1, §4.3, §12.8; ADR-0006 (sidecar mechanism).
