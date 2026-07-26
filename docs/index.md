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

Use the site as a route to the level of detail you need:

- **Public roadmap** — [what the current release contains, what comes next, and what is
  explicitly out of scope](roadmap.md).
- **Reference** — the stable project-store format, analysis parameters, exports, and
  command-line behavior under the Reference navigation section.
- **Architecture decisions** — the [MADR log](adr/README.md) for the reasoning behind
  consequential design choices.

## Modules (PRD §4.2)

| Module | Responsibility |
| --- | --- |
| `tether.io` | Readers, the HDF5 project store, the filename parser, and exporters. |
| `tether.imaging` | Native movie-to-trace extraction and registration. |
| `tether.fret` | Photobleaching detection, correction factors, and corrected FRET. |
| `tether.idealize` | The tMAVEN sidecar driver and dwell/rate analysis. |
| `tether.ml` | The per-condition incrementally-retrained quality ranker. |
| `tether.analysis` | Histograms, transition-density plots, and population statistics. |
| `tether.gui` | The PySide6 shell and dockable analysis surfaces. |
| `tether.project` | The experiment/session model and the headless core. |

## License

Tether is free software under the [GNU GPL v3.0-or-later](https://www.gnu.org/licenses/gpl-3.0.html).
