# Tether

**A provenance-first desktop suite for single-molecule FRET (smFRET) analysis.**

[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Status: pre-alpha](https://img.shields.io/badge/status-pre--alpha%20(M0)-orange.svg)](docs/PRD.md)

> **Status — pre-alpha (milestone M0, foundation).** Tether is under active
> construction. This repository currently holds governance, the frozen product
> spec, and the scaffold; the analysis features described below are the target
> defined in [`docs/PRD.md`](docs/PRD.md), landed milestone by milestone (§9).

Tether is a cross-platform application for the Mondragón Lab (Northwestern
University) that takes you from a raw TIRF movie to curated, corrected,
idealized smFRET traces — without ever losing the link between a trace and the
pixels it came from.

## Why Tether

Most smFRET pipelines discard provenance: once traces are extracted, you can no
longer ask *"which spot in which movie produced this trace, and exactly how was
it corrected?"* Tether is built the other way around — **every datum carries its
origin**: coordinates, correction factors, parameters, and the app version are
stamped into a single self-describing project file, and the trace ⇄ movie
**round-trip** is a property of the data model, not a fragile convenience.

## What it does (target capability — see [PRD §3](docs/PRD.md))

- **Native extraction** — faithful TIFF movie → trace extraction (channel split,
  à trous spot detection, polynomial registration, donor-anchored
  colocalization, aperture integration), validated against Deep-LASI.
- **Round-trip browser** — a PySide6 shell with an embedded **napari** movie
  panel and a fast **pyqtgraph** trace dock; click a trace → jump to its spot,
  click a spot → open its trace, across many movies.
- **Curation** — keyboard-driven accept/reject/categorize with full label
  provenance, feeding a per-condition, incrementally-retrained quality ranker
  (sort/rank only, never auto-drop).
- **Corrections** — native per-channel Bayesian photobleach detection → leakage
  α → γ → corrected FRET, with an apparent-E fallback that **never emits a NaN**.
- **Idealization** — one-click vbFRET / consensus VB-HMM / ebFRET via an
  **isolated tMAVEN sidecar**, plus a non-destructive hand-off to standalone
  tMAVEN.
- **Analysis** — histograms with CI, transition-density and dwell-time plots, the
  raw FRET cloud, and provenance-stamped exports.

## Installation

> Packaging (conda-forge + `constructor` signed installers) lands at milestone
> M9. For now Tether is a source checkout against a pinned conda environment.

```bash
git clone https://github.com/bioedca/tether.git
cd tether
# environment + editable install instructions arrive with the M0 package
# skeleton (PLAN M0 S2); see CONTRIBUTING.md.
```

The base application stack (Python, NumPy, Numba, napari/PySide6/pyqtgraph) is
pinned in a committed `conda-lock`; the tMAVEN idealization sidecar runs in its
own isolated environment. See [`docs/PRD.md` §4](docs/PRD.md) for the stack.

## Documentation

- **Product spec:** [`docs/PRD.md`](docs/PRD.md) — the §-numbered source of truth.
- **Architecture decisions:** [`docs/adr/`](docs/adr/) (MADR format).
- **Contributing:** [`CONTRIBUTING.md`](CONTRIBUTING.md) — workflow, gates,
  schema-freeze rule, Conventional Commits.
- **Security:** [`SECURITY.md`](SECURITY.md) — private vulnerability reporting.

## Citing

If you use Tether, please cite it via [`CITATION.cff`](CITATION.cff) (GitHub's
"Cite this repository" button). Tether interoperates with and credits
[tMAVEN](NOTICE), and mirrors algorithms from Deep-LASI and MASH-FRET — see
[`NOTICE`](NOTICE).

## License

Tether is free software licensed under the **GNU General Public License v3.0 or
later** (`GPL-3.0-or-later`). See [`LICENSE`](LICENSE). GPL-3.0 is required to
embed the tMAVEN sidecar; Tether itself is never linked into proprietary code.
