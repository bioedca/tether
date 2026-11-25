---
description: "Tether is a free desktop suite for single-molecule FRET analysis. Start here to find the install guide, the tutorial and the citation."
---

<div class="tether-hero">
  <img class="on-light" src="assets/brand/lockup-light.svg" alt="The Tether logo" width="420" height="120">
  <img class="on-dark" src="assets/brand/lockup-dark.svg" alt="The Tether logo" width="420" height="120">
</div>

# Tether

**Tether** takes your single-molecule FRET (smFRET) movies to curated, corrected and idealized
traces, and it records how it made each result. It is a free desktop suite for Windows, macOS
and Linux. It was built for the Mondragón Lab at Northwestern University.

Extraction, the corrections and idealization stamp their Tether version and most of their
parameters into one self-describing project file. Tether does not stamp every setting. It
stamps no direct-excitation factor, because it does not correct for direct excitation. The
publication exports carry the same stamp beside them.
[Reproducibility and reporting](reproducibility.md) lists what Tether records, what it does not
record and how to read it back.

**Where each feature runs today.** The desktop application opens a project, curates its
traces, fits one trace at a time and draws a population histogram. Extraction runs from the
[command line](cli.md). There, `tether batch` also applies the corrections and fits every
molecule. From Python, you can run the corrections and whole-project fits with your own
settings. You can also make the other plots and the exports, as each
[how-to guide](#using-tether) shows.

The application also shows the movies of a project in a movie panel, with their donor and
acceptor spots. A missing or changed movie file is listed but not shown. A movie with no
recorded geometry has no spots.

**New here?** Start with [Does Tether fit my data?](compatibility.md). It describes the
acquisition geometry and the file formats that Tether reads. It also says what Tether does not
do by design, and what a dataset costs in time and disk. It is the fastest way to find out
whether Tether suits your experiment before you install anything.

**Just installed it?** Do [Your first analysis](tutorial.md) next. It takes a small sample
movie that comes with these docs through every stage, from extraction to an exported table, in
about fifteen minutes.

**Want to know how it works?** [How Tether works](pipeline.md) follows a movie through every
stage, from extraction to export. It also explains the decisions that change what you can claim
in a paper.

## Where to go next

### Using Tether

- [How Tether works](pipeline.md): for the order Tether processes a movie in, and why.
- [Install Tether](install.md): for downloading, verifying, installing and uninstalling
  the desktop installer.
- [Your first analysis](tutorial.md): for a guided first run on a sample movie, once Tether
  is installed.
- [Installers & offline bundle](packaging.md): for what an installer contains and how to
  verify a download.
- [Command line](cli.md): for scripting or batch-processing movies, and for launching
  Tether after an installer install.
- How-to guides, one for each task once you have your own data:
    - [Extract traces](how-to/extract-traces.md).
    - [Curate them](how-to/curate-traces.md).
    - [Correct for leakage and gamma](how-to/apply-corrections.md).
    - [Idealize them](how-to/idealize.md).
    - [Draw a histogram, TDP and dwell plots](how-to/analyze.md).
    - [Export the results](how-to/export-results.md).
- [Legacy Deep-LASI import](io/legacy-import.md): for re-analyzing an existing
  Deep-LASI acquisition without re-extracting the movie.
- [Standalone-tMAVEN hand-off](idealize/standalone-tmaven-handoff.md): for idealizing
  in tMAVEN's own application window and bringing the result back.
- [Seven-plot parity gallery](analysis/parity-gallery.md): for the seven analysis
  plots and how each corresponds to its tMAVEN counterpart.
- [Validation & benchmarks](validation.md): for deciding whether a Tether analysis is
  defensible enough to publish.
- [Deep trace classifier](ml/deep-classifier.md): for users with a supported NVIDIA GPU
  who want the optional deep-learning add-on.
- [Exports](reference/exports.md): for reading the files, columns and units Tether
  writes.
- [Analysis parameters](reference/parameters.md): for every default Tether applies and
  whether it is recorded in your project.
- [Project file](reference/tether-format.md): for reading a `.tether` file directly.
- [Reproducibility and reporting](reproducibility.md): for the version and settings a
  project records, reading them back, and writing them into a Methods section.
- [Glossary](glossary.md): for the terms used across these pages.
- [Troubleshooting](troubleshooting.md): for when a result or an install looks wrong.

### Project & maintainers

- [Public roadmap](roadmap.md): for what the current release contains, what comes
  next, and what is explicitly out of scope.
- [Stability policy](stability.md): for what a version number promises.
- [Version history](release.md#change-record): for what changed between two versions,
  and where pre-1.0 history lives.
- [Privacy policy](privacy.md): for what Tether and this site do, and do not, collect.
- [Architecture decisions](adr/README.md): for the reasoning behind consequential
  design choices.
- [Releasing](release.md): for maintainers cutting a tagged release.
- [Building the installers](maintainers/building-installers.md): for maintainers building
  and smoke-testing the installers from source.
- [Standalone-tMAVEN cross-OS record](maintainers/standalone-tmaven-cross-os-record.md):
  for maintainers recording the manual tMAVEN GUI check on each operating system.

## Modules

This section is for contributors. Tether pairs a PySide6 application shell and
[pyqtgraph](https://www.pyqtgraph.org) trace docks with one self-describing HDF5 (`.tether`)
project file. The movie panel uses [napari](https://napari.org).

Each package below owns one responsibility. The table lists them by package. For the order in
which a movie passes through them, and what each leaves in the project file, see
[How Tether works](pipeline.md).

| Module | Responsibility |
| --- | --- |
| [`tether.io`](io/legacy-import.md) | Readers, the HDF5 project file, the filename parser, and [exporters](reference/exports.md). |
| [`tether.imaging`](cli.md#tether-extract) | Native movie-to-trace extraction and registration. |
| [`tether.fret`](adr/0026-photobleach-detection-and-window-default.md) | Photobleaching detection, correction factors, and corrected FRET. |
| [`tether.idealize`](idealize/standalone-tmaven-handoff.md) | The tMAVEN sidecar driver and dwell/rate analysis. |
| [`tether.ml`](ml/deep-classifier.md) | The per-condition incrementally-retrained quality ranker. |
| [`tether.analysis`](analysis/parity-gallery.md) | Histograms, transition-density plots, and population statistics. |
| [`tether.gui`](adr/0055-gui-session-writer-ownership.md) | The PySide6 shell and dockable analysis surfaces. |
| [`tether.project`](adr/0030-headless-batch-runner-isolation-and-checkpoint.md) | The experiment/session model and the headless core. |

## Cite Tether

If you publish results that you made with Tether, cite its concept DOI,
[10.5281/zenodo.23306397](https://doi.org/10.5281/zenodo.23306397). That DOI always resolves to
the newest archived release. [Citing Tether](citing.md) gives the full citation, tells you how to
cite the exact version you ran, and lists the published methods to cite with it.

## Support Tether

Tether is free. Its installers are not yet code-signed, because signing costs money every
year. [Why the installers are unsigned](install.md#why-the-installers-are-unsigned) explains
what signing costs and what it would change. If Tether is useful to you, please consider
[sponsoring it](https://github.com/sponsors/bioedca). Sponsorship pays for development, and code
signing comes first.

## License

Tether is free software under the [GNU GPL v3.0-or-later](https://www.gnu.org/licenses/gpl-3.0.html).
