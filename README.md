# Tether

**A provenance-first desktop suite for single-molecule FRET (smFRET) analysis.**

[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-bioedca.github.io%2Ftether-indigo.svg)](https://bioedca.github.io/tether/)

Tether is a cross-platform application for the Mondragón Lab (Northwestern
University) that takes you from a raw TIRF movie to curated, corrected,
idealized smFRET traces — without ever losing the link between a trace and the
pixels it came from. Extraction, curation, corrections, idealization and analysis
are all in the application today; the
[documentation site](https://bioedca.github.io/tether/) is the user manual.

**Does Tether fit my data?** Check
[supported inputs, acquisition geometry, scale and non-goals](https://bioedca.github.io/tether/latest/compatibility/)
before you install.

## Why Tether

Most smFRET pipelines discard provenance: once traces are extracted, you can no
longer ask *"which spot in which movie produced this trace, and exactly how was
it corrected?"* Tether is built the other way around — **every datum carries its
origin**: coordinates, correction factors, parameters, and the app version are
stamped into a single self-describing project file, and the trace ⇄ movie
**round-trip** is a property of the data model, not a fragile convenience.

## What it does

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

### Desktop installer (recommended)

Tether ships as one self-contained, offline installer per platform — a Windows
`.exe`, a macOS `.pkg` and a Linux `.sh` — that bundles the application and its
isolated tMAVEN sidecar. Follow
**[Install Tether](https://bioedca.github.io/tether/latest/install/)**
([source](docs/install.md)) to download, verify, install, run and uninstall it.

The installers are **not OS-code-signed**, so expect a Windows SmartScreen or macOS
Gatekeeper warning; the published SHA-256 manifests and build-provenance attestation
are the integrity check instead
([ADR-0059](docs/adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)).

### From source

For development, restore the pinned base environment from the committed
`conda-lock.yml` (never re-solve it) and install Tether into it without
dependencies:

```bash
git clone https://github.com/bioedca/tether.git
cd tether
micromamba create -n tether -f conda-lock.yml
micromamba activate tether
pip install -e . --no-deps
```

Idealization needs the separate tMAVEN sidecar environment, which
`python scripts/setup_sidecar.py` builds from `sidecar/conda-lock.yml`.
[`CONTRIBUTING.md`](CONTRIBUTING.md) has the full setup.

## Quickstart

<!-- A hero screenshot of the round-trip browser goes here. -->

The installer does **not** edit your `PATH`. It writes both launchers into
`<install-root>/bin/` (`<install-root>\bin\` on Windows), and on Windows and Linux it
also adds a **Tether** entry to the Start Menu or applications menu.

Open the desktop application from that menu entry, or run the window launcher:

```bash
<install-root>/bin/tether-gui        # Windows: <install-root>\bin\tether-gui.bat
```

To use the command line, add `<install-root>/bin` to the **end** of your `PATH` (on
Linux and macOS it also holds the installer's own `python`, `pip` and `conda`, which
should not shadow yours), check the install, and extract a dual-channel TIFF movie into a
new project. [Install Tether](https://bioedca.github.io/tether/latest/install/) shows how
to make the `PATH` change permanent.

```bash
export PATH="$PATH:<install-root>/bin"   # PowerShell: $env:PATH = "$env:PATH;<install-root>\bin"
tether --version
tether extract movie.tif -o project.tether
```

`--donor-side` (default `left`) must match which half of your frame carries the donor,
and nothing warns you when it is wrong. The
[command-line reference](https://bioedca.github.io/tether/latest/cli/) covers that,
every other flag, and `tether batch`.

## Documentation

- **User manual:** https://bioedca.github.io/tether/ — use the version selector to
  match your installed release.
- **Installers:** [Packaging & installers](https://bioedca.github.io/tether/latest/packaging/)
  and [launching Tether after an install](https://bioedca.github.io/tether/latest/cli/#launching-tether-after-an-installer-install).
- **Does Tether fit my data?** —
  [supported inputs, acquisition geometry, scale and non-goals](https://bioedca.github.io/tether/latest/compatibility/).
- **Architecture decisions:** [`docs/adr/`](docs/adr/) (MADR format).
- **Contributing:** [`CONTRIBUTING.md`](CONTRIBUTING.md) — workflow, gates,
  schema-freeze rule, Conventional Commits.
- **Product spec:** [`docs/PRD.md`](docs/PRD.md) — the §-numbered source of truth.

## Getting help

- **Something went wrong?** Check
  [Troubleshooting](https://bioedca.github.io/tether/latest/troubleshooting/) first.
- **A question?** Ask in
  [Discussions Q&A](https://github.com/bioedca/tether/discussions/new?category=q-a),
  or use the Question form under
  [New issue](https://github.com/bioedca/tether/issues/new/choose).
- **A bug or a docs gap?** Open the matching form under
  [New issue](https://github.com/bioedca/tether/issues/new/choose).
- **A security problem?** Never open a public issue; follow
  [`SECURITY.md`](SECURITY.md) to report it privately.

## Citing

If you use Tether, please cite it via [`CITATION.cff`](CITATION.cff) (GitHub's
"Cite this repository" button). Tether interoperates with and credits
[tMAVEN](NOTICE), and mirrors algorithms from Deep-LASI and MASH-FRET — see
[`NOTICE`](NOTICE).

## License

Tether is free software licensed under the **GNU General Public License v3.0 or
later** (`GPL-3.0-or-later`). See [`LICENSE`](LICENSE). GPL-3.0 is required to
embed the tMAVEN sidecar; Tether itself is never linked into proprietary code.
