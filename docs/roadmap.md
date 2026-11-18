---
description: "What the current Tether release contains, what stable v1.0.0 adds, what comes next and what is out of scope, as a short public summary of the GitHub milestones."
---

# Roadmap

**Who this page is for.** Readers who want a short, public view of what Tether is
shipping, what comes next, and where its scope stops. This page summarizes the
[GitHub milestones](https://github.com/bioedca/tether/milestones) and their issues, which
are the roadmap of record. It does not duplicate the frozen schema or parameter tables.
Use the [project file reference](reference/tether-format.md) and
[analysis-parameter reference](reference/parameters.md) for those contracts. Reading
this page needs no repository access.

## Current release

`v1.0.0-rc3` is the current public version. It was published on 2026-11-09 as a GitHub
pre-release. It is a release candidate for the first stable Tether release, and stable
`v1.0.0` has not been tagged yet. The release candidate collects the closed M0 to M8
capability milestones and the M9 packaging and documentation work. M9 closed on
2026-11-14. The [Releases page](https://github.com/bioedca/tether/releases) lists each
published version and its assets.

The stable v1.0.0 scope is:

- a provenance-first `.tether` project file that holds the movies of one experiment and
  keeps every trace linked to its source movie, coordinates, corrections, annotations,
  idealization, and analysis settings;
- movie import for TIFF movies, with channel layouts (side by side, top and bottom, free
  rectangles, or two files), the geometry of each channel, missing metadata, and a
  registry of each source with its checksum;
- native two-color, single-laser TIRF movie extraction with a channel map made from a
  bead-slide video or one image, plus round-trip Deep-LASI and tMAVEN interchange;
- photophysical correction with review pages, keyboard-driven curation, condition-aware
  ranking, batch processing, and provenance-stamped exports;
- one-click tMAVEN-sidecar idealization and seven native population-analysis views,
  backed by the project's validation oracles;
- the **unified GUI**: one application window with sections to prepare movies, curate and
  correct traces, and analyze them, with a guided mode and direct navigation, so that lab
  members do not need a terminal. Each action also has a command-line route;
- the **in-app updater**: Tether finds a newer release, verifies the download against its
  build-provenance attestation, and gives the install to the installer of the operating
  system; and
- cross-platform packaging with frozen environments, checksums, an SBOM, and versioned
  documentation. See [Installers & offline bundle](packaging.md) for current platform and
  signing details.

## Next planned work

The open milestones before stable `v1.0.0` are these. Each one links to its GitHub
milestone, which holds its acceptance criteria and its issues.

- [M12 - Project data model & write safety](https://github.com/bioedca/tether/milestone/14)
  gives one notion of file identity, a destination lock, and the multi-movie append. The
  GUI sections stand on it.
- [M13 - In-app updater](https://github.com/bioedca/tether/milestone/15) builds the
  updater.
- [M14 - Unified GUI shell](https://github.com/bioedca/tether/milestone/16) builds the one
  window with its sections. It lands before any section content.
- [M15 - Trace extraction section](https://github.com/bioedca/tether/milestone/17),
  [M16 - Trace curation section](https://github.com/bioedca/tether/milestone/18) and
  [M17 - Analysis & plot composition](https://github.com/bioedca/tether/milestone/19) fill
  the sections of the shell. With M15, a lab member takes a fresh acquisition from the raw
  movie to a `.tether` project with no terminal. Until then, a new movie is extracted with
  `tether extract` or `tether batch` (see [Command line](cli.md)), and the application
  opens the result.
- [M20 - Movie import and channel geometry](https://github.com/bioedca/tether/milestone/22)
  has a part before the tag: channel layouts, two-file acquisitions, the geometry recipe,
  missing metadata, the source registry, the import page, and `tether import`.

[M18 - Validation & multi-OS release acceptance](https://github.com/bioedca/tether/milestone/20)
comes after all of these and **cuts the `v1.0.0` tag**. It binds one evidence artifact for
each validation oracle to the release commit. It also records installer passes on three
clean machines, one each for Windows, macOS, and Linux. The milestones above set no other
order among themselves.

Two open milestones do not block the tag.
[M19 - Regression and benchmark suite](https://github.com/bioedca/tether/milestone/21)
adds checks for loss of speed and for drift from the reference tools.
[M21 - Data deposit](https://github.com/bioedca/tether/milestone/23) adds a deposit of
movies, projects, and results to Zenodo. It comes after `v1.0.0`.

[M10 - Docs & community](https://github.com/bioedca/tether/milestone/12) is the post-1.0
bucket. It opens when M18 closes. It holds documentation and community work, distribution
channels that need the tag, such as conda-forge and PyPI, and work deferred from the
milestones before 1.0. The part of M20 after the tag adds readers for more movie formats:
ND2, CZI, SIF, SIRA, lossless AVI, and `.vsi` through an optional isolated stack.

A place on a milestone is a plan, not a promise or a date. A capability is done when its
acceptance criterion is met and its evidence is bound to a commit on `main`. A milestone
closes on that evidence, not on a date. Work that has no accepted issue on a milestone is
not planned.

## Explicit non-goals

These boundaries apply to v1.0 and are deliberate:

- **No ALEX/PIE, stoichiometry, or three-color analysis.** Tether v1 is for two-color,
  single-laser acquisitions.
- **No built-in data simulator.** Validation uses real labeled traces and named benchmark
  data instead.
- **No attempt to clone every tMAVEN plot.** Parity with tMAVEN covers seven plot types;
  other tMAVEN views remain available through the standalone hand-off. Tether also plots
  its own results that tMAVEN does not have, such as the raw FRET cloud.
- **No open-ended legacy-format support.** Deep-LASI inputs and tMAVEN SMD are supported;
  older `.dat` and vbFRET `.mat` formats are not.
- **No central Tether service.** The application runs on each user's workstation and
  projects remain on storage chosen by the lab.

For acquisition-level limits and supported inputs, read
[Does Tether fit my data?](compatibility.md).

## Milestone history

Each milestone is designed to leave a runnable, tested application, never a partial
layer. Annotated repository tags record the public checkpoints. The table lists every
milestone in number order. The number is a name, not a position in time, so M10 comes
after M11 to M18.

Some closed milestones met their criteria in the library and the command line, but not
yet in the desktop application. Those criteria are not retracted, and no closed milestone
is reopened. An open issue carries each one, and most of those issues are on M12 and M14
to M17.

| Milestone | State | Public checkpoint | What it covers |
| --- | --- | --- | --- |
| M0 — Foundation | Closed | `v0.0.0` | Project file and schema freeze, application shell, pinned environments, CI, and repository governance. |
| M0.5 — De-risking | Closed | Validation gate before `v0.1.0` | tMAVEN-sidecar interchange and parity, and Deep-LASI extraction and registration feasibility. |
| M1 — Extraction core | Closed | `v0.1.0` | Movie-to-trace extraction, calibration and registration, and the headless extraction path. |
| M2 — MVP | Closed | `v0.2.0` | Trace↔movie browsing components, curation provenance, idealization hand-off, locking, and first analysis views. |
| M3 — Corrections | Closed | `v0.3.0` | Photobleaching, leakage and gamma correction, stale-result handling, and resumable batch processing. |
| M4 — Annotation | Closed | `v0.4.0` | Structured cross-file conditions, editable categories, and audited condition correction. |
| M5 — Curation + ML v1 | Closed | `v0.5.0` | Persistent condition-aware ranking, active-learning cues, drift checks, and multi-curator label merging. |
| M6 — Analysis suite | Closed | `v0.6.0` | Population models, seven-plot parity, kinetics, raw-FRET views, and provenance-stamped exports. |
| M7 — Legacy importers | Closed | `v0.7.0` | Full Deep-LASI reconstruction and analysis-only tMAVEN SMD intake. |
| M8 — ML v2 | Closed | `v0.8.0` | Optional isolated GPU/deep-classifier stack and kinetics-benchmark integration. |
| M9 — Packaging & docs | Closed 2026-11-14 | `v1.0.0-rc1` to `v1.0.0-rc3` | Bundled unsigned installers, the release pipeline proven end to end, and the public documentation set. The stable tag is not cut here. |
| M10 — Docs & community | Post-1.0 bucket | Opens when the `v1.0.0` tag is cut | Documentation and community work, distribution channels that need the tag, and work deferred from the milestones before 1.0. |
| M11 — Agent-swarm infrastructure | Closed 2026-10-13 | None | The coordination layer for automated contributors: claims, the reaper, and the review lane. It has no user-visible change. |
| M12 — Project data model & write safety | Open | Before `v1.0.0` | One notion of file identity, a destination lock that cannot be bypassed, and the multi-movie append. |
| M13 — In-app updater | Open | Before `v1.0.0` | The updater, with each download verified against its build-provenance attestation. |
| M14 — Unified GUI shell | Open | Before `v1.0.0` | One window with one section for each area, a guided mode, and resume at the saved stage. |
| M15 — Trace extraction section | Open | Before `v1.0.0` | Extraction from the application, with the channel map from a bead-slide video or one image. |
| M16 — Trace curation section | Open | Before `v1.0.0` | Curation and corrections in the application, with browsing between traces and movies. |
| M17 — Analysis & plot composition | Open | Before `v1.0.0` | A plot builder in which every plot that Tether computes can be reached and saved. |
| M18 — Validation & multi-OS release acceptance | Open | Cuts `v1.0.0` | The validation suite with evidence bound to the release commit, three clean-machine passes, and the stable tag. |
| M19 — Regression and benchmark suite | Open | Does not block `v1.0.0` | Checks for loss of speed, for drift from the reference tools, and for bad tests. |
| M20 — Movie import and channel geometry | Open | Part before and part after `v1.0.0` | Channel layouts and import of TIFF movies before the tag. Readers for more movie formats after it. |
| M21 — Data deposit | Open | After `v1.0.0` | A checked deposit package of movies, projects, and results, uploaded to a Zenodo draft and published with one explicit action. |
