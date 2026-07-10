# 0044 — matplotlib-base as the static vector plot-export backend

- **Status:** accepted
- **Date:** 2026-07-09
- **Deciders:** bioedca
- **PRD anchor:** §7.9 (FR-EXPORT) — "every plot as vector PDF/SVG + PNG, stamped with provenance and parameters"
- **Milestone:** M6 (PR-8)

## Context and problem statement

The M6 analysis suite (`tether.analysis.*`, PRD Appendix C) is **compute-only** —
each plot type returns a frozen data dataclass (histograms, TDP grids, dwell
survival fits, the raw FRET cloud, …), and the interactive GUI docks render a
subset via **pyqtgraph**. PRD §7.9 requires that *every* plot also export as a
**vector PDF/SVG + PNG** artifact stamped with provenance and parameters. The
base environment had no PDF-capable rendering backend (only `pillow` for PNG,
`cairo` as a C library with no Python binding, and pyqtgraph — which has no PDF
exporter). Which backend renders the analysis plots to publication-quality
vector files, without contaminating the PySide6 base stack?

## Decision drivers

- **PRD §7.9** demands a *vector* format — PDF **and** SVG — plus PNG; pyqtgraph
  cannot emit PDF.
- **The §4.1 no-PyQt5 base-env invariant** (ADR-0004): the export backend must
  not pull a second Qt binding into the PySide6 stack.
- **Headless, GUI-decoupled export**: the FR-EXPORT path runs against a project
  store without a live Qt scene, and the CI `test` matrix builds the env from the
  base lock — the backend must render offscreen with no display.
- **Reuse over reinvention**: the ~13 analysis dataclasses each need axes,
  overlays, contours, error bars, and annotations — a real plotting library, not
  hand-painted primitives.

## Considered options

- **A. `matplotlib-base`** (the Agg/PDF/SVG file backends, *no* Qt/Tk GUI
  toolkit). Renders every analysis dataclass to vector PDF + SVG + raster PNG
  headlessly via the built-in `backend_pdf`/`backend_svg`/`backend_agg`.
- **B. Qt-native export** — build offscreen pyqtgraph scenes for all ~13
  compute-only plot types, then paint each `QGraphicsScene` onto `QPdfWriter`
  (PDF) / `QtSvg.QSvgGenerator` (SVG) / `QImage` (PNG). No new dependency, but
  requires authoring and GUI-testing a full pyqtgraph renderer per plot, couples
  export to the Qt event loop, and inherits pyqtgraph's imperfect SVG fidelity.
- **C. Full `matplotlib`** — same capability as A but the conda-forge `matplotlib`
  meta-package pulls `pyqt` (PyQt5) as its default GUI backend, violating the
  §4.1 no-PyQt5 invariant.

## Decision outcome

Chosen option: **A — `matplotlib-base`**. It delivers exactly the required
formats (vector PDF + SVG + PNG) from a headless, well-tested library; the
`-base` package deliberately excludes every GUI toolkit, so it adds **no** Qt/Tk
binding and keeps the §4.1 no-PyQt5 base-stack invariant intact (the isolated
sidecar env, §4.3, already uses `matplotlib-base` for the same reason). Option B
was rejected as materially more code, GUI-coupled, and lower-fidelity for vector
output; Option C was rejected for pulling PyQt5.

Adding `matplotlib-base>=3.9` to the base `environment.yml` is a **deliberate,
maintainer-approved base-lock bump** (approved by bioedca, 2026-07-09) under
ADR-0004's "bumped only deliberately" clause — recorded as that ADR's second
Amendment — and is landed as an **isolated build-only PR** (no feature code) so its
lock-drift diff and 3-OS install are proven green on their own before the renderer
depends on them (PLAN §0.5; mirrors the M5 split). The renderer itself — the
per-plot `dataclass → figure → PDF/SVG/PNG + provenance stamp` code and its tests
— lands in the follow-up PR-8b, reusing the existing
`tether.project.export.write_provenance_sidecar` stamp machinery.

### Consequences

- Good: PRD §7.9 vector PDF/SVG + PNG satisfied with a headless, mature backend;
  export stays decoupled from the interactive Qt docks; no PyQt5 in the base env.
- Trade-off: a base-lock re-lock (deliberate, per ADR-0004) — the resolved drift
  is minimal (only `matplotlib-base` 3.11.0 + its font/plotting transitive deps
  added; every core compute/GUI pin — numpy 2.1.3, numba 0.61.2, napari 0.5.6,
  pyside6 6.11.1, pyqtgraph 0.14.0 — held).
- Follow-up: `conda-lock-verify` (base) stays green (content-hash match); the 3-OS
  `test` matrix proves the drifted lock installs+imports; PR-8b lands the renderer
  + export tests (each plot produces a valid vector + raster carrying the stamp).

## More information

PRD §7.9 (FR-EXPORT), §4.1, §4.3; ADR-0004 (pin-and-hold dual-lock policy; this is
its second recorded deliberate base-lock bump — see that ADR's Amendments);
`tether.project.export` (existing provenance-sidecar stamp machinery reused by the
PR-8b renderer).
