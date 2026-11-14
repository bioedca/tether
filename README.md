<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/lockup-dark.svg">
  <img src="docs/assets/brand/lockup-light.svg" alt="Tether" width="420" height="120">
</picture>

# Tether

**A provenance-first desktop suite for single-molecule FRET (smFRET) analysis.**

[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPLv3-121417.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-bioedca.github.io%2Ftether-121417.svg)](https://bioedca.github.io/tether/)

Tether is a cross-platform suite for the Mondragón Lab (Northwestern
University) that takes you from a raw TIRF movie to curated, corrected,
idealized smFRET traces — without ever losing the link between a trace and the
pixels it came from. Today the desktop application opens a project, curates its
traces, idealizes one trace at a time, hands traces to tMAVEN and back, imports
Deep-LASI files and draws a population histogram. Extraction runs from the command
line, where `tether batch` also applies the corrections and idealizes every molecule.
From Python you can run the corrections and whole-project idealization with your own
settings, and make the other plots and the exports. [What it does](#what-it-does) says
where each feature runs today,
and the [documentation site](https://bioedca.github.io/tether/) is the user manual.

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

Tether's features reach the desktop application in stages. Each feature below sits in the
group where it runs today, and each planned feature links to the issue or milestone that
brings it to the application.

### In the application today

- **Open and browse.** **File → Open project…** opens a `.tether` project. A **pyqtgraph**
  trace dock shows each molecule's donor, acceptor and total intensity over its apparent FRET
  efficiency, with marginal histograms. The movie list holds one fixed entry.
- **Curation.** Keyboard-driven accept, reject and un-reject. Each decision is written to the
  project with who made it and when.
- **Neighbor view.** The selected molecule's cached image patch and the distance to its
  nearest neighbor, with a warning when the apertures overlap.
- **One-trace idealization.** The **I** key fits the selected molecule in the **isolated
  tMAVEN sidecar** and draws the fitted steps on its trace. Use it to check a trace by eye.
  The status bar calls it vbFRET, but it runs the consensus VB-HMM
  ([#554](https://github.com/bioedca/tether/issues/554)).
- **Standalone-tMAVEN hand-off.** A non-destructive round trip: save molecules as a file that
  tMAVEN's own window opens, then import its result back through a per-trace reconcile prompt.
- **Condition validation.** Confirm or correct the condition Tether read from each file name.
- **Deep-LASI import.** A wizard turns an existing Deep-LASI acquisition into `.tether`
  projects.
- **Population histogram.** The apparent-FRET histogram of the project, with rejected
  molecules left out.

### From the command line today

- **Native extraction.** `tether extract` and `tether batch`: faithful TIFF movie → trace
  extraction (channel split, à trous spot detection, polynomial registration, donor-anchored
  colocalization, aperture integration), validated against Deep-LASI. The application gets it
  with the [trace extraction section (M15)](https://github.com/bioedca/tether/milestone/17).
- **Batch pipeline.** `tether batch` runs extraction, the corrections and a whole-project
  consensus VB-HMM fit over many movies, one project per movie. A second run resumes at the
  first stage that did not finish. When the tMAVEN sidecar is not available, the fit is left
  for a later run ([command-line reference](docs/cli.md#tether-batch)).

### From Python today; planned for the application

Each of these runs from a short script in the Python that comes with Tether. Where an entry
links a how-to guide, that guide shows the script; the quality ranker has no how-to guide yet.
`tether batch` also runs the corrections and the whole-project fit with their default settings.

- **Corrections.** Native per-channel Bayesian photobleach detection → leakage α → γ →
  corrected FRET. A molecule with a missing α or γ falls back to apparent E, so a missing
  factor never gives a NaN. A frame whose E denominator is zero still gives NaN, because the
  ratio is not defined there: for apparent E, that is a frame with zero total intensity
  ([how-to](docs/how-to/apply-corrections.md)). The application gets it with the
  [trace curation section (M16)](https://github.com/bioedca/tether/milestone/18).
- **Whole-project idealization.** The consensus VB-HMM or ebFRET over every molecule, in the
  isolated tMAVEN sidecar ([how-to](docs/how-to/idealize.md)). An **Idealize project…** action
  is planned ([#625](https://github.com/bioedca/tether/issues/625), M16).
- **Quality ranker.** A per-condition, incrementally-retrained ranker that learns from your
  accepts and rejects and orders traces for review (sort/rank only, never auto-drop). The
  application gets it with [#639](https://github.com/bioedca/tether/issues/639) and
  [#640](https://github.com/bioedca/tether/issues/640) (M16).
- **Analysis plots.** Histograms with confidence intervals, transition-density and dwell-time
  plots, and the raw FRET cloud ([how-to](docs/how-to/analyze.md)). The application gets them
  with [analysis and plot composition (M17)](https://github.com/bioedca/tether/milestone/19).
- **Exports.** Provenance-stamped tables and figures
  ([how-to](docs/how-to/export-results.md)). The application gets them with
  [#621](https://github.com/bioedca/tether/issues/621) and
  [M17](https://github.com/bioedca/tether/milestone/19).

### Not in Tether yet

- **Movie panel.** A **napari** movie panel in the application window, with a movie switcher
  filled from the project ([#356](https://github.com/bioedca/tether/issues/356), M16).
- **Trace ⇄ movie round trip.** Click a trace → jump to its spot, click a spot → open its
  trace, across many movies ([#357](https://github.com/bioedca/tether/issues/357), M16). The
  project already stores the spot each trace came from.
- **Category keys.** The **1**–**9** keys put a trace in a category. Today they report
  "unavailable" ([#359](https://github.com/bioedca/tether/issues/359), M16).

## Installation

### Desktop installer (recommended)

Tether ships as one self-contained, offline installer per platform — a Windows
`.exe`, a macOS `.pkg` and a Linux `.sh` — that bundles the application and its
isolated tMAVEN sidecar. Follow
**[Install Tether](docs/install.md)** to download, verify, install, run and uninstall it.

The installers are **not OS-code-signed**, so expect a Windows SmartScreen or macOS
Gatekeeper warning; the published SHA-256 manifests and build-provenance attestation
are the integrity check instead
([ADR-0059](docs/adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)).
Signing costs money every year; the install guide explains
[why the installers are unsigned](docs/install.md#why-the-installers-are-unsigned), and
[sponsoring Tether](https://github.com/sponsors/bioedca) helps pay for it.

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
also adds a **Tether** entry to the Start Menu or applications menu. From the next release
the launchers are in `<install-root>/launchers/` instead, a folder that holds only them
([#492](https://github.com/bioedca/tether/issues/492)); read `launchers` for `bin` below.

Open the desktop application from that menu entry, or run the window launcher:

```bash
<install-root>/bin/tether-gui        # Windows: <install-root>\bin\tether-gui.bat
```

To use the command line, add `<install-root>/bin` to the **end** of your `PATH` (in rc3,
on Linux and macOS, it also holds the installer's own `python`, `pip` and `conda`, which
should not shadow yours), check the install, and extract a dual-channel TIFF movie into a
new project. [Install Tether](docs/install.md) shows how
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
- **First run:** [Your first analysis](https://bioedca.github.io/tether/latest/tutorial/) —
  a guided tutorial on a sample movie, from extraction to an exported table.
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

If you use Tether, please cite it with its Zenodo concept DOI, which always
resolves to the newest archived version:
[10.5281/zenodo.23306397](https://doi.org/10.5281/zenodo.23306397). The same
metadata is in [`CITATION.cff`](CITATION.cff) (GitHub's "Cite this repository"
button).

The [Citing Tether](docs/citing.md) page lists
which upstream papers to cite as well for each part of the pipeline you ran
(idealization backend, corrections, deep classifier), and how to report the
exact version that produced your data. Licensing credit for tMAVEN, Deep-LASI
and MASH-FRET is in [`NOTICE`](NOTICE).

## AI use

AI tools draft Tether's documentation and source code. People direct and approve that
work. See [How AI tools are used in Tether](docs/ai-use.md).

## License

Tether is free software licensed under the **GNU General Public License v3.0 or
later** (`GPL-3.0-or-later`). See [`LICENSE`](LICENSE). GPL-3.0 is required to
embed the tMAVEN sidecar; Tether itself is never linked into proprietary code.
