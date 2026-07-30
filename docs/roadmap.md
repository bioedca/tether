# Roadmap

**Who this page is for.** Readers who want a short, public view of what Tether is
shipping, what comes next, and where its scope stops. This page summarizes the internal
product requirements; it does not duplicate the frozen schema or parameter tables. Use
the [project-store reference](reference/tether-format.md) and
[analysis-parameter reference](reference/parameters.md) for those contracts.

## Current release

`v1.0.0-rc1` is the current public version. It is the release candidate for the first
stable Tether release; stable `v1.0.0` has not been tagged yet. The release candidate
collects the completed M0–M8 capability milestones and the M9 packaging and documentation
work that is now being hardened for release.

The stable v1.0.0 scope is:

- a provenance-first `.tether` project store that keeps every trace linked to its source
  movie, coordinates, corrections, annotations, idealization, and analysis settings;
- native two-color, single-laser TIRF movie extraction, plus round-trip Deep-LASI and
  tMAVEN interchange;
- photophysical correction, keyboard-driven curation, condition-aware ranking, batch
  processing, and provenance-stamped exports;
- one-click tMAVEN-sidecar idealization and seven native population-analysis views,
  backed by the project's validation oracles; and
- cross-platform packaging with frozen environments, checksums, an SBOM, and versioned
  documentation. See [Installers & offline bundle](packaging.md) for current platform and
  signing details.

## Next planned work

The next release step is to finish M9 and tag stable `v1.0.0`: exercise the bundled
installers and validation suite end to end, finish release hardening, and publish the
installer assets, environment locks, checksums, SBOM, and versioned documentation through
the release pipeline.

There is no committed v1.1 feature slate yet. Post-1.0 work will be selected through the
public issue backlog after the stable-release gates are complete; deferred ideas are not
promises until they have an accepted work item and milestone.

## Explicit non-goals

These boundaries apply to v1.0 and are deliberate:

- **No ALEX/PIE, stoichiometry, or three-color analysis.** Tether v1 is for two-color,
  single-laser acquisitions.
- **No built-in data simulator.** Validation uses real labeled traces and named benchmark
  data instead.
- **No attempt to clone every tMAVEN plot.** Seven plot types are native; other tMAVEN
  views remain available through the standalone hand-off.
- **No open-ended legacy-format support.** Deep-LASI inputs and tMAVEN SMD are supported;
  older `.dat` and vbFRET `.mat` formats are not.
- **No central Tether service.** The application runs on each user's workstation and
  projects remain on storage chosen by the lab.

For acquisition-level limits and supported inputs, read
[Does Tether fit my data?](compatibility.md).

## Milestone history

Each milestone was designed to leave a runnable, tested application rather than a partial
layer. Annotated repository tags record the public checkpoints.

| Milestone | Public checkpoint | What it established |
| --- | --- | --- |
| M0 — Foundation | `v0.0.0` | Project store and schema freeze, application shell, pinned environments, CI, and repository governance. |
| M0.5 — De-risking | Validation gate before `v0.1.0` | tMAVEN-sidecar interchange/parity and Deep-LASI extraction/registration feasibility. |
| M1 — Extraction core | `v0.1.0` | Movie-to-trace extraction, calibration/registration, and the headless extraction path. |
| M2 — MVP | `v0.2.0` | Trace↔movie browsing, curation provenance, idealization hand-off, locking, and first analysis views. |
| M3 — Corrections | `v0.3.0` | Photobleaching, leakage and gamma correction, stale-result handling, and resumable batch processing. |
| M4 — Annotation | `v0.4.0` | Structured cross-file conditions, editable categories, and audited condition correction. |
| M5 — Curation + ML v1 | `v0.5.0` | Persistent condition-aware ranking, active-learning cues, drift checks, and multi-curator label merging. |
| M6 — Analysis suite | `v0.6.0` | Population models, seven-plot parity, kinetics, raw-FRET views, and provenance-stamped exports. |
| M7 — Legacy importers | `v0.7.0` | Full Deep-LASI reconstruction and analysis-only tMAVEN SMD intake. |
| M8 — ML v2 | `v0.8.0` | Optional isolated GPU/deep-classifier stack and kinetics-benchmark integration. |
| M9 — Packaging & docs | `v1.0.0-rc1` (current) | Bundled installers, release automation, validation, and the public documentation set; stable `v1.0.0` is next. |
