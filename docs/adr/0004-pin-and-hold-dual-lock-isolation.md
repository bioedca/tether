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

## Amendments

### 2026-07-06 — Maintainer-approved base-lock bump (M5 ranker deps)

This ADR's "bumped only deliberately" clause was exercised for the first time.
Maintainer **bioedca** authorized adding `scikit-learn` + `xgboost` to the base
`environment.yml` for the M5 per-condition quality ranker (PRD §7.5, PLAN §9 M5),
and the base `conda-lock.yml` was regenerated for all four platforms
(linux-64/osx-64/osx-arm64/win-64) with `conda-lock==4.0.1`. The resolved stack
holds numpy at 2.1.3 — inside the Numba `<2.2` window — with scikit-learn 1.9.0
and xgboost 3.3.0; the drifted GUI stack was re-verified (headless `pytest-qt`
suite green + a real-GL desktop smoke). The sidecar lock was **not** touched.

This approval is scoped to **this single re-lock**; it does not standing-authorize
future base-lock bumps, which remain deliberate, per-change decisions.

### 2026-07-09 — Maintainer-approved base-lock bump (M6 plot-export backend)

The "bumped only deliberately" clause was exercised a second time. Maintainer
**bioedca** authorized adding `matplotlib-base>=3.9` to the base `environment.yml`
for the M6 plot vector-export path (PRD §7.9, PLAN §10 M6 PR-8; ADR-0044 records
the backend-choice rationale). The base `conda-lock.yml` was regenerated for all
four platforms with `conda-lock==4.0.1`; the resolved drift is **minimal** — only
`matplotlib-base` 3.11.0 plus its font/plotting transitive deps were added, and
every core compute/GUI pin held (numpy 2.1.3, numba 0.61.2, napari 0.5.6, pyside6
6.11.1, pyqtgraph 0.14.0, scikit-learn 1.9.0, xgboost 3.3.0). `matplotlib-base`
(not full `matplotlib`) is deliberate: it ships the headless Agg/PDF/SVG file
backends with no Qt/Tk toolkit, so no PyQt binding enters the PySide6 base stack.
The drifted stack was re-verified — matplotlib's pdf/svg/agg backends plus the
full GUI/compute stack import clean from the regenerated lock. The sidecar lock
was **not** touched.

As with the M5 bump, this approval is scoped to **this single re-lock** and does
not standing-authorize future base-lock bumps.

## More information

PRD §4.1, §4.3, §12.8; ADR-0006 (sidecar mechanism).
