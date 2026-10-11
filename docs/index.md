# Tether

**Tether** is a cross-platform single-molecule FRET (smFRET) desktop suite built for the
Mondragón Lab at Northwestern University. It pairs a PySide6 application shell with an
embedded [napari](https://napari.org) movie panel and [pyqtgraph](https://www.pyqtgraph.org)
trace docks, backed by a single self-describing HDF5 (`.tether`) project store that carries
full provenance for every datum.

**New here?** Start with [Does Tether fit my data?](compatibility.md) — it describes the
acquisition geometry and file formats Tether reads, what it deliberately does not do, and
what a dataset costs in time and disk. It is the fastest way to find out whether Tether
suits your experiment before you install anything.

## Where to go next

### Using Tether

- [Install Tether](install.md) — for downloading, verifying, installing and uninstalling
  the desktop installer.
- [Command line](cli.md) — for scripting or batch-processing movies, and for launching
  Tether after an installer install.
- [Legacy Deep-LASI import](io/legacy-import.md) — for re-analysing an existing
  Deep-LASI acquisition without re-extracting the movie.
- [Standalone-tMAVEN hand-off](idealize/standalone-tmaven-handoff.md) — for idealizing
  in tMAVEN's own window and bringing the result back.
- [Seven-plot parity gallery](analysis/parity-gallery.md) — for the seven analysis
  plots and how each corresponds to its tMAVEN counterpart.
- [Validation & benchmarks](validation.md) — for deciding whether a Tether analysis is
  defensible enough to publish.
- [Deep trace classifier](ml/deep-classifier.md) — for users with a supported NVIDIA GPU
  who want the optional deep-learning add-on.
- [Exports](reference/exports.md) — for reading the files, columns and units Tether
  writes.
- [Analysis parameters](reference/parameters.md) — for every default Tether applies and
  whether it is recorded in your project.
- [Project store](reference/tether-format.md) — for reading a `.tether` file directly.
- [Glossary](glossary.md) — for the terms used across these pages.
- [Troubleshooting](troubleshooting.md) — for when a result or an install looks wrong.

### Project & maintainers

- [Public roadmap](roadmap.md) — for what the current release contains, what comes
  next, and what is explicitly out of scope.
- [Packaging & installers](packaging.md) — for what an installer contains and how to
  verify a download.
- [Releasing](release.md) — for maintainers cutting a tagged release.
- [Architecture decisions](adr/README.md) — for the reasoning behind consequential
  design choices.
- [Stability policy](stability.md) — for what a version number promises.
- [Privacy policy](privacy.md) — for what Tether and this site do, and do not, collect.

## Modules (PRD §4.2)

| Module | Responsibility |
| --- | --- |
| [`tether.io`](io/legacy-import.md) | Readers, the HDF5 project store, the filename parser, and [exporters](reference/exports.md). |
| [`tether.imaging`](cli.md#tether-extract) | Native movie-to-trace extraction and registration. |
| [`tether.fret`](adr/0026-photobleach-detection-and-window-default.md) | Photobleaching detection, correction factors, and corrected FRET. |
| [`tether.idealize`](idealize/standalone-tmaven-handoff.md) | The tMAVEN sidecar driver and dwell/rate analysis. |
| [`tether.ml`](ml/deep-classifier.md) | The per-condition incrementally-retrained quality ranker. |
| [`tether.analysis`](analysis/parity-gallery.md) | Histograms, transition-density plots, and population statistics. |
| [`tether.gui`](adr/0055-gui-session-writer-ownership.md) | The PySide6 shell and dockable analysis surfaces. |
| [`tether.project`](adr/0030-headless-batch-runner-isolation-and-checkpoint.md) | The experiment/session model and the headless core. |

## License

Tether is free software under the [GNU GPL v3.0-or-later](https://www.gnu.org/licenses/gpl-3.0.html).
