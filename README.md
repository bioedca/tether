<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/lockup-dark.svg">
  <img src="docs/assets/brand/lockup-light.svg" alt="Tether" width="420" height="120">
</picture>

# Tether

**A provenance-first desktop suite for single-molecule FRET (smFRET) analysis.**

[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPLv3-121417.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-bioedca.github.io%2Ftether-121417.svg)](https://bioedca.github.io/tether/)

Tether takes you from a raw TIRF movie to curated, corrected and idealized smFRET traces.
It keeps the link between each trace and the pixels that it came from. Tether is a
cross-platform suite for the Mondragón Lab (Northwestern University).

Today the desktop application opens a project and curates its traces. It idealizes one trace
at a time, and it hands traces to tMAVEN and back. It also imports Deep-LASI files and draws a
population plots. Extraction runs from the command line. There, `tether batch` also applies
the corrections and idealizes every molecule.

From Python, you can run the corrections and the whole-project idealization with your own
settings. You can also make the exports. [What it does](#what-it-does) says
where each feature runs today. The [documentation site](https://bioedca.github.io/tether/) is
the user manual.

**Does Tether fit my data?** Check
[supported inputs, acquisition geometry, scale and non-goals](https://bioedca.github.io/tether/latest/compatibility/)
before you install.

## Why Tether

Most smFRET pipelines discard provenance. After a pipeline extracts the traces, you can no
longer ask *"which spot in which movie produced this trace, and exactly how was it corrected?"*
Tether does the opposite: **every datum carries its origin**. Tether stamps the coordinates, the
correction factors, the parameters and the app version into one self-describing project file.
The trace ⇄ movie **round trip** is a property of the data model. It is not an extra feature
that can break.

## What it does

Tether's features reach the desktop application in stages. Each feature below sits in the
group where it runs today. Each planned feature links to the issue or milestone that brings it
to the application.

### In the application today

- **Open and browse.** **File → Open project…** opens a `.tether` project. A **pyqtgraph**
  trace dock shows each molecule's donor, acceptor and total intensity over its apparent FRET
  efficiency, with marginal histograms.
- **Curation.** Accept, reject and un-reject traces from the keyboard. Tether writes each
  decision to the project, with who made it and when.
- **Category keys.** The **1**–**9** keys put a trace in a category of its condition, and
  **0** clears it. Other keys move or reset the analysis window or end it at the photobleach
  ([how-to](docs/how-to/curate-traces.md#4-set-a-category-and-the-analysis-window)).
- **Neighbor view.** The view shows the cached image patch of the selected molecule and the
  distance to its nearest neighbor. It warns you when the apertures overlap.
- **Whole-project idealization.** **Idealize → Idealize project…**, or the **I** key, fits the
  accepted molecules with the consensus VB-HMM or ebFRET in the **isolated tMAVEN sidecar**. It
  runs in the background with progress and **Cancel**, and it can refit only a stale model
  ([how-to](docs/how-to/idealize.md)).
- **Standalone-tMAVEN hand-off.** A non-destructive round trip. Save molecules as a file that the
  tMAVEN application window opens. Then import its result back through a reconcile prompt for
  each trace.
- **Condition validation.** Confirm or correct the condition Tether read from each file name.
- **Deep-LASI import.** A wizard turns an existing Deep-LASI acquisition into `.tether`
  projects.
- **Analysis plots.** The **Analyze** section draws each population plot of Tether. They
  include the FRET histograms, the heatmaps and the FRET clouds. They also include the TDP,
  dwell-time, transition-probability and state-number plots, the cross-correlation and the
  anticorrelation events. One plot holds one or more named sets of molecules, such as one
  condition. Rejected molecules are left out ([how-to](docs/how-to/analyze.md)). The figure
  shown is the one an export writes. Saving it from the application comes with
  [#377](https://github.com/bioedca/tether/issues/377).
- **Movie panel.** A **napari** movie panel in the application window shows the project's
  movies with their donor and acceptor spots. A movie switcher lists the project's movies. A
  missing or changed movie file is listed but not shown. A movie with no recorded geometry has
  no spots.
- **Native extraction.** The **Prepare** section, `tether extract` and `tether batch` extract
  traces from a TIFF movie. The steps are channel split, à trous spot detection, polynomial
  registration, donor-anchored colocalization and aperture integration. The project validates
  this extraction against Deep-LASI. The application previews each stage and shows the detected
  spots and the control-point residuals on the movie before it extracts
  ([how-to](docs/how-to/extract-traces.md#extract-in-the-application)).
- **Trace ⇄ movie round trip.** Select a trace, or press Enter, and the movie panel shows its
  own movie centered on its spot. Click a donor or acceptor spot and the list selects its trace,
  across all the movies of the project
  ([how-to](docs/how-to/curate-traces.md#go-between-a-trace-and-its-movie-spot)). Drag the
  trace's frame cursor and the movie shows that frame. Move the movie's time slider and the
  cursor follows
  ([how-to](docs/how-to/curate-traces.md#move-through-the-trace-and-the-movie-together)).

### From the command line today

- **Batch pipeline.** `tether batch` runs extraction, the corrections and a whole-project
  consensus VB-HMM fit over many movies, one project per movie. A second run resumes at the
  first stage that did not finish. When the tMAVEN sidecar is not available, the fit is left
  for a later run ([command-line reference](docs/cli.md#tether-batch)).

### From Python today; planned for the application

Each of these runs from a short script in the Python that comes with Tether. Where an entry
links a how-to guide, that guide shows the script. The quality ranker has no how-to guide yet.
`tether batch` also runs the corrections and the whole-project fit with their default settings.

- **Corrections.** Native per-channel Bayesian photobleach detection → leakage α → γ →
  corrected FRET. A molecule with a missing α or γ falls back to apparent E, so a missing
  factor never gives a NaN. A frame whose E denominator is zero still gives NaN, because the
  ratio is not defined there. For apparent E, that is a frame with zero total intensity
  ([how-to](docs/how-to/apply-corrections.md)). The application gets it with the
  [trace curation section (M16)](https://github.com/bioedca/tether/milestone/18).
- **Quality ranker.** A ranker for each condition learns from your accepts and rejects, and it
  orders traces for review. It retrains incrementally. It only sorts and ranks traces, and it
  never drops a trace by itself. The application gets it with [#639](https://github.com/bioedca/tether/issues/639) and
  [#640](https://github.com/bioedca/tether/issues/640) (M16).
- **Exports.** Provenance-stamped tables and figures
  ([how-to](docs/how-to/export-results.md)). The application gets them with
  [#621](https://github.com/bioedca/tether/issues/621) and
  [M17](https://github.com/bioedca/tether/milestone/19).

### Not in Tether yet

Nothing is listed here now. Planned work is in the
[issue tracker](https://github.com/bioedca/tether/issues).

## Installation

### Desktop installer (recommended)

Tether ships as one self-contained offline installer for each platform: a Windows `.exe`, a
macOS `.pkg` and a Linux `.sh`. Each installer bundles the application and its isolated tMAVEN
sidecar. Follow **[Install Tether](docs/install.md)** to download, verify, install, run and
uninstall it.

The installers are **not OS-code-signed**, so expect a Windows SmartScreen or macOS Gatekeeper
warning. The published SHA-256 manifests and the build-provenance attestation are the integrity
check instead
([ADR-0059](docs/adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)).
Signing costs money every year. The install guide explains
[why the installers are unsigned](docs/install.md#why-the-installers-are-unsigned).
[Sponsoring Tether](https://github.com/sponsors/bioedca) helps pay for signing.

### From source

For development, restore the pinned base environment from the committed `conda-lock.yml`. Do
not re-solve it. Then install Tether into it without dependencies:

```bash
git clone https://github.com/bioedca/tether.git
cd tether
micromamba create -n tether -f conda-lock.yml
micromamba activate tether
pip install -e . --no-deps
```

Idealization needs the separate tMAVEN sidecar environment.
`python scripts/setup_sidecar.py` builds it from `sidecar/conda-lock.yml`.
[`CONTRIBUTING.md`](CONTRIBUTING.md) has the full setup.

## Quickstart

The installer does **not** edit your `PATH`. It writes both launchers into
`<install-root>/bin/` (`<install-root>\bin\` on Windows). On Windows and Linux, it also adds a
**Tether** entry to the Start Menu or the applications menu.

From the next release, the launchers are in `<install-root>/launchers/` instead, a folder that
holds only them
([#492](https://github.com/bioedca/tether/issues/492)). Read `launchers` for `bin` below.
From the next release, the Windows Start Menu also has **Tether Command Prompt**. In that
console, `tether` works with no `PATH` change
([#666](https://github.com/bioedca/tether/issues/666)).

Open the desktop application from that menu entry, or run the application-window launcher:

```bash
<install-root>/bin/tether-gui        # Windows: <install-root>\bin\tether-gui.bat
```

To use the command line, add `<install-root>/bin` to the **end** of your `PATH`. In rc3 on
Linux and macOS, that folder also holds the installer's own `python`, `pip` and `conda`. They
must not come before your own on the `PATH`. Then check the install, and extract a dual-channel
TIFF movie into a new project. [Install Tether](docs/install.md) shows how to make the `PATH`
change permanent.

Check `--donor-side` before you extract. It must name the half of your frame that carries the
donor, and its default is `left`. Nothing warns you when it is wrong.

```bash
export PATH="$PATH:<install-root>/bin"   # PowerShell: $env:PATH = "$env:PATH;<install-root>\bin"
tether --version
tether extract movie.tif -o project.tether
```

The [command-line reference](https://bioedca.github.io/tether/latest/cli/) covers
`--donor-side`, every other flag and `tether batch`.

## Documentation

- **User manual:** <https://bioedca.github.io/tether/>. Use the version selector to match your
  installed release.
- **First run:** [Your first analysis](https://bioedca.github.io/tether/latest/tutorial/). This
  guided tutorial uses a sample movie and goes from extraction to an exported table.
- **Installers:** [Packaging & installers](https://bioedca.github.io/tether/latest/packaging/)
  and [launching Tether after an install](https://bioedca.github.io/tether/latest/cli/#launching-tether-after-an-installer-install).
- **Does Tether fit my data?**
  [Supported inputs, acquisition geometry, scale and non-goals](https://bioedca.github.io/tether/latest/compatibility/).
- **Architecture decisions:** [`docs/adr/`](docs/adr/) (MADR format).
- **Product spec:** [`docs/PRD.md`](docs/PRD.md), the source of truth, in numbered sections.

## Getting help

- **Something went wrong?** Check
  [Troubleshooting](https://bioedca.github.io/tether/latest/troubleshooting/) first.
- **A question?** Ask in
  [Discussions Q&A](https://github.com/bioedca/tether/discussions/new?category=q-a),
  or use the Question form under
  [New issue](https://github.com/bioedca/tether/issues/new/choose).
- **A bug or a docs gap?** Open the matching form under
  [New issue](https://github.com/bioedca/tether/issues/new/choose).
- **A security problem?** Never open a public issue. Follow [`SECURITY.md`](SECURITY.md) to
  report it privately.

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before you start: it gives the workflow, the checks,
the schema-freeze rule and the commit format. For a first change,
[correct a documentation page](CONTRIBUTING.md#correct-the-documentation-the-quick-path) or pick
an issue from the [issue tracker](https://github.com/bioedca/tether/issues).

## Citing

If you use Tether, please cite it with its Zenodo concept DOI:
[10.5281/zenodo.23306397](https://doi.org/10.5281/zenodo.23306397). That DOI always resolves
to the newest archived version. [`CITATION.cff`](CITATION.cff) holds the same metadata, and
GitHub's "Cite this repository" button reads it.

The [Citing Tether](docs/citing.md) page lists the upstream papers to cite for each part of
the pipeline that you ran. These parts are the idealization backend, the corrections and the
deep classifier. The page also tells you how to report the exact version that produced your
data. [`NOTICE`](NOTICE) gives the licensing credit for tMAVEN, Deep-LASI and MASH-FRET.

## License

Tether is free software under the **GNU General Public License v3.0 or later**
(`GPL-3.0-or-later`). See [`LICENSE`](LICENSE). Tether must use GPL-3.0 because it embeds the
tMAVEN sidecar. Tether itself is never linked into proprietary code.

## AI use

AI tools draft Tether's documentation and source code. People direct and approve that
work. See [How AI tools are used in Tether](docs/ai-use.md).
